Feature: SkillEdit 只把 actionable skill 提交进 edit 池
  不会开 LLM 的 skill 不得占用 workers×3 提交窗口。
  单 skill 过滤失败不得中断整轮调度。
  关联 Issue #156 / #157 / #158。

  Background:
    Given edit pool 一次只能跑 1 个 skill

  @recovery @state_machine
  Scenario: main 无 ux_score 不进池，READY baby 进池
    Given baby skill "ready-baby" 已达冷启动阈值且可编辑
    And main skill "main-no-ux" 有候选但还没有 main 侧 ux_score
    When watcher 调度 SkillEdit
    Then 提交列表应包含 "ready-baby"
    And 提交列表不应包含 "main-no-ux"

  @recovery @state_machine
  Scenario: 未达阈值且无 checkpoint 的 baby 不进池
    Given baby skill "thin-baby" 仅有不足阈值的候选且无 checkpoint
    And baby skill "ready-baby" 已达冷启动阈值且可编辑
    When watcher 调度 SkillEdit
    Then 提交列表应包含 "ready-baby"
    And 提交列表不应包含 "thin-baby"

  @recovery @state_machine
  Scenario: 无 git 目录只跳过该 skill 不中断整轮
    Given skill 目录 "broken-nongit" 存在但没有 .git
    And baby skill "ready-baby" 已达冷启动阈值且可编辑
    When watcher 调度 SkillEdit
    Then 整轮调度不应因 NotGitRepository 失败
    And 提交列表应包含 "ready-baby"
    And 提交列表不应包含 "broken-nongit"

  @recovery @state_machine
  Scenario: actionable 检查抛错只排除该 skill
    Given baby skill "boom-skill" 在 actionable 检查时会抛错
    And baby skill "ready-baby" 已达冷启动阈值且可编辑
    When watcher 调度 SkillEdit
    Then 提交列表应包含 "ready-baby"
    And 提交列表不应包含 "boom-skill"
