# xskill 阈值与门槛清单

本文档总结 xskill v2（AtomTask 流水线）当前实现下所有的硬门槛、判定阈值、
分档表与入参约束。**面向运维和后续阅读**，按系统链路从上到下组织；不复述代码，
只描述当前生效的规则与可配置项的默认值。

调整任一项需评估对全局质量与性能的影响——`ATOM_PROMOTION_THRESHOLD` 改 5 会让
劣质 skill 横生，改 20 会让 skill 几乎永远不诞生；canary `min_samples` 改大会
让翻牌极慢，改小会让噪声样本误升 staging。

---

## 1. TaskAgent —— Trajectory → AtomTask 拆分

| 项 | 取值 | 含义 |
|---|---|---|
| `max_context_chars` | 30000 | 单次喂 LLM 的 delta 上限；超出截断到该长度 |
| offset 单调性 | 强制 | `offset_start ≥ 上一 atom.offset_end` 且 `offset_end > offset_start` |
| 必填字段 | 强制 | `<atom>` 内 `offset_start / offset_end / intent / summary` 全部不可省 |
| 解析失败处理 | raise ValueError | 不写 fallback；watcher 据此把 traj 转 error 状态重试 |

**ux_score 严格 1-10 分档表**（TaskAgent 自评，与后续灰度打分同一份）：

| 分 | 语义 |
|---|---|
| 10 | 一次到位：用户提需求 → agent 一步给出正确产出 → 用户无澄清/无负面情绪 |
| 9 | 仅一处细节澄清，后续顺畅 |
| 8 | 正确完成但绕了 1 个小弯（多读一个文件、命令小修一次） |
| 7 | 正确完成；用户 2-3 次澄清/修正；无明显不耐烦 |
| 6 | 完成度边界（用户表达"这就行吧"之类不完全满意） |
| 5 | 部分完成：核心需求达成但遗漏明显细节 |
| 4 | 多次错误后才接近正确；用户已用否定词 ≥2 次 |
| 3 | 任务勉强完成但用户明显失望 |
| 2 | 任务未完成 / agent 反复触发 blocker / 用户放弃 |
| 1 | 完全失败 / 引发副作用（删错文件、改坏代码、误推送等） |

`used_skills` 非空时的覆盖规则：调了 skill 一步到位 → 起步 8；调了 skill 但
绕弯/错误 → ≤5。

---

## 2. TaskClusterAgent —— AtomTask 归类决策

| 项 | 取值 | 含义 |
|---|---|---|
| `sysprompt_budget_chars` | 25000 | skill 路由表 sysprompt 预算（约 128k context 的 20%） |
| desc 截断下限 | 75 字符（≈25 token） | 每条 desc 平摊预算低于此值时整批 desc 丢弃只留 name |
| desc 单条上限 | 300 字符 | 即便预算够也截至该长度 |
| name 上限 | 无限制 | 任何情况都全量保留 skill 名（否则模型完全看不到候选） |

**weightscore 严格 0-10 分档表**（cluster agent 写进 candidates buffer 的分数）：

| 分 | 语义 |
|---|---|
| 10 | 一个 atom 单独支撑该 skill 核心场景（罕见，含可机械执行的、跨多类问题成立的修复决策）；立即触发 SkillEdit |
| 8-9 | 高质量贡献：完整覆盖关键阶段，含具体命令/路径/函数名 + 可核验产出；两个 8 分相加即触发 SkillEdit |
| 6-7 | 中等贡献：只覆盖子阶段或某 warning，需其他 atom 补齐 |
| 4-5 | 弱贡献：相关问题但执行细节模糊，进 candidates 但不期望单独触发 |
| 2-3 | 边缘相关：不写 |
| 1 | 完全不相关：不写 |

**新建 skill 门槛**：单 atom weightscore < 7 不允许新建（防 skill 列表被低质 atom 污染）。

---

## 3. Candidates Buffer —— Skill 触发判定

| 项 | 取值 | 含义 |
|---|---|---|
| `ATOM_PROMOTION_THRESHOLD` | 10 | 单 skill candidates buffer 中所有 pending 项的 `weightscore_total` 累加 ≥ 10 触发 SkillEdit |
| promoted 重置 | 单次触发后整批标 promoted | 即便 agent 编辑失败也标完，避免反复重触发同一批信号 |
| 同 atom 多次 add | 累加 | `add_task_to_skill` 重复调以同一 atom_id 时 `weightscore_total` 相加 |

---

## 4. SkillEditAgent —— SKILL.md 写入

| 项 | 取值 | 含义 |
|---|---|---|
| SKILL.md 行数 | ≤ 400 | 长参考材料放 `references/`、脚本放 `scripts/` |
| `write_file` 路径范围 | 强制限定在 skill_dir 下 | 越界返回 error |
| frontmatter `created` 校验 | 自动 sanitize | 未来日期 / 非 ISO 自动覆盖成今天；保留合法过去日期 |
| frontmatter `last_updated` | 自动覆盖 | 每次写入都设为当前时间 |
| frontmatter 引用语义 | `source_atoms` 字段 | 引用 atom_id 列表（旧 `source_trajs` 已废弃） |

