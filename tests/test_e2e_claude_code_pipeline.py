"""End-to-end test: Claude Code ↔ xskill ↔ Claude Code

This test wires up a single fake HTTP LLM backend on one port that pretends
to be all three protocols xskill's runtime touches:

  - Anthropic ``/v1/messages``       (the real ``claude`` CLI calls this)
  - OpenAI    ``/chat/completions``  (xskill LLMClient calls this)
  - ARK       ``/embeddings/multimodal`` (xskill EmbedClient calls this)

It then drives the full pipeline:

  Stage A: run ``claude -p`` against the fake server with ``HOME`` and
           ``ANTHROPIC_BASE_URL`` redirected to a sandbox + the fake. The CLI
           writes a session JSONL into ``<sandbox>/.claude/projects/...``.
  Stage B: bridge the JSONL into xskill's watch dir via
           ``ingest_claude_code_sessions`` — produces ``traj_NNNN.md`` +
           ``traj_NNNN.json``.
  Stage C: run xskill's ``process_traj`` directly. We stub ``run_agent``
           (the agno-based skill curation agent) with a fake that still
           dials the fake LLM endpoint and writes a real SKILL.md.
           Eval + frontmatter + index rebuild all go over the fake server.
  Stage D: ``install_all_to_claude_code`` copies SKILL.md into
           ``<sandbox>/.claude/skills/<name>/``.
  Stage E: run ``claude -p`` a second time. Assert the fake server received
           a system prompt containing our skill name + description — this
           is exactly the signal Claude Code uses to make the skill
           discoverable to the model.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml

# ── path setup ──────────────────────────────────────────────────────
# conftest.py adds src/ to sys.path. We additionally need to import the
# fake server helper that sits next to this test file.
import sys
THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from _fake_llm_server import (  # noqa: E402
    FakeLLMServer,
    Responder,
    make_anthropic_text_response,
    make_anthropic_tool_use_response,
    make_openai_chat_response,
)


# ────────────────────────────────────────────────────────────────────
# pytest fixtures
# ────────────────────────────────────────────────────────────────────

CLAUDE_BIN = shutil.which("claude")


@pytest.fixture(scope="module")
def fake_server():
    srv = FakeLLMServer()
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()


@pytest.fixture
def sandbox(tmp_path, monkeypatch, fake_server):
    """Self-contained HOME for both the claude subprocess and xskill.

    - ``<sandbox>/home`` -- becomes ``HOME`` for the claude CLI. CC writes
      session JSONLs and reads skills under here.
    - ``<sandbox>/home/.xskill`` -- xskill HOME. ``config.yaml`` points at
      the fake server. ``skill/`` is the xskill skill repo. ``watch/`` is
      the registered trajectory directory.

    Returns a small object with the paths the test needs.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"theme": "auto"}), encoding="utf-8",
    )

    xhome = home / ".xskill"
    xhome.mkdir(parents=True)
    skill_dir = xhome / "skill"
    watch_dir = xhome / "watch"
    logs_dir = xhome / "logs"
    for d in (skill_dir, watch_dir, logs_dir):
        d.mkdir()

    # xskill config — point LLM and embedding at the fake server.
    config_yaml = {
        "skill_dir": str(skill_dir),
        "llm": {
            "base_url": fake_server.base_url,
            "model": "fake-llm",
            "api_key": "fake-key",
        },
        "embedding": {
            "base_url": fake_server.base_url,
            "model": "fake-embed",
            "api_key": "fake-key",
            "dim": 0,
        },
        # Loosen consensus gate so a single-trajectory skill is acceptable
        # — the production policy of ≥2 source_trajs is orthogonal to what
        # this test is verifying (the data flow itself).
        "candidates": {"threshold": 1, "stale_days": 60, "min_source_trajs": 1},
        # No sandbox eval — that path tries to spin up docker.
        "sandbox": {"enabled": False, "trigger_threshold": 999},
        "canary": {"enabled": False, "probability": 0.0, "min_samples": 5, "max_days_hold": 14},
        "watcher": {"poll_interval": 1},
    }
    (xhome / "config.yaml").write_text(
        yaml.safe_dump(config_yaml, allow_unicode=True), encoding="utf-8",
    )

    # Repoint xskill's hard-coded global paths into the sandbox. These are
    # module-level Path objects evaluated at import; monkeypatching them
    # mid-flight keeps every downstream call (registry, config loaders,
    # log writers) inside the sandbox.
    from xskill import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "XSKILL_HOME", xhome)
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", xhome / "config.yaml")
    monkeypatch.setattr(cfg_mod, "REGISTRY_DB", xhome / "registry.db")
    monkeypatch.setattr(cfg_mod, "CHAT_DB", xhome / "chat_sessions.db")
    monkeypatch.setattr(cfg_mod, "LOGS_DIR", logs_dir)
    # Reset the cached config so the new yaml gets re-read.
    cfg_mod._config.clear()

    # Register the watch dir.
    from xskill.registry import register_dir
    register_dir(watch_dir, label="e2e", auto_index=True)

    class Box:
        pass

    box = Box()
    box.home = home
    box.xhome = xhome
    box.skill_dir = skill_dir
    box.watch_dir = watch_dir
    box.fake = fake_server
    return box


