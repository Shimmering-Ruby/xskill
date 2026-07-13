# user-cluster-graph — admin 用户聚类 graph（P3-3.5）

## ADDED Requirements

### Requirement: 聚类 graph 端点（admin）

系统 SHALL 提供 `GET /admin/cluster-graph`（仅 admin）：节点=有画像的用户
（附原子点数与 top tags），边=`mean_tensor` 余弦相似度 > 0.6（附相似度、
共同 top tag、共同 used skill）。无边节点 SHALL 标 `isolated`（前端灰点标
"冷启动"）。mean_tensor 维度不一致（换过 embedding 模型）SHALL 不连边、
不报错。画像库不存在 → 404。

#### Scenario: 孤立节点标冷启动

- **当** 某用户的兴趣向量与所有人相似度 ≤ 0.6
- **那么** 该节点 isolated=true，前端灰色渲染并标注"冷启动"

#### Scenario: 相似用户连边带注记

- **当** 两用户 mean_tensor 余弦相似度 0.9 且都用过 sk1
- **那么** 边 sim=0.9、common_skills 含 sk1，悬停边显示注记

### Requirement: 前端 force 布局

前端 SHALL 手写 force-directed 布局（用户十的量级，无第三方图形库）：
节点大小 ∝ 原子数、边粗细 ∝ 相似度、初始位置确定性（布局可复现）。
点击节点 SHALL 跳该用户画像散点。
