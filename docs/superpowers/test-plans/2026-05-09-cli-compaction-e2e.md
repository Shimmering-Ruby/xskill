# xskill CLI 紧凑化 E2E 测试方案

- 日期：2026-05-09
- 关联设计：[2026-05-09-cli-compaction-design.md](../specs/2026-05-09-cli-compaction-design.md)
- 验收原则：CLAUDE.md 第 2 条——单测过 + 用户端到端通路实际跑通

---

## 1. 测试视角

从**新用户**视角（无 xskill 历史包袱）。`~/.xskill/` 不存在，`./config.yaml` 不存在。任何 fallback 都视为 BUG。

---

## 2. 用户路径分类

### 路径 A：CLI 交互（shell 用户）
最常见路径。覆盖 `serve / registry / search`。

### 路径 B：SDK 编程（Python 用户）
覆盖 `XSkill / Skill / Trajectory / Evaluator` 4 个公开类。

### 路径 C：HTTP / Web UI（chat 用户）
覆盖 `serve` 起的 FastAPI + UI；含 chat 反馈轨迹回灌闭环。

---

## 3. 测试矩阵

### 3.1 路径 A：CLI

| # | 操作 | 预期 | 验证手段 |
|---|---|---|---|
| A1 | `xskill --help` | 仅列 5 子命令（serve / registry / search） | `grep -c "^\s\+\(serve\|registry\|search\)" --help 输出` |
| A2 | `~/.xskill/config.yaml` 不存在时跑 `xskill registry list` | 抛 `FileNotFoundError` 含明确路径 | `1>&2` 含 `~/.xskill/config.yaml` 字串；exit 非 0 |
| A3 | `xskill registry add /tmp/no_such_dir` | 抛"不是目录"错；exit 非 0 | shell exit code |
| A4 | `xskill registry add /tmp/xskill-e2e-data`（先 mkdir） | 输出 `Registered: /tmp/...`；`~/.xskill/registry.db` 出现该 path | sqlite 查询 |
| A5 | `xskill registry list` | 列出 A4 注册的目录 + traj_count=0 | stdout 含路径 |
| A6 | drop `traj_0001.md` 到 A4 目录，等 watcher 一轮 | `~/.xskill/registry.db` trajectories 表新增一条；status 走 discovered → meta_done → indexed | sqlite 查询；watch 时序 |
| A7 | `xskill search traj "django form"` | 文本表格输出，含列 `similarity status skill_used side path` | regex 验证输出列数 |
| A8 | `xskill search skill "form validation"` | 文本表格输出，含列 `similarity name used ux_avg(N) canary` | regex 验证 |
| A9 | `xskill registry remove /tmp/xskill-e2e-data` | 输出 `Removed.`；db 中目录消失 | sqlite |
| A10 | `xskill` 无参 | 打印 help，exit 1 | exit code |
| A11 | grep `--config / --skill-dir / --traj-dir / --llm-` 在 `--help` 输出中 | 0 命中（已删） | grep |

### 3.2 路径 B：SDK

| # | 操作 | 预期 | 验证手段 |
|---|---|---|---|
| B1 | `from xskill import XSkill, Skill, Trajectory, Evaluator` | 全部 import 成功 | python -c |
| B2 | `XSkill()` | 默认从 `~/.xskill/config.yaml` 加载；返回实例 | repr |
| B3 | `XSkill(config_path=Path("/tmp/foo.yaml"))` 不存在 | `FileNotFoundError` | except |
| B4 | `xskill.registry.add(path)` → 返回 `WatchDir` dataclass，字段齐全 | `result.id, result.path, result.traj_count` 可访问 | assert |
| B5 | `xskill.search_skills("foo")` 返回 `list[SkillHit]` | 每条 `.skill: Skill` `.similarity: float` | isinstance |
| B6 | `skill.read()` / `skill.frontmatter` / `skill.use_count` / `skill.recent_ux_scores("main", 30)` | 全部可访问；类型正确 | type check |
| B7 | `skill.candidates` 返回 `list[Candidate]` | 元素 dataclass 字段齐全 | type check |
| B8 | `skill.canary_status()` ∈ {"main_only", "staging_active", "expired"} | 字符串值合规 | assert in set |
| B9 | `Trajectory.load(path)` 单独加载 | `traj.meta` 可读；`traj.skill_used` 为 None（无 registry） | isinstance |
| B10 | `Trajectory.load(path, registry=xskill.registry)` | `traj.skill_used` 走 DB 反查 | sqlite |
| B11 | `Evaluator(xskill.config["llm"], xskill.config["sandbox"]).evaluate(skill)` | 返回 `EvalScore`；`tier ∈ {"llm", "sandbox"}` | type check |
| B12 | grep `from xskill.process import process_traj` 在新 SDK | 该函数仍可工作（内部仍存在，watcher 用） | python import |

### 3.3 路径 C：HTTP + chat

