# t2s CLI 紧凑化与 OOP 重构设计

- 作者：370025263
- 日期：2026-05-09
- 状态：Design (待 user 审阅)
- 关联分支：`refactor/skill-format-v2`

---

## 1. 背景与动机

### 1.1 现状

`src/traj2skill/cli.py` 当前 911 行、**16 个子命令**（`index / search / search-skill / process / batch / init / status / reindex / eval / skill (12 子动作) / show / serve / validate / watch / registry`）。状态与配置散落在三处：

- `./config.yaml`（cwd 相对，`config.py:34` 写死 `Path.cwd() / "config.yaml"`）
- `~/.traj2skill/registry.db`（SQLite，`watch_dirs + trajectories`）
- `~/.traj2skill/chat_sessions.db`（agno chat 历史）

模块层面有 3 处独立的 agno Agent 装配（蒸馏 / 候选促升 / chat），每处自带一套 `read_file / write_file / list_files` 工具实现，写域、字节上限、根目录边界各不相同。`<skill_dir>` 实际是双 git 仓结构：顶层 `.git`（`process.commit_changes` 用）+ 每个 `<skill>/.git`（canary 子仓 main/staging）。

`should_merge(is_new=False)` 分支无任何调用方；chat 路由的 side 选择在 `/resolve / /chat / /chat/stream` 三处实现不一致；`chat_app.py` 整个文件未被 import；`git_lock.acquire_lock / release_lock / make_branch_name / merge_to_main` 全部 0 调用；v1 lowercase `skill.md` + `.abstract` 的 fallback 在 ≥7 处仍激活。

### 1.2 动机

CLI 子命令绝大多数是 watcher 内部步骤的对外暴露，不是用户工作流。配置/状态散落导致 cwd 耦合（必须在项目目录跑 `t2s` 才正常）。三套 agent 工具是熵增最严重的位置，改一处 bug 要同步三处。

**目标**：把 CLI 收敛到"用户真正用的入口"，把状态/配置统一到 `~/.t2s/`，把三套 agent 工具收敛到一套 `AgentToolkit`，把蒸馏流程从 200+ 行函数升级为 `PipelineRunner` 类，删除全部已识别死代码。

### 1.3 非目标

- 不重写 watcher / canary / process 的核心逻辑——只做职责聚合 + 接口收敛
- 不改变 SKILL.md frontmatter schema、`.candidates.yml`、`.ux_scores.jsonl` 文件格式
- 不引入向后兼容层（按 `CLAUDE.md` 第 4 条："不在代码中做老配置兼容"）

---

## 2. CLI 终态

```bash
t2s serve [--host 0.0.0.0] [--port 8000]
        # 唯一长跑入口：FastAPI + watcher 内置同进程
        # 不再有 --no-watch / --no-ui 等关功能 flag

t2s registry add <path>
t2s registry remove <path>
t2s registry list

t2s search traj  <query> [--top-k 5]   # 跨所有 registry
t2s search skill <query> [--top-k 5]   # 跨所有 registry
```

全局：`--debug / --quiet`。**删除** `--config / --skill-dir / --traj-dir / --llm-base-url / --llm-model / --llm-key`。

**删除子命令清单**：`index, search-skill, process, batch, init, status, reindex, eval, skill (12 个 action 全删), show, validate, watch`。

### 2.1 search 输出格式（文本，shell 友好）

```
# t2s search traj <query>
# 列：similarity  status   skill_used        side   path
0.781  success  fix_form_validation  main     /home/admin/data/swe/traj_0042.md
0.722  failure  -                    -        /home/admin/data/swe/traj_0123.md

# t2s search skill <query>
# 列：similarity  name                 used   ux_avg(N)   canary
0.853  fix_form_validation  23     7.8(15)     staging
0.791  fix_orm_n_plus_one   17     8.1(12)     -
```

字段空格分隔，便于 `awk / grep`。`ux_avg(N)` 是近 30 天 N 条 UX 分平均；`canary` 列 `staging | -`。**不提供** `--filter / --json`——筛选交给 shell。

