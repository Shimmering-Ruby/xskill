# Cross-Platform × Multi-Agent Expansion — Design Doc

> 目标：把 xskill 的真实支持矩阵从 **Linux × Claude Code** 扩展到 **{Linux, macOS, Windows} × {Claude Code, Codex, OpenCode}** 的 3 × 3 = 9 个支持单元，并通过 GitHub Actions 矩阵在每次 PR 都自动验证。
>
> 上游材料：
> - 调研：[`adapter-research.md`](adapter-research.md)
> - 测试策略：[`testing-strategy.md`](testing-strategy.md)
> - CI 草稿：[`ci-workflow-draft.yml`](ci-workflow-draft.yml)
> - 项目 ecosystem 已有：[`../ecosystem/codex.md`](../ecosystem/codex.md) · [`../ecosystem/opencode.md`](../ecosystem/opencode.md)

---

## 1. TL;DR

- **能做。** Codex / OpenCode 都有"扫描磁盘 + frontmatter-style skill"的相似形态，xskill ingester 是纯文件读取（不 spawn agent 进程）。
- **必须实装 + 实测**（用户指令，覆盖前一版"全程 fixture"策略）：
  - **开发期**：P2/P3 subagent 在 worktree 里**实地 `npm i -g`** 装 Codex CLI / OpenCode，**用真实进程跑一条短 session**（用 `~/.aikey` 里的 DeepSeek key），把产出的 JSONL / SQLite **作为 fixture 入仓**，并对比 ingester 解析结果与真实 agent 状态一致。
  - **CI 每次 PR**：Smoke E2E job 在 runner 上**先装 codex/opencode CLI**（验证安装不出错），再用入仓的 fixture 跑 adapter（不调 LLM，避免 secret 泄漏）。
  - **CI push-to-main / nightly**：Real-LLM E2E + Live-agent E2E job 用 `secrets.DEEPSEEK_API_KEY` **真跑** codex/opencode 一条短 session，端到端验证。
- **接口要小破坏。** 现有 `_KNOWN_ECOSYSTEMS` 假设来源全是 JSONL append-only；OpenCode 用 SQLite + WAL，必须新增 **`source_kind: jsonl | sqlite`** 维度 + 一个 cursor-based SQLite poller。Codex 是同 JSONL 形态，几乎可以复用 CC 骨架。
- **Skill 安装收口到一个目录。** `~/.agents/skills/<name>/` 是 Codex / OpenCode 双方都扫的全局共享目录；xskill 对 Codex/OpenCode 不再各写各的，**统一只写 `~/.agents/skills/`**（CC 仍写 `~/.claude/skills/<name>/`，保留原行为）。
- **Windows 是次等公民。** symlink 需要 Developer Mode，OpenCode 的 xdg-basedir 行为在 Win 上未验证。CI 跑 UT/IT 全平台，Smoke E2E 全平台跑（含装 CLI）；真实 codex/opencode 进程在 Win 上的兼容性 P3 实测后定。
- **并行可拆 4 个 PR**：P1 跨平台基线 / P2 Codex 适配器 / P3 OpenCode 适配器 / P4 Smoke E2E。P2 与 P3 可纯并行；P1 是地基（先 merge）；P4 在 P1+P2+P3 全 merge 后跑。

---

## 2. 接口抽象（破坏性扩展点）

### 2.1 现状（CC-only）

`src/xskill/ecosystems.py` 当前定义：

```python
_KNOWN_ECOSYSTEMS = [
    {
      "name": "claude_code",
      "source_subpath": ".claude/projects",   # <home>/<this>/<cwd-hash>/*.jsonl
      "label": "claude_code",
      ...
    },
]
```

`CCSessionIngester` 假设：
- 来源是 `<home>/<source_subpath>/<dir>/*.jsonl` 流式 append
- 用 `mtime + offset cursor` 增量扫
- 桥成 `traj_*.md` 落到 xskill watch dir

### 2.2 目标接口

#### 2.2.1 Ingester 抽象

引入两个维度：`source_kind` + `path_resolver`：

