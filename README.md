# traj2skill

从 AI Agent 执行轨迹中自动蒸馏可复用的 Skill。

## 安装

```bash
pip install -e .
```

## CLI: `t2s`

```bash
# 轨迹入库
t2s index /path/to/trajectories                    # 索引指定目录
t2s index --dataset swe_smith_dataset              # 索引 data/ 下的数据集
t2s index --all                                    # 索引全部数据集

# 轨迹检索
t2s search /path/to/trajectories --query "Django 表单"
t2s search --dataset swe_smith_dataset --traj data/.../traj_0042.md

# Skill 生成
t2s init                                           # 初始化 skill 仓库
t2s process /path/to/traj_0042.md                  # 处理单条轨迹
t2s batch /path/to/trajectories --max 10           # 批量处理

# Skill 评测
t2s eval --skill fix_xxx                           # LLM 打分
t2s eval --skill fix_xxx --sandbox                 # 沙箱 A/B 评测

# Skill 版本管理
t2s skill list                                     # 列出所有 skill
t2s skill show fix_xxx                             # 查看详情
t2s skill log fix_xxx                              # 版本历史
t2s skill diff fix_xxx                             # 版本差异
t2s skill rollback fix_xxx                         # 回滚
t2s skill freeze fix_xxx                           # 冻结
t2s skill export fix_xxx -o /path/output           # 导出

# 调试 / 查看
t2s status                                         # skill 仓库状态
t2s show --skill fix_xxx                           # 打印 skill 详情
t2s show --traj /path/to/traj_0042.md              # 打印轨迹 meta
t2s search-skill --query "xxx"                     # 检索已有 skill
t2s validate /path/to/trajectories                 # 校验轨迹格式

# 全局选项
t2s --debug ...                                    # 详细日志
t2s --config path.yaml                             # 指定配置文件
t2s --skill-dir /path                              # 覆盖 skill 目录
t2s --traj-dir /path                               # 覆盖轨迹目录
```

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

也可通过环境变量配置：

```bash
export T2S_TRAJ_DIR=/path/to/trajectories
export T2S_SKILL_DIR=/path/to/skills
export T2S_CONFIG=/path/to/config.yaml
```

## 包结构

```
src/traj2skill/
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
    ├── base.py         # 抽象基类
    ├── registry.py     # 沙箱注册表
    └── swe_smith.py    # SWE-smith 实现
```
