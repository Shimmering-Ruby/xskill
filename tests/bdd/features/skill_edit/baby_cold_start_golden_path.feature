Feature: 用户在冷启动后获得完整可用的 main skill
  新采集的原子知识先进入 baby 的 candidates 队列。
  用户不需要手工合并分支，也不需要在任务失败后重新提交已经处理的原子。
  系统应当持续编辑 baby，并在所有原子都形成可恢复提交后将它晋升为 main。

  Background:
    Given xskill 使用隔离的测试目录
    And SkillEdit 每批最多处理 5 个原子

  @primary @golden_path @http_llm
  Scenario: 用户等待系统把新知识整理成可用的 main skill
    Given SkillEdit 模型指向本地 OpenAI-compatible 测试后端
    Given cluster 已经创建名为 "incident-recovery" 的 baby skill
    And baby 的 candidates 按以下顺序保存
      | atom_id | knowledge                                      |
      | atom-01 | 检查服务进程是否存在                           |
      | atom-02 | 检查监听端口是否存在                           |
      | atom-03 | 检查反向代理的 upstream 配置                   |
      | atom-04 | 对比进程端口和 upstream 端口                   |
      | atom-05 | 恢复服务后先执行本地健康检查                   |
      | atom-06 | 最后从代理入口验证请求                         |
      | atom-07 | 把恢复结果和失败原因写入操作记录               |
    And candidates 的总权重已经达到 baby 冷启动阈值
    And 测试模型会根据每批原子更新 SKILL.md 并调用 commit_baby
    When watcher 调度这个 baby 的 SkillEdit 工作
    Then 模型应当收到 2 个互相独立的编辑 turn
    And 第 1 个 turn 应当只包含 "atom-01,atom-02,atom-03,atom-04,atom-05"
    And 第 2 个 turn 应当只包含 "atom-06,atom-07"
    And 每个 turn 都应当在 baby 上产生一个非空 checkpoint commit
    And 每次 commit 只应当删除当前 turn 绑定的 atom_id
    And candidates 最终应当为空
    And 框架应当把 baby 晋升为 main
    And main 的 SKILL.md 应当包含 7 个原子贡献的恢复流程

  @state_machine
  Scenario: 新原子在编辑过程中到达时不会破坏当前批次边界
    Given baby 当前有 5 个已经绑定到本 turn 的原子
    And cluster 在模型编辑期间追加了 1 个新原子
    When 模型提交当前 baby checkpoint
    Then 当前 commit 只应当消费原先绑定的 5 个原子
    And 新原子应当留给下一个 turn
    And 下一个 turn 成功后 baby 才能晋升为 main