---

## 3. `~/.t2s/` 布局

```
~/.t2s/
├── config.yaml         # 取代 ./config.yaml；带注释，无 fallback
├── registry.db         # 监听目录 + 轨迹状态
├── chat_sessions.db    # agno chat 历史
├── logs/               # 取代 ./output/*.log.json
└── skill/              # 默认 skill 仓库（config.yaml 可改）
```

**不做自动迁移**。老用户手动 `mv ~/.traj2skill ~/.t2s/` 并把原 `./config.yaml` 内容搬入 `~/.t2s/config.yaml`。找不到 `~/.t2s/config.yaml` 直接 `FileNotFoundError`。

**关键观念变更**：
- 删除 `traj_dir` 概念。dataset 只通过 `registry add <绝对路径>` 注册，没有"默认 traj 目录"
- `skill_dir` 默认 `~/.t2s/skill/`，单一全局仓库；改在 `config.yaml` 里改
- `<skill_dir>` 顶层 `.git` 删除（历史遗留）；每个 `<skill>/.git` 保留，作为 canary main/staging 的载体
- 全部使用 `<skill>/.git` 子仓做 commit；不再有顶层"打包 commit"行为

---

## 4. `~/.t2s/config.yaml` Schema

```yaml
# ===== Skill 仓库 =====
skill_dir: ~/.t2s/skill          # 全局唯一 skill repo

# ===== LLM（生成 / 评分用）=====
llm:
  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
  model:    deepseek-v3.2
  api_key:  sk-xxxx              # 必填，不读环境变量、不读 ~/.aikey

# ===== Embedding（向量检索用）=====
embedding:
  base_url: https://ark.cn-beijing.volces.com/api/v3
  model:    doubao-embedding-vision-251215
  api_key:  xxxx                 # 必填
  dim:      0                    # 0 = 自动探测

# ===== L2 沙箱评估（SWE-bench docker A/B/C）=====
sandbox:
  enabled:           true
  max_instances:     5            # 单次 eval 跑几个 instance
  n_trials:          10           # 每 instance 重复次数（pass@1 的 N）
  timeout_per_trial: 300          # 单 trial 墙钟超时（秒）
  trigger_threshold: 3            # skill 至少关联 N 条 SWE instance 才触发

# ===== Skill 候选门控 =====
candidates:
  threshold:        3             # supporting_trajs ≥ N 才升级到 SKILL.md body
  stale_days:       60            # 超过 N 天未达标 → 沉到 references/
  min_source_trajs: 2             # 新建 skill 至少要 N 条 source_trajs

# ===== Skill 编辑 Agent（PromotionAgent）=====
skill_edit_agent:
  tool_call_limit:     20         # 单次 promote 最多调几次工具
  timeout_seconds:     600        # 墙钟超时（保留已写入部分，不抛异常）
  read_file_max_bytes: 15000      # read_file 单次截断字节数

# ===== Canary（灰度发布：staging 分支按概率替换 main，
#       两侧各 ≥ min_samples 条 UX 分后比较，胜出 merge / 落败 discard）=====
canary:
  enabled:       true
  probability:   0.2              # 检索命中时以 p 概率走 staging
  min_samples:   5                # 两侧各 ≥ N 条 UX 分才判定
  max_days_hold: 14               # staging 最长存活；超时丢弃

# ===== Watcher（serve 内置的目录轮询）=====
watcher:
  poll_interval: 30               # 秒
```

格式选择 yaml 而非 json：人类可写、可注释，机器读写仍然简单（`yaml.safe_load / yaml.safe_dump`）。**无 fallback**——api_key 缺失不读 env、不读 `~/.aikey`，直接抛 `KeyError`。

---

## 5. SDK 类设计

### 5.1 分层

