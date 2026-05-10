# 10-项目 Trajectory→Skill 横向调研综述

- 分析日期: 2026-05-09
- 输入: `reports/01_hermes.md` … `reports/10_gepa.md`（各报告含 path:line 级证据）
- 单项报告未在此重复——本文件只做横向矩阵 + 共性/分歧/最有借鉴的设计

---

## 0. 各项目"skill"的实际指代（先对齐定义）

| #  | 项目                | "skill" 实际是什么？                                                                                       | 证据 |
|----|--------------------|------------------------------------------------------------------------------------------------------------|------|
| 1  | Hermes self-evolution | 一份**已存在**的 Anthropic SKILL.md 的 *body 字符串*；只做就地变异，不新建                                  | `evolution/skills/skill_module.py:84-123` |
| 2  | OpenSpace          | 目录形态的 SKILL.md (+ scripts/, references/) +SQLite lineage DAG + `.skill_id` sidecar                     | `openspace/skill_engine/types.py:26-66` |
| 3  | EvoSkill           | `.claude/skills/<name>/SKILL.md`（YAML frontmatter）；每个程序版本=一个 git 分支                            | `src/registry/manager.py:33-95, 281-323` |
| 4  | AutoSkill          | Anthropic-style `Users/<uid>/<slug>/SKILL.md` 或 `Common/<lib>/...`；语义 semver patch                       | `autoskill/management/formats/agent_skill.py:48-95`，`maintenance.py:1126-1133` |
| 5  | AgentEvolver       | **没有 SKILL 概念**；称作"experience"，存储与抽取都委托给独立 ReMe HTTP 服务                                  | `agentevolver/client/em_client.py:48-72`，`docs/guidelines/exp_manager.md:337` |
| 6  | MemSkill           | 内存中的 `Operation` 行（INSERT/UPDATE/DELETE/NOOP 及 LLM 进化变体），由 PPO controller 选择              | `src/operation_bank.py:114, 219-260` |
| 7  | EvoAgentX          | **被进化的 workflow 文件对** `round_N/{graph.py, prompt.py}` + `experience.json`                            | `evoagentx_optimizers_aflow_optimizer.py:107-117`，`utils_graph.py:43-69` |
| 8  | SE-Agent           | 每轮、每实例一份 `iteration_K/system_prompt/<instance_id>.yaml` + 全局 `traj.pool` 摘要                     | `SE/operators/base.py:289-339`，`SE/core/utils/traj_pool_manager.py:140-159` |
| 9  | SkillRL            | `claude_style_skills.json` 里一条 `{skill_id,title,principle,when_to_apply}` 文本记录                       | `agent_system/memory/skills_only_memory.py:198-206`，`memory_data/alfworld/claude_style_skills.json` |
| 10 | GEPA               | `dict[str, str]` 形式的 candidate（任意可优化文本组件 mapping）；gskill 子包退化到 `{"skills": "<text>"}` | `src/gepa/api.py:96-145`，`src/gepa/gskill/gskill/train_optimize_anything.py:629-631` |

> **关键观察**：仓库自称"skill"的 10 个项目中，只有 5 个真的产 SKILL.md 文件（1, 2, 3, 4, 部分 10）。其余 5 个的"skill"分别是 RL operation / workflow code / system-prompt YAML / JSON record / vector-store entry——任何"skill 学习"调研都必须先穿过这层定义不一致。

---

## 1. 触发方式 (Trigger) — 横向矩阵

| 维度                     | 1 Hermes        | 2 OpenSpace                 | 3 EvoSkill            | 4 AutoSkill                 | 5 AgentEvolver       | 6 MemSkill            | 7 EvoAgentX            | 8 SE-Agent              | 9 SkillRL                  | 10 GEPA               |
|--------------------------|-----------------|-----------------------------|-----------------------|-----------------------------|----------------------|-----------------------|------------------------|-------------------------|----------------------------|------------------------|
| 触发类型                 | 离线 CLI 单 skill | **3 条独立线**：每任务 hook + tool-degradation 事件 + 每5 任务 counter | 离线批量 + 强制 GAP   | **逐次每 turn**（默认 turn_limit=1） | counter (`steps % updated_freq`) | counter (`outer_epoch % designer_freq`) | 离线批量 (`max_rounds` for 循环) | 离线 (`for i in iterations`) | counter (`steps % skill_update_freq`) + success_rate gate | 离线 batch (`while !stop`) |
| 一次产几个               | 0 或 1          | 0..N（per analysis）         | ≤1（CREATE 或 EDIT） | ≤1（`max_candidates_per_ingest=1`）| 0..N（ReMe 决定，未公开） | ≤`max_changes`（默认1）| 1（重复则 reroll）     | N×M (N 迭代×M 实例)     | ≤`max_new_skills`（默认3） | 1（默认）/ N parallel |
| 用户体验                 | 用户主动        | host **无感** + 显式 `fix_skill` 可手动触发 | 用户主动 `evoskill run` | **用户无感**（async background） | 用户**无感**         | 用户开关式（`--enable-designer`）| 用户主动脚本           | 用户写 yaml 编排算子    | 用户开关后**无感**         | 用户主动 `gepa.optimize` |
| 底层原理                 | Click CLI       | hook + counter + 事件        | counter for-loop      | per-turn counter + 后台 thread | counter + cold-start hook | counter | counter for-loop       | counter for-loop        | counter + success_rate 阈值 | counter + budget stopper |
| 同步/异步/阻塞           | 同步阻塞         | Trigger1 同步阻塞返回 / Trigger2&3 `asyncio.Task` 不阻塞 | 同步             | **异步、不阻塞**（thread + per-user FIFO + Semaphore(1)） | 异步、不阻塞（ThreadPoolExecutor）| 同步阻塞 | 同步阻塞 | 同步串行 | 同步阻塞主训练循环 | 同步主循环 |
| 内存触发 / 落盘后触发    | 离线           | 落盘后（读 metadata.json/conversations.jsonl）| 内存          | 内存 (`messages` window) | 内存           | 内存                  | 内存                   | 落盘后（读 .tra/.pool） | 内存                       | 内存                  |
| 聊天中可多次触发？        | 否              | 是（每 task & 每 5 task 计数）| 否（不是 chat）      | **是，每 turn**             | N/A（训练态）        | N/A（训练态）         | N/A                    | 否                      | N/A（训练态）              | 否                    |
| 证据                     | `evolve_skill.py:60-294,296-307` | `tool_layer.py:595-600,855,891`、`evolver.py:266-307,310-438,441-517` | `runner.py:263-403` | `interactive/session.py:1313-1376`、`server.py:541-546` | `exp_manager.py:111-125,1024,1184` | `trainer.py:1299` | `evoagentx_optimizers_aflow_optimizer.py:107-117` | `SE/basic_run.py:274-293` | `ray_trainer.py:826-834,1521-1526` | `engine.py:628,894-900` |

