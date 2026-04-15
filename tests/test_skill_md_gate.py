"""
test_skill_md_gate.py -- write_file(SKILL.md) 硬拦截
=====================================================
覆盖三条 gate 规则：
  R1 source_trajs 只接受 traj_NNNN 规范形式
  R2 每条 traj_NNNN 必须在 data/ 下有对应 .md 文件
  R3 body 有实质内容时，去重后的 source_trajs 必须 ≥ 3

gate 被拦截时 write_file 不应真的写文件，而是返回 error 字符串给 agent。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import pytest


def _mk_workspace(tmp_path: Path):
    """搭一个 skill_dir + data_dir 的临时环境"""
    from traj2skill import skill_tools
    (tmp_path / "skill").mkdir()
    (tmp_path / "data").mkdir()
    skill_tools._ctx["skill_dir"] = tmp_path / "skill"
    skill_tools._ctx["data_dir"] = tmp_path / "data"
    return skill_tools


def _mk_trajs(data_dir: Path, ids: list[str]):
    """在 data_dir 里造出对应的 traj_NNNN.md 文件"""
    for tid in ids:
        (data_dir / f"{tid}.md").write_text("# stub", encoding="utf-8")


def _mk_skill_md(trajs: list[str], body: str = "# title\n\nintro.\n") -> str:
    today = date.today().isoformat()
    trajs_yaml = "\n".join(f"  - {t}" for t in trajs)
    return f"""---
name: fix-x
description: test
metadata:
  version: 1
  created: "{today}"
  last_updated: "{today}"
  source_trajs:
{trajs_yaml}
  frozen: false
  use_count: 0
---

{body}"""


# ── R1: non-canonical source_trajs ─────────────────────────────────────
def test_gate_rejects_swesmith_instance_id(tmp_path):
    st = _mk_workspace(tmp_path)
    _mk_trajs(tmp_path / "data", ["traj_0009"])
    sk = tmp_path / "skill" / "fix-x"
    sk.mkdir()
    content = _mk_skill_md(
        trajs=["joke2k__faker.8b401a7d.func_pm_ctrl_invert_if__bompadwm.8qa84d2e", "traj_0009"],
        body="# title\n\n## stage\n1. step\n",
    )
    result = st.write_file(str(sk / "SKILL.md"), content)
    assert result.startswith("error:"), f"应被 R1 拦截，实际: {result}"
    assert "instance_id" in result or "traj_NNNN" in result
    assert not (sk / "SKILL.md").exists(), "被拦截的写入不应落盘"


def test_gate_accepts_canonical_trajs(tmp_path):
    st = _mk_workspace(tmp_path)
    _mk_trajs(tmp_path / "data", ["traj_0001", "traj_0002", "traj_0003"])
    sk = tmp_path / "skill" / "fix-x"
    sk.mkdir()
    content = _mk_skill_md(
        trajs=["traj_0001", "traj_0002", "traj_0003"],
        body="# title\n\n## stage\n1. step\n",
    )
    result = st.write_file(str(sk / "SKILL.md"), content)
    assert not result.startswith("error:"), f"合法写入不应被拦截，实际: {result}"
    assert (sk / "SKILL.md").exists()


# ── R2: fabricated traj_id ─────────────────────────────────────────────
def test_gate_rejects_nonexistent_traj(tmp_path):
    st = _mk_workspace(tmp_path)
    _mk_trajs(tmp_path / "data", ["traj_0001", "traj_0002"])
    sk = tmp_path / "skill" / "fix-x"
    sk.mkdir()
    content = _mk_skill_md(
        trajs=["traj_0001", "traj_0002", "traj_9999"],  # 9999 不存在
        body="# title\n\n## stage\n1. step\n",
    )
    result = st.write_file(str(sk / "SKILL.md"), content)
    assert result.startswith("error:"), "R2 应拦截不存在的 traj_9999"
    assert "traj_9999" in result


def test_gate_finds_traj_in_nested_data(tmp_path):
    """data/<dataset>/traj_NNNN.md 嵌套一层也能找到"""
    st = _mk_workspace(tmp_path)
    nested = tmp_path / "data" / "swe_smith_demo"
    nested.mkdir()
    _mk_trajs(nested, ["traj_0001", "traj_0002", "traj_0003"])
    sk = tmp_path / "skill" / "fix-x"
    sk.mkdir()
    content = _mk_skill_md(
        trajs=["traj_0001", "traj_0002", "traj_0003"],
        body="# title\n\n## stage\n1. step\n",
    )
    result = st.write_file(str(sk / "SKILL.md"), content)
    assert not result.startswith("error:"), f"嵌套路径找不到？{result}"


# ── R3: substantive body requires ≥3 source_trajs ───────────────────────
def test_gate_rejects_substantive_body_with_two_trajs(tmp_path):
    """用户原始 case: faker skill 只有 2 条 source_trajs 但 body 有阶段标题"""
    st = _mk_workspace(tmp_path)
    _mk_trajs(tmp_path / "data", ["traj_0001", "traj_0002"])
    sk = tmp_path / "skill" / "fix-x"
    sk.mkdir()
    content = _mk_skill_md(
        trajs=["traj_0001", "traj_0002"],
        body="# title\n\n## 阶段\n1. 具体步骤\n",
    )
    result = st.write_file(str(sk / "SKILL.md"), content)
    assert result.startswith("error:"), "R3 应拦截 body 有实质内容但 trajs<3"
    assert "add_candidate" in result or "路径 C" in result


def test_gate_rejects_warning_only_body_with_two_trajs(tmp_path):
    """body 只有一个 warning 也算实质内容"""
    st = _mk_workspace(tmp_path)
    _mk_trajs(tmp_path / "data", ["traj_0001", "traj_0002"])
    sk = tmp_path / "skill" / "fix-x"
    sk.mkdir()
    content = _mk_skill_md(
        trajs=["traj_0001", "traj_0002"],
        body="# title\n\n正文.\n\n> ⚠️ 见 traj_0001\n",
    )
    result = st.write_file(str(sk / "SKILL.md"), content)
    assert result.startswith("error:"), "只有 warning 也算实质内容"


def test_gate_allows_placeholder_scaffold_with_any_trajs(tmp_path):
    """scaffold 骨架（无阶段标题无 warning）source_trajs=[] 也应放行"""
    st = _mk_workspace(tmp_path)
    sk = tmp_path / "skill" / "fix-x"
    sk.mkdir()
    content = _mk_skill_md(
        trajs=[],
        body="# title\n\n(Write body here. Use ## <stage-name> phase headers.)\n",
    )
    result = st.write_file(str(sk / "SKILL.md"), content)
    assert not result.startswith("error:"), f"scaffold 不应被拦截: {result}"


def test_gate_dedup_source_trajs(tmp_path):
    """source_trajs 里重复的 traj_0001 算 1 条，不能用重复凑数"""
    st = _mk_workspace(tmp_path)
    _mk_trajs(tmp_path / "data", ["traj_0001", "traj_0002"])
    sk = tmp_path / "skill" / "fix-x"
    sk.mkdir()
    content = _mk_skill_md(
        trajs=["traj_0001", "traj_0001", "traj_0002"],  # 实际只有 2 unique
        body="# title\n\n## stage\n1. step\n",
    )
    result = st.write_file(str(sk / "SKILL.md"), content)
    assert result.startswith("error:"), "去重后 <3 应被拦截"
