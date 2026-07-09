# Dashboard 重设计：从"陈列看板"到"分析工具 + 控制面"
# Dashboard Redesign: from "display board" to "analysis tool + control plane"

> 状态：**设计草案（讨论中）**。本 proposal 覆盖三期范围的总体设计；实现按期出 PR。
> 未决问题见 `design.md` §Open Questions，拍板后补 `specs/` 与细化 `tasks.md`。

## Why

当前 `src/xskill/dashboard/` v0 是一个**只读陈列看板**：数字铺在卡片里，但回答不了任何
"为什么"。具体痛点（按用户原话归纳）：

1. **skill 版本不直观**：skill 由 git 管理（main/staging 双分支 + canary 裁决），但看板只有
   线性版本列表；应该是 git tree + node 的结构，点节点看版本、看 diff。
2. **有评价没血缘**：我们有 ux 评分、推荐引擎、双向推荐记录（`RecoStore`），但一个 skill 的
   **得分趋势来自哪些原子、哪些用户、哪些轨迹、什么模型的轨迹**——全都看不到。我们要的是
   分析工具，不是数据陈列。
3. **指标不可信**：部分指标在代码层面是不通的、口径有 bug，数字不可信；陈列不可信的数字
   比不陈列更糟。且该用折线图/柱状图的地方全是干巴巴的数字。
4. **画像不可见**：`ClientInterest` 已有 ≤5 聚类中心的多兴趣画像，但没有可视化——用户原子
   点/中心点/skill 的类群结构、每用户 tag 占比、推荐列表×触发情况，都看不到。
5. **轨迹/原子没有详情页**：存了轨迹、存了原子，但没有任何页面能看单条轨迹的原子切分、
   单个原子的内容，也没有 traj—atom—skill 的关系图。
6. **没有控制面**：dashboard 无身份概念（单一 Basic 口令），普通用户看不到"我被推了什么、
   我贡献的轨迹去了哪、我的 skill 被谁用了"；管理员没法人工 pin skill 给某人/全员；
   没有任何通知/世界消息机制让"你的 skill 被别人用了"这种正反馈流动起来。

## What Changes

三期交付（每期一个独立可合并的 PR）：

### P1 看板 → 分析工具（纯读，不动权限模型）

- **指标审计**（第一件事）：逐个审计现有 overview/rates/by-domain 指标的口径与数据链路，
  产出指标口径文档；不可信的**修或删**，不许陈列假数字。已知问题：推荐触发率是 skill
  粗粒度（`trajectories` 无 `client_id`）。
- **静态资产 vendor 化**：index.html 现从 jsdelivr CDN 拉 Tabler CSS——内网部署直接裸奔。
  全部静态资产（CSS/图表库）vendor 进 `static/`。
- **skill 进化 graph**：`/skill/{name}/graph` 返回 main+staging 的 commit DAG（含 canary
  裁决、每版本 ux 均分标注），前端渲染 tree+node，点节点看 diff（复用既有 `/diff`）。
- **skill 血缘页**：skill ← 组成它的 atoms（`.candidates.yml` + `atom_adoption`）← 所属
  trajectories ← (source_model, user)。得分趋势折线（按版本/时间）、来源构成柱状。
- **轨迹/原子详情页**：traj 页 = 原子时间线（链表序）+ 每原子 intent/summary/tags/ux/
  used_skills；atom 页 = 全字段 + 原文切片（offset 定位）+ 去向（进了哪个 skill）。
  traj—atom—skill 二部关系图。
- **图表化**：关键趋势用折线、构成用柱状/占比，替换纯数字陈列。

### P2 身份 + 普通用户控制面 + Pin

- **归因地基**：`trajectories` 表加 `client_id` 列（CS bridge 入库时写入；按 CLAUDE.md
  规则手动迁移存量，不做兼容读）。用户级"推荐→触发"闭环自此可算。