---

## 2. 轨迹保存 — 横向矩阵

| 维度               | 1 Hermes | 2 OpenSpace | 3 EvoSkill | 4 AutoSkill | 5 AgentEvolver | 6 MemSkill | 7 EvoAgentX | 8 SE-Agent | 9 SkillRL | 10 GEPA |
|-------------------|----------|-------------|------------|-------------|----------------|------------|-------------|------------|-----------|---------|
| 是否区分成败       | 通过 LLM judge 标 `relevant`/`expected` | 由 LLM 判定 `task_completed`（不信 agent 自报） | `score>=0.8` 算 OK | online 不区分；offline trajectory 有 `--success-only` | `Reward.outcome>0` 即 success | `is_correct = f1>=θ` 或 `judge==1.0`| `experience["succeed"]= avg_score>before` | `patch_content or "FAILED_NO_PATCH"`（LLM 也另打 `strategy_status`）| RL reward `score<=0` | fitness fn 返回 score（gskill: 1.0 if FAIL_TO_PASS+PASS_TO_PASS 都过）|
| 落盘格式           | JSONL eval set | `metadata.json`+`conversations.jsonl`+`traj.jsonl` | `.cache/runs/<hash>/<q_hash>.json`（默认不存 messages） | `<root>/index/online_skill_provenance_<user>.json` + version snapshots in metadata | ReMe 端 `local_vector_store/<workspace>.jsonl` | pickle (`memories/memory_*.pkl`) + checkpoint .pt | `round_N/{graph.py, prompt.py, log.json, experience.json}` | `iteration_K/<instance>/{*.tra, *.pred, *.problem}` + 全局 `traj.pool` JSON | `outputs/updated_skills_step{N}.json` + 失败轨迹只在内存 | `gepa_state.bin` + `candidates.json` + `run_log.json` + `proposer_calls/call_NNN.json` |
| 是否建索引         | 无       | SQLite `execution_analyses`（task_id UNIQUE）| `.cache/runs/<tree_hash>/`（哈希分桶）| 是：vector + persistent BM25 双库 + usage_stats.json | ReMe 端向量索引 | numpy faiss-style index for memory_bank | `pandas.DataFrame.groupby("round")["score"]` | `traj.pool` 自带 instance→iteration 索引 | embedding cache 字典 | `EvaluationCache` 按 `_candidate_hash` |
| 落盘后会再触发 SKILL? | 否       | 同 task_id 不重写；同 skill 累积指标可经 Trigger3 进 evolve | feedback 持续 append, 下一轮 proposer 自决 | 不会（provenance 是单向流） | ReMe 端 dedup op 会合并，不"重生成" | 失败池更新但 outer-epoch 决定再生成 | 是（每轮选父→生子→评测→新 round） | 池只追加，每轮算子读新池产 YAML | 失败轨迹收集后立即触发 LLM 产 dyn_skill | candidate 增量入池，不覆盖旧 |
| 证据               | `evolve_skill.py:106-107`、`external_importers.py:165` | `recorder.py:38`、`store.py:118-133` | `runner.py:319-324`、`run_cache.py:152-161` | `skill_provenance.py:30-45`、`stores/local.py:443-560` | `docs/guidelines/exp_manager.md:337,398` | `designer.py:160-184`、`trainer.py:1383-1390` | `utils_experience.py:14-98`、`utils_data.py:80-204` | `traj_pool_manager.py:127-165`、`traj_extractor.py:36-50` | `ray_trainer.py:998-1019,915-917` | `state.py:31-33,306-340` |

---

## 3. 生产方式 (Production) — 横向矩阵