```
┌───────────────────────────────────────────────────────────────┐
│ SDK 暴露层（用户 import）                                     │
│   T2S, Skill, Trajectory, Evaluator                           │
│   + dataclasses: WatchDir / SkillHit / TrajectoryHit /        │
│                  EvalScore / Candidate / UxScoreResult        │
└───────────────────────────────────────────────────────────────┘
                              │
┌───────────────────────────────────────────────────────────────┐
│ 内部组件                                                      │
│   Registry, SkillRepo, CandidateBuffer, CanaryGitOps,         │
│   AgentToolkit, DistillationAgent, PromotionAgent,            │
│   ChatAgent, PipelineRunner, DirectoryWatcher                 │
└───────────────────────────────────────────────────────────────┘
                              │
┌───────────────────────────────────────────────────────────────┐
│ 持久化（不动现有实现）                                        │
│   config.yaml, registry.db, chat_sessions.db,                 │
│   <skill>/.git, .candidates.yml, .ux_scores.jsonl,            │
│   SKILL.md, index.pkl, .skill_index.pkl                       │
└───────────────────────────────────────────────────────────────┘
```

### 5.2 类图

```
═════════════════════════════════════════════════════════════════════════════
  SDK 暴露层
═════════════════════════════════════════════════════════════════════════════

           ┌────────────────────────────────────────┐
           │                T2S                     │
           │            (facade / 门面)             │
           ├────────────────────────────────────────┤
           │ - config: dict                         │
           │ - registry: Registry          ◆────────┼──┐
           │ - skill_repo: SkillRepo       ◆────────┼──┼──┐
           ├────────────────────────────────────────┤  │  │
           │ + search_trajectories(q, top_k)        │  │  │
           │ + search_skills(q, top_k)              │  │  │
           │ + serve(host, port)                    │  │  │
           │ + score_trajectory_ux(traj)            │  │  │
           └────────────────────────────────────────┘  │  │
                                                       │  │
   ┌───────────────────────────────────────────────────┘  │
   ▼                                                      │
┌──────────────────────────┐                              │
│       Registry           │                              │
├──────────────────────────┤                              │
│ - db_path: Path          │  ← ~/.t2s/registry.db        │
├──────────────────────────┤                              │
│ + add(path) → WatchDir   │                              │
│ + remove(path) → bool    │                              │
│ + list() → list[WatchDir]│                              │
│ + trajectories_using(    │  ← 反查：用过某 skill 的轨迹 │
│       skill_name)        │      → list[Trajectory]      │
│ + trajectory_status(     │                              │
│       traj_path) → dict  │                              │
└──────────────────────────┘                              │
                                                          │
   ┌──────────────────────────────────────────────────────┘
   ▼
┌──────────────────────────────┐
│        SkillRepo             │  ← <skill_dir> 顶层管理
├──────────────────────────────┤  ← 顶层 .git 删除，只管 <skill>/ 子目录
│ - root: Path                 │  ← ~/.t2s/skill/
├──────────────────────────────┤
│ + __getitem__(name) → Skill  │  (t2s.skill_repo["foo"])
│ + __iter__() → Iter[Skill]   │
│ + __contains__(name) → bool  │
│ + rebuild_index()            │  ← .skill_index.pkl
└──────────┬───────────────────┘
           │ yields
           ▼
┌──────────────────────────────────┐ ◄────────────────┐
│             Skill                │                  │
├──────────────────────────────────┤                  │ wraps
│ - path: Path                     │                  │
│ - sub_git: GitRepo (子仓)        │                  │
│ - candidates: CandidateBuffer ◆──┼─┐                │
│ - canary: CanaryGitOps        ◆──┼─┼─┐ (内部组件,   │
├──────────────────────────────────┤ │ │  不暴露)     │
│ + name: str                      │ │ │              │
│ + read() → str                   │ │ │              │
│ + frontmatter: dict              │ │ │              │
│ + use_count: int                 │ │ │              │
│ + supporting_trajectories()      │ │ │ → list[Trajectory]
│ + recent_ux_scores(side, days)   │ │ │ → list[UxScore]
│ + canary_status() → str          │ │ │ ("main_only"|"staging"|"expired")
└──────────────────────────────────┘ │ │              │
                                     │ │              │
                ┌────────────────────┘ │              │
                ▼                      ▼              │
┌──────────────────────────┐  ┌──────────────────────┐│
│   CandidateBuffer        │  │   CanaryGitOps       ││
│   (per-Skill, internal)  │  │   (per-Skill,        ││
├──────────────────────────┤  │    internal)         ││
│ - skill_path: Path       │  ├──────────────────────┤│
├──────────────────────────┤  │ - skill_path: Path   ││
│ + view() → list[Candidate│  ├──────────────────────┤│
│ + add_or_merge(...)      │  │ + has_staging() → b  ││
│ + ready(threshold)       │  │ + main_sha() → str   ││
│ + archive_stale(...)     │  │ + staging_sha() →str ││
└──────────────────────────┘  │ + promote_to_staging ││
                              │ + merge_staging      ││
                              │ + discard_staging    ││
                              │ + ux_scores(side,    ││
                              │     days)→list[Ux..] ││
                              │ + check_and_decide() ││
                              │   → CanaryDecision   ││
                              └──────────────────────┘│
                                                      │
┌──────────────────────────────────┐ ◄────────────────┼────┐
│         Trajectory               │                  │    │ wraps
├──────────────────────────────────┤                  │    │
│ - path: Path                     │                  │    │
│ - registry: Registry (lazy)      │                  │    │
├──────────────────────────────────┤                  │    │
│ + load(path)  [@classmethod]     │                  │    │
│ + meta: dict                     │                  │    │
│ + is_success: bool               │                  │    │
│ + skill_used: str | None         │  ← 走 registry   │    │
│ + skill_generated: str | None    │                  │    │
│ + canary_side: "main"|"staging"  │                  │    │
└──────────────────────────────────┘                  │    │
                                                      │    │
   ── 检索结果 ─────────────────────────────────────  │    │
   ┌───────────────────┐    ┌──────────────────────┐  │    │
   │     SkillHit      │    │   TrajectoryHit      │  │    │
   ├───────────────────┤    ├──────────────────────┤  │    │
   │ skill: Skill ─────┼────┼─trajectory: ─────────┼──┼────┘
   │ similarity: float │    │       Trajectory ────┼──┘
   └───────────────────┘    │ similarity: float    │
                            └──────────────────────┘


═════════════════════════════════════════════════════════════════════════════
  评估（独立路径）
═════════════════════════════════════════════════════════════════════════════

   ┌────────────────────────────────────────┐
   │             Evaluator                  │
   ├────────────────────────────────────────┤
   │ - llm: LLMClient                       │
   │ - sandbox_config: dict                 │
   ├────────────────────────────────────────┤
   │ + evaluate(skill: Skill,               │
   │     n_runs=3, force_sandbox=False,     │
   │     force_no_sandbox=False)            │
   │     → EvalScore                        │
   │   (内部自动选 L1 或 L2)                │
   │                                        │
   │ + should_merge(score: EvalScore,       │
   │     threshold: float = 6.0) → bool     │
   │   (砍掉 is_new=False 死分支)           │
   └────────────────────────────────────────┘
                       │ returns
                       ▼
   ┌──────────────────────────────────────┐
   │           EvalScore                  │
   │           (dataclass)                │
   ├──────────────────────────────────────┤
   │ tier: "llm" | "sandbox"              │
   │ eval_score: float                    │  ← 归一总分
   │                                      │
   │ # tier="llm" 时填：                  │
   │ scores: dict[str, int] | None        │  ← 7 维分
   │ runs: int | None                     │
   │                                      │
   │ # tier="sandbox" 时填：              │
   │ baseline_pass_rate: float | None     │
   │ skill_pass_rate: float | None        │
   │ delta: float | None                  │
   │ instances: list[str] | None          │
   └──────────────────────────────────────┘


═════════════════════════════════════════════════════════════════════════════
  内部组件（不上 SDK）
═════════════════════════════════════════════════════════════════════════════

   ┌──────────────────────────────────────────────┐
   │            AgentToolkit                      │  ★ 关键消熵
   ├──────────────────────────────────────────────┤  ← 收敛 3 套独立工具
   │ - llm_client                                 │
   │ - allowed_roots: list[Path]                  │
   │ - max_read_bytes: int                        │
   ├──────────────────────────────────────────────┤
   │ + read_file(path) → str                      │
   │ + write_file(path, content) → str            │
   │ + list_files(path) → list[str]               │
   │ + execute_code(code) → str (chat 用)         │
   │   ← 全部带边界检查 + 字节上限                │
   └──────────┬─────────┬─────────┬───────────────┘
              │ used by │         │
   ┌──────────┴───┐ ┌───┴────┐ ┌──┴──────────────┐
   │Distillation- │ │Promot- │ │  ChatAgent      │
   │   Agent      │ │ionAgent│ │                 │
   ├──────────────┤ ├────────┤ ├─────────────────┤
   │ + run(traj)  │ │+ run(  │ │+ run(query, sid)│
   │   → DistillRe│ │  skill,│ │   → stream/answr│
   │              │ │  cands)│ │                 │
   │ 替代         │ │  → bool│ │ 替代            │
   │ agent.run_   │ │        │ │ server.api_chat │
   │ agent_agno   │ │ 替代   │ │ _stream         │
   │              │ │candi-  │ │                 │
   │              │ │dates._ │ │                 │
   │              │ │run_    │ │                 │
   │              │ │skill_  │ │                 │
   │              │ │edit    │ │                 │
   └──────────────┘ └────────┘ └─────────────────┘

   ┌──────────────────────────────────────────────┐
   │            PipelineRunner                    │
   ├──────────────────────────────────────────────┤
   │ - registry, skill_repo, llm, embed, config   │
   ├──────────────────────────────────────────────┤
   │ + run_meta(traj)         → bool              │
   │ + run_embed(trajs)       → None              │
   │ + run_distill(traj)      → DistillResult     │
   │ + run_ux_score(traj)     → UxScoreResult     │
   │   ← 替代 process.process_traj 200+ 行流程    │
   │   ← commit 走 <skill>/.git 子仓              │
   └──────────────────────────────────────────────┘

   ┌──────────────────────────────────────────────┐
   │       DirectoryWatcher (保留)                │
   ├──────────────────────────────────────────────┤
   │ - pipeline: PipelineRunner                   │
   │ - registry: Registry                         │
   ├──────────────────────────────────────────────┤
   │ + start() / stop() / stats: dict             │
   └──────────────────────────────────────────────┘


═════════════════════════════════════════════════════════════════════════════
   Legend
     ◆────  composition (拥有)
     ────►  returns / yields
     ◄────  wraps      (Hit dataclass 包实体)
     ★      关键消熵点
═════════════════════════════════════════════════════════════════════════════
```

