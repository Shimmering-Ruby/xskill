# traj2skill

从 AI Agent 执行轨迹中自动蒸馏可复用的 Skill。

## 架构

```
轨迹 (.md) → LLM 提取 meta → 向量索引 → Skill Agent 分析 → 生成 skill.md → LLM eval → 合入
```

### 核心模块

| 文件 | 职责 |
|------|------|
| `traj2skill.py` | 主入口，6 个子命令（process/batch/init/reindex/status/eval） |
| `index.py` | 轨迹索引构建（增量，并发 LLM 提取 meta + embedding） |
| `search.py` | 轨迹检索（向量相似度） |
| `modules/llm_client.py` | LLM + Embedding 客户端（ARK/OpenAI 兼容） |
| `modules/skill_tools.py` | Skill Agent 的 6 个工具函数 |
| `modules/skill_eval.py` | 两层评估：LLM 7 维打分（SWE-bench 沙箱占位） |
| `modules/git_lock.py` | 文件锁 + git 版本管理 |

### 数据流

```
data/{dataset}/traj_XXXX.md       ← 原始轨迹
data/{dataset}/traj_XXXX.md.meta  ← LLM 提取的结构化 meta（增量）
data/{dataset}/index.pkl          ← 向量索引（增量）
skill/{skill_name}/skill.md       ← 蒸馏出的 skill
skill/{skill_name}/.abstract      ← 自动生成的摘要 + eval 结果
skill/.skill_index.pkl            ← skill 向量索引
```

## 使用

### 前置

```bash
pip3.11 install -r requirements.txt
```

配置 `config.yaml`（参考 `.key` 格式）：

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
```

### 索引轨迹

```bash
# 索引单个数据集
python3.11 index.py --dataset swe_smith_dataset

# 索引全部数据集
python3.11 index.py --all

# 调整并发数
python3.11 index.py --all --concurrency 20
```

### 检索轨迹

```bash
# 自然语言查询
python3.11 search.py --dataset swe_smith_dataset --query "修复 Django 表单验证"

# 用轨迹作为查询
python3.11 search.py --dataset swe_smith_dataset --traj data/swe_smith_dataset/traj_0042.md
```

### Skill 生成

```bash
# 初始化 skill 仓库
python3.11 traj2skill.py init

# 处理单条轨迹
python3.11 traj2skill.py process --traj data/swe_smith_dataset/traj_0042.md

# 批量处理
python3.11 traj2skill.py batch --dataset swe_smith_dataset --max 10

# 查看状态
python3.11 traj2skill.py status

# 手动 eval
python3.11 traj2skill.py eval --skill fix_orm_query --n-runs 5
```

### Skill 生成流程

```
1. 读取轨迹 + meta
2. 获取文件锁
3. 启动 agno Agent（流式输出思考过程）
   - search_skills → 检索已有 skill
   - search_similar_trajs → 检索相似轨迹
   - 决策：创建新 skill / 更新已有 / 跳过
   - create_skill + write_file → 写入 skill.md
4. 检测变更，commit
5. LLM 7 维评估（trigger_precision, step_actionability, granularity, generalizability, pitfall_quality, faithfulness, structural_quality）
6. eval ≥ 6.0 → 生成 abstract + 重建索引
7. eval < 6.0 → revert
8. 释放锁
```

## 设计决策

- **增量索引**：meta 提取和 embedding 都支持断点续跑，单条失败不影响全局
- **meta 质量校验**：intent/summary/tags 必须非空且有实质内容，不合格自动重提
- **文件锁而非 git 分支锁**：`.lock` 文件写 PID，atexit/signal 自动清理，死进程检测
- **eval 后才生成摘要**：agent 只写 skill.md，abstract 和索引在 eval 通过后由系统自动生成
- **无 TF-IDF fallback**：embedding API 不可用直接报错，不做隐性降级
