# xskill：团队级 Coding Agent Skill 蒸馏、分享与进化方案

近期，大家都逐渐用上 coding agent，开始探索内部流程和 Agent 结合的实践。

有的同事懂这个，有的同事懂那个，每个人都在自己的子领域花时间跟 code agent 做教育，把流程打磨清楚了。

问题在于：**一个 workflow 能跑通**和**觉得它该能跑通**，两者间存在巨大的鸿沟——通常意味着一个小时甚至一下午的探索时间。

- 同事 A 探索成功的路径，应该能被同事 B 快速复用，并且 B 可以借助自己的环境进一步完善这条路径。
- 同事 A 应该能方便地复用自己的旧流程。
- 这个分享 / 复用的路径应该足够短——比网页版的 SkillHub 还要短——最好别让人进行任何操作。

## 我们的答案

我们团队给出的答案，就是 **[xskill](https://github.com/SkillNerds/xskill)**：一个面向组织的 skill 蒸馏、分享和进化方案。

首先，找一台服务器，拉起 xskill server 进程（Qwen3.6-27B + Qwen3-0.6B-Embed 模型）。服务器会增量收集大家脱敏后的 coding agent 轨迹，将其蒸馏为 skill。服务器端为用户维护一个向量画像，将同一个 skill 的不同版本灰度推送给不同用户，然后基于用户体验做 skill 评分（非 LLM 直评）。优胜的 skill 进入 main 分支；后续更新时，将用户在该 skill 下的 rollout 轨迹收集起来做进一步演进。

## 相关工作调研

业界已有相当多的 trace2skill 开源方案，但大多不是开箱即用的 skill 进化工具，也未考虑团队场景下的用户需求。完成度较高的有以下几项：

### HKU：OpenSpace

https://github.com/HKUDS/OpenSpace

本项目和 xskill 最为相似，是值得对照的竞品。

OpenSpace 将自己定位为一个维护自有 skill 库的 Agent，用户并不接触其 skill 产出物，更像是 Manus MCP。

**主成功流程：**

1. 在服务器拉起 OpenSpace MCP 进程，或在 Agent 上注册 OpenSpace MCP（本地模式，作为主 Agent 的子进程）。
2. 在用户目录注入两个手册性质的 skill，用户需手动 `cp` 到 `~/.agent/skills/` 等目录。
3. Agent 连接 OpenSpace 的 MCP，OpenSpace 通过 MCP 暴露四个工具：`execute_task`、`search_skills`、`fix_skill`、`upload_skill`。
4. 遇到问题时，Agent 调用 `execute_task`，将任务的**自然语言描述**传过去。OpenSpace 收到后从零开始独立执行：
   - 先调用 `_select_and_inject_skills` 检索 skill；
   - Agent 加载 skill 处理任务（20 次 ReAct iteration）；
   - 若失败（根据最终输出状态判定），则 rerun 一次无 skill 的纯 Agent；
   - 无论成败，均调用 `_maybe_analyze_execution` 分析轨迹（conversations.jsonl + traj.jsonl + metadata.json），喂给分析 LLM，输出"执行成功与否判断 + 进化建议清单"；
   - 若建议清单 `candidate_for_evolution` 为真，立即调 SkillEvolver 修改 skill。
5. 另有 `_maybe_evolve_quality`，基于统计指标（加载率、成功率等）触发 skill 质量改进。
6. SkillEvolver 有三类触发来源：分析 LLM、工具调用率阈值、skill 成功率阈值；对应三类操作：
   - **FIX**：原地修补，目录不变，版本号 +1，适合"思路对但细节错"。
   - **DERIVED**：另起炉灶但标注为某条或多条 skill 的后代，父子并存，检索时多候选竞争。
   - **CAPTURED**：从零创建全新 skill，无 parent，适合"Agent 没用任何现有 skill 但搞出了值得记住的新模式"。

**问题：** 用户侧的 harness 无法在 OpenSpace 的 Agent（skill 增强的）中生效——skill 库的能力和你的 harness 是隔离的。团队部署时，可以让所有 Manus-like 的服务端 Agent 共享一个 skill 库，但执行时 Agent 不能操作你的电脑，只能把结果传给你的 Claude Code / Code Agent。它更像一个**能用进化 skill 的 subagent**，而非**提供 skill 的 library**。主 Agent 无法直接使用其 skill，且 subagent 是 OpenSpace 自实现的，不受你的 Agent 代码影响（例如它读不到 `claude.md`）。

### 阿里：Trace2Skill

https://github.com/Qwen-Applications/Trace2Skill

Trace2Skill 是一个学术性工作，并未计划将 trace2skill 做成供团队使用的工具。

**主成功流程：**

1. **准备 skillset：** 专家编写的 `S_ex`（希望进一步 skill 增强，即 Deepening）和 LLM 生成的 `S_llm`（希望 skill 变得更丰满和真实，即 Skill Creation from Scratch）。
2. **收集用户轨迹：** 离线收集用户轨迹集 `Tau = RollOut(Agent, S)`，按 succ/fail 分类。
3. **Skill 改进 Patch 生成：** 对每条轨迹 `tau`，分配一个基于 ReAct 的 `subAgent(tau, S_0)` 返回 patch。成功轨迹走单次 LLM call（成功分析师），失败轨迹走 Agent Loop 多轮，直到定位有效原因才返回 patch（需要真实环境——这在工业场景下不可理喻）。
4. **Patch 分组合并：** 采用类 MapReduce 方式，将 patch 分为固定大小的组，让 LLM 参考 `S` 和 patch 进行归并，输出新 patch，直到只剩一个。其中包含拦截规则（如编辑文件冲突、假路径等）。
5. **Patch 应用：** patch 应用到 skill 上，视为一次更新。

**评测流程：**

- **数据集：** SpreadsheetBench-Verified，有程序化可验收的标准答案。
- **数据分割：** 200 条轨迹用于进化，200 条用于评测（仅评测）。
- **模型：** Qwen3.5 MoE 122B-A10B 和 35B-A3B，两者既当 Skill Author 又当 Skill User，形成 2×2 矩阵以测可迁移性。
- **实验：** No Skill、Human-Written（Anthropic 专家手写 xlsx skill）、Parametric（122B 凭参数知识写简单 xlsx skill）、+Error（仅错误分析师）、+Success（仅成功分析师）、+Combined（两者都用）。
- **OOD 测试：** WikiTableQuestions——将输入输出转为 xlsx 格式，进化出的 skill 无需修改即可运行。
- **结果：** 小模型生成 skill 给大模型用时，Deepening 情境下只有错误分析师正向提升，成功分析师反而是副作用；小模型 skill 给小模型用时，任何分析师都有效，两者都用效果更好。Skill Creation from Scratch 情境下，小模型 skill 给大模型在 OOD 上效果明显，SpreadsheetBench 中提升不显著。

**点评：** Trace2Skill 是高质量的学术探索，但并非可用工具。它提出了类 MapReduce 的 skill edit 思想。关键发现：① 自动化轨迹分析可产出优于人类专家的 skill，且机器写的 skill 泛化性良好；② 跨模型尺度的 skill 迁移可行。但工业场景下，用户上传轨迹后环境已丢失，无法跑"错误分析师"，因此它更像一种归并思路，而非完整的团队解决方案。

### 华东师范：AutoSkill

https://github.com/ECNU-ICALK/AutoSkill

这是一篇未发表的学术工作，skill 进化所用的数据集是非 agentic 的（基于 WildChat_4.8M_qwen，纯 chat 数据）。AutoSkill 更像一个带 skill 维护功能的 chatbot，而非 skill 库应用。

**主成功流程：** 用户输入问题 `q` 后，同时触发"回复生成"和"skill 生成"：
- **回复生成：** `q` 被重写后进行 skill 检索（BM25 + Vector），检索到的 skills 组装为提示词块，做类 RAG 的回复生成。
- **Skill 生成与管理：** `q` 及其之前的所有 query 组成提示词，每轮与存量轨迹结合进行 skill 生成。每个新 skill 按向量检索最近邻，由 LLM 做 discard/merge/add 决策。触发 merge 则以 JSON 形式产出新 skill schema 并写入，版本号 +0.1。

**评测：** 不存在。真正的评测在其 SkillEvo 工作中——每个 skill 手动触发进化：获取父轨迹组 → LLM 出题 → skill 增强后 replay 回复 → 判题 → 根据答卷 mutate 多种 skill 变体 → 再判题 → 选出 SOTA。

严格来说，本文作者对 skill 的理解还比较浅，整体流程本质上是把 skill 当一组 prompt 做 RAG。

## xskill

https://github.com/SkillNerds/xskill

xskill 与 OpenSpace 类似，为用户提供一个自演进的 skill 库，能将流程性知识沉淀下来。但 xskill 通过挂载 `~/.claude/skills/` 等目录加载 skill，并具备画像推荐功能——而非像 OpenSpace 那样通过 MCP 包装为 subagent 运行。这意味着 **xskill 与你本地的 harness 是原生兼容的**。

OpenSpace 包装 subagent 本质上是借助 MCP 协议规避在各 code agent 上做生态兼容的难题，但大幅牺牲了用户的自主性——用户侧的 harness 无法生效。

此外，**xskill 的使用流程非常丝滑，几乎无感**。

### 主成功流程

1. 用户安装 xskill 到本地，拉起 client 进程。进程自动扫描本机包含哪些 Agent，上传其轨迹，并从服务器端获取 skills 加载到 Agent 中。
2. 用户实时产生的 trajectory 被脱敏后增量检测上传，在云端由 TaskAgent 拆分为子轨迹，同时识别子轨迹中用户使用了什么 skill，以及使用过程中用户的**主观体验评分（UX Score，0-10）**。
3. 子轨迹积攒到一定数量后，触发 **TaskClusterAgent**，可执行 skill 新建、在 skill 候选 buffer 中放置子轨迹、skill 合并等操作。其主要作用是 agentic 地将拆分出的子轨迹与 skill 关联，为 skill 进化提供弹药。关联时，它为子轨迹对 skill 的价值进行 0-10 评分；当某 skill 关联的子轨迹总分超过阈值后，触发 **SkillEditAgent** 进行 skill 改进。
4. SkillEditAgent 读取当前 skill 及其关联轨迹，agentic 地在服务端文件系统上进行改进，完成后 `git commit` 成为一个灰度分支（staging branch）。
5. 灰度分支与 main 分支进行用户体验比拼——xskill 将灰度分支推送到部分用户的电脑上，统计 UX Score 均值。若胜出，则替代 main 分支。

**xskill 几乎从诞生起就是为企业/团队场景设计的。用户基数越大，skill 进化就越快、越准。**

## 我们为什么这样做

xskill 的出发点不是包装学术概念，而是聚焦于"**在工程上用简单的方式，尽快将事情做好、做收敛**"。目前以这种形式放出，是为了尽快收集用户数据和反馈，用工程化、软件的思维迭代应用本身。

写一个 Agent 可能几千行代码就够了，但效果和 Claude Code 差距巨大——关键在于 Claude Code 背后的工程细节堆砌：用灰度测试的思想发布特性，在用户侧统计指标，然后迭代。这种积累到了质变，所以它这么好用。

如果我们用"设计 → 开发 → 发布 → 提单"这种动辄半年的传统流程，是没法快速积攒这些工程细节达到质变的。最终只会泯然众人、无人问津。**AI 相关的项目必须有自己的逃逸速度，才能成长和生存。**

所以，xskill 以这种形式放出来，希望大家可以积极部署、体验和反馈。

## xskill 需要什么样的计算资源

实测：**Qwen3.6-27B（16K 上下文）+ Qwen3-0.6B-Embed 模型**即可。找一台带 910 卡的服务器就足够。即便用户轨迹过长，xskill 也能消化——其内部会做 compact。
