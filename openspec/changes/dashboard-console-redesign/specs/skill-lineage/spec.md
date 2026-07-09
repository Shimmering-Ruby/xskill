## ADDED Requirements

### Requirement: skill 血缘可从聚合钻取到明细

skill 详情页 SHALL 提供血缘分区（`GET /api/v1/dashboard/skill/{name}/lineage`）：
贡献来源（按用户、按来源模型的计数与占比）与贡献原子列表（意图/用户/模型/weightscore/
进入版本）。列表行 SHALL 可点击跳转对应 atom 详情页。原子文件已被清理的行 SHALL 显式
标注"源已清理"并保留可得字段，SHALL NOT 静默省略该行。

#### Scenario: 断链显式标注

- **当** 某贡献原子的 JSON 文件已被清理，但 candidates/adoption 记录仍在
- **那么** 血缘列表中该行显示"源已清理"，weightscore 等仍展示

### Requirement: 得分趋势按日聚合且可下钻

skill 详情页 SHALL 提供 ux 得分按日趋势（`GET /api/v1/dashboard/skill/{name}/ux/daily`）：
main 与 staging 两条 series，版本切换点标注。趋势点 SHALL 可下钻到当日贡献的 atom 列表。

#### Scenario: 趋势点下钻

- **当** 用户点击趋势折线上某一天的数据点
- **那么** 展示当日计入该均值的 atom 列表（id/意图/分数）