# ────────────────────────────────────────────────────────────────────
# Responder programming helpers
# ────────────────────────────────────────────────────────────────────

def _is_extract_meta_prompt(body: dict) -> bool:
    msgs = body.get("messages", [])
    if not msgs:
        return False
    user = next((m for m in msgs if m.get("role") == "user"), None)
    if not user:
        return False
    content = user.get("content", "")
    if isinstance(content, list):
        content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return "<intent>" in content and "<summary>" in content and "<tags>" in content


def _is_skill_eval_prompt(body: dict) -> bool:
    msgs = body.get("messages", [])
    user = next((m for m in msgs if m.get("role") == "user"), None)
    if not user:
        return False
    content = user.get("content", "")
    if isinstance(content, list):
        content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return "description_precision" in content and "step_actionability" in content


def _is_agent_skill_draft_prompt(body: dict) -> bool:
    msgs = body.get("messages", [])
    user = next((m for m in msgs if m.get("role") == "user"), None)
    if not user:
        return False
    content = user.get("content", "")
    if isinstance(content, list):
        content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return "[xskill-e2e-agent-stub]" in content


META_XML = """<intent>列出当前目录下的 Python 文件</intent>
<summary>
1. 用户请求列出工作目录中的 .py 文件。
2. Agent 直接给出建议命令 `ls *.py` 而无需调用 shell 工具。
3. 产出：一条用于复用的快捷查询模式。
4. 验证情况：用户在示例 stdout 中确认 main.py 与 utils.py 存在。
</summary>
<blockers><blocker>[无] 执行顺畅，未遇到明显障碍</blocker></blockers>
<shortest_path>1. 在项目根目录执行 ls *.py</shortest_path>
<success>true</success>
<tags><tag>file_ops</tag><tag>bash</tag><tag>listing</tag></tags>
"""


SKILL_MD_DRAFT = """---
name: list-py-files
description: >
  列出工作目录或子目录下的 Python 源文件。典型触发："列一下 *.py"、"看下有哪些 python 文件"
  以及"列出当前目录下所有 python 文件"。仅需 Bash 工具访问。
compatibility: >
  任何具备 Bash 工具的环境；只读操作，不修改文件系统。不要把此 skill 套用到
  其他语言的源文件枚举上。
metadata:
  version: 1
  created: "<AUTO>"
  last_updated: "<AUTO>"
  source_trajs: ["traj_0001"]
  frozen: false
  use_count: 0
---

# 列出 Python 源文件

当用户提出"列出 python 文件"一类请求时，给出可直接执行的 Bash 命令。

## 在项目根目录列出

1. 在项目根目录执行 `ls *.py`。
2. 如果当前目录无文件而需要递归，使用 `find . -name '*.py' -type f`。

   > ⚠️ 见 traj_0001：直接 `ls *.py` 在子目录无法显示嵌套结果，递归请用 find。

## 验证

3. 输出应为换行分隔的相对路径列表；返回码 0 表示成功。
"""


EVAL_XML = """<description_precision>9</description_precision>
<step_actionability>9</step_actionability>
<granularity>8</granularity>
<generalizability>8</generalizability>
<warning_coverage>9</warning_coverage>
<faithfulness>10</faithfulness>
<structural_quality>10</structural_quality>
"""


