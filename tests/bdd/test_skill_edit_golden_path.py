"""Executable BDD for the Issue #146 primary user journey.

This test deliberately keeps the production boundary intact:

DirectoryWatcher -> SkillEditAgent -> Agno/OpenAI client -> HTTP -> aimock
                 -> tool call -> xskill tool implementation -> git/candidates

Only the remote model is replaced.  The agent, tool schemas, tool execution,
candidate consumption, git checkpoints, and framework-controlled graduation
are production code.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, scenario, then, when

from xskill.agents import agent_tools
from tests.pool_helpers import pool_config
from xskill.agents.agno_factory import make_default_factory
from xskill.agents.skill_edit_agent import SkillEditAgent
from xskill.pipeline.atom import AtomTaskStore
from xskill.pipeline.registry import register_dir
from xskill.pipeline.runner import DirectoryWatcher
from xskill.skill import candidates as candidate_buffer
from xskill.skill.git import current_branch, init_skill_repo_on_baby, run_git


RUN_AIMOCK_E2E = os.environ.get("XSKILL_AIMOCK_E2E") == "1"

pytestmark = [
    pytest.mark.bdd,
    pytest.mark.http_llm,
    pytest.mark.skipif(
        not RUN_AIMOCK_E2E,
        reason="set XSKILL_AIMOCK_E2E=1 to run the local aimock HTTP contract",
    ),
]


@scenario(
    "features/skill_edit/baby_cold_start_golden_path.feature",
    "用户等待系统把新知识整理成可用的 main skill",
)
def test_user_receives_complete_main_skill() -> None:
    """The Gherkin scenario is implemented by the steps below."""


@scenario(
    "features/skill_edit/skill_edit_llm_backend.feature",
    "模型通过 OpenAI tool calls 完成一次 baby checkpoint",
)
def test_aimock_executes_one_real_checkpoint() -> None:
    """Production Agno performs the complete three-request tool loop."""


@scenario(
    "features/skill_edit/skill_edit_llm_backend.feature",
    "测试运行期间没有请求离开本机",
)
def test_model_requests_remain_local() -> None:
    """The request journal proves that the contract stays on loopback."""


@scenario(
    "features/skill_edit/skill_edit_llm_backend.feature",
    "模型后端持续返回 429 时当前批次不会被误提交",
)
def test_persistent_429_preserves_the_batch() -> None:
    """A real HTTP 429 drives the production adaptive batch state machine."""


@scenario(
    "features/skill_edit/baby_cold_start_recovery.feature",
    "commit 完成后的模型 429 不会导致原子被再次处理",
)
def test_post_commit_429_does_not_replay_atoms() -> None:
    """The durable checkpoint wins over a failed final model response."""


@dataclass
class SkillEditWorld:
    root: Path
    aimock: Any
    skill_root: Path
    watch_root: Path
    db_path: Path
    logs_root: Path
    batch_size: int = 5
    api_key: str = "sk-aimock-xskill-test"
    skill_name: str | None = None
    skill_dir: Path | None = None
    candidate_rows: list[dict[str, str]] = field(default_factory=list)
    watcher: DirectoryWatcher | None = None
    journal: list[dict[str, Any]] = field(default_factory=list)
    result: bool | None = None
    head_before: str = ""
    attempts: list[int] = field(default_factory=list)

    @property
    def model_requests(self) -> list[dict[str, Any]]:
        return [
            entry
            for entry in self.journal
            if entry.get("method") == "POST"
            and entry.get("path") == "/v1/chat/completions"
        ]

    @property
    def initial_turn_requests(self) -> list[dict[str, Any]]:
        requests = []
        for entry in self.model_requests:
            messages = (entry.get("body") or {}).get("messages") or []
            if not any(message.get("role") == "tool" for message in messages):
                requests.append(entry)
        return requests

    def stop_watcher(self) -> None:
        if self.watcher is not None:
            self.watcher.stop()
            self.watcher = None


@pytest.fixture
def skill_edit_world(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> SkillEditWorld:
    if not RUN_AIMOCK_E2E:
        pytest.skip("set XSKILL_AIMOCK_E2E=1 to start the aimock backend")
    aimock = request.getfixturevalue("aimock")
    skill_root = tmp_path / "skills"
    watch_root = tmp_path / "watch"
    skill_root.mkdir()
    watch_root.mkdir()
    world = SkillEditWorld(
        root=tmp_path,
        aimock=aimock,
        skill_root=skill_root,
        watch_root=watch_root,
        db_path=tmp_path / "registry.db",
        logs_root=tmp_path / "logs",
    )
    yield world
    world.stop_watcher()


def _skill_document(
    skill_name: str,
    rows: list[dict[str, str]],
    *,
    version: int,
) -> str:
    rules = "\n".join(
        f"{index}. {row['knowledge']}"
        for index, row in enumerate(rows, start=1)
    )
    return (
        "---\n"
        f"name: {skill_name}\n"
        "description: 系统化恢复反向代理后端服务并验证结果。"
        "适用于进程、端口和 upstream 不一致的故障定位。\n"
        "metadata:\n"
        f"  version: {version}\n"
        "---\n\n"
        f"# {skill_name}\n\n"
        "## 恢复流程\n\n"
        f"{rules}\n"
    )


def _register_turn(
    world: SkillEditWorld,
    *,
    turn: int,
    rows: list[dict[str, str]],
) -> None:
    assert world.skill_name is not None
    assert world.skill_dir is not None
    target = world.skill_dir / "SKILL.md"

    # aimock deliberately owns/generated tool-call IDs.  Match follow-ups by
    # the observable tool result instead of coupling this contract to an ID
    # that the mock backend is allowed to rewrite.
    world.aimock.add_fixture(
        match={
            "userMessage": rows[0]["atom_id"],
            "hasToolResult": True,
            "toolResultContains": "Created baby checkpoint",
        },
        response={"content": f"turn {turn} checkpoint complete"},
    )
    world.aimock.add_fixture(
        match={
            "userMessage": rows[0]["atom_id"],
            "hasToolResult": True,
            "toolResultContains": "wrote:",
        },
        response={
            "toolCalls": [
                {
                    "name": "commit_baby",
                    "arguments": {
                        "skill_name": world.skill_name,
                        "message": f"BDD checkpoint turn {turn}",
                    },
                }
            ]
        },
    )
    world.aimock.add_fixture(
        match={
            "userMessage": rows[0]["atom_id"],
            "hasToolResult": False,
        },
        response={
            "toolCalls": [
                {
                    "name": "write_file",
                    "arguments": {
                        "path": str(target),
                        "content": _skill_document(
                            world.skill_name,
                            world.candidate_rows[: turn * world.batch_size],
                            version=turn,
                        ),
                    },
                }
            ]
        },
    )


def _create_baby_with_candidates(
    world: SkillEditWorld,
    *,
    name: str,
    count: int,
    weight: int,
) -> None:
    world.skill_name = name
    world.skill_dir = world.skill_root / name
    init_skill_repo_on_baby(
        str(world.skill_dir),
        name=name,
        description="BDD aimock contract draft",
    )
    world.candidate_rows = [
        {
            "atom_id": f"atom-{index:02d}",
            "knowledge": f"可复用恢复规则 {index}",
        }
        for index in range(1, count + 1)
    ]
    data: dict[str, Any] = {"candidates": []}
    for row in world.candidate_rows:
        data, _ = candidate_buffer.add_atom_contribution(
            data,
            row["atom_id"],
            weight,
            note=row["knowledge"],
        )
    candidate_buffer.save_candidates(world.skill_dir, data)
    world.head_before = run_git(
        ["rev-parse", "HEAD"],
        cwd=str(world.skill_dir),
    )[1].strip()


def _run_direct_agent(world: SkillEditWorld, *, retry_batch_size: int | None = None) -> None:
    assert world.skill_dir is not None
    config = {
        "llm": {
            "base_url": f"{world.aimock.base_url}/v1",
            "model": "aimock-skill-edit",
            "api_key": world.api_key,
            "request_timeout": 10,
            "connect_timeout": 2,
            "client_max_retries": 0,
            "max_retries": 1,
            "retry_base_delay": 0.01,
            "retry_max_delay": 0.01,
            "max_context": 128_000,
        },
        "skill_opt": {"enabled": False},
    }
    default_factory = make_default_factory(
        config,
        spill_root=world.root / "spill",
    )

    def factory(*, instructions: list[str], tools: list[Any]) -> Any:
        return default_factory(
            instructions=instructions,
            tools=tools,
            retries=0,
        )

    saved_context = agent_tools.agent_tool_config.snapshot()
    store = AtomTaskStore(root=world.watch_root)
    agent_tools.init_atom_task_tool_context(
        skill_dir=world.skill_root,
        atom_store=store,
        default_traj_root=world.watch_root,
    )
    agent_tools.init_skill_authoring_tool_context(
        world.skill_root,
        world.skill_root,
        config,
        spill_root=world.root / "spill",
    )
    try:
        agent = SkillEditAgent(
            skill_dir=world.skill_dir,
            store=store,
            agno_agent_factory=factory,
            llm_cfg=config["llm"],
            traj_root=world.watch_root,
            batch_size=world.batch_size,
            retry_batch_size=retry_batch_size,
            logs_dir=world.logs_root,
        )
        world.result = agent.maybe_run()
    finally:
        world.journal = world.aimock.get_journal()
        agent_tools.agent_tool_config.restore(saved_context)
    # Derive batch sizes after the journal has been captured.
    world.attempts = [
        len(set(re.findall(r"atom-\d{2}", _joined_message_content(entry))))
        for entry in world.initial_turn_requests
    ]


@given("xskill 使用隔离的测试目录")
def isolated_xskill_directory(skill_edit_world: SkillEditWorld) -> None:
    assert skill_edit_world.root.is_dir()
    assert skill_edit_world.skill_root.parent == skill_edit_world.root


@given("SkillEdit 每批最多处理 5 个原子")
def edit_batch_size_is_five(skill_edit_world: SkillEditWorld) -> None:
    skill_edit_world.batch_size = 5


@given("SkillEdit 默认每批处理 5 个原子")
def default_edit_batch_size_is_five(skill_edit_world: SkillEditWorld) -> None:
    skill_edit_world.batch_size = 5


@given("baby 的 candidates 使用稳定的 FIFO 顺序")
def candidates_use_fifo_order() -> None:
    return None


@given("SkillEdit 模型指向本地 OpenAI-compatible 测试后端")
def local_openai_backend(skill_edit_world: SkillEditWorld) -> None:
    assert skill_edit_world.aimock.base_url.startswith("http://127.0.0.1:")


@given('cluster 已经创建名为 "incident-recovery" 的 baby skill')
def existing_baby_skill(skill_edit_world: SkillEditWorld) -> None:
    skill_edit_world.skill_name = "incident-recovery"
    skill_edit_world.skill_dir = skill_edit_world.skill_root / skill_edit_world.skill_name
    init_skill_repo_on_baby(
        str(skill_edit_world.skill_dir),
        name=skill_edit_world.skill_name,
        description="incident recovery draft",
    )
    assert current_branch(str(skill_edit_world.skill_dir)) == "baby"


@given("baby 的 candidates 按以下顺序保存")
def ordered_candidates(
    skill_edit_world: SkillEditWorld,
    datatable: list[list[str]],
) -> None:
    assert skill_edit_world.skill_dir is not None
    headers = datatable[0]
    rows = [dict(zip(headers, values, strict=True)) for values in datatable[1:]]
    skill_edit_world.candidate_rows = rows

    data: dict[str, Any] = {"candidates": []}
    for row in rows:
        data, _ = candidate_buffer.add_atom_contribution(
            data,
            row["atom_id"],
            2,
            note=row["knowledge"],
        )
    candidate_buffer.save_candidates(skill_edit_world.skill_dir, data)


@given("candidates 的总权重已经达到 baby 冷启动阈值")
def candidates_reach_threshold(skill_edit_world: SkillEditWorld) -> None:
    assert skill_edit_world.skill_dir is not None
    data = candidate_buffer.load_candidates(skill_edit_world.skill_dir)
    assert sum(item["weightscore"] for item in data["candidates"]) >= 10


@given("测试模型会根据每批原子更新 SKILL.md 并调用 commit_baby")
def scripted_model_updates_and_commits(skill_edit_world: SkillEditWorld) -> None:
    rows = skill_edit_world.candidate_rows
    assert [row["atom_id"] for row in rows] == [
        f"atom-{index:02d}" for index in range(1, 8)
    ]
    _register_turn(
        skill_edit_world,
        turn=1,
        rows=rows[: skill_edit_world.batch_size],
    )
    _register_turn(
        skill_edit_world,
        turn=2,
        rows=rows[skill_edit_world.batch_size :],
    )


@given("aimock 在随机本地端口启动 OpenAI-compatible 服务")
def aimock_runs_on_random_loopback_port(skill_edit_world: SkillEditWorld) -> None:
    assert re.fullmatch(r"http://127\.0\.0\.1:\d+", skill_edit_world.aimock.base_url)


@given("xskill 的 llm.base_url 指向该服务")
def llm_points_to_aimock(skill_edit_world: SkillEditWorld) -> None:
    assert skill_edit_world.aimock.base_url.startswith("http://127.0.0.1:")


@given("xskill 使用无权限的测试 API key")
def harmless_api_key(skill_edit_world: SkillEditWorld) -> None:
    assert skill_edit_world.api_key == "sk-aimock-xskill-test"


@given("一个 SkillEdit turn 绑定了 5 个 atom_id")
def five_atoms_are_bound(skill_edit_world: SkillEditWorld) -> None:
    _create_baby_with_candidates(
        skill_edit_world,
        name="aimock-contract",
        count=5,
        weight=2,
    )


@given("aimock 为这个 turn 准备了以下响应")
def aimock_prepares_tool_loop(
    skill_edit_world: SkillEditWorld,
    datatable: list[list[str]],
) -> None:
    assert [row[0] for row in datatable[1:]] == ["first", "second", "third"]
    _register_turn(
        skill_edit_world,
        turn=1,
        rows=skill_edit_world.candidate_rows,
    )


@given("主成功路径已经执行完成")
def completed_success_path(skill_edit_world: SkillEditWorld) -> None:
    five_atoms_are_bound(skill_edit_world)
    _register_turn(
        skill_edit_world,
        turn=1,
        rows=skill_edit_world.candidate_rows,
    )
    _run_direct_agent(skill_edit_world)
    assert skill_edit_world.result is True


@given("当前 turn 的 N 为 5")
def current_turn_n_is_five(skill_edit_world: SkillEditWorld) -> None:
    skill_edit_world.batch_size = 5
    _create_baby_with_candidates(
        skill_edit_world,
        name="aimock-rate-limit",
        count=5,
        weight=2,
    )


@given("aimock 对模型请求返回 HTTP 429 和 Retry-After")
def aimock_always_rate_limits(skill_edit_world: SkillEditWorld) -> None:
    skill_edit_world.aimock.add_fixture(
        match={"userMessage": "atom-01", "hasToolResult": False},
        response={
            "error": {
                "message": "Rate limited by BDD fixture",
                "type": "rate_limit_error",
                "code": "rate_limit_exceeded",
            },
            "status": 429,
        },
    )


@given("模型客户端已经耗尽该 turn 内部的有限重试")
def client_retry_is_bounded() -> None:
    # _run_direct_agent configures max_retries=1 and OpenAI max_retries=0.
    return None


@given("模型已经调用 commit_baby 并成功提交当前批次")
def model_will_commit_before_error(skill_edit_world: SkillEditWorld) -> None:
    _create_baby_with_candidates(
        skill_edit_world,
        name="aimock-post-commit-429",
        count=3,
        weight=4,
    )


@given("commit_baby 已经从 candidates 删除当前批次 atom_id")
def commit_consumes_bound_atoms() -> None:
    # This is asserted from the real commit_baby result after execution.
    return None


@given("aimock 在模型的结束响应阶段返回 HTTP 429")
def final_model_response_is_429(skill_edit_world: SkillEditWorld) -> None:
    rows = skill_edit_world.candidate_rows
    assert skill_edit_world.skill_name is not None
    assert skill_edit_world.skill_dir is not None
    target = skill_edit_world.skill_dir / "SKILL.md"
    skill_edit_world.aimock.add_fixture(
        match={
            "userMessage": rows[0]["atom_id"],
            "hasToolResult": True,
            "toolResultContains": "Created baby checkpoint",
        },
        response={
            "error": {
                "message": "Rate limited after durable checkpoint",
                "type": "rate_limit_error",
            },
            "status": 429,
        },
    )
    skill_edit_world.aimock.add_fixture(
        match={
            "userMessage": rows[0]["atom_id"],
            "hasToolResult": True,
            "toolResultContains": "wrote:",
        },
        response={
            "toolCalls": [
                {
                    "name": "commit_baby",
                    "arguments": {
                        "skill_name": skill_edit_world.skill_name,
                        "message": "BDD durable before final 429",
                    },
                }
            ]
        },
    )
    skill_edit_world.aimock.add_fixture(
        match={
            "userMessage": rows[0]["atom_id"],
            "hasToolResult": False,
        },
        response={
            "toolCalls": [
                {
                    "name": "write_file",
                    "arguments": {
                        "path": str(target),
                        "content": _skill_document(
                            skill_edit_world.skill_name,
                            rows,
                            version=1,
                        ),
                    },
                }
            ]
        },
    )


@when("watcher 调度这个 baby 的 SkillEdit 工作")
def watcher_schedules_skill_edit(skill_edit_world: SkillEditWorld) -> None:
    config = {
        "llm": {
            "base_url": f"{skill_edit_world.aimock.base_url}/v1",
            "model": "aimock-skill-edit",
            "api_key": skill_edit_world.api_key,
            "request_timeout": 10,
            "connect_timeout": 2,
            "client_max_retries": 0,
            "max_retries": 1,
            "retry_base_delay": 0.01,
            "retry_max_delay": 0.01,
            "max_context": 128_000,
        },
        "skill_opt": {"enabled": False},
    }
    store = AtomTaskStore(root=skill_edit_world.watch_root)
    register_dir(skill_edit_world.watch_root, db_path=skill_edit_world.db_path)
    default_factory = make_default_factory(
        config,
        spill_root=skill_edit_world.root / "spill",
    )

    def factory(*, instructions: list[str], tools: list[Any]) -> Any:
        # Fixture mismatches should fail this contract immediately; retry and
        # backoff behavior has separate state-machine/429 scenarios.
        return default_factory(
            instructions=instructions,
            tools=tools,
            retries=0,
        )

    watcher = DirectoryWatcher(
        config=config,
        skill_dir=skill_edit_world.skill_root,
        poll_interval=0,
        pool_config=pool_config(
            workers=1,
            edit_workers=1,
            edit_batch_size=skill_edit_world.batch_size,
        ),
        db_path=skill_edit_world.db_path,
        store=store,
        agno_agent_factory=factory,
        home_root=skill_edit_world.root / "home",
        xskill_home=skill_edit_world.root / "xskill",
        logs_dir=skill_edit_world.logs_root,
        server_mode=True,
    )
    skill_edit_world.watcher = watcher
    watcher._check_pending_skill_edits()
    watcher._drain_futures(stage="skill_edit", timeout=90)
    skill_edit_world.journal = skill_edit_world.aimock.get_journal()
    if watcher.stats["skills_edited"] != 1:
        evidence = []
        for entry in skill_edit_world.model_requests:
            messages = (entry.get("body") or {}).get("messages") or []
            content = "\n".join(
                str(message.get("content") or "") for message in messages
            )
            evidence.append(
                {
                    "roles": [message.get("role") for message in messages],
                    "atom_ids": sorted(set(re.findall(r"atom-\d{2}", content))),
                    "tool_results": [
                        str(message.get("content") or "").splitlines()[0][:100]
                        for message in messages
                        if message.get("role") == "tool"
                    ],
                    "status": (entry.get("response") or {}).get("status"),
                }
            )
        pytest.fail(
            "watcher did not complete SkillEdit; compact aimock evidence: "
            f"{evidence!r}"
        )


@when("SkillEdit 使用生产 Agno factory 执行这个 turn")
def production_factory_executes_turn(skill_edit_world: SkillEditWorld) -> None:
    _run_direct_agent(skill_edit_world)


@when("检查测试后端的 request journal")
def inspect_request_journal(skill_edit_world: SkillEditWorld) -> None:
    assert skill_edit_world.journal


@when("SkillEditAgent 收到模型调用失败")
def agent_receives_backend_failure(skill_edit_world: SkillEditWorld) -> None:
    _run_direct_agent(skill_edit_world)


@when("SkillEditAgent 检查 baby HEAD 和剩余 candidates")
def agent_checks_durable_state(skill_edit_world: SkillEditWorld) -> None:
    _run_direct_agent(skill_edit_world)


@then("模型应当收到 2 个互相独立的编辑 turn")
def two_independent_turns(skill_edit_world: SkillEditWorld) -> None:
    initial_requests = skill_edit_world.initial_turn_requests
    assert len(initial_requests) == 2
    assert len(skill_edit_world.model_requests) == 6


def _joined_message_content(entry: dict[str, Any]) -> str:
    messages = (entry.get("body") or {}).get("messages") or []
    return "\n".join(str(message.get("content") or "") for message in messages)


@then('第 1 个 turn 应当只包含 "atom-01,atom-02,atom-03,atom-04,atom-05"')
def first_turn_contains_first_batch_only(skill_edit_world: SkillEditWorld) -> None:
    content = _joined_message_content(skill_edit_world.initial_turn_requests[0])
    for atom_id in [f"atom-{index:02d}" for index in range(1, 6)]:
        assert atom_id in content
    assert "atom-06" not in content
    assert "atom-07" not in content


@then('第 2 个 turn 应当只包含 "atom-06,atom-07"')
def second_turn_contains_second_batch_only(skill_edit_world: SkillEditWorld) -> None:
    content = _joined_message_content(skill_edit_world.initial_turn_requests[1])
    assert "atom-06" in content
    assert "atom-07" in content
    for atom_id in [f"atom-{index:02d}" for index in range(1, 6)]:
        assert atom_id not in content


@then("每个 turn 都应当在 baby 上产生一个非空 checkpoint commit")
def every_turn_creates_checkpoint(skill_edit_world: SkillEditWorld) -> None:
    assert skill_edit_world.skill_dir is not None
    code, output, error = run_git(
        ["log", "--oneline", "-20"],
        cwd=str(skill_edit_world.skill_dir),
    )
    assert code == 0, error
    assert "BDD checkpoint turn 1" in output
    assert "BDD checkpoint turn 2" in output


def _tool_results(world: SkillEditWorld, marker: str) -> list[str]:
    results = []
    for entry in world.model_requests:
        messages = (entry.get("body") or {}).get("messages") or []
        for message in messages:
            if message.get("role") == "tool" and marker in str(message.get("content")):
                results.append(str(message["content"]))
    return results


@then("每次 commit 只应当删除当前 turn 绑定的 atom_id")
def commits_consume_only_bound_atoms(skill_edit_world: SkillEditWorld) -> None:
    results = _tool_results(skill_edit_world, "Consumed atoms:")
    # Each result remains in later history, so keep the first occurrence for
    # each checkpoint rather than asserting a raw count.
    unique_results = list(dict.fromkeys(results))
    assert len(unique_results) == 2
    for atom_id in [f"atom-{index:02d}" for index in range(1, 6)]:
        assert atom_id in unique_results[0]
        assert atom_id not in unique_results[1]
    assert "atom-06" not in unique_results[0]
    assert "atom-07" not in unique_results[0]
    assert "atom-06" in unique_results[1]
    assert "atom-07" in unique_results[1]


@then("candidates 最终应当为空")
def candidates_are_empty(skill_edit_world: SkillEditWorld) -> None:
    assert skill_edit_world.skill_dir is not None
    assert candidate_buffer.load_candidates(skill_edit_world.skill_dir)["candidates"] == []


@then("框架应当把 baby 晋升为 main")
def framework_promotes_main(skill_edit_world: SkillEditWorld) -> None:
    assert skill_edit_world.skill_dir is not None
    assert current_branch(str(skill_edit_world.skill_dir)) == "main"


@then("main 的 SKILL.md 应当包含 7 个原子贡献的恢复流程")
def final_skill_contains_all_knowledge(skill_edit_world: SkillEditWorld) -> None:
    assert skill_edit_world.skill_dir is not None
    body = (skill_edit_world.skill_dir / "SKILL.md").read_text(encoding="utf-8")
    for row in skill_edit_world.candidate_rows:
        assert row["knowledge"] in body


@then("aimock 应当收到真实的 POST /v1/chat/completions 请求")
def aimock_received_chat_completions(skill_edit_world: SkillEditWorld) -> None:
    assert len(skill_edit_world.model_requests) == 3


@then("第一次请求应当声明 write_file 和 commit_baby 工具")
def first_request_declares_tools(skill_edit_world: SkillEditWorld) -> None:
    tools = (skill_edit_world.model_requests[0].get("body") or {}).get("tools") or []
    names = {
        (tool.get("function") or {}).get("name")
        for tool in tools
    }
    assert {"write_file", "commit_baby"} <= names


@then("第二次请求应当包含 write_file 的 tool result")
def second_request_contains_write_result(skill_edit_world: SkillEditWorld) -> None:
    assert "wrote:" in _joined_message_content(skill_edit_world.model_requests[1])


@then("第三次请求应当包含 commit_baby 的 tool result")
def third_request_contains_commit_result(skill_edit_world: SkillEditWorld) -> None:
    assert "Created baby checkpoint" in _joined_message_content(
        skill_edit_world.model_requests[2]
    )


@then("atom_id 不应当由模型作为 commit_baby 参数提供")
def commit_schema_excludes_atom_ids(skill_edit_world: SkillEditWorld) -> None:
    tools = (skill_edit_world.model_requests[0].get("body") or {}).get("tools") or []
    commit = next(
        tool["function"] for tool in tools
        if tool["function"]["name"] == "commit_baby"
    )
    properties = (commit.get("parameters") or {}).get("properties") or {}
    assert "atom_id" not in properties
    assert "atom_ids" not in properties


@then("commit_baby 应当从框架绑定的批次取得 atom_id")
def commit_uses_framework_bound_ids(skill_edit_world: SkillEditWorld) -> None:
    results = list(dict.fromkeys(_tool_results(skill_edit_world, "Consumed atoms:")))
    assert len(results) == 1
    for row in skill_edit_world.candidate_rows:
        assert row["atom_id"] in results[0]


@then("所有模型请求都应当发送到 aimock")
def all_requests_hit_aimock(skill_edit_world: SkillEditWorld) -> None:
    assert skill_edit_world.model_requests
    assert all(entry["path"] == "/v1/chat/completions" for entry in skill_edit_world.model_requests)


@then("请求不应当包含生产 API key")
def no_production_key_is_sent(skill_edit_world: SkillEditWorld) -> None:
    for entry in skill_edit_world.model_requests:
        authorization = (entry.get("headers") or {}).get("authorization", "")
        assert authorization == "[REDACTED]"
        assert skill_edit_world.api_key not in str(entry.get("headers") or {})


@then("测试不应当访问任何公共模型 endpoint")
def no_public_model_endpoint(skill_edit_world: SkillEditWorld) -> None:
    assert skill_edit_world.aimock.base_url.startswith("http://127.0.0.1:")


@then("baby 的 HEAD 不应当前进")
def baby_head_does_not_advance(skill_edit_world: SkillEditWorld) -> None:
    assert skill_edit_world.skill_dir is not None
    head_after = run_git(
        ["rev-parse", "HEAD"],
        cwd=str(skill_edit_world.skill_dir),
    )[1].strip()
    assert head_after == skill_edit_world.head_before


@then("当前 5 个 atom_id 不应当从 candidates 删除")
def rate_limited_atoms_remain(skill_edit_world: SkillEditWorld) -> None:
    assert skill_edit_world.skill_dir is not None
    remaining = candidate_buffer.load_candidates(
        skill_edit_world.skill_dir
    )["candidates"]
    assert [item["atom_id"] for item in remaining] == [
        row["atom_id"] for row in skill_edit_world.candidate_rows
    ]


@then("本次调度应当依次尝试 N=5、N=2、N=1")
def adaptive_attempt_sizes_are_observed(skill_edit_world: SkillEditWorld) -> None:
    assert skill_edit_world.attempts == [5, 2, 1]


@then("watcher 下次调度时应当继续使用 N=1")
def retry_size_stays_at_one(skill_edit_world: SkillEditWorld) -> None:
    # The failed direct agent exposes the value the watcher persists.
    trace = (
        skill_edit_world.logs_root
        / "agents"
        / "skill_edit_agents"
        / "skills"
        / f"{skill_edit_world.skill_name}.log"
    ).read_text(encoding="utf-8")
    assert "TURN END | FAILED | consumed=0 | 5 remaining | next N=1" in trace


@then("当前 turn 应当被判定为成功")
def post_commit_turn_is_success(skill_edit_world: SkillEditWorld) -> None:
    assert skill_edit_world.result is True


@then("当前批次不应当再次发送给模型")
def committed_batch_is_not_replayed(skill_edit_world: SkillEditWorld) -> None:
    assert skill_edit_world.attempts == [3]


@then("下一个 turn 应当从第一个未消费 atom_id 开始")
def next_turn_starts_after_committed_batch(skill_edit_world: SkillEditWorld) -> None:
    assert skill_edit_world.skill_dir is not None
    assert candidate_buffer.load_candidates(
        skill_edit_world.skill_dir
    )["candidates"] == []
