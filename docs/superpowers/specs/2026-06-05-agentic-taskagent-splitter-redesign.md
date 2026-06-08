# 原子拆分代理(TaskAgent)改 agentic 弃窗 — 设计 spec

> 状态:设计待评审(brainstorming 产出)。开发按 CLAUDE.md「先建度量,再迭代收敛」+
> 责任分离执行。是 0.6.0 正式版前的阻塞项重设计。

## 1. 背景与现状机制

TaskAgent 把一条轨迹(trajectory,agent 与用户的对话 markdown)按"用户意图切换"切成
若干 AtomTask。现状用**窗(window)**:从续接点起按字符截一段(`max_context_chars=30000`)
喂给 agent,逐窗循环到文件末尾(EOF),无 User 的窗"吸收"进上一原子。

**窗机制的硬伤**(已用 15 条对抗性合成数据 + deepseek-v4-flash 实测):
- 单意图超过 30000 字符时被**字符边界硬切**成残缺原子(伪边界 + 漏真边界)。
- 超长前言(首个 `## User` 前 >30000 字符)那一窗无 User 又无上一原子可并 → 循环停 →
  **整条轨迹零产出,内容静默丢失**。这类故障藏在"覆盖丢失"里,边界指标看不见。
- 把判边界用不到的 assistant 正文反复喂模型,浪费 token。

实测:窗 边界 F1≈0.92 但 **EOF 覆盖只有 0.87**(超长意图/前言两条直接丢内容);
agentic 单趟 EOF 覆盖 1.0。

## 2. 目标

弃窗。**一趟把整条轨迹拆完**,只把"带行号的 User 提问地图"喂进上下文,assistant 正文
靠工具按需读;代码层硬保证覆盖到 EOF;高并发 + 超长(百万字符级)轨迹下不静默漏拆。

## 3. 度量(度量先于实现)

复用已建的合成 benchmark(已知 ground-truth 最优切分)。**指标(随迭代单调下降到目标)**:
每条 case 判"失败"当且仅当满足任一:① 切分边界集 ≠ ground-truth;② 未覆盖到 EOF;
③ 静默产空(有 User 轮却 0 原子)。失败数驱动到 0(c07"撤销/反悔"这类已知难点单独标注,
不卡总收敛)。度量返回**富误差**(哪条、预测 vs 真值、EOF 是否覆盖)。

benchmark 要从 `/tmp` 固化进仓库(`scripts/bench/` 或 `tests/`),含 15 条对抗 case +
生成器 + 评测器(边界 F1/precision/recall + EOF 覆盖)。

## 4. 设计

### 4.1 上下文内容(context-0,精简)
只放:任务元信息(traj_id / trajpath / source_model / total_lines / resume_line)+
**全轨迹 User 提问地图**(每个 `## User` 行号 + 首句摘要,来源 `_extract_user_queries`)+
续拆衔接块(已拆原子行号范围,来源 `store.list_by_traj`)。**assistant 正文不进 context-0。**

### 4.2 工具
- `submit_atom(start_line, intent, summary, tags, used_skills, ux_score)` —— 提交边界 +
  打分复盘。提交即校验:`start_line` 必须是 `## User` 行、严格递增、≥ 续接点。**打分留
  TaskAgent**(它能用 `look` 读 assistant,有料可打),下游 cluster 不改。
- `look(line, before=40, after=20)` —— 读某行附近原文,**含向前看**(判"新意图 vs 追问"
  的主力)。
- `context_budget()` —— 返回已用 / 上限 / 剩余 token。已用以**后端真实 `usage.prompt_tokens`**
  为准,4 字符/token 仅作"还没发出去那部分"的预估。
- `my_atoms()` —— 返回本轮已提交原子的行号区间(自查进度/覆盖)。

### 4.3 提示词(方法论)
切分原则:只在用户意图切换处切;同一意图的追问/澄清/纠错/催促/**撤销-反悔=不切**
(撤销需显式规则 + few-shot,c07 难点);拿不准就用 `look` 读 assistant 再判。输出契约:
每个新意图调一次 `submit_atom`,`start_line` 严格递增。

### 4.4 EOF 覆盖硬校验(代码兜死,不靠 agent 自律)
- 首原子 `offset_start = 1`(把首个 User 前的前言并入)。
- 末原子 `offset_end = total_lines + 1`(强制盖到 EOF)。
- 有 User 轮却 0 提交 → **抛错**(不静默产空)。
- 落盘后断言区间无缝无叠地铺满 `[1, total+1)`。

### 4.5 上下文自管理(健壮性)
- **主动剪裁**:在 invoke 包装层(我们自己的代码)自管,到 resolved max_context 的
  **85%** 时,把旧的 `look` 工具返回从消息历史里**剪裁掉**(纯截断,不调模型;look 结果
  可重读,丢了安全)。
- **最底层兜底(唯一)**:抓住模型抛出的"超长"报错 → 剪裁对话历史 → **重新发起请求**。
  就这一条,不做解析上限学分母、不做多触发统一等过度设计。
- **分母(max_context)**:无统一查询 API。配置优先;配置模板里给默认 **200K**(注释掉,
  用户反注释改自己值);用到默认打一条 warning。
- **重试**:agno 默认 `retries=0`(不重试)→ 必须显式设 `retries>0` +
  `exponential_backoff=True` + `delay_between_retries`。每次 `agent.run()` 后**查
  `run_response.status`**,error 即抛/标该轨迹重拆——**绝不把静默空当"0 原子, done"收下**。

### 4.6 增量续拆(续写场景)
`resume_line = last_offset`;agent 只对 `≥ resume_line` 的新意图 `submit_atom`;已拆原子
列在衔接块里供锚定。不全量重拆(省钱、不重排已有 atom_id)。

## 5. 责任分离开发流程
- **度量子代理**:固化 benchmark 进仓库 + 搭骨架,确认"失败数"指标可跑。
- **编程子代理**:实现 §4,删窗机制(`_line_window`/`max_context_chars`/窗循环/吸收分支,
  源唯一不留兼容),对指标迭代到目标。
- **验收子代理(独立、只读)**:仅 `Read/Grep/Glob/Bash`,无 Edit/Write;复核指标 + 反
  "假实现"闸(防止把拆分写成直接吐 ground-truth)。不过持续回退编程子代理。
- **主代理**:调度,不下场。

## 6. 范围
**v0 做**:§4 全部(弃窗单趟 + 工具 + EOF 硬校验 + 自管理 + 增量 + 打分留 TaskAgent)。

**不做(后续)**:TaskCluster / EditAgent 的上下文重设计;>1000 User 轮导致**地图本身**
撑爆的分页(目前只有 §4.5 的超长兜底,不专门处理,记为已知边界);agno 自带 LLM 压缩
(我们选纯剪裁,不用它)。

## 7. 验收标准
- benchmark 失败数降到目标(c07 类难点单独标注豁免)、EOF 覆盖 = 1.0。
- 反"假实现"闸通过。
- `make test` 通过;发版前 `make e2e` 通过。