### 5.3 SDK Import 一览

```python
# 4 个核心类
from traj2skill import T2S, Skill, Trajectory, Evaluator

# dataclass 类型
from traj2skill.types import (
    WatchDir, SkillHit, TrajectoryHit,
    EvalScore, Candidate, UxScoreResult,
)
```

`Config / Registry / SkillRepo / CandidateBuffer / CanaryGitOps / AgentToolkit / DistillationAgent / PromotionAgent / ChatAgent / PipelineRunner` **不暴露**——通过 `T2S.config / T2S.registry / T2S.skill_repo / Skill.candidates / Skill.canary` 访问。

### 5.4 关键 dataclass schema

```python
@dataclass
class WatchDir:
    id: int
    path: Path
    label: str
    auto_index: bool
    traj_count: int
    indexed_count: int

@dataclass
class SkillHit:
    skill: Skill
    similarity: float

@dataclass
class TrajectoryHit:
    trajectory: Trajectory
    similarity: float

@dataclass
class EvalScore:
    tier: Literal["llm", "sandbox"]
    eval_score: float
    # tier="llm"
    scores: dict[str, int] | None = None
    runs: int | None = None
    # tier="sandbox"
    baseline_pass_rate: float | None = None
    skill_pass_rate: float | None = None
    delta: float | None = None
    instances: list[str] | None = None

@dataclass
class Candidate:
    pattern: str
    kind: Literal["step", "warning", "decision_branch"]
    attach_to: str | None
    supporting_trajs: list[str]
    first_seen: date
    promoted: bool

@dataclass
class UxScoreResult:
    scored: bool                       # False = 已存在（幂等）
    score: int | None
    reasons: str
    decision: dict                     # canary.check_and_decide 返回

@dataclass
class DistillResult:
    """PipelineRunner.run_distill 返回值"""
    action: Literal["merged", "staged", "rejected", "skip",
                    "updated_metadata", "dry_run", "error"]
    changed_skills: list[str]          # 本次改动的 skill 名
    eval_scores: dict[str, EvalScore]  # skill_name → 评分
    error: str | None = None
```