```python
@dataclass
class EcosystemSpec:
    name: str                        # "claude_code" | "codex" | "opencode"
    source_kind: Literal["jsonl", "sqlite"]
    path_resolver: Callable[[Path], Path]   # (home_root) -> source dir/file
    cursor_strategy: Literal["mtime_offset", "sqlite_time_updated"]
    label: str
    cwd_extractor: Callable[[dict], Path]   # 从 record 抽 cwd

_KNOWN_ECOSYSTEMS: list[EcosystemSpec] = [
    EcosystemSpec(
        name="claude_code",
        source_kind="jsonl",
        path_resolver=lambda home: home / ".claude" / "projects",
        cursor_strategy="mtime_offset",
        ...
    ),
    EcosystemSpec(
        name="codex",
        source_kind="jsonl",
        path_resolver=lambda home: home / ".codex" / "sessions",  # recursive YYYY/MM/DD
        cursor_strategy="mtime_offset",
        ...
    ),
    EcosystemSpec(
        name="opencode",
        source_kind="sqlite",
        path_resolver=lambda home: home / ".local" / "share" / "opencode" / "opencode.db",
        cursor_strategy="sqlite_time_updated",
        ...
    ),
]
```

`Ingester` 类按 `source_kind` 分派：

```python
class Ingester(ABC):
    @abstractmethod
    def scan_new_records(self, spec, since_cursor) -> list[Record]: ...

class JsonlIngester(Ingester):  # 复用现 CCSessionIngester 骨架
    ...

class SqliteIngester(Ingester):  # 新增
    def scan_new_records(self, spec, since_cursor):
        # 用 sqlite3.connect("file:...?mode=ro&immutable=1", uri=True) 只读打开
        # SELECT * FROM message WHERE session_id IN
        #   (SELECT id FROM session WHERE time_updated > ?)
        # ORDER BY time_created
        ...
```

#### 2.2.2 Installer 抽象

引入 `install_target`：

```python
@dataclass
class InstallSpec:
    name: str
    install_path: Callable[[Path], Path]   # (home_root) -> install dir

_INSTALL_SPECS = {
    "claude_code": lambda home: home / ".claude" / "skills",
    "codex":       lambda home: home / ".agents" / "skills",  # 共享目录
    "opencode":    lambda home: home / ".agents" / "skills",  # 同 codex（同一目录写一次即可）
}
```

**重要简化**：Codex 与 OpenCode 共享 `~/.agents/skills/`，所以 `install_to_codex` 和 `install_to_opencode` 实际是同一个底层调用 `install_to_path(home / ".agents" / "skills")`，**只在 metadata 标签上区分**。

#### 2.2.3 跨平台 symlink fallback

```python
def install_skill(src_dir: Path, dest: Path):
    """三阶 fallback：symlink → directory junction (Win) → copy + warning."""
    try:
        dest.symlink_to(src_dir, target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError):
        if platform.system() == "Windows":
            try:
                subprocess.run(["cmd", "/c", "mklink", "/J", str(dest), str(src_dir)], check=True)
                return "junction"
            except subprocess.CalledProcessError:
                pass
        # 终极 fallback
        shutil.copytree(src_dir, dest)
        logger.warning("falling back to copy install at %s — user edits will NOT round-trip", dest)
        return "copy"
```

注意 **copy 模式下 UserEditAbsorbAgent 失效**（用户改的是副本，源仓看不到）。文档要明示。

### 2.3 兼容性收口

- 现有 `CCSessionIngester` 改名 `JsonlIngester(spec=CLAUDE_CODE_SPEC)`，CC-only 路径保留作为 spec 实例
- 新增 `SqliteIngester`
- 旧 `install_to_claude_code` 保留为 `install_to(spec=CC)` 的 wrapper（向后兼容 SDK）

---

## 3. 平台 × Agent 目标矩阵

| | Linux | macOS | Windows |
| --- | :--: | :--: | :--: |
| **Claude Code** | ✅ tested | ✅ should work (CI 新增) | ⚠️ symlink fallback (CI 新增) |
| **Codex** | ⏳ P2 | ⏳ P2 | ⏳ P2 (symlink fallback) |
| **OpenCode** | ⏳ P3 | ⏳ P3 | ⚠️ P3 (xdg + symlink；本机未验) |

**未达成目标**：
- Live-agent E2E（真实跑 Codex / OpenCode 进程）→ Manual / Nightly only，不卡 PR
- Windows × OpenCode 在 xdg-basedir 失常时的边界 → 文档明示，给用户预设 `XDG_DATA_HOME` 的指引

---

## 4. 测试策略