**已删除（v1 残留）**：`source_trajs ≥ 3` gate、N/M 条轨迹 warning 校验、SWE-smith instance_id 拒绝规则。质量保障已下沉到 candidates 阈值与 cluster agent 评分。

---

## 5. Watcher 流水线

状态机：`discovered → splitting → split_done → indexed → clustering → done`
（异常 → `error`，超过 `max_retries` 不再重试）

| 项 | 取值 | 含义 |
|---|---|---|
| `poll_interval` | 30s（config 可改） | 主循环扫描周期 |
| `max_concurrent` | 30 | ThreadPoolExecutor 工作线程数 |
| in-flight 上限 | `max_concurrent * 3` | `_futures` 超过此值停止提交新任务 |
| `cold_start_threshold` | 3 | 未 indexed 的 traj 数 ≥ 此值时 cluster 阶段全部 defer |
| `max_retries` | 3 | error 状态超过此次数不再被重新提交 |
| 僵尸清理 | 启动时回退 | `splitting` / `clustering` 状态在 DB 但无对应 in-flight future 时回到前一阶段 |

---

## 6. ux_score (atom 粒度灰度打分)

| 项 | 取值 | 含义 |
|---|---|---|
| 分档表 | 复用 TaskAgent 同一份 1-10 表 | 包含 `used_skills` 起步/降档规则 |
| body 截断 | 6000 字符 | `context_prefix + raw_segment` 超长时头尾各取一半 |
| 解析失败处理 | 返回 `score=None` | 不落盘，不影响后续 atom 的打分 |

---

## 7. AtomCanary —— 灰度判定与翻牌

`.ux_scores.jsonl` 主键：`(atom_id, skill_name, side)` 三元组幂等去重。

| 项 | 取值 | 含义 |
|---|---|---|
| `probability` | 0.2 | 检索命中时以此概率把 staging 版本路由给当前轨迹 |
| `min_samples` | 5 | main / staging 两侧各 ≥ 5 条 ux 分才进入判定 |
| `max_days_hold` | 14 | staging 存活超过 14 天仍未集齐样本 → discard |
| 翻牌规则 | staging 均分 ≥ main 均分 → promote；否则 reject |
| 触发时机 | 每条 atom ux 分入库后调一次 `check_and_decide` |

排队（按 user memory `feedback_canary_queue`）：每个 skill 同时只跑一轮灰度；
新候选未轮到时排队不覆盖正在跑的 staging。

---

## 8. HybridSearch —— Atom 检索

| 项 | 取值 | 含义 |
|---|---|---|
| 向量检索 | top_k 条 | 基于 `summary or intent` 的归一化嵌入 |
| BM25 关键字 | top_k 条 | `[\w]+ UNICODE` 分词；中文整段作一个 token（后续可换 jieba） |
| 合并方式 | union + 去重 | 按 atom_id 去重；**无 rerank** |
| 标记 | `sources: ["vector"/"keyword"]` | 每条结果声明命中通道 |
| `min_similarity` | 默认 0.0 | 仅对向量分数生效，BM25 命中不过滤 |

---

## 9. 工具入参校验

| 工具 | 校验 |
|---|---|
| `read_traj(traj_id, offset_start, offset_end)` | `offset_end > offset_start`，区间在文件长度内 |
| `add_task_to_skill(skill_name, atom_id, weightscore)` | weightscore 必须 int 且 1 ≤ weightscore ≤ 10 |
| `score_task(atom_id, score)` | score 必须 int 且 1 ≤ score ≤ 10 |
| `new_skill_folder(skill_name)` | 已存在返回 `already exists`，不覆盖 |
| `add_task_to_skill` 在不存在 skill 上调用 | 返回 error 提示先 `new_skill_folder` |
| `write_file(path, content)` | 路径越出 skill_dir 返回 error |

---

## 10. LLM / Embedding 客户端

| 项 | 取值 | 含义 |
|---|---|---|
| `LLMClient.max_tokens` | 10000 | 给 thinking + 实际输出留充足预算（thinking 类模型常吃掉一半） |
| `LLMClient.temperature` | 0.0 | 抽取 / 评分场景默认确定性 |
| HTTP 超时 | 60s | LLM / Embedding 客户端 |
| DeepSeek 直连 model 类 | `agno.models.deepseek.DeepSeek` | base_url 含 `api.deepseek.com` 时强制使用，避免 reasoning_content 丢失 |
| 其他 OpenAI 兼容 endpoint | `agno.models.openai.OpenAIChat` | 通用 |
| SSL 校验 | `T2S_SSL_VERIFY=false` 关闭 | 企业 MITM 代理临时方案 |

---

## 修订记录

- 2026-05-12 AtomTask 重构（v2）：废弃旧 `source_trajs ≥ 3` gate / `path B`
  共识门槛 / SkillAgent 三路径决策；引入 `ATOM_PROMOTION_THRESHOLD=10` /
  cluster weightscore 分档 / atom-level canary。
