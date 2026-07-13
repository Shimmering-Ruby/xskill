# events-notifications — 事件流 + 通知 + 世界消息（P3-3.1/3.2/3.3）

## ADDED Requirements

### Requirement: events 表只消费既有事实源（D7）

系统 SHALL 在 registry 内维护 `events` 表，事件只由四个既有事实源的落盘点
顺手产生，SHALL NOT 引入 👍/👎/短评等新的用户反馈动作：

- `feedback`：他人触发我贡献的 skill + atom ux 打分（runner 打分落盘点）
- `push_edit`：client 手改 skill 推 `user-staging/<client_id>` 分支
  （team server push-edit 端点），payload 带分支引用与 ref_sha
- `canary`：灰度裁决 promoted/rejected/timeout_discarded（裁决落盘点）
- `pin`：skill 被 pin（控制面写入点；block 不是社交事件）

所有埋点 SHALL 为旁路 telemetry：事件发送失败绝不阻断打分/推送/裁决主链路。

#### Scenario: 打分落盘产生 feedback 事件

- **当** bob 的轨迹打分落盘，其中 2 个 atom 命中 skill-x
- **那么** 产生一条 feedback 事件（actor=bob，payload 含均分/原子数/side/sha）

### Requirement: 扇出规则（D7 评审采纳）

feedback 事件 SHALL 按 traj 去重——同一轨迹多 atom 命中同一 skill 只发一条
（`(skill, traj_id)` 唯一索引兜底）。通知 SHALL 只发给该 skill 累计
weightscore ≥ 阈值（默认 3）的贡献者；本人触发本人贡献的 skill SHALL NOT
通知本人。无人可通知的事件仍 SHALL 入库（世界消息可见）。

#### Scenario: 本人触发不自通知

- **当** alice 触发自己贡献的 skill-x 并打分落盘
- **那么** 事件出现在世界消息 feed，但 alice 的通知列表与未读数不变

#### Scenario: 同轨迹去重

- **当** 同一轨迹再有 atom 命中同一 skill 并打分
- **那么** 不再产生第二条 feedback 事件

### Requirement: 评价措辞口径（3.3）

ux 分数段 SHALL 统一翻译为语义措辞（铃铛/toast/世界消息同源）：
score ≥ 7 → 好评；score ≤ 4 → 差劲；其余 → 一般。push-edit 事件 SHALL
可点开对应分支引用的 diff。

#### Scenario: 差评措辞

- **当** feedback 事件均分为 3
- **那么** 通知与 feed 中显示"差劲"徽章（rose 语义色），不显示裸数字为主体

### Requirement: 通知读侧（Q6：登录可见，只读实例不挂）

系统 SHALL 提供 `GET /events?scope=me|world`（分页 before_id）、
`GET /events/unread`、`POST /events/read`，全部要求登录（console 路由，
独立只读实例物理不挂载）。已读状态 SHALL 为每用户一个只前进的游标；
重复/乱序标记幂等。

#### Scenario: 匿名 401

- **当** 未登录请求 /events
- **那么** 401

#### Scenario: 游标只前进

- **当** 用户已读到事件 id=5，随后请求标记 last_id=2
- **那么** 未读数不变（游标不倒退）

### Requirement: 通知三层展示（D10：Web Notifications 是 HTTPS 增强而非基线）

前端 SHALL 提供：① 全局铃铛 + 未读数（所有页面，30s 轮询）；② 页面内右下角
toast（新事件到达时弹出，首轮对齐水位不弹历史）；③ 浏览器系统通知——仅在
secure context（HTTPS）下提供，授权入口在铃铛下拉；`Notification` API 不存在
时 SHALL 显式标注"需 HTTPS 部署"，SHALL NOT 视为错误。世界消息 SHALL 为
卡片式 feed：头像 + 富文本句（skill 名为可点 chip、分数为语义徽章）+ 按
今天/昨天分组；只含 skill 名与动作，SHALL NOT 展示轨迹内容。

#### Scenario: 内网 http 降级

- **当** 看板经 `http://host:port` 打开（非 secure context）
- **那么** 铃铛与 toast 正常工作，铃铛下拉显示"系统通知需 HTTPS 部署"
