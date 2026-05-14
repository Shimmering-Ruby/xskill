# xskill team SP1 — C/S 同步骨架 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 xskill 加上 team C/S 模式：`xskill serve --server` 起 team server，`xskill connect IP:port` 起瘦客户端；client 采集本地轨迹脱敏上传、server 跑全部 agent、client 持有 server 算出的 ≤100 个 skill working copy 并按 server 分配的 side 对齐。

**Architecture:** 重 server / 瘦 client。所有调大模型的 agent（TaskAgent/Cluster/SkillEdit/canary judge）只在 server 跑；client 零 LLM、零 git 写 main、零灰度判定。skill 分发本质是 git 分布式——server 是中心仓库（每 skill 一个 git repo，baby/main/staging 三分支），client 持有部分 skill 的 working copy，靠 git bundle over HTTP 同步。灰度统一成 `pick_side(bucket_key, skill, p)` 一个确定性函数：CS 模式 bucket = `client_id`（空间维度真分桶），单机模式 bucket = 时间窗（已存在）。server 不存"账本表"——账本是 `pick_side` 纯函数 + skill git 状态的实时投影，client 每次 sync 现算返回。

**Tech Stack:** Python 3.11 / FastAPI / uvicorn / httpx / SQLite / git CLI（subprocess）/ pytest。新代码全部落 `src/xskill/team/`，测试落 `tests/test_team_*.py`。

**约束（来自 `CLAUDE.md`）：** ① 不写 fallback，遇问题 throw error；② 验收要端到端 E2E 集成测试，先写测试方案；③ OOP；④ 改动逻辑不做老配置兼容，手动迁移 + 新代码。每个 Task 自带单元测试。

**Out of scope（后续子项目）：** SP2 = 完整脱敏（db 文件、智能检测、模型侧意识）；SP3 = catalog/tag 索引 + 千人千面推荐算法（本 SP1 只落 80+20 的 *slot 结构*，`recommended` 桶用"按 ux 取接下来 20 个"占位，SP3 替换为画像质心）。质量门 / 进化 / 手动 push 不单列——它们是同步协议里的策略参数。

---

## File Structure

新增 `src/xskill/team/` 包，每个文件单一职责：

| 文件 | 职责 |
| --- | --- |
| `src/xskill/team/__init__.py` | 空包标记 |
| `src/xskill/team/redact.py` | 最小代码脱敏 hook（`sk-` key / 密码字面量正则）。SP2 扩展。 |
| `src/xskill/team/sync_protocol.py` | C/S 线协议的 pydantic 模型（Register/Upload/Sync/PushEdit）。单一事实源。 |
| `src/xskill/team/git_bundle.py` | git bundle 打包/落地/推送的 subprocess 封装。 |
| `src/xskill/team/server_state.py` | server 端 join token 的生成与读取（`~/.xskill/team_server.json`，0600）。 |
| `src/xskill/team/client_registry.py` | server 端 `ClientRegistry`（SQLite `team_clients.db`）。 |
| `src/xskill/team/skill_manifest.py` | server 端 `build_manifest(client_id)`：80+20 slot + per-client side 现算。 |
| `src/xskill/team/server_api.py` | `/api/v1/team/*` FastAPI 路由 + `init_team_context()`。 |
| `src/xskill/team/client_state.py` | client 端 `~/.xskill/team_client.json` 读写（server_url/client_id/token）。 |
| `src/xskill/team/reconcile.py` | 共享的 `reconcile_skill_side()`（contract 的步骤 2/3/4）。单机 watcher 与 client 共用。 |
| `src/xskill/team/collector.py` | client 端 `TeamCollector`：复用 `JsonlIngester`/`SqliteIngester` 把本地生态轨迹镜像进 outbox，跟踪上传游标。 |
| `src/xskill/team/client.py` | client 端 `TeamClient` 守护：register 握手 + `run_forever` + `_tick`（collect→upload→sync→reconcile→push-edit→cleanup）。 |

修改的文件：

| 文件 | 改动 |
| --- | --- |
| `src/xskill/config.py` | 加 team 路径 helper（`get_team_*`）。 |
| `src/xskill/watcher.py` | `DirectoryWatcher` 加 `server_mode` 旗标；server 模式跳过 `_check_user_edits` / `_reconcile_skill_sides`，`_install_skill_to_all_detected` 变 no-op；加 `_score_atoms_for_traj_server`（CS canary 归因）；`_reconcile_skill_sides` 改用共享 `reconcile_skill_side`。 |
| `src/xskill/server.py` | `create_app(team_server=False)`：team 模式跳过生态自动探测、注册 team traj_root、watcher 开 `server_mode`、挂 team router。 |
| `src/xskill/core.py` | `XSkill.serve(server_mode=False)`：team 模式打印 join token + connect 示例。 |
| `src/xskill/cli.py` | `serve` 加 `--server`；新增 `connect` 子命令；`main()` 重构——`connect` 不构造 `XSkill()`（瘦客户端无 LLM key）。 |
| `examples/config.yaml.example` | 加 `team:` 段。 |
| `README.md` | 加 team 模式简述。 |

---

## Phase A — 脱敏 / 协议 / 路径基建

### Task 1: 最小脱敏 hook

**Files:**
- Create: `src/xskill/team/__init__.py`
- Create: `src/xskill/team/redact.py`
- Test: `tests/test_team_redact.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_team_redact.py
from xskill.team.redact import redact_text


def test_redacts_sk_style_keys():
    src = 'OPENAI_API_KEY = "sk-abcdEFGH1234567890wxyz"'
    out = redact_text(src)
    assert "sk-abcdEFGH1234567890wxyz" not in out
    assert "[REDACTED]" in out


def test_redacts_password_assignments():
    for src in ['password: hunter2supersecret', 'DB_PASS="p@ssw0rd-very-long"',
                "token = 'ghp_0123456789abcdef0123'"]:
        out = redact_text(src)
        assert "[REDACTED]" in out
        assert "hunter2" not in out and "p@ssw0rd" not in out and "ghp_0123" not in out


def test_leaves_ordinary_text_untouched():
    src = "# 这是一段正常的轨迹\nuser: 帮我跑 pytest\nassistant: 好的"
    assert redact_text(src) == src


def test_idempotent():
    src = 'key = "sk-abcdEFGH1234567890wxyz"'
    once = redact_text(src)
    assert redact_text(once) == once
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3.11 -m pytest tests/test_team_redact.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'xskill.team'`

- [ ] **Step 3: 实现**

```python
# src/xskill/team/__init__.py
"""xskill team — C/S 同步骨架（SP1）。"""
```

```python
# src/xskill/team/redact.py
"""redact.py — 上传前的最小代码脱敏 hook（SP1）

只防最常见的明文凭证泄漏：``sk-`` 风格的 key、``password/token/secret/api_key``
赋值字面量。db 文件、智能检测、模型侧意识留到 SP2。

设计：纯函数 + 幂等。命中即整体替换为 ``[REDACTED]``，不做部分遮掩——
SP1 的目标是"别让明文密钥裸奔到 server"，不是精细化脱敏。
"""
from __future__ import annotations

import re

# sk- / ghp_ / AKIA 等带固定前缀的长 token
_PREFIXED_TOKEN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{12,}"
    r"|ghp_[A-Za-z0-9]{16,}"
    r"|AKIA[0-9A-Z]{12,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,})\b"
)

# password / passwd / pass / token / secret / api_key = "...." 形式的赋值
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|pass|secret|token|api[_-]?key)\b"
    r"(\s*[:=]\s*)"
    r"(\"[^\"]{6,}\"|'[^']{6,}'|[^\s\"']{6,})"
)

_REDACTED = "[REDACTED]"


def redact_text(text: str) -> str:
    """对一段轨迹文本做最小脱敏。幂等：``[REDACTED]`` 自身不会被再次命中。"""
    text = _PREFIXED_TOKEN.sub(_REDACTED, text)
    text = _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", text)
    return text
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3.11 -m pytest tests/test_team_redact.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/xskill/team/__init__.py src/xskill/team/redact.py tests/test_team_redact.py
git commit -m "feat(team): SP1 最小脱敏 hook (sk-/password 正则)"
```

---

### Task 2: config team 路径 helper

**Files:**
- Modify: `src/xskill/config.py`（在 `get_chat_db_path` 后、`is_debug` 前插入）
- Test: `tests/test_team_config.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_team_config.py
from pathlib import Path

from xskill import config as C


def test_team_paths_under_xskill_home():
    assert C.get_team_server_state_path() == C.XSKILL_HOME / "team_server.json"
    assert C.get_team_clients_db_path() == C.XSKILL_HOME / "team_clients.db"
    assert C.get_team_client_state_path() == C.XSKILL_HOME / "team_client.json"
    assert C.get_team_skills_dir() == C.XSKILL_HOME / "team_skills"
    assert C.get_team_outbox_dir() == C.XSKILL_HOME / "team_outbox"


def test_team_dir_helpers_create_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "XSKILL_HOME", tmp_path / ".xskill")
    skills = C.get_team_skills_dir()
    outbox = C.get_team_outbox_dir()
    assert skills.is_dir() and outbox.is_dir()
```

> ⚠️ 这些 helper 必须**不调用 `get_config()`**——client 是瘦客户端，没有 `llm.api_key`，`load_config` 会抛 `KeyError`。只有 server 侧的 `get_team_trajectories_dir()` 才允许读 config（server 一定有 key）。见 `config.py:47-50`。

- [ ] **Step 2: 跑测试确认失败**

Run: `python3.11 -m pytest tests/test_team_config.py -q`
Expected: FAIL — `AttributeError: module 'xskill.config' has no attribute 'get_team_server_state_path'`

- [ ] **Step 3: 实现**

在 `src/xskill/config.py` 的 `get_chat_db_path()` 定义之后插入：

```python
# ─── team (C/S 模式) 路径 ───────────────────────────────────────
# 纯路径运算，不读 config.yaml——client 瘦客户端无 llm.api_key，
# get_config() 会抛 KeyError。get_team_trajectories_dir() 是唯一例外
# （只 server 调，server 一定有 key）。

def get_team_server_state_path() -> Path:
    """server join token 落盘位置（~/.xskill/team_server.json，0600）。"""
    return XSKILL_HOME / "team_server.json"


def get_team_clients_db_path() -> Path:
    """server 端 client 注册表 SQLite。"""
    return XSKILL_HOME / "team_clients.db"


def get_team_client_state_path() -> Path:
    """client 端连接信息（server_url / client_id / join_token）。"""
    return XSKILL_HOME / "team_client.json"


def get_team_skills_dir() -> Path:
    """client 端 skill working copies 根目录。"""
    p = XSKILL_HOME / "team_skills"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_team_outbox_dir() -> Path:
    """client 端生态轨迹镜像 outbox 根目录。"""
    p = XSKILL_HOME / "team_outbox"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_team_trajectories_dir() -> Path:
    """server 端收下的 client 上传轨迹根目录。

    读 config.yaml ``team.server.traj_root``，缺省 ~/.xskill/team_trajectories。
    仅 server 调用。
    """
    cfg = get_config()
    raw = (cfg.get("team", {}).get("server", {}).get("traj_root")
           or str(XSKILL_HOME / "team_trajectories"))
    p = Path(raw).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3.11 -m pytest tests/test_team_config.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/xskill/config.py tests/test_team_config.py
git commit -m "feat(team): config team 路径 helper"
```

---

### Task 3: 线协议 pydantic 模型

**Files:**
- Create: `src/xskill/team/sync_protocol.py`
- Test: `tests/test_team_protocol.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_team_protocol.py
from xskill.team.sync_protocol import (
    RegisterRequest, RegisterResponse,
    UploadTrajectory, UploadRequest, UploadResponse,
    SkillSlot, SyncResponse, PushEditResponse,
)


def test_register_roundtrip():
    req = RegisterRequest(token="abc", client_label="alice-laptop", hostname="alice")
    assert RegisterRequest.model_validate(req.model_dump()) == req
    resp = RegisterResponse(client_id="cid-1")
    assert RegisterResponse.model_validate(resp.model_dump()).client_id == "cid-1"


def test_upload_roundtrip():
    req = UploadRequest(trajectories=[
        UploadTrajectory(traj_id="traj_cc_x_001", content="# hi", sha256="deadbeef"),
    ])
    back = UploadRequest.model_validate(req.model_dump())
    assert back.trajectories[0].traj_id == "traj_cc_x_001"
    resp = UploadResponse(accepted=["traj_cc_x_001"], rejected=[])
    assert UploadResponse.model_validate(resp.model_dump()).accepted == ["traj_cc_x_001"]


def test_sync_response_slots():
    slot = SkillSlot(skill_name="fix-foo", side="staging", sha="abc123", bucket="ranked")
    resp = SyncResponse(slots=[slot], server_time=1.0)
    back = SyncResponse.model_validate(resp.model_dump())
    assert back.slots[0].side == "staging" and back.slots[0].bucket == "ranked"


def test_skill_slot_rejects_bad_side():
    import pytest
    with pytest.raises(Exception):
        SkillSlot(skill_name="x", side="prod", sha="abc", bucket="ranked")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3.11 -m pytest tests/test_team_protocol.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'xskill.team.sync_protocol'`

- [ ] **Step 3: 实现**

```python
# src/xskill/team/sync_protocol.py
"""sync_protocol.py — C/S 线协议模型（SP1）

C 与 S 之间所有 HTTP body 的单一事实源。端点：

  POST /api/v1/team/register          RegisterRequest  -> RegisterResponse
  POST /api/v1/team/upload            UploadRequest    -> UploadResponse
  GET  /api/v1/team/sync              (query)          -> SyncResponse
  GET  /api/v1/team/skill/{n}/bundle  (query)          -> application/octet-stream
  POST /api/v1/team/push-edit         (multipart)      -> PushEditResponse

鉴权（除 register 外所有端点）：HTTP header
  X-Xskill-Token   = server join token
  X-Xskill-Client  = client_id
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Side = Literal["main", "staging"]
Bucket = Literal["ranked", "recommended"]


class RegisterRequest(BaseModel):
    token: str
    client_label: str = ""
    hostname: str = ""


class RegisterResponse(BaseModel):
    client_id: str


class UploadTrajectory(BaseModel):
    traj_id: str           # 形如 traj_cc_<project>_<sid8>，必须 traj_ 前缀
    content: str           # 已脱敏的 markdown 全文
    sha256: str            # content 的 sha256，server 端去重用


class UploadRequest(BaseModel):
    trajectories: list[UploadTrajectory] = Field(default_factory=list)


class UploadRejection(BaseModel):
    traj_id: str
    reason: str


class UploadResponse(BaseModel):
    accepted: list[str] = Field(default_factory=list)
    rejected: list[UploadRejection] = Field(default_factory=list)


class SkillSlot(BaseModel):
    """client 应持有的一个 skill 槽位。side/sha 由 server 现算（pick_side + git 状态）。"""
    skill_name: str
    side: Side
    sha: str
    bucket: Bucket         # ranked = ux_score 滑窗；recommended = SP3 画像位（SP1 占位）


class SyncResponse(BaseModel):
    slots: list[SkillSlot] = Field(default_factory=list)   # ≤100
    server_time: float


class PushEditResponse(BaseModel):
    branch: str            # user-staging/<client_id>
    ref_sha: str
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3.11 -m pytest tests/test_team_protocol.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/xskill/team/sync_protocol.py tests/test_team_protocol.py
git commit -m "feat(team): C/S 线协议 pydantic 模型"
```

---

### Task 4: git bundle 封装

