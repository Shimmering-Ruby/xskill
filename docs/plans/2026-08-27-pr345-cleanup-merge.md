# PR #345 清理后合入方案

日期：2026-08-27。对象：PR #345（feat/generate-otel-observe → main）。
关联：PR #339（旧方案，由本 PR 取代）、PR #354（obs 精简版，已在 main）、
PR #357（search traj 命令面，已在 main）。

## 一、这个 PR 合入什么

面向 generate 代理的轨迹工具面改造，核心是把「读轨迹」收敛成一条链：

1. traj_search：关键词检索全部轨迹，报每条轨迹的命中处数，context 参数
   可带前后行。
2. traj_cards：按 traj_id 出紧凑卡片（用户问题带行号、关键回答摘录），
   丢弃工具输出与推理长文，折叠重复问题。
3. atom_search：语义检索轨迹原子摘要（复用 atom 向量索引），返回
   traj_id、行号、intent、summary；行号不可靠时明确标注。
4. read_traj：按行号窗口分页精读，内置精读计数与 wiki 提醒。

配套改动：

- llm_wiki 新增 wiki_edit（old_string 留空追加、非空唯一替换），
  survey、patterns、outline 三个种子页带引导骨架；上下文压缩后
  代理靠 wiki 恢复已读进度。
- generate 提交闸门：SKILL.md 缺失或还是 stub 占位时
  commit_generate_main 拒绝提交。
- Cursor 桥：traj_id 的项目段从路径 slug 解出（不再 unknown）、
  支持嵌套 transcript 布局、工具入参写进 timeline 供卡片使用。
- 全部 18 个工具 docstring 重写为模型面向的中文描述（agno 从
  docstring 直接采集进模型上下文）；导出核对脚本 dump_schema.py
  的用法记入 skills/xskill-dev。
- 废弃并删除 session_catalog 三工具（list_sessions、session_card、
  session_cards）及其测试。

## 二、已经做过的清理（合并 main 时的取舍）

- obs 采用 main 上 #354 的精简实现（register 加 OpenAIInstrumentor）。
  本分支早期的手工 span 层（obs/features.py、obs/generate.py、旧
  tracing.py）连同配套实验脚本 scripts/bench/generate_obs/ 一并删除，
  它们量的埋点已不存在，属于被取代的 demo 层。
- skill 写根采用 main 的 pin 机制（new_skill_folder 后把可写根钉到
  新目录）。本分支重叠的 active-skill 路径重定向已移除，两套叠加会
  写出双层目录。提交闸门与 pin 不冲突，保留。
- 系统提示词保留本分支版本：不在提示词里罗列可用工具清单（agno 会
  自动注入工具 schema），保留 wiki 相关引导与「SKILL.md 列出精读过
  的 traj_id」要求。
- 误入合并提交的三个无关文件已移出分支：损坏的
  tests/test_compact_keeps_reasoning.py（收集即 ImportError）、
  docs/plans/2026-08-20-skilledit-compact-bench.md、
  docs/plans/2026-08-21-otel-only-replay-handoff.md。

## 三、合入前检查单

- [x] 分支已合并最新 main（671b49d，含 #354、#357），GitHub 显示
      MERGEABLE。
- [x] 相关测试 88 项全过（traj_tools、llm_wiki、generate_command、
      cursor_adapter、agent_tools_explore、generate_wiki_radius）。
- [x] 全量 pytest 2709 项通过。5 个失败（test_event_loop_safety 3 个、
      test_api_status 2 个）已在干净 origin/main worktree 原样复现，
      属 main 预先存在问题，与本 PR 无关。
- [x] 无残留冲突标记、无对已删 obs 模块的 import。
- [x] 实验验证（本机 harness，DeepSeek flash 经 litellm，100K
      max_context、85K 阈值真实生效）：42 条去重精读（目标 40），
      spill 45 次把历史压在 85K 内，compact 0 次即收敛，wiki_edit
      增量 16 次无整页重写，SKILL.md 238 行正确落目录并提交 main。

## 四、合入步骤

1. 关闭 PR #339，评论注明方案已由 #345 的 traj_tools 链路整体取代
   （session_catalog 在 #345 中删除）。
2. 合并 PR #345 到 main（squash 或 merge commit 按仓库惯例；分支含
   一次 main 合并提交，建议普通 merge 保留脉络）。
3. 合并后在观测机确认 XSKILL_OTEL_ENDPOINT 指向 Phoenix 时 generate
   任务有 trace（obs 走的是 main 的 #354 路径，本 PR 未改动）。

## 五、合入后跟进（不在本 PR 内，以后再说）

- main 预先存在的 5 个测试失败（test_event_loop_safety、
  test_api_status）：另开 issue 跟进。
- 现网客户端的 llm 配置需要真实设置 max_context 与
  compact_token_limit（内网 50K 到 100K 窗口），否则预算机制不触发；
  给用户侧的 config.yaml 样例补注释。第二期。
- ~/.xskill/config.yaml 里 doubao embedding key 403 过期，atom_search
  在现网要用需先换 key；实验环境用的是 DashScope。第二期。
- 上下文预算包装只套 model.invoke，任何 stream=True 的调用路径会
  绕过预算（详见 skills/xskill-dev 的流式陷阱一节）。产品 GenerateAgent
  是非流式不受影响；若以后有流式入口需先给 invoke_stream 补包装。
- 轨迹原始数据质量问题（Codex 占位回复、Slock 通知刷屏）目前靠
  traj_cards 过滤缓解，根治要在桥接层做。以后再说。
