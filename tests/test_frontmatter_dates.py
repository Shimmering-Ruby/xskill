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


def test_sanitize_future_created_date():
    from traj2skill.skill_tools import _sanitize_frontmatter_dates
    future = (date.today() + timedelta(days=365)).isoformat()
    fm = {"metadata": {"created": future, "last_updated": future}}
    _sanitize_frontmatter_dates(fm)
    assert fm["metadata"]["created"] == date.today().isoformat(), \
        "未来日期必须被重置为今天"


def test_sanitize_auto_placeholder_created():
    from traj2skill.skill_tools import _sanitize_frontmatter_dates
    fm = {"metadata": {"created": "<AUTO>", "last_updated": "<AUTO>"}}
    _sanitize_frontmatter_dates(fm)
    assert fm["metadata"]["created"] == date.today().isoformat()


def test_sanitize_empty_created():
    from traj2skill.skill_tools import _sanitize_frontmatter_dates
    fm = {"metadata": {}}
    _sanitize_frontmatter_dates(fm)
    assert fm["metadata"]["created"] == date.today().isoformat()


def test_sanitize_keeps_valid_past_created():
    """历史 created（合法 ISO 且非未来）要保留，别把老 skill 的 created 改掉"""
    from traj2skill.skill_tools import _sanitize_frontmatter_dates
    past = "2024-11-15"  # 真实过去日期
    fm = {"metadata": {"created": past}}
    _sanitize_frontmatter_dates(fm)
    assert fm["metadata"]["created"] == past, "合法过去日期不应被覆盖"


def test_sanitize_last_updated_always_now():
    from traj2skill.skill_tools import _sanitize_frontmatter_dates
    fm = {"metadata": {"created": "2024-01-01", "last_updated": "1970-01-01T00:00:00"}}
    _sanitize_frontmatter_dates(fm)
    # 检查是合法 ISO datetime，且年份是今天或之后
    ts = datetime.fromisoformat(fm["metadata"]["last_updated"])
    assert ts.date() == date.today(), "last_updated 必须被覆盖到今天"


def test_write_file_sanitizes_skill_md(tmp_path, monkeypatch):
    """write_file 写 SKILL.md 时自动消毒（集成层）"""
    from traj2skill import skill_tools
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
    from traj2skill import skill_tools
    skill_tools._ctx["skill_dir"] = tmp_path
    sk = tmp_path / "fix-x"
    sk.mkdir()
    content = "---\ncreated: 2099-12-31\n---\nbody"
    skill_tools.write_file(str(sk / "references" / "notes.md"), content)
    text = (sk / "references" / "notes.md").read_text()
    assert text == content, "非 SKILL.md 文件不应被处理"


# ── warning fraction 消毒（N/M 编造检查）────────────────────────────────
def test_sanitize_fraction_strips_when_denominator_exceeds_sources():
    """observed real bug: "3/7 条失败轨迹" 但 source_trajs 只有 4 条"""
    from traj2skill.skill_tools import _sanitize_warning_fractions
    body = "> ⚠️ 3/7 条失败轨迹未完整查看函数代码\n\n> ⚠️ 2/7 条轨迹修复后遗漏了边界"
    new_body, n = _sanitize_warning_fractions(body, source_trajs_count=4)
    assert "3/7" not in new_body
    assert "2/7" not in new_body
    assert "源失败轨迹中" in new_body or "源轨迹中" in new_body
    assert n == 2


def test_sanitize_fraction_keeps_valid():
    """N/M 在合理范围内时不改"""
    from traj2skill.skill_tools import _sanitize_warning_fractions
    body = "> ⚠️ 2/3 条失败轨迹有相同错误"
    new_body, n = _sanitize_warning_fractions(body, source_trajs_count=3)
    assert new_body == body
    assert n == 0


def test_sanitize_fraction_blocks_numerator_greater_than_denominator():
    from traj2skill.skill_tools import _sanitize_warning_fractions
    body = "> ⚠️ 5/3 条轨迹"
    new_body, n = _sanitize_warning_fractions(body, source_trajs_count=3)
    assert "5/3" not in new_body
    assert n == 1


def test_sanitize_fraction_integration_via_write_file(tmp_path):
    from traj2skill import skill_tools
    from datetime import date
    skill_tools._ctx["skill_dir"] = tmp_path
    sk = tmp_path / "fix-x"
    sk.mkdir()
    content = f"""---
name: fix-x
description: test
metadata:
  created: "{date.today().isoformat()}"
  source_trajs: [traj_a, traj_b]
---

# body

1. step one
   > ⚠️ 3/9 条失败轨迹做了错事
"""
    skill_tools.write_file(str(sk / "SKILL.md"), content)
    text = (sk / "SKILL.md").read_text()
    assert "3/9" not in text, "source_trajs=2，M=9 属于编造，必须被替换"