详见 [`testing-strategy.md`](testing-strategy.md)。要点：

- **UT/IT 全平台**（3 OS × 2 py）— 包含新增的 SqliteIngester、Codex JsonlIngester、跨平台 symlink fallback
- **Smoke E2E 全平台 × 全 agent**（3 OS × 3 agent = 9 jobs）— 用 fixture 文件，不需要装 agent 本体
- **Real-LLM E2E 仅 Linux** — 控制成本，验证 prompt / agent 决策没坏
- **Live-agent E2E manual** — 真装 Codex / OpenCode 走端到端，release gate

新增需要的 fixture：

```
tests/fixtures/
├── claude_code/                # 已有
├── codex/
│   ├── rollout-2026-01-15T10-00-00-deadbeef.jsonl
│   └── README.md               # session schema 说明
└── opencode/
    ├── opencode.db             # 脱敏后的 SQLite, 1-2 session
    └── README.md               # db schema + 怎么造的
```

---

## 5. CI Workflow

详见 [`ci-workflow-draft.yml`](ci-workflow-draft.yml)。P1 PR 落地。

关键点：
- 每次 PR：UT+IT (6 jobs) + Smoke E2E (9 jobs) — wall-clock ~5 min
- push to main：上面 + Real-LLM E2E (1 job, continue-on-error)
- manual / nightly：上面 + Live-agent E2E (1 job, manual)
- 时间预算：免费额度足够支撑 ~50 PR/月

---

## 6. PR 拆分（4 个 PR，可并行）

### P1: 跨平台基线 `feat/p1-cross-platform-baseline`

**Scope**：
- 落地 `.github/workflows/ci.yml`（替换现版本）
- 新增 `src/xskill/install_fallback.py`：跨平台 symlink → junction → copy 三阶 fallback
- 修改 `ecosystems.py::install_to_claude_code` 调上面 fallback（不动 Linux / macOS 行为）
- 加 `tests/test_install_fallback.py`：模拟 symlink 失败、junction 失败的两条路径
- `pyproject.toml` classifiers 加 `"Operating System :: OS Independent"`、`"Operating System :: POSIX :: Linux"`、`"Operating System :: MacOS"`、`"Operating System :: Microsoft :: Windows"`
- 在 ecosystems.py 把硬编码 `~/.claude/...` 拆出来成 `_path_for_cc_*()` helpers，为 P2/P3 复用做准备

**验收**：
- CI 在 ubuntu/macos/windows 三平台 UT+IT 都绿
- `pytest tests/test_install_fallback.py` 跑过三平台
- 在 Windows runner 不开 Dev Mode 时也能装 skill（走 copy fallback）

**不在 scope**：
- 任何 Codex / OpenCode 相关代码
- Smoke E2E（留给 P4）

**预计 diff**：~300 行 src + ~150 行 tests + ~80 行 workflow

### P2: Codex 适配器 `feat/p2-codex-adapter`

**强制实装步骤**（开发期 subagent 必须真做）：
1. `node --version && npm --version` 验证 runner 有 Node
2. `npm i -g @openai/codex`（如有 `which codex` 校验）；如失败用 `cargo install codex` 或从 source build
3. `which codex && codex --version` 确认安装成功
4. 配置 codex 使用 DeepSeek（用 `~/.aikey` 的 `DEEPSEEK_API_KEY` + base_url `https://api.deepseek.com`）
5. **用真实 codex 跑一条短 session**：`codex "write a python hello world to /tmp/foo.py"`（或类似 30 秒内能完的任务）
6. 验证 `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` 生成
7. 把这条 rollout 文件**脱敏**（去掉 user-specific token / hostname）后入仓作 `tests/fixtures/codex/sample_rollout.jsonl`
8. PR description 必须含：装 codex 用的命令、`codex --version` 输出、生成 rollout 的 prompt、fixture 大小

**Scope**（代码）：
- 把现有 `CCSessionIngester` 抽象成 `JsonlIngester(spec)`，CC 作为一个 spec 实例
- 加 Codex 的 `EcosystemSpec`：路径 `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`、cwd 从 `SessionMeta` 首行抽
- 加 `install_to_codex(target=~/.agents/skills/)` —— 调 P1 的 install_fallback
- 加 fixture `tests/fixtures/codex/sample_rollout.jsonl`（**真实跑 codex 出来的，脱敏后入仓**）
- 加单测：`tests/test_codex_adapter.py` 覆盖 ingest + install + 路径解析
- **加 live test**：`tests/live/test_codex_live.py`，需要 `XSKILL_LIVE_CODEX=1` env 才跑；CI 在 push-to-main / nightly 设这个 env + secret