def _msg_parts(body: dict) -> list[dict]:
    out: list[dict] = []
    for m in body.get("messages", []):
        c = m.get("content")
        if isinstance(c, list):
            for p in c:
                if isinstance(p, dict):
                    out.append(p)
    return out


def _has_tool(body: dict, tool_name: str) -> bool:
    return any(t.get("name") == tool_name for t in body.get("tools", []))


def _has_tool_result(body: dict) -> bool:
    return any(p.get("type") == "tool_result" for p in _msg_parts(body))


def program_for_stage_a(fake: FakeLLMServer):
    """Stage A: a single ``claude -p`` call that uses a Bash tool then finishes.

    The CLI fires off preprocessing requests (e.g. topic summarization) with
    empty tools=[] before the real reasoning turn. We must only emit the
    tool_use response on the *real* turn — identified by Bash being among
    the declared tools.

    Sequence we expect on the real turn:
      req(tools=[..., Bash, ...], no tool_result)  → respond tool_use(Bash)
      req(... tool_result of Bash present)         → respond final text
    """
    # Real-turn tool_use trigger — must require Bash in tools.
    fake.add_responder("/v1/messages", Responder(
        name="stageA-toolcall",
        match=lambda body: _has_tool(body, "Bash") and not _has_tool_result(body),
        build=lambda body: make_anthropic_tool_use_response(
            tool_name="Bash",
            tool_input={"command": "ls *.py", "description": "list py files"},
            model=body.get("model", "claude-fake"),
        ),
    ))
    # Follow-up after tool_result arrives — final assistant text.
    fake.add_responder("/v1/messages", Responder(
        name="stageA-finalize",
        match=_has_tool_result,
        build=lambda body: make_anthropic_text_response(
            "找到了：main.py、utils.py 两个文件。",
            model=body.get("model", "claude-fake"),
        ),
    ))


def program_for_xskill_processing(fake: FakeLLMServer):
    """Stage C: serve meta XML, eval scores, agent-stub drafts."""
    # meta extraction
    fake.add_responder("/chat/completions", Responder(
        name="xskill-meta-extract",
        match=_is_extract_meta_prompt,
        build=lambda body: make_openai_chat_response(META_XML, model=body.get("model", "fake-llm")),
    ))
    # agent stub draft request
    fake.add_responder("/chat/completions", Responder(
        name="xskill-agent-stub",
        match=_is_agent_skill_draft_prompt,
        build=lambda body: make_openai_chat_response(SKILL_MD_DRAFT, model=body.get("model", "fake-llm")),
    ))
    # eval scoring
    fake.add_responder("/chat/completions", Responder(
        name="xskill-eval-score",
        match=_is_skill_eval_prompt,
        build=lambda body: make_openai_chat_response(EVAL_XML, model=body.get("model", "fake-llm")),
    ))


def program_for_stage_e(fake: FakeLLMServer):
    """Stage E: any /v1/messages just gets a plain text response.

    The point of stage E is not the response — it's that we capture
    the request body so we can assert the skill is in the system prompt.
    """
    fake.add_responder("/v1/messages", Responder(
        name="stageE-finalize",
        match=lambda body: True,
        build=lambda body: make_anthropic_text_response(
            "(stage E) skill listing capture done.",
            model=body.get("model", "claude-fake"),
        ),
    ))


# ────────────────────────────────────────────────────────────────────
# Agent stub — replaces xskill.process.run_agent for the test
# ────────────────────────────────────────────────────────────────────

