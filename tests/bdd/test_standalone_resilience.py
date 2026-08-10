"""Executable BDD for standalone first-use search (#46) and reindex (#200).

Process-in FastAPI TestClient + deterministic fake embed client. No aimock,
no real network. Intended to run under ordinary ``pytest tests/bdd``.
"""
from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_bdd import given, parsers, scenario, then, when

import xskill.api.app as api_app
from xskill import core
from xskill.skill.repo import rebuild_skill_index
from xskill.utils import llm as llm_mod


pytestmark = [
    pytest.mark.bdd,
    pytest.mark.state_machine,
]


# ── scenarios: #46 first-use local search ─────────────────────────────

@scenario(
    "features/standalone/first_use_local_search.feature",
    "缺 .skill_index.pkl 时 skills/search 返回空列表且不建 embed client",
)
def test_search_missing_index_skips_embed() -> None:
    """Missing index must not construct an embedding client."""


@scenario(
    "features/standalone/first_use_local_search.feature",
    "缺索引时 skills/resolve 返回空结果且不建 embed client",
)
def test_resolve_missing_index_skips_embed() -> None:
    """Missing index resolve path stays empty without embed."""


@scenario(
    "features/standalone/first_use_local_search.feature",
    "有占位索引但未配 embedding 时 skills/search 返回空列表",
)
def test_search_unset_embedding_skips_embed() -> None:
    """Placeholder index still skips when embedding is unset."""


@scenario(
    "features/standalone/first_use_local_search.feature",
    "SDK search_skills 在缺索引时返回空列表",
)
def test_sdk_search_missing_index() -> None:
    """SDK search_skills mirrors the API empty-index guard."""


@scenario(
    "features/standalone/first_use_local_search.feature",
    "skill 目录尚未 git init 时 status 返回 200 且 git_branch 为空",
)
def test_status_without_git_repo() -> None:
    """Uninitialized skill dir status returns 200 with null branch."""


# ── scenarios: #200 reindex empty description ─────────────────────────

@scenario(
    "features/standalone/reindex_empty_description.feature",
    "仓内同时有合法 skill 与空 description skill 时 reindex 成功并写出索引",
)
def test_reindex_skips_empty_description_skill() -> None:
    """Empty-description skills are skipped; good skills are indexed."""


@scenario(
    "features/standalone/reindex_empty_description.feature",
    "仓内存在非法裸多行 description 的 baby SKILL.md 时 reindex 不抛错",
)
def test_reindex_tolerates_illegal_multiline_description() -> None:
    """Illegal bare multiline YAML must not abort the whole reindex."""


@scenario(
    "features/standalone/reindex_empty_description.feature",
    "被跳过的空 description 不会发给 embedding 客户端",
)
def test_reindex_does_not_send_empty_strings_to_embed() -> None:
    """Embed client must never see empty description texts."""


# ── fakes / world ─────────────────────────────────────────────────────

class _RecordingEmbed:
    """Deterministic embed that records texts and rejects empty strings."""

    dim = 4
    model = "fake-bdd"
    base_url = "test://"

    def __init__(self) -> None:
        self.texts: list[str] = []

    def encode(self, text: str) -> np.ndarray:
        if not str(text).strip():
            raise ValueError("empty description must not be embedded")
        self.texts.append(text)
        digest = abs(hash(text)) % (10 ** 8)
        rng = np.random.default_rng(digest)
        vector = rng.random(self.dim, dtype=np.float32)
        return vector / (np.linalg.norm(vector) or 1.0)

    def encode_batch(self, texts: list[str]) -> np.ndarray:
        return np.stack([self.encode(text) for text in texts])


