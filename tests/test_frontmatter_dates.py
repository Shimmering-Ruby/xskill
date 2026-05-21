"""
test_frontmatter_dates.py -- 日期消毒器不准 LLM 瞎写 created/last_updated
========================================================================
覆盖 generated skill 中观察到的真实 bug：
  - created: '2026-05-20' （未来日期）
  - created: '2024-05-20' （LLM 想当然的"一年前"）
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import pytest


@pytest.fixture(autouse=True)
def _reset_skill_tools_ctx():
    """每个 case 前后重置 _ctx / _ctx_v2，避免其他 test 文件污染 write_file 的
    skill_dir 判定（write_file 优先用 _ctx_v2 而非 _ctx）。"""
    from xskill.agents import skill_tools as ST
    saved_v2 = dict(ST._ctx_v2)
    saved_v1 = dict(ST._ctx)
    ST._ctx_v2["skill_dir"] = None
    ST._ctx_v2["store"] = None
    ST._ctx_v2["embed_client"] = None
    ST._ctx_v2["traj_root"] = None
    yield
    ST._ctx_v2.update(saved_v2)
    ST._ctx.update(saved_v1)


def test_sanitize_future_created_date():
    from xskill.agents.skill_tools import _sanitize_frontmatter_dates
    future = (date.today() + timedelta(days=365)).isoformat()
    fm = {"metadata": {"created": future, "last_updated": future}}
    _sanitize_frontmatter_dates(fm)
    assert fm["metadata"]["created"] == date.today().isoformat(), \
        "未来日期必须被重置为今天"


def test_sanitize_auto_placeholder_created():
    from xskill.agents.skill_tools import _sanitize_frontmatter_dates
    fm = {"metadata": {"created": "<AUTO>", "last_updated": "<AUTO>"}}
    _sanitize_frontmatter_dates(fm)
    assert fm["metadata"]["created"] == date.today().isoformat()


def test_sanitize_empty_created():
    from xskill.agents.skill_tools import _sanitize_frontmatter_dates
    fm = {"metadata": {}}
    _sanitize_frontmatter_dates(fm)
    assert fm["metadata"]["created"] == date.today().isoformat()


def test_sanitize_keeps_valid_past_created():
    """历史 created（合法 ISO 且非未来）要保留，别把老 skill 的 created 改掉"""
    from xskill.agents.skill_tools import _sanitize_frontmatter_dates
    past = "2024-11-15"  # 真实过去日期
    fm = {"metadata": {"created": past}}
    _sanitize_frontmatter_dates(fm)
    assert fm["metadata"]["created"] == past, "合法过去日期不应被覆盖"


def test_sanitize_last_updated_always_now():
    from xskill.agents.skill_tools import _sanitize_frontmatter_dates
    fm = {"metadata": {"created": "2024-01-01", "last_updated": "1970-01-01T00:00:00"}}
    _sanitize_frontmatter_dates(fm)
    # 检查是合法 ISO datetime，且年份是今天或之后
    ts = datetime.fromisoformat(fm["metadata"]["last_updated"])
    assert ts.date() == date.today(), "last_updated 必须被覆盖到今天"


def test_write_file_sanitizes_skill_md(tmp_path, monkeypatch):
    """write_file 写 SKILL.md 时自动消毒（集成层）"""
    from xskill.agents import skill_tools
    skill_tools._ctx["skill_dir"] = tmp_path
    sk = tmp_path / "fix-x"
    sk.mkdir()
    bad = """---
name: fix-x
description: test
metadata:
  created: "2099-12-31"
  last_updated: "2099-12-31"
---

# body
"""
    skill_tools.write_file(str(sk / "SKILL.md"), bad)
    text = (sk / "SKILL.md").read_text()
    assert "2099-12-31" not in text, "未来日期必须被消毒掉"
    assert date.today().isoformat() in text


def test_write_file_leaves_nonskill_md_alone(tmp_path):
    """写非 SKILL.md 的文件不改内容"""
    from xskill.agents import skill_tools
    skill_tools._ctx["skill_dir"] = tmp_path
    sk = tmp_path / "fix-x"
    sk.mkdir()
    content = "---\ncreated: 2099-12-31\n---\nbody"
    skill_tools.write_file(str(sk / "references" / "notes.md"), content)
    text = (sk / "references" / "notes.md").read_text()
    assert text == content, "非 SKILL.md 文件不应被处理"


# ── v1 N/M warning 消毒 / source_trajs gate 已删除 ─────────────────
# 旧 v1 path B 强制 source_trajs ≥ 3 + N/M 分母消毒在 AtomTask 重构中
# 整体废弃：v2 用 source_atoms 引用 atom 而非 traj，质量保障靠 candidates
# buffer 累计 weightscore ≥ 10 的硬门槛，不需要 SKILL.md 写入端再卡一道。
# 相关单测随 _sanitize_warning_fractions / _validate_skill_md_gate 函数
# 一并移除。


def test_v2_write_skill_md_with_source_atoms_not_blocked(tmp_path):
    """v2 SkillEditAgent 写出来的 SKILL.md 用 source_atoms 而非 source_trajs；
    必须能直接写入，不被旧 source_trajs ≥ 3 gate 拦下。"""
    from xskill.agents import skill_tools
    skill_tools._ctx["skill_dir"] = tmp_path
    sk = tmp_path / "fix-django"
    sk.mkdir()
    content = """---
name: fix-django
description: test v2
metadata:
  version: 1
  created: "<AUTO>"
  last_updated: "<AUTO>"
  source_atoms: ["atom_traj_x_0001"]
  frozen: false
  use_count: 0
---

# 修复 django 迁移冲突

## 检测冲突

1. 跑 `python manage.py showmigrations` 看冲突。

   > ⚠️ 见 atom_traj_x_0001：直接 fake-apply 会污染 django_migrations 表。
"""
    result = skill_tools.write_file(str(sk / "SKILL.md"), content)
    assert not result.startswith("error"), f"write_file 不应被拦下: {result}"
    assert (sk / "SKILL.md").is_file()
    text = (sk / "SKILL.md").read_text(encoding="utf-8")
    assert "source_atoms" in text
    assert "atom_traj_x_0001" in text