def make_agent_stub():
    """Build a drop-in replacement for ``xskill.agent.run_agent`` that:

    1. Actually dials the configured LLM (so the fake server records it).
    2. Writes the response verbatim as ``<skill_dir>/list-py-files/SKILL.md``.

    This bypasses agno's multi-turn tool-call dance — modelling that
    inside the fake server would dwarf the rest of the test — while still
    exercising the LLM endpoint exactly the way the real agent would for
    the final write step.
    """
    def fake_run_agent(new_traj_md, new_traj_meta, config, log):
        from xskill.llm_client import create_llm_client
        from xskill.config import get_skill_dir

        llm = create_llm_client(config, role="skill")
        if llm is None:
            raise RuntimeError("fake_run_agent: no llm configured")
        # The marker is what the fake server's matcher keys on.
        prompt = (
            "[xskill-e2e-agent-stub]\n"
            "请基于以下轨迹生成 SKILL.md（含 YAML frontmatter）：\n"
            + new_traj_md[:1000]
        )
        content = llm.chat(prompt)
        skill_root = Path(get_skill_dir())
        # Extract skill name from the returned frontmatter; fall back to a
        # known value so the test stays self-contained if the draft is odd.
        skill_name = "list-py-files"
        target = skill_root / skill_name
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(content, encoding="utf-8")
        log(f"[stub] wrote SKILL.md for {skill_name}", "step")
        return {"content": content, "framework": "fake-stub"}

    return fake_run_agent


# ────────────────────────────────────────────────────────────────────
# Subprocess helper
# ────────────────────────────────────────────────────────────────────