@dataclass
class _World:
    skill_dir: Path
    config: dict = field(default_factory=dict)
    client: TestClient | None = None
    embed: _RecordingEmbed | None = None
    embed_created: bool = False
    last_status: int | None = None
    last_body: Any = None
    sdk_hits: list | None = None
    reindex_error: BaseException | None = None
    caplog: pytest.LogCaptureFixture | None = None


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    state = _World(
        skill_dir=skill_dir,
        config={"llm": {}, "embedding": {}, "watcher": {"poll_interval": 30}},
        caplog=caplog,
    )

    def _create_embed(_config):
        state.embed_created = True
        raise AssertionError("unexpected embed client")

    monkeypatch.setattr(api_app, "_skill_dir", skill_dir)
    monkeypatch.setattr(api_app, "_config", state.config)
    monkeypatch.setattr(api_app, "create_embed_client", _create_embed)

    app = FastAPI()
    app.include_router(api_app.router)
    state.client = TestClient(app)

    with caplog.at_level(logging.WARNING):
        yield state


def _write_skill(skill_dir: Path, name: str, body: str) -> Path:
    path = skill_dir / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(body, encoding="utf-8")
    return path


# ── given: first-use ──────────────────────────────────────────────────

@given("xskill API 使用隔离的空 skill 目录", target_fixture="world")
def given_isolated_empty_skill_dir(world: _World) -> _World:
    assert world.skill_dir.is_dir()
    assert not (world.skill_dir / ".skill_index.pkl").exists()
    return world


@given("embedding 配置为空")
def given_embedding_unset(world: _World) -> None:
    world.config["embedding"] = {}


@given("skill 目录存在占位 .skill_index.pkl")
def given_placeholder_index(world: _World) -> None:
    (world.skill_dir / ".skill_index.pkl").write_bytes(b"placeholder")


# ── given: reindex ────────────────────────────────────────────────────

@given(
    "xskill 使用隔离 skill 目录与可记录的假 embedding 客户端",
    target_fixture="world",
)
def given_reindex_world(world: _World) -> _World:
    world.embed = _RecordingEmbed()
    return world


@given(parsers.parse('skill 仓中有合法 skill "{name}" 描述为 "{description}"'))
def given_good_skill(world: _World, name: str, description: str) -> None:
    _write_skill(
        world.skill_dir,
        name,
        (
            f"---\nname: {name}\n"
            f"description: {description}\n"
            f"---\n# {name}\nbody\n"
        ),
    )


@given(parsers.parse('skill 仓中有空 description 的 skill "{name}"'))
def given_empty_description_skill(world: _World, name: str) -> None:
    _write_skill(
        world.skill_dir,
        name,
        f"---\nname: {name}\ndescription: \"\"\n---\n# {name}\nbody\n",
    )


@given(parsers.parse('skill 仓中有非法裸多行 description 的 skill "{name}"'))
def given_illegal_multiline_skill(world: _World, name: str) -> None:
    # Mirrors SkillNerds/xskill#200: bare multiline description breaks YAML.
    _write_skill(
        world.skill_dir,
        name,
        (
            f"---\nname: {name}\n"
            "description: 服务于登录页 V4 版本的 UI 对齐与全英文（i18n）适配任务。\n"
            "典型操作包括 Apple 登录按钮置顶、文案本地化替换、CSS 样式对齐、locale 配置验证等。\n"
            "常在 worktree 并行开发模式下执行，需跨组件/语言包协同修改。\n"
            "metadata:\n"
            "  version: 0\n"
            "  state: baby\n"
            f"---\n# {name}\nbody\n"
        ),
    )


# ── when ──────────────────────────────────────────────────────────────

@when(parsers.parse('客户端 POST /api/v1/skills/search 查询 "{query}"'))
def when_post_search(world: _World, query: str) -> None:
    assert world.client is not None
    response = world.client.post(
        "/api/v1/skills/search", json={"query": query, "top_k": 2},
    )
    world.last_status = response.status_code
    world.last_body = response.json()


