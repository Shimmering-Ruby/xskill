# traj2skill

从 AI Agent 执行轨迹中自动蒸馏可复用的 Skill。

## 安装

```bash
pip install -e .
```

## CLI: `t2s`

### 轨迹入库

```bash
t2s index /path/to/trajectories                    # 索引指定目录
t2s index --dataset swe_smith_dataset              # 索引 data/ 下的数据集
t2s index --all                                    # 索引全部数据集
t2s index /path/to/trajectories -c 20              # 20 并发
```

入库产物存放在**轨迹目录下**：

```
/path/to/trajectories/
├── traj_0000.md              ← 原始轨迹（输入）
├── traj_0000.json            ← 原始元数据（可选输入）
├── traj_0000.md.meta         ← 提取的结构化 meta（入库产出）
└── index.pkl                 ← 向量索引（入库产出）
```

### 轨迹检索

```bash
t2s search /path/to/trajectories --query "Django 表单"
t2s search --dataset swe_smith_dataset --traj data/.../traj_0042.md
t2s search-skill --query "表单验证"                # 检索已有 skill
```

### Skill 生成

```bash
t2s init                                           # 默认在 cwd/skill 初始化
t2s init /path/to/skill_repo                       # 在指定路径初始化
t2s process data/swe_smith_dataset/traj_0000.md    # 处理单条轨迹
t2s batch data/swe_smith_dataset --max 10          # 批量处理
```

Skill 产物存放在 **skill 目录**下，每个 skill 一个独立目录：

```
skill/
├── fix_orm_n_plus_one/
│   ├── skill.md              ← Skill 描述文件（trigger + steps + pitfalls）
│   └── .abstract             ← 摘要 + eval 结果（JSON）
├── fix_form_validation/
│   ├── skill.md
│   └── .abstract
└── .skill_index.pkl          ← Skill 向量索引
```

### Skill 评测

```bash
t2s eval --skill fix_xxx                           # LLM 多维打分
t2s eval --skill fix_xxx --sandbox                 # 沙箱闭环评测（Docker）
t2s eval --skill fix_xxx --sandbox --n-runs 3      # 控制试验次数
t2s eval --list                                    # 列出所有 eval 历史
```

### Skill 版本管理

```bash
t2s skill list                                     # 列出所有 skill
t2s skill show fix_xxx                             # 查看详情
t2s skill log fix_xxx                              # 版本历史
t2s skill diff fix_xxx                             # 当前 vs 上一版本
t2s skill diff fix_xxx v1 v3                       # 指定版本对比
t2s skill rollback fix_xxx                         # 回滚到上一版本
t2s skill rollback fix_xxx --version v2            # 回滚到指定版本
t2s skill freeze fix_xxx                           # 冻结，阻止自动更新
t2s skill unfreeze fix_xxx                         # 解冻
t2s skill delete fix_xxx                           # 删除（带确认）
t2s skill export fix_xxx -o /path/output           # 导出
t2s skill import --source /path/to/skill_dir       # 导入
```

### 调试 / 查看

```bash
t2s status                                         # skill 仓库状态
t2s show --skill fix_xxx                           # 打印 skill 详情
t2s show --traj data/.../traj_0000.md              # 打印轨迹 meta
t2s validate /path/to/trajectories                 # 校验轨迹格式
t2s --debug process ...                            # 详细日志模式
```

### 全局选项

```bash
t2s --debug                    # 详细日志
t2s --quiet                    # 安静模式
t2s --config path.yaml         # 指定配置文件
t2s --skill-dir /path          # 覆盖 skill 目录
t2s --traj-dir /path           # 覆盖轨迹目录
t2s --llm-base-url URL         # 覆盖 LLM 地址
t2s --llm-model MODEL          # 覆盖 LLM 模型
t2s --llm-key KEY              # 覆盖 LLM 密钥
```

### 环境变量

