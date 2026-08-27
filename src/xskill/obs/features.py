"""obs/features.py —— 一次 agent 跑的行为特征，进程内累计后落一份 JSON
================================================================

为什么不直接读 OTel span：span 走 exporter 出去（Phoenix / 文件），要看
"这趟 compact 了几次、read_file 打了多少下、到底读了哪些轨迹"就得先把
span 收回来再聚合，实验脚本每次都要写一遍解析。这里在埋点的同一处顺手
把计数记在进程里，跑完直接 dump 成 ``features.json``——Phoenix 看时间
分布和瀑布，JSON 做跨 job 对比。

线程模型：watcher 把每条 agent 跑在线程池各自线程里，计数器全部加锁。
一个进程一次 job（容器实验就是这么跑的），所以收集器是进程级单例。

隐私：只记工具名、计数、轨迹 id、路径的 basename。不落 prompt 正文、
不落工具返回内容、不落 API key。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 轨迹文件名形如 traj_cursor_<project>_<sid8>.md / traj_oc_<...>.json。
# 只认 basename 的 traj_ 前缀，避免把普通文件误当轨迹。
_TRAJ_STEM = re.compile(r"^(traj_[A-Za-z0-9][A-Za-z0-9._\-]*?)(?:\.md|\.json)?$")

# 读类工具：这几个的调用次数是"读取行为"的主口径。
READ_TOOLS = (
    "read_file", "grep_files", "list_files", "skill_read", "read_traj",
    "traj_search", "traj_cards", "atom_search",
)
# 只看过卡片不算精读；精读口径就是 read_traj 读到的不同 traj_id。
CARD_TOOLS = ("traj_cards", "atom_search")
TRAJ_READ_TOOLS = ("read_traj", "read_file", "grep_files")


def traj_id_from_path(path: Any) -> str | None:
    """从工具参数里的路径抽轨迹 id；不是轨迹就返回 None。"""
    text = str(path or "").strip()
    if not text:
        return None
    name = Path(text).name
    matched = _TRAJ_STEM.match(name)
    return matched.group(1) if matched else None


@dataclass
class FeatureCollector:
    """一次 job 的行为特征。字段名就是 features.json 的键。"""

    job: str = ""
    agent: str = ""
    # 上下文窗口配置：compact 次数只有配着窗口大小才读得懂
    max_context: int | None = None
    compact_token_limit: int | None = None
    enable_spill: bool | None = None

    llm_rounds: int = 0
    compact_count: int = 0
    compact_seconds: float = 0.0
    compact_events: list[dict] = field(default_factory=list)
    spill_count: int = 0

    tool_calls: dict[str, int] = field(default_factory=dict)
    tool_call_total: int = 0
    tool_errors: dict[str, int] = field(default_factory=dict)
    tool_seconds: dict[str, float] = field(default_factory=dict)

    # 真正精读过的轨迹（read_file / grep_files / read_traj），按首次读到的顺序去重
    read_traj_ids: list[str] = field(default_factory=list)
    traj_read_calls: int = 0
    # session_card / session_cards 扫过的 id，不算精读
    card_traj_ids: list[str] = field(default_factory=list)
    card_traj_calls: int = 0
    _card_seen: set[str] = field(
        default_factory=set, repr=False, compare=False,
    )

    started_at: float | None = None
    finished_at: float | None = None
    error: str = ""

    _lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False,
    )
    _traj_seen: set[str] = field(
        default_factory=set, repr=False, compare=False,
    )

    # ── 记账入口（全部 best-effort，绝不因为埋点把 agent 跑崩）──────

    def start(self, *, job: str, agent: str) -> None:
        with self._lock:
            self.job = job
            self.agent = agent
            self.started_at = time.time()

    def finish(self, *, error: str = "") -> None:
        with self._lock:
            self.finished_at = time.time()
            if error:
                self.error = error

    def note_budget(
        self,
        *,
        max_context: int | None,
        compact_token_limit: int | None,
        enable_spill: bool | None = None,
    ) -> None:
        with self._lock:
            self.max_context = max_context
            self.compact_token_limit = compact_token_limit
            self.enable_spill = enable_spill

    def note_llm_round(self) -> int:
        """记一轮 model.invoke，返回这是第几轮（1 起）。"""
        with self._lock:
            self.llm_rounds += 1
            return self.llm_rounds

    def note_compact(
        self,
        *,
        seconds: float,
        tokens_before: int | None = None,
        tokens_after: int | None = None,
        attempts: int = 1,
        ok: bool = True,
    ) -> None:
        with self._lock:
            self.compact_count += 1
            self.compact_seconds += max(0.0, seconds)
            self.compact_events.append({
                "index": self.compact_count,
                "at_llm_round": self.llm_rounds,
                "seconds": round(max(0.0, seconds), 3),
                "tokens_before": tokens_before,
                "tokens_after": tokens_after,
                "attempts": attempts,
                "ok": ok,
            })

    def note_spill(self) -> None:
        with self._lock:
            self.spill_count += 1

    def note_tool_call(
        self,
        name: str,
        *,
        arguments: dict | None = None,
        seconds: float = 0.0,
        failed: bool = False,
    ) -> None:
        tool = str(name or "tool")
        with self._lock:
            self.tool_calls[tool] = self.tool_calls.get(tool, 0) + 1
            self.tool_call_total += 1
            if seconds:
                self.tool_seconds[tool] = round(
                    self.tool_seconds.get(tool, 0.0) + max(0.0, seconds), 3,
                )
            if failed:
                self.tool_errors[tool] = self.tool_errors.get(tool, 0) + 1
            if tool in CARD_TOOLS:
                for traj_id in self._card_ids_from_arguments(arguments):
                    self.card_traj_calls += 1
                    if traj_id not in self._card_seen:
                        self._card_seen.add(traj_id)
                        self.card_traj_ids.append(traj_id)
            elif tool in TRAJ_READ_TOOLS:
                for traj_id in self._path_traj_ids_from_arguments(arguments):
                    self.traj_read_calls += 1
                    if traj_id not in self._traj_seen:
                        self._traj_seen.add(traj_id)
                        self.read_traj_ids.append(traj_id)

    @staticmethod
    def _path_traj_ids_from_arguments(arguments: dict | None) -> list[str]:
        """认 read_traj 的 traj_id，以及旧读法的 path。目录不算精读。"""
        if not isinstance(arguments, dict):
            return []
        found: list[str] = []
        seen: set[str] = set()
        for key in ("traj_id", "path", "file_path", "traj_path"):
            if key in arguments:
                traj_id = traj_id_from_path(arguments.get(key))
                if traj_id and traj_id not in seen:
                    seen.add(traj_id)
                    found.append(traj_id)
        return found

    @staticmethod
    def _card_ids_from_arguments(arguments: dict | None) -> list[str]:
        if not isinstance(arguments, dict):
            return []
        found: list[str] = []
        seen: set[str] = set()

        def _add(raw: Any) -> None:
            traj_id = traj_id_from_path(raw)
            if traj_id and traj_id not in seen:
                seen.add(traj_id)
                found.append(traj_id)

        if "traj_id" in arguments:
            _add(arguments.get("traj_id"))
        raw_ids = arguments.get("traj_ids")
        if raw_ids is not None:
            for part in str(raw_ids).replace(",", " ").split():
                _add(part)
        return found

    # ── 导出 ────────────────────────────────────────────────────

    def as_dict(self) -> dict:
        with self._lock:
            wall = None
            if self.started_at is not None and self.finished_at is not None:
                wall = round(self.finished_at - self.started_at, 3)
            read_tool_calls = {
                name: self.tool_calls[name]
                for name in READ_TOOLS
                if name in self.tool_calls
            }
            return {
                "job": self.job,
                "agent": self.agent,
                "wall_seconds": wall,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "context": {
                    "max_context": self.max_context,
                    "compact_token_limit": self.compact_token_limit,
                    "enable_spill": self.enable_spill,
                },
                "llm_rounds": self.llm_rounds,
                "compact_count": self.compact_count,
                "compact_seconds": round(self.compact_seconds, 3),
                "compact_events": list(self.compact_events),
                "spill_count": self.spill_count,
                "tool_call_total": self.tool_call_total,
                "tool_calls": dict(sorted(self.tool_calls.items())),
                "read_tool_calls": read_tool_calls,
                "read_tool_call_total": sum(read_tool_calls.values()),
                "tool_errors": dict(sorted(self.tool_errors.items())),
                "tool_seconds": dict(sorted(self.tool_seconds.items())),
                "traj_read_calls": self.traj_read_calls,
                "read_traj_count": len(self.read_traj_ids),
                "read_traj_ids": list(self.read_traj_ids),
                "card_traj_calls": self.card_traj_calls,
                "card_traj_count": len(self.card_traj_ids),
                "card_traj_ids": list(self.card_traj_ids),
                "error": self.error,
            }

    def dump(self, path: Path | str) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.as_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return target


_COLLECTOR = FeatureCollector()


def collector() -> FeatureCollector:
    return _COLLECTOR


def reset_collector() -> FeatureCollector:
    """测试与多次跑用：换一个干净的收集器。"""
    global _COLLECTOR
    _COLLECTOR = FeatureCollector()
    return _COLLECTOR


def features_path() -> Path | None:
    """``XSKILL_OTEL_OUT`` 指到的输出目录下的 features.json。"""
    out = os.environ.get("XSKILL_OTEL_OUT", "").strip()
    if not out:
        return None
    return Path(out) / "features.json"
