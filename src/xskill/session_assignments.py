"""
session_assignments.py -- CC session → (side, sha, used_skill) 持久化映射
==========================================================================

设计动机（呼应"灰度链路 session 内一致性"的需求）：

daemon 翻牌子是事件驱动的——每见到一个真正"用了"灰度 skill 的 CC
session，立刻翻一次让下个 session 拿对面 side。但如果有人事后问"session
A 用的是哪 side？"，单看 install_history 反推得出"session 启动那一刻盘
上装的内容"——这没问题。问题是**同一 session 内**的一致性如果要"问得到"
（比如同一 session 触发多个内部子查询），就需要一个权威的 sid→side 表。

这个文件维护那张表。append-only jsonl，每行一条 assignment：

  {"sid": "abc-uuid", "side": "main", "sha": "abc1234",
   "used_skill": true, "t": 1700000000.123}

``used_skill`` 标识"这条 session 是否真触发了 Skill tool 调用我们关心
的灰度 skill"。仅 used_skill=true 的 session 进 ux 评分链路、消耗灰度
配额、触发翻牌。其他 session 桥过来但**透明跳过**，不影响 A/B。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional


class SessionAssignments:
    """thread-safe append + dict lookup for ``sid → record``.

    内存维护一份 sid→record 字典，构造时从 jsonl 加载。append 同时写盘
    + 更新内存。get(sid) 走内存即可，O(1)。
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._cache: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = rec.get("sid")
            if sid:
                # 后写覆盖前写（同一 sid 重复 record 时取最新）
                self._cache[sid] = rec

    def record(
        self,
        *,
        sid: str,
        side: str,
        sha: str = "",
        used_skill: bool = False,
        t: float,
    ) -> dict:
        rec = {
            "sid": sid, "side": side, "sha": sha,
            "used_skill": used_skill, "t": t,
        }
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._cache[sid] = rec
        return rec

    def get(self, sid: str) -> Optional[dict]:
        return self._cache.get(sid)

    def all_sids(self) -> list[str]:
        return list(self._cache.keys())

    def filter_used_skill(self) -> list[dict]:
        """只返回真正 used_skill=true 的 assignments（消耗灰度配额的那些）。"""
        return [r for r in self._cache.values() if r.get("used_skill")]

    def count_by_side(self, *, used_only: bool = True) -> dict[str, int]:
        counts = {"main": 0, "staging": 0}
        for r in self._cache.values():
            if used_only and not r.get("used_skill"):
                continue
            s = r.get("side")
            if s in counts:
                counts[s] += 1
        return counts
