# Tasks — Dashboard 重设计

> 骨架版。Open Questions（design.md §5）拍板后细化到可执行粒度并补 specs/。

## P1 看板 → 分析工具（纯读）

- [ ] 1.1 指标审计：逐指标产出「定义/SQL 口径/数据源/已知误差」→ `docs/dashboard-metrics.md`；不可信指标修或删
- [ ] 1.2 静态资产 vendor 化：Tabler CSS + uPlot 进 `static/`，去 CDN；内网冒烟
- [ ] 1.3 `GET /skill/{name}/graph`：main+staging commit DAG + canary 裁决 + 版本 ux 标注；前端 SVG tree+node，点节点 diff
- [ ] 1.4 skill 血缘分区：得分趋势折线（按版本/按天，可钻取 atoms）+ 来源构成（模型/用户柱状）
- [ ] 1.5 traj/atom 详情页：`GET /traj/{id}` `/traj/{id}/atoms` `/atom/{id}`（含原文切片）；traj—atom—skill 关系图
- [ ] 1.6 tag 占比按用户切分视图
- [ ] 1.7 推荐×触发（skill 粗粒度版，口径诚实标注）
- [ ] 1.8 单测（空库/断链/git fixture 边界）+ 验收文档 `docs/acceptance/dashboard-p1.md` + playwright E2E

## P2 身份 + 控制面 + Pin

- [ ] 2.1 `trajectories.client_id` 列 + bridge 入库写入 + 存量一次性迁移脚本（跑完即弃）
- [ ] 2.2 dashboard 登录（复用 connect --name 身份）+ `dashboard.admins` 角色 + 中间件演进
- [ ] 2.3 普通用户视图：推给我的 / 我触发的 / skill 详情复用 / 语义检索
- [ ] 2.4 `pins` 表 + `build_manifest` pinned 优先注入（超量报错）+ 用户自 pin / admin per-user & global pin
- [ ] 2.5 "我的贡献去向"：traj → skill → 使用者钻取
- [ ] 2.6 推荐触发率升级为用户级精确口径 + "常推不用"排行
- [ ] 2.7 写端点只挂 serve 内置形态；只读实例物理不挂载
- [ ] 2.8 单测 + 验收文档 + E2E（CS server + 2 模拟 client）

## P3 社交 + 画像可视化

- [ ] 3.1 `events` 表 + 四类埋点（他人触发+原子 ux 打分即评价 / push-edit 修改分支即修改意见 / canary 裁决 / 被 pin）
- [ ] 3.2 通知气泡 + 通知中心 + 世界消息 feed（轮询）
- [ ] 3.3 评价事件口径：ux 分数段 → 好评/差劲措辞；push-edit 事件带分支引用可点开 diff
- [ ] 3.4 画像散点：numpy PCA 投影（原子点+中心+skill），hover 预览/点击跳转；t-SNE 可选增强
- [ ] 3.5 admin 用户聚类交互 graph（mean_tensor 相似度，手写 force 布局）
- [ ] 3.6 单测 + 验收文档 + E2E
