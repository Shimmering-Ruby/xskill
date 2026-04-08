"""
tasks.py -- Background task management with SSE streaming
==========================================================
Provides SSE (Server-Sent Events) endpoints for long-running operations:
  - /api/v1/trajectories/index   -- build/update trajectory index
  - /api/v1/skills/process       -- process a single trajectory into skill
  - /api/v1/skills/batch         -- batch process trajectories
  - /api/v1/skills/{name}/eval   -- evaluate a skill

Each endpoint runs the heavy work in a ThreadPoolExecutor and streams
progress, log, and result events back to the client via SSE.
"""

import asyncio
import json
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from traj2skill.config import load_config, get_skill_dir, get_traj_dir
from traj2skill.llm_client import create_llm_client, create_embed_client

logger = logging.getLogger("tasks")

# ---------------------------------------------------------------------------
# Thread pool shared across all SSE endpoints
# ---------------------------------------------------------------------------
_executor = ThreadPoolExecutor(max_workers=4)

# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def sse_event(event: str, data: dict) -> str:
    """Format a raw SSE event string.

    Example output::

        event: progress
        data: {"step": "meta提取", "current": 42, "total": 300}

    """
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def make_sse_log(queue: asyncio.Queue):
    """Return a *log_fn(msg, tag)* that pushes SSE events onto *queue*.

    The returned callable is compatible with :class:`traj2skill.log.StreamLog`
    (same ``(msg, tag)`` signature) but instead of printing to stdout it
    enqueues ``("log", {"tag": ..., "msg": ...})`` tuples that the SSE
    generator will pick up.
    """

    def log_fn(msg: str, tag: str = "info"):
        try:
            queue.put_nowait(("log", {"tag": tag, "msg": msg}))
        except Exception:
            pass  # queue full / closed — drop silently

    return log_fn


def _push(queue: asyncio.Queue, event: str, data: dict):
    """Convenience: put an (event, data) tuple onto the queue."""
    try:
        queue.put_nowait((event, data))
    except Exception:
        pass


def _finish(queue: asyncio.Queue, data: dict):
    """Push a ``result`` event followed by the sentinel ``None``."""
    _push(queue, "result", data)
    queue.put_nowait(None)


def _fail(queue: asyncio.Queue, error: str):
    """Push an ``error`` event followed by the sentinel ``None``."""
    _push(queue, "error", {"error": error})
    queue.put_nowait(None)


async def _event_generator(queue: asyncio.Queue):
    """Async generator that drains *queue* and yields SSE dicts."""
    while True:
        item = await queue.get()
        if item is None:
            break
        event, data = item
        yield {"event": event, "data": json.dumps(data, ensure_ascii=False)}


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class IndexRequest(BaseModel):
    path: Optional[str] = None
    dataset: Optional[str] = None
    concurrency: int = 10
    no_llm: bool = False


class ProcessRequest(BaseModel):
    traj_path: str
    dry_run: bool = False


class BatchRequest(BaseModel):
    path: Optional[str] = None
    dataset: Optional[str] = None
    max: Optional[int] = None
    dry_run: bool = False


class EvalRequest(BaseModel):
    n_runs: int = 3
    sandbox: bool = False


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
sse_router = APIRouter(prefix="/api/v1")


# ===================================================================
# POST /api/v1/trajectories/index
# ===================================================================

