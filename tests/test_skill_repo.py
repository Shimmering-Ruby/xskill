"""SkillRepo 回归测试。

锚定：``SkillRepo.rebuild_index()`` 不应依赖 agent tools 的运行时上下文；
skill index 是 repo 维护逻辑，embed client 由调用方从 config 创建后显式传入。
"""
from __future__ import annotations

import numpy as np
import pytest

from xskill.skill.repo import SkillRepo, rebuild_skill_index


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
    """rebuild_skill_index 脱离 agent tool config，用显式参数直接调。"""
    _make_skill(tmp_path)
    rebuild_skill_index(skill_dir=tmp_path, embed_client=_StubEmbed())
    assert (tmp_path / ".skill_index.pkl").is_file()


def test_rebuild_skill_index_requires_embed_client(tmp_path):
    """缺少 embed client 时 fail loud，不从 agent tool config 偷对象。"""
    _make_skill(tmp_path)
    with pytest.raises(RuntimeError, match="embed_client"):
        rebuild_skill_index(skill_dir=tmp_path, embed_client=None)


def test_rebuild_skill_index_rejects_invalid_scope(tmp_path):
    """非法 scope 直接抛错，不做静默兜底。"""
    _make_skill(tmp_path)
    with pytest.raises(ValueError, match="invalid scope"):
        rebuild_skill_index(
            skill_dir=tmp_path, embed_client=_StubEmbed(), scope="everything",
        )


def test_rebuild_skill_index_default_scope_is_search(tmp_path, monkeypatch):
    """默认 scope=search：不进入 atom 扫描路径。"""
    _make_skill(tmp_path)

    def _boom(*_args, **_kwargs):
        raise AssertionError("AtomTaskStore must not be constructed for scope=search")

    monkeypatch.setattr("xskill.pipeline.atom.AtomTaskStore", _boom)
    rebuild_skill_index(
        skill_dir=tmp_path,
        embed_client=_StubEmbed(),
        atom_store_roots=[tmp_path / "atoms"],
    )
    assert (tmp_path / ".skill_index.pkl").is_file()


def test_rebuild_skill_index_search_scope_skips_atom_scan(tmp_path, monkeypatch):
    """显式 scope=search 同样不构造 AtomTaskStore。"""
    _make_skill(tmp_path)

    def _boom(*_args, **_kwargs):
        raise AssertionError("AtomTaskStore must not be constructed for scope=search")

    monkeypatch.setattr("xskill.pipeline.atom.AtomTaskStore", _boom)
    rebuild_skill_index(
        skill_dir=tmp_path,
        embed_client=_StubEmbed(),
        atom_store_roots=[tmp_path / "atoms"],
        scope="search",
    )


def test_skill_repo_rebuild_index_no_typeerror_regression(tmp_path, monkeypatch):
    """SkillRepo.rebuild_index() 从 config 创建 embed client 后显式传入 repo 函数。"""
    _make_skill(tmp_path)

    # stub 真实 config / 真 embed client，避免读 ~/.xskill 和打真 API
    monkeypatch.setattr("xskill.config.get_config", lambda: {"_stub": True})
    monkeypatch.setattr(
        "xskill.utils.llm.create_embed_client",
        lambda _config: _StubEmbed(),
    )

    SkillRepo(tmp_path).rebuild_index()
    assert (tmp_path / ".skill_index.pkl").is_file(), (
        "rebuild_index 跑完了但索引文件不在——重建链路有问题"
    )
