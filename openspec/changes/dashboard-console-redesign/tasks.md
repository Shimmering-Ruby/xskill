# Tasks — Dashboard 重设计

> 骨架版。Open Questions（design.md §5）拍板后细化到可执行粒度并补 specs/。

## P1 看板 → 分析工具（纯读）

- [ ] 1.1 指标审计：逐指标产出「定义/SQL 口径/数据源/已知误差」→ `docs/dashboard-metrics.md`；不可信指标修或删。点名两个已知系统性偏差：① recommendation_log 每次 sync 注水 → 定义曝光口径（user×skill×version 或按天去重）并拍板 ranked/pinned 是否计入；② `trajectories.skill_used` 单值 vs atom `used_skills` 多值 → 以 atom 级为触发事实源
- [ ] 1.1b canary 裁决可定位（D9）：`canary_decision` 加 `staging_sha/main_sha` 列；`discard_staging` 保留 `refs/rejected/<ts>-<sha>` 只读 ref；存量裁决图上标"无法定位"
- [ ] 1.2 静态资产 vendor 化：Tabler CSS + uPlot 进 `static/`，去 CDN；内网冒烟
- [ ] 1.3 `GET /skill/{name}/graph`：main+staging commit DAG + canary 裁决 + 版本 ux 标注；前端 SVG tree+node，点节点 diff
- [ ] 1.4 skill 血缘分区：得分趋势折线（按版本/按天，可钻取 atoms）+ 来源构成（模型/用户柱状）
- [ ] 1.5 traj/atom 详情页：`GET /traj/{id}` `/traj/{id}/atoms` `/atom/{id}`（含原文切片）；traj—atom—skill 关系图
- [ ] 1.6 tag 占比按用户切分视图
- [ ] 1.7 推荐×触发（skill 粗粒度版，口径诚实标注）
- [ ] 1.7b 总览页改造：可信 KPI + 蒸馏管线五阶段实时进度 + 冷启动屏障进度条 + 候选孵化 weightscore 进度（纯读：trajectories.status / cold_start / .candidates.yml）
- [ ] 1.7c 用户连接状态看板（读侧）：在线/离线（last_seen 阈值）、上次活跃、轨迹·原子、触发次数、harness/主力模型占比（样本不足显式标注）；版本列 P2 点亮
- [ ] 1.8 单测（空库/断链/git fixture 边界）+ 验收文档 `docs/acceptance/dashboard-p1.md` + playwright E2E

## P2 身份 + 控制面 + Pin

- [ ] 2.1 `trajectories.user_key` 列（canonical=user_name，D5）+ bridge 入库写入 + 存量按 sessions 桶目录名一次性回填（跑完即弃）；`metrics.skill_by_user` 等 label JOIN 口径废弃改读新列；client_id 表聚合层经 ClientRegistry 译成 user_name
- [ ] 2.2 dashboard 登录（复用 connect --name 身份）+ `dashboard.admins` 角色 + 中间件演进
- [ ] 2.3 普通用户视图：推给我的 / 我触发的 / skill 详情复用 / 语义检索
- [ ] 2.4 `skill_prefs` 表（pinned|blocked）+ `build_manifest` 注入（blocked 排除 → pinned 占位 → ranked → recommended；pinned 超量报错）+ 用户自 pin/屏蔽 + admin 代 pin/代屏蔽 & global pin
- [ ] 2.4b 数量伸缩交互：用户/admin 的 skill 清单右侧抽屉（搜索 + 分组 tab + 滚动）
- [ ] 2.4c admin 技能管理：下线（停止分发保留数据）/ 删除（二次确认输名）两段式；server 侧影响面单列——retire 状态需 build_manifest、推荐引擎候选池、canary controller 三处尊重；删除复用 skill_repo_lock 防与 watcher/canary 并发；定义"删后同名 skill 再生"语义
- [ ] 2.4d pinned 超量校验在写入侧（D8）：POST /my/prefs 与 admin/global pin 写入时拒绝，sync 路径永不报错
- [ ] 2.9 设置页：config.yaml 分段表单 + 原文编辑 + 仅校验/校验并热加载（失败不落盘直接报错）；热加载范围 dashboard/canary/recommend/skillhub，llm/watch_dirs 标注需重启
- [ ] 2.10 client 版本上报：register + sync 携带 X-Xskill-Version，server 写 clients.client_version（touch 时 upsert）；连接状态看板版本列 + "落后"标注点亮
- [ ] 2.5 "我的贡献去向"：traj → skill → 使用者钻取
- [ ] 2.6 推荐触发率升级为用户级精确口径 + "常推不用"排行
- [ ] 2.7 写端点只挂 serve 内置形态；只读实例物理不挂载
- [ ] 2.8 单测 + 验收文档 + E2E（CS server + 2 模拟 client）

## P3 社交 + 画像可视化

- [ ] 3.1 `events` 表 + 四类埋点（他人触发+原子 ux 打分即评价 / push-edit 修改分支即修改意见 / canary 裁决 / 被 pin）
- [ ] 3.2 全局通知三层：全局铃铛组件（所有页面）+ 页面内 toast + 浏览器系统通知（Web Notifications，授权入口在铃铛下拉）；世界消息卡片式 feed（头像/skill chip/语义徽章/按天分组）
- [ ] 3.3 评价事件口径：ux 分数段 → 好评/差劲措辞；push-edit 事件带分支引用可点开 diff
- [ ] 3.4 画像散点：numpy PCA 投影（原子点+中心+skill），hover 预览/点击跳转；t-SNE 可选增强
- [ ] 3.5 admin 用户聚类交互 graph（mean_tensor 相似度，手写 force 布局）
- [ ] 3.6 单测 + 验收文档 + E2E
