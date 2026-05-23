"""SkillRepo 回归测试。

锚定：``SkillRepo.rebuild_index()`` 之前走
``skill_tools.init_context(self.root, None, None, embed, cfg)``，``init_context``
里 ``_ctx["data_dir"] = Path(data_dir)`` 对 ``data_dir=None`` 触发
``TypeError: argument should be a str or an os.PathLike ... not 'NoneType'``。
后果：team 模式画像推荐依赖 ``<skill_dir>/.skill_index.pkl``，索引建不出
就一直冷启动退回 ux，特性发挥不出来。
"""
from __future__ import annotations

import numpy as np
import pytest

from xskill.agents.skill_tools import rebuild_skill_index
from xskill.skill.repo import SkillRepo


class _StubEmbed:
    """最小可用的 embed client：encode_batch 返回固定向量。

    不接真 API；索引文件能产出即证明 rebuild 链路通了。
    """

    def encode_batch(self, texts):
        return np.ones((len(texts), 4), dtype=np.float32)


def _make_skill(skill_dir, name: str = "demo-skill") -> None:
    sk = skill_dir / name
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: just for test\n"
        "metadata:\n  tags: [demo]\n"
        "---\n"
        "# body\n",
        encoding="utf-8",
    )


def test_rebuild_skill_index_accepts_explicit_kwargs(tmp_path):
    """rebuild_skill_index 现在能脱离 _ctx 用——给 skill_dir/embed_client
    两个 kwarg 直接调，不需要先调 init_context。"""
    _make_skill(tmp_path)
    rebuild_skill_index(skill_dir=tmp_path, embed_client=_StubEmbed())
    assert (tmp_path / ".skill_index.pkl").is_file()


def test_rebuild_skill_index_fail_loud_when_both_missing():
    """两路都没填（既没 kwarg 又没 init_context）→ RuntimeError，
    fail-loud；不要拿着 None 接着跑出更难懂的 AttributeError。"""
    with pytest.raises(RuntimeError, match="skill_dir"):
        rebuild_skill_index()  # _ctx 在测试上下文里没初始化


def test_skill_repo_rebuild_index_no_typeerror_regression(tmp_path, monkeypatch):
    """回归：SkillRepo.rebuild_index() 之前 init_context(None,None,...)
    会在 ``Path(None)`` 上抛 TypeError，整个 rebuild 链路直接挂。修法是
    SkillRepo 改走显式 kwarg、绕开 init_context。
    """
    _make_skill(tmp_path)

    # stub 真实 config / 真 embed client，避免读 ~/.xskill 和打真 API
    monkeypatch.setattr("xskill.config.get_config", lambda: {"_stub": True})
    monkeypatch.setattr(
        "xskill.utils.llm.create_embed_client",
        lambda cfg: _StubEmbed(),
    )

    SkillRepo(tmp_path).rebuild_index()
    assert (tmp_path / ".skill_index.pkl").is_file(), (
        "rebuild_index 跑完了但索引文件不在——重建链路有问题"
    )
