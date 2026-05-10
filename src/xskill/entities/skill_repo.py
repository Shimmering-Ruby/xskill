"""
entities/skill_repo.py — SkillRepo 集合
═══════════════════════════════════════════
管理 ~/.xskill/skill/ 下所有 Skill 子目录。dict-like + iterable。
顶层 .git 已废弃，所有 git 操作走 <skill>/.git 子仓。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from xskill.entities.skill import Skill

if TYPE_CHECKING:
    from xskill.entities.registry import Registry


class SkillRepo:
    """skill_dir 顶层视图。

    接口：
      repo["foo"]            → Skill | KeyError
      "foo" in repo          → bool
      for s in repo: ...     → 迭代所有 Skill
      len(repo)              → int
      repo.get("foo")        → Skill | None
      repo.rebuild_index()   → 重建 .skill_index.pkl
    """

    def __init__(self, root: Path, registry: Optional["Registry"] = None):
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self._registry = registry

    # ─── dict-like ─────────────────────────────────────────────
    def __getitem__(self, name: str) -> Skill:
        skill_path = self.root / name
        if not (skill_path / "SKILL.md").is_file():
            raise KeyError(f"skill not found: {name}")
        return Skill(path=skill_path, registry=self._registry)

    def get(self, name: str) -> Optional[Skill]:
        try:
            return self[name]
        except KeyError:
            return None

    def __contains__(self, name: str) -> bool:
        return (self.root / name / "SKILL.md").is_file()

    def __iter__(self) -> Iterator[Skill]:
        if not self.root.is_dir():
            return iter([])
        for sub in sorted(self.root.iterdir()):
            if not sub.is_dir():
                continue
            if sub.name.startswith(".") or sub.name == "references":
                continue
            if not (sub / "SKILL.md").is_file():
                continue
            yield Skill(path=sub, registry=self._registry)

    def __len__(self) -> int:
        return sum(1 for _ in self)

    # ─── 索引 ──────────────────────────────────────────────────
    def rebuild_index(self) -> None:
        """重建 .skill_index.pkl（向量检索用）。"""
        from xskill import skill_tools
        from xskill.config import get_config
        from xskill.llm_client import create_embed_client
        cfg = get_config()
        embed = create_embed_client(cfg)
        skill_tools.init_context(self.root, None, None, embed, cfg)
        skill_tools.rebuild_skill_index()

    def __repr__(self) -> str:
        return f"SkillRepo({self.root}, n={len(self)})"
