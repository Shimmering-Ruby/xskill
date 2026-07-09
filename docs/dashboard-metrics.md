# Dashboard 指标口径 / Metric Definitions

> 本文档是前端 ⓘ tooltip 的唯一事实源。审计日期 2026-07-09。
> 审计范围：`src/xskill/dashboard/metrics.py`、`router.py`、`static/app.js`、`static/index.html`，
> 及其数据源 `pipeline/registry.py`（表结构与写入点）、`team/server/skill_manifest.py`（推荐埋点）、`canary.py`（ux 分与裁决）。

## 审计中发现的三个"死列"（多个指标的共同根因）

先说三个贯穿性事实，后面多条指标的结论都由它们决定：

1. **`trajectories.ux_score` 现行代码从不写入。**
   `update_traj_status()` 支持 `ux_score` 参数（registry.py:840），但全仓无任何调用方传它；
   watcher 的 `_score_new` 是 noop（runner.py:1146-1153）。真实 UX 分全部走 atom 粒度落在
   各 skill 目录的 `.ux_scores.jsonl`（canary.py `AtomCanary.append`），不回写 trajectories。
   → 所有 `AVG(ux_score) FROM trajectories` 的口径（总览平均 ux、按生态/模型的平均 ux、
   灰度分桶平均 ux、单 skill 版本/用户/趋势的 UX）**恒为 NULL**，前端显示 "—"。
2. **`trajectories.skill_generated` 从不写入**（只有读取点：api/app.py、trajectory.py）。
   → 技能产出率恒 0（app.js:29 已知并显示 "—"），按生态/模型表的"产 skill"列恒 0（且未做 "—" 处理，显示误导性的 0）。
3. **`trajectories.skill_used` / `canary_side` 只在单机模式写入。**
   写入点唯一：`runner.py:1222 mark_skill_used()`，仅 `_score_atoms_for_traj`（单机、读 traj 头
   `<!-- xskill:skill=X side=Y sha=Z -->`，**单值**）调用；team server 模式的
   `_score_atoms_for_traj_server`（runner.py:1246）遍历 atom 的 `used_skills`（**多值**）打分，
   但**从不调用 mark_skill_used**。→ team 部署下 `skill_used`/`canary_side` 恒 NULL；
   单机下也只记 header 里那一个 skill，atom 实际用到的多个 skill 被系统性漏计。

另有一个数值正确性 bug：**`tasks_extracted` 增量续拆时被 delta 覆盖**。
`_on_split_done`（runner.py:1065-1068）把 `update_traj_offset(tasks_extracted=n_atoms)` 写成
本次 split 新增的 atom 数（`TaskAgent.run` 只返回 ≥ resume_line 的新 atom，task_agent.py:339），
不是该轨迹累计 atom 数。轨迹追加内容触发续拆后，该列从"全量"被覆盖成"本次增量"
→ 原子总数、单轨迹均原子、原子采纳率分母、团队用户 atoms 列全部系统性低估。

## 结论汇总