- **dashboard 登录**：复用 `connect --name` 身份体系；`dashboard.admins` 配置角色。
- **普通用户视图**："推给我的 skill"（`RecoStore.skills_for_user`）、我触发过的 skill、
  skill 详情（血缘/评价/触发次数/推送命中率）、语义检索（复用 `relevance_search`）。
- **Pin**：用户 pin skill = 持久化进自己的 manifest；管理员可 per-user pin / global pin。
  新 `pins` 表 + `build_manifest` 注入 pinned 优先占位。这是 dashboard 首批**写操作**，
  写端点须登录+角色校验；公网只读实例（dashboarddemo）不开写。
- **我的贡献去向**：我的轨迹 → 原子 → 进了哪些 skill → 被哪些人使用（社交正反馈的数据层）。

### P3 社交通知 + 画像可视化 + admin 聚类

- **events 表 + 通知**：埋点四类，全部消费既有事实源、不新增用户动作（review 已拍板）：
  他人触发你贡献的 skill + 原子 ux 打分即评价（"A 使用了你的 skill 打了 8.5 分"）/
  push-edit 修改分支即修改意见（"A 对你的 skill 提交了修改"）/ canary 裁决 / 被 pin。
  前端轮询，右下角气泡通知可点击跳转；全局"世界消息" feed。
- **画像散点可视化**：用户原子点 + 聚类中心 + skill 向量降维（numpy-only PCA 先行，
  t-SNE 可选增强）到 2D 散点，hover 预览、点击跳详情；每用户 tag 占比。
- **admin 用户聚类 graph**：基于 `mean_tensor` 相似度的交互式力导向图，看协作类群。

## Capabilities

### New Capabilities

- `dashboard-metric-audit`: 指标口径审计与修伪——每个展示指标有成文口径、数据源、已知误差；不可信指标修复或删除。
- `skill-evolution-graph`: skill git 进化 tree+node 可视化 + 任意节点 diff + canary 裁决标注。
- `skill-lineage`: skill 血缘分析——得分趋势 + atoms/trajectories/users/models 来源钻取。
- `traj-atom-explorer`: 轨迹/原子详情页 + traj—atom—skill 关系图。
- `dashboard-identity`: dashboard 登录（复用 connect --name 身份）+ admin/普通用户角色。
- `traj-attribution`: `trajectories.client_id` 归因列，用户级推荐→触发闭环。
- `skill-pin`: 用户自 pin / 管理员 per-user & global pin，manifest 优先注入。
- `contribution-tracking`: "我的轨迹贡献的 skill 被谁使用"的去向追踪。
- `dashboard-events`: 事件埋点 + 个人通知气泡 + 世界消息 feed。
- `profile-visualization`: 画像降维散点 + tag 占比 + admin 用户聚类 graph。

### Modified Capabilities

- 现有 dashboard v0 端点：指标审计后部分口径修正/删除；静态资产 vendor 化（去 CDN）。
- `skill_manifest.build_manifest`：注入 pinned 优先占位（P2）。

## Impact

- **`src/xskill/dashboard/`**：主战场——metrics/router 扩展、前端重构（保持零构建 SPA-lite）、
  登录与角色中间件（演进 `security.py`）。
- **`src/xskill/pipeline/registry.py`**：`trajectories.client_id` 列（手动迁移）、`pins` 表、
  `events` 表、若干血缘聚合查询。
- **`src/xskill/team/server/`**：bridge 入库写 `client_id`；`build_manifest` pinned 注入；
  `RecoStore` 读扩展。
- **`src/xskill/recommend/`**：只读复用（`relevance_search`/`find_friend`/`ProfileStore`），
  P3 加 numpy-only PCA（可选 t-SNE）。
- **依赖**：不新增 Python 重包；前端图表库 vendor 进 static（候选 uPlot，~45KB，无构建）。
- **验收**：每期附行为验收文档（Given/When/Then 清单），playwright 驱动的 E2E 行为测试
  纳入 `make e2e`；单测覆盖所有新聚合/写路径。
