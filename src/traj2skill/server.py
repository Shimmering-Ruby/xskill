"""
server.py -- FastAPI application (SHORT operation endpoints)
=============================================================
Non-SSE REST endpoints for trajectory search, skill CRUD, and system operations.

Usage:
    from traj2skill.server import create_app
    app = create_app()
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from traj2skill import __version__
from traj2skill.config import load_config, get_skill_dir, get_traj_dir
from traj2skill.search import search as search_trajs
from traj2skill.skill_manager import (
    list_skills,
    show_skill,
    skill_log,
    skill_diff,
    rollback_skill,
    freeze_skill,
    unfreeze_skill,
    delete_skill,
    export_skill,
    import_skill,
)
from traj2skill.skill_tools import init_context, search_skills, rebuild_skill_index
from traj2skill.llm_client import create_llm_client, create_embed_client
from traj2skill.git_lock import ensure_repo, current_branch

logger = logging.getLogger("traj2skill.server")

# ---------------------------------------------------------------------------
# Module-level config -- loaded at import time
# ---------------------------------------------------------------------------
_config = load_config()
_skill_dir = get_skill_dir()
_traj_dir = get_traj_dir()

# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------


# -- Trajectories --

class TrajectorySearchRequest(BaseModel):
    query: str
    dataset_dir: Optional[str] = None
    top_k: int = Field(default=5, ge=1, le=100)
    filter: str = Field(default="all", pattern="^(all|success|failure)$")


class TrajectorySearchResult(BaseModel):
    traj_id: str
    similarity: float
    meta: dict = {}
    md_path: str = ""


class TrajectorySearchResponse(BaseModel):
    results: list[TrajectorySearchResult]
    count: int


# -- Skills --

class SkillSummary(BaseModel):
    name: str
    version: int = 0
    eval_score: Optional[float] = None
    tags: list[str] = []
    frozen: bool = False


class SkillListResponse(BaseModel):
    skills: list[SkillSummary]
    count: int


class SkillDetailResponse(BaseModel):
    name: str
    description: str = ""
    metadata: dict = {}
    skill_md_body: str = ""    # body AFTER the frontmatter
    skill_md_raw: str = ""     # full raw SKILL.md including frontmatter
    files: list[str] = []


class SkillLogResponse(BaseModel):
    name: str
    log: str


class SkillDiffResponse(BaseModel):
    name: str
    diff: str


class RollbackRequest(BaseModel):
    version: Optional[str] = None


class ImportSkillRequest(BaseModel):
    source_path: str


class SkillSearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=100)


# -- System --

class HealthResponse(BaseModel):
    status: str
    version: str


class StatusResponse(BaseModel):
    skill_dir: str
    traj_dir: str
    skill_count: int
    git_branch: str


class InitRequest(BaseModel):
    path: Optional[str] = None


class MessageResponse(BaseModel):
    message: str
    ok: bool = True


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/api/v1")


# ---- Trajectories --------------------------------------------------------

@router.post("/trajectories/search", response_model=TrajectorySearchResponse)
async def api_search_trajectories(req: TrajectorySearchRequest):
    """Search for similar trajectories in the dataset index."""
    try:
        dataset_dir = Path(req.dataset_dir) if req.dataset_dir else _traj_dir
        if not dataset_dir.is_dir():
            raise HTTPException(status_code=404, detail=f"Dataset directory not found: {dataset_dir}")

        results = search_trajs(
            dataset_dir=dataset_dir,
            query_text=req.query,
            top_k=req.top_k,
            success_filter=req.filter,
            config=_config,
        )
        items = [
            TrajectorySearchResult(
                traj_id=r["traj_id"],
                similarity=r["similarity"],
                meta=r.get("meta", {}),
                md_path=r.get("md_path", ""),
            )
            for r in results
        ]
        return TrajectorySearchResponse(results=items, count=len(items))
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("trajectory search failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---- Skills CRUD ---------------------------------------------------------

@router.get("/skills", response_model=SkillListResponse)
async def api_list_skills():
    """List all skills and their status."""
    try:
        skills = list_skills(_skill_dir)
        items = [SkillSummary(**s) for s in skills]
        return SkillListResponse(skills=items, count=len(items))
    except Exception as e:
        logger.exception("list skills failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/skills/{name}", response_model=SkillDetailResponse)
async def api_show_skill(name: str):
    """Show skill details: description, metadata, and raw SKILL.md body."""
    try:
        result = show_skill(_skill_dir, name)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return SkillDetailResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("show skill failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/skills/{name}/log", response_model=SkillLogResponse)
async def api_skill_log(name: str):
    """Return the git log for a skill."""
    try:
        log_text = skill_log(_skill_dir, name)
        if log_text.startswith("skill not found"):
            raise HTTPException(status_code=404, detail=log_text)
        return SkillLogResponse(name=name, log=log_text)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("skill log failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/skills/{name}/diff", response_model=SkillDiffResponse)
async def api_skill_diff(name: str, v1: Optional[str] = None, v2: Optional[str] = None):
    """Return the diff for a skill between two versions."""
    try:
        diff_text = skill_diff(_skill_dir, name, v1=v1, v2=v2)
        if diff_text.startswith("skill not found"):
            raise HTTPException(status_code=404, detail=diff_text)
        return SkillDiffResponse(name=name, diff=diff_text)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("skill diff failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/{name}/rollback", response_model=MessageResponse)
async def api_rollback_skill(name: str, req: RollbackRequest):
    """Rollback a skill to a specific version or to the previous version."""
    try:
        ok = rollback_skill(_skill_dir, name, version=req.version)
        if not ok:
            raise HTTPException(status_code=400, detail=f"Rollback failed for skill: {name}")
        target = req.version or "previous version"
        return MessageResponse(message=f"Rolled back {name} to {target}", ok=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("rollback failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/{name}/freeze", response_model=MessageResponse)
async def api_freeze_skill(name: str):
    """Freeze a skill so it is not auto-updated by batch runs."""
    try:
        ok = freeze_skill(_skill_dir, name)
        if not ok:
            raise HTTPException(status_code=400, detail=f"Freeze failed for skill: {name}")
        return MessageResponse(message=f"Frozen: {name}", ok=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("freeze failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/{name}/unfreeze", response_model=MessageResponse)
async def api_unfreeze_skill(name: str):
    """Unfreeze a skill to allow auto-updates."""
    try:
        ok = unfreeze_skill(_skill_dir, name)
        if not ok:
            raise HTTPException(status_code=400, detail=f"Unfreeze failed for skill: {name}")
        return MessageResponse(message=f"Unfrozen: {name}", ok=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("unfreeze failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/skills/{name}", response_model=MessageResponse)
async def api_delete_skill(name: str):
    """Delete a skill and commit the change."""
    try:
        ok = delete_skill(_skill_dir, name)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Skill not found or delete failed: {name}")
        return MessageResponse(message=f"Deleted: {name}", ok=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("delete failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/skills/{name}/export")
async def api_export_skill(name: str):
    """Export a skill directory as a downloadable archive."""
    try:
        tmp_dir = Path(tempfile.mkdtemp())
        exported = export_skill(_skill_dir, name, tmp_dir)
        # Tar it up for download
        import shutil
        archive_path = Path(tempfile.mkdtemp()) / f"{name}.tar.gz"
        shutil.make_archive(
            str(archive_path).replace(".tar.gz", ""),
            "gztar",
            root_dir=str(tmp_dir),
            base_dir=name,
        )
        return FileResponse(
            path=str(archive_path),
            filename=f"{name}.tar.gz",
            media_type="application/gzip",
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("export failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/skills/import", response_model=MessageResponse)
async def api_import_skill(req: ImportSkillRequest):
    """Import a skill from a source directory path."""
    try:
        source = Path(req.source_path)
        if not source.is_dir():
            raise HTTPException(status_code=404, detail=f"Source path not found: {req.source_path}")
        name = import_skill(_skill_dir, source)
        return MessageResponse(message=f"Imported: {name}", ok=True)
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("import failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---- Skill Search --------------------------------------------------------

@router.post("/skills/search")
async def api_search_skills(req: SkillSearchRequest):
    """Search existing skills by semantic similarity."""
    try:
        results_json = search_skills(req.query, top_k=req.top_k)
        import json
        results = json.loads(results_json)
        return results
    except Exception as e:
        logger.exception("skill search failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---- System --------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
async def api_health():
    """Health check endpoint."""
    return HealthResponse(status="ok", version=__version__)


@router.get("/status", response_model=StatusResponse)
async def api_status():
    """Return system status: skill dir, skill count, git branch."""
    try:
        skills = list_skills(_skill_dir)
        branch = current_branch(str(_skill_dir))
        return StatusResponse(
            skill_dir=str(_skill_dir),
            traj_dir=str(_traj_dir),
            skill_count=len(skills),
            git_branch=branch,
        )
    except Exception as e:
        logger.exception("status check failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/init", response_model=MessageResponse)
async def api_init(req: InitRequest):
    """Initialize the skill git repository."""
    try:
        target = req.path or str(_skill_dir)
        ensure_repo(target)
        return MessageResponse(message=f"Initialized skill repo at: {target}", ok=True)
    except Exception as e:
        logger.exception("init failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reindex", response_model=MessageResponse)
async def api_reindex():
    """Rebuild the skill vector index."""
    try:
        rebuild_skill_index()
        return MessageResponse(message="Skill index rebuilt", ok=True)
    except Exception as e:
        logger.exception("reindex failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="traj2skill",
        description="Trajectory-to-Skill distillation API",
        version=__version__,
    )
    app.include_router(router)

    # SSE 长耗时接口
    from traj2skill.tasks import sse_router
    app.include_router(sse_router)

    # 轨迹提交接口
    from traj2skill.adapters import submit_trajectory
    from pydantic import BaseModel as _BaseModel

    class _SubmitRequest(_BaseModel):
        content: str
        format: str = "markdown"
        metadata: dict | None = None
        traj_id: str | None = None

    @app.post("/api/v1/trajectories/submit")
    async def api_submit_trajectory(req: _SubmitRequest):
        try:
            result = submit_trajectory(
                content=req.content,
                format=req.format,
                metadata=req.metadata or {},
                traj_id=req.traj_id,
                traj_dir=_traj_dir,
            )
            return result
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    # ------------------------------------------------------------------
    # Static UI mount (SPA) -- must be registered AFTER all /api/* routes
    # ------------------------------------------------------------------
    _dist_dir = Path(__file__).parent / "web" / "dist"
    _ui_disabled = os.environ.get("T2S_NO_UI", "").lower() in ("1", "true", "yes")

    if _dist_dir.is_dir() and not _ui_disabled:
        _index_file = _dist_dir / "index.html"
        _dist_root = _dist_dir.resolve()

        # Mount static assets (serves index.html at "/" and all asset files).
        # Because this is mounted at "/", it consumes every path not already
        # matched by the routes registered above. Unmatched paths inside the
        # mount return 404 from StaticFiles, so we also register a custom
        # sub-app that falls back to index.html for SPA routes.
        from starlette.exceptions import HTTPException as StarletteHTTPException

        class _SPAStaticFiles(StaticFiles):
            async def get_response(self, path: str, scope):
                # Never serve the SPA for API paths -- let FastAPI's own
                # 404 propagate so the client knows the endpoint is missing.
                request_path = scope.get("path", "") or ""
                if request_path.startswith("/api/") or request_path == "/api":
                    raise StarletteHTTPException(status_code=404)
                try:
                    response = await super().get_response(path, scope)
                except StarletteHTTPException as exc:
                    if exc.status_code == 404 and _index_file.is_file():
                        return FileResponse(str(_index_file))
                    raise
                if response.status_code == 404 and _index_file.is_file():
                    return FileResponse(str(_index_file))
                return response

        app.mount(
            "/",
            _SPAStaticFiles(directory=str(_dist_dir), html=True),
            name="ui",
        )

    @app.on_event("startup")
    async def _startup():
        """Initialize skill_tools context so search_skills / rebuild_skill_index work."""
        try:
            llm = create_llm_client(_config)
            embed = create_embed_client(_config)
            init_context(
                skill_dir=_skill_dir,
                data_dir=_traj_dir,
                llm_client=llm,
                embed_client=embed,
                config=_config,
            )
            logger.info(
                "traj2skill server ready  skill_dir=%s  traj_dir=%s",
                _skill_dir,
                _traj_dir,
            )
        except Exception:
            logger.warning(
                "LLM/embed clients not configured -- skill search and reindex "
                "will fail until config is fixed",
                exc_info=True,
            )

    return app