| 维度                  | 1 Hermes | 2 OpenSpace | 3 EvoSkill | 4 AutoSkill | 5 AgentEvolver | 6 MemSkill | 7 EvoAgentX | 8 SE-Agent | 9 SkillRL | 10 GEPA |
|----------------------|----------|-------------|------------|-------------|----------------|------------|-------------|------------|-----------|---------|
| 形态                 | DSPy+GEPA workflow（10步固定） | hook + 子 agent loop（最多5 iter+3 retry）| 多 Agent (proposer/generator) 角色严格分离 | 写死 pipeline (extract→maintain→store) | RPC 客户端（生产逻辑全在 ReMe） | 3 段 LLM pipeline (Analysis→Reflect×K→Refinement) | 裸 LLM + 优化器 prompt 直驱 | 算子注册 + 单次 LLM 调用（无 ReAct）| RL training loop 内同步调 LLM | 进化算法（reflective + system-aware merge）|
| 信息来源              | 仅当前 SKILL 自身（就地变异）| 当前轨迹 + 选中 skill 全文 + 同 skill 历史 5 条 analyses | 当前 batch failures + 现有 skill 名单 + feedback_history.md 全文 | 当前 6-msg window + top-1 retrieved reference（"never extraction evidence"）| 当前 batch trajectories + score | 失败池 K-means + 上次 evolution 的 reward feedback | 历史经验 (success/failure 桶) + ≤3 条 log.json + 父 graph 代码 | per-instance 同实例历史摘要（最近2/全量/最后1）| 离线: 全部已分类 memories；在线: 当前 batch failed_traj | reflective: 父 candidate + minibatch trace; merge: 两 frontier 后代 + 共同祖先 |
| 查重机制              | 无 | 目录名 6位 uuid 后缀 + DB path/skill_id 双键 + `.skill_id` sidecar | **LLM 自决**（prompt 强制 EDIT vs CREATE，无 vector） | **三层夹心**：identity hash + previous-skill hint + semantic+BM25 top5 + LLM action judge + 加权分硬规则 | ReMe 端 `memory_deduplication_op`（未公开）| 仅按 `name` 字符串匹配 | `check_modification` **字符串完全匹配**（exact） | **不查重**（覆盖同名 yaml）| `_get_all_skill_ids` set 去重 + `_reassign_dyn_ids` 服务端重写 ID 防 LLM 撞名 | 无显式查重；`EvaluationCache` 在评估层命中；merge 用 (id1,id2,ancestor) 三元组去重 |
| 合并机制              | 无 | DERIVED 多 parent merge：LLM 在 `target_skills:[id_a,id_b]` 自决 | edit 路径：generator 收"Read existing+modify+preserve"指令，LLM 自决 | LLM judge 输出 `{action:add\|merge\|discard}` + `0.70*sem+0.18*signal+0.12*name` 加权打分 + capability/name hard gate | ReMe op `memory_deduplication_op`（未公开）| LLM 三选一 (`add_new`/`refine_existing`/`no_change`) + 容量满按最低 avg_reward 替换 | 无 merge；用历史 modification 做 negative prompt `-Absolutely prohibit ...` | 无 merge；"crossover" 算子是 LLM 合两条历史轨迹文本，非合 SKILL | 仅追加 (append-only) | **三方 merge by common ancestor**（git-like）|
| 更新 vs 新建平衡      | 永远更新 | 类型由 analysis LLM 选 fix/derived/captured + 4 条阈值（`_FALLBACK=0.4` 等）筛 + LLM 二次确认 | 提示词 + Anti-Patterns 强约束，LLM 自决 | LLM 选 add 但 `score>=0.4` → 强制改 merge | 全在 ReMe 端 | 提示词三选项 + flag (`--designer-refine-only` 硬禁 add_new) + 上次 reward feedback | 永远新建 round_{N+1}/ 目录 | 不存在（每轮覆盖 yaml） | **仅追加**（dyn_ skill），从不修改旧 | 仅追加新 candidate idx，父子图记账 |
| 默认存储路径          | `output/<skill>/<timestamp>/` | `OPENSPACE_HOST_SKILL_DIRS` env 优先 → `config.skills.skill_dirs` → `openspace/skills/`；DB `<root>/.openspace/openspace.db` | `.claude/skills/<name>/SKILL.md`（硬编码） | `SkillBank/` 默认；`Users/<uid>/...` + `Common/<lib>/...` 双层 | `ReMe/local_vector_store/<workspace_id>.jsonl`（默认 backend） | 内存 `OperationBank` + checkpoint .pt | `root_path/round_N/` | `SE/trajectories/<run>_<ts>/iteration_K/` | `memory_data/<env>/claude_style_skills.json` 输入；`outputs/updated_skills_step{N}.json` 快照 | `run_dir/` 下 pickle + json；gskill: `prompts/best_skills.txt` |
| 冷启动                | 无预置；不支持轨迹批冷启 | host_skills/ 2 个；showcase 60+ 是真跑出来的；**云端 community top-N 自动下载** | 预置 brainstorming/skill-creator 2 个；不支持轨迹批冷启 | 9 个 Common library 共数百 SKILL.md；**支持 offline 批扫**（`--success-only`）| 无预置；`init_exp_before_training` **整个 val_dataloader 一次性 rollout 灌库** | 4 个 seed (`insert/update/delete/noop`)；不支持轨迹批冷启 | 1 份种子 graph.py（必须） | 8 个手写 base_configs 作种子 | 12 general+~30 task+~10 mistake；用 LLM 一次性 distill 所有 memory | 用户提供 `seed_candidate`，gskill 默认 `{"skills":""}` 空种子；支持 seedless |
| 粒度入口              | 单 skill 任务级 | 任务级 + 用户级 (`fix_skill`) + 通用级（云端公共池）| 项目级 + 任务级（task.md）| 用户级 + 通用级（无任务级）| 仅 `workspace_id`（平面）| 通用级（单 OperationBank 全局） | 任务级/benchmark 级 | 任务级（per-instance）| 三档 (general/task_specific[cat]/common_mistakes) | candidate 是组件 mapping，`module_selector` round_robin/all 控粒度 |
| 版本控制              | 时间戳目录；无 git | SQLite 自定义 DAG（`lineage_content_diff` unified-diff 字符串 + `content_snapshot` JSON） | **git 分支** = 程序版本；**git tag** = frontier 成员 | 自定义 semver patch + 内嵌 ≤30 条快照；无 git | 无（覆盖式 dedup）| 自定义 `EvolutionSnapshotManager` 内存快照 | 文件夹版本 `round_N/`；无 git | 时间戳目录；无 git | 文件名 step 编号；无 git diff | 追加 list + `parent_program_for_candidate`；HTML 树可视化；无 git |
| 输出件 blast radius    | 单 SKILL.md（仅 body 替换）+ metrics.json | 1 skill 目录（含辅助文件，patch 支持 Add/Update/Delete File）；可并发多 skill (Semaphore=3) | 单 SKILL.md（YAML schema），prompt mode 也改 base prompt | 单 skill 文件夹，`max_candidates_per_ingest=1` | 单 jsonl entry | 单 Operation 对象，max_changes=1 默认 | 单 round 目录（graph+prompt+experience.json）| N 份 instance.yaml + 池更新 | 单 JSON entry（dyn_）+ 整 bank 快照 | dict[str,str] 整体；只追加新 idx |
| 权限隔离              | 无（脱敏：secret regex 6 类）| `SkillVisibility.PRIVATE/PUBLIC` + `threading.Lock` + `asyncio.Semaphore(3)`；`check_skill_safety` regex 警告 | 无；单仓库设计 | **多租户**（per-user 子目录 + scope 过滤）+ store RLock + per-user Semaphore(1) + 默认开 `redact_sources_before_llm` | 仅 `workspace_id`；ReMe 端 `thread_pool_max_workers=256`；脱敏未公开 | 单进程多线程 RLock 即所有 | 单进程独占 root_path；`LongTermMemory` sha256 去重；无 acl/脱敏 | 无；单进程串行 | val/train env 严格隔离防数据泄漏（**最严谨的之一**）| 训练时无；gskill 评估每 task 起新 docker container |
| Skill provenance      | metrics.json 仅记 `optimizer_model/baseline_score/...`，不记具体轨迹 id | **完整**：`SkillLineage.source_task_id` + `created_by`(model) + `parent_skill_ids` + LLM 一句 `change_summary` | git commit msg + feedback_history.md 含 active_skills；SKILL.md 文件本身**无字段**指回 session | 显式记 `session_id/job_id/channel/trigger/messages 摘要`；无 git commit | 仅 `messages+score`，不传 trajectory_id | `Operation.meta_info` 有 `created_at='designer'/'initial'`+`last_modified`；`MemoryItem.operation_history` 反向跟踪 | `experience.json` 含 `father node`(round 编号)；TextGrad snapshot 字典存 graph+metrics+index | YAML 文件名带 instance_id 路径含 iteration_K；不记 trajectory id | skill_id 区分 `gen_/dyn_/pnp_/err_` 前缀；`update_history` 仅内存 | `parent_program_for_candidate[idx]`（reflective 单父，merge 双父）+ `num_metric_calls_by_discovery` + `full_program_trace` |

