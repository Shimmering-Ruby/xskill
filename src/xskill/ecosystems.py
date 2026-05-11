"""
ecosystems.py -- Install distilled skills into AI-agent ecosystems
==================================================================

xskill produces skills in a self-managed directory (``~/.xskill/skill/<name>/``)
that is internally a git repo with canary / staging branches. External
ecosystems (Claude Code, OpenCode, Codex, openclaw) discover skills from their
own well-known directories.

This module bridges the two: it copies the stable (``main`` branch) SKILL.md
into the ecosystem's discovery path so the host agent can load it.

Only the stable side is installed. Canary / staging trials are an internal
A/B mechanism; once a staging variant wins (``canary.merge_staging_to_main``)
it lands on ``main`` and the next ``install_*`` call ships it to the host.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Iterable, Optional

from xskill.adapters import submit_trajectory

logger = logging.getLogger("xskill.ecosystems")


# ─────────────────────────────────────────────────────────────────
# Ecosystem auto-detection
# ─────────────────────────────────────────────────────────────────

# Known agent tools and where each one writes its session trajectories.
# Used by ``detect_known_ecosystems`` at server startup to auto-register
# without making the user run `xskill registry add` for every ecosystem.
_KNOWN_ECOSYSTEMS: list[dict] = [
    {
        "id": "claude_code",
        "source_subpath": ".claude/projects",  # CC writes <home>/<this>/<cwd-hash>/*.jsonl
        "bridge_subpath": ".xskill/cc_sessions",  # we mirror them here as traj_*.md
    },
    # Future: codex / opencode / etc. follow the same {id, source, bridge} shape.
]


def detect_known_ecosystems(home_root: Path | str | None = None) -> list[dict]:
    """Probe the user's HOME for known agent tools and report which ones
    have something on disk. Returns a list of detection records:

        {"ecosystem": "claude_code",
         "source": <abs path of native session dir>,
         "bridge": <abs path of paired xskill watch dir>}

    A record only appears if the source dir exists. The bridge dir is the
    path daemon should ``register_dir(..., ecosystem=...)`` to put under
    Registry control — it may or may not exist yet.
    """
    root = Path(home_root) if home_root else Path.home()
    found: list[dict] = []
    for spec in _KNOWN_ECOSYSTEMS:
        source = root / spec["source_subpath"]
        if not source.is_dir():
            continue
        found.append({
            "ecosystem": spec["id"],
            "source": source.resolve(),
            "bridge": (root / spec["bridge_subpath"]).resolve(),
        })
    return found


def install_to_claude_code(
    skill_path: Path | str,
    target_root: Path | str | None = None,
) -> Path:
    """Install one skill into ``<target_root>/.claude/skills/<name>/``.

    ``skill_path`` is a xskill skill directory (must contain ``SKILL.md``).
    ``target_root`` defaults to ``Path.home()``. Returns the destination
    ``SKILL.md`` path.

    The destination directory is created if absent. An existing ``SKILL.md``
    is overwritten (Claude Code's discovery is content-driven; stale frontmatter
    must not survive an update).
    """
    skill_path = Path(skill_path)
    if not skill_path.is_dir():
        raise NotADirectoryError(f"skill_path is not a directory: {skill_path}")

    src_md = skill_path / "SKILL.md"
    if not src_md.exists():
        raise FileNotFoundError(f"SKILL.md not found in {skill_path}")

    name = skill_path.name
    root = Path(target_root) if target_root else Path.home()
    dest_dir = root / ".claude" / "skills" / name
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_md = dest_dir / "SKILL.md"
    shutil.copyfile(src_md, dest_md)
    return dest_md


def ingest_claude_code_sessions(
    target_traj_dir: Path | str,
    *,
    home_root: Path | str | None = None,
    seen_sessions: Optional[set[str]] = None,
) -> list[dict]:
    """Bridge Claude Code session JSONLs into xskill's trajectory directory.

    Scans ``<home_root>/.claude/projects/*/*.jsonl`` and submits any session
    whose ``sessionId`` is not in ``seen_sessions`` as a new trajectory
    (``traj_NNNN.md`` + ``.json``) under ``target_traj_dir`` using the
    ``claude_code_jsonl`` adapter. ``seen_sessions`` is updated in place so
    repeat calls are idempotent. Returns the list of submission results from
    ``submit_trajectory``.

    ``home_root`` defaults to ``Path.home()``.

    A session is identified by the file stem (which is the session UUID Claude
    Code uses as filename). Empty or malformed JSONLs raise upstream rather
    than being silently skipped — this is by design (no fallback).
    """
    target_traj_dir = Path(target_traj_dir)
    target_traj_dir.mkdir(parents=True, exist_ok=True)
    root = Path(home_root) if home_root else Path.home()
    proj_root = root / ".claude" / "projects"
    if not proj_root.is_dir():
        return []

    seen = seen_sessions if seen_sessions is not None else set()
    submitted: list[dict] = []
    for jsonl_path in sorted(proj_root.glob("*/*.jsonl")):
        sid = jsonl_path.stem
        if sid in seen:
            continue
        content = jsonl_path.read_text(encoding="utf-8")
        if not content.strip():
            continue
        result = submit_trajectory(
            content=content,
            format="claude_code_jsonl",
            traj_dir=target_traj_dir,
        )
        result["session_id"] = sid
        result["source_jsonl"] = str(jsonl_path)
        submitted.append(result)
        seen.add(sid)
    return submitted


