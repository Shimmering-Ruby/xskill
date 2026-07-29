Feature: 操作者可以从一份 SkillEdit trace 解释整个冷启动过程
  当模型失败、spill 或 compact 发生时，操作者不应依赖原始 JSON 猜测状态。
  同一个 skill 的所有 turn 应当追加到同一个可读日志文件。

  Background:
    Given SkillEdit 已为目标 skill 创建唯一的追加日志
    And 日志不记录原始 tool call JSON

  @observability @state_machine
  Scenario: 成功的多批次冷启动可以从日志完整复盘
    Given baby 中存在 7 个原子
    And 默认批次大小 N=5
    When 两个 turn 分别成功提交 5 个和 2 个原子
    Then 日志应当包含两个显著的 TURN START 分隔
    And 每个 TURN START 应当显示当次 N 和待处理数量
    And 每个 round 应当显示当前 token、spill 上限和 compact 上限
    And 工具摘要应当显示 commit_baby 消费的 atom_id
    And 每个 turn 应当显示已消费数量、剩余数量和下一次 N
    And 日志不应当包含原始 JSON 对象

  @observability @recovery @state_machine
  Scenario: 失败缩批和上下文处理顺序可以从日志确认
    Given 当前 turn 从 N=5 开始
    And 模型第一次调用触发上下文压力
    And spill 后仍然超过 compact 上限
    And 本次模型调用最终因 429 失败
    When SkillEdit 把当前工作缩小到 N=2 后重试
    Then 日志中 spill 事件应当出现在 compact 事件之前
    And 日志应当显示 compact 前后的 token 数量
    And 日志应当显示模型调用失败的可读原因
    And exhausted 日志行应当包含原始错误文本
    And 日志应当显示 "Retry batch reduced: 5 -> 2"
    And 下一次 TURN START 应当显示 N=2

  @observability @recovery @state_machine
  Scenario: 无关键词的 5xx ModelProviderError 仍按 status_code 重试
    Given 模型抛出 status_code=500 且 message 为 "server error" 的 ModelProviderError
    And 客户端 max_retries 为 3
    When 调用生产 retry wrapper
    Then invoke 应被尝试 3 次
    And exhausted 日志行应当包含 "server error"

  @observability @recovery @state_machine
  Scenario: 中文 429 ModelProviderError 仍按 status_code 重试
    Given 模型抛出 status_code=429 且 message 为 "当前并发请求过多，请稍后重试" 的 ModelProviderError
    And 客户端 max_retries 为 3
    When 调用生产 retry wrapper
    Then invoke 应被尝试 3 次
    And exhausted 日志行应当包含 "当前并发请求过多"
