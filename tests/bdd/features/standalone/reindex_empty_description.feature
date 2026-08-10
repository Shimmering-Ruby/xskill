Feature: reindex 遇到空或非法 description 时局部降级而不是整轮 500
  description-only 索引重构后，宽松 frontmatter 可能恢复出空 description；
  若把空串发给 embedding 端点，单条 400 会拖死整轮 reindex（issue #200）。
  系统应跳过空 description 的 skill，仍为合法 skill 写出索引。

  Background:
    Given xskill 使用隔离 skill 目录与可记录的假 embedding 客户端

  @state_machine @reindex @issue200
  Scenario: 仓内同时有合法 skill 与空 description skill 时 reindex 成功并写出索引
    Given skill 仓中有合法 skill "good-skill" 描述为 "Manage docker containers"
    And skill 仓中有空 description 的 skill "empty-skill"
    When 重建 skill 向量索引
    Then 索引重建不抛错
    And skill 目录存在 .skill_index.pkl
    And 索引包含 skill "good-skill"
    And 索引不包含 skill "empty-skill"

  @state_machine @reindex @issue200
  Scenario: 仓内存在非法裸多行 description 的 baby SKILL.md 时 reindex 不抛错
    Given skill 仓中有合法 skill "good-skill" 描述为 "Manage docker containers"
    And skill 仓中有非法裸多行 description 的 skill "login-v4-i18n"
    When 重建 skill 向量索引
    Then 索引重建不抛错
    And skill 目录存在 .skill_index.pkl
    And 索引包含 skill "good-skill"

  @state_machine @reindex @issue200
  Scenario: 被跳过的空 description 不会发给 embedding 客户端
    Given skill 仓中有合法 skill "good-skill" 描述为 "Manage docker containers"
    And skill 仓中有空 description 的 skill "empty-skill"
    When 重建 skill 向量索引
    Then 索引重建不抛错
    And 假 embedding 客户端收到的文本不含空串
    And 假 embedding 客户端收到过 "Manage docker containers"