---

## 4. 评价和更新 (Eval & Update) — 横向矩阵

| 维度          | 1 Hermes | 2 OpenSpace | 3 EvoSkill | 4 AutoSkill | 5 AgentEvolver | 6 MemSkill | 7 EvoAgentX | 8 SE-Agent | 9 SkillRL | 10 GEPA |
|---------------|----------|-------------|------------|-------------|----------------|------------|-------------|------------|-----------|---------|
| 评价对象       | SKILL 本身（LLM judge 类已定义但**未在主循环调用**，主循环用便宜 keyword overlap proxy）| SKILL 本身（4 个原子计数器 + LLM `SkillJudgment`）；不评用户体验 | program 整体在 val_data 的 accuracy；不评单 SKILL | (a) 在线 LLM judge `relevant/used`；(b) 离线 SkillEvo 单 skill 自动 compile 3-6 binary rules + LLM judge | experience 不显式打分；只用整体 `exp_mask_ratio` + 三 mode (`woexp/mixed/all`) reward 对比 | 整 OperationBank 当单元，对照 best snapshot 的 reward | workflow 本身按 benchmark 分数；TextGrad 用 LLM-as-judge | 评轨迹（成败=patch 是否产出）；不评 yaml | RL rollout success_rate 隐式反映 bank+policy 联合表现；无 per-skill 分 | 每 candidate minibatch + valset 全 eval；gskill 用 SWE-smith 二阶段测试 |
| 评价方法       | 控制变量 holdout（baseline vs evolved）| 开环 LLM 打分；**不**做控制变量 bench；**不**灰度 | 控制变量 bench（val_data 固定 split）| 在线开环 LLM judge + 启发式 fallback；离线控制变量 + `>=min_score_delta` | 控制变量 bench（README 表格 `+Q&N` vs baseline）| 控制变量（stage_avg_reward 取末 25% 与 best snapshot 比）| 控制变量（5 次跑均值）+ TextGrad LLM-as-judge | 端到端 swe-bench 通过率 + LLM 自评 strategy_status | RL reward + success_rate 阈值门 (`update_threshold`) | Pareto frontier (4 类: instance/objective/hybrid/cartesian) + Strict Improvement acceptance |
| 评分落盘      | `metrics.json` | SQL 表 + skills_catalog 给 LLM 时附 `(success x/y = z%)` | `program.yaml.metadata.score` + git commit + feedback log | `<root>/index/skill_usage_stats.json`（per-user）| 仅 wandb metrics；不写回 ReMe | snapshot 落 checkpoint | `results.json` + `experience.json` `before/after` | 不落盘 SKILL 评分（池里附 strategy_status） | success_rate 不持久化到 skill 上，只触发追加 | `prog_candidate_val_subscores[idx]` + `proposer_calls/call_NNN.json` |
| 影响淘汰/排序  | 不影响（无 catalog）| `selections>=2 & completions==0` 直接踢；`fallbacks/applied>0.5` 直接踢；catalog 注解给 LLM 看 | top-K (默认3) frontier 按 score 排，最差被 unmark + `git branch -D` | 排序仍纯 vector+BM25，**不混入 usage 分**；usage 仅决定淘汰 | 不影响 ReMe 任何条目 | reward 不升 → **整 bank 回滚到 best snapshot** + 失败 diff 进下一次 prompt 黑名单 | 影响下轮父图 softmax+均匀混合采样 (α=0.2, λ=0.3)；TextGrad **整图 rollback** | 不影响（"取最后两条"硬规则）| dyn_ skill 永远优先填 top_k；静态按文件序或 cosine | `ParetoCandidateSelector` 按 frontier 频次抽父 |
| 淘汰          | 不淘汰 | **软关闭** `is_active=0`；`delete_record` 接口存在但无调用 | 固定 K 频繁 `git branch -D` | 阈值删除（`retrieved>=40 && used<=0`）| 不淘汰（合并由 ReMe）| op：bank 满按最低 avg_reward 替换；snapshot：永留 best | 不淘汰（所有 round 目录都留）| 不淘汰（池只追加） | `remove_skill` API 存在但**仓库内零调用** | dominated 仅从 sampling pool 剔除，candidate 列表只增不减 |
| 灰度/A-B      | 无       | 无（线上单一 is_active=1）| 无       | 无          | 无             | 无          | 无           | 无          | 无（但 val/train env 隔离）| 无 |
| 证据          | `fitness.py:107-136`、`evolve_skill.py:212-227` | `types.py:367-398`、`store.py:469-482`、`registry.py:382-441` | `runner.py:347-374`、`manager.py:378-430` | `interactive/usage_tracking.py`、`stores/local.py:443-560` | `ae_ray_trainer.py:1140-1148`、`config/agentevolver.yaml:165-191` | `trainer.py:1383-1390`、`designer.py:676-714` | `evoagentx_optimizers_textgrad_optimizer.py:289-294` | `traj_pool_manager.py:104-107` | `ray_trainer.py:861-870`、`skills_only_memory.py:362-366,501-525` | `engine.py:175-281`、`gepa_utils.py:37-75` |