@when(parsers.parse('客户端 POST /api/v1/skills/resolve 查询 "{query}"'))
def when_post_resolve(world: _World, query: str) -> None:
    assert world.client is not None
    response = world.client.post(
        "/api/v1/skills/resolve", json={"query": query},
    )
    world.last_status = response.status_code
    world.last_body = response.json()


@when("客户端 GET /api/v1/status")
def when_get_status(world: _World) -> None:
    assert world.client is not None
    response = world.client.get("/api/v1/status")
    world.last_status = response.status_code
    world.last_body = response.json()


@when(parsers.parse('SDK 调用 search_skills 查询 "{query}"'))
def when_sdk_search(
    world: _World, query: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        core, "load_config", lambda config_path=None: {"embedding": {}},
    )
    monkeypatch.setattr(core, "get_skill_dir", lambda: world.skill_dir)

    def _boom(_config):
        world.embed_created = True
        raise AssertionError("unexpected embed client")

    monkeypatch.setattr(llm_mod, "create_embed_client", _boom)
    world.sdk_hits = core.XSkill().search_skills(query, top_k=2)


@when("重建 skill 向量索引")
def when_rebuild_index(world: _World) -> None:
    assert world.embed is not None
    try:
        rebuild_skill_index(
            skill_dir=world.skill_dir,
            embed_client=world.embed,
            scope="search",
        )
        world.reindex_error = None
    except BaseException as exc:  # noqa: BLE001 — BDD captures failure
        world.reindex_error = exc


# ── then: first-use ───────────────────────────────────────────────────

@then(parsers.parse("响应状态码是 {code:d}"))
def then_status_code(world: _World, code: int) -> None:
    assert world.last_status == code


@then("响应 JSON 是空列表")
def then_body_empty_list(world: _World) -> None:
    assert world.last_body == []


@then("resolve 结果为空")
def then_resolve_empty(world: _World) -> None:
    assert world.last_body == {
        "skill_name": None, "path": None, "side": "none", "sha": "",
    }


@then("不应创建 embedding 客户端")
def then_embed_not_created(world: _World) -> None:
    assert world.embed_created is False


@then(parsers.parse('日志应包含 "{snippet}"'))
def then_log_contains(world: _World, snippet: str) -> None:
    assert world.caplog is not None
    messages = [record.getMessage() for record in world.caplog.records]
    assert any(snippet in message for message in messages), messages


@then("SDK 搜索结果是空列表")
def then_sdk_empty(world: _World) -> None:
    assert world.sdk_hits == []


@then("status 的 git_branch 为空")
def then_git_branch_null(world: _World) -> None:
    assert isinstance(world.last_body, dict)
    assert world.last_body.get("git_branch") is None


# ── then: reindex ─────────────────────────────────────────────────────

@then("索引重建不抛错")
def then_reindex_ok(world: _World) -> None:
    assert world.reindex_error is None, world.reindex_error


@then("skill 目录存在 .skill_index.pkl")
def then_index_file_exists(world: _World) -> None:
    assert (world.skill_dir / ".skill_index.pkl").is_file()


@then(parsers.parse('索引包含 skill "{name}"'))
def then_index_contains(world: _World, name: str) -> None:
    with open(world.skill_dir / ".skill_index.pkl", "rb") as handle:
        data = pickle.load(handle)
    assert name in data["skill_names"]


@then(parsers.parse('索引不包含 skill "{name}"'))
def then_index_excludes(world: _World, name: str) -> None:
    with open(world.skill_dir / ".skill_index.pkl", "rb") as handle:
        data = pickle.load(handle)
    assert name not in data["skill_names"]


@then("假 embedding 客户端收到的文本不含空串")
def then_embed_no_empty(world: _World) -> None:
    assert world.embed is not None
    assert all(str(text).strip() for text in world.embed.texts)


@then(parsers.parse('假 embedding 客户端收到过 "{text}"'))
def then_embed_saw_text(world: _World, text: str) -> None:
    assert world.embed is not None
    assert text in world.embed.texts
