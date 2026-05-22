"""
pipeline/trajectory.py — Trajectory 实体类 + 轨迹 header 解析
═══════════════════════════════════════════════════════════════
单条轨迹的视图。纯数据 + 反查接口；不持 LLM。

另含 ``parse_traj_header``：从轨迹 markdown 头部提取
``<!-- xskill:... -->`` 元数据注释。

约定格式::

    <!-- xskill:skill=fix_django_migration side=staging sha=a1b2c3d4 -->

所有字段均可选，按 key=value 空格分隔。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from xskill.pipeline.registry import Registry


class Trajectory:
    """一条轨迹的视图。包 .md / .json / .meta 三件套。

    若构造时不传 registry，则 skill_used / skill_generated / canary_side 全返回 None
    （独立加载模式：只读文件本身，不查 DB）。
    """

    def __init__(self, path: Path, registry: Optional["Registry"] = None):
        self.path = Path(path)
        self._registry = registry
        self._meta_cache: Optional[dict] = None

    @classmethod
    def load(cls, path: str | Path,
             registry: Optional["Registry"] = None) -> "Trajectory":
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"trajectory file not found: {p}")
        return cls(path=p, registry=registry)

    # ─── 文件三件套 ────────────────────────────────────────────────
    @property
    def md_text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def meta(self) -> dict:
        """从 <traj>.md.meta 读结构化 meta；不存在返回 {}。"""
        if self._meta_cache is not None:
            return self._meta_cache
        meta_path = self.path.parent / f"{self.path.name}.meta"
        if meta_path.exists():
            self._meta_cache = json.loads(meta_path.read_text(encoding="utf-8"))
        else:
            self._meta_cache = {}
        return self._meta_cache

    @property
    def raw_json(self) -> dict:
        """从 <traj>.json 读上游原始数据；不存在返回 {}。"""
        json_path = self.path.with_suffix(".json")
        if json_path.exists():
            return json.loads(json_path.read_text(encoding="utf-8"))
        return {}

    @property
    def is_success(self) -> bool:
        return bool(self.meta.get("success"))

    # ─── DB 反查 ──────────────────────────────────────────────────
    @property
    def _row(self) -> Optional[dict]:
        if self._registry is None:
            return None
        return self._registry.trajectory_status(self.path)

    @property
    def skill_used(self) -> Optional[str]:
        row = self._row
        return row.get("skill_used") if row else None

    @property
    def skill_generated(self) -> Optional[str]:
        row = self._row
        return row.get("skill_generated") if row else None

    @property
    def canary_side(self) -> Optional[str]:
        row = self._row
        return row.get("canary_side") if row else None

    @property
    def status(self) -> Optional[str]:
        row = self._row
        return row.get("status") if row else None

    def __repr__(self) -> str:
        return f"Trajectory({self.path.name})"


# =============================================================================
# 轨迹 header 解析
# =============================================================================

_T2S_RE = re.compile(
    r"<!--\s*xskill:"
    r"((?:\s*\w+=\S+)+)"
    r"\s*-->"
)

_KV_RE = re.compile(r"(\w+)=(\S+)")


def parse_traj_header(md_text: str) -> dict | None:
    """解析轨迹中的 ``<!-- xskill:... -->`` 元数据注释。

    只扫描前 500 字符。返回 ``None`` 表示无 xskill 标记。
    返回示例::

        {"skill": "fix_django", "side": "staging", "sha": "a1b2c3d4"}
    """
    m = _T2S_RE.search(md_text[:500])
    if not m:
        return None
    pairs = _KV_RE.findall(m.group(1))
    if not pairs:
        return None
    return dict(pairs)