| # | 操作 | 预期 | 验证手段 |
|---|---|---|---|
| C1 | `xskill serve` 起来 | 8000 端口监听；`/api/v1/health` 返回 200 | `curl http://localhost:8000/api/v1/health` |
| C2 | `xskill serve` 自动注册 chat 归档目录 | `~/.xskill/registry.db` 含 `chat_app_demo` watch_dir | sqlite |
| C3 | watcher 后台线程跑起来 | `/api/v1/watcher/status` 返回 stats | curl |
| C4 | `POST /api/v1/chat` 用一条 query | 返回 LLM 回复；retrieved_skill 字段含命中 skill | curl + jq |
| C5 | `POST /api/v1/chat/archive` 写一条 traj | 文件落盘到 chat 归档目录；带 `<!-- xskill:skill=... side=... sha=... -->` header | ls |
| C6 | 等 watcher 下一轮（30s） | C5 的 traj 进入 trajectories 表；调用 ux_score 打分 | sqlite + `.ux_scores.jsonl` 新增 |
| C7 | 浏览 `http://localhost:8000/`（playwright） | UI 加载成功 | playwright snapshot |

---

## 4. 死代码清理验证

| # | 操作 | 预期 |
|---|---|---|
| D1 | `grep -rn chat_app src/xskill/` | 0 命中 |
| D2 | `grep -rn acquire_lock\|release_lock\|make_branch_name\|merge_to_main src/xskill/git_lock.py` | 0 命中（函数全删）|
| D3 | `grep -rn cleanup_canary\|promote_main_to_staging src/xskill/canary.py` | 0 命中 |
| D4 | `grep -rn 'is_new=False' src/xskill/` | 0 命中 |
| D5 | `grep -rn 'staging_queued' src/xskill/` | 0 命中（process.py 死路径删） |
| D6 | `grep -rn '"skill\.md"' src/xskill/` (lowercase) | 0 命中（v1 fallback 全删） |
| D7 | `grep -rn '\.abstract' src/xskill/` | 仅 migrate.py 保留（其他 fallback 全删） |
| D8 | `grep -rn '~/\.xskill\|\.xskill' src/xskill/` | 0 命中（路径全 ~/.xskill/） |
| D9 | `grep -rn 'cwd.*config\.yaml' src/xskill/` | 0 命中（无 cwd fallback） |
| D10 | `wc -l src/xskill/cli.py` | < 200 行 |

---

## 5. 端到端冒烟（最关键）

**单一长测**：模拟一个新用户从安装到看到 skill 被使用的完整流程。

```bash
# 0. 准备：删旧状态
rm -rf ~/.xskill ~/.xskill

# 1. 装 xskill
pip install -e .

# 2. 创建 config（按 docs 提供的模板）
mkdir -p ~/.xskill
cp examples/config.yaml.template ~/.xskill/config.yaml
# 用户手编 api_key

# 3. 注册一个数据目录（用现有 SWE 数据集）
xskill registry add /home/admin/xskill/data/swe_smith_dataset

# 4. 起 daemon
xskill serve &
SERVE_PID=$!

# 5. 等 watcher 处理几条（约 60s）
sleep 90

# 6. 查 search
xskill search skill "form validation" --top-k 3
xskill search traj "django" --top-k 3

# 7. 起 chat（http POST）
curl -s -X POST http://localhost:8000/api/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"我的 form 表单验证有 bug","session_id":"e2e-test"}' | jq '.'

# 8. 写一条 chat 反馈 traj
curl -s -X POST http://localhost:8000/api/v1/chat/archive \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"e2e-test","skill":"...","side":"main","sha":"...","content":"..."}'

# 9. 等 watcher 处理反馈轨迹（约 60s），ux_score 应该被打
sleep 90
sqlite3 ~/.xskill/registry.db "SELECT skill_used, canary_side, ux_score FROM trajectories WHERE filename LIKE 'traj_e2e%'"

# 10. 收
kill $SERVE_PID
```

**通过条件**：
- 步骤 6：`xskill search skill` 至少返回 1 条结果，含 `used` 列数字
- 步骤 7：chat 返回包含 LLM 回复 + retrieved_skill 字段
- 步骤 8：归档文件落盘
- 步骤 9：trajectories 表含归档 traj 且 `ux_score` 有值（1-10）

任何一步失败，反向修复直到通过。

---

## 6. 测试执行约束

- **不需要**完整 SWE 沙箱评估（L2 docker 跑得慢，跳过；L1 LLM 打分够覆盖）
- **不需要**完整向量索引重建（用现有 `data/swe_smith_dataset/index.pkl`）
- chat UI playwright 验证只做"页面加载成功"，不深入交互
- `~/.xskill/config.yaml` 用真实 DeepSeek key（`~/.aikey`）手贴一份进去；不读取环境变量

---

## 7. 验收 sign-off

全部步骤通过 + 死代码清理验证全过 + `xskill --help` 视觉检查后，记录到 commit message 标"E2E 通过"。