---

## 6. 关键实现细节

### 6.1 AgentToolkit 收敛

当前三处实现（`skill_tools.read_file / write_file`、`candidates._read_file / _write_file`、`server._exec_tool` 内置）的边界各不相同。统一为：

```python
class AgentToolkit:
    def __init__(self, llm_client, *,
                 allowed_roots: list[Path],
                 max_read_bytes: int = 15_000):
        self.llm = llm_client
        self.allowed_roots = [p.resolve() for p in allowed_roots]
        self.max_read_bytes = max_read_bytes

    def _check_root(self, path: Path) -> Path:
        p = path.resolve()
        if not any(p == r or r in p.parents for r in self.allowed_roots):
            raise PermissionError(f"path outside allowed roots: {p}")
        return p

    def read_file(self, path: str) -> str: ...
    def write_file(self, path: str, content: str) -> str: ...
    def list_files(self, path: str) -> list[str]: ...
    def execute_code(self, code: str) -> str: ...   # chat 才装配
```

三个 Agent 各自构造 toolkit 时传不同 `allowed_roots`：
- `DistillationAgent`：`[skill_dir]`
- `PromotionAgent`：`[<skill_dir>/<skill_name>]`（仅写当前 skill）
- `ChatAgent`：`[skill_dir, traj_dir_for_chat_archive]`

