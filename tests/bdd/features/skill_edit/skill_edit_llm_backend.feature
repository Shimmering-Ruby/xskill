Feature: SkillEdit 通过可控的本地模型后端执行真实工具调用
  BDD 不应把 SkillEditAgent 替换成一个直接修改文件的假对象。
  至少主成功路径必须经过生产使用的 Agno/OpenAI 客户端和 HTTP 协议，
  同时不能访问公网或依赖真实模型账号。

  Background:
    Given ai-mocks 在随机本地端口启动 OpenAI-compatible 服务
    And xskill 的 llm.base_url 指向该服务
    And xskill 使用无权限的测试 API key

  @http_llm @contract
  Scenario: 模型通过 OpenAI tool calls 完成一次 baby checkpoint
    Given 一个 SkillEdit turn 绑定了 5 个 atom_id
    And ai-mocks 为这个 turn 准备了以下响应
      | response | behavior                                      |
      | first    | 调用 write_file 更新目标 SKILL.md             |
      | second   | 收到 write_file 结果后调用 commit_baby         |
      | third    | 收到 commit_baby 结果后结束模型 turn           |
    When SkillEdit 使用生产 Agno factory 执行这个 turn
    Then ai-mocks 应当收到真实的 POST /v1/chat/completions 请求
    And 第一次请求应当声明 write_file 和 commit_baby 工具
    And 第二次请求应当包含 write_file 的 tool result
    And 第三次请求应当包含 commit_baby 的 tool result
    And atom_id 不应当由模型作为 commit_baby 参数提供
    And commit_baby 应当从框架绑定的批次取得 atom_id

  @http_llm @contract
  Scenario: 测试运行期间没有请求离开本机
    Given 主成功路径已经执行完成
    When 检查测试后端的 request journal
    Then 所有模型请求都应当发送到 ai-mocks
    And 请求不应当包含生产 API key
    And 测试不应当访问任何公共模型 endpoint

  @http_llm @recovery
  Scenario: 模型后端持续返回 429 时当前批次不会被误提交
    Given 当前 turn 的 N 为 5
    And ai-mocks 对模型请求返回 HTTP 429 和 Retry-After
    And 模型客户端已经耗尽该 turn 内部的有限重试
    When SkillEditAgent 收到模型调用失败
    Then baby 的 HEAD 不应当前进
    And 当前 5 个 atom_id 不应当从 candidates 删除
    And 下一次尝试的 N 应当变为 2