---

## 5. 使用方式 (Usage) — 横向矩阵

| 维度          | 1 Hermes | 2 OpenSpace | 3 EvoSkill | 4 AutoSkill | 5 AgentEvolver | 6 MemSkill | 7 EvoAgentX | 8 SE-Agent | 9 SkillRL | 10 GEPA |
|---------------|----------|-------------|------------|-------------|----------------|------------|-------------|------------|-----------|---------|
| 注入方式       | 不在本仓库（注入逻辑在 hermes-agent）| **全量塞 SYSP**（仅被选中的 ≤2 个）+ `# Active Skills` header + mid-iteration `retrieve_skill` 工具 | **不塞 SYSP**——靠各 CLI 自身发现 `.claude/skills/`（hint+folder）| **全量塞 SYSP**（top-k=1 默认），完整 SKILL.md body 拼成 `## AutoSkill Skills` 块 | **全量塞最近一条 user message 开头**（`<EXP>...</EXP>` 模板）| 全量塞 system prompt 风格，仅塞 controller top-K 选中的 | 不存在 SYSP 注入；产物是 import 进来的 workflow 代码 | **全量替换 system_template**（YAML 整段替换）| 全量塞 obs 模板的 `{retrieved_memories}` 占位（markdown 三段） | 用户拿 `result.best_candidate` 自己写回原 program；DSPy 集成可 compile；gskill 写 `.claude/skills/<repo>/SKILL.md` 或 `CLAUDE.md` 或 Jinja `{{skills}}` |
| Hint 提示原文（≤15 词）| N/A | "The following skills provide domain knowledge and tested procedures relevant to this task." (`registry.py:617-619`) | （仅短系统 prompt "You are an expert analyst..."）| "Choose the most relevant skill and follow its prompt; ignore if none applies." (`render.py:30`) | "Some Related Experience to help you to complete the task:`<EXP>{}</EXP>`" (`config/agentevolver.yaml:177`)| "You are a memory management executor. Apply the selected skills" (`executor.py:78-79`) | N/A | "You are a helpful assistant that can interact with a terminal to solve software engineering tasks." | 三段固定 markdown header（`### General Principles` 等）| gskill Jinja: `{{ skills }}` 槽位 (`mini.yaml:7`) |
| Skill search 实现 | 简单文件名匹配（`find_skill`） | **三段式**：BM25Okapi → text-embedding-3-small cosine → LLM JSON select；嵌入 pickle 缓存；agent 中可调 `retrieve_skill` 工具 | 无（CLI 自发现）；proposer 自己用扫目录+文件夹名 | **vector + BM25 hybrid**（`bm25_weight=0.1`）+ 可选 LLM 查询改写 + 可选 LLM selector | vector top-k（dashscope `text-embedding-v4`，query 是 `json.dumps(steps)` 整轨迹）| **RL-trained PPO controller**（softmax over op embeddings）| 无（按 round-id+score）；LongTermMemory 子系统是平行 vector top-k | **无搜索**——文件名严格匹配 `<instance_id>.yaml` | template (KW→task_type) 或 embedding (Qwen3-Embedding-0.6B cosine top-k) | 无 inference-time retrieval；whole-text injection |
| 项目形态       | CLI 工具，操作的是外部 hermes-agent | **MCP server + CLI 双形态**；4 工具 `execute_task/search_skills/fix_skill/upload_skill`；兼容 Claude Code/Codex/OpenClaw/nanobot | 多 SDK 兼容: Claude Code/OpenCode/Codex/Goose/OpenHands | 独立框架 + OpenAI 兼容反向代理 + OpenClaw 适配器；与 Anthropic SKILL artifact 兼容 | RL 训练框架（verl/Ray PPO）；不兼容 chat | 独立 PPO 研究框架；不兼容任何 chat agent | 独立 Python 库；不兼容 Claude Code | fork 自 SWE-agent；非 chat | RL 训练框架（verl-agent fork）；不兼容 chat | 独立 Python lib + DSPy/MLflow/Comet Opik/Pydantic AI/Google ADK 集成；通过 GEPAAdapter 接口接任意系统 |
| 证据          | `skill_module.py:58-100` | `registry.py:340-504,580-630`、`skill_ranker.py:99-129,191-301`、`mcp_server.py:529-998` | `base_agent.py:10-44`、`opencode/skill_utils.py:80-103` | `render.py:19-119`、`stores/local.py:265-268`、`autoskill/README.md:94-112` | `exp_manager.py:269-271`、`em_client.py:30-46` | `executor.py:43-105`、`controller.py:347-461` | `utils_graph.py:43-69`、`evoagentx_memory_long_term_memory.py:232-257` | `system_template_hook.py:42-66`、`intelligent_guidance_hook.py:61` | `env_manager.py:158-167`、`skills_only_memory.py:115-177,305-382` | `api.py:96-145`、`mini_swe_agent_config/mini.yaml:7`、`evaluate/claude_code_skills.py:107-121` |

