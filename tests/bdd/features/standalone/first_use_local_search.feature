Feature: 首次安装时本地 skill 搜索不因缺索引或未配 embedding 而 500
  未初始化安装（无 .skill_index.pkl，或 embedding 未配置）时，本地
  /api/v1/skills/search、/skills/resolve 与 SDK search_skills 应返回空结果，
  不得为了建 embed client 而 500。status 在 skill 目录尚未 git init 时也应 200。

  Background:
    Given xskill API 使用隔离的空 skill 目录
    And embedding 配置为空

  @state_machine @first_use @issue46
  Scenario: 缺 .skill_index.pkl 时 skills/search 返回空列表且不建 embed client
    When 客户端 POST /api/v1/skills/search 查询 "heartbeat"
    Then 响应状态码是 200
    And 响应 JSON 是空列表
    And 不应创建 embedding 客户端
    And 日志应包含 "skill search skipped"

  @state_machine @first_use @issue46
  Scenario: 缺索引时 skills/resolve 返回空结果且不建 embed client
    When 客户端 POST /api/v1/skills/resolve 查询 "heartbeat"
    Then 响应状态码是 200
    And resolve 结果为空
    And 不应创建 embedding 客户端
    And 日志应包含 "skill resolve skipped"

  @state_machine @first_use @issue46
  Scenario: 有占位索引但未配 embedding 时 skills/search 返回空列表
    Given skill 目录存在占位 .skill_index.pkl
    When 客户端 POST /api/v1/skills/search 查询 "heartbeat"
    Then 响应状态码是 200
    And 响应 JSON 是空列表
    And 不应创建 embedding 客户端
    And 日志应包含 "embedding.base_url/model unset"

  @state_machine @first_use @issue46
  Scenario: SDK search_skills 在缺索引时返回空列表
    When SDK 调用 search_skills 查询 "heartbeat"
    Then SDK 搜索结果是空列表
    And 不应创建 embedding 客户端
    And 日志应包含 "skill search skipped"

  @state_machine @first_use @issue46
  Scenario: skill 目录尚未 git init 时 status 返回 200 且 git_branch 为空
    When 客户端 GET /api/v1/status
    Then 响应状态码是 200
    And status 的 git_branch 为空
