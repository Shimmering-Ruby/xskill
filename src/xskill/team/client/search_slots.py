"""search_slots.py — 搜索下载 skill 的本地安装与持久化管理。

search 命中的 skill 落 ``~/.xskill/search_skills/<skill_id>/``，与 sync 管理的
``~/.xskill/skill/`` 分开——daemon 的 cleanup 按 manifest 清理那边，不碰这里。
台账 ``~/.xskill/search_slots.json`` 按最近命中排序，超过容量淘汰最旧的
（同时摘掉仍由该槽位拥有的生态安装）。每个槽位目录里有 ``.xskill_search.json``
标记（sha / 查询词 / 时间），与 sync 的 ``.xskill_skillhub.json`` 区分来源。

``DownloadedSkills`` 是 ``xskill download`` 的显式、持久化安装区：
``~/.xskill/downloaded_skills/<skill_id>/``。它不参与 search slot 的 LRU，
重复下载只更新同一条台账和安装内容。
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from xskill.team.client.daemon import (
    apply_skillhub_archive,
    install_skill_to_ecosystems,
    uninstall_skill_from_ecosystems,
)
from xskill.skill.git import skill_repo_lock

logger = logging.getLogger("xskill.team.client")

SEARCH_SLOT_CAPACITY = 10
SEARCH_MARKER_NAME = ".xskill_search.json"
DOWNLOAD_MARKER_NAME = ".xskill_download.json"


def _valid_slot_id(skill_id: str) -> bool:
    """槽位 id 必须是单个路径段。"""
    return bool(skill_id) and skill_id not in {".", ".."} \
        and "/" not in skill_id and "\\" not in skill_id \
        and "\x00" not in skill_id


class SearchSlots:
    """search 槽位管理：落盘、打标、按最近命中滚动淘汰。"""

    def __init__(self, *, xskill_home: Path, home_root: Path | None = None,
                 capacity: int = SEARCH_SLOT_CAPACITY):
        self.slots_dir = Path(xskill_home) / "search_skills"
        self.ledger_path = Path(xskill_home) / "search_slots.json"
        self.home_root = Path(home_root) if home_root else Path.home()
        self.capacity = capacity

    def entries(self) -> list[dict]:
        """台账内容（旧 → 新）。文件缺失或损坏按空处理。"""
        try:
            loaded = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [slot for slot in loaded if isinstance(slot, dict)] \
            if isinstance(loaded, list) else []

    def install(
        self, result: dict, archive_bytes: bytes, *, query: str,
        return_details: bool = False,
    ) -> Path | dict:
        """落盘命中 skill、安装并滚动淘汰。

        默认仍返回绝对路径，兼容既有内部调用；CLI 可用 ``return_details`` 在
        同一次安装中取得缓存路径和逐 harness 安装结果，避免为展示再次探测或安装。
        """
        skill_id = result.get("skill_id")
        if not isinstance(skill_id, str) or not _valid_slot_id(skill_id):
            raise ValueError(f"invalid search skill_id: {skill_id!r}")
        dest_dir = self.slots_dir / skill_id
        searched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        apply_skillhub_archive(
            archive_bytes, dest_dir,
            expected_sha=result["content_sha"],
            display_name=result.get("display_name"),
            source_path=result.get("source_path"),
            marker_name=SEARCH_MARKER_NAME,
            extra_meta={"query": query, "searched_at": searched_at},
        )
        installations = install_skill_to_ecosystems(
            dest_dir, home_root=self.home_root,
        )
        slots = [slot for slot in self.entries()
                 if slot.get("skill_id") != skill_id]
        slots.append({
            "skill_id": skill_id,
            "display_name": result.get("display_name"),
            "description": result.get("description"),
            "sha": result["content_sha"],
            "query": query,
            "searched_at": searched_at,
            "installations": [dict(record) for record in installations],
        })
        evicted, kept = slots[:-self.capacity], slots[-self.capacity:]
        for stale in evicted:
            stale_id = stale.get("skill_id")
            if not isinstance(stale_id, str) or not _valid_slot_id(stale_id):
                logger.warning("ignored invalid search slot id: %r", stale_id)
                continue
            stale_dir = self.slots_dir / stale_id
            uninstall_skill_from_ecosystems(
                stale_id,
                home_root=self.home_root,
                source_dir=stale_dir,
                installations=stale.get("installations"),
            )
            shutil.rmtree(stale_dir, ignore_errors=True)
            logger.info("search slot evicted: %s", stale_id)
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text(
            json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        resolved_path = dest_dir.resolve()
        if return_details:
            return {
                "cache_path": resolved_path,
                "installations": tuple(
                    dict(record) for record in installations
                ),
            }
        return resolved_path


class DownloadedSkills:
    """显式下载的持久 skill：独立台账管理，不因后续搜索而淘汰。"""

    def __init__(
        self, *, xskill_home: Path, home_root: Path | None = None,
    ) -> None:
        self.skills_dir = Path(xskill_home) / "downloaded_skills"
        self.ledger_path = Path(xskill_home) / "downloads.json"
        self.home_root = Path(home_root) if home_root else Path.home()

    def _entries_unlocked(self) -> list[dict]:
        try:
            loaded = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return (
            [entry for entry in loaded if isinstance(entry, dict)]
            if isinstance(loaded, list) else []
        )

    def entries(self) -> list[dict]:
        with skill_repo_lock(
            self.skills_dir, use_git_write_limit=False,
        ):
            return self._entries_unlocked()

    def install(
        self, result: dict, archive_bytes: bytes,
        *, ecosystems: list[str] | tuple[str, ...] | None = None,
        return_details: bool = False,
    ) -> Path | dict:
        """落盘并安装一个显式下载项；同 ID 原地更新，其余下载项全部保留。

        显式 ``ecosystems`` 会写入台账，供 daemon 后续内容刷新继续复用。重复
        下载同一 ID 时取新旧选择的并集，避免把仍在使用的旧 harness 变成不再
        更新的孤儿安装；不传则兼容旧调用，继续安装到所有已检测生态。
        """
        skill_id = result.get("skill_id")
        if not isinstance(skill_id, str) or not _valid_slot_id(skill_id):
            raise ValueError(f"invalid download skill_id: {skill_id!r}")
        content_sha = result.get("content_sha")
        if not isinstance(content_sha, str) or not content_sha:
            raise ValueError("download metadata missing content_sha")

        with skill_repo_lock(
            self.skills_dir, use_git_write_limit=False,
        ):
            existing_entries = self._entries_unlocked()
            existing_entry = next(
                (
                    entry for entry in existing_entries
                    if entry.get("skill_id") == skill_id
                ),
                None,
            )
            selected_ecosystems = ecosystems
            if ecosystems is not None:
                stored_agents = (
                    existing_entry.get("agents")
                    if isinstance(existing_entry, dict) else None
                )
                previous_agents = (
                    [
                        agent for agent in stored_agents
                        if isinstance(agent, str)
                    ]
                    if isinstance(stored_agents, list) else []
                )
                selected_ecosystems = list(dict.fromkeys(
                    [*previous_agents, *ecosystems]
                ))
            dest_dir = self.skills_dir / skill_id
            downloaded_at = datetime.now(timezone.utc).isoformat(
                timespec="seconds",
            )
            apply_skillhub_archive(
                archive_bytes,
                dest_dir,
                expected_sha=content_sha,
                display_name=result.get("display_name"),
                source_path=result.get("source_path"),
                marker_name=DOWNLOAD_MARKER_NAME,
                extra_meta={"downloaded_at": downloaded_at},
            )
            installations = install_skill_to_ecosystems(
                dest_dir, home_root=self.home_root,
                ecosystems=selected_ecosystems,
            )
            entries = [
                entry for entry in existing_entries
                if entry.get("skill_id") != skill_id
            ]
            installed_agents = list(dict.fromkeys(
                str(record.get("ecosystem"))
                for record in installations
                if isinstance(record, dict) and record.get("ecosystem")
            ))
            entries.append({
                "skill_id": skill_id,
                "display_name": result.get("display_name"),
                "description": result.get("description"),
                "source": result.get("source"),
                "source_path": result.get("source_path"),
                "sha": content_sha,
                "downloaded_at": downloaded_at,
                "agents": (
                    list(selected_ecosystems)
                    if selected_ecosystems is not None
                    else installed_agents
                ),
                "installations": [
                    dict(record) for record in installations
                ],
            })
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_ledger = self.ledger_path.with_name(
                f".{self.ledger_path.name}.tmp",
            )
            temporary_ledger.write_text(
                json.dumps(entries, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_ledger.replace(self.ledger_path)
        resolved_path = dest_dir.resolve()
        if return_details:
            return {
                "path": resolved_path,
                "installations": tuple(
                    dict(record) for record in installations
                ),
            }
        return resolved_path