### 6.2 PipelineRunner 替代 `process_traj`

现状 `process.process_traj` 有 200+ 行，依次完成 meta 加载 → context init → repo 确保 → run_agent → 共识硬护栏 → promote candidates → commit → eval → frontmatter 写回 → canary 路由 → 索引重建。

升级为：

```python
class PipelineRunner:
    def __init__(self, registry: Registry, skill_repo: SkillRepo,
                 llm, embed, config: dict):
        self.registry = registry
        self.skill_repo = skill_repo
        self.llm = llm
        self.embed = embed
        self.config = config
        self.distiller = DistillationAgent(llm, AgentToolkit(...))
        self.promoter = PromotionAgent(llm, AgentToolkit(...))
        self.evaluator = Evaluator(llm, config["sandbox"])

    def run_distill(self, traj: Trajectory) -> DistillResult:
        # 1. distiller.run(traj) → 改动若干 skill
        # 2. 对每个改动的 skill：
        #    a. 共识硬护栏（source_trajs >= min）
        #    b. promoter.run(skill, ready_candidates)
        #    c. <skill>/.git 子仓 commit
        #    d. evaluator.evaluate(skill) → EvalScore
        #    e. evaluator.should_merge(score) → 走灰度 or revert
        # 3. 返回 DistillResult
```

`process.commit_changes`（顶层 git）删除。每个 skill 独立 commit 到 `<skill>/.git`。

### 6.3 chat 路由 side 选择统一