| 指标 | 展示位置 | 审计结论 | 处置 |
|---|---|---|---|
| 轨迹总数 | 总览卡片 | 可信 | 保留 |
| 原子总数 | 总览卡片 | 口径修正后可信 | 修 tasks_extracted 覆盖 bug |
| 今日成本 | 总览/成本卡片 | 口径修正后可信 | UTC 日界 → 本地日界 |
| 平均 ux（轨迹） | 总览卡片 | 不可信-可修 | 死列；改从 .ux_scores.jsonl 聚合 |
| 单轨迹均原子 | 总览·关键指标 | 口径修正后可信 | 同原子总数；分母含未处理轨迹需注明 |
| 技能产出率 | 总览·关键指标 | 不可信-下线 | 死列，前端已显 "—"，建议整卡下线 |
| 处理成功率 | 总览·关键指标 | 口径修正后可信 | 分母剔除在途/区分 filtered |
| 重试率 | 总览·关键指标 | 可信 | 保留 |
| 推荐触发率 | 总览·关键指标 + 技能库表 | 不可信-可修 | 分子分母重定义（见条目） |
| 原子采纳率 | 总览·关键指标 | 口径修正后可信 | 对齐分子分母时间窗 + 封顶 |
| canary 晋升率 | 总览 + 灰度页 | 可信 | 保留 |
| 按生态对比 | 总览表格 | 口径修正后可信 | 删"产 skill"/"平均 ux"死列；除幻影行 |
| 按用户模型对比 | 总览表格 | 口径修正后可信 | 同上 |
| 成本&用量（累计/By模型/By步骤） | 成本页 | 可信 | 保留 |
| coding agent 占比 | 画像页 | 可信 | 保留（注明缺失兜底口径） |
| 用户模型占比 | 画像页 | 口径修正后可信 | unknown 改写为配置桶需在 UI 注明 |
| 团队用户列表 | 画像页 | 口径修正后可信 | last_active 语义修正；atoms 同覆盖 bug |
| 标签云 | 画像页 | 可信 | 保留（归因口径注明） |
| 技能库存清单 | 技能库页 | 口径修正后可信 | "使用次数"列是死数据，删列 |
| 单 skill 触发率表 | 技能库页 | 不可信-可修 | 同推荐触发率 |
| 单 skill 详情（触发/版本/趋势/按用户） | 技能库 drill-in | 不可信-可修 | 换 atom.used_skills 口径 + 趋势排序修 |
| 版本统计 工具/atom、token/atom | 技能库 drill-in | 口径修正后可信 | 归因粒度与估算方式注明 |
| 离线探针触发率 | 技能库 drill-in | 可信 | 保留 |
| 灰度分桶分布 | 灰度页 | 不可信-可修 | 剔除未触发 skill 的轨迹；ux 改源 |
| 生态目录 | 生态目录页 | 可信 | 保留 |
| /ux 版本聚合端点（未上前端） | API-only | 可信 | 前端 UX 数据应改用它 |

## 逐指标条目

### 轨迹总数（overview.trajs）
- 定义：registry 中已入库的轨迹行数。
- SQL/代码口径：`DashboardMetrics.overview()` — `SELECT COUNT(*) FROM trajectories`。
- 数据源：`trajectories` 表，`discover_trajectories()` 扫盘 upsert。
- 已知误差/偏差：包含 filtered/error 轨迹（"入库"而非"处理成功"），与 tooltip"已入库的对话轨迹文件总数"一致。
- 审计结论与处置：**可信**，保留。

### 原子总数（overview.atoms）
- 定义：所有轨迹拆出的 AtomTask 总数。
- SQL/代码口径：`overview()` — `COALESCE(SUM(tasks_extracted),0)`。
- 数据源：`trajectories.tasks_extracted`，由 `_on_split_done → update_traj_offset` 写入。
- 已知误差/偏差：**增量续拆覆盖 bug**（见文首）：轨迹追加内容后该列被覆盖为本次新增数，
  历史 atom 数丢失 → 系统性低估。对照真值：磁盘 `<traj_id>/tasks/atom_*.json` 文件数
  （`AtomTaskStore.list_by_traj`）。
- 审计结论与处置：**口径修正后可信**。修 `_on_split_done`：写 `len(store.list_by_traj(traj_id))`
  （全量口径）而非 `len(atoms)`（增量口径）。

### 今日成本（cost.today）
- 定义：今天 xskill 流水线 LLM/embedding 调用累计成本（USD）。
- SQL/代码口径：`registry.usage_summary()` — `SUM(cost_usd) WHERE ts >= date('now')`。
- 数据源：`llm_usage` 表，`record_usage()` 旁路 telemetry；`ts DEFAULT datetime('now')`（**UTC**）。
- 已知误差/偏差：`date('now')` 是 UTC 日界。本地 UTC+8 时，每天 08:00 前"今日"仍统计昨天 16:00 起的窗口，与 tooltip"今天"不符。另：`estimated` 字段（存在非 config 价源）前端未展示。
- 审计结论与处置：**口径修正后可信**。改用本地日界：`ts >= datetime('now','localtime','start of day','utc')`（ts 存 UTC，取本地当日零点换算回 UTC 比较）。

