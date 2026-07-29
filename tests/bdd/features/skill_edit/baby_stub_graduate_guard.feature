Feature: baby 毕业前必须去掉 stub 正文
  SkillEdit 不得把仍含 init placeholder 的 SKILL.md 晋升为 main。
  若 candidates 已空但 stub 仍在，框架应重触发写正文后再毕业。

  Background:
    Given SkillEdit 默认每批处理 5 个原子
    And baby 的 candidates 使用稳定的 FIFO 顺序

  @recovery @state_machine
  Scenario: 直接调用 commit_baby_to_main 时 stub 未清除则报错
    Given baby skill "stub-locked" 仍是 init stub 正文
    When 模型调用 commit_baby_to_main 尝试毕业
    Then 工具应当返回 stub 拒绝错误
    And baby 应当继续停留在 baby 分支

  @recovery @state_machine
  Scenario: candidates 已空但 stub 仍在时框架重写后再晋升
    Given baby skill "stub-empty" 仍是 init stub 正文
    And candidates 已经为空
    And 已有一次未改写 stub 的 baby checkpoint
    When watcher 再次调度这个 baby 的 SkillEdit
    Then 框架应当先触发一轮 stub 重写
    And SkillEdit 应当成功并把 baby 晋升为 main
    And 最终 SKILL.md 不应当再含 init placeholder
