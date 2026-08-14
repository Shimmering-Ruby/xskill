"""team server 上的 generate 任务：落盘日志、后台跑代理、流式给客户端。"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger("xskill.team.generate")

_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


def _job_dir(logs_dir: Path, user_id: str) -> Path:
    path = logs_dir / "agents" / "generate_agents" / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_job(
    *,
    client_id: str,
    user_id: str,
    instruction: str,
    preferred_names: list[str],
    logs_dir: Path,
) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    log_path = _job_dir(logs_dir, user_id) / f"{job_id}.log"
    log_path.write_text("", encoding="utf-8")
    job = {
        "job_id": job_id,
        "client_id": client_id,
        "user_id": user_id,
        "instruction": instruction,
        "preferred_names": list(preferred_names),
        "status": "running",
        "log_path": str(log_path),
        "skill_names": [],
        "pinned": [],
        "error": "",
        "created_at": time.time(),
    }
    with _JOBS_LOCK:
        _JOBS[job_id] = job
    _write_status(job)
    return dict(job)


def get_job(job_id: str) -> dict[str, Any] | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job is not None else None


def _update_job(job_id: str, **fields: Any) -> dict[str, Any] | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return None
        job.update(fields)
        snapshot = dict(job)
    _write_status(snapshot)
    return snapshot


def _write_status(job: dict[str, Any]) -> None:
    log_path = Path(job["log_path"])
    status_path = log_path.with_suffix(".status.json")
    payload = {
        key: job[key]
        for key in (
            "job_id", "client_id", "user_id", "status",
            "skill_names", "pinned", "error", "created_at",
        )
        if key in job
    }
    try:
        status_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("failed to write generate status %s", status_path, exc_info=True)


def collect_read_roots(skill_dir: Path, traj_root: Path | None) -> list[Path]:
    roots: list[Path] = [Path(skill_dir)]
    if traj_root is not None:
        roots.append(Path(traj_root))
        clients = Path(traj_root) / "clients"
        if clients.is_dir():
            roots.append(clients)
    try:
        from xskill.pipeline.registry import list_watch_dirs
        for row in list_watch_dirs():
            path = Path(row["path"])
            if path.is_dir():
                roots.append(path)
    except Exception:
        logger.debug("list_watch_dirs unavailable for generate roots", exc_info=True)
    unique: list[Path] = []
    seen: set[str] = set()
    for path in roots:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def pin_generated_skills(
    *,
    user_id: str,
    skill_names: list[str],
    db_path: Path | None,
    max_pinned: int | None,
) -> list[str]:
    from xskill.pipeline.registry import PinQuotaExceeded, set_skill_pref

    pinned: list[str] = []
    for name in skill_names:
        try:
            set_skill_pref(
                user_key=user_id,
                skill_name=name,
                pref="pinned",
                set_by=user_id,
                max_pinned=max_pinned,
                db_path=db_path,
            )
            pinned.append(name)
        except PinQuotaExceeded as error:
            logger.warning("generate pin quota exceeded for %s: %s", name, error)
        except Exception:
            logger.exception("generate pin failed for %s", name)
    try:
        from xskill.team.server import api as server_api
        with server_api._MANIFEST_CONTROL_CACHE_LOCK:
            server_api._MANIFEST_CONTROL_CACHE.clear()
    except Exception:
        logger.debug("could not invalidate manifest cache after generate pin", exc_info=True)
    return pinned


def run_generate_job(job_id: str, *, ctx: Any, config: dict | None) -> None:
    """Run one generate job in the current thread. Tests may call this directly."""
    job = get_job(job_id)
    if job is None:
        return
    try:
        _run_generate_job_body(job, ctx=ctx, config=config or {})
    except Exception as error:  # noqa: BLE001 — job must end in failed, not crash thread
        logger.exception("generate job %s failed", job_id)
        _update_job(job_id, status="failed", error=str(error))


def _run_generate_job_body(job: dict[str, Any], *, ctx: Any, config: dict) -> None:
    from xskill.agents import agent_tools
    from xskill.agents.agno_factory import make_default_factory
    from xskill.agents.generate_agent import GenerateAgent
    from xskill.config import get_logs_dir, get_registry_db_path

    skill_dir = Path(ctx.skill_dir)
    traj_root = Path(ctx.traj_root) if ctx.traj_root is not None else None
    extra_roots = collect_read_roots(skill_dir, traj_root)
    logs_dir = get_logs_dir()
    spill_root = (
        logs_dir / "agents" / "generate_agents" / job["user_id"] / "spill" / job["job_id"]
    )
    spill_root.mkdir(parents=True, exist_ok=True)
    db_path = get_registry_db_path()
    agent_tools.reset_generate_session()
    tool_context = agent_tools.create_agent_tool_context(
        skill_dir=skill_dir,
        data_dir=skill_dir,
        config=config,
        atom_skill_dir=skill_dir,
        default_traj_root=traj_root,
        spill_root=spill_root,
        extra_read_roots=tuple(extra_roots),
        generate_user_id=job["user_id"],
        registry_db_path=db_path,
    )
    llm_cfg = {**(config.get("llm") or {}), **(config.get("llm_skill") or {})}
    factory = make_default_factory(
        config, spill_root=spill_root,
    )
    agent = GenerateAgent(
        skill_dir=skill_dir,
        agno_agent_factory=factory,
        llm_cfg=llm_cfg,
        logs_dir=logs_dir,
        extra_read_roots=tuple(extra_roots),
    )
    token = agent_tools.bind_agent_tool_context(tool_context)
    try:
        agent.run(
            instruction=job["instruction"],
            user_id=job["user_id"],
            job_id=job["job_id"],
            preferred_names=job.get("preferred_names") or [],
        )
        skill_names = agent_tools.generate_committed_skills()
    finally:
        agent_tools.reset_agent_tool_context(token)

    if not skill_names:
        _update_job(
            job["job_id"],
            status="failed",
            error="generate 结束但没有 commit_generate_main 提交任何 skill",
        )
        return
    max_pinned = None
    try:
        from xskill.config import team_server_slots_config
        max_pinned = team_server_slots_config(config)["skill_slots"]
    except Exception:
        logger.debug("skill_slots unavailable for generate pin quota", exc_info=True)
    pinned = pin_generated_skills(
        user_id=job["user_id"],
        skill_names=skill_names,
        db_path=db_path,
        max_pinned=max_pinned,
    )
    _update_job(
        job["job_id"],
        status="succeeded",
        skill_names=skill_names,
        pinned=pinned,
        error="",
    )


def start_generate_job_thread(job_id: str, *, ctx: Any, config: dict | None) -> None:
    thread = threading.Thread(
        target=run_generate_job,
        args=(job_id,),
        kwargs={"ctx": ctx, "config": config},
        name=f"xskill-generate-{job_id[:8]}",
        daemon=True,
    )
    thread.start()


def iter_job_events(
    job_id: str,
    *,
    poll_seconds: float = 0.2,
    ping_every: float = 15.0,
) -> Iterator[dict[str, Any]]:
    """Yield log chunks then a terminal event. Blocking generator.

    Stays open until the job leaves ``running``. Quiet periods emit ping
    events so proxies and the CLI do not treat a long model call as death.
    """
    job = get_job(job_id)
    if job is None:
        yield {"type": "done", "ok": False, "error": "unknown job_id"}
        return
    log_path = Path(job["log_path"])
    offset = 0
    last_emit = time.time()
    while True:
        try:
            data = log_path.read_bytes()
        except OSError:
            data = b""
        if len(data) > offset:
            chunk = data[offset:].decode("utf-8", errors="replace")
            offset = len(data)
            yield {"type": "log", "chunk": chunk}
            last_emit = time.time()
        current = get_job(job_id) or job
        if current.get("status") != "running":
            try:
                data = log_path.read_bytes()
            except OSError:
                data = b""
            if len(data) > offset:
                chunk = data[offset:].decode("utf-8", errors="replace")
                yield {"type": "log", "chunk": chunk}
            yield {
                "type": "done",
                "ok": current.get("status") == "succeeded",
                "skill_names": current.get("skill_names") or [],
                "pinned": current.get("pinned") or [],
                "error": current.get("error") or "",
            }
            return
        if time.time() - last_emit >= ping_every:
            yield {"type": "ping"}
            last_emit = time.time()
        time.sleep(poll_seconds)