### 平均 ux（overview.avg_ux）
- 定义（tooltip）：所有轨迹的平均用户体验分（1–10）。
- SQL/代码口径：`overview()` — `AVG(ux_score) FROM trajectories`。
- 数据源：`trajectories.ux_score` —— **死列，现行代码从不写入**（见文首事实 1）。真实 UX 分在各 skill 目录 `.ux_scores.jsonl`（atom 粒度，`AtomCanary.append` 写，含 side/commit_sha/user_model）。
- 已知误差/偏差：恒 NULL → 0.0，前端 `ux()` 显示 "—"。指标名存实亡。
- 审计结论与处置：**不可信-可修**。改为扫 `skill_dir/*/.ux_scores.jsonl` 聚合全局均分（`canary.load_ux_scores`，可限 days 窗口），或在打分链路回写轨迹级均分。前者与"分析而非埋点"设计一致，推荐前者。

### 单轨迹均原子（overview.avg_atoms_per_traj）
- 定义：原子总数 ÷ 轨迹总数。
- SQL/代码口径：`overview()` — `atoms / trajs`，Python 侧除零保护。
- 数据源：同原子总数。
- 已知误差/偏差：(a) 分子受 tasks_extracted 覆盖 bug 低估；(b) 分母含尚未 split 的和 filtered 的轨迹（贡献 0 原子），是"入库轨迹均值"不是"已拆轨迹均值"。
- 审计结论与处置：**口径修正后可信**。修分子 bug；tooltip 注明分母含未处理轨迹，或分母改 `status IN ('split_done','indexed','done')`。

### 技能产出率（overview.skill_yield）
- 定义（tooltip）：生成了 skill 的轨迹数 ÷ 轨迹总数。
- SQL/代码口径：`overview()` — `SUM(skill_generated IS NOT NULL AND !='') / COUNT(*)`。
- 数据源：`trajectories.skill_generated` —— **死列，从不写入**（skill 实际经 atom → `.candidates.yml` → git 落地，不回写该列）。
- 已知误差/偏差：恒 0%；app.js:29-31 已认知并显示 "—"。
- 审计结论与处置：**不可信-下线**。"轨迹 → skill"在 atom 聚类架构下已不是一对一关系，该指标语义本身过时；建议卡片下线（若要保留"产出"信号，用 atom_adoption 的"有 atom 被采纳的轨迹占比"另立指标）。

### 处理成功率（overview.success_rate）
- 定义（tooltip）：处理到 done 的轨迹占比。
- SQL/代码口径：`overview()` — `SUM(status='done') / COUNT(*)`。
- 数据源：`trajectories.status`（状态机见 `TrajectoryStatus`）。
- 已知误差/偏差：分母混入三类非失败：(a) 在途（discovered/splitting/split_done/indexed/clustering），刚入库一批轨迹时"成功率"瞬间被稀释；(b) `filtered`（interest not_fit，**主动跳过**非失败）；(c) `updated`（等重拆）。真失败只有 `error`。
- 审计结论与处置：**口径修正后可信**。改为终态口径：`done / (done + error + filtered)` 并把 filtered 单列，或直接展示 `get_status_counts()` 状态分布替代单一比率。

### 重试率（overview.retry_rate）
- 定义：处理中发生过重试的轨迹占比。
- SQL/代码口径：`overview()` — `SUM(retry_count>0) / COUNT(*)`。
- 数据源：`trajectories.retry_count`（`increment_retry` + cluster partial-fail 回写）。
- 已知误差/偏差：无实质问题；分母同样含在途轨迹（轻微稀释），可接受。
- 审计结论与处置：**可信**，保留。

### 推荐触发率（rates.trigger / 技能库"单 skill 触发率"表）
- 定义（tooltip）：被推荐给用户的 skill 中、随后被实际采用的占比；单 skill = 被采用次数 ÷ 被推荐(去重用户)，封顶 100%。
- SQL/代码口径：`DashboardMetrics.trigger_rate()` —
  分母 `SELECT skill, COUNT(DISTINCT client_id) FROM recommendation_log GROUP BY skill`；
  分子 `SELECT skill_used, COUNT(*) FROM trajectories WHERE skill_used!='' GROUP BY skill_used`；
  单 skill rate = `min(used/rec*100, 100)`；overall = 被用过的被推荐 skill 数 ÷ 被推荐 skill 总数。