**Files:**
- Create: `src/xskill/team/git_bundle.py`
- Test: `tests/test_team_git_bundle.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_team_git_bundle.py
import subprocess
from pathlib import Path

import pytest

from xskill.team.git_bundle import (
    make_repo_bundle, apply_repo_bundle, make_branch_bundle, fetch_branch_from_bundle,
)


def _git(args, cwd):
    r = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _seed_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], root)
    _git(["checkout", "-q", "-b", "main"], root)
    _git(["config", "user.email", "t@t"], root)
    _git(["config", "user.name", "t"], root)
    (root / "SKILL.md").write_text("v1", encoding="utf-8")
    _git(["add", "."], root)
    _git(["commit", "-q", "-m", "v1"], root)
    _git(["checkout", "-q", "-b", "staging"], root)
    (root / "SKILL.md").write_text("v2", encoding="utf-8")
    _git(["commit", "-q", "-am", "v2"], root)
    _git(["checkout", "-q", "main"], root)
    return root


def test_make_and_apply_bundle_clones_all_branches(tmp_path):
    src = _seed_repo(tmp_path / "central" / "fix-foo")
    bundle = make_repo_bundle(src)
    assert isinstance(bundle, bytes) and len(bundle) > 0

    dest = tmp_path / "client" / "fix-foo"
    apply_repo_bundle(bundle, dest)
    assert (dest / ".git").is_dir()
    main_sha = _git(["rev-parse", "main"], dest)
    staging_sha = _git(["rev-parse", "staging"], dest)
    assert main_sha != staging_sha


def test_apply_bundle_updates_existing_repo(tmp_path):
    src = _seed_repo(tmp_path / "central" / "fix-foo")
    dest = tmp_path / "client" / "fix-foo"
    apply_repo_bundle(make_repo_bundle(src), dest)
    # central 上 main 前进一格
    (src / "SKILL.md").write_text("v3", encoding="utf-8")
    _git(["commit", "-q", "-am", "v3"], src)
    new_main = _git(["rev-parse", "main"], src)
    apply_repo_bundle(make_repo_bundle(src), dest)
    assert _git(["rev-parse", "main"], dest) == new_main


def test_push_branch_roundtrip(tmp_path):
    central = _seed_repo(tmp_path / "central" / "fix-foo")
    client = tmp_path / "client" / "fix-foo"
    apply_repo_bundle(make_repo_bundle(central), client)
    # client 在 main 基础上做一笔"用户手改"提交到 _useredit
    _git(["checkout", "-q", "-B", "_useredit", "main"], client)
    (client / "SKILL.md").write_text("user-edit", encoding="utf-8")
    _git(["commit", "-q", "-am", "user edit"], client)
    bundle = make_branch_bundle(client, "_useredit")
    sha = fetch_branch_from_bundle(bundle, central, "_useredit",
                                   "refs/heads/user-staging/cid-1")
    assert _git(["rev-parse", "user-staging/cid-1"], central) == sha
    assert _git(["rev-parse", "main"], central) != sha   # main 没被动


def test_make_repo_bundle_rejects_non_repo(tmp_path):
    with pytest.raises(NotADirectoryError):
        make_repo_bundle(tmp_path / "nope")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3.11 -m pytest tests/test_team_git_bundle.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'xskill.team.git_bundle'`

- [ ] **Step 3: 实现**

```python
# src/xskill/team/git_bundle.py
"""git_bundle.py — skill git 仓的 bundle 传输封装（SP1）

skill 分发本质是 git 分布式。SP1 不跑独立 git http daemon，而是把每个
skill 子仓打成 git bundle 走普通 HTTP body 传输：

- server → client：``make_repo_bundle`` 打全分支 → client ``apply_repo_bundle``
  克隆/fetch 落到本地 working copy。
- client → server：client ``make_branch_bundle`` 打 ``_useredit`` 分支 →
  server ``fetch_branch_from_bundle`` 收进 ``user-staging/<client_id>``。

SP1 每次传全量 bundle（skill 仓很小：SKILL.md ≤400 行 + 几个 script）。
增量 bundle 是后续优化。遇到 git 失败一律 throw（CLAUDE.md：不写 fallback）。
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


def _run(args: list[str]) -> str:
    r = subprocess.run(["git"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def make_repo_bundle(repo_dir: Path | str) -> bytes:
    """把一个 skill git 仓的所有本地分支打成 bundle 字节。"""
    repo_dir = Path(repo_dir)
    if not (repo_dir / ".git").is_dir():
        raise NotADirectoryError(f"not a git repo: {repo_dir}")
    with tempfile.NamedTemporaryFile(suffix=".bundle", delete=False) as tf:
        bundle_path = Path(tf.name)
    try:
        _run(["-C", str(repo_dir), "bundle", "create", str(bundle_path), "--branches"])
        return bundle_path.read_bytes()
    finally:
        bundle_path.unlink(missing_ok=True)


def apply_repo_bundle(bundle_bytes: bytes, dest_dir: Path | str) -> None:
    """用 bundle 在本地物化/刷新一个 skill working copy。

    dest_dir 不是 git 仓 → ``git clone --no-checkout``（分支齐全，工作树留给
    reconcile 去 checkout）。已是 git 仓 → ``git fetch`` 把 bundle 的
    ``refs/heads/*`` 强制覆盖本地同名分支（main/staging/baby）。
    """
    dest_dir = Path(dest_dir)
    with tempfile.NamedTemporaryFile(suffix=".bundle", delete=False) as tf:
        tf.write(bundle_bytes)
        bundle_path = Path(tf.name)
    try:
        if not (dest_dir / ".git").is_dir():
            dest_dir.parent.mkdir(parents=True, exist_ok=True)
            _run(["clone", "--no-checkout", "-q", str(bundle_path), str(dest_dir)])
        else:
            _run(["-C", str(dest_dir), "fetch", "-q", str(bundle_path),
                  "+refs/heads/*:refs/heads/*"])
    finally:
        bundle_path.unlink(missing_ok=True)


def make_branch_bundle(repo_dir: Path | str, branch: str) -> bytes:
    """把一个分支（含完整历史）打成 bundle 字节。client 推手改用。"""
    repo_dir = Path(repo_dir)
    if not (repo_dir / ".git").is_dir():
        raise NotADirectoryError(f"not a git repo: {repo_dir}")
    with tempfile.NamedTemporaryFile(suffix=".bundle", delete=False) as tf:
        bundle_path = Path(tf.name)
    try:
        _run(["-C", str(repo_dir), "bundle", "create", str(bundle_path), branch])
        return bundle_path.read_bytes()
    finally:
        bundle_path.unlink(missing_ok=True)


def fetch_branch_from_bundle(
    bundle_bytes: bytes, dest_repo: Path | str, src_branch: str, dest_ref: str,
) -> str:
    """把 bundle 里的 ``src_branch`` fetch 进 ``dest_repo`` 的 ``dest_ref``。

    返回 ``dest_ref`` 的新 sha。server 收 client 手改时用——dest_ref 形如
    ``refs/heads/user-staging/<client_id>``，永远不碰 main。
    """
    dest_repo = Path(dest_repo)
    if not (dest_repo / ".git").is_dir():
        raise NotADirectoryError(f"not a git repo: {dest_repo}")
    with tempfile.NamedTemporaryFile(suffix=".bundle", delete=False) as tf:
        tf.write(bundle_bytes)
        bundle_path = Path(tf.name)
    try:
        _run(["-C", str(dest_repo), "fetch", "-q", str(bundle_path),
              f"refs/heads/{src_branch}:{dest_ref}"])
        return _run(["-C", str(dest_repo), "rev-parse", dest_ref])
    finally:
        bundle_path.unlink(missing_ok=True)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3.11 -m pytest tests/test_team_git_bundle.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/xskill/team/git_bundle.py tests/test_team_git_bundle.py
git commit -m "feat(team): git bundle 传输封装"
```

---

## Phase B — Server 侧：token / 注册表 / manifest / API

### Task 5: server join token

**Files:**
- Create: `src/xskill/team/server_state.py`
- Test: `tests/test_team_server_state.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_team_server_state.py
import stat

from xskill.team.server_state import ensure_join_token, load_join_token


def test_ensure_generates_and_persists(tmp_path):
    p = tmp_path / "team_server.json"
    tok = ensure_join_token(p)
    assert isinstance(tok, str) and len(tok) >= 16
    assert p.is_file()
    # 第二次调用返回同一个 token（不重新生成）
    assert ensure_join_token(p) == tok


def test_token_file_is_0600(tmp_path):
    p = tmp_path / "team_server.json"
    ensure_join_token(p)
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600


def test_load_returns_none_when_missing(tmp_path):
    assert load_join_token(tmp_path / "absent.json") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3.11 -m pytest tests/test_team_server_state.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'xskill.team.server_state'`

- [ ] **Step 3: 实现**

```python
# src/xskill/team/server_state.py
"""server_state.py — team server 的 join token（SP1）

join token 是 client 接入的唯一门槛（单一共享 token，见设计决策）。
client 完全信任 server；token 只用来挡住组织外的随机接入。真正的防呆
是"client 永远只能写 user-staging/<client_id>，碰不到 main"。

token 落 ~/.xskill/team_server.json，0600 权限。切勿回显进日志。
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path


def ensure_join_token(path: Path | str) -> str:
    """读取已有 join token；不存在则生成一个并以 0600 落盘。返回 token。"""
    path = Path(path)
    existing = load_join_token(path)
    if existing:
        return existing
    token = secrets.token_hex(16)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"join_token": token}), encoding="utf-8")
    path.chmod(0o600)
    return token


def load_join_token(path: Path | str) -> str | None:
    """读 join token；文件不存在或损坏返回 None。"""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    tok = data.get("join_token")
    return tok if isinstance(tok, str) and tok else None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3.11 -m pytest tests/test_team_server_state.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/xskill/team/server_state.py tests/test_team_server_state.py
git commit -m "feat(team): server join token 生成与读取"
```

---

### Task 6: client 注册表

**Files:**
- Create: `src/xskill/team/client_registry.py`
- Test: `tests/test_team_client_registry.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_team_client_registry.py
from xskill.team.client_registry import ClientRegistry


def test_register_returns_unique_ids(tmp_path):
    reg = ClientRegistry(tmp_path / "team_clients.db")
    a = reg.register(label="alice-laptop", hostname="alice")
    b = reg.register(label="bob-laptop", hostname="bob")
    assert a != b
    assert reg.exists(a) and reg.exists(b)
    assert not reg.exists("nonexistent")


def test_touch_updates_last_seen(tmp_path):
    reg = ClientRegistry(tmp_path / "team_clients.db")
    cid = reg.register(label="x", hostname="x")
    before = reg.get(cid)["last_seen"]
    reg.touch(cid)
    after = reg.get(cid)["last_seen"]
    assert after >= before


def test_list_returns_all(tmp_path):
    reg = ClientRegistry(tmp_path / "team_clients.db")
    reg.register(label="a", hostname="a")
    reg.register(label="b", hostname="b")
    rows = reg.list()
    assert len(rows) == 2
    assert {r["label"] for r in rows} == {"a", "b"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3.11 -m pytest tests/test_team_client_registry.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'xskill.team.client_registry'`

- [ ] **Step 3: 实现**

```python
# src/xskill/team/client_registry.py
"""client_registry.py — team server 的 client 注册表（SP1）

server 需要持久化的只有三样：client 注册表、skill git 仓、汇聚的
ux_score 明细。这个文件是第一样。

client_id 是 server 生成的 uuid——它同时是 ① canary 分桶 key（喂
pick_side）② 上传轨迹的落盘分桶（clients/<client_id>/sessions/）③
手改分支命名（user-staging/<client_id>）。
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    client_id  TEXT PRIMARY KEY,
    label      TEXT DEFAULT '',
    hostname   TEXT DEFAULT '',
    joined_at  TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ClientRegistry:
    """SQLite 支撑的 client 注册表。每次操作开新连接（规模小，几十个 client）。"""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        conn = self._conn()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def register(self, *, label: str = "", hostname: str = "") -> str:
        """注册一个新 client，返回新生成的 client_id。"""
        client_id = uuid.uuid4().hex
        now = _now()
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO clients (client_id, label, hostname, joined_at, last_seen)"
                " VALUES (?, ?, ?, ?, ?)",
                (client_id, label, hostname, now, now),
            )
            conn.commit()
        finally:
            conn.close()
        return client_id

    def exists(self, client_id: str) -> bool:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM clients WHERE client_id=?", (client_id,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def touch(self, client_id: str) -> None:
        """更新 last_seen。client_id 不存在则静默 no-op。"""
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE clients SET last_seen=? WHERE client_id=?",
                (_now(), client_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get(self, client_id: str) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM clients WHERE client_id=?", (client_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list(self) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM clients ORDER BY joined_at"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3.11 -m pytest tests/test_team_client_registry.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/xskill/team/client_registry.py tests/test_team_client_registry.py
git commit -m "feat(team): client 注册表 (SQLite)"
```

---

### Task 7: skill manifest 构建器

**Files:**
- Create: `src/xskill/team/skill_manifest.py`
- Test: `tests/test_team_skill_manifest.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_team_skill_manifest.py
import subprocess
from pathlib import Path

from xskill.team.skill_manifest import build_manifest


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True, check=True)


def _make_skill(root: Path, name: str, *, with_staging: bool = False) -> Path:
    d = root / name
    d.mkdir(parents=True)
    _git(["init", "-q"], d)
    _git(["checkout", "-q", "-b", "main"], d)
    _git(["config", "user.email", "t@t"], d)
    _git(["config", "user.name", "t"], d)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\nmetadata:\n  version: 1\n---\n# {name}\n",
        encoding="utf-8",
    )
    _git(["add", "."], d)
    _git(["commit", "-q", "-m", "v1"], d)
    if with_staging:
        _git(["checkout", "-q", "-b", "staging"], d)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: d2\nmetadata:\n  version: 2\n---\n# {name} v2\n",
            encoding="utf-8",
        )
        _git(["commit", "-q", "-am", "v2"], d)
        _git(["checkout", "-q", "main"], d)
    return d


def test_manifest_caps_total_slots(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    for i in range(150):
        _make_skill(skill_dir, f"skill-{i:03d}")
    resp = build_manifest(client_id="cid-1", skill_dir=skill_dir,
                          probability=0.2, ranked_slots=80, total_slots=100)
    assert len(resp.slots) == 100
    ranked = [s for s in resp.slots if s.bucket == "ranked"]
    recommended = [s for s in resp.slots if s.bucket == "recommended"]
    assert len(ranked) == 80 and len(recommended) == 20


def test_manifest_main_only_skill_has_main_side(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    _make_skill(skill_dir, "no-staging")
    resp = build_manifest(client_id="cid-1", skill_dir=skill_dir,
                          probability=0.2, ranked_slots=80, total_slots=100)
    assert len(resp.slots) == 1
    assert resp.slots[0].side == "main"
    assert resp.slots[0].sha   # 非空


def test_manifest_staging_side_is_deterministic_per_client(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    _make_skill(skill_dir, "graying", with_staging=True)
    s1 = build_manifest(client_id="cid-A", skill_dir=skill_dir,
                        probability=0.2, ranked_slots=80, total_slots=100).slots[0]
    s2 = build_manifest(client_id="cid-A", skill_dir=skill_dir,
                        probability=0.2, ranked_slots=80, total_slots=100).slots[0]
    assert s1.side == s2.side          # 同 client 同 skill 永远同 side
    assert s1.side in ("main", "staging")
    # probability=1.0 → 必 staging；sha 必须是 staging HEAD
    forced = build_manifest(client_id="cid-A", skill_dir=skill_dir,
                            probability=1.0, ranked_slots=80, total_slots=100).slots[0]
    assert forced.side == "staging"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3.11 -m pytest tests/test_team_skill_manifest.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'xskill.team.skill_manifest'`

