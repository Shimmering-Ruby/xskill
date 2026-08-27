# Generate 代理的工具面

日期：2026-08-27。对象：`xskill generate` 派发的 GenerateAgent。
起因：观测发现这个代理平均只能读到十条左右轨迹，而现网单个用户对某个 skill
的调用一天就有成百上千条。读轨迹的入口过多、卡片过重、上下文一压缩就丢证据，
是三个直接原因。本文定下工具面，代码见
`src/xskill/agents/traj_tools.py` 的模块头（同一份清单，两边要一起改）。

## 一、一共 16 个工具

### 轨迹侧 3 个（`traj_tools.py`，Generate 专用）

1. `traj_search(query, offset, limit)` —— 找轨迹的唯一入口。不带 query 是
   翻目录，按最近修改排序分页给 traj_id、行数、首问一句；带 query 是全文
   检索，每行带首个命中行号与片段，同名前缀的轨迹会错开展示，结尾说明还剩
   多少条没列。
2. `traj_cards(traj_ids)` —— 看轨迹概要的唯一入口，一次最多 8 条。每张卡给
   来源、总行数、user 轮数、工具使用统计，然后是带行号的全部用户问题、每问
   一句去掉思维链的回答、末尾一句收尾结论。工具返回结果不进卡，重复问句
   折叠，单卡约 1500 字符，超了从中间删并标出省略多少行。看卡不算精读。
3. `read_traj(traj_id, offset, limit)` —— 精读的唯一入口，按 id 跨全部轨迹
   目录解析，返回带行号原文，行号越界自动夹紧不报错。返回尾部给进度：已精读
   多少条不同轨迹、本条还剩多少行、续读用什么 offset；每满五条改为催促写
   wiki。精读计数只在这里记。

### wiki 侧 5 个（`llm_wiki.py`，Generate 专用）

4. `wiki_status()` 列出现有页面，压缩后第一个该调的。
5. `wiki_read(path)` 读回某一页，主要用来找回 survey 表。
6. `wiki_write(path, content)` 整页覆盖，把证据落盘。
7. `wiki_search(pattern)` 在全部页跑正则，查某个 traj_id 写过没有。
8. `wiki_log(entry)` 追加一行带时间戳的进度。

### skill 侧 8 个（`agent_tools.py`，与其他 agent 共用）

9. `skill_read(skill_name)` 读某个已有 skill 的 SKILL.md 与文件清单。
10. `list_files(path)` 列目录，只服务 skill 目录与 spill；打到轨迹目录改道。
11. `grep_files(pattern, path, ...)` 全文搜，同样只服务 skill 目录与 spill。
12. `read_file(path, offset, limit)` 按行读 skill 文件与 spill 落盘件；
    碰到 `traj_*.md` 改道 `read_traj`。读过才允许 edit。
13. `write_file(path, content)` 新建或整文件覆盖，写 SKILL.md 先校验
    frontmatter，非法不落盘。
14. `edit(path, old_string, new_string)` 对读过的文件做一处精确替换。
15. `new_skill_folder(skill_name, description)` 新建 skill 目录并初始化 git。
16. `commit_generate_main(skill_name, message)` 提交到 main，提交前查精读条数。

## 二、相对上一版（PR #339）改了什么

- 删掉 `list_sessions`、`session_card`、`session_cards`；`session_catalog.py`
  整个模块退役。`atom_task_read` 不再挂给 Generate。
- 读轨迹从「read_file 或 read_traj 或 session_cards 都行」收成一条链：
  `traj_search` 找 → `traj_cards` 挑 → `read_traj` 读。旧入口在 Generate job
  里碰到轨迹会报错改道，其他 agent 的行为不变。
- 精读口径从「read_file 过的 traj_id」改成「read_traj 过的 traj_id」，
  `obs/features.py` 的 `TRAJ_READ_TOOLS` 与 `CARD_TOOLS` 同步。
  顺带修掉一个既有漏洞：features 原先只从 path 参数抽 traj_id，而 `read_traj`
  的参数是 traj_id，所以它读的轨迹一直没被记进 `read_traj_ids`。
- 卡片重做。旧卡片是「时间线前 8 步」，工具调用与返回都在里面，一条两千八百
  字；新卡片是「全部用户问题加行号 + 每问一句回答 + 收尾」，工具返回丢弃、
  思维链丢弃，一条一千五百字以内。理由：让代理靠卡片判断这条轨迹该不该精读，
  用户问了什么最有判断力，工具返回最占地方且最没判断力。

## 三、轨迹条数：提示保底加用户指定

不做成配置项。分两层：

- 硬保底写在代码里（`GENERATE_MIN_TRAJ_READS`，现为 8 条），
  `commit_generate_main` 不够就拒绝。这一层只防摆烂，不表达目标。
- 目标条数写在提示词里。用户指令里点了数（「读至少 50 条轨迹」这类），
  就按用户的数写进提示词并说明以用户为准；没点数就给默认目标
  （`READ_TARGET_DEFAULT`，现为 30 条上下）并说明为什么需要这个量级。
  解析见 `generate_agent._read_target_line`。

这样实验里能观测到的是「代理是否服从指令里的条数」，而不是「代码是否强行卡住
条数」。前者才是我们要的能力。

## 四、提示词不列工具清单

Agno 从每个 `@tool` 函数的 docstring 取工具描述（摘要加长描述），
从 Args 段取每个参数的描述，一起放进模型的工具契约。所以：

- 工具怎么用、什么时候用、和别的工具怎么分工，写在 docstring 里。
- 系统提示词只讲任务、读轨迹的流程、写 skill 的红线，不再有「# 可用工具」
  一节。清单写两遍必然两边不一致。
- 打样脚本 `scratch/standalone-generate/tool_surface/dump_schema.py` 可以
  打印模型真正看到的那份契约，改完 docstring 应当跑一次看效果。
  当前 16 个工具的契约合计约 7200 字符。

## 五、卡片与预算的当前取值

| 项 | 取值 | 理由 |
| --- | --- | --- |
| 单张卡片上限 | 1500 字符 | 八张卡约一万二，100K 窗口下读一批卡不会逼近压缩线 |
| 一次卡片条数 | 8 条 | 旧版 10 条配两千八百字的卡片，一次就两万八 |
| 首问截断 | 160 字符 | 够判断主题，不够就精读 |
| 答句截断 | 120 字符 | 只用来判断这条轨迹走向，不承担传递结论 |
| 收尾截断 | 200 字符 | 结论句信息密度最高，给多一点 |
| read_traj 默认行数 | 120 行，上限 400 | 一屏能看完一两个来回 |
| 目录分页 | 30 条 | 一页扫完不压缩 |

## 六、已知数据问题（不在本期修）

- codex 桥接件里 assistant 段大量是 `[codex response_item #N]` 占位符，正文
  没落进 md。卡片侧已跳过占位符，但根因在桥接层。
- 有的 codex 会话被 Slock 通知刷出上百个 user 轮，卡片靠重复问句折叠顶住。

这两条都记在案，以后再说。

## 七、下一步

工具面定稿之后进实验台，回答两个问题：新卡片能不能把精读条数从十条量级抬到
几十条；50K 到 100K 窗口真正触发压缩时，wiki 恢复链路能不能接着干而不重扫。
数字出来再重写 PR #339 的正文。