- 数据源：`recommendation_log`（`skill_manifest.build_manifest` 每次 client sync、每个 recommended slot 各插一条，≤20 条/次/客户端——**注水源**；仅 bucket=recommended，ranked-80 槽位不记）；`trajectories.skill_used`（仅单机模式写、单值，见文首事实 3）。
- 已知误差/偏差（按严重度）：
  1. **分子分母来自互斥的部署模式**：recommendation_log 只在 team server sync 写；skill_used 只在单机打分链路写。team 部署下分子恒 0 → 触发率结构性为 0；单机部署下分母恒空 → 表恒空。当前实现下该指标**在任何一种部署里都算不出真值**。
  2. 分母注水：每次 sync 重复插行。`COUNT(DISTINCT client_id)` 是止血补丁：把分母语义改成了"被推荐过的去重客户端数"，但分子仍是"使用事件数"（一个客户端可贡献多条轨迹）——**分子分母量纲不一致**，才需要 min(...,100) 封顶第二层补丁。
  3. 无时间因果：使用发生在推荐之前也计入"被推荐后采用"。tooltip 里"随后"不成立。
  4. 分子单值漏计：atom 的 `used_skills` 多值不进 `skill_used`；历史逗号分隔值（`Registry.trajectories_using` 按 LIKE 匹配的那种）在 `GROUP BY skill_used` 下整串不匹配。
  5. overall 与 by_skill 语义不同（"有采用的技能占比" vs "采用率"），共用一个"触发率"名字易误读。
- 审计结论与处置：**不可信-可修**。正确口径应为事件级配对：
  分母 = 去重 `(client_id, skill)` 推荐对（`recommendation_log`，首个 ts 为准）；
  分子 = 该 client 在推荐 ts **之后**的 atom `used_skills` 含该 skill 的去重 `(client_id, skill)` 对
  （client 归因走 `watch_dirs.ecosystem='team_client'` 的 label JOIN）；
  率 = 命中对数 ÷ 推荐对数，天然 ≤100%，撤掉封顶补丁。写入侧建议 `record_recommendation`
  幂等化（同 (client_id, skill, side, sha) 不重插）从根上止注水。

### 原子采纳率（rates.adoption）
- 定义（tooltip）：拆出的原子中被聚合进某个 skill 的比例。
- SQL/代码口径：`adoption_rate()` — `COUNT(DISTINCT atom_id) FROM atom_adoption` ÷ `SUM(tasks_extracted) FROM trajectories`。
- 数据源：`atom_adoption`（cluster 落 `.candidates.yml` 后 `record_atom_adoption`，runner.py:1426/1498）；`trajectories.tasks_extracted`。
- 已知误差/偏差：
  1. **分子分母时间窗不一致**：分子是全历史累计（仅 `rebuild --force` 经 `clear_rebuild_derived_state` 清空）；分母是 registry 当前值——eco 级 `reset_trajectories` 把 tasks_extracted 清 0 但不动 atom_adoption、`unregister_dir` 级联删轨迹行、tasks_extracted 覆盖 bug 压小分母 → 分子可大于分母，`_pct` **无封顶**，可显示 >100%。
  2. `was_new=True` 恒写死（runner.py:1427/1499），列语义（首次/覆盖）名存实亡——不影响本指标（DISTINCT 去重），但列已是摆设。
- 审计结论与处置：**口径修正后可信**。分子改为只计当前仍存在的 atom（`atom_adoption.atom_id` 与磁盘 atom 交集，或 reset 时同步清对应 atom_adoption 行）；同时修分母覆盖 bug；保险再加 min(...,100)。

### canary 晋升率（rates.promotion / 灰度页）
- 定义：灰度候选裁决中晋升为正式版的比例 = 晋升 ÷ 已裁决（晋升+拒绝+超时丢弃）。
- SQL/代码口径：`promotion_rate()` — `SELECT action, COUNT(*) FROM canary_decision GROUP BY action`；rate = promoted / (promoted+rejected+timeout_discarded)。
- 数据源：`canary_decision`，`check_and_decide` 终态裁决时 `_record_decision` 写入（canary.py:580）。
- 已知误差/偏差：`merge_failed`（合并失败）不入表，正确（非裁决）；`rebuild --force` 会清空历史。埋点与裁决同点位，无注水。
- 审计结论与处置：**可信**，保留。tooltip 与实现一致。

