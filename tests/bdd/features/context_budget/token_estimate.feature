Feature: 上下文 token 估算口径完整且方向安全（issue #149）
  ContextManager 的 spill/compact 触发判定依赖历史 token 估算。估算必须覆盖
  content、reasoning_content 和 tool_calls arguments，并在已知模型家族上
  保持"宁可略高估、不可低估"的方向，避免后端报上下文超长后才兜底。

  @estimate @coverage
  Scenario: reasoning_content 计入估算
    Given 一条 content 很短但 reasoning_content 很长的 assistant 消息
    When 估算这段历史的 token
    Then 估算值明显大于只按 content 估算的值

  @estimate @coverage
  Scenario: tool_calls arguments 计入估算（dict 与 object 两种结构）
    Given 一条带大参数 tool_calls 的 assistant 消息（dict 结构）
    And 一条带大参数 tool_calls 的 assistant 消息（object 结构）
    When 估算这段历史的 token
    Then 两种结构的估算都包含 arguments 折算的 token

  @estimate @overhead
  Scenario: 每条消息计入结构开销
    Given 100 条 content 为空的消息
    When 估算这段历史的 token
    Then 估算值至少为 400

  @estimate @family-band
  Scenario: 已知模型家族的估算落在参考计数安全带内
    Given 真实分词器参考计数 fixture
    When 对每条参考文本按对应模型家族估算
    Then 每个文本每个家族的估算不小于参考值的 85% 且不大于 145%
    And 每个家族全部文本的估算总和不小于参考总和

  @trigger @spill
  Scenario: 大 reasoning_content 使估算越过 85% 触发剪裁
    Given 一个 max_context=1000 的 ContextManager
    And 历史中 reasoning_content 占估算的大头且有一条可剪裁的 look 工具结果
    When 通过包装后的 invoke 发起请求
    Then 旧的 look 结果被剪裁标记替换
    And 桩 invoke 只被调用一次

  @trigger @compact
  Scenario: 估算超过 compact_token_limit 触发历史压缩
    Given 一个 max_context=1000 且 compact_token_limit=900 的 ContextManager
    And 一段估算超过 900 的历史
    When 通过包装后的 invoke 发起请求
    Then 压缩函数被调用且历史中出现 compact 标记消息

  @estimate @calibration
  Scenario: 后端真实 usage 校准后续触发判定
    Given 一个 max_context=1000 的 ContextManager
    And 桩 invoke 按收到消息估算值的一半返回 usage.prompt_tokens
    When 连续两次携带大估算历史发起请求
    Then 第一次请求触发剪裁
    And 第二次请求因校准后估算低于阈值而未触发剪裁

  @estimate @unknown-family
  Scenario: 未知模型家族使用保守缺省比率并打 warning
    When 用未知模型名构造 ContextManager
    Then 日志出现未知家族 warning
    And 家族路由返回缺省比率 0.75