@sse_router.post("/trajectories/index")
async def api_index(req: IndexRequest):
    queue: asyncio.Queue = asyncio.Queue()

    def run():
        try:
            from traj2skill.index import index_dataset

            config = load_config()
            log_fn = make_sse_log(queue)

            # Resolve dataset directory
            base_traj_dir = get_traj_dir()
            if req.path:
                dataset_dir = Path(req.path)
            elif req.dataset:
                dataset_dir = base_traj_dir / req.dataset
            else:
                dataset_dir = base_traj_dir

            if not dataset_dir.is_dir():
                _fail(queue, f"directory not found: {dataset_dir}")
                return

            # Create clients
            llm = None if req.no_llm else create_llm_client(config)
            embed_client = create_embed_client(config)

            _push(queue, "progress", {
                "step": "start",
                "current": 0,
                "total": 0,
                "detail": f"indexing {dataset_dir.name}, concurrency={req.concurrency}",
            })

            # Count trajectories for progress reporting
            md_files = sorted(dataset_dir.glob("traj_*.md"))
            md_files = [f for f in md_files if not f.name.endswith(".meta")]
            total = len(md_files)
            _push(queue, "progress", {
                "step": "meta提取",
                "current": 0,
                "total": total,
            })

            # Run the actual index — this is the heavy part.
            # index_dataset uses tqdm internally; we wrap with log_fn for
            # coarse-grained progress.  Fine-grained tqdm interception is
            # left to a future enhancement.
            index_dataset(
                dataset_dir=dataset_dir,
                llm=llm,
                embed_client=embed_client,
                concurrency=req.concurrency,
            )

            _push(queue, "progress", {
                "step": "meta提取",
                "current": total,
                "total": total,
            })

            _finish(queue, {
                "status": "done",
                "dataset": str(dataset_dir),
                "trajectories": total,
            })
        except Exception as exc:
            logger.error("index task failed: %s", exc, exc_info=True)
            _fail(queue, f"{type(exc).__name__}: {exc}")

    loop = asyncio.get_event_loop()
    loop.run_in_executor(_executor, run)

    return EventSourceResponse(_event_generator(queue))


# ===================================================================
# POST /api/v1/skills/process
# ===================================================================

@sse_router.post("/skills/process")
async def api_process(req: ProcessRequest):
    queue: asyncio.Queue = asyncio.Queue()

    def run():
        try:
            from traj2skill.process import process_traj
            from traj2skill.git_lock import ensure_repo

            config = load_config()
            log_fn = make_sse_log(queue)
            skill_dir = get_skill_dir()

            _push(queue, "progress", {
                "step": "init",
                "current": 0,
                "total": 1,
                "detail": f"processing {Path(req.traj_path).name}",
            })

            ensure_repo(str(skill_dir))

            # process_traj creates its own StreamLog internally.
            # We monkey-patch nothing — instead we capture the result.
            # For richer streaming the caller can parse the result dict.
            result = process_traj(
                traj_md_path=req.traj_path,
                config=config,
                dry_run=req.dry_run,
                skill_dir=skill_dir,
            )

            _push(queue, "log", {
                "tag": "decision",
                "msg": f"action={result.get('action', '?')}",
            })

            if result.get("eval"):
                for skill_name, er in result["eval"].items():
                    _push(queue, "log", {
                        "tag": "eval",
                        "msg": f"{skill_name}: score={er.get('eval_score', 0)}",
                    })

            _finish(queue, result)
        except Exception as exc:
            logger.error("process task failed: %s", exc, exc_info=True)
            _fail(queue, f"{type(exc).__name__}: {exc}")

    loop = asyncio.get_event_loop()
    loop.run_in_executor(_executor, run)

    return EventSourceResponse(_event_generator(queue))


# ===================================================================
# POST /api/v1/skills/batch
# ===================================================================