**验收**：
- ✅ subagent 报告"实装 codex 成功 + 跑出 rollout"，PR description 截图 `codex --version`
- 全平台 UT 跑通（用入仓 fixture）
- fixture rollout 能正确生成 traj_*.md
- install 到 `~/.agents/skills/` 路径正确（macOS / Linux）和 fallback 路径正确（Windows）
- live test 在本机用真 codex 跑过一次（subagent 验证）

**预计 diff**：~250 行 src + ~250 行 tests + 1 fixture（真实数据脱敏）

### P3: OpenCode 适配器 `feat/p3-opencode-adapter`

**强制实装步骤**（开发期 subagent 必须真做）：
1. `npm i -g opencode-ai@latest` 或 `curl -fsSL https://opencode.ai/install | bash`
2. `which opencode && opencode --version` 确认
3. 配置 opencode 用 DeepSeek（写 `~/.config/opencode/config.json` 或类似）
4. `opencode "write a python hello world"` 跑一条短 session
5. 验证 `~/.local/share/opencode/opencode.db` 出现（或更新）
6. 把这个 DB **脱敏**（去掉 user path / hostname；保留 1-2 session schema 完整）入仓作 `tests/fixtures/opencode/sample.db`
7. PR description 必须含装 opencode 用的命令 + `opencode --version` + 跑的 prompt + db schema dump

**Scope**（代码）：
- 加 `SqliteIngester` 新类：`sqlite3.connect("file:...?mode=ro&immutable=1", uri=True)`，cursor 用 `session.time_updated`
- 加 OpenCode 的 `EcosystemSpec`：路径 `~/.local/share/opencode/opencode.db`（XDG）
- 加 `install_to_opencode` —— 同 Codex 写 `~/.agents/skills/`（同一目录，不重复写）
- 加 fixture `tests/fixtures/opencode/sample.db`（**真实跑 opencode 出来的，脱敏后入仓**）
- 加单测：`tests/test_opencode_adapter.py` 覆盖 SQLite 读、cursor 增量、cwd 抽取
- **加 live test**：`tests/live/test_opencode_live.py`，需要 `XSKILL_LIVE_OPENCODE=1` env 才跑

**验收**：
- ✅ subagent 报告"实装 opencode 成功 + 跑出 db"，PR description 含命令 + 版本 + schema
- 全平台 UT 跑通
- SQLite WAL 并发模拟下不触发 `database is locked`
- Windows 上能跑（用 fixture 路径绕开 xdg-basedir；live test 在 Win 上 R1 实测后回填到 PR）

**预计 diff**：~300 行 src + ~250 行 tests + 1 fixture（真实数据脱敏）

### P4: Smoke E2E + Live E2E `feat/p4-e2e`

**Scope**：
- 加 `tests/e2e/test_smoke.py`：参数化 `agent_name in {claude_code, codex, opencode}`
- 起 daemon → register watch dir → 丢 fixture（每 agent 一份） → 等 watcher pick up → 断言 skill 装到正确路径
- Smoke 跑 stub LLM + 入仓 fixture（不调真 LLM），但 **CI job 第一步装 codex/opencode CLI**（验证安装在三 OS 都能跑）
- 加 `tests/live/test_full_pipeline.py`：用真 codex/opencode + 真 LLM 跑完整 pipeline 一次（仅 Linux）
- CI workflow 完成填充：
  - `smoke-e2e` job：装 CLI + 跑 stub e2e（每 PR 都跑）
  - `live-agent-e2e` job：装 CLI + 用 secret API key 真跑（push-to-main + nightly + manual）

**验收**：
- CI 矩阵 9 job (3 OS × 3 agent) smoke-e2e 全绿；每 job 第一步装 CLI 成功
- live-agent-e2e 在 Linux 上跑通至少一次（subagent 实地验，PR description 含跑通日志）
- 单 smoke job ≤ 3 min（多了 npm 装 CLI 的时间）

**Depends on**：P1 + P2 + P3 全部 merge

**预计 diff**：~500 行 tests + 3 fixtures（共用 P2/P3 的）

