# 方案一：干货教学型 -- "让人学到东西"

## 策略说明

切入"skill 自进化"这个学术前沿话题，用达尔文进化论类比降低认知门槛，辅以 10 篇竞品论文的横向调研，让读者产生"原来这个领域已经这么卷了，我居然不知道"的信息差焦虑。干货密度高 = 收藏率高 = 算法推流。定位"内行人科普"而非"项目方宣传"。

## 封面图描述

**风格**：深色科技感背景（深蓝/深紫渐变），左上角一个小的 DNA 双螺旋图标，暗示"进化"概念。

**主文字**（居中偏上，白色加粗无衬线体，两行）：
- 第一行大字："Coding Agent 也能自进化？"
- 第二行小字，浅蓝色："我调研了 10 个开源方案告诉你"

**底部**：5 个 agent 的 logo 横排（Claude Code、Codex、OpenCode、Cursor、OpenClaw），用细白线连接成网状图，暗示"跨生态"。

**配色**：主背景 #1a1a2e → #16213e 渐变，文字白色 + #00d2ff 强调色。

## 标题

调研了 10 个开源方案后，我找到了让 Agent 自动涨经验的方法

## 正文

我靠，上周五下午你是不是又被 Claude Code 坑了？我打赌它又把 Nginx 的 proxy_pass 写成了带尾斜杠的格式。明明两周前刚教会过它，结果人家直接失忆，跟第一次见面似的。这不是 bug，是所有 coding agent 的通病——每次对话都跟重启了一样，啥都不记得。

那问题是，能不能让 agent 自己学会记笔记？这个方向其实有个很装逼的名字叫 trajectory-to-skill，过去半年至少冒出来 10 个正经研究。比如港大那个 OpenSpace，他们把 skill 包装成了 MCP subagent，结果发现你的 CLAUDE.md 在里面根本不生效，白忙活。阿里那个 Trace2Skill 更离谱，非要重跑失败轨迹才能提取技能，但工业场景下环境早没了，跑都跑不动。华东师范的 AutoSkill 本质就是个 prompt RAG，没啥真正的 skill 版本管理，改完就忘。

这些方案有个共同的坑：LLM 自己生成了新 skill 就直接用上了。谁来保证新版本比旧版本强？让 LLM 自己评估自己？这不就跟让运动员兼裁判一样扯淡吗？

xskill（github.com/SkillNerds/xskill）干了一件别人没干的事：灰度发布 + 用户打分。具体来说，三个 Agent 各管一摊：TaskAgent 每 30 秒扫描你那堆乱七八糟的轨迹，拆成原子任务；TaskClusterAgent 给每个原子任务打 0 到 10 分，攒够 10 分才触发一次 skill 改进；SkillEditAgent 改完 SKILL.md 后，直接 git commit 到 staging 分支，进入灰度模式。

灰度是怎么玩的？20% 的用户用新版 skill，80% 继续用旧版。然后统计一个叫 UX Score 的东西——这玩意儿不是 LLM 自己瞎评的，而是基于用户真实行为来算：你是不是动不动就 undo、是不是老手动改输出、交互轮数有没有变多。新版赢过旧版才会上线，否则直接扔掉，没人发现。

xskill 还支持 5 个 agent 生态，SKILL.md 跨 agent 可复用。装起来也简单：pip install xskill，然后 xskill serve 就能跑。轨迹会脱敏上传，支持团队模式，你们组内共享 skill 不是梦。

## 封面图

![封面：Coding Agent 也能自进化？](images/d1_cover.png)

## 图片序列

**图 1：问题场景图 — 你教过它的东西，它不记得**

![问题场景](images/d1_fig1.png)

**图 2：竞品全景对比表 — 10 个系统横评**

![竞品横评](images/d1_fig2.png)

**图 3：三 Agent 流水线架构**

![三Agent流水线](images/d1_fig3.png)

**图 4：灰度 A/B 原理图**

![灰度AB](images/d1_fig4.png)

**图 5：快速上手指南**

![快速上手](images/d1_fig5.png)

## 标签

#CodingAgent #ClaudeCode #AI编程 #开发者工具 #Skill自进化 #灰度发布 #开源项目 #程序员效率 #AgentAI #Trajectory2Skill #团队协作 #xskill

## 评论区预埋

**评论 1**（引导深度讨论）：
"补充一下学术参考：OpenSpace 论文在 github.com/HKUDS/OpenSpace，Trace2Skill 在 github.com/Qwen-Applications/Trace2Skill。想深入了解这个方向的可以先看这两篇，设计思路完全不同但各有启发。"

**评论 2**（降低使用门槛）：
"有人问模型要求：实测 Qwen3.6-27B + Qwen3-0.6B-Embed 就够跑了，不需要 GPT-4 级别的模型。一台带 910 卡的服务器就行。DeepSeek v4-flash 也可以。"

**评论 3**（引导互动）：
"好奇大家平时用 coding agent 最头疼的重复劳动是什么？我个人是每次配 Docker Compose 都要从头教一遍..."
