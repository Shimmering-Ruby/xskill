"""trigger_probe 单测：mock agno 工厂 + skill_index，不调真 LLM/embedding。"""

from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pytest

from xskill.skill import trigger_probe as tp


# ─────────────────────────────────────────────────────────────────
# 测试替身
# ─────────────────────────────────────────────────────────────────

class _FakeAgent:
    """模拟 agno 代理：run() 时调用 `choose` 指定的工具（mimic agno 内部吞
    StopAgentRun）。choose=None 表示什么都不调。"""

    def __init__(self, tools, choose):
        self.tools = tools
        self.choose = choose

    def run(self, query):  # noqa: ARG002
        if self.choose is None:
            return
        for t in self.tools:
            if t.__name__ == self.choose:
                try:
                    t()
                except Exception:  # StopAgentRun 之类 —— agno 内部会吞
                    pass
                return


def _factory_choosing(tool_name):
    def factory(*, instructions, tools, **kwargs):  # noqa: ARG001
        return _FakeAgent(tools, tool_name)
    return factory


class _FakeEmbed:
    def __init__(self, vec):
        self._vec = np.asarray(vec, dtype=np.float32)

    def encode(self, text):  # noqa: ARG002
        return self._vec


class _FakeSkill:
    def __init__(self, name, description, path):
        self.name = name
        self.description = description
        self.path = Path(path)


# ─────────────────────────────────────────────────────────────────
# probe_trigger
# ─────────────────────────────────────────────────────────────────

def test_probe_returns_self_when_agent_calls_self_tool():
    catalog = [{"name": "other-skill", "description": "do other things"}]
    factory = _factory_choosing(tp._slug_to_tool("my-skill"))
    out = tp.probe_trigger(
        "fix my django migration", "my-skill", "Use this for django fixes",
        catalog, agno_agent_factory=factory, desc_cap=256,
    )
    assert out == "my-skill"


def test_probe_returns_decoy_when_agent_calls_decoy_tool():
    catalog = [{"name": "other-skill", "description": "do other things"}]
    factory = _factory_choosing(tp._slug_to_tool("other-skill"))
    out = tp.probe_trigger(
        "q", "my-skill", "desc", catalog,
        agno_agent_factory=factory, desc_cap=256,
    )
    assert out == "other-skill"


def test_probe_returns_none_when_agent_calls_nothing():
    factory = _factory_choosing(None)
    out = tp.probe_trigger(
        "q", "my-skill", "desc", [],
        agno_agent_factory=factory, desc_cap=256,
    )
    assert out == "NONE"


def test_probe_returns_none_when_agent_calls_only_stub():
    factory = _factory_choosing("_stub_read_file")
    out = tp.probe_trigger(
        "q", "my-skill", "desc", [{"name": "x", "description": "d"}],
        agno_agent_factory=factory, desc_cap=256,
    )
    assert out == "NONE"


def test_probe_swallows_agent_exception_as_none():
    class _BoomFactory:
        def __call__(self, *, instructions, tools, **kwargs):  # noqa: ARG002
            class _A:
                def run(self, q):  # noqa: ARG002
                    raise RuntimeError("network down")
            return _A()

    out = tp.probe_trigger(
        "q", "my-skill", "desc", [],
        agno_agent_factory=_BoomFactory(), desc_cap=256,
    )
    assert out == "NONE"


def test_slug_to_tool_handles_dashes_and_leading_digit():
    assert tp._slug_to_tool("django-fix") == "use_django_fix"
    assert tp._slug_to_tool("3d-render").startswith("use_")


# ─────────────────────────────────────────────────────────────────
# build_probe_catalog
# ─────────────────────────────────────────────────────────────────

def _write_index(root: Path, names, embeddings):
    with open(root / ".skill_index.pkl", "wb") as f:
        pickle.dump(
            {"skill_names": names,
             "embeddings": np.asarray(embeddings, dtype=np.float32)},
            f,
        )


def test_build_catalog_main_only_ranked_capped_truncated(tmp_path, monkeypatch):
    # index: b 最近(1.0), a 次(0.8), self(排除), c(0) —— c 还是 baby(无 main)
    names = ["a", "b", "c", "self"]
    embs = [[0.8, 0.6, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.9, 0.1, 0.0]]
    _write_index(tmp_path, names, embs)

    skills = [
        _FakeSkill("a", "alpha skill description", tmp_path / "a"),
        _FakeSkill("b", "B" * 500, tmp_path / "b"),   # 超长 → 测截断
        _FakeSkill("c", "charlie baby stub", tmp_path / "c"),
        _FakeSkill("self", "the candidate", tmp_path / "self"),
    ]
    monkeypatch.setattr(tp, "SkillRepo", lambda root: skills)
    # a,b 是 main；c 是 baby（无 main）；self 不参与
    monkeypatch.setattr(
        tp, "main_sha",
        lambda p: None if Path(p).name == "c" else "deadbeef",
    )

    catalog = tp.build_probe_catalog(
        "some query", "self",
        skill_root=tmp_path, embed_client=_FakeEmbed([1.0, 0.0, 0.0]),
        max_skills=5, desc_cap=64,
    )
    got = [e["name"] for e in catalog]
    assert got == ["b", "a"]              # 按 cosine 降序；c(baby) 与 self 排除
    assert len(catalog[0]["description"]) == 64   # b 超长被截到 cap


def test_build_catalog_respects_max_skills(tmp_path, monkeypatch):
    names = ["a", "b"]
    _write_index(tmp_path, names, [[1.0, 0.0], [0.9, 0.1]])
    skills = [
        _FakeSkill("a", "aa", tmp_path / "a"),
        _FakeSkill("b", "bb", tmp_path / "b"),
    ]
    monkeypatch.setattr(tp, "SkillRepo", lambda root: skills)
    monkeypatch.setattr(tp, "main_sha", lambda p: "sha")
    catalog = tp.build_probe_catalog(
        "q", "self", skill_root=tmp_path,
        embed_client=_FakeEmbed([1.0, 0.0]), max_skills=1, desc_cap=0,
    )
    assert [e["name"] for e in catalog] == ["a"]


def test_build_catalog_empty_when_index_missing(tmp_path):
    catalog = tp.build_probe_catalog(
        "q", "self", skill_root=tmp_path,
        embed_client=_FakeEmbed([1.0]), max_skills=5, desc_cap=0,
    )
    assert catalog == []
