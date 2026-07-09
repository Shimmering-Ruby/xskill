## ADDED Requirements

### Requirement: 轨迹详情页展示原子时间线

dashboard SHALL 提供轨迹详情页（`GET /api/v1/dashboard/traj/{id}` +
`/traj/{id}/atoms`）：轨迹元信息（harness/模型/状态/原子数/平均 ux）+ 原子时间线
（按 `pre/post_atom_id` 链表序）。时间线节点 SHALL 展示 intent 缩略、ux、used_skills，
点击 SHALL 展开该 atom 详情。

#### Scenario: 链表序展示

- **当** 一条轨迹切出 5 个 atom（链表相连）
- **那么** 时间线按链表序（非文件名序）从左到右排列 5 个节点

### Requirement: 原子详情页含原文切片与去向

atom 详情页（`GET /api/v1/dashboard/atom/{id}`）SHALL 展示全部关键字段
（intent/summary/tags/used_skills/offset）、按 offset 从轨迹原文截取的只读切片、
以及去向（进入哪个 skill、weightscore），去向 SHALL 链接到该 skill 的血缘分区。
原文文件缺失时 SHALL 显式报"源已清理"，SHALL NOT 返回空白假装无内容。

#### Scenario: 原文切片定位

- **当** atom 的 offset 为 [12400, 18220]
- **那么** 详情页原文区展示轨迹原文该区间的文本（可截断展示并标注总长）

### Requirement: traj—atom—skill 关系图

轨迹详情页 SHALL 渲染 traj—atom—skill 二部关系图：轨迹节点连接其全部 atom，atom 与其
贡献进入的 skill 之间画贡献边（标 weightscore）。任意节点点击 SHALL 跳转对应详情页。

#### Scenario: 贡献边可辨

- **当** 轨迹的 atom 0003 进入了 skill X（weightscore 4.2），其余 atom 无贡献
- **那么** 图中仅 0003→X 有高亮贡献边并标注 4.2