@sse_router.post("/skills/batch")
async def api_batch(req: BatchRequest):
    queue: asyncio.Queue = asyncio.Queue()

    def run():
        try:
            from traj2skill.process import process_traj
            from traj2skill.git_lock import ensure_repo

            config = load_config()
            log_fn = make_sse_log(queue)
            skill_dir = get_skill_dir()
            base_traj_dir = get_traj_dir()

            # Resolve directory
            if req.path:
                dataset_dir = Path(req.path)
            elif req.dataset:
                dataset_dir = base_traj_dir / req.dataset
            else:
                dataset_dir = base_traj_dir

            if not dataset_dir.is_dir():
                _fail(queue, f"directory not found: {dataset_dir}")
                return

            md_files = sorted(dataset_dir.glob("traj_*.md"))
            md_files = [f for f in md_files if not f.name.endswith(".meta")]
            if req.max and req.max > 0:
                md_files = md_files[: req.max]

            total = len(md_files)
            if total == 0:
                _finish(queue, {"status": "done", "processed": 0, "detail": "no trajectories found"})
                return

            _push(queue, "progress", {
                "step": "batch",
                "current": 0,
                "total": total,
            })

            ensure_repo(str(skill_dir))

            summary = {
                "merged": 0,
                "rejected": 0,
                "skipped": 0,
                "errors": 0,
                "details": [],
            }

            for idx, md_file in enumerate(md_files, 1):
                _push(queue, "progress", {
                    "step": "batch",
                    "current": idx,
                    "total": total,
                    "detail": md_file.name,
                })

                try:
                    result = process_traj(
                        traj_md_path=str(md_file),
                        config=config,
                        dry_run=req.dry_run,
                        skill_dir=skill_dir,
                    )
                    action = result.get("action", "unknown")
                    if action == "merged":
                        summary["merged"] += 1
                    elif action == "rejected":
                        summary["rejected"] += 1
                    elif action in ("skip", "updated_abstract", "dry_run"):
                        summary["skipped"] += 1
                    else:
                        summary["errors"] += 1

                    summary["details"].append({
                        "traj": md_file.name,
                        "action": action,
                    })

                    _push(queue, "log", {
                        "tag": "batch",
                        "msg": f"[{idx}/{total}] {md_file.name} -> {action}",
                    })
                except Exception as e:
                    summary["errors"] += 1
                    summary["details"].append({
                        "traj": md_file.name,
                        "action": "error",
                        "error": str(e),
                    })
                    _push(queue, "log", {
                        "tag": "error",
                        "msg": f"[{idx}/{total}] {md_file.name} failed: {e}",
                    })

            _finish(queue, {"status": "done", **summary})
        except Exception as exc:
            logger.error("batch task failed: %s", exc, exc_info=True)
            _fail(queue, f"{type(exc).__name__}: {exc}")

    loop = asyncio.get_event_loop()
    loop.run_in_executor(_executor, run)

    return EventSourceResponse(_event_generator(queue))


# ===================================================================
# POST /api/v1/skills/{name}/eval
# ===================================================================

@sse_router.post("/skills/{name}/eval")
async def api_eval(name: str, req: EvalRequest):
    queue: asyncio.Queue = asyncio.Queue()

    def run():
        try:
            from traj2skill.skill_eval import run_eval
            from traj2skill.skill_tools import init_context

            config = load_config()
            log_fn = make_sse_log(queue)
            skill_dir = get_skill_dir()
            skill_path = skill_dir / name

            if not skill_path.is_dir():
                _fail(queue, f"skill not found: {name}")
                return

            llm = create_llm_client(config)
            embed = create_embed_client(config)

            # init_context is needed so skill_tools has references
            data_dir = get_traj_dir()
            init_context(skill_dir, data_dir, llm, embed, config)

            _push(queue, "progress", {
                "step": "eval",
                "current": 0,
                "total": req.n_runs,
                "detail": f"evaluating skill '{name}'",
            })

            # run_eval accepts a log_fn with (msg, tag) signature — our
            # make_sse_log produces exactly that.
            result = run_eval(
                skill_dir=skill_path,
                llm_client=llm,
                n_runs=req.n_runs,
                log_fn=log_fn,
                config=config,
                force_sandbox=req.sandbox,
                force_no_sandbox=not req.sandbox,
            )

            _push(queue, "progress", {
                "step": "eval",
                "current": req.n_runs,
                "total": req.n_runs,
            })

            _finish(queue, {
                "status": "done",
                "skill": name,
                **result,
            })
        except Exception as exc:
            logger.error("eval task failed: %s", exc, exc_info=True)
            _fail(queue, f"{type(exc).__name__}: {exc}")

    loop = asyncio.get_event_loop()
    loop.run_in_executor(_executor, run)

    return EventSourceResponse(_event_generator(queue))