当前三处实现（`/resolve` 走 `pick_side(p)`、`/chat` 看物化文件、`/chat/stream` 看物化文件）。统一为单一函数 `traj2skill.canary.resolve_side`，**ChatAgent / api_chat / api_chat_stream 三处都调它**：

```python
# traj2skill/canary.py 新增
def resolve_side(skill: Skill, traj_id: str, config: dict) -> tuple[str, Path]:
    """统一返回 (side, skill_md_path)。
       staging 端要求 materialize 已由 PipelineRunner 在路由 staging 时完成；
       此处仅读，不再 lazy materialize。"""
    if not skill.canary.has_staging():
        return "main", skill.path / "SKILL.md"
    side = canary.pick_side(
        traj_id, skill.name, config["canary"]["probability"]
    )
    if side == "staging":
        return "staging", canary.staging_md_path(skill.path)
    return "main", skill.path / "SKILL.md"
```

物化（`materialize_staging`）的发生时机不变——`PipelineRunner` 在路由 staging 时已经物化好；resolve_side 只读不写，避免 chat 路径上的副作用。

### 6.4 Trajectory.skill_used 反查

现状 `trajectories.skill_used` 是 TEXT 字段（逗号分隔），由 `watcher._score_new` 调 `mark_skill_used` 写入。Trajectory 实体读它走 Registry：

```python
class Trajectory:
    @classmethod
    def load(cls, path: Path, registry: Registry | None = None) -> "Trajectory":
        return cls(path=path, _registry=registry)

    @property
    def skill_used(self) -> str | None:
        if self._registry is None:
            return None  # 独立加载模式：无反查能力
        row = self._registry.trajectory_status(self.path)
        return row.get("skill_used") if row else None
```

**Registry 注入路径**：
- `T2S.search_trajectories()` 返回的 `Trajectory` 自动带 `registry=self.registry`
- `Skill.supporting_trajectories()` 返回的 `Trajectory` 自动带 registry（Skill 持有 SkillRepo 引用，间接拿到 T2S.registry）
- 用户主动 `Trajectory.load(path)`（无 registry 参数）→ `skill_used / skill_generated / canary_side` 全返回 None。这是有意的：独立加载模式只读文件本身，不查 DB

**为什么不让 Trajectory 自己 import `t2s.config` 找 DB**：避免 Trajectory 实体持全局状态，保持纯数据视图特性。需要反查的代码路径都从 T2S/Skill 走，自然有 registry。

`Registry.trajectories_using(skill_name)` 反查：

```sql
SELECT t.*, w.path AS watch_dir_path
FROM trajectories t
JOIN watch_dirs w ON t.watch_dir_id = w.id
WHERE t.skill_used LIKE ? OR t.skill_used = ?
```

---

## 7. 删除清单

### 7.1 CLI 子命令

| 命令 | 处理 |
|---|---|
| `index` | 删；自动由 watcher 触发 |
| `search-skill` | 合并入 `search skill` |
| `process` `batch` | 删；watcher 自动 |
| `init` | 删；`SkillRepo` 构造时自动 ensure |
| `status` `validate` | 删 |
| `reindex` | 删；`SkillRepo.rebuild_index()` SDK 调 |
| `eval` | 删；`Evaluator` SDK 调 |
| `skill (12 子动作)` | 全删；通过 SDK + git 命令操作 |
| `show` | 删；`cat <skill>/SKILL.md` |
| `watch` | 删；`serve` 内置 |

### 7.2 死代码

| 位置 | 行为 |
|---|---|
| `chat_app.py` 整个文件 | 删（无 import） |
| `git_lock.acquire_lock / release_lock / make_branch_name / merge_to_main` | 删（0 调用） |
| `canary.cleanup_canary` | 删（0 调用） |
| `canary.promote_main_to_staging` | 删（被 `route_main_history_to_staging` 替代） |
| `skill_eval.should_merge(is_new=False)` 分支 | 删 |
| `process.process_traj` 返回 `staging_queued` 路径 | 删（条件触发只 log，无 return） |
| `skill_tools.update_abstract` shim | 删 |
| v1 lowercase `skill.md` 所有 fallback（`skill_eval.py:158-164` 等 5 处） | 删 |
| v1 `.abstract` 所有 fallback | 删 |
| `process.py` 中 `pass  # lock removed` 占位（10+ 处） | 删 |
| `tasks.py` SSE 路径 `/skills/process/batch/eval` | 删（CLI 都没了） |

