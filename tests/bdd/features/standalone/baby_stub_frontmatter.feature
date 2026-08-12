Feature: baby stub frontmatter 安全序列化（#200 根治）
  蒸馏器创建 baby skill 初版时，description 来自 cluster/LLM，可能是多行
  或含 YAML 特殊字符。stub 写入必须走 yaml.safe_dump 序列化而非 f-string
  裸拼，保证 baby 初版从出生就是合法 YAML——宽松 loader 不会恢复出空
  description，reindex 也无需触发止血跳过。

  Background:
    Given xskill 使用隔离 skill 目录与可记录的假 embedding 客户端

  @state_machine @stub @issue200
  Scenario: 多行 description 创建 baby stub 后 frontmatter 仍严格可解析
    Given 用敌意 description 创建 baby skill "hostile-multiline"
    Then skill "hostile-multiline" 的 SKILL.md frontmatter 严格可解析
    And skill "hostile-multiline" 的 description 与输入逐字一致

  @state_machine @stub @issue200
  Scenario: 含冒号引号井号的 description 创建 baby stub 后 frontmatter 仍严格可解析
    Given 用敌意 description 创建 baby skill "hostile-specials"
    Then skill "hostile-specials" 的 SKILL.md frontmatter 严格可解析
    And skill "hostile-specials" 的 description 与输入逐字一致

  @state_machine @stub @reindex @issue200
  Scenario: 敌意 description 的 baby stub 无需止血即可进入索引
    Given 用敌意 description 创建 baby skill "hostile-multiline"
    When 重建 skill 向量索引
    Then 索引重建不抛错
    And skill 目录存在 .skill_index.pkl
    And 索引包含 skill "hostile-multiline"