def _scan_seen_sessions(target_traj_dir: Path) -> set[str]:
    """重启时重建 ``seen_sessions``。

    桥接出的 ``traj_NNNN.json`` 的 metadata 里已经存了 ``session_id``（由
    ``_adapt_claude_code_jsonl`` 写入）。扫一遍 ``target_traj_dir`` 下所有
    json，把它们的 session_id 集进 set，避免 daemon 重启时把同一条 CC
    session 再桥一遍。
    """
    seen: set[str] = set()
    if not target_traj_dir.is_dir():
        return seen
    for jp in target_traj_dir.glob("traj_*.json"):
        try:
            meta = json.loads(jp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        sid = meta.get("session_id")
        if sid:
            seen.add(sid)
    return seen


class CCSessionIngester:
    """周期性把 Claude Code 会话 JSONL 桥到 xskill 的 watch 目录。

    服务启动时实例化一份长跑线程；它和 DirectoryWatcher 并行，但只负责
    "从 native 源拉到 xskill 这边"这一步——拉过来之后剩下的 meta / index /
    skill 生成全部走 DirectoryWatcher 现有流水线。

    设计上：
      - ``seen_sessions`` 重启可恢复：扫 target dir 的 traj_*.json 重建。
      - 周期 poll，没有用 inotify (移植性差且并发模型上没必要)。
      - 找不到 source 目录是正常情况（用户机器上压根没装 CC），不报错。
    """

    def __init__(
        self,
        target_traj_dir: Path | str,
        *,
        home_root: Path | str | None = None,
        poll_interval: float = 10.0,
    ):
        self.target_traj_dir = Path(target_traj_dir)
        self.home_root = Path(home_root) if home_root else Path.home()
        self.poll_interval = poll_interval
        self._seen: set[str] = _scan_seen_sessions(self.target_traj_dir)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stats = {"polls": 0, "ingested": 0, "errors": 0, "last_poll": None}

    # ── lifecycle ─────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="xskill-cc-ingester",
        )
        self._thread.start()
        logger.info(
            "CCSessionIngester started "
            "(source=%s, target=%s, interval=%.1fs, %d sessions pre-seen)",
            self.home_root / ".claude" / "projects",
            self.target_traj_dir,
            self.poll_interval,
            len(self._seen),
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.poll_interval + 5)
        logger.info("CCSessionIngester stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> dict:
        return {**self._stats, "seen_sessions": len(self._seen),
                "running": self.is_running}

    # ── main loop ─────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                self._stats["errors"] += 1
                logger.exception("CCSessionIngester scan error")
            self._stop.wait(self.poll_interval)

    def run_once(self) -> list[dict]:
        """单次扫描 + 桥接。供测试或手动触发使用。"""
        self._stats["polls"] += 1
        self._stats["last_poll"] = time.time()
        submitted = ingest_claude_code_sessions(
            target_traj_dir=self.target_traj_dir,
            home_root=self.home_root,
            seen_sessions=self._seen,
        )
        if submitted:
            self._stats["ingested"] += len(submitted)
            logger.info(
                "CCSessionIngester: bridged %d new CC session(s) → %s",
                len(submitted), self.target_traj_dir,
            )
        return submitted


def install_all_to_claude_code(
    skill_dir: Path | str,
    target_root: Path | str | None = None,
    names: Iterable[str] | None = None,
) -> list[Path]:
    """Install every skill under ``skill_dir`` (each subdir = one skill) to
    Claude Code's discovery root. If ``names`` is given, restrict to those.
    Returns the list of destination ``SKILL.md`` paths actually written.
    """
    skill_dir = Path(skill_dir)
    if not skill_dir.is_dir():
        raise NotADirectoryError(f"skill_dir is not a directory: {skill_dir}")

    name_filter = set(names) if names is not None else None
    installed: list[Path] = []
    for entry in sorted(skill_dir.iterdir()):
        if not entry.is_dir():
            continue
        if name_filter is not None and entry.name not in name_filter:
            continue
        if not (entry / "SKILL.md").exists():
            continue
        installed.append(install_to_claude_code(entry, target_root=target_root))
    return installed
