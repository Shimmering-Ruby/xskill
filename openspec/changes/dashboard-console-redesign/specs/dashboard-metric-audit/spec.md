## ADDED Requirements

### Requirement: 每个展示指标有成文口径且不陈列不可信数字

看板展示的每一个指标 SHALL 在 `docs/dashboard-metrics.md` 中有对应条目：定义、SQL 口径、
数据源、已知误差。前端指标卡的 ⓘ tooltip 内容 SHALL 与该文档同源。审计判定为"代码层面
不通/无法修复"的指标 SHALL 从前端下线，而非打星号继续陈列。指标算不出（数据缺失/除零）
时 SHALL 不渲染该卡片，SHALL NOT 显示 0 或占位假数。

#### Scenario: 指标卡口径可追溯

- **当** 用户 hover 总览页任一指标卡的 ⓘ
- **那么** tooltip 内容与 `docs/dashboard-metrics.md` 中该指标的定义一致

#### Scenario: 无数据时不显示假数

- **当** registry 为空库（无任何轨迹）
- **那么** 衍生率类指标卡不渲染，页面不出现 "0%" 或 "NaN"

### Requirement: 触发事实源为 atom 级 used_skills

触发次数/触发率类指标 SHALL 以 atom 级 `used_skills`（多值）为事实源聚合；SHALL NOT 使用
`trajectories.skill_used` 单值列（多 skill 轨迹被系统性低估）。

#### Scenario: 多 skill 轨迹计数不丢失

- **当** 一条轨迹的 3 个 atom 分别命中 skill A、A、B
- **那么** A 的触发计数 +2（或按去重口径 +1），B 的触发计数 +1，B 不被丢弃

### Requirement: 推荐曝光口径去注水

推荐次数（曝光）SHALL 按 `user × skill × 版本(sha)` 去重统计，SHALL NOT 随 `/sync`
频率线性膨胀。命中率 = 去重触发 / 去重曝光，SHALL 天然落在 [0,1]，SHALL NOT 依赖
封顶 100% 的补丁。

#### Scenario: 反复 sync 不虚增曝光

- **当** 同一 client 对同一 skill 版本连续 sync 50 次
- **那么** 该 (user, skill) 的曝光计数为 1
