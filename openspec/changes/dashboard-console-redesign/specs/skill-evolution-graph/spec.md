## ADDED Requirements

### Requirement: skill 进化以双泳道 commit 图呈现且节点可 diff

skill 详情页 SHALL 提供 main/staging 双泳道的 commit 图（`GET
/api/v1/dashboard/skill/{name}/graph`）：节点=commit，按时间排列，分支/合并以连线表达。
节点 SHALL 标注：canary 裁决（晋升/回滚/观察中，语义色 + 文字）、该版本 ux 均分（有则）、
HEAD 状态。回滚节点 SHALL 直接展示回滚原因（分数对比）。点击任意节点 SHALL 展示该版本
与父节点的 diff（复用既有 diff 端点）。

#### Scenario: 晋升与回滚在图上可辨

- **当** 某 skill 历史上有一次 staging 晋升合入 main、一次 staging 被回滚
- **那么** 图中晋升节点为绿色并连线到 main 泳道，回滚节点为红色并标注 "x.x < y.y" 原因

#### Scenario: 点节点看 diff

- **当** 用户点击图中任意 commit 节点
- **那么** 右侧展示该 commit 相对父节点的 diff 文本

### Requirement: canary 裁决可定位到 commit

`canary_decision` 表 SHALL 记录 `staging_sha` 与 `main_sha`（新裁决写入时）。
`discard_staging` 在删除 staging 分支前 SHALL 保留只读 ref `refs/rejected/<ts>-<sha>`，
保证被拒 commit 对 `git log` 可达。存量无 sha 的历史裁决 SHALL 在图上显式标注
"裁决无法定位到节点"，SHALL NOT 按时间戳模糊匹配到某个 commit。

#### Scenario: 被拒 staging 仍可见

- **当** 一个 staging 分支被 canary 裁决拒绝并 discard
- **那么** 其 commit 仍通过 refs/rejected/* 可达，进化图能画出该红色节点并可 diff