---

## 6. 共性模式（≥3）

### C1. **"counter + offline-batch" 才是默认触发，per-turn 无感触发只是少数派**
10 个里有 8 个本质上都是"调脚本/进程"才会跑（Hermes/EvoSkill/EvoAgentX/SE-Agent/MemSkill/AgentEvolver/SkillRL/GEPA 都是 counter 或 batch loop）。只有 **OpenSpace** (`tool_layer.py:855,891` 三种触发线) 和 **AutoSkill** (`session.py:1313-1376` per-turn + 后台线程) 真正做到"对用户无感、聊天中持续触发"。
> 含义：研究界主流仍把 trajectory→skill 当作离线训练循环，"边聊边沉淀"在工程上的代价（异步/并发/性能/锁）多数项目尚未付出。

### C2. **生产端几乎一律是 LLM-as-author，没人用纯规则生成 skill 文本**
所有 10 个项目的 skill 文本都是 LLM 写出来的——无论是 GEPA 的 reflection LM、OpenSpace 的 evolve agent、SE-Agent 的算子单次 LLM、MemSkill 的 designer 三段 prompt、SkillRL 的 o3 distill，还是 EvoAgentX 的 AFlow optimizer prompt。**没有任何项目尝试用模板/规则/AST/DSL 生产 skill 内容。**
> 含义：成本与可控性都被 push 给 LLM；这意味着 skill 质量上限本质受 author LLM 上限制约。

### C3. **"只增不删 / 软删" 是绝对主流，真淘汰极少**
- 不淘汰：Hermes、EvoAgentX (round 都留)、SE-Agent (池只追加)、SkillRL (`remove_skill` 零调用)、GEPA (dominated 仅从 sampling pool 剔除，candidate 列表只增)、AgentEvolver (本端不淘汰)
- 软关闭：OpenSpace `is_active=0`
- 真淘汰：仅 EvoSkill (frontier 满则 `git branch -D`)、AutoSkill (`retrieved>=40 && used<=0` 删除)、MemSkill (bank 满按最低 avg_reward 替换)
> 含义：磁盘膨胀 / catalog 污染 / 检索噪声未被作为一线问题处理；真投生产前都需要补 LRU/TTL/质量阈值。

### C4. **多数项目只评 SKILL（或它的代理变量），几乎没人评用户体验**
评价对象大多是 SKILL 本身的得分（OpenSpace LLM judge、EvoSkill val_data accuracy、EvoAgentX benchmark、GEPA Pareto、MemSkill snapshot reward、SkillRL success_rate）。只有 **AutoSkill** 真正部署 `LLMSkillUsageJudge` 在每个 turn 让 LLM 判每条被注入的 skill 是否 `relevant`/`used` (`interactive/usage_tracking.py:103-220`)。
> 含义：从"用户实际有没有从 skill 受益"反向调优的链路在大多数项目缺失。