### 按生态对比（by-domain / eco-body 表）
- 定义：轨迹按生态（team_client 改按真实 coding agent）分组的轨迹数/均原子/产 skill/平均 ux。
- SQL/代码口径：`by_ecosystem()` — `watch_dirs LEFT JOIN trajectories`，`CASE WHEN wd.ecosystem='team_client' THEN COALESCE(NULLIF(t.source_harness,''),:hlabel) ELSE wd.ecosystem END` 分组。
- 数据源：`watch_dirs.ecosystem` + `trajectories.source_harness`（discover 时读 sidecar）。
- 已知误差/偏差：(a) "产 skill" 列 = skill_generated 死列，恒 0 且前端不做 "—" 处理；(b) "平均 ux" 列 = ux_score 死列，恒 "—"；(c) LEFT JOIN 让 0 轨迹的 watch_dir 也出一行（空 team_client 目录出成 `unknown` 幻影行，trajs=0）；(d) 分组主体（轨迹/均原子）本身正确。
- 审计结论与处置：**口径修正后可信**。删"产 skill"与"平均 ux"两列（或 ux 改 `.ux_scores.jsonl` 口径按 harness 聚合）；JOIN 加 `WHERE t.id IS NOT NULL` 去幻影行。

### 按用户模型对比（by-domain / model-body 表）
- 定义：按 `source_model` 分组的同上四列。
- SQL/代码口径：`by_model()` — `GROUP BY COALESCE(source_model,:mlabel)`。
- 数据源：`trajectories.source_model`（sidecar）。
- 已知误差/偏差：同 by_ecosystem 的死列问题（产 skill / 平均 ux）。分组正确。
- 审计结论与处置：**口径修正后可信**，同上删两列。

### 成本 & 用量（cost 页：累计/tokens/calls/按模型/按步骤）
- 定义：llm_usage 全量聚合。
- SQL/代码口径：`registry.usage_summary()` — 全表 SUM/COUNT + GROUP BY step / model。
- 数据源：`llm_usage`，每次 LLM/embedding 调用 `record_usage` 旁路写入；`rebuild` 刻意保留（已花的钱）。
- 已知误差/偏差：仅"今日"卡的 UTC 日界问题（见"今日成本"条）。其余无发现。
- 审计结论与处置：**可信**，保留。

### 用户 coding agent 占比（profile 页 harness 表）
- 定义：按轨迹来源 coding agent（harness）的轨迹占比。
- SQL/代码口径：`registry.harness_share()` — `_HARNESS_EXPR`：`source_harness` 缺失时非 team/manual 目录回退 `wd.ecosystem`，再缺退 `:hlabel`（config `dashboard.default_harness`，默认 unknown）。
- 数据源：`trajectories.source_harness` + `watch_dirs.ecosystem`。
- 已知误差/偏差：兜底桶名可被 config 改成某个真实 harness 名——把"未知"计入该 harness 份额，UI 无标注（见下条同理）。逻辑本身合理。
- 审计结论与处置：**可信**（默认 unknown 配置下）；若配置了 default_harness，UI 应注明"含未归因轨迹"。

### 用户模型占比（profile 页 model 表）
- 定义：按 `source_model` 的轨迹占比。
- SQL/代码口径：`registry.model_share(unknown_label=default_model)` — `COALESCE(source_model,:mlabel)`。
- 数据源：`trajectories.source_model`。
- 已知误差/偏差：看板路由把 unknown 桶**改名为 config 的 `dashboard.default_model`**（router.py:44,81）——历史无 sidecar 的轨迹全记到该模型名下，抬高其占比；tooltip 未披露。注意 canary 的 `eligible_models` 用默认 'unknown' 语义，两条路径已正确分离。
- 审计结论与处置：**口径修正后可信**。UI 对该桶标注"(默认归类)"，或不改名保留 unknown 行。

### 团队用户列表（profile 页 users 表）
- 定义：team server 上每个 client 的轨迹数/原子数/最近活跃。
- SQL/代码口径：`users()` — `watch_dirs.ecosystem='team_client'` 按 label(=client_id) 分组，`MAX(t.updated_at)` 作 last_active。
- 数据源：`watch_dirs`（upload 端点注册，label=client_id）+ `trajectories`。这就是已知问题 3 的现状：**trajectories 无用户归因列**，按用户的一切只能靠 team_client watch_dir 的 label 粗归因；单机轨迹无用户归属。
- 已知误差/偏差：(a) `last_active` 实为**流水线最后写该行的时间**（重拆/rebuild/打分都会 bump updated_at），不是用户最后上传时间；(b) atoms 列受 tasks_extracted 覆盖 bug；(c) 归因粒度=目录，client 换 id 即算新用户。
- 审计结论与处置：**口径修正后可信**。last_active 改 `MAX(discovered_at)`（入库时间近似上传时间）或按轨迹 sidecar 时间；表头注明"目录级归因"。