---

## 7. 风险 + 未确认事项

来自 [`adapter-research.md`](adapter-research.md) §"未确认 / 需要后续验证" + 本设计中暴露的新风险：

| # | 风险 | 影响 | 缓解 |
| --- | --- | --- | --- |
| R1 | OpenCode 在 Windows 上 xdg-basedir 行为未验证 | Win × OpenCode 可能完全跑不通 | P3 fixture 测试不依赖真实 xdg，绕开；文档显式标"Win × OpenCode = experimental"；Live-agent E2E 实地验证后再升级标签 |
| R2 | OpenCode 跑时 SQLite WAL 锁 | xskill ingester 触发 `database is locked` | 用 `?mode=ro&immutable=1` 只读 URI；P3 加并发 stress 测试 |
| R3 | `@openai/codex` npm 包 = codex-rs 还是 codex-cli (JS) | 路径 / 文件名假设可能错 | P2 实施时第一步装一次 `npm i -g @openai/codex` + `which codex` 确认；如果是 JS 版本，调整 fixture |
| R4 | `~/.agents/skills/` 跨 agent 同名冲突 | 用户装多个 agent 时 skill 互相覆盖 | xskill 加 `.xskill-managed` 标签文件防止外部 overwrite；user_edit_absorb 不抓没有这个标签的目录 |
| R5 | Codex 多 `originator` (cli / vscode / atlas / chatgpt) | 同一 user 跨入口写入，xskill 是否分桶 | 初版不分桶（atom-level cluster 本就能处理多源）；适配器把 `originator` 透传到 trajectory metadata，未来想分时再切 |
| R6 | Windows symlink 在 Dev Mode 关闭时失败 | install 失败 → user_edit_absorb 失效 | P1 三阶 fallback（symlink → junction → copy）；copy 模式下显式 warning + 文档标 |
| R7 | macOS file system mtime 精度 (HFS+ vs APFS) | user_edit_absorb 之前修过的精度 bug 可能复现 | 已有 fix（mtime - commit_ts ≥ 1.0s），加 macOS CI 覆盖再验证 |
| R8 | Codex `forked_from_id` fork 链 | 蒸馏数据重复计数 | 初版按独立 trajectory 处理（每个 fork 一条），未来如发现 atom 重复再加去重 |

---

## 8. 实施时序

```
   现在 (HEAD: main, 79f0d92 = README OS matrix push)
     │
     ▼
  P0.3 (this doc) ──► 用户 review ──► approved
     │
     ▼
  开 agent team:
   ├── subagent-1  feat/p1-cross-platform-baseline  ─┐
   ├── subagent-2  feat/p2-codex-adapter  ───────────┤  并行
   └── subagent-3  feat/p3-opencode-adapter  ────────┘
     │
     ▼ (3 个 PR 都 ready)
  主 agent: review + merge P1
     │ (P1 落地后 P2/P3 可能 rebase)
     ▼
  主 agent: review + merge P2
     │
     ▼
  主 agent: review + merge P3
     │
     ▼
  subagent-4  feat/p4-smoke-e2e  (依赖 P1+P2+P3 已 merge)
     │
     ▼
  主 agent: review + merge P4
     │
     ▼
  release v0.4.0a2 (PyPI + GitHub tag)
```

每个 subagent 工作完用 `gh pr create` 提 PR。主 agent 用 `gh pr checks` + `gh pr diff` 验，最后 `gh pr merge --squash`。

---

## 9. 决策记录（已拍板）

- **R3** Codex npm 包对应 codex-rs 还是 codex-cli：P2 subagent 第一步实地 `npm i -g @openai/codex` + `which codex` 验证，根据实际结果调整 spec
- **R4 `.xskill-managed` 标签**：**不加**（保守）。xskill 写 `~/.agents/skills/` 时直接占用，由用户保证不与外部冲突
- **R1 / R8 实地验**（用户 2026-05-13 指令"必须实际装 实际测试"覆盖前一版策略）：
  - P2/P3 subagent **强制实地装 + 实跑**（§6 强制实装步骤）
  - CI smoke-e2e **每个 PR 都装 codex/opencode CLI** 验证安装步骤
  - CI live-agent-e2e 在 main push / nightly 用 secret 真跑端到端
  - 入仓 fixture 必须来自真实 agent 进程（脱敏后），不接受手编