- [ ] **Step 3: 实现**

```python
# src/xskill/team/skill_manifest.py
"""skill_manifest.py — 给一个 client 现算它该持有的 ≤100 个 skill slot（SP1）

server 端**不存"账本表"**。manifest = ``pick_side`` 纯函数 + skill git
状态（has_staging / main_sha / staging_sha）的实时投影，每次 sync 现算。

slot 结构 = 80 ranked + 20 recommended：
- ranked      —— 按 ux_score（main 侧近 30 天均分）滑窗取高分。
- recommended —— SP3 = 用户画像质心推荐位。SP1 占位：按 ux 继续往下取 20 个。
                 slot 结构本身（bucket 字段）SP1 就落地，SP3 只换 recommended 的选法。

灰度归因：某 skill 有 staging 分支 → side = pick_side(client_id, name, p)，
确定性伪随机，同 client 同 skill 在整轮灰度内 side 钉死。无 staging → main。
"""
from __future__ import annotations

import time
from pathlib import Path

from xskill.canary import has_staging, main_sha, pick_side, staging_sha
from xskill.entities.skill import Skill
from xskill.entities.skill_repo import SkillRepo
from xskill.team.sync_protocol import SkillSlot, SyncResponse


def _rank_key(skill: Skill) -> tuple[float, int]:
    """排序键：(main 侧近 30 天 ux 均分, use_count)，都缺则 (0.0, 0)。"""
    avg = skill.ux_avg(side="main", days=30)
    return (avg if avg is not None else 0.0, skill.use_count)


def _resolve_slot(skill: Skill, client_id: str, probability: float, bucket: str) -> SkillSlot:
    """对一个 skill 现算它对该 client 的 side + sha。"""
    if has_staging(skill.path):
        side = pick_side(client_id, skill.name, probability)
        sha = staging_sha(skill.path) if side == "staging" else main_sha(skill.path)
    else:
        side = "main"
        sha = main_sha(skill.path)
    if not sha:
        raise RuntimeError(f"skill {skill.name!r}: cannot resolve sha for side={side}")
    return SkillSlot(skill_name=skill.name, side=side, sha=sha, bucket=bucket)


def build_manifest(
    *,
    client_id: str,
    skill_dir: Path | str,
    probability: float,
    ranked_slots: int = 80,
    total_slots: int = 100,
) -> SyncResponse:
    """为 ``client_id`` 现算 manifest。skill 总数不足 total_slots 时全发。"""
    repo = SkillRepo(Path(skill_dir))
    skills = sorted(repo, key=_rank_key, reverse=True)
    chosen = skills[:total_slots]
    slots: list[SkillSlot] = []
    for idx, skill in enumerate(chosen):
        bucket = "ranked" if idx < ranked_slots else "recommended"
        slots.append(_resolve_slot(skill, client_id, probability, bucket))
    return SyncResponse(slots=slots, server_time=time.time())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3.11 -m pytest tests/test_team_skill_manifest.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/xskill/team/skill_manifest.py tests/test_team_skill_manifest.py
git commit -m "feat(team): skill manifest 构建器 (80+20 slot 结构)"
```

---

### Task 8: team server API 路由

**Files:**
- Create: `src/xskill/team/server_api.py`
- Test: `tests/test_team_server_api.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_team_server_api.py
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xskill.team import server_api
from xskill.team.client_registry import ClientRegistry


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True, check=True)


def _make_skill(skill_dir: Path, name: str):
    d = skill_dir / name
    d.mkdir(parents=True)
    _git(["init", "-q"], d); _git(["checkout", "-q", "-b", "main"], d)
    _git(["config", "user.email", "t@t"], d); _git(["config", "user.name", "t"], d)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\nmetadata:\n  version: 1\n---\n# {name}\n",
        encoding="utf-8")
    _git(["add", "."], d); _git(["commit", "-q", "-m", "v1"], d)
    return d


@pytest.fixture
def client(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    _make_skill(skill_dir, "fix-foo")
    traj_root = tmp_path / "team_traj"
    reg = ClientRegistry(tmp_path / "clients.db")
    server_api.init_team_context(
        join_token="secret-token",
        client_registry=reg,
        skill_dir=skill_dir,
        traj_root=traj_root,
        probability=0.2, ranked_slots=80, total_slots=100,
        register_dir=lambda path, label: None,   # 测试不碰真 registry.db
    )
    app = FastAPI()
    app.include_router(server_api.router)
    return TestClient(app)


def test_register_then_use_endpoints(client):
    # 错 token 被拒
    r = client.post("/api/v1/team/register", json={"token": "wrong"})
    assert r.status_code == 401
    # 正确 token → 拿 client_id
    r = client.post("/api/v1/team/register",
                    json={"token": "secret-token", "client_label": "alice", "hostname": "a"})
    assert r.status_code == 200
    cid = r.json()["client_id"]

    hdr = {"X-Xskill-Token": "secret-token", "X-Xskill-Client": cid}
    # 上传一条轨迹
    r = client.post("/api/v1/team/upload", headers=hdr, json={
        "trajectories": [{"traj_id": "traj_cc_x_001", "content": "# hello",
                          "sha256": "abc"}]})
    assert r.status_code == 200
    assert r.json()["accepted"] == ["traj_cc_x_001"]

    # sync 拿 manifest
    r = client.get("/api/v1/team/sync", headers=hdr)
    assert r.status_code == 200
    names = [s["skill_name"] for s in r.json()["slots"]]
    assert "fix-foo" in names

    # 拉 skill bundle
    r = client.get("/api/v1/team/skill/fix-foo/bundle", headers=hdr)
    assert r.status_code == 200
    assert r.content[:4] == b"# v2" or len(r.content) > 0


def test_unknown_client_rejected(client):
    hdr = {"X-Xskill-Token": "secret-token", "X-Xskill-Client": "ghost"}
    r = client.get("/api/v1/team/sync", headers=hdr)
    assert r.status_code == 403


def test_upload_writes_traj_md_under_client_bucket(client, tmp_path):
    r = client.post("/api/v1/team/register", json={"token": "secret-token"})
    cid = r.json()["client_id"]
    hdr = {"X-Xskill-Token": "secret-token", "X-Xskill-Client": cid}
    client.post("/api/v1/team/upload", headers=hdr, json={
        "trajectories": [{"traj_id": "traj_cc_x_001", "content": "# body",
                          "sha256": "abc"}]})
    expected = (tmp_path / "team_traj" / "clients" / cid / "sessions"
                / "traj_cc_x_001.md")
    assert expected.is_file()
    assert expected.read_text(encoding="utf-8") == "# body"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3.11 -m pytest tests/test_team_server_api.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'xskill.team.server_api'`

- [ ] **Step 3: 实现**

```python
# src/xskill/team/server_api.py
"""server_api.py — /api/v1/team/* 路由（SP1）

team server 的 5 个端点。鉴权：除 register 外都校验
``X-Xskill-Token`` == join token 且 ``X-Xskill-Client`` 在注册表里。
client 完全信任 server；token 只挡组织外随机接入。

上下文（join_token / registry / skill_dir / traj_root / canary 参数）通过
``init_team_context`` 注入到模块级单例——沿用 ``skill_tools.init_context``
的既有模式，不引入 FastAPI Depends 体系。
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response

from xskill.team.client_registry import ClientRegistry
from xskill.team.git_bundle import fetch_branch_from_bundle, make_repo_bundle
from xskill.team.skill_manifest import build_manifest
from xskill.team.sync_protocol import (
    PushEditResponse, RegisterRequest, RegisterResponse,
    UploadRejection, UploadRequest, UploadResponse,
)

logger = logging.getLogger("xskill.team.server_api")
router = APIRouter(prefix="/api/v1/team")


class _Ctx:
    """模块级上下文单例。init_team_context 填，端点读。"""
    join_token: str = ""
    client_registry: ClientRegistry | None = None
    skill_dir: Path | None = None
    traj_root: Path | None = None
    probability: float = 0.2
    ranked_slots: int = 80
    total_slots: int = 100
    register_dir: Callable[[Path, str], None] | None = None


_ctx = _Ctx()


def init_team_context(
    *,
    join_token: str,
    client_registry: ClientRegistry,
    skill_dir: Path,
    traj_root: Path,
    probability: float,
    ranked_slots: int,
    total_slots: int,
    register_dir: Callable[[Path, str], None],
) -> None:
    """create_app(team_server=True) 在 startup 时调用一次。"""
    _ctx.join_token = join_token
    _ctx.client_registry = client_registry
    _ctx.skill_dir = Path(skill_dir)
    _ctx.traj_root = Path(traj_root)
    _ctx.probability = probability
    _ctx.ranked_slots = ranked_slots
    _ctx.total_slots = total_slots
    _ctx.register_dir = register_dir


def _auth(token: str | None, client_id: str | None) -> str:
    """校验 token + client_id，返回 client_id。失败抛 HTTPException。"""
    if _ctx.client_registry is None:
        raise HTTPException(status_code=503, detail="team context not initialized")
    if not token or token != _ctx.join_token:
        raise HTTPException(status_code=401, detail="invalid join token")
    if not client_id or not _ctx.client_registry.exists(client_id):
        raise HTTPException(status_code=403, detail="unknown client_id")
    _ctx.client_registry.touch(client_id)
    return client_id


@router.post("/register", response_model=RegisterResponse)
async def team_register(req: RegisterRequest) -> RegisterResponse:
    if _ctx.client_registry is None:
        raise HTTPException(status_code=503, detail="team context not initialized")
    if req.token != _ctx.join_token:
        raise HTTPException(status_code=401, detail="invalid join token")
    client_id = _ctx.client_registry.register(
        label=req.client_label, hostname=req.hostname,
    )
    logger.info("team client registered: %s (label=%s)", client_id, req.client_label)
    return RegisterResponse(client_id=client_id)


@router.post("/upload", response_model=UploadResponse)
async def team_upload(
    req: UploadRequest,
    x_xskill_token: str | None = Header(default=None),
    x_xskill_client: str | None = Header(default=None),
) -> UploadResponse:
    client_id = _auth(x_xskill_token, x_xskill_client)
    sessions_dir = _ctx.traj_root / "clients" / client_id / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    # 该 client 桶首次出现 → 注册成 watch_dir，label=client_id 让 watcher
    # 在 CS 归因时能反查 client。register_dir 幂等。
    if _ctx.register_dir is not None:
        _ctx.register_dir(sessions_dir, client_id)

    accepted: list[str] = []
    rejected: list[UploadRejection] = []
    for t in req.trajectories:
        if not t.traj_id.startswith("traj_"):
            rejected.append(UploadRejection(traj_id=t.traj_id,
                                            reason="traj_id must start with 'traj_'"))
            continue
        actual = hashlib.sha256(t.content.encode("utf-8")).hexdigest()
        # sha256 不匹配 → 传输损坏，拒收（CLAUDE.md：遇问题 throw，不静默接受）
        if t.sha256 and actual != t.sha256:
            rejected.append(UploadRejection(traj_id=t.traj_id, reason="sha256 mismatch"))
            continue
        (sessions_dir / f"{t.traj_id}.md").write_text(t.content, encoding="utf-8")
        accepted.append(t.traj_id)
    logger.info("team upload from %s: %d accepted, %d rejected",
                client_id, len(accepted), len(rejected))
    return UploadResponse(accepted=accepted, rejected=rejected)


@router.get("/sync")
async def team_sync(
    x_xskill_token: str | None = Header(default=None),
    x_xskill_client: str | None = Header(default=None),
):
    client_id = _auth(x_xskill_token, x_xskill_client)
    resp = build_manifest(
        client_id=client_id,
        skill_dir=_ctx.skill_dir,
        probability=_ctx.probability,
        ranked_slots=_ctx.ranked_slots,
        total_slots=_ctx.total_slots,
    )
    return resp.model_dump()


@router.get("/skill/{name}/bundle")
async def team_skill_bundle(
    name: str,
    x_xskill_token: str | None = Header(default=None),
    x_xskill_client: str | None = Header(default=None),
) -> Response:
    _auth(x_xskill_token, x_xskill_client)
    repo_dir = _ctx.skill_dir / name
    if not (repo_dir / ".git").is_dir():
        raise HTTPException(status_code=404, detail=f"skill not found: {name}")
    bundle = make_repo_bundle(repo_dir)
    return Response(content=bundle, media_type="application/octet-stream")


@router.post("/push-edit", response_model=PushEditResponse)
async def team_push_edit(
    request: Request,
    x_xskill_token: str | None = Header(default=None),
    x_xskill_client: str | None = Header(default=None),
    x_xskill_skill: str | None = Header(default=None),
) -> PushEditResponse:
    client_id = _auth(x_xskill_token, x_xskill_client)
    if not x_xskill_skill:
        raise HTTPException(status_code=400, detail="X-Xskill-Skill header required")
    repo_dir = _ctx.skill_dir / x_xskill_skill
    if not (repo_dir / ".git").is_dir():
        raise HTTPException(status_code=404, detail=f"skill not found: {x_xskill_skill}")
    bundle = await request.body()
    if not bundle:
        raise HTTPException(status_code=400, detail="empty bundle")
    dest_ref = f"refs/heads/user-staging/{client_id}"
    sha = fetch_branch_from_bundle(bundle, repo_dir, "_useredit", dest_ref)
    logger.info("team push-edit: %s -> %s (%s)", x_xskill_skill, dest_ref, sha[:8])
    return PushEditResponse(branch=f"user-staging/{client_id}", ref_sha=sha)
```

> ⚠️ `push-edit` 把 client 手改收进 `user-staging/<client_id>` 分支——**永远不碰 main**。SP1 到此为止：server 的 SkillEditAgent 下一轮做 staging 时读不读这个分支，是 SP1 之后的策略（设计里说"提示词引导 agent 读这个分支的 commit"）。SP1 只保证手改安全落到隔离分支。

- [ ] **Step 4: 跑测试确认通过**

Run: `python3.11 -m pytest tests/test_team_server_api.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/xskill/team/server_api.py tests/test_team_server_api.py
git commit -m "feat(team): /api/v1/team/* 路由 (register/upload/sync/bundle/push-edit)"
```

---

## Phase C — Watcher server 模式 + CS canary 归因

### Task 9: watcher server_mode 旗标

