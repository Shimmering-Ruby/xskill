"""tests/test_e2e_xskill_serve_auto.py — 真正的 E2E

跟 tests/test_e2e_claude_code_pipeline.py 的区别：那一份是"集成测试穿上
subprocess 外衣"——中间用 ``from xskill.ecosystems import ...`` 手调把
ingest / process / install 拼起来。本文件是从用户视角出发的 E2E：

- 起一个真实的 ``xskill serve`` 子进程（sandbox HOME）
- 起一个真实的 ``claude -p`` 子进程对话
- 验证只通过 HTTP / CLI 子命令观察，**0 行 import xskill.***

期望行为（daemon 启动后用户应当看到的事实）：

1. ``xskill serve`` 自动 detect 到 ``<HOME>/.claude/projects/`` 存在，自动
   register 一个配套的 ``<HOME>/.xskill/cc_sessions/`` 给 watch（带
   ecosystem=claude_code）。无需用户跑 ``xskill registry add``。
2. 用户跑 ``claude -p`` → CC 在 ``~/.claude/projects/`` 写会话 JSONL →
   daemon 内的 CCSessionIngester 周期扫到 → 桥成 traj_*.md 落到 cc_sessions
   → DirectoryWatcher 周期发现 + 入索引。整个链路 daemon 自跑，用户什么都
   不用做。
3. ``GET /api/v1/registry/dirs`` 返回的 cc_sessions 行 ``traj_count`` 涨。

测试本身只用 subprocess + http；fake LLM 仍用本测试套通用的
``_fake_llm_server.FakeLLMServer``。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests
import yaml

# 复用 commit-A 留下的 fake server——它仍然是测试基础设施而不是 xskill 内部 API。
THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))
from _fake_llm_server import (  # noqa: E402
    FakeLLMServer,
    Responder,
    make_anthropic_text_response,
    make_anthropic_tool_use_response,
)


CLAUDE_BIN = shutil.which("claude")
XSKILL_BIN = shutil.which("xskill")


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _poll(predicate, *, timeout: float = 30.0, interval: float = 0.5,
          desc: str = "predicate") -> None:
    """Spin until ``predicate()`` returns truthy or ``timeout`` expires."""
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception as e:  # noqa: BLE001 — diagnostic only
            last_err = e
        time.sleep(interval)
    msg = f"timeout waiting for {desc} after {timeout}s"
    if last_err is not None:
        msg += f"; last exception: {last_err}"
    raise AssertionError(msg)


# ────────────────────────────────────────────────────────────────────
# Sandbox + daemon fixtures
# ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def fake_server():
    srv = FakeLLMServer()
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()


@pytest.fixture
def sandbox(tmp_path, fake_server):
    """A self-contained HOME the daemon + claude CLI will both look at.

    Returns a small bundle of paths the test uses to spawn subprocesses and
    later observe filesystem state.
    """
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"theme": "auto"}), encoding="utf-8",
    )
    # 关键：CC native session 根目录必须存在，否则 detect_known_ecosystems
    # 跳过 claude_code 不去 register。我们这里就是要测 daemon 自动发现。
    (home / ".claude" / "projects").mkdir()
    # CC 在第二次启动时还会读 skills/，提前建好空目录。
    (home / ".claude" / "skills").mkdir()

    xhome = home / ".xskill"
    xhome.mkdir()
    (xhome / "skill").mkdir()

    # xskill 自身配置：LLM / embed 全指向 fake；watcher poll_interval 调短
    # (默认 30s 太久——E2E 跑不完)，CC ingester 用 watcher.poll_interval。
    config_yaml = {
        "skill_dir": str(xhome / "skill"),
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
        "candidates": {"threshold": 3, "stale_days": 60, "min_source_trajs": 2},
        "sandbox": {"enabled": False, "trigger_threshold": 999},
        "canary": {"enabled": False, "probability": 0.0,
                   "min_samples": 5, "max_days_hold": 14},
        "watcher": {"poll_interval": 2},
    }
    (xhome / "config.yaml").write_text(
        yaml.safe_dump(config_yaml, allow_unicode=True), encoding="utf-8",
    )

    workdir = home / "workdir"
    workdir.mkdir()
    (workdir / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (workdir / "utils.py").write_text("def x(): pass\n", encoding="utf-8")

    class Box:
        pass
    box = Box()
    box.home = home
    box.xhome = xhome
    box.workdir = workdir
    box.cc_projects = home / ".claude" / "projects"
    box.cc_sessions = xhome / "cc_sessions"  # daemon will mkdir + register this
    box.fake = fake_server
    return box


def _child_env_for_sandbox(home: Path) -> dict:
    """Build subprocess env that points HOME at the sandbox but keeps Python's
    user-site-packages lookup pointed at the real user-base.

    Why: Python computes ``user-site`` as ``~/.local/lib/pythonX.Y/site-packages``
    by expanding ``$HOME``. Overriding HOME for our sandbox makes Python look
    in ``<sandbox>/.local/...`` which doesn't contain xskill's editable install
    → ``ModuleNotFoundError: No module named 'xskill'``. ``PYTHONUSERBASE``
    pins user-base back to the real one so the ``xskill`` CLI can import
    itself; HOME still steers ``Path.home()`` to the sandbox.
    """
    import site as _site
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PYTHONUSERBASE"] = _site.USER_BASE
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        env.pop(k, None)
    return env


@pytest.fixture
def xskill_daemon(sandbox):
    """Spawn the real ``xskill serve`` subprocess. Yields its base URL.

    Tears down by SIGTERM at fixture exit.
    """
    port = _free_port()
    env = _child_env_for_sandbox(sandbox.home)
    proc = subprocess.Popen(
        [XSKILL_BIN, "serve", "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base_url = f"http://127.0.0.1:{port}"

    # 等 server 就绪：HTTP 调通 + startup hook 完成（cc_sessions 注册可见）。
    def _ready() -> bool:
        try:
            r = requests.get(f"{base_url}/api/v1/registry/dirs", timeout=1.5)
            if r.status_code != 200:
                return False
            dirs = r.json().get("dirs", [])
            return any(d.get("ecosystem") == "claude_code" for d in dirs)
        except requests.RequestException:
            return False

    try:
        try:
            _poll(_ready, timeout=20.0, desc="xskill serve startup + cc_sessions registered")
        except AssertionError:
            # 拉一下 daemon 的 stderr 让失败诊断有用。
            proc.terminate()
            try:
                _, stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                _, stderr = proc.communicate(timeout=5)
            raise AssertionError(
                f"xskill serve did not become ready; subprocess stderr:\n"
                f"{stderr.decode('utf-8', errors='replace')[-2000:]}"
            )
        yield base_url
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()


# ────────────────────────────────────────────────────────────────────
# Real E2E test
# ────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(XSKILL_BIN is None, reason="xskill CLI not installed")
@pytest.mark.skipif(CLAUDE_BIN is None, reason="claude CLI not installed")
def test_serve_auto_detects_and_indexes_claude_sessions(sandbox, xskill_daemon):
    base_url = xskill_daemon
    fake = sandbox.fake

    # ───── 期望 1：daemon 已经自动 register 了 cc_sessions ─────
    # （xskill_daemon fixture 内 _ready() 已经断过一次，这里走 CLI 子命令
    # 验证从用户视角看到的输出，列里也带 ecosystem 标签。）
    out = subprocess.run(
        [XSKILL_BIN, "registry", "list"],
        env=_child_env_for_sandbox(sandbox.home),
        capture_output=True, text=True, timeout=10,
    )
    assert out.returncode == 0, f"xskill registry list failed: {out.stderr}"
    lines = [l for l in out.stdout.splitlines() if l.strip()]
    cc_line = next((l for l in lines if "claude_code" in l), None)
    assert cc_line is not None, (
        f"`xskill registry list` 未把 claude_code 行打出来：\n{out.stdout}"
    )
    # 列序: id  ecosystem  traj  indexed  label  path
    parts = cc_line.split("\t")
    assert len(parts) >= 6, f"unexpected list format: {cc_line!r}"
    assert parts[1] == "claude_code"
    assert str(sandbox.cc_sessions.resolve()) == parts[-1], (
        f"cc_sessions path mismatch in list output: {parts[-1]}"
    )
    initial_traj_count = int(parts[2])

    # ───── 期望 2：跑 claude -p 让 CC 写 JSONL ─────
    fake.reset()
    fake.add_responder("/v1/messages", Responder(
        name="stageA-toolcall",
        match=lambda b: any(t.get("name") == "Bash" for t in b.get("tools", [])) \
            and not any(
                p.get("type") == "tool_result"
                for m in b.get("messages", [])
                for p in (m.get("content") if isinstance(m.get("content"), list) else [])
            ),
        build=lambda b: make_anthropic_tool_use_response(
            tool_name="Bash",
            tool_input={"command": "ls *.py", "description": "list py files"},
            model=b.get("model", "claude-fake"),
        ),
    ))
    fake.add_responder("/v1/messages", Responder(
        name="stageA-final",
        match=lambda b: any(
            p.get("type") == "tool_result"
            for m in b.get("messages", [])
            for p in (m.get("content") if isinstance(m.get("content"), list) else [])
        ),
        build=lambda b: make_anthropic_text_response(
            "找到了 main.py 和 utils.py。", model=b.get("model", "claude-fake"),
        ),
    ))
    claude_env = os.environ.copy()
    claude_env["HOME"] = str(sandbox.home)
    claude_env["ANTHROPIC_BASE_URL"] = fake.base_url
    claude_env["ANTHROPIC_AUTH_TOKEN"] = "fake-token"
    claude_proc = subprocess.run(
        [
            CLAUDE_BIN, "--model", "claude-sonnet-4-6",
            "--permission-mode", "bypassPermissions",
            "--allowed-tools", "Bash",
            "-p", "请用 Bash 列出当前目录下的 .py 文件",
        ],
        cwd=str(sandbox.workdir),
        env=claude_env,
        stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=45,
    )
    assert claude_proc.returncode == 0, (
        f"claude -p failed: {claude_proc.stderr}"
    )

    # CC 必然写了 JSONL（其它流是 claude 内置行为，daemon 无关）
    jsonls = list((sandbox.cc_projects).glob("*/*.jsonl"))
    assert jsonls, "CC did not write a session JSONL"

    # ───── 期望 3：daemon 自己把 JSONL 桥成 traj_*.md ─────
    # CCSessionIngester 周期 poll_interval=2s（配在 sandbox config 里）
    def _bridge_done() -> bool:
        return any(sandbox.cc_sessions.glob("traj_*.md"))
    _poll(_bridge_done, timeout=20.0, desc="CC JSONL bridged into cc_sessions")
    bridged = sorted(sandbox.cc_sessions.glob("traj_*.md"))
    assert bridged, "ingester didn't create any traj_*.md under cc_sessions"

    # 校验桥过来的内容确实是 CC session（含 tool call）
    md_text = bridged[0].read_text(encoding="utf-8")
    assert "Claude Code Session Trajectory" in md_text
    assert "Tool Call: Bash" in md_text

    # 桥过来的 .json metadata 必须带 session_id（去重凭据）
    json_meta = json.loads(bridged[0].with_suffix(".json").read_text(encoding="utf-8"))
    assert json_meta.get("session_id"), "bridged metadata missing session_id"

    # ───── 期望 4：HTTP /api/v1/registry/dirs 反映 traj_count 涨 ─────
    def _count_increased() -> bool:
        r = requests.get(f"{base_url}/api/v1/registry/dirs", timeout=3)
        r.raise_for_status()
        for d in r.json().get("dirs", []):
            if d.get("ecosystem") == "claude_code":
                return int(d.get("traj_count", 0)) > initial_traj_count
        return False
    _poll(_count_increased, timeout=20.0,
          desc="watcher discovered bridged traj and traj_count updated")

    # 最终从 HTTP 端口读一次完整状态，断言关键字段对齐
    r = requests.get(f"{base_url}/api/v1/registry/dirs", timeout=3)
    r.raise_for_status()
    cc_dir = next(d for d in r.json()["dirs"] if d["ecosystem"] == "claude_code")
    assert cc_dir["path"] == str(sandbox.cc_sessions.resolve())
    assert cc_dir["traj_count"] >= 1
    assert cc_dir["label"] == "claude_code sessions"