def run_claude(prompt: str, *, home: Path, base_url: str, cwd: Path,
               extra_args: list[str] | None = None, timeout: int = 45) -> str:
    """Invoke real ``claude -p`` against the fake server. Returns stdout."""
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["ANTHROPIC_BASE_URL"] = base_url
    env["ANTHROPIC_AUTH_TOKEN"] = "fake-test-token"
    # Be paranoid: drop anything that might steer the CLI elsewhere.
    for k in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        env.pop(k, None)
    args = [
        CLAUDE_BIN,
        "--model", "claude-sonnet-4-6",
        "--permission-mode", "bypassPermissions",
        "--allowed-tools", "Bash",
        "-p", prompt,
    ]
    if extra_args:
        args[1:1] = extra_args
    proc = subprocess.run(
        args, cwd=str(cwd), env=env, timeout=timeout,
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"claude -p failed (code={proc.returncode})\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    return proc.stdout


# ────────────────────────────────────────────────────────────────────
# Main E2E test
# ────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(CLAUDE_BIN is None, reason="claude CLI not installed")
def test_full_pipeline_claude_to_skill_to_claude(sandbox, monkeypatch):
    fake: FakeLLMServer = sandbox.fake
    fake.reset()

    # ─── Stage A: claude -p → session JSONL ─────────────────────
    program_for_stage_a(fake)
    workdir = sandbox.home / "workdir"
    workdir.mkdir()
    (workdir / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (workdir / "utils.py").write_text("def x(): pass\n", encoding="utf-8")

    stdout = run_claude(
        "请列出当前目录下所有 python 文件，使用 Bash 工具。",
        home=sandbox.home,
        base_url=fake.base_url,
        cwd=workdir,
    )
    assert stdout.strip(), "claude produced no stdout"

    # The CLI should have written a session JSONL under the sandboxed HOME.
    sessions = list((sandbox.home / ".claude" / "projects").glob("*/*.jsonl"))
    assert sessions, "claude did not create any session JSONL"
    jsonl_size_before_ingest = sessions[0].stat().st_size
    assert jsonl_size_before_ingest > 0

    # And the fake server should have seen the Anthropic call(s).
    anth_reqs_a = fake.requests_for("/v1/messages")
    assert len(anth_reqs_a) >= 1
    # Sanity: at least one of them carried a tool_result back to us, proving
    # the CLI actually executed our Bash tool_use call.
    saw_tool_result = any(
        any(
            isinstance(p, dict) and p.get("type") == "tool_result"
            for m in r["body"].get("messages", [])
            for p in (m.get("content") if isinstance(m.get("content"), list) else [])
        )
        for r in anth_reqs_a
    )
    assert saw_tool_result, "expected the CLI to send a tool_result back"

    # ─── Stage B: ingest JSONL → traj_NNNN.md ────────────────────
    from xskill.ecosystems import ingest_claude_code_sessions
    submitted = ingest_claude_code_sessions(
        target_traj_dir=sandbox.watch_dir, home_root=sandbox.home,
    )
    assert submitted, "ingest_claude_code_sessions yielded nothing"
    traj_path = Path(submitted[0]["path"])
    assert traj_path.exists()
    assert traj_path.with_suffix(".json").exists()
    md_text = traj_path.read_text(encoding="utf-8")
    assert "# Claude Code Session Trajectory" in md_text
    assert "Tool Call: Bash" in md_text

    # Idempotency: a second ingest call should be a no-op.
    seen = {submitted[0]["session_id"]}
    submitted2 = ingest_claude_code_sessions(
        target_traj_dir=sandbox.watch_dir,
        home_root=sandbox.home,
        seen_sessions=seen,
    )
    assert submitted2 == [], "ingest should be idempotent given seen_sessions"

    # ─── Stage C: xskill processes traj → SKILL.md ───────────────
    program_for_xskill_processing(fake)
    monkeypatch.setattr("xskill.process.run_agent", make_agent_stub())

    # Also seed the .md.meta file that process_traj would otherwise need
    # from the meta extraction stage. We invoke the index path's single-meta
    # helper so this goes through the same code as production watcher does.
    from xskill.index import _process_one_meta
    from xskill.llm_client import create_llm_client
    from xskill.config import get_config
    cfg = get_config()
    llm_for_meta = create_llm_client(cfg)
    _process_one_meta(traj_path, llm_for_meta)
    meta_file = traj_path.parent / f"{traj_path.name}.meta"
    assert meta_file.exists(), "meta extraction did not produce .md.meta"

    from xskill.process import process_traj
    result = process_traj(
        traj_md_path=str(traj_path),
        config=cfg,
        skill_dir=sandbox.skill_dir,
        data_dir=sandbox.watch_dir,
    )
    assert result["action"] in ("merged", "staged"), f"unexpected result: {result!r}"
    assert "list-py-files" in result.get("skills", [])

    skill_md = sandbox.skill_dir / "list-py-files" / "SKILL.md"
    assert skill_md.exists(), "SKILL.md was not written to the skill repo"
    skill_text = skill_md.read_text(encoding="utf-8")
    assert "name: list-py-files" in skill_text
    assert "description:" in skill_text

    # The fake server should have served meta + agent-draft + eval requests.
    chat_reqs = fake.requests_for("/chat/completions")
    assert any(_is_extract_meta_prompt(r["body"]) for r in chat_reqs), \
        "no meta-extraction call landed on the fake server"
    assert any(_is_agent_skill_draft_prompt(r["body"]) for r in chat_reqs), \
        "agent stub did not dial the fake LLM"
    assert any(_is_skill_eval_prompt(r["body"]) for r in chat_reqs), \
        "no eval scoring call landed on the fake server"
    embed_reqs = fake.requests_for("/embeddings/multimodal")
    assert embed_reqs, "no embedding traffic — index rebuild did not run"

    # ─── Stage D: install into Claude Code's discovery path ──────
    from xskill.ecosystems import install_all_to_claude_code
    installed = install_all_to_claude_code(
        sandbox.skill_dir, target_root=sandbox.home,
    )
    assert installed, "install_all_to_claude_code returned nothing"
    cc_skill_path = sandbox.home / ".claude" / "skills" / "list-py-files" / "SKILL.md"
    assert cc_skill_path.exists(), "skill did not land in <home>/.claude/skills/"

    # ─── Stage E: claude -p sees the skill in its system prompt ──
    # Clear the request log so we only look at the second invocation.
    fake.reset()
    program_for_stage_e(fake)

    stdout2 = run_claude(
        "我现在想找 python 文件，列出它们。",
        home=sandbox.home,
        base_url=fake.base_url,
        cwd=workdir,
    )
    assert stdout2.strip()

    anth_reqs_e = fake.requests_for("/v1/messages")
    assert anth_reqs_e, "no anthropic traffic during stage E"

    # Search for the skill name string in any request body, anywhere.
    blob = json.dumps([r["body"] for r in anth_reqs_e], ensure_ascii=False)
    assert "list-py-files" in blob, (
        "Claude Code did not surface the installed skill to the model. "
        "Expected the skill name to appear in the system prompt or tools."
    )
    # And the description should be there too — that's what the routing LLM
    # actually reads to decide whether to load the skill body.
    assert "列出工作目录" in blob, (
        "skill description text was not surfaced to the model"
    )