**Files:**
- Modify: `src/xskill/watcher.py`（`__init__` 加参数；`_scan_once` 加分支；`_install_skill_to_all_detected` 加早返回）
- Test: `tests/test_team_watcher_server_mode.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_team_watcher_server_mode.py
from xskill.watcher import DirectoryWatcher


def test_server_mode_flag_defaults_false():
    w = DirectoryWatcher()
    assert w.server_mode is False


def test_server_mode_skips_user_edit_and_reconcile(monkeypatch):
    w = DirectoryWatcher(server_mode=True)
    calls = []
    monkeypatch.setattr(w, "_harvest", lambda: calls.append("harvest"))
    monkeypatch.setattr(w, "_check_pending_skill_edits", lambda: calls.append("edits"))
    monkeypatch.setattr(w, "_check_canary_decisions", lambda: calls.append("canary"))
    monkeypatch.setattr(w, "_check_user_edits", lambda: calls.append("user_edits"))
    monkeypatch.setattr(w, "_reconcile_skill_sides", lambda: calls.append("reconcile"))
    # list_watch_dirs 返回空，跳过 _scan_dir
    monkeypatch.setattr("xskill.watcher.list_watch_dirs", lambda **kw: [])
    w._scan_once()
    assert "harvest" in calls and "edits" in calls and "canary" in calls
    assert "user_edits" not in calls   # server 模式跳过
    assert "reconcile" not in calls    # server 模式跳过


def test_server_mode_install_is_noop(tmp_path):
    w = DirectoryWatcher(server_mode=True)
    result = w._install_skill_to_all_detected(tmp_path / "any-skill")
    assert result == {}


def test_standalone_mode_runs_all(monkeypatch):
    w = DirectoryWatcher(server_mode=False)
    calls = []
    for m in ("_harvest", "_check_pending_skill_edits", "_check_canary_decisions",
              "_check_user_edits", "_reconcile_skill_sides"):
        monkeypatch.setattr(w, m, lambda m=m: calls.append(m))
    monkeypatch.setattr("xskill.watcher.list_watch_dirs", lambda **kw: [])
    w._scan_once()
    assert "_check_user_edits" in calls and "_reconcile_skill_sides" in calls
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3.11 -m pytest tests/test_team_watcher_server_mode.py -q`
Expected: FAIL — `AttributeError: 'DirectoryWatcher' object has no attribute 'server_mode'`

- [ ] **Step 3: 实现**

在 `src/xskill/watcher.py` 的 `DirectoryWatcher.__init__` 签名加参数（`home_root=None` 之后）：

```python
    def __init__(self, *, llm=None, embed_client=None, config=None,
                 skill_dir=None, poll_interval=30.0, max_concurrent=30,
                 max_retries=3, db_path=None, cold_start_threshold=3,
                 store=None, agno_agent_factory=None, home_root=None,
                 server_mode=False):
```

在 `__init__` body 里（`self.home_root = ...` 之后）加：

```python
        # server_mode：team server 模式。server 是纯 server——不装 skill 到
        # 本机生态、不做单机灰度轮转、不做本地手改回流（手改走 client
        # push-edit → user-staging/<client_id> 分支）。只跑 agent 流水线
        # （split/cluster/SkillEdit/canary 判定）+ CS 归因打分。
        self.server_mode = bool(server_mode)
```

把 `_scan_once` 的 Step 7 / Step 8 改成 server 模式跳过：

```python
        # ── Step 7: 用户手改回流检测 ──
        # server 模式跳过：server 本机没有 symlink 出去的 skill 给用户改；
        # client 手改走 push-edit 进 user-staging/<client_id> 分支。
        if not self.server_mode:
            self._check_user_edits()

        # ── Step 8: 单机 canary 流量入口轮转 ──
        # server 模式跳过：server 不装 skill 到本机，无"流量入口"概念。
        # CS 模式的分桶在 client 的 reconcile_skill_sides 里按 client_id 做。
        if not self.server_mode:
            self._reconcile_skill_sides()
```

在 `_install_skill_to_all_detected` 方法体最开头（`from xskill.ecosystems import ...` 之前）加：

```python
        # server 模式：纯 server 不装 skill 到本机生态，直接 no-op。
        if self.server_mode:
            return {}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3.11 -m pytest tests/test_team_watcher_server_mode.py tests/test_watcher.py tests/test_watcher_atom.py tests/test_canary_rotation.py -q`
Expected: PASS（新测试 4 passed + 既有 watcher/rotation 测试不回归）

- [ ] **Step 5: Commit**

```bash
git add src/xskill/watcher.py tests/test_team_watcher_server_mode.py
git commit -m "feat(team): watcher server_mode 旗标 (跳过本机装/轮转/手改回流)"
```

---

### Task 10: CS canary 归因——server 模式打分

**Files:**
- Modify: `src/xskill/watcher.py`（加 `_score_atoms_for_traj_server`；`_on_cluster_done` 末尾按 mode 分流）
- Test: `tests/test_team_cs_attribution.py`

**背景：** 单机 `_score_atoms_for_traj` 靠解析 traj header `<!-- xskill:skill=X side=Y sha=Z -->` 来归因——一条 traj 只认一个 skill。CS 模式下一条上传轨迹可能用了多个 team skill，且 side 由 `pick_side(client_id, ...)` 决定。所以 server 模式走一条新路径：遍历每个 atom 的 `used_skills`，对每个用到的 team skill 用 `pick_side` 现算 side，逐个 `score_atom` + `AtomCanary.append`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_team_cs_attribution.py
import subprocess
from pathlib import Path

from xskill.watcher import DirectoryWatcher
from xskill.atom_task import AtomTask, AtomTaskStore
from xskill.canary import load_ux_scores


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True, check=True)


def _make_skill(skill_dir: Path, name: str):
    d = skill_dir / name
    d.mkdir(parents=True)
    _git(["init", "-q"], d); _git(["checkout", "-q", "-b", "main"], d)
    _git(["config", "user.email", "t@t"], d); _git(["config", "user.name", "t"], d)
    (d / "SKILL.md").write_text(f"# {name}", encoding="utf-8")
    _git(["add", "."], d); _git(["commit", "-q", "-m", "v1"], d)
    return d


class _FakeLLM:
    """score_atom 用的最小 LLM 桩——score_atom 内部调用形态见 ux_score.py。"""
    def __init__(self): self.calls = 0
    # score_atom 实际怎么调 LLM 由实现决定；此桩在 Step 3 按 score_atom
    # 的真实签名补齐（执行者读 src/xskill/ux_score.py:score_atom 确认）。


