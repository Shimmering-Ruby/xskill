"""本机未 connect 时的轨迹引导：扫 harness、转 traj_*.md、建会话索引。

``xskill init`` 与首次 ``xskill traj search``（standalone 或 ``--local``）共用。
不装团队 skill 仓，也不要求 ``config.yaml`` / team server。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("xskill.ecosystems")

_STATE_NAME = "local_init.json"


def local_init_state_path(home_root: Path | str | None = None) -> Path:
    root = Path(home_root).expanduser().resolve() if home_root else Path.home()
    return root / ".xskill" / _STATE_NAME


def load_local_init_state(home_root: Path | str | None = None) -> dict | None:
    path = local_init_state_path(home_root)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("local_init.json 无法读取，将重新扫描", exc_info=True)
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def already_initialized(home_root: Path | str | None = None) -> bool:
    return load_local_init_state(home_root) is not None


def make_session_ingester(
    detection: dict,
    *,
    home_root: Path,
    poll_interval: float = 10.0,
):
    """按探测记录构造一轮即走的镜像 ingester；未知生态返回 None。"""
    from xskill.ecosystems import (
        CC_SPEC,
        CODEX_SPEC,
        CURSOR_SPEC,
        DSH_SPEC,
        NGA3_SPEC,
        NGAGENT_SPEC,
        OPENCLAW_SPEC,
        OPENCODE_SPEC,
        JsonlIngester,
        SqliteIngester,
        TraeIngester,
        ensure_zstandard_for_dsh,
    )

    eco = detection["ecosystem"]
    bridge = Path(detection["bridge"])
    bridge.mkdir(parents=True, exist_ok=True)
    if eco == "claude_code":
        return JsonlIngester(
            CC_SPEC, target_traj_dir=bridge, home_root=home_root,
            poll_interval=poll_interval,
        )
    if eco == "codex":
        return JsonlIngester(
            CODEX_SPEC, target_traj_dir=bridge, home_root=home_root,
            poll_interval=poll_interval,
        )
    if eco == "nga3":
        return JsonlIngester(
            NGA3_SPEC, target_traj_dir=bridge, home_root=home_root,
            poll_interval=poll_interval,
        )
    if eco == "cursor":
        return JsonlIngester(
            CURSOR_SPEC, target_traj_dir=bridge, home_root=home_root,
            poll_interval=poll_interval,
        )
    if eco == "openclaw":
        return JsonlIngester(
            OPENCLAW_SPEC, target_traj_dir=bridge, home_root=home_root,
            poll_interval=poll_interval,
        )
    if eco == "deepseek_harness":
        ensure_zstandard_for_dsh(home_root)
        return JsonlIngester(
            DSH_SPEC, target_traj_dir=bridge, home_root=home_root,
            poll_interval=poll_interval,
        )
    if eco == "opencode":
        return SqliteIngester(
            target_traj_dir=bridge, home_root=home_root,
            spec=OPENCODE_SPEC, poll_interval=poll_interval,
        )
    if eco == "ngagent":
        return SqliteIngester(
            target_traj_dir=bridge, home_root=home_root,
            spec=NGAGENT_SPEC, poll_interval=poll_interval,
        )
    if eco == "trae":
        return TraeIngester(
            target_traj_dir=bridge, home_root=home_root,
            poll_interval=poll_interval,
        )
    return None


def _local_corpus_empty(home_root: Path) -> bool:
    from xskill.traj_search import (
        iter_local_bridge_session_dirs,
        session_index_count,
    )

    xhome = home_root / ".xskill"
    for _label, directory in iter_local_bridge_session_dirs(xhome):
        if session_index_count(directory):
            return False
    return True


def ingest_detected_sessions_once(
    home_root: Path | str | None = None,
    ecosystems: list[str] | None = None,
) -> dict[str, Any]:
    """探测本机 harness，各跑一轮桥接，并给 bridge 目录建会话索引。"""
    from xskill.ecosystems import detect_known_ecosystems
    from xskill.traj_search import refresh_session_index, session_index_count

    root = Path(home_root).expanduser().resolve() if home_root else Path.home()
    detections = detect_known_ecosystems(home_root=root)
    wanted = set(ecosystems) if ecosystems else None
    bridged: dict[str, int] = {}
    indexed: dict[str, int] = {}
    errors: dict[str, str] = {}
    used: list[str] = []
    for detection in detections:
        eco = str(detection["ecosystem"])
        if wanted is not None and eco not in wanted:
            continue
        used.append(eco)
        try:
            ingester = make_session_ingester(detection, home_root=root)
            if ingester is None:
                errors[eco] = "no ingester"
                continue
            submitted = ingester.run_once()
            bridged[eco] = len(submitted or [])
            refresh_session_index(Path(detection["bridge"]), limit=None)
            indexed[eco] = session_index_count(Path(detection["bridge"]))
        except Exception as ingest_error:
            logger.warning("local bootstrap failed for %s", eco, exc_info=True)
            errors[eco] = f"{type(ingest_error).__name__}"
    return {
        "home_root": str(root),
        "harnesses": used,
        "detections": detections,
        "bridged": bridged,
        "indexed": indexed,
        "errors": errors,
    }


def ensure_local_sessions(
    *,
    home_root: Path | str | None = None,
    force: bool = False,
    skip_if_server: bool = True,
) -> dict[str, Any]:
    """需要时扫盘转轨迹。已初始化且本机已有索引则跳过。

    第一次（没有 ``local_init.json``）、``force``、或标记在但索引仍空时会跑。
    team server 进程所在机器默认跳过，避免把操作员 HOME 扫进 serve 仓库。
    """
    if skip_if_server:
        from xskill.runtime import role
        if role() == "server":
            return {"ran": False, "reason": "server"}

    root = Path(home_root).expanduser().resolve() if home_root else Path.home()
    state = load_local_init_state(root)
    if state is not None and not force and not _local_corpus_empty(root):
        return {
            "ran": False,
            "reason": "already",
            "harnesses": list(state.get("harnesses") or []),
        }

    report = ingest_detected_sessions_once(home_root=root)
    payload = {
        "version": 1,
        "harnesses": report["harnesses"],
        "bridged": report["bridged"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    state_path = local_init_state_path(root)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report["ran"] = True
    report["reason"] = "scanned"
    report["state_path"] = str(state_path)
    return report