### 7.3 模块级删除

- `cli.py`：从 911 行 → 预计 <200 行（5 个子命令）
- `process.py`：核心逻辑迁入 `PipelineRunner`；保留 `Trajectory` 解析等纯函数
- `cmd_eval / cmd_skill / cmd_process / ...` 函数全删

---

## 8. 迁移与影响

### 8.1 用户影响

- **不再支持** `./config.yaml`，找不到 `~/.t2s/config.yaml` 直接抛异常
- **不再支持** `--skill-dir / --traj-dir / --llm-*` flag
- **不读** `~/.aikey`、不读环境变量、无任何 fallback
- 老用户必须手动 `mv ~/.traj2skill ~/.t2s/` + 手编 `~/.t2s/config.yaml`
- README / docs 全部改写

### 8.2 代码影响

- 现有 `tests/` 中过程式 import（如 `from traj2skill.process import process_traj`）部分会失效——`process_traj` 函数被 `PipelineRunner.run_distill` 替代
- `server.py` 内部目前直接 import `skill_tools / candidates / canary` 等模块函数；改为通过 `T2S` 实例访问
- `__init__.py` 重写公开面（见 §5.3）

### 8.3 顶层 git 删除

`<skill_dir>/.git`（顶层）历史遗留，commit 包含全部 skill 的整体快照。删除后：
- 每个 `<skill>/.git` 子仓独立维护 main / staging 分支
- 跨 skill 的"批量回滚"能力丧失（用户层面不需要）
- 用户已有的 `<skill_dir>/.git` 可选自行 `rm -rf`，不强制

---

## 9. 后续设计（本期不做）

### 9.1 评估精度单测脚本

依赖 §5 的 `Evaluator` 类落地后，新增 `tests/test_eval_precision.py`：
- 数据集 `tests/data/eval_precision/cases.jsonl`，每行 `{skill_dir, expected: {dim: [low, high]}, rationale}`
- 跑 `evaluator.evaluate(skill, n_runs=3)`，逐 case 断言中位数落区间内
- 全局 precision 报告
- L2 沙箱单测单独 `tests/test_sandbox_precision.py`，标 `@pytest.mark.slow`

### 9.2 chat 路由统一逻辑下沉

`resolve_side` 当前规划散在 ChatAgent / api_chat / api_chat_stream 三处调用。下一期可考虑收敛为 `Skill.resolve_side_for(traj_id, config)` 方法。

### 9.3 死代码持续清理

v1/v2 schema 共存清理（`migrate.py` 是否仍需保留）放下一期评估。

---

## 10. 验收

本设计落地后应满足：

- [ ] `t2s --help` 列出仅 5 个子命令（serve / registry add|remove|list / search traj|skill）
- [ ] `cli.py` < 200 行
- [ ] `~/.t2s/config.yaml` 不存在时 `t2s serve` 抛 `FileNotFoundError`，不 fallback
- [ ] `from traj2skill import T2S, Skill, Trajectory, Evaluator` 成功
- [ ] 单一 `AgentToolkit` 实现，三个 Agent 类共用
- [ ] `<skill_dir>/.git`（顶层）不再被任何代码路径访问
- [ ] §7 列出的死代码全部从代码库消失（`grep` 0 匹配）
- [ ] 现有 watcher 自动 pipeline 行为不变（traj 落盘 → meta → embed → distill → ux_score）
- [ ] `t2s search skill <q>` 输出包含 `used` 和 `ux_avg(N)` 列
