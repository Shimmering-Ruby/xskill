Feature: baby 冷启动可以从模型失败和进程中断中恢复
  已经形成 checkpoint 的工作不应重放。
  尚未形成 checkpoint 的工作不应丢失。

  Background:
    Given SkillEdit 默认每批处理 5 个原子
    And baby 的 candidates 使用稳定的 FIFO 顺序

  @recovery @state_machine
  Scenario: 连续失败时缩小当前批次，成功后恢复默认批次
    Given baby 中按顺序存在 5 个原子
    And N=5 的第一次尝试因 429 失败
    And N=2 的第二次尝试因上下文超长失败
    When N=1 的第三次尝试成功提交 checkpoint
    Then 三次尝试处理的 atom_id 都应当从 FIFO 队首开始
    And 前两次失败不应当消费任何原子
    And 第三次只应当消费 1 个原子
    And 剩余 4 个原子的下一次成功尝试应当使用默认 N=5

  @recovery @http_llm
  Scenario: commit 完成后的模型 429 不会导致原子被再次处理
    Given 模型已经调用 commit_baby 并成功提交当前批次
    And commit_baby 已经从 candidates 删除当前批次 atom_id
    And aimock 在模型的结束响应阶段返回 HTTP 429
    When SkillEditAgent 检查 baby HEAD 和剩余 candidates
    Then 当前 turn 应当被判定为成功
    And 当前批次不应当再次发送给模型
    And 下一个 turn 应当从第一个未消费 atom_id 开始

  @recovery @state_machine
  Scenario: 进程在多个 checkpoint 之间重启
    Given baby 最初有 6 个原子
    And 第一个进程已经提交并消费前 5 个原子
    And 第一个进程在晋升 main 之前退出
    When watcher 在新进程中再次调度这个 baby
    Then 新进程只应当把最后 1 个原子发送给模型
    And 已提交的前 5 个原子不应当重放
    And 最后 1 个原子成功后 baby 应当晋升为 main

  @recovery @state_machine
  Scenario: N=1 仍失败时把工作留给下一次 watcher 调度
    Given baby 中只剩 1 个原子
    And 当前重试批次已经降为 N=1
    When 模型仍然因为上下文超长而失败
    Then SkillEdit 工作应当结束并释放 worker
    And baby 应当继续停留在 baby 分支
    And 最后 1 个原子应当保留在 candidates
    And watcher 下次调度时仍应当从 N=1 开始

  @recovery @state_machine
  Scenario: 错误分相同时原子更少的 skill 优先调度
    Given 两个 baby skill "few-atoms" 有 1 个原子且 "many-atoms" 有 3 个原子
    And 两者错误分均为 0
    And edit pool 一次只能跑 1 个 skill
    When watcher 调度 SkillEdit
    Then 本轮应先提交 "few-atoms"

  @recovery @state_machine
  Scenario: N=1 连败 3 次后降优先级并换下一个 skill
    Given 两个 baby skill "hard" 有 1 个原子且 "easy" 有 2 个原子
    And "hard" 已在 N=1 上连续失败 3 次
    And edit pool 一次只能跑 1 个 skill
    When watcher 调度 SkillEdit
    Then 本轮应先提交 "easy"
    And "hard" 的 candidates 原子应当全部保留
    And "hard" 的重试批次仍为 N=1