### 标签云（profile 页 tagcloud）
- 定义：全部已拆 atom 的 tags 计数聚合（小写归一），字号 ∝ 次数，附贡献用户。
- SQL/代码口径：`tag_cloud()` — 遍历 watch_dirs，`_resolve_local_root` 重映射路径后 `AtomTaskStore.all_atoms()` 扫盘计数；team_client 目录的 atom 归属 label 用户。
- 数据源：磁盘 atom JSON（分析式，无埋点）。
- 已知误差/偏差：(a) 用户归因同 users 条的目录级粗归因，本机目录 atom 计数但不归属用户；(b) 每次请求全量扫盘，目录多时慢（性能非口径问题）；(c) 单目录不可读整目录跳过（OSError 吞掉），数据不全时无提示。
- 审计结论与处置：**可信**，保留。UI 副标题已写明口径。

### 技能库存清单（skills 页目录表）
- 定义：skill 目录扫盘：name/state(git 分支)/description/version/候选数/使用次数。
- SQL/代码口径：`skills_catalog()` — 读目录 + SKILL.md frontmatter + `.candidates.yml`；state 由 `.git/refs/heads` + packed-refs 判 baby/main/staging。
- 数据源：磁盘（分析式）。
- 已知误差/偏差：**"使用次数"列 = frontmatter `metadata.use_count`，模板初始化为 0 后无任何代码递增——恒 0 的死数据**（D7 已在 drill-in 用 trajectories 实算替代，但列表页还挂着死列）。version 同为 frontmatter 自报。state/候选数可信。
- 审计结论与处置：**口径修正后可信**。删"使用次数"列，或改为 drill-in 同源的实算触发数（注意后者在 team 模式因 skill_used 死列同样是 0，见下条——先修触发口径再谈补列）。

### 单 skill 详情（drill-in：总触发/版本统计/按用户/趋势）
- 定义：该 skill 的真实总触发次数、按版本(sha)的触发/UX/工具/token、按用户触发、跨版本 UX 趋势。
- SQL/代码口径：`skill_detail()` → `_skill_traj_rows()`（`WHERE t.skill_used=?` 精确匹配；sha 从 traj .md 头现场解析）→ `skill_version_stats()` / `skill_by_user()` / `skill_timeseries(sha=None)`。
- 数据源：`trajectories.skill_used`（死于 team 模式，单机单值，见文首事实 3）+ traj .md 文件 + atom 文件。
- 已知误差/偏差：
  1. **team server 上恒空**（skill_used 不写）；单机下漏计 atom 多 skill 使用——已知问题 2 的完整形态。
  2. `_skill_traj_rows` 直接 `Path(wpath)/fn` 读盘，**没有走 `_resolve_local_root` 重映射**（tag_cloud 有）——独立只读镜像部署下 .md 读不到 → sha 全部 "unknown"、atom 聚合全 0。同一文件内两套路径解析不一致。
  3. **趋势图排序错误**：`skill_timeseries(sha=None)` 的点序继承 `skill_version_stats` 的 `sort(key=(sha=='unknown', sha))` —— 按 **sha 字符串字典序**排列，不是时间序。前端把它画成"跨版本 UX 进化趋势"折线（app.js sparkline），折线顺序无意义。
  4. `skill_by_user` 的 `COALESCE(w.label,'(local)')`：label 列 DEFAULT `''` 非 NULL，空 label 显示空串而非 "(local)"，应 `NULLIF(w.label,'')`。
  5. UX 列同 ux_score 死列问题，恒 "—"。
- 审计结论与处置：**不可信-可修**。触发口径改为 atom 粒度：扫该 skill 的 `.ux_scores.jsonl`（每条 = 一次真实使用打分，含 atom_id/commit_sha/scored_at/user_model，幂等去重）——触发数、版本分组、时间序、UX 一次全有，且天然覆盖 team 模式与多 skill；按用户维度经 atom_id → traj_id → watch_dir label 反查。趋势按 `first_scored_at` 排序。