| 变量 | 作用 | 默认值 |
|------|------|--------|
| `T2S_TRAJ_DIR` | 轨迹目录 | `cwd/data` |
| `T2S_SKILL_DIR` | Skill 目录 | `cwd/skill` |
| `T2S_CONFIG` | 配置文件路径 | `cwd/config.yaml` |

优先级：CLI flag > 环境变量 > config.yaml > 默认值

## 配置

`config.yaml`:

```yaml
llm:
  base_url: "https://ark.cn-beijing.volces.com/api/v3"
  model: "doubao-seed-2-0-mini-260215"
  api_key: "your-key"

embedding:
  base_url: "https://ark.cn-beijing.volces.com/api/v3"
  model: "doubao-embedding-vision-251215"
  api_key: "your-key"
  dim: 0

sandbox:
  enabled: true
  max_instances: 5
  n_trials: 10
  timeout_per_trial: 300
```

## SDK 用法

traj2skill 的所有核心能力都可通过 Python import 使用：

```python
import traj2skill

# 配置
traj2skill.load_config()
traj2skill.set_overrides(skill_dir="/data/skills", traj_dir="/data/trajectories")

# 轨迹入库
from traj2skill import create_llm_client, create_embed_client, index_dataset
config = traj2skill.load_config()
llm = create_llm_client(config)
embed = create_embed_client(config)
index_dataset(Path("/data/trajectories"), llm, embed)

# 轨迹检索
from traj2skill import search
results = search(Path("/data/trajectories"), "Django 表单验证", top_k=5, config=config)

# Skill 生成
from traj2skill import process_traj
result = process_traj("/data/trajectories/traj_0042.md", config)

# Skill 评测
from traj2skill import run_eval, should_merge
eval_result = run_eval(Path("skill/fix_xxx"), llm, config=config)
if should_merge(eval_result, is_new=True):
    print("pass")

# Skill 管理
from traj2skill import list_skills, show_skill, rollback_skill, freeze_skill
skills = list_skills(Path("skill"))
info = show_skill(Path("skill"), "fix_xxx")
rollback_skill(Path("skill"), "fix_xxx", version="v1")
freeze_skill(Path("skill"), "fix_xxx")

# 沙箱评测
from traj2skill import get_sandbox, available_sandboxes
print(available_sandboxes())  # ['swe_smith']
sb = get_sandbox("swe_smith")
tasks = sb.load_tasks(["instance_id_here"])
result = sb.evaluate(Path("skill/fix_xxx"), ["instance_id"], llm_config=config["llm"])

# Git 管理
from traj2skill import ensure_repo
ensure_repo("/path/to/skill_repo")
```

## 轨迹格式规范

轨迹目录下的文件契约：

| 文件 | 必须 | 说明 |
|------|------|------|
| `traj_*.md` | 是 | 轨迹内容（markdown 格式，包含 User/Assistant/Tool 交互记录） |
| `traj_*.json` | 否 | 元数据（query, success, tool_names, raw_metadata 等） |

可通过 `t2s validate /path` 校验目录是否符合规范。

## 包结构

```
src/traj2skill/
├── __init__.py         # SDK 导出
├── cli.py              # 统一 CLI 入口 (t2s)
├── config.py           # 路径管理 + 配置加载
├── index.py            # 轨迹索引（增量）
├── search.py           # 轨迹检索
├── process.py          # Skill 生成流程
├── agent.py            # agno Agent（SYSTEM_PROMPT + 流式执行）
├── log.py              # StreamLog
├── llm_client.py       # LLM + Embedding 客户端
├── git_lock.py         # 文件锁 + git 版本管理
├── skill_tools.py      # Agent 工具函数
├── skill_eval.py       # 多维评价 + 沙箱评测
├── skill_manager.py    # Skill 版本管理
└── sandbox/            # 可扩展沙箱框架
    ├── base.py         # 抽象基类 (Sandbox, TaskSpec, SandboxResult)
    ├── registry.py     # @register 装饰器 + get_sandbox 工厂
    └── swe_smith.py    # SWE-smith Docker 沙箱实现
```
