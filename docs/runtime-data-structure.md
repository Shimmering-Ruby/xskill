# Trajectory 运行时数据结构

## 文件三件套

每条轨迹由磁盘上的三个文件组成，以 `traj_cc_dsv4_890da9d9` 为例：

```
data/watch/claude_code/traj_cc_dsv4_890da9d9.md       # 标准化对话原文（markdown）
data/watch/claude_code/traj_cc_dsv4_890da9d9.md.meta   # 结构化元数据（JSON，key 级）
data/watch/claude_code/traj_cc_dsv4_890da9d9.json       # 上游原始格式（adapter 产物，JSON）
```

### `.md` — 对话体（主体）

标准化后的 agent 对话历史，markdown 格式，按 `## User` / `## Assistant` 分段：

```markdown
<!-- xskill:skill=fix_django_migration side=staging sha=a1b2c3d4 -->

## User
帮我修复 Django migration 冲突

## Assistant
我来分析你的 migration 文件。首先检查 ...

## User
具体来说，我有两个 migration 都修改了同一张表 ...
```

### `.md.meta` — 结构化 meta

上游 adapter 在落地时提取出的 key-value 元数据，**所有 key 都是生产可见的**（区别于 `.json` 里可能有 adapter 内部字段）：

```json
{
  "success": true,
  "source": "claude_code",
  "source_model": "deepseek-v4-flash",
  "user_intent_count": 2,
  "adapter_version": "1.0"
}
```

### `.json` — 上游原始数据

adapter 从特定 agent 原始格式转来的完整 JSON 结构，**不做 schema 承诺**，给下游 ETL 的 `raw_json` 回退用：

```json
{
  "conversation_id": "890da9d9",
  "model": "deepseek-v4-flash",
  "turns": [
    {"role": "user", "content": "帮我修复 Django migration 冲突"},
    {"role": "assistant", "content": "我来分析你的 migration 文件..."}
  ],
  "total_tokens": 2843,
  "adapter": "claude_code_dsv4"
}
```

---

## 类模型 — `Trajectory` (src/xskill/pipeline/trajectory.py:28)

文件三件套的视图层，**不持有 LLM、不持有 DB session**，只做数据访问：

```python
class Trajectory:
    def __init__(self, path, registry=None):
        self.path = Path(path)          # → traj_cc_dsv4_890da9d9.md
        self._registry = registry       # 可选 DB 反查句柄
        self.md_text                    # → path.read_text() → 整段 markdown
        self.meta                       # → path + ".meta"  → JSON dict
        self.raw_json                   # → path.with_suffix(".json") → JSON dict
        self.is_success                 # → meta.get("success")  bool
        self.skill_used                 # → _row["skill_used"]       "fix_django,git_ops"
        self.skill_generated            # → _row["skill_generated"]  "fix_django_migration"
        self.canary_side                # → _row["canary_side"]      "staging" | "control"
        self.status                     # → _row["status"]  "discovered" | "indexed" | "promoted"
```

### 构造 & 加载

```python
# 方式一：直接从文件构造（无 DB 反查，skill_used 等全部返回 None）
traj = Trajectory(Path("data/watch/claude_code/traj_cc_dsv4_890da9d9.md"))

# 方式二：关联 Registry（可反查 skill_used / skill_generated / canary_side）
traj = Trajectory.load("data/watch/.../traj_cc_dsv4_890da9d9.md", registry=reg)
```

---

## DB 层 — SQLite `trajectories` 表

`registry.trajectory_status(traj_path)` 返回整行 dict：

| 列 | 示例值 | 说明 |
|---|---|---|
| `id` | 42 | PK |
| `watch_dir_id` | 5 | 所属 watch_dir FK |
| `filename` | `traj_cc_dsv4_890da9d9.md` | 文件名（含扩展） |
| `has_meta` | 1 | 是否存在 `.md.meta` |
| `has_embedding` | 1 | 是否已向量化 |
| `status` | `"promoted"` | `discovered` → `indexed` → `promoted` |
| `process_action` | `"embed+canary"` | 记录 pipeline 对这条轨迹执行的操作 |
| `skill_generated` | `"fix_django_migration"` | 从这条轨迹生成的 skill 名 |
| `skill_used` | `"fix_django,git_ops"` | 这条轨迹执行时用过的 skill |
| `canary_side` | `"staging"` | 金丝雀侧标记 |
| `source_model` | `"deepseek-v4-flash"` | 生成对话的模型 |
| `source_harness` | `"claude_code"` | 来源 agent 平台 |
| `ux_score` | 8.5 | UX 评分 1-10 |
| `error_msg` | `null` | 失败原因 |
| `retry_count` | 0 | 重试次数 |

---

## Trajectory → Skill 三阶段晋升

```
[文件落地]
     │
     ▼
discovered ──→ indexed ──→ promoted
     │            │
     │            ├── pipeline/atom.py  → 拆 User intent
     │            ├── pipeline/embed.py → 向量化
     │            └── skill/candidate   → 提取 candidate
     │
     └── skill/promote.py  → 候选成熟 → 写 skill 文件 → status = promoted
```

## 典型运行时快照（mocked）

```python
# ── 磁盘文件 ──
traj.md_text = """\
## User
帮我写一个 FastAPI CRUD

## Assistant
from fastapi import APIRouter, Depends
router = APIRouter()
...
"""

traj.meta = {"success": True, "source": "claude_code"}
traj.raw_json = {"turns": [...], "total_tokens": 1024}

# ── DB 反查 ──
traj.status           # "promoted"
traj.skill_generated  # "fastapi_crud_generator"
traj.skill_used       # "python_boilerplate"
traj.canary_side      # "staging"
```