### 版本统计的 工具/atom、token/atom（drill-in 表列）
- 定义：命中该版本的轨迹的 atom 平均工具调用次数与估算 token。
- SQL/代码口径：`_atom_aggregate()` — 读 `<wpath>/<traj>/tasks/atom_*.json` 的 raw_segment，`count_tool_calls`（数 `## Tool Call` 段）+ `estimate_tokens`（len//4）。
- 数据源：磁盘 atom（分析式）。
- 已知误差/偏差：(a) 归因粒度是**整条轨迹的全部 atom**，不是与该 skill 相关的 atom——多意图轨迹会把无关 atom 的工具/token 摊进该 skill 版本；(b) token 为 len//4 粗估（设计上只求版本间相对趋势）；(c) 独立镜像下路径问题同上恒 0。
- 审计结论与处置：**口径修正后可信**。随上条改造：atom 集合改为 `.ux_scores.jsonl` 里该版本的 atom_id 列表（正是"用了该 skill 的 atom"），归因即精确；UI 注明 token 为估算值。

### 离线探针触发率（drill-in trigger 面板）
- 定义：真跑代理探针在诱饵清单中的触发准确率（描述质量信号，区别于线上真实使用）。
- SQL/代码口径：`registry.trigger_eval_for_skill()` — `skill_trigger_eval` 按 skill 时间升序；逐 case 从 `.description_optimization/<exp>/` 读盘。
- 数据源：`skill_trigger_eval`（`record_trigger_eval`，探针评测时写）+ 实验目录 JSON。
- 已知误差/偏差：分数存 0–1，前端 `pctf` ×100 一致。评测时点数据，无注水。
- 审计结论与处置：**可信**，保留。UI 已明确标注与线上使用率的区别。

### 灰度分桶分布（canary 页 sides 表）
- 定义：轨迹按 canary_side（staging/main）计数 + 平均 ux。
- SQL/代码口径：`canary_sides()` — `GROUP BY COALESCE(canary_side,'main')`。
- 数据源：`trajectories.canary_side`（仅单机 mark_skill_used 写）。
- 已知误差/偏差：(a) **COALESCE 把从未触发任何 skill 的轨迹（即绝大多数）全部计入 main 桶**——main 数字 ≈ 轨迹总数，与灰度流量无关，数字严重失真；(b) team 模式该列恒 NULL → 全部进 main；(c) 平均 ux 死列恒 "—"。真实分桶样本在 `.ux_scores.jsonl` 的 `side` 字段（atom 粒度、绑 commit_sha，`check_and_decide` 判定用的就是它）。
- 审计结论与处置：**不可信-可修**。最小修：`WHERE canary_side IS NOT NULL` 去掉 COALESCE。正确修：改读各 skill `.ux_scores.jsonl` 按 side 聚合（count + avg score），与灰度裁决同源。

### 生态目录（eco 页 dirs 表）
- 定义：注册目录清单 + 轨迹数 + 已索引数。
- SQL/代码口径：`registry.list_watch_dirs()` — 子查询 `COUNT(*)` 与 `COUNT(has_embedding=1)`。
- 数据源：`watch_dirs` + `trajectories`。
- 已知误差/偏差：无发现；reset 路径正确维护 has_embedding。
- 审计结论与处置：**可信**，保留。

### （API-only）/skill/{name}/ux、/skillhub/{name}/ux 版本聚合
- 定义：ux 分按 commit_sha/content_sha 分组聚合 + 当前版本指针；atoms 变体附 atom 内容。
- SQL/代码口径：`Skill.ux_scores_by_version` / `SkillHub.ux_scores_by_version` → `canary.aggregate_ux_by_version`（days 窗口，默认 30；无分的 sha 不出组）。
- 数据源：`.ux_scores.jsonl`（幂等追加，绑 commit_sha/side/user_model）——**这是全系统最可靠的 UX 数据面**。
- 已知误差/偏差：前端 app.js 目前**没有调用**这两个端点（drill-in 的 UX 走的是死列口径）。
- 审计结论与处置：**可信**。处置：drill-in 的 UX/版本/趋势应切换到此数据源（见"单 skill 详情"条）。