### C5. **Anthropic SKILL.md 格式正在成为事实标准**
OpenSpace、EvoSkill、AutoSkill、Hermes、GEPA-gskill 都直接产 YAML frontmatter `name/description` + Markdown body 这个 schema。AutoSkill 把 anthropics-skill 等 9 个 Common library 当预置包；EvoSkill 主推 [agentskills.io](https://agentskills.io) 跨 agent 可移植；GEPA-gskill 把训练产物写成 `.claude/skills/<repo>/SKILL.md`。
> 含义：Markdown SKILL.md 已成 cross-agent 互操作单位；非 SKILL.md 阵营（MemSkill/EvoAgentX/SE-Agent/AgentEvolver）几乎都是闭门 RL 训练框架。

---

## 7. 显著分歧（≥3）

### D1. **"Skill" 的本体差异（最大分歧，会影响所有下游讨论）**
| 阵营            | 项目                                | "skill" 本质                                    |
|-----------------|-------------------------------------|-------------------------------------------------|
| Markdown SKILL.md | OpenSpace, EvoSkill, AutoSkill, Hermes, GEPA-gskill | 跨 agent 可移植的纯文本文件                    |
| 工作流代码       | EvoAgentX                            | `round_N/{graph.py, prompt.py}`                  |
| 系统 prompt YAML | SE-Agent                             | per-instance 的 `system_template` 整段          |
| JSON 文本记录    | SkillRL                              | `{title, principle, when_to_apply}`             |
| RL Operation     | MemSkill                             | LLM 执行的 INSERT/UPDATE/DELETE 模板            |
| Vector store entry | AgentEvolver                       | ReMe 内的 memory 字符串（无 schema）            |

**含义：在做 skill 调研/复用时，第一步必须明确"我们要的 skill 是哪一个抽象"——RL operation 与 SKILL.md 之间没有任何复用价值。**

### D2. **查重/合并机制的成熟度差距巨大**
- **零机制**：Hermes、SE-Agent (覆盖)、EvoAgentX (字符串完全匹配)、SkillRL (set 去重)、MemSkill (name 字符串)
- **LLM 自决**：EvoSkill (proposer prompt 强约束)、OpenSpace (analysis LLM 在 `target_skills:[id_a,id_b]` 自决)
- **多层 sandwich**：**AutoSkill** 是唯一同时跑 (a) identity hash fast-path + (b) previous-skill hint + (c) semantic+BM25 top5 + (d) LLM action judge + (e) `0.70*sem+0.18*signal+0.12*name` 加权 + (f) capability/name hard gate 的项目（`maintenance.py:140-166,245-290,703-714,798-922`）
- **特殊**：**GEPA** 用 git-style three-way merge by common ancestor（`merge.py:118-207`）

> 含义：当 skill 库膨胀时，AutoSkill 的多层夹心方案几乎是唯一可参考工程蓝本；GEPA 的祖先合并是另一种更"程序员化"的视角。

### D3. **注入策略：从"hint+folder 让 agent 自发现"到"全量塞 SYSP"两极**
| 策略 | 项目 | 关键特点 |
|------|------|---------|
| 不注入 / 让外部 CLI 自发现 | EvoSkill | 把 SKILL 放 `.claude/skills/`，靠 Claude SDK / Codex symlink / OpenCode normalization 各自发现 |
| 注入到 user message 而非 SYSP | AgentEvolver | `<EXP>...</EXP>` 模板拼到最后一条 user message 前 |
| 全量塞 SYSP（被选中的） | OpenSpace, AutoSkill, MemSkill, SE-Agent, SkillRL | 直接拼成 markdown block |
| 写文件让外部进程读 | GEPA-gskill (写 `.claude/skills/...`) | 进化阶段产物落盘，inference 阶段被外部读取 |

> 含义：SYSP 注入虽然简单粗暴，但配合 KV-cache 能享受所有缓存收益；hint+folder 路线对底层 CLI 的 progressive disclosure 实现强依赖；user-message 路线则可以做 token-级 mask（见 D4）。

### D4. **训练时是否屏蔽注入文本（解决 reward hacking）只有一家这么做**
- **AgentEvolver** 是唯一显式做 `manage_training_context` 用正则把 `<EXP>...</EXP>` 剥掉再算 loss，并通过 `exp_mask` 走异构 PPO clip range 的项目 (`exp_manager.py:300-322`)。
- **SkillRL** 用了一种更软的隔离：`val/train env` 各自独立 bank，新 skill 永远只加进 train bank，**val 永远干净** (`ray_trainer.py:889-913`)。这两种 hack 思路完全不同。
- 其它项目（OpenSpace/AutoSkill/EvoSkill/...）都不做 RL 训练，所以不存在该问题。

### D5. **版本控制：从"git 直接当 program registry"到"自定义 SQL DAG"再到"完全没有"**
- **EvoSkill**：每个程序版本 = `program/<name>` git 分支，frontier = `frontier/<name>` git tag；`evoskill diff 3 7` = `git diff` 两个分支
- **OpenSpace**：SQLite 自定义 DAG，存 `lineage_content_diff` (unified-diff 字符串) + `lineage_content_snapshot` JSON；多对多 `skill_lineage_parents` 表
- **AutoSkill**：自定义 semver patch + 内嵌 ≤30 条 metadata 历史快照，无 git
- **MemSkill**：内存 `EvolutionSnapshotManager`，回滚到 best snapshot
- **GEPA**：追加 list + `parent_program_for_candidate` + HTML 树
- **EvoAgentX/SE-Agent/SkillRL/AgentEvolver/Hermes**：完全无版本控制概念（仅时间戳/step 编号目录）

### D6. **冷启动："批轨迹一次性灌库"只有 3 家真做**
- **AgentEvolver** 的 `init_exp_before_training` 跑一遍 val_dataloader 全 rollout 灌 ReMe (`ae_ray_trainer.py:968-1024`)
- **AutoSkill** 的 `offline/conversation/extract.py` + `offline/trajectory/extract.py` 双 CLI，`--success-only` 过滤后批扫
- **SkillRL** 的 `skill_generation/{alfworld,webshop,search}.py` 用 o3 把分类后的 memories 一次性 distill
- **OpenSpace** 用云端社区池逐任务下载 top-N (`mcp_server.py:373-426`)
- 其它（Hermes/EvoSkill/MemSkill/EvoAgentX/SE-Agent/GEPA）都需要从空池/单种子起步

---

## 8. 最值得借鉴的设计（≥3）

排序按"工程可借鉴度 × 概念新颖度"。

### B1. **OpenSpace `.skill_id` sidecar：把身份与路径解耦**
- 路径：`openspace/skill_engine/registry.py:41-70`
- 做法：第一次发现某 SKILL 目录时生成 `{name}__imp_{uuid8}` 写入 `.skill_id` 文件；改名/搬目录/跨机器复用 → ID 不变。
- 借鉴价值：所有"文件即数据库"型设计都需要这种 stable identity。Anthropic skill 自身没有这个，AutoSkill/EvoSkill/GEPA-gskill 都靠目录名做主键，移目录就乱。

### B2. **GEPA 的 system-aware merge by common ancestor（git-style three-way merge）**
- 路径：`src/gepa/proposer/merge.py:118-207`，特别是 `:155-194`
- 做法：找两个 Pareto front 上的后代 `id1, id2`，找它们的 *共同祖先* `ancestor`；按 component 比对，**只采那个相对祖先有改动的一边**，两边都改了按 agg score 选优。
- 借鉴价值：把"prompt 进化"变成"版本合并"，理论上比 prompt crossover 更可控、更可解释；几乎所有 multi-skill 共同进化的场景都能套用。

### B3. **OpenSpace 把质量信号变成 prompt 约束**
- 路径：`openspace/skill_engine/registry.py:419-441,696`
- 做法：catalog 给 LLM 时附 `(success 3/5 = 60%)` 注解 + 显式 prompt "Selecting an irrelevant or low-quality skill is worse than selecting none"
- 借鉴价值：很多 skill-catalog 系统拿到 quality 信号只用于排序——OpenSpace 把它直接告诉 LLM 让其自决，是低成本高 ROI 的做法。

### B4. **AutoSkill 的"用户下一句 = 上一轮的隐式反馈" + 异步背景抽取**
- 路径：`autoskill/interactive/session.py:574-586`，`:1313-1376`
- 做法：每收到一条新 user 输入，先用上一窗口（最近 6 message）作为 extraction window 在背景线程抽 skill；当前 user 输入既驱动检索/回答，又被视作对上一轮 assistant 输出的 feedback。
- 借鉴价值：把"用户继续说"当成隐式打分信号是几乎零成本的设计；配合 per-user FIFO + Semaphore(1) 的并发模型可以扩展到生产 chat。

### B5. **AgentEvolver 的"rollout 看 EXP，训练 mask 掉 EXP"**
- 路径：`agentevolver/module/exp_manager/exp_manager.py:269-271,300-322`，`docs/guidelines/exp_manager.md:181-205`
- 做法：rollout 时把检索到的 experience 拼到 user message 前；算 loss 前用 `re.sub` 把同一段精确剥掉；通过 `exp_mask` 让这部分 token 走异构 PPO clip range（off-policy）。
- 借鉴价值：所有 RL 训练 + 检索增强场景都会遇到"reward hacking 把检索结果 echo 回去"的污染问题；这种 token 级 mask + 异构 clip 是干净解。

### B6. **MemSkill 的"失败 evolution diff 进下一次 prompt 黑名单"**
- 路径：`src/designer.py:676-714`（外加 EvoAgentX `utils_experience.py:58-70` 的 `-Absolutely prohibit ...` 是同思路另一种实现）
- 做法：每次 evolution 失败（reward 不升）后，把整段失败的 diff 内容拼进下一次 designer prompt 的 "Previously Failed Approaches (DO NOT repeat these)" 节。
- 借鉴价值：把"失败"显式做成 LLM 上下文里的负样本，远比 reward-only feedback 表达力强；适合所有 evolution 类型项目。

### B7. **SkillRL 的 val/train env skill bank 严格隔离**
- 路径：`verl/trainer/ppo/ray_trainer.py:889-913`，注释 `:847-851`
- 做法：所有动态新增 skill 只写入 training env 的 bank，val_env 始终保持初始版本，防 val 失败模式被 skill 化后污染 val score。
- 借鉴价值：在很多"skill 与 policy 共同进化"的论文实现里这一点会被忽略，但漏了就会让所有 ablation 数字失真。

### B8. **EvoSkill 的"git 分支 = 程序版本"**
- 路径：`src/registry/manager.py:33-95,281-323`
- 做法：每个程序版本 = `program/<name>` 分支；frontier 成员 = `frontier/<name>` tag；`evoskill diff 3 7` 直接是 `git diff`；reset = `git branch -D program/*`；checkout 切换程序就是切分支并自动 stash。
- 借鉴价值：把"agent 程序版本管理"问题完全外包给 git，避免自造数据库；天然支持 review/rollback/blame。

### B9. **OpenSpace 的"自定义 patch 格式 + 6 级模糊匹配"**
- 路径：`openspace/skill_engine/fuzzy_match.py:1-11` + `prompts/skill_engine_prompts.py:404-444`
- 做法：用 `*** Begin Patch / *** Update File / @@ <anchor line>` 而非 unified diff；当 LLM 写错 anchor 时按 exact → trim → block-anchor → whitespace-norm → indent-flexible → trim-boundary 6 级降级匹配。
- 借鉴价值：所有让 LLM 输出 patch 的系统都会遇到"格式错一个字符整段失败"的问题；这种 graceful degradation 是面向真实 LLM 不稳定性的工程级 hardening。

### B10. **AutoSkill 的"usage-stat-driven auto-prune"**
- 路径：`autoskill/interactive/config.py:58-59`，`stores/local.py:443-560`
- 做法：每个 skill 记 `retrieved/relevant/used/last_*` 计数；当一条 user skill `retrieved >= 40 && used <= 0` 时直接删除文件夹。
- 借鉴价值：是 10 个项目里**唯一**真正解决"skill 库越长越烂"的方案——用 LLM 自评信号 + 简单阈值就能有效自清。

---

## 9. 给"trajectory→skill"系统的设计 checklist（取自上述项目的最佳实践）

| 维度 | 推荐实践 | 来源 |
|------|---------|------|
| 触发 | per-turn 异步 + 显式手动 override (`/extract_now`) | AutoSkill |
| 触发 | 多种独立 trigger 线（task hook + 事件 + counter）+ LLM 二阶段确认 | OpenSpace |
| 身份 | `.skill_id` sidecar，路径不是主键 | OpenSpace |
| 存储 | YAML frontmatter SKILL.md（兼容 Anthropic agentskills.io） | OpenSpace/EvoSkill/AutoSkill |
| 查重 | identity hash → previous hint → semantic+BM25 top-K → LLM judge → 加权分硬规则 sandwich | AutoSkill |
| 合并 | git-style three-way merge by common ancestor（多源经验合并）| GEPA |
| 版本 | git 分支/tag 当 program registry | EvoSkill |
| 评价 | 在线 LLM-judge `relevant/used` per turn → 写 usage_stats | AutoSkill |
| 淘汰 | `retrieved>=N && used<=0` 阈值删除 + 软关闭 `is_active=0` | AutoSkill + OpenSpace |
| 注入 | 全量塞 SYSP（被选中的）+ catalog 注解质量 + mid-iteration `retrieve_skill` 工具 | OpenSpace |
| 检索 | BM25 → embedding cosine → LLM JSON select 三段式 | OpenSpace |
| RL 兼容 | 注入文本在训练时 mask 掉 + val/train bank 隔离 | AgentEvolver + SkillRL |
| 失败回溯 | 失败 evolution diff 进下一次 prompt 的"DO NOT repeat" 节 | MemSkill / EvoAgentX |
| Provenance | 记录 `parent_skill_ids + source_task_id + created_by + change_summary` | OpenSpace |

---

## 10. 不在仓库公开 / 仍是空白的能力

- **跨用户 skill 联邦**：仅 OpenSpace 有云端 import，但 server-side 代码不在仓库
- **multi-tenant skill lock**：所有项目都没有 per-skill 写锁，只有 store 级 RLock
- **真正灰度 / A-B**：10 个项目无一实现
- **skill 间依赖图**：所有项目都是平面 catalog，没有 prerequisite/conflicts 关系
- **skill 脱敏**：仅 Hermes / AutoSkill 做 secret 正则；OpenSpace 做 safety regex；其他都不做
- **skill 退化检测**：除 OpenSpace 的 fallback_rate 外，无项目检测"skill 反而变差"的情况

这些是"trajectory→skill"系统从研究迈向生产时仍未被填上的坑。