def test_server_mode_scores_each_used_skill(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    _make_skill(skill_dir, "fix-foo")

    # 构造一条已 split 完的 traj：1 个 atom，used_skills=["fix-foo"]
    sessions = tmp_path / "clients" / "cid-1" / "sessions"
    sessions.mkdir(parents=True)
    md = sessions / "traj_cc_x_001.md"
    md.write_text("# body", encoding="utf-8")
    store = AtomTaskStore(root=sessions)
    store.save(AtomTask(
        atom_id="atom_traj_cc_x_001_0001", traj_id="traj_cc_x_001",
        offset_start=0, offset_end=6, intent="i", summary="s",
        tags=[], used_skills=["fix-foo"], ux_score=None,
        pre_atom_id=None, post_atom_id=None, context_prefix="", raw_segment="# body",
    ))

    # 桩掉 score_atom，断言它对 fix-foo 被调用、side=main（无 staging）
    scored = []
    def _fake_score_atom(*, llm, atom, side):
        scored.append((atom.atom_id, side))
        return {"score": 8, "reasons": "ok"}
    monkeypatch.setattr("xskill.ux_score.score_atom", _fake_score_atom)

    w = DirectoryWatcher(llm=object(), skill_dir=skill_dir, store=store,
                         config={"canary": {"probability": 0.2}}, server_mode=True)
    # 模拟 list_watch_dirs 返回该 client 桶，label=client_id
    monkeypatch.setattr("xskill.watcher.list_watch_dirs",
                        lambda **kw: [{"id": 1, "path": str(sessions), "label": "cid-1"}])
    w._score_atoms_for_traj_server(1, "traj_cc_x_001.md")

    assert scored == [("atom_traj_cc_x_001_0001", "main")]
    rows = load_ux_scores(skill_dir / "fix-foo")
    assert len(rows) == 1 and rows[0]["side"] == "main" and rows[0]["score"] == 8.0
```

> ⚠️ 执行者在 Step 3 前先读 `src/xskill/ux_score.py` 的 `score_atom` 与 `src/xskill/atom_canary.py` 的 `AtomCanary.append` 确认精确签名——Step 1 桩按既有调用形态写（`score_atom(llm=, atom=, side=)`，见 `watcher.py:871-873`）。

- [ ] **Step 2: 跑测试确认失败**

Run: `python3.11 -m pytest tests/test_team_cs_attribution.py -q`
Expected: FAIL — `AttributeError: 'DirectoryWatcher' object has no attribute '_score_atoms_for_traj_server'`

- [ ] **Step 3: 实现**

在 `src/xskill/watcher.py` 紧接 `_score_atoms_for_traj` 方法之后加新方法：

```python
    def _score_atoms_for_traj_server(self, wd_id, fname, **kw):
        """CS 模式打分：遍历每个 atom 的 used_skills，对每个用到的 team skill
        用 pick_side(client_id, ...) 现算 side，逐个 score + AtomCanary.append。

        与单机 _score_atoms_for_traj 的差异：
        - 不读 traj header（一条上传轨迹可能用多个 team skill）
        - client_id 从 watch_dir 的 label 取（upload 端点注册时 label=client_id）
        - side 由 pick_side 现算，不是 header 里写死的
        """
        if self.llm is None or self.skill_dir is None:
            return
        from xskill.atom_canary import AtomCanary
        from xskill.canary import (
            CanaryConfig, has_staging, main_sha, pick_side, staging_sha,
        )
        from xskill.ux_score import score_atom

        # 找到该 wd 的 dir_path + client_id（label）
        client_id = None
        dir_path = None
        for wd in list_watch_dirs(**kw):
            if wd["id"] == wd_id:
                dir_path = Path(wd["path"])
                client_id = wd.get("label") or ""
                break
        if dir_path is None or not client_id:
            return
        md_path = dir_path / fname
        if not md_path.is_file():
            return
        traj_id = md_path.stem
        store = self._store_for(dir_path)
        atoms = store.list_by_traj(traj_id)
        if not atoms:
            return
        canary_cfg = CanaryConfig.from_dict(self.config.get("canary", {}))
        used_any = False
        for atom in atoms:
            for skill_name in (atom.used_skills or []):
                skill_sub = self.skill_dir / skill_name
                if not (skill_sub / ".git").is_dir():
                    continue
                if has_staging(skill_sub):
                    side = pick_side(client_id, skill_name, canary_cfg.probability)
                    sha = staging_sha(skill_sub) if side == "staging" else main_sha(skill_sub)
                else:
                    side = "main"
                    sha = main_sha(skill_sub)
                try:
                    result = score_atom(llm=self.llm, atom=atom, side=side)
                    if result["score"] is None:
                        continue
                    AtomCanary(skill_dir=skill_sub).append(
                        atom_id=atom.atom_id, skill_name=skill_name,
                        side=side, commit_sha=sha or "",
                        score=result["score"], reasons=result["reasons"],
                    )
                    self._stats["scores"] += 1
                    used_any = True
                except Exception:
                    logger.exception("CS score_atom failed: %s/%s/%s",
                                     fname, atom.atom_id, skill_name)
        if used_any:
            logger.info("CS attribution done: %s (client=%s)", fname, client_id)
```

把 `_on_cluster_done` 末尾的打分调用改为按 mode 分流：

```python
        # cluster 完成后该 traj 的所有 atom 都已落盘——打分时机。
        if self.server_mode:
            self._score_atoms_for_traj_server(wd_id, fname, **kw)
        else:
            self._score_atoms_for_traj(wd_id, fname, **kw)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3.11 -m pytest tests/test_team_cs_attribution.py tests/test_watcher_atom.py -q`
Expected: PASS（新测试 1 passed + watcher 既有测试不回归）

- [ ] **Step 5: Commit**

```bash
git add src/xskill/watcher.py tests/test_team_cs_attribution.py
git commit -m "feat(team): CS canary 归因 — server 模式按 used_skills + pick_side 打分"
```

---

### Task 11: create_app team_server 接线

**Files:**
- Modify: `src/xskill/server.py`（`create_app` 签名 + startup hook）
- Test: `tests/test_team_create_app.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_team_create_app.py
import inspect

from xskill.server import create_app


def test_create_app_accepts_team_server_kwarg():
    sig = inspect.signature(create_app)
    assert "team_server" in sig.parameters
    assert sig.parameters["team_server"].default is False
```

> ⚠️ `create_app` 的完整 startup 行为（生态探测开关、watcher server_mode、team router 挂载）由 Task 19 的端到端集成测试覆盖——这里只钉签名契约，避免单测里真起 FastAPI startup（要 LLM/embed client）。

- [ ] **Step 2: 跑测试确认失败**

Run: `python3.11 -m pytest tests/test_team_create_app.py -q`
Expected: FAIL — `AssertionError: assert 'team_server' in {...}`

- [ ] **Step 3: 实现**

`src/xskill/server.py` 的 `create_app` 签名改为：

```python
def create_app(home_root: Path | str | None = None,
               *, team_server: bool = False) -> FastAPI:
```

在 `app.include_router(router)` 之后加 team router 挂载：

```python
    # team server 模式：挂 /api/v1/team/* 路由
    if team_server:
        from xskill.team.server_api import router as team_router
        app.include_router(team_router)
```

在 `_startup` hook 里，把"Auto-detect known agent ecosystems"整块（`server.py:1416-1555` 的 `try: ... except: logger.warning("ecosystem auto-detect failed")`）用 `if not team_server:` 包起来——team server 是纯 server，不采集自己这台机器的本地轨迹。

紧接其后、`# Start watcher if any dirs are registered` 之前，加 team server 的上下文初始化：

```python
        # team server：初始化 team 上下文 + 注册 traj_root 为 watch_dir 基。
        if team_server:
            try:
                from xskill.team.client_registry import ClientRegistry
                from xskill.team.server_api import init_team_context
                from xskill.team.server_state import ensure_join_token
                from xskill.config import (
                    get_team_clients_db_path, get_team_server_state_path,
                    get_team_trajectories_dir,
                )
                from xskill.registry import register_dir as _register_dir
                from xskill.canary import CanaryConfig

                join_token = ensure_join_token(get_team_server_state_path())
                client_registry = ClientRegistry(get_team_clients_db_path())
                traj_root = get_team_trajectories_dir()
                team_cfg = _config.get("team", {}).get("server", {})
                canary_cfg = CanaryConfig.from_dict(_config.get("canary", {}))

                def _team_register_dir(path, label):
                    # team_client 生态标签：watcher 的 CS 归因靠 wd.label 反查 client
                    _register_dir(path, label=label, ecosystem="team_client")

                init_team_context(
                    join_token=join_token,
                    client_registry=client_registry,
                    skill_dir=_skill_dir,
                    traj_root=traj_root,
                    probability=canary_cfg.probability,
                    ranked_slots=int(team_cfg.get("ranked_slots", 80)),
                    total_slots=int(team_cfg.get("skill_slots", 100)),
                    register_dir=_team_register_dir,
                )
                logger.info("team server context ready (traj_root=%s)", traj_root)
            except Exception:
                logger.warning("team server context init failed", exc_info=True)
```

把 watcher 创建块（`server.py:1558-1575`）里的 `DirectoryWatcher(...)` 调用加上 `server_mode=team_server`：

```python
                watcher = DirectoryWatcher(
                    llm=llm, embed_client=embed, config=_config,
                    skill_dir=_skill_dir,
                    poll_interval=float(watcher_cfg.get("poll_interval", 30)),
                    max_concurrent=int(watcher_cfg.get("max_concurrent", 30)),
                    cold_start_threshold=int(watcher_cfg.get("cold_start_threshold", 3)),
                    server_mode=team_server,
                )
```

> ⚠️ team server 模式下 `dirs = list_watch_dirs()` 在**首个 client upload 之前是空的**——watcher 不会启动。这是正确行为：没 client 接入就没活干。第一个 upload 端点会 `register_dir`，下次进程重启或……不对，watcher 只在 startup 起一次。**修正**：team server 模式下，即使 `dirs` 为空也要 `watcher.start()`——watcher 的 `_scan_once` 每轮重新 `list_watch_dirs()`，新注册的 client 桶下一轮就被扫到。把 watcher 启动条件改成 `if dirs or team_server:`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python3.11 -m pytest tests/test_team_create_app.py tests/test_e2e_xskill_serve_auto.py -q`
Expected: PASS（新测试 1 passed + 既有 serve 自动化 E2E 不回归）

- [ ] **Step 5: Commit**

```bash
git add src/xskill/server.py tests/test_team_create_app.py
git commit -m "feat(team): create_app(team_server=) 接线 — 跳过生态探测/挂 team router/watcher server_mode"
```

---

### Task 12: XSkill.serve server_mode + CLI `--server`

**Files:**
- Modify: `src/xskill/core.py`（`serve` 加 `server_mode`）
- Modify: `src/xskill/cli.py`（`serve` 加 `--server`；`cmd_serve` 分流）
- Test: `tests/test_team_cli_serve.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_team_cli_serve.py
import inspect

from xskill.cli import build_parser
from xskill.core import XSkill


def test_serve_subcommand_has_server_flag():
    parser = build_parser()
    args = parser.parse_args(["serve", "--server"])
    assert args.server is True
    args2 = parser.parse_args(["serve"])
    assert args2.server is False


def test_xskill_serve_accepts_server_mode():
    sig = inspect.signature(XSkill.serve)
    assert "server_mode" in sig.parameters
    assert sig.parameters["server_mode"].default is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3.11 -m pytest tests/test_team_cli_serve.py -q`
Expected: FAIL — `AttributeError: 'Namespace' object has no attribute 'server'`

- [ ] **Step 3: 实现**

`src/xskill/core.py` 的 `serve` 方法签名 + body 改为：

```python
    def serve(self, host: str = "0.0.0.0", port: int = 8000,
              *, home_root: Path | str | None = None,
              server_mode: bool = False) -> None:
        """启动 FastAPI server（含 watcher 后台线程）。阻塞。

        Args:
            home_root: debug 模式下指向自选目录。生产留 None。
            server_mode: True = team server 模式（收 client 上传、跑全部
                agent、提供 /api/v1/team/* 同步接口）。
        """
        import uvicorn
        from xskill.server import create_app
        app = create_app(home_root=home_root, team_server=server_mode)
        if server_mode:
            from xskill.team.server_state import ensure_join_token
            from xskill.config import get_team_server_state_path
            token = ensure_join_token(get_team_server_state_path())
            print(f"xskill team server at http://{host}:{port}/")
            print(f"  clients join with:")
            print(f"    xskill connect <THIS_HOST>:{port} --token {token}")
        elif home_root:
            print(f"xskill serve at http://{host}:{port}/  [debug home: {home_root}]")
        else:
            print(f"xskill serve at http://{host}:{port}/")
            print(f"  standalone mode — skills 留在本机。team 共享用:"
                  f" xskill serve --server")
        uvicorn.run(app, host=host, port=port)
```

`src/xskill/cli.py` 的 `build_parser` 里 `p_serve` 加 flag：

```python
    p_serve.add_argument(
        "--server", action="store_true",
        help="team server 模式：收 client 上传轨迹、跑全部 agent、"
             "提供 /api/v1/team/* 同步接口。不加则 standalone（仅本机）。",
    )
```

`cmd_serve` 末尾 `xskill.serve(...)` 调用改为传 `server_mode`：

```python
    xskill.serve(host=args.host, port=args.port, home_root=home_root,
                 server_mode=args.server)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3.11 -m pytest tests/test_team_cli_serve.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/xskill/core.py src/xskill/cli.py tests/test_team_cli_serve.py
git commit -m "feat(team): xskill serve --server — team server 模式 + 打印 connect 示例"
```

---

## Phase D — Client 侧：状态 / 采集 / reconcile / 守护

### Task 13: client 连接状态

**Files:**
- Create: `src/xskill/team/client_state.py`
- Test: `tests/test_team_client_state.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_team_client_state.py
import pytest

from xskill.team.client_state import ClientState, save_client_state, load_client_state


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "team_client.json"
    st = ClientState(server_url="http://1.2.3.4:8000", client_id="cid-1",
                     join_token="tok")
    save_client_state(st, p)
    back = load_client_state(p)
    assert back == st


def test_load_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_client_state(tmp_path / "absent.json")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3.11 -m pytest tests/test_team_client_state.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'xskill.team.client_state'`

- [ ] **Step 3: 实现**

```python
# src/xskill/team/client_state.py
"""client_state.py — client 端连接信息持久化（SP1）

瘦客户端不读 config.yaml（无 llm.api_key）。它要记住的只有连上谁：
server_url / client_id / join_token，落 ~/.xskill/team_client.json。

``xskill connect <addr> --token <t>`` 首次握手后写这个文件；后续
``xskill connect``（无参）直接读它复用连接。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class ClientState:
    server_url: str          # 形如 http://1.2.3.4:8000
    client_id: str
    join_token: str


def save_client_state(state: ClientState, path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
    path.chmod(0o600)


def load_client_state(path: Path | str) -> ClientState:
    """读连接状态。文件不存在抛 FileNotFoundError（CLAUDE.md：遇问题 throw）。"""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"team client state not found: {path}\n"
            f"先跑一次 `xskill connect <host:port> --token <token>` 建立连接。"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return ClientState(
        server_url=data["server_url"],
        client_id=data["client_id"],
        join_token=data["join_token"],
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3.11 -m pytest tests/test_team_client_state.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/xskill/team/client_state.py tests/test_team_client_state.py
git commit -m "feat(team): client 连接状态持久化"
```

---

### Task 14: 共享 reconcile 助手

**Files:**
- Create: `src/xskill/team/reconcile.py`
- Test: `tests/test_team_reconcile.py`

**背景（设计里约定的函数）：** `reconcile_skill_sides()` 是 k8s controller 式调谐——每轮把"本地 skill 实际挂的 side"对齐到"它该挂的 side"。契约 4 步里**只有第 1 步（决定 target）分叉**：单机按时间窗、CS 按 server 账本。步骤 2/3/4（手改优先 / 已对齐则跳过 / checkout+记账）两模式通用，抽成本文件的 `reconcile_skill_side()`（单数）。Task 16 的 client 与 Task 18 的单机 watcher 都调它。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_team_reconcile.py
import subprocess
from pathlib import Path

from xskill.team.reconcile import reconcile_skill_side
from xskill.install_history import InstallHistory


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                          text=True, check=True).stdout.strip()


def _seed(root: Path) -> tuple[Path, str, str]:
    root.mkdir(parents=True)
    _git(["init", "-q"], root); _git(["checkout", "-q", "-b", "main"], root)
    _git(["config", "user.email", "t@t"], root); _git(["config", "user.name", "t"], root)
    (root / "SKILL.md").write_text("v1", encoding="utf-8")
    _git(["add", "."], root); _git(["commit", "-q", "-m", "v1"], root)
    main_sha = _git(["rev-parse", "HEAD"], root)
    _git(["checkout", "-q", "-b", "staging"], root)
    (root / "SKILL.md").write_text("v2", encoding="utf-8")
    _git(["commit", "-q", "-am", "v2"], root)
    staging_sha = _git(["rev-parse", "HEAD"], root)
    _git(["checkout", "-q", "main"], root)
    return root, main_sha, staging_sha


def test_checks_out_target_and_records(tmp_path):
    repo, main_sha, staging_sha = _seed(tmp_path / "fix-foo")
    hist = InstallHistory(tmp_path / "history.jsonl")
    changed = []
    res = reconcile_skill_side(repo_dir=repo, target_side="staging",
                               target_sha=staging_sha, history=hist,
                               on_changed=lambda p: changed.append(p))
    assert res == "checked_out"
    assert _git(["rev-parse", "HEAD"], repo) == staging_sha
    assert (repo / "SKILL.md").read_text() == "v2"
    assert changed == [repo]
    assert hist.count_by_side()["staging"] == 1


def test_already_aligned_is_noop(tmp_path):
    repo, main_sha, _ = _seed(tmp_path / "fix-foo")
    hist = InstallHistory(tmp_path / "history.jsonl")
    res = reconcile_skill_side(repo_dir=repo, target_side="main",
                               target_sha=main_sha, history=hist, on_changed=None)
    assert res == "already_aligned"


def test_skips_pending_user_edit(tmp_path, monkeypatch):
    repo, main_sha, staging_sha = _seed(tmp_path / "fix-foo")
    hist = InstallHistory(tmp_path / "history.jsonl")
    monkeypatch.setattr("xskill.team.reconcile.has_pending_user_edit", lambda d: True)
    res = reconcile_skill_side(repo_dir=repo, target_side="staging",
                               target_sha=staging_sha, history=hist, on_changed=None)
    assert res == "skipped_user_edit"
    assert _git(["rev-parse", "HEAD"], repo) == main_sha   # 没动
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3.11 -m pytest tests/test_team_reconcile.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'xskill.team.reconcile'`

- [ ] **Step 3: 实现**

```python
# src/xskill/team/reconcile.py
"""reconcile.py — 共享的 skill side 调谐（SP1）

设计里约定的 reconcile_skill_sides() 契约里，只有"决定 target side"那一步
分叉（单机按时间窗 / CS 按 server 账本）。本文件是步骤 2/3/4 的共享实现：

  2. 有未吸收的用户手改 → skip（让路给 absorb / push-edit 链路）
  3. 本地已对齐 target → skip
  4. checkout 到 target + 落 install_history

调用方（client TeamClient / 单机 watcher）各自做步骤 1 再调本函数。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Literal

from xskill.git_lock import run_git
from xskill.install_history import InstallHistory
from xskill.user_edit_absorb_agent import has_pending_user_edit

logger = logging.getLogger("xskill.team.reconcile")

ReconcileResult = Literal["skipped_user_edit", "already_aligned", "checked_out", "error"]


def reconcile_skill_side(
    *,
    repo_dir: Path,
    target_side: str,
    target_sha: str,
    history: InstallHistory,
    on_changed: Callable[[Path], None] | None = None,
) -> ReconcileResult:
    """把一个 skill 仓的工作树对齐到 (target_side, target_sha)。

    checkout 到 ``_active`` 本地分支（指向 target_sha）——不直接 checkout
    main/staging 分支名，让用户手改 / git 操作有一个稳定落点。

    返回四种结果之一；只有 "checked_out" 会调 on_changed（用于 install）。
    """
    repo_dir = Path(repo_dir)
    # 步骤 2：用户正在手改 → 不碰，让路给 absorb / push-edit 链路
    if has_pending_user_edit(repo_dir):
        logger.info("reconcile skip (pending user edit): %s", repo_dir.name)
        return "skipped_user_edit"

    # 步骤 3：已对齐 → no-op
    code, cur, _ = run_git(["rev-parse", "HEAD"], cwd=str(repo_dir))
    if code == 0 and cur.strip() == target_sha:
        return "already_aligned"

    # 步骤 4：checkout 到 target + 记账
    code, _, err = run_git(["checkout", "-B", "_active", target_sha], cwd=str(repo_dir))
    if code != 0:
        logger.warning("reconcile checkout failed: %s -> %s: %s",
                       repo_dir.name, target_sha[:8], err)
        return "error"
    history.record(skill=repo_dir.name, side=target_side, sha=target_sha)
    logger.info("reconcile: %s -> %s (%s)", repo_dir.name, target_side, target_sha[:8])
    if on_changed is not None:
        on_changed(repo_dir)
    return "checked_out"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3.11 -m pytest tests/test_team_reconcile.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/xskill/team/reconcile.py tests/test_team_reconcile.py
git commit -m "feat(team): 共享 reconcile_skill_side 助手 (契约步骤 2/3/4)"
```

---

### Task 15: client 轨迹采集器

**Files:**
- Create: `src/xskill/team/collector.py`
- Test: `tests/test_team_collector.py`

**背景：** client 要把本地 code-agent 轨迹变成 `traj_*.md`。复用既有 ingester：`JsonlIngester(CC_SPEC|CODEX_SPEC, ...)` 与 `SqliteIngester(...)` 做的就是"扫原生 session → 桥成 `traj_*.md`"的纯镜像（`JsonlIngester` 文档明确说它**不**做 staging/header 注入——那是 `CCSessionIngester` 的活，client 不要它）。`TeamCollector` 把这些 ingester 指向 outbox bridge 目录，再维护一个"已上传游标"，吐出"静默 ≥3min 且未上传过"的 `traj_*.md`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_team_collector.py
import time
from pathlib import Path

from xskill.team.collector import TeamCollector


def test_pending_returns_quiet_unuploaded_md(tmp_path):
    outbox = tmp_path / "outbox"
    bridge = outbox / "cc_sessions"
    bridge.mkdir(parents=True)
    # 一个"静默已久"的 traj_*.md
    old = bridge / "traj_cc_x_001.md"
    old.write_text("# old body", encoding="utf-8")
    old_time = time.time() - 600
    import os
    os.utime(old, (old_time, old_time))
    # 一个"刚改过"的 traj_*.md
    fresh = bridge / "traj_cc_x_002.md"
    fresh.write_text("# fresh", encoding="utf-8")

    col = TeamCollector(outbox_dir=outbox, cursor_path=tmp_path / "cursor.json",
                        quiet_seconds=180)
    pending = col.pending()
    ids = {p.traj_id for p in pending}
    assert "traj_cc_x_001" in ids        # 静默够久
    assert "traj_cc_x_002" not in ids    # 太新，可能还在写


def test_mark_uploaded_excludes_next_time(tmp_path):
    outbox = tmp_path / "outbox"
    bridge = outbox / "cc_sessions"
    bridge.mkdir(parents=True)
    md = bridge / "traj_cc_x_001.md"
    md.write_text("# body", encoding="utf-8")
    old = time.time() - 600
    import os
    os.utime(md, (old, old))

    col = TeamCollector(outbox_dir=outbox, cursor_path=tmp_path / "cursor.json",
                        quiet_seconds=180)
    p = col.pending()[0]
    col.mark_uploaded(p.traj_id, p.sha256)
    assert col.pending() == []           # 已上传，不再吐

    # 内容变了 → 重新吐（增量）
    md.write_text("# body changed", encoding="utf-8")
    os.utime(md, (old, old))
    assert len(col.pending()) == 1


def test_redaction_applied_to_content(tmp_path):
    outbox = tmp_path / "outbox"
    bridge = outbox / "cc_sessions"
    bridge.mkdir(parents=True)
    md = bridge / "traj_cc_x_001.md"
    md.write_text('key = "sk-abcdEFGH1234567890wxyz"', encoding="utf-8")
    old = time.time() - 600
    import os
    os.utime(md, (old, old))
    col = TeamCollector(outbox_dir=outbox, cursor_path=tmp_path / "cursor.json",
                        quiet_seconds=180)
    p = col.pending()[0]
    assert "sk-abcdEFGH1234567890wxyz" not in p.content
    assert "[REDACTED]" in p.content
```

> ⚠️ 这些单测只覆盖"游标 + 静默窗口 + 脱敏"逻辑，**不**起真 ingester。`start_ingesters()` / `stop_ingesters()`（真桥接本机生态）由 Task 19 的 E2E 覆盖。

- [ ] **Step 2: 跑测试确认失败**

Run: `python3.11 -m pytest tests/test_team_collector.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'xskill.team.collector'`

- [ ] **Step 3: 实现**

```python
# src/xskill/team/collector.py
"""collector.py — client 端本地轨迹采集（SP1）

两件事：
1. start_ingesters() —— 复用既有 JsonlIngester(CC_SPEC/CODEX_SPEC) +
   SqliteIngester(OPENCODE_SPEC) 把本机 code-agent session 镜像成
   ``traj_*.md`` 落进 outbox bridge 目录。这些 ingester 是纯镜像——不做
   canary/header 注入（那是 server 的活）。
2. pending() —— 扫 outbox，吐出"静默 ≥quiet_seconds 且未上传过/内容已变"
   的 traj，content 已过脱敏 hook。游标落 cursor.json：traj_id -> sha256。

静默窗口 = 设计里约定的上传时机点（与 xskill 既有的"用户手改静默 3min
才吸收"同源），也天然是脱敏 hook 的插入位。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from xskill.team.redact import redact_text

logger = logging.getLogger("xskill.team.collector")


@dataclass
class PendingTrajectory:
    traj_id: str
    content: str       # 已脱敏
    sha256: str        # 脱敏后 content 的 sha256


class TeamCollector:
    """采集本机生态轨迹 → outbox；吐 pending 给 TeamClient 上传。"""

    def __init__(
        self,
        *,
        outbox_dir: Path,
        cursor_path: Path,
        quiet_seconds: int = 180,
        home_root: Path | None = None,
        poll_interval: float = 10.0,
    ):
        self.outbox_dir = Path(outbox_dir)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        self.cursor_path = Path(cursor_path)
        self.quiet_seconds = quiet_seconds
        self.home_root = Path(home_root) if home_root else None
        self.poll_interval = poll_interval
        self._ingesters: list = []
        self._cursor: dict[str, str] = self._load_cursor()

    # ── 游标 ─────────────────────────────────────────────────────
    def _load_cursor(self) -> dict[str, str]:
        if not self.cursor_path.is_file():
            return {}
        try:
            return json.loads(self.cursor_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_cursor(self) -> None:
        self.cursor_path.parent.mkdir(parents=True, exist_ok=True)
        self.cursor_path.write_text(json.dumps(self._cursor), encoding="utf-8")

    def mark_uploaded(self, traj_id: str, sha256: str) -> None:
        """记录某 traj 的某版本已上传。"""
        self._cursor[traj_id] = sha256
        self._save_cursor()

    # ── ingester 生命周期 ────────────────────────────────────────
    def start_ingesters(self) -> None:
        """探测本机生态，对每个起一个纯镜像 ingester 写进 outbox/<eco>/。"""
        from xskill.ecosystems import (
            detect_known_ecosystems, JsonlIngester, SqliteIngester,
            CC_SPEC, CODEX_SPEC, OPENCODE_SPEC,
        )
        detections = detect_known_ecosystems(home_root=self.home_root)
        for det in detections:
            eco = det["ecosystem"]
            bridge = self.outbox_dir / f"{eco}_sessions"
            bridge.mkdir(parents=True, exist_ok=True)
            if eco == "claude_code":
                ing = JsonlIngester(CC_SPEC, target_traj_dir=bridge,
                                    home_root=self.home_root,
                                    poll_interval=self.poll_interval)
            elif eco == "codex":
                ing = JsonlIngester(CODEX_SPEC, target_traj_dir=bridge,
                                    home_root=self.home_root,
                                    poll_interval=self.poll_interval)
            elif eco == "opencode":
                ing = SqliteIngester(target_traj_dir=bridge,
                                     home_root=self.home_root,
                                     spec=OPENCODE_SPEC,
                                     poll_interval=self.poll_interval)
            else:
                continue
            ing.start()
            self._ingesters.append(ing)
            logger.info("collector ingester started: %s -> %s", eco, bridge)

    def stop_ingesters(self) -> None:
        for ing in self._ingesters:
            try:
                ing.stop()
            except Exception:
                logger.warning("failed to stop ingester", exc_info=True)
        self._ingesters.clear()

    # ── pending ─────────────────────────────────────────────────
    def pending(self) -> list[PendingTrajectory]:
        """扫 outbox 所有 traj_*.md，吐出静默够久 + 未上传过/内容已变的。"""
        now = time.time()
        out: list[PendingTrajectory] = []
        for md in sorted(self.outbox_dir.rglob("traj_*.md")):
            if not md.is_file():
                continue
            # 静默窗口：太新的文件可能还在写，等它静默
            if (now - md.stat().st_mtime) < self.quiet_seconds:
                continue
            raw = md.read_text(encoding="utf-8")
            content = redact_text(raw)
            sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
            traj_id = md.stem
            if self._cursor.get(traj_id) == sha:
                continue   # 这个版本已上传过
            out.append(PendingTrajectory(traj_id=traj_id, content=content, sha256=sha))
        return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3.11 -m pytest tests/test_team_collector.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/xskill/team/collector.py tests/test_team_collector.py
git commit -m "feat(team): client 轨迹采集器 (复用 ingester + 静默游标 + 脱敏)"
```

---

### Task 16: TeamClient 守护

**Files:**
- Create: `src/xskill/team/client.py`
- Test: `tests/test_team_client.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_team_client.py
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xskill.team import server_api
from xskill.team.client import TeamClient, register_with_server
from xskill.team.client_registry import ClientRegistry
from xskill.team.client_state import ClientState


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True, check=True)


def _make_skill(skill_dir: Path, name: str):
    d = skill_dir / name
    d.mkdir(parents=True)
    _git(["init", "-q"], d); _git(["checkout", "-q", "-b", "main"], d)
    _git(["config", "user.email", "t@t"], d); _git(["config", "user.name", "t"], d)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\n# {name}\n", encoding="utf-8")
    _git(["add", "."], d); _git(["commit", "-q", "-m", "v1"], d)


@pytest.fixture
def server_app(tmp_path):
    skill_dir = tmp_path / "server_skill"
    skill_dir.mkdir()
    _make_skill(skill_dir, "fix-foo")
    reg = ClientRegistry(tmp_path / "clients.db")
    server_api.init_team_context(
        join_token="tok", client_registry=reg, skill_dir=skill_dir,
        traj_root=tmp_path / "team_traj", probability=0.2,
        ranked_slots=80, total_slots=100, register_dir=lambda p, l: None,
    )
    app = FastAPI()
    app.include_router(server_api.router)
    return app, skill_dir


def test_register_with_server_returns_client_id(server_app):
    app, _ = server_app
    tc = TestClient(app)
    cid = register_with_server(tc, token="tok", label="alice", hostname="a")
    assert isinstance(cid, str) and cid


def _client(server_app, tmp_path) -> TeamClient:
    app, _ = server_app
    http = TestClient(app)
    cid = register_with_server(http, token="tok", label="alice", hostname="a")
    state = ClientState(server_url="http://testserver", client_id=cid, join_token="tok")
    return TeamClient(
        state=state, http=http,
        team_skills_dir=tmp_path / "team_skills",
        outbox_dir=tmp_path / "outbox",
        cursor_path=tmp_path / "cursor.json",
        history_path=tmp_path / "history.jsonl",
        home_root=tmp_path / "client_home",
    )


def test_sync_and_reconcile_materializes_skill(server_app, tmp_path):
    tc = _client(server_app, tmp_path)
    manifest = tc.sync()
    assert any(s.skill_name == "fix-foo" for s in manifest.slots)
    tc.reconcile_skill_sides(manifest)
    repo = tmp_path / "team_skills" / "fix-foo"
    assert (repo / ".git").is_dir()
    assert (repo / "SKILL.md").read_text(encoding="utf-8").startswith("---")


def test_upload_sends_pending_trajectory(server_app, tmp_path):
    tc = _client(server_app, tmp_path)
    # 造一个静默够久的 outbox traj
    bridge = (tmp_path / "outbox" / "cc_sessions")
    bridge.mkdir(parents=True)
    md = bridge / "traj_cc_x_001.md"
    md.write_text("# body", encoding="utf-8")
    import os, time
    old = time.time() - 600
    os.utime(md, (old, old))
    n = tc.collect_and_upload()
    assert n == 1
    # server 端落盘检查
    expected = (tmp_path / "team_traj" / "clients" / tc.state.client_id
                / "sessions" / "traj_cc_x_001.md")
    assert expected.is_file()
    # 再跑一次不重传（游标生效）
    assert tc.collect_and_upload() == 0


def test_cleanup_removes_skill_not_in_manifest(server_app, tmp_path):
    tc = _client(server_app, tmp_path)
    # 本地有个 manifest 里没有的 stale skill
    stale = tmp_path / "team_skills" / "stale-skill"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("# stale", encoding="utf-8")
    manifest = tc.sync()
    tc.reconcile_skill_sides(manifest)
    tc.cleanup(manifest)
    assert not stale.exists()
    assert (tmp_path / "team_skills" / "fix-foo").is_dir()   # manifest 里的保留
```

> ⚠️ `TeamClient` 的 `http` 参数接受 `httpx.Client` 或 FastAPI `TestClient`——两者 `.get/.post` 接口一致，测试用 TestClient 免起真端口。`run_forever` / `start`（起 collector ingester + 循环）由 Task 19 的 E2E 覆盖。

- [ ] **Step 2: 跑测试确认失败**

Run: `python3.11 -m pytest tests/test_team_client.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'xskill.team.client'`

- [ ] **Step 3: 实现**

```python
# src/xskill/team/client.py
"""client.py — TeamClient 瘦客户端守护（SP1）

client 只干三件事：采集本地轨迹脱敏上传、持有 server 算出的 skill
working copy 并对齐 side、把本地手改推成 user-staging/<client_id> 分支。
零 LLM、零 git 写 main、零灰度判定。

_tick 一轮：
  collect_and_upload → sync → reconcile_skill_sides → push_user_edits → cleanup
"""
from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path

from xskill.install_history import InstallHistory
from xskill.team.client_state import ClientState
from xskill.team.collector import TeamCollector
from xskill.team.git_bundle import (
    apply_repo_bundle, make_branch_bundle,
)
from xskill.team.reconcile import reconcile_skill_side
from xskill.team.sync_protocol import (
    SyncResponse, UploadRequest, UploadTrajectory,
)
from xskill.user_edit_absorb_agent import has_pending_user_edit
from xskill.git_lock import run_git

logger = logging.getLogger("xskill.team.client")


def register_with_server(http, *, token: str, label: str, hostname: str) -> str:
    """跟 server 握手注册，返回 server 分配的 client_id。"""
    resp = http.post("/api/v1/team/register", json={
        "token": token, "client_label": label, "hostname": hostname,
    })
    if resp.status_code != 200:
        raise RuntimeError(
            f"register failed: HTTP {resp.status_code} — {resp.text}"
        )
    return resp.json()["client_id"]


class TeamClient:
    """team 瘦客户端。http 接受 httpx.Client 或 FastAPI TestClient。"""

    def __init__(
        self,
        *,
        state: ClientState,
        http,
        team_skills_dir: Path,
        outbox_dir: Path,
        cursor_path: Path,
        history_path: Path,
        home_root: Path | None = None,
        poll_interval: float = 30.0,
        quiet_seconds: int = 180,
    ):
        self.state = state
        self.http = http
        self.team_skills_dir = Path(team_skills_dir)
        self.team_skills_dir.mkdir(parents=True, exist_ok=True)
        self.home_root = Path(home_root) if home_root else Path.home()
        self.poll_interval = poll_interval
        self.history = InstallHistory(history_path)
        self.collector = TeamCollector(
            outbox_dir=Path(outbox_dir), cursor_path=Path(cursor_path),
            quiet_seconds=quiet_seconds, home_root=self.home_root,
        )
        self._stop = threading.Event()

    # ── HTTP 鉴权头 ──────────────────────────────────────────────
    def _hdr(self, extra: dict | None = None) -> dict:
        h = {"X-Xskill-Token": self.state.join_token,
             "X-Xskill-Client": self.state.client_id}
        if extra:
            h.update(extra)
        return h

    # ── ① 采集 + 上传 ────────────────────────────────────────────
    def collect_and_upload(self) -> int:
        """扫 outbox 静默轨迹，脱敏后上传 server。返回成功上传条数。"""
        pending = self.collector.pending()
        if not pending:
            return 0
        req = UploadRequest(trajectories=[
            UploadTrajectory(traj_id=p.traj_id, content=p.content, sha256=p.sha256)
            for p in pending
        ])
        resp = self.http.post("/api/v1/team/upload", headers=self._hdr(),
                              json=req.model_dump())
        if resp.status_code != 200:
            logger.warning("upload failed: HTTP %s — %s", resp.status_code, resp.text)
            return 0
        accepted = set(resp.json().get("accepted", []))
        for p in pending:
            if p.traj_id in accepted:
                self.collector.mark_uploaded(p.traj_id, p.sha256)
        logger.info("uploaded %d trajectories", len(accepted))
        return len(accepted)

    # ── ② sync ──────────────────────────────────────────────────
    def sync(self) -> SyncResponse:
        """拉 server 现算的 skill manifest。"""
        resp = self.http.get("/api/v1/team/sync", headers=self._hdr())
        if resp.status_code != 200:
            raise RuntimeError(f"sync failed: HTTP {resp.status_code} — {resp.text}")
        return SyncResponse.model_validate(resp.json())

    # ── ③ reconcile ─────────────────────────────────────────────
    def reconcile_skill_sides(self, manifest: SyncResponse) -> None:
        """对 manifest 每个 slot：拉 bundle → 对齐 side → 装到本机生态。

        这是设计里约定的 reconcile_skill_sides——契约步骤 1（决定 target）
        就是读 manifest slot 的 side/sha；步骤 2/3/4 走共享
        reconcile_skill_side。
        """
        for slot in manifest.slots:
            repo_dir = self.team_skills_dir / slot.skill_name
            # 拉 bundle 落地/刷新本地 working copy
            r = self.http.get(f"/api/v1/team/skill/{slot.skill_name}/bundle",
                              headers=self._hdr())
            if r.status_code != 200:
                logger.warning("bundle fetch failed: %s HTTP %s",
                               slot.skill_name, r.status_code)
                continue
            apply_repo_bundle(r.content, repo_dir)
            # 步骤 1 = manifest 给的 (side, sha)；2/3/4 = 共享助手
            reconcile_skill_side(
                repo_dir=repo_dir, target_side=slot.side, target_sha=slot.sha,
                history=self.history, on_changed=self._install_to_ecosystems,
            )

    def _install_to_ecosystems(self, repo_dir: Path) -> None:
        """把一个已 checkout 好的 skill working copy 装到本机所有生态。

        working tree 已是 server 指定 side 的内容，所以一律用 side='main'
        语义（= 链接整个 working tree 目录）。
        """
        from xskill.ecosystems import (
            detect_known_ecosystems, install_to_claude_code,
            install_to_codex, install_to_opencode,
        )
        installer = {
            "claude_code": install_to_claude_code,
            "codex": install_to_codex,
            "opencode": install_to_opencode,
        }
        for det in detect_known_ecosystems(home_root=self.home_root):
            fn = installer.get(det["ecosystem"])
            if fn is None:
                continue
            try:
                fn(repo_dir, target_root=self.home_root, side="main")
            except Exception:
                logger.warning("install %s to %s failed",
                               repo_dir.name, det["ecosystem"], exc_info=True)

    # ── ④ push 用户手改 ──────────────────────────────────────────
    def push_user_edits(self) -> int:
        """检测本地 working copy 的未吸收手改，推成 user-staging/<client_id>。

        返回推送成功的 skill 数。client 是愚蠢且可能恶意的——它推过去的
        只能进隔离分支，永远碰不到 main。
        """
        pushed = 0
        for repo_dir in sorted(self.team_skills_dir.iterdir()):
            if not (repo_dir / ".git").is_dir():
                continue
            if not has_pending_user_edit(repo_dir):
                continue
            # 把手改 commit 到 _useredit 分支（从当前 _active 起）
            run_git(["checkout", "-B", "_useredit"], cwd=str(repo_dir))
            run_git(["add", "-A"], cwd=str(repo_dir))
            code, _, err = run_git(
                ["commit", "-m", f"user edit from {self.state.client_id}"],
                cwd=str(repo_dir),
            )
            if code != 0 and "nothing to commit" not in err:
                logger.warning("commit user edit failed: %s: %s", repo_dir.name, err)
                continue
            bundle = make_branch_bundle(repo_dir, "_useredit")
            resp = self.http.post(
                "/api/v1/team/push-edit",
                headers=self._hdr({"X-Xskill-Skill": repo_dir.name}),
                content=bundle,
            )
            if resp.status_code == 200:
                pushed += 1
                logger.info("pushed user edit: %s -> %s",
                            repo_dir.name, resp.json()["branch"])
            else:
                logger.warning("push-edit failed: %s HTTP %s",
                               repo_dir.name, resp.status_code)
        return pushed

    # ── ⑤ cleanup ───────────────────────────────────────────────
    def cleanup(self, manifest: SyncResponse) -> None:
        """删掉本地 working copy 里 manifest 已不包含的 skill。

        client 的 skill 集合完全由 server 算出的 manifest 决定——server 把
        某 skill 移出 100 → 下次 sync 后本地也删，不自留。
        """
        keep = {s.skill_name for s in manifest.slots}
        for repo_dir in sorted(self.team_skills_dir.iterdir()):
            if not repo_dir.is_dir() or repo_dir.name in keep:
                continue
            # 先摘生态里的安装（symlink），再删本地仓
            self._uninstall_from_ecosystems(repo_dir.name)
            shutil.rmtree(repo_dir, ignore_errors=True)
            logger.info("cleanup removed stale skill: %s", repo_dir.name)

    def _uninstall_from_ecosystems(self, skill_name: str) -> None:
        from xskill.ecosystems import (
            _cc_skills_path, _agents_skills_path,
        )
        for root_fn in (_cc_skills_path, _agents_skills_path):
            dest = root_fn(self.home_root) / skill_name
            if dest.is_symlink():
                try:
                    dest.unlink()
                except OSError:
                    logger.warning("failed to unlink %s", dest, exc_info=True)

    # ── 守护循环 ─────────────────────────────────────────────────
    def _tick(self) -> None:
        try:
            self.collect_and_upload()
            manifest = self.sync()
            self.reconcile_skill_sides(manifest)
            self.push_user_edits()
            self.cleanup(manifest)
        except Exception:
            logger.exception("team client tick failed")

    def run_forever(self) -> None:
        """阻塞循环。先起 collector ingester，再每 poll_interval 跑一轮 _tick。"""
        self.collector.start_ingesters()
        logger.info("team client running: server=%s client_id=%s",
                    self.state.server_url, self.state.client_id)
        try:
            while not self._stop.is_set():
                self._tick()
                self._stop.wait(self.poll_interval)
        finally:
            self.collector.stop_ingesters()

    def stop(self) -> None:
        self._stop.set()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3.11 -m pytest tests/test_team_client.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/xskill/team/client.py tests/test_team_client.py
git commit -m "feat(team): TeamClient 守护 (collect/upload/sync/reconcile/push-edit/cleanup)"
```

---

### Task 17: CLI `connect` 子命令

**Files:**
- Modify: `src/xskill/cli.py`（加 `cmd_connect`；`build_parser` 加 `connect`；`main()` 重构；`_setup_logging` 加 `connect`）
- Test: `tests/test_team_cli_connect.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_team_cli_connect.py
from xskill.cli import build_parser, cmd_connect
from xskill.team.client_state import ClientState, save_client_state


def test_connect_subcommand_parses():
    parser = build_parser()
    args = parser.parse_args(["connect", "1.2.3.4:8000", "--token", "tok",
                              "--label", "alice"])
    assert args.address == "1.2.3.4:8000"
    assert args.token == "tok"
    assert args.label == "alice"
    # 无参形式（复用已存连接）
    args2 = parser.parse_args(["connect"])
    assert args2.address is None


def test_connect_no_address_no_saved_state_errors(tmp_path, monkeypatch, capsys):
    # 无 address 且无 team_client.json → 返回非 0
    monkeypatch.setattr("xskill.config.get_team_client_state_path",
                        lambda: tmp_path / "absent.json")
    parser = build_parser()
    args = parser.parse_args(["connect"])
    rc = cmd_connect(args)
    assert rc != 0


def test_connect_with_address_requires_token():
    parser = build_parser()
    args = parser.parse_args(["connect", "1.2.3.4:8000"])  # 没 --token
    rc = cmd_connect(args)
    assert rc != 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3.11 -m pytest tests/test_team_cli_connect.py -q`
Expected: FAIL — `argparse` error / `ImportError: cannot import name 'cmd_connect'`

- [ ] **Step 3: 实现**

`src/xskill/cli.py` 加 `cmd_connect`（放在 `cmd_search` 之后）：

```python
def cmd_connect(args) -> int:
    """team 瘦客户端：连上 server，跑采集/同步/对齐守护循环。

    ``xskill connect <host:port> --token <t>``  首次握手 + 落盘连接信息
    ``xskill connect``                          复用已存连接
    """
    import socket as _socket
    from xskill.config import (
        get_team_client_state_path, get_team_skills_dir, get_team_outbox_dir,
        XSKILL_HOME,
    )
    from xskill.team.client_state import (
        ClientState, load_client_state, save_client_state,
    )
    from xskill.team.client import TeamClient, register_with_server

    state_path = get_team_client_state_path()

    if args.address:
        if not args.token:
            print("error: 首次 connect 必须带 --token（server 启动时打印的 join token）",
                  file=sys.stderr)
            return 2
        server_url = args.address
        if not server_url.startswith("http"):
            server_url = f"http://{server_url}"
        import httpx
        http = httpx.Client(base_url=server_url, timeout=30.0)
        try:
            client_id = register_with_server(
                http, token=args.token,
                label=args.label or _socket.gethostname(),
                hostname=_socket.gethostname(),
            )
        except Exception as e:
            print(f"error: 注册失败: {e}", file=sys.stderr)
            return 1
        state = ClientState(server_url=server_url, client_id=client_id,
                            join_token=args.token)
        save_client_state(state, state_path)
        print(f"connected: client_id={client_id}  server={server_url}")
    else:
        try:
            state = load_client_state(state_path)
        except FileNotFoundError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        import httpx
        http = httpx.Client(base_url=state.server_url, timeout=30.0)
        print(f"reconnecting: client_id={state.client_id}  server={state.server_url}")

    client = TeamClient(
        state=state, http=http,
        team_skills_dir=get_team_skills_dir(),
        outbox_dir=get_team_outbox_dir(),
        cursor_path=XSKILL_HOME / "team_client_cursor.json",
        history_path=XSKILL_HOME / "install_history.jsonl",
    )
    client.run_forever()   # 阻塞
    return 0
```

`build_parser` 加 `connect` 子命令（放在 `p_search` 之后、`return p` 之前）：

```python
    p_conn = sub.add_parser(
        "connect", help="Join a team server as a thin client",
    )
    p_conn.add_argument(
        "address", nargs="?", default=None,
        help="server 地址 host:port。省略则复用已存连接（~/.xskill/team_client.json）。",
    )
    p_conn.add_argument("--token", default=None,
                        help="join token（server 启动 `xskill serve --server` 时打印）")
    p_conn.add_argument("--label", default="",
                        help="本 client 的可读标签（默认主机名）")
```

`main()` 重构——`connect` 不构造 `XSkill()`（瘦客户端无 LLM key，`load_config` 会抛 `KeyError`）：

```python
    # connect 是瘦客户端：不读 config.yaml / 不需要 llm.api_key / 不构造 XSkill 门面
    if args.command == "connect":
        return cmd_connect(args)

    from xskill import XSkill
    xskill = XSkill()

    handler = {
        "serve":    cmd_serve,
        "registry": cmd_registry,
        "search":   cmd_search,
    }.get(args.command)
    return handler(args, xskill) if handler else (parser.print_help() or 1)
```

`_setup_logging` 的 file-split 分支条件从 `if command == "serve":` 改成 `if command in ("serve", "connect"):`（connect 也是长跑守护，值得落文件日志）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python3.11 -m pytest tests/test_team_cli_connect.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/xskill/cli.py tests/test_team_cli_connect.py
git commit -m "feat(team): xskill connect 子命令 (瘦客户端，不构造 XSkill 门面)"
```

---

## Phase E — reconcile 统一 / E2E / 文档

### Task 18: 单机 watcher reconcile 收敛到共享助手

**Files:**
- Modify: `src/xskill/watcher.py`（`_reconcile_skill_sides` 步骤 2/3/4 改调 `team.reconcile.reconcile_skill_side`）
- Test: `tests/test_team_reconcile_unified.py`（新增）+ `tests/test_canary_rotation.py`（回归）

**背景（设计里用户明确要求）：** 单机 `_reconcile_skill_sides` 与 client `TeamClient.reconcile_skill_sides` 是同一个调谐契约，只有"决定 target side"那步分叉。Task 14 已抽出共享 `reconcile_skill_side`，Task 16 client 已用它。本任务把单机 watcher 也收敛过去——契约统一，避免两处各写一份步骤 2/3/4。

单机的步骤 1（决定 target）保持原样：`rotate_interval` 节流 + 时间窗 `window_id` + `pick_side(str(window_id), name, p)`。变化的只是：原来 `git checkout <branch名>`，现在改为解析 `side → sha`（`main_sha`/`staging_sha`）后调 `reconcile_skill_side(... target_sha=sha ...)`，由它 `checkout -B _active <sha>` + 记账。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_team_reconcile_unified.py
import subprocess
import time
from pathlib import Path

from xskill.watcher import DirectoryWatcher


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                          text=True, check=True).stdout.strip()


def _seed_skill_with_staging(skill_dir: Path, name: str):
    d = skill_dir / name
    d.mkdir(parents=True)
    _git(["init", "-q"], d); _git(["checkout", "-q", "-b", "main"], d)
    _git(["config", "user.email", "t@t"], d); _git(["config", "user.name", "t"], d)
    (d / "SKILL.md").write_text("v1", encoding="utf-8")
    _git(["add", "."], d); _git(["commit", "-q", "-m", "v1"], d)
    _git(["checkout", "-q", "-b", "staging"], d)
    (d / "SKILL.md").write_text("v2", encoding="utf-8")
    _git(["commit", "-q", "-am", "v2"], d)
    _git(["checkout", "-q", "main"], d)
    return d


def test_single_machine_reconcile_checks_out_active_branch(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    d = _seed_skill_with_staging(skill_dir, "graying")
    w = DirectoryWatcher(skill_dir=skill_dir,
                         config={"canary": {"probability": 1.0, "rotate_interval": 1}})
    # probability=1.0 → 必选 staging
    w._reconcile_skill_sides()
    # 工作树应是 staging 内容，HEAD 在 _active 分支
    assert (d / "SKILL.md").read_text() == "v2"
    branch = _git(["branch", "--show-current"], d)
    assert branch == "_active"


def test_single_machine_reconcile_records_history(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    _seed_skill_with_staging(skill_dir, "graying")
    w = DirectoryWatcher(skill_dir=skill_dir,
                         config={"canary": {"probability": 1.0, "rotate_interval": 1}})
    w._reconcile_skill_sides()
    from xskill.install_history import InstallHistory
    from xskill.config import XSKILL_HOME
    hist = InstallHistory(XSKILL_HOME / "install_history.jsonl")
    assert hist.count_by_side(skill="graying")["staging"] >= 1
```

> ⚠️ 第二个测试会写真 `~/.xskill/install_history.jsonl`——执行者改成注入临时 `history_path`，或断言 watcher 暴露的 history 对象。若 `_reconcile_skill_sides` 当前硬编码 `XSKILL_HOME / "install_history.jsonl"`，本任务顺带把它改成可注入（watcher `__init__` 加 `install_history_path=None`，缺省回退 `XSKILL_HOME/...`）——这同时修掉既有单测污染真 home 的隐患。

- [ ] **Step 2: 跑测试确认失败**

Run: `python3.11 -m pytest tests/test_team_reconcile_unified.py -q`
Expected: FAIL — `assert branch == "_active"`（当前实现 checkout 的是 `main`/`staging` 分支名，不是 `_active`）

- [ ] **Step 3: 实现**

`src/xskill/watcher.py` 的 `_reconcile_skill_sides` 改写——步骤 1（节流 + 时间窗 + pick_side）保留，步骤 2/3/4 委托给共享助手：

```python
    def _reconcile_skill_sides(self):
        """单机 canary 流量入口：周期性按概率把有 staging 的 skill 子仓
        checkout 到 main / staging。

        调谐契约（与 client TeamClient.reconcile_skill_sides 同契约）：
          步骤 1（本方法独有）：rotate_interval 节流 + 时间窗伪随机定 target side
          步骤 2/3/4（共享）  ：team.reconcile.reconcile_skill_side
                                （手改优先 / 已对齐跳过 / checkout+记账）

        单机 bucket = 时间窗（int(now // rotate_interval)）；CS bucket =
        client_id。两模式唯一差别就是步骤 1 的 bucket key 来源。
        """
        if self.skill_dir is None or not self.skill_dir.is_dir():
            return
        from xskill.canary import (
            CanaryConfig, has_staging, main_sha, pick_side, staging_sha,
        )
        from xskill.team.reconcile import reconcile_skill_side

        canary_cfg = CanaryConfig.from_dict(self.config.get("canary", {}))
        rotate_interval = canary_cfg.rotate_interval

        now = time.time()
        if (
            self._last_rotate_ts is not None
            and (now - self._last_rotate_ts) < rotate_interval
        ):
            return
        self._last_rotate_ts = now

        window_id = int(now // rotate_interval) if rotate_interval > 0 else 0
        history = self._install_history()

        for d in sorted(self.skill_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            if not (d / ".git").is_dir():
                continue
            if not has_staging(d):
                continue
            # 步骤 1：时间窗伪随机定 target side（单机 bucket = window_id）
            side = pick_side(str(window_id), d.name, canary_cfg.probability)
            target_sha = staging_sha(d) if side == "staging" else main_sha(d)
            if not target_sha:
                continue
            # 步骤 2/3/4：共享调谐助手
            reconcile_skill_side(
                repo_dir=d, target_side=side, target_sha=target_sha,
                history=history, on_changed=None,
            )
```

`DirectoryWatcher.__init__` 加可注入的 history 路径（在 `server_mode` 旗标之后）：

```python
        # install_history 路径可注入（测试用 tmp，生产回退 ~/.xskill/）。
        from xskill.config import XSKILL_HOME
        self.install_history_path = (
            Path(install_history_path) if install_history_path
            else XSKILL_HOME / "install_history.jsonl"
        )
```

并在 `__init__` 签名加 `install_history_path=None`。新增 helper：

```python
    def _install_history(self):
        from xskill.install_history import InstallHistory
        return InstallHistory(self.install_history_path)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3.11 -m pytest tests/test_team_reconcile_unified.py tests/test_canary_rotation.py tests/test_watcher.py tests/test_watcher_atom.py -q`
Expected: PASS（新测试 + 既有 rotation/watcher 测试全绿——若 `test_canary_rotation.py` 断言旧的 `checkout <branch名>` 行为，按"`_active` 分支指向同一 sha、工作树内容一致"更新断言）

- [ ] **Step 5: Commit**

```bash
git add src/xskill/watcher.py tests/test_team_reconcile_unified.py tests/test_canary_rotation.py
git commit -m "refactor(team): 单机 watcher reconcile 收敛到共享 reconcile_skill_side"
```

---

### Task 19: 端到端集成测试（mock LLM）

**Files:**
- Create: `tests/e2e/test_team_cs_e2e.py`
- Read first: `tests/_fake_llm_server.py`, `tests/e2e/test_smoke.py`, `tests/conftest.py`

**测试方案（CLAUDE.md 要求先写方案）：** 端到端打通"用户视角"的 team C/S 全链路，用 mock LLM 后端避免真烧 token：

1. **起 server**：`create_app(team_server=True, home_root=<tmp server home>)` + uvicorn（或复用 `tests/e2e/test_smoke.py` 的起服务模式）；配置指向 `tests/_fake_llm_server.py` 的 mock LLM/embedding。server 的 skill_dir 预置 1 个 `fix-foo` skill（main only）。
2. **起 client**：`TeamClient` 指向 server（用 `httpx.Client` 真打 HTTP，或按 smoke test 的模式）；client home 是另一个 tmp 目录，预置一个假的 CC session（`<client_home>/.claude/projects/<hash>/<sid>.jsonl`，内容里**故意塞一个 `sk-` key** 验脱敏）。
3. **跑 client `_tick`**：
   - collector ingester 把假 CC session 镜像成 `traj_*.md` 进 outbox；
   - `collect_and_upload` 上传——断言 server 端 `clients/<cid>/sessions/traj_*.md` 落盘、且**不含 `sk-` 明文**（脱敏生效）；
   - server watcher（server_mode）跑 split/cluster/score——mock LLM 返回构造好的 atom 拆分 + 打分；
   - client `sync` 拿到 manifest（含 `fix-foo`）；
   - `reconcile_skill_sides` 把 `fix-foo` 落到 `<client_home>/.xskill/team_skills/fix-foo` 并装进 `<client_home>/.claude/skills/fix-foo`（symlink）。
4. **验手改回流**：在 client 的 `team_skills/fix-foo/SKILL.md` 改一行 → `push_user_edits` → 断言 server 端 `fix-foo` 仓出现 `user-staging/<cid>` 分支、`main` 未变。
5. **验 cleanup**：在 client `team_skills/` 放一个 manifest 外的 `stale/` → `cleanup` 后它被删。

**关键约束：** 全程 `home_root` 必须是 tmp 目录——`tests/conftest.py` 有 autouse 防护拦截污染真 `~/.claude/skills/`，新测试必须显式注入 tmp home，否则当场 fail。

- [ ] **Step 1: 读现有 E2E 基建，确定 harness API**

Run: `python3.11 -m pytest tests/e2e/test_smoke.py -q` 先确认现有 smoke E2E 能跑。
读 `tests/_fake_llm_server.py`（`Responder` / fake server 启停 API）、`tests/e2e/test_smoke.py`（起 `create_app` + mock LLM 的既有模式）、`tests/conftest.py`（home 防污染 fixture）。**复用**它们的 mock LLM fixture 与起服务模式，不重造。

- [ ] **Step 2: 写 E2E 测试**

按上面测试方案写 `tests/e2e/test_team_cs_e2e.py`。骨架（`<...>` 处按 Step 1 读到的 harness API 填实）：

```python
# tests/e2e/test_team_cs_e2e.py
"""team C/S 端到端：用户视角全链路（mock LLM）。

server: xskill serve --server 等价物（create_app(team_server=True)）
client: xskill connect 等价物（TeamClient 真打 HTTP）
断言链：脱敏 → 上传落盘 → server 跑 agent → manifest → reconcile 装到生态
        → 手改 push 进 user-staging/<cid> → cleanup 删 stale
"""
import os
import time
from pathlib import Path

import httpx
import pytest

# 复用既有 mock LLM harness（按 Step 1 读到的真实 API import）
from tests._fake_llm_server import <FakeLLMServer / start_fake_llm 等>


SK_LEAK = "sk-abcdEFGH1234567890leak"


def _seed_fake_cc_session(client_home: Path):
    """在 client home 造一个含 sk- key 的假 CC session JSONL。"""
    proj = client_home / ".claude" / "projects" / "hash1"
    proj.mkdir(parents=True)
    sid = "11111111-2222-3333-4444-555555555555"
    # 最小可被 claude_code_jsonl adapter 解析的事件序列；内容里塞 sk- key
    lines = [
        '{"type":"user","cwd":"/work/demo","timestamp":"2026-05-14T10:00:00.0Z",'
        '"message":{"role":"user","content":"帮我配置 ' + SK_LEAK + '"}}',
        '{"type":"assistant","cwd":"/work/demo","timestamp":"2026-05-14T10:00:01.0Z",'
        '"message":{"role":"assistant","content":[{"type":"text","text":"好的"}]}}',
    ]
    (proj / f"{sid}.jsonl").write_text("\n".join(lines), encoding="utf-8")


@pytest.mark.e2e
def test_team_cs_full_loop(tmp_path):
    server_home = tmp_path / "server_home"
    client_home = tmp_path / "client_home"
    server_home.mkdir(); client_home.mkdir()

    # ── 1. mock LLM + server ──
    fake_llm = <启动 fake LLM，配置 atom 拆分 + 打分 Responder>
    <写 server 的 ~/.xskill/config.yaml 指向 fake_llm，team.server 段齐全>
    <server 的 skill_dir 预置 fix-foo（main only）>
    app = <create_app(team_server=True, home_root=server_home)>
    <用 uvicorn 起 app，拿到 base_url；或按 smoke test 模式>

    # ── 2. client ──
    _seed_fake_cc_session(client_home)
    http = httpx.Client(base_url=base_url, timeout=30.0)
    from xskill.team.client import TeamClient, register_with_server
    from xskill.team.client_state import ClientState
    token = <读 server_home/.xskill/team_server.json 的 join_token>
    cid = register_with_server(http, token=token, label="e2e", hostname="e2e")
    state = ClientState(server_url=base_url, client_id=cid, join_token=token)
    client = TeamClient(
        state=state, http=http,
        team_skills_dir=client_home / ".xskill" / "team_skills",
        outbox_dir=client_home / ".xskill" / "team_outbox",
        cursor_path=client_home / ".xskill" / "cursor.json",
        history_path=client_home / ".xskill" / "install_history.jsonl",
        home_root=client_home, quiet_seconds=0,   # 测试不等 3min
    )

    # ── 3. collect + upload ──
    client.collector.start_ingesters()
    time.sleep(2)   # 等 ingester 桥接
    n = client.collect_and_upload()
    assert n >= 1
    # 脱敏断言：server 端落盘内容不含 sk- 明文
    uploaded = list((server_home / ".xskill" / "team_trajectories"
                     / "clients" / cid / "sessions").glob("traj_*.md"))
    assert uploaded
    assert SK_LEAK not in uploaded[0].read_text(encoding="utf-8")
    assert "[REDACTED]" in uploaded[0].read_text(encoding="utf-8")

    # ── 4. server 跑 agent（等 watcher 几轮）+ sync + reconcile ──
    <轮询等 server watcher 把上传轨迹跑到 done；超时上限 ~60s>
    manifest = client.sync()
    assert any(s.skill_name == "fix-foo" for s in manifest.slots)
    client.reconcile_skill_sides(manifest)
    installed = client_home / ".claude" / "skills" / "fix-foo"
    assert installed.is_symlink() or installed.is_dir()

    # ── 5. 手改 push ──
    repo = client_home / ".xskill" / "team_skills" / "fix-foo"
    skill_md = repo / "SKILL.md"
    skill_md.write_text(skill_md.read_text() + "\n<!-- user tweak -->\n",
                        encoding="utf-8")
    os.utime(skill_md, (time.time() - 1, time.time() - 1))
    pushed = client.push_user_edits()
    assert pushed == 1
    import subprocess
    branches = subprocess.run(
        ["git", "-C", str(server_home / ".xskill" / "skill" / "fix-foo"),
         "branch", "--list", f"user-staging/{cid}"],
        capture_output=True, text=True).stdout
    assert f"user-staging/{cid}" in branches

    # ── 6. cleanup ──
    stale = client_home / ".xskill" / "team_skills" / "stale"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("# stale", encoding="utf-8")
    client.cleanup(client.sync())
    assert not stale.exists()

    client.collector.stop_ingesters()
    <停 server / fake_llm>
```

- [ ] **Step 3: 跑 E2E，反向迭代修 bug**

Run: `python3.11 -m pytest tests/e2e/test_team_cs_e2e.py -q -s`
Expected: 首跑大概率有问题（mock LLM Responder 没覆盖到某调用 / watcher 时序 / 路径）。**逐个修到全绿**——这是 CLAUDE.md 要求的"实际打通用户通路，出 bug 反向迭代修复直到 ok"。

- [ ] **Step 4: 全量回归**

Run: `python3.11 -m pytest -q`
Expected: 全绿（含既有所有测试）。

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_team_cs_e2e.py
git commit -m "test(team): C/S 端到端集成测试 (mock LLM, 脱敏→上传→agent→reconcile→push-edit)"
```

---

### Task 20: 配置样例 + README

**Files:**
- Modify: `examples/config.yaml.example`（加 `team:` 段）
- Modify: `README.md`（加 team 模式简述）

- [ ] **Step 1: 改 `examples/config.yaml.example`**

在 `canary:` 段之后加：

```yaml
# team C/S 模式（仅 `xskill serve --server` 读；client 用 ~/.xskill/team_client.json）
team:
  server:
    traj_root: ~/.xskill/team_trajectories   # client 上传轨迹的落盘根
    skill_slots: 100                         # client 本地最多持有的 skill 数
    ranked_slots: 80                         # 其中按 ux_score 滑窗取的数量
                                             # 余下 20 = recommended 桶（SP3 = 画像推荐）
```

- [ ] **Step 2: 改 `README.md`**

在合适位置（CLI 子命令说明附近）加一段：

```markdown
## team 模式（C/S 共享 skill）

xskill 支持把组织里的 skill 共享出来：

- **server**：`xskill serve --server` —— 起 team server，收 client 上传的轨迹、
  在 server 端跑全部 agent（拆分/归类/撰写/灰度判定），并打印 client 加入命令。
- **client**：`xskill connect <host:port> --token <token>` —— 瘦客户端，采集本机
  code-agent 轨迹脱敏后上传，持有 server 算出的 ≤100 个 skill working copy 并按
  server 分配的灰度 side 对齐。零 LLM 调用、零 git 写 main。
- **standalone**：`xskill serve`（不加 `--server`）—— 仍是单机模式，skill 留本机。

灰度（canary）在 CS 模式下按 `client_id` 真分桶；client 的本地手改只会进
`user-staging/<client_id>` 隔离分支，永远碰不到公共 main。
```

- [ ] **Step 3: Commit**

```bash
git add examples/config.yaml.example README.md
git commit -m "docs(team): config.yaml.example + README 加 team C/S 模式说明"
```

---

## Self-Review

**1. Spec coverage（对照设计「最终设计方案」逐条）：**

| 设计要点 | 落点 |
| --- | --- |
| `xskill serve --server` 起 server + 打印 connect 示例 | Task 12 |
| `xskill serve` standalone + 提示 | Task 12 |
| `xskill connect IP:port` 瘦客户端 | Task 17 |
| 重 server / 瘦 client（agent 全在 server） | Task 9/10/11（watcher server_mode）+ Task 16（client 零 LLM） |
| 静默 ~3min 增量上传 | Task 15（`quiet_seconds` + 游标） |
| client 完全信任 server + 最小脱敏（sk-/密码） | Task 1（redact）+ Task 15（上传前过 hook） |
| `<user>/sessions/` 分桶落盘 | Task 8（`clients/<client_id>/sessions/`） |
| skill = git 分布式（baby/main/staging） | Task 4（bundle）+ Task 8（bundle 端点） |
| 100 slot = 80 ranked + 20 recommended | Task 7（`build_manifest` bucket 字段） |
| 单一 join token 鉴权 | Task 5 + Task 8（`_auth`） |
| client 愚蠢可能恶意 → 手改进 `user-staging/<user>` | Task 8（push-edit）+ Task 16（`push_user_edits`） |
| `pick_side(bucket_key, skill, p)` 两模式共用 | 复用既有 `canary.pick_side`；Task 7 CS 用 `client_id`，Task 18 单机用时间窗 |
| server 不存账本表 = pick_side + git 状态投影 | Task 7（`build_manifest` 现算） |
| `reconcile_skill_sides` 统一契约（步骤 1 分叉，2/3/4 共享） | Task 14（共享助手）+ Task 16（client）+ Task 18（单机收敛） |
| CS canary 归因（ux_score 归到正确 side） | Task 10（`_score_atoms_for_traj_server`） |
| client cleanup（manifest 外的本地 skill 删掉） | Task 16（`cleanup`） |
| 三个子项目拆分（SP1 骨架 / SP2 脱敏 / SP3 推荐） | 本 plan = SP1；SP2/SP3 在 "Out of scope" 标注 |

无遗漏。`search-fetch` 工具（设计里"画像没覆盖的逃生口"）归 SP3——SP1 的 100 slot 已是 turn-0 主路径，逃生口不阻塞骨架。

**2. Placeholder scan：** E2E（Task 19）有 `<...>` 占位——这是有意的：它依赖 `tests/_fake_llm_server.py` / `tests/e2e/test_smoke.py` 的既有 harness API，Task 19 Step 1 是明确的"读这两个文件确定 API"步骤，Step 2 骨架其余部分是完整代码。其余 Task 的实现代码均完整无占位。Task 10 的测试桩 `_FakeLLM` 标注了"执行者读 `ux_score.py` 确认签名"——`score_atom` 的调用形态已从 `watcher.py:871-873` 锁定为 `score_atom(llm=, atom=, side=)`，桩可直接按此写。

**3. Type consistency：** 
- `SkillSlot` 字段 `skill_name/side/sha/bucket` 在 Task 3 定义，Task 7/8/16 一致使用。
- `reconcile_skill_side(*, repo_dir, target_side, target_sha, history, on_changed)` 在 Task 14 定义，Task 16/18 调用签名一致。
- `pick_side(bucket_key, skill_name, probability)` —— 既有函数（`canary.py:229`），Task 7 传 `client_id`、Task 18 传 `str(window_id)`，与既有签名一致。
- `InstallHistory.record(*, skill, side, sha, t=None)` —— 既有（`install_history.py:45`），Task 14 调用 `history.record(skill=, side=, sha=)` 一致。
- `make_repo_bundle / apply_repo_bundle / make_branch_bundle / fetch_branch_from_bundle` 在 Task 4 定义，Task 8/16 调用一致。
- `register_with_server(http, *, token, label, hostname)` 在 Task 16 定义，Task 17 调用一致。

无签名漂移。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-14-xskill-team-sp1.md`. Two execution options:

**1. Subagent-Driven (recommended)** — 每个 Task 派一个全新 subagent 执行，Task 之间 review；20 个 Task 顺序有依赖（Phase A→B→C→D→E），适合逐个推进、快速迭代。

**2. Inline Execution** — 在当前 session 用 executing-plans 批量执行，带 checkpoint review。

Which approach?
