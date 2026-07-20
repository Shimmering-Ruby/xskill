"""trigger_probe 单测：mock agno 工厂 + skill_index，不调真 LLM/embedding。"""

from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np

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
                    return
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
    # 空仓（无任何 skill 目录）→ 无竞争模式：返回 []，但不许静默——必须 WARNING
    catalog = tp.build_probe_catalog(
        "q", "self", skill_root=tmp_path,
        embed_client=_FakeEmbed([1.0]), max_skills=5, desc_cap=0,
    )
    assert catalog == []


# ─────────────────────────────────────────────────────────────────
# index 缺失时的行为（rebuild --force 必现路径，不是边角条件）
# ─────────────────────────────────────────────────────────────────

class _FakeBatchEmbed:
    """带 encode_batch 的 fake embed（rebuild_skill_index 需要）。"""

    def __init__(self, dim=3):
        self.dim = dim
        self.batch_calls = 0

    def _vec(self, text):
        # 确定性 hash → 向量（同文本同向量）
        h = abs(hash(text))
        v = np.asarray([(h >> (8 * i)) % 251 + 1 for i in range(self.dim)],
                       dtype=np.float32)
        return v

    def encode(self, text):
        return self._vec(text)

    def encode_batch(self, texts):
        self.batch_calls += 1
        return np.stack([self._vec(t) for t in texts])


class _BoomEmbed:
    """encode/encode_batch 一律炸——用于断言某路径绝不触 embedding。"""

    def encode(self, text):  # noqa: ARG002
        raise AssertionError("不该调 encode")

    def encode_batch(self, texts):  # noqa: ARG002
        raise RuntimeError("embedding backend down")


def _make_skill_dir(root: Path, name: str, description: str) -> Path:
    d = root / name
    d.mkdir()
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n"
        "metadata:\n  version: 1\n---\n\n# x\n\nbody\n",
        encoding="utf-8",
    )
    return d


def test_build_catalog_rebuilds_index_from_main_when_missing(
        tmp_path, monkeypatch):
    """index 缺失 + 存在 main 竞争技能 → 自动重建索引，诱饵清单非空。"""
    _make_skill_dir(tmp_path, "self-skill", "the candidate skill")
    _make_skill_dir(tmp_path, "decoy-a", "analyze logs and error traces")
    _make_skill_dir(tmp_path, "decoy-b", "deploy apps to kubernetes")
    monkeypatch.setattr(tp, "main_sha", lambda p: "deadbeef")

    embed = _FakeBatchEmbed()
    catalog = tp.build_probe_catalog(
        "find the error in my log", "self-skill",
        skill_root=tmp_path, embed_client=embed, max_skills=5, desc_cap=256,
    )
    assert (tmp_path / ".skill_index.pkl").is_file(), "重建后索引必须落盘"
    assert embed.batch_calls == 1
    got = {e["name"] for e in catalog}
    assert got == {"decoy-a", "decoy-b"}      # self 排除，两个 main 竞争者都在


def test_build_catalog_no_competition_mode_warns_and_skips_embed(
        tmp_path, monkeypatch, caplog):
    """全库只有本 skill（重建也不会有竞争者）→ 降级无竞争模式：
    显式 WARNING + catalog_size=0 标记语，且绝不触 embedding。"""
    _make_skill_dir(tmp_path, "self-skill", "the candidate skill")
    monkeypatch.setattr(tp, "main_sha", lambda p: "deadbeef")

    with caplog.at_level("WARNING", logger="xskill.skill_edit_agent"):
        catalog = tp.build_probe_catalog(
            "q", "self-skill", skill_root=tmp_path,
            embed_client=_BoomEmbed(), max_skills=5, desc_cap=0,
        )
    assert catalog == []
    text = caplog.text
    assert "无竞争" in text
    assert "catalog_size=0" in text


def test_build_catalog_rebuild_failure_degrades_with_warning(
        tmp_path, monkeypatch, caplog):
    """有竞争者但重建失败（embedding 后端炸）→ 不抛、WARNING、返回空清单。"""
    _make_skill_dir(tmp_path, "self-skill", "candidate")
    _make_skill_dir(tmp_path, "decoy-a", "analyze logs")
    monkeypatch.setattr(tp, "main_sha", lambda p: "deadbeef")

    with caplog.at_level("WARNING", logger="xskill.skill_edit_agent"):
        catalog = tp.build_probe_catalog(
            "q", "self-skill", skill_root=tmp_path,
            embed_client=_BoomEmbed(), max_skills=5, desc_cap=0,
        )
    assert catalog == []
    assert "重建" in caplog.text and "catalog_size=0" in caplog.text
