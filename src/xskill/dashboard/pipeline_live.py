"""看板「流水线」页的实时监看数据：读 agent-worker 状态文件 + agent 日志尾巴。

常驻 agent-worker 子进程每 ``status_interval`` 秒把
``DirectoryWatcher.agent_worker_status`` 原子落盘到
``<home>/agent_worker_status.json``（见 ``utils/status_file.py``）；本模块只做
只读整形，不碰 watcher 进程内存，因此 serve 内置看板与独立只读实例都能用。

原则（与概念稿一致）：**禁止 fallback 糊弄**——状态文件缺失 / 日志不存在
一律显式空态，由前端如实展示，绝不编造数据。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from xskill.utils.status_file import AGENT_WORKER_STATUS_FILE, read_status_file

logger = logging.getLogger(__name__)

# 监看展示的三池（embed 不进概念稿：读库事实，不占席位色块）。
MONITORED_POOLS = ("split", "cluster", "edit")

# 日志尾巴默认/上限行数。
DEFAULT_LOG_TAIL = 300
MAX_LOG_TAIL = 2000
# 读日志尾巴时最多回退的字节数（避免整文件载入内存）。
_LOG_READBACK_BYTES = 512 * 1024

_KIND_TO_LOG_SUBPATH = {
    # SkillEditAgent._trace_path()
    "skill": ("agents", "skill_edit_agents", "skills"),
    # TaskAgent 拆分轨迹的逐轮 trace
    "traj": ("agents", "task_agents"),
}


def status_root_for(db_path: Optional[Path]) -> Path:
    """状态文件所在 home：与 ``_skill_dir_for`` 同一套旁推——registry.db /
    状态文件 / skill 库同在 XSKILL_HOME 下；独立实例（显式 db_path）与
    serve 内置（db_path=None 走 config 默认）都能解析。"""
    if db_path is not None:
        return Path(db_path).parent
    from xskill.config import XSKILL_HOME
    return XSKILL_HOME


def pipeline_live(db_path: Optional[Path]) -> dict:
    """整形 agent-worker 状态文件为「流水线」页响应。

    状态文件缺失 / 内容为空 / watcher 已停 → ``running: False`` + 明示原因；
    绝不返回半真半假的占位数据。
    """
    root = status_root_for(db_path)
    payload = read_status_file(root / AGENT_WORKER_STATUS_FILE)
    if payload is None:
        return {
            "running": False,
            "message": "agent-worker 尚未启动（无状态文件）",
        }
    stats = payload.get("stats") or {}
    if not stats:
        return {
            "running": False,
            "ok": bool(payload.get("ok")),
            "error": payload.get("error"),
            "message": "agent-worker 未上报状态（状态文件为空）",
        }

    pool_config = stats.get("pool_config") or {}
    raw_pools = stats.get("pools") or {}
    pools: dict[str, dict] = {}
    for name in MONITORED_POOLS:
        status = raw_pools.get(name) or {}
        cfg = pool_config.get(name) or {}
        workers = int(status.get("workers") or cfg.get("workers") or 0)
        seats = status.get("seats")
        if not isinstance(seats, list) or len(seats) != workers:
            # 老版本 worker 未上报席位簿记：显式空席，不伪造任务。
            seats = [None] * workers
        queue = status.get("queue")
        if not isinstance(queue, list):
            queue = []
        pools[name] = {
            "workers": workers,
            "llm_weight": cfg.get("llm_weight"),
            "batch_size": cfg.get("batch_size"),
            "seats": seats,
            "queue": queue,
            "queued": int(status.get("queued") or 0),
            "completed": int(status.get("completed") or 0),
            "failed": int(status.get("failed") or 0),
        }

    watcher = stats.get("watcher") or {}
    cluster = stats.get("cluster") or {}
    return {
        "running": bool(watcher.get("running")),
        "ok": bool(payload.get("ok")),
        "error": payload.get("error"),
        "pid": stats.get("pid"),
        "started_at": stats.get("started_at"),
        "heartbeat_at": stats.get("heartbeat_at"),
        "llm": stats.get("llm") or {},
        "pending_atoms": int(cluster.get("pending_atoms") or 0),
        "pools": pools,
    }


def _safe_log_name(name: str) -> str:
    """日志文件名防路径穿越：只允许单段文件名。"""
    if not name or name in (".", ".."):
        raise ValueError("name 不能为空")
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"非法日志名: {name!r}")
    return name


def tail_task_log(
    db_path: Optional[Path],
    *,
    kind: str,
    name: str,
    tail: int = DEFAULT_LOG_TAIL,
) -> dict:
    """读单个任务的 agent trace 尾巴。

    ``kind`` 只认 ``skill`` / ``traj``；cluster 批没有独立日志文件（概念稿
    的 Cluster 详情展示本批 atom id 列表，不走本端点）。文件不存在是正常
    情况（任务刚起跑 / logs_dir 未配），返回显式空态而非 404 错误页。
    """
    if kind not in _KIND_TO_LOG_SUBPATH:
        raise ValueError(f"kind 必须是 {sorted(_KIND_TO_LOG_SUBPATH)} 之一")
    name = _safe_log_name(name)
    tail = max(1, min(int(tail), MAX_LOG_TAIL))
    root = status_root_for(db_path) / "logs"
    path = root.joinpath(*_KIND_TO_LOG_SUBPATH[kind]) / f"{name}.log"
    if not path.is_file():
        return {
            "kind": kind,
            "name": name,
            "exists": False,
            "lines": [],
            "message": "该任务暂无日志文件",
        }
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - _LOG_READBACK_BYTES))
            raw = handle.read()
    except OSError as exc:
        logger.warning("读任务日志失败 %s: %s", path, exc)
        return {
            "kind": kind,
            "name": name,
            "exists": False,
            "lines": [],
            "message": f"日志读取失败: {exc}",
        }
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()[-tail:]
    return {
        "kind": kind,
        "name": name,
        "exists": True,
        "lines": lines,
        "truncated": size > _LOG_READBACK_BYTES,
    }
