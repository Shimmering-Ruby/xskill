# Design — Dashboard 重设计（看板→分析工具 + 控制面）

> 状态：草案。§2 逐条点评需求的可行性与数据缺口；§3 是待拍板的决策；§5 Open Questions
> 讨论完再写 `specs/` 的 SHALL 条款。

## 1. Context（现状盘点，先观测后控制）

### 1.1 已有的（比想象中多）

| 资产 | 位置 | 对本设计的意义 |
| --- | --- | --- |
| dashboard v0 | `src/xskill/dashboard/`（router 424 行 / metrics 480 行 / 零构建前端） | 骨架可演进，不推倒重来 |
| skill 版本端点 | `/skill/{name}/tree,/file,/diff,/ux,/ux/atoms`（`_git_versions` 线性列表） | 进化 graph 只差 DAG 化 + 前端渲染 |
| 埋点三件套 | `recommendation_log(client_id,skill,side,bucket)` / `atom_adoption(atom_id,skill)` / `canary_decision` | 血缘与触发分析的原料 |
| 推荐引擎 | `SkillRecommendEngine` + `RecoStore`（users_for_skill/skills_for_user 双向）+ `ProfileStore`（≤5 聚类中心） | 控制面"推给我的"/admin 视图直接读它 |
| 稳定身份 | `connect --name` → 确定性 client_id；server 端 sessions 桶目录名 = user_name 明文 | dashboard 登录与归因的身份键已存在 |
| ux 按版本聚合 | `canary.aggregate_ux_by_version` + `.uxscores` | 得分趋势折线的数据源 |
| atom 结构 | `atom_id` 内嵌 `traj_id`、`pre/post_atom_id` 链表、`offset_start/end`、`tags`、`used_skills`、`source_model` | 详情页与关系图全部字段都在 |
| canary 侧 | `AtomCanary` / `SessionAssignments` / `check_and_decide` 裁决历史 | 进化 graph 节点标注 |

### 1.2 数据缺口（诚实清单）

| 缺口 | 现状 | 影响 | 修法 |
| --- | --- | --- | --- |
| **traj 无 client_id** | `trajectories` 表无该列；CS 模式下只能靠 sessions 桶目录名（=user_name）反查 | 推荐触发率只能 skill 粗粒度；"我触发过什么"、"我的 skill 被谁用了"算不准 | P2 加列，bridge 入库时写入；存量手动迁移（目录名→回填），不做兼容读 |
| **无显式用户反馈** | ux_score 是自动算的，没有"A 给 skill 打 👍/👎/评论"的动作 | "表示好评/差劲"的通知没有事实来源 | P3 决策点（§5 Q3） |
| **无事件流** | 各埋点表是孤立的宽表，没有统一 events | 通知/世界消息无处可读 | P3 新 `events` 表 + 埋点 |
| **原子点向量不落盘** | `ProfileStore` 只存聚类中心，原子 embedding 现算 | 画像散点图要么现算（慢）要么补存 | P3 决策点（§5 Q4） |
| **指标口径 bug** | 用户判定"很多指标代码层面不通、数字不可信"（具体清单待审计产出） | 看板可信度为负 | P1 第一任务：审计 |
| **CDN 依赖** | index.html 从 jsdelivr 拉 Tabler CSS | 内网部署样式裸奔 | P1 vendor 化 |

### 1.3 部署形态约束

dashboard 有两种运行形态（见 `reference_dashboard_local_debug`）：serve 内置挂载（读写皆可）
和**独立只读实例**（公网 dashboarddemo，systemd 常驻）。控制面的所有写操作（pin/反馈）
只在 serve 内置形态开放；只读实例保持纯读——这是安全边界，不是可选项。

## 2. 逐条点评用户需求

### 看板侧

**A1. skill 进化 git tree+node + 版本 diff** — ✅ 数据全在，P1。
`git log --all --parents` 出 DAG（main+staging 两分支），节点挂三类标注：canary 裁决
（promoted/rejected/timeout，来自 `canary_decision`）、该版本 ux 均分（`aggregate_ux_by_version`）、
是否当前 main/staging HEAD。前端手写 SVG 渲染（skill 的 commit 数量级是几十，不需要图库）；
点节点 → 复用既有 `/skill/{name}/diff?sha`。新 API：`GET /skill/{name}/graph`。

**A2. 得分趋势 + 血缘（来自哪些原子/用户/轨迹/模型）** — ✅ 链路可拉通，P1 主菜。
血缘链：skill ← atoms（两源：`.candidates.yml` 的贡献记录 + `atom_adoption` 表）
← traj（`atom_id` 内嵌 `traj_id`）← `source_model`（registry 有）← user（CS 模式：
sessions 桶目录名；P2 加 `client_id` 列后精确）。
得分趋势：`.uxscores` 按版本/按天折线，每个数据点可下钻到贡献它的 atoms（`/ux/atoms` 已有雏形）。
呈现为 skill 详情页的"血缘"分区：趋势折线 + 来源构成（按模型/按用户柱状）+ atom 列表钻取。
**注意**：单机（非 CS）模式只有一个隐式用户，"按用户"维度自然退化，不造假数据。

**A3. 指标审计（"数字不可信"）** — ✅ 完全同意，且必须是 P1 第一件事。
先审计后扩建：逐个指标产出「定义 / SQL 口径 / 数据源 / 已知误差」四元组，写成
`docs/dashboard-metrics.md`（前端 ⓘ tooltip 与之同源）。审计结论三分类：**保留**（口径成文）、
**修复**（如触发率在 P2 归因后升级为用户级）、**删除**（代码层面不通、无法修的直接下线）。
不陈列任何"需要打星号解释才敢放的数字"。

**A4. 折线图/柱状图** — ✅ P1，但有一个前置决策（§5 Q5）。
现状只有手写 sparkline。约束：内网可用（不能 CDN）+ 零构建。建议 vendor uPlot
（~45KB 单文件，折线/柱状够用）；关系图/进化树用手写 SVG（数据量小）。

**A5. 画像降维散点（tsne？）** — ⚠️ 可做，但降维算法受"不引重包"约束，P3。
t-SNE 没有现成 numpy-only 依赖（sklearn/openTSNE 都是重包）；纯 numpy 手写 t-SNE 约百行、
但慢且带感知超参。建议：**PCA 先行**（numpy SVD 十行、确定性、可解释），同一投影里画
用户原子点（小点）+ 聚类中心（大点）+ skill 向量（另一形状），hover 预览 / 点击跳详情。
t-SNE 作为 P3 内的可选增强，只在 PCA 明显分不开类群时再上。另一个坑：原子 embedding
现算（1.2 表），需要决定现算 or 补存（§5 Q4）。

**A6. 每用户 tag 占比** — ✅ 数据在（`AtomTask.tags`），前端已有标签云 + highlightUser 雏形，
P1 顺手补占比数字与按用户切分视图。

**A7. 推荐列表 × 触发情况（低价值 skill 识别）** — ⚠️ 分两步。
"推给谁"已精确（`recommendation_log` 有 client_id）；"谁触发了"当前只能 skill 粗粒度。
P1 先上粗粒度版（诚实标注口径），P2 `client_id` 归因落地后升级为用户级精确版，并加
"常推不用"排行（推送次数高 × 触发率低 = 候选下架/重写的 skill）——这正是"分析工具"
该回答的问题。

**A8. 轨迹/原子详情页 + 关系图** — ✅ 数据全在文件系统 + registry，纯读，P1。
traj 详情页：元信息（来源/模型/harness/状态/ux）+ 原子时间线（链表序）。
atom 详情页：全字段 + 按 offset 切原文预览 + 去向（进了哪个 skill、weightscore）。
关系图：traj—atom—skill 二部图（手写 SVG，force 布局不必要，分层布局即可）。
新 API：`GET /traj/{id}`、`GET /traj/{id}/atoms`、`GET /atom/{id}`（含原文切片）。

### 控制面侧

**B1. 管理员/普通用户角色** — ✅ 方向对，登录机制要拍板（§5 Q2）。
身份键复用 `connect --name`（不另造一套账号体系）。角色：config `dashboard.admins:
[name,...]`。现有 `DashboardAccessMiddleware` 演进为 session 登录（用户名 + 口令），
角色注入 request state，写端点校验。

**B2. 普通用户："推给我的/我触发的/skill 详情/语义检索 + pin"** — ✅ 读侧几乎全是现成引擎的
投影：`RecoStore.skills_for_user`（推给我的）、`relevance_search`（语义检索）、A1/A2 的
skill 详情复用。"我触发的"依赖 P2 归因。

**B3. Pin（用户自 pin + admin per-user/global pin）** — ✅ 新概念，设计如下，P2。
新 `pins` 表：`(user_id | '*global*', skill_name, pinned_by, ts)`。`build_manifest` 注入
顺序改为：**pinned 先占位 → ranked-80 → recommended-20 回填**（总量仍 ≤100 slot；pinned
超量是配置错误，直接报错不静默截断——no fallback）。用户 pin 记 `pinned_by=self`，admin
代 pin 记 admin 的 name，详情页可见"谁 pin 的"。**skill 来源展示**：pin 列表每项标注来源
（本仓蒸馏 / skillhub 三方 / admin 指派）。

**B4. 我的贡献去向（社交正反馈）** — ✅ 非常认同这是差异化功能，P2 读侧 + P3 通知。
数据闭环：我的 trajs → atoms → `atom_adoption`/candidates 进了哪些 skill →
`recommendation_log` 推给了谁 + 他人 atoms 的 `used_skills` 谁真用了。
页面："我的贡献"分区 —— 我贡献了 N 条轨迹 → 蒸馏进 M 个 skill → 被 K 个用户触发过，
每级可钻取。依赖 P2 归因列。

**B5. 通知气泡 + 世界消息** — ⚠️ 值得做，但"好评/差劲"缺事实来源，P3。
新 `events` 表 `(ts, type, actor, target_user, skill, payload)`，埋点四处：他人触发你贡献的
skill（watcher 检出 used_skills 时）、canary 裁决你贡献的 skill、你被 admin pin 了 skill、
显式反馈（若 Q3 通过）。前端简单轮询（看板已是轮询模式，不上 SSE），右下角气泡 + 通知
中心，点击跳转。世界消息 = events 的全局 feed 页（脱敏：只放 skill 名与动作，不放轨迹内容）。
**但**："A 表示好评/提出修改意见/表示差劲"需要显式反馈动作——当前只有自动 ux 分，没有
用户主动评价。要么 P3 加 👍/👎/短评（新特性，Q3），要么第一版通知只播"使用了"不播"评价了"。

**B6. admin：看每人被推了什么/pin 了什么，手动 pin** — ✅ `RecoStore` 双向记录 + B3 pins 表
的直接投影，P2。

**B7. admin：基于画像的用户聚类交互 graph** — ✅ `mean_tensor` 相似度矩阵已可算
（`find_friend` 就是它的 KNN 视图），P3。呈现：用户为节点、相似度 > 阈值连边、手写
force-directed（用户数是十的量级，百行内搞定，不引 d3）。点边看两人共同 tag/共同 skill。

### 验收方式

**✅ 强烈同意，且升格为交付物**：每期 PR 附 `docs/acceptance/dashboard-<phase>.md`，
格式为行为清单（Given/When/Then，面向"打开页面→点什么→看到什么"），人可以照着手工验收。
其中可自动化的子集用 playwright（node playwright 环境已就绪）写成 E2E 行为测试，挂进
`make e2e` 的 Docker 场景（CS server + 2 个模拟 client 喂数据 → 断言页面行为）。
openspec `specs/` 的 Scenario 与验收文档同源，避免两套口径。

## 3. Decisions（待确认）

**D1 分期与 PR 切法**：P1 纯读分析（指标审计/进化 graph/血缘/详情页/图表/vendor）→
P2 身份+归因+pin → P3 events+社交+画像可视化。每期独立 PR、独立验收文档。
理由：P1 无 schema 变更无权限变更，风险最低、当下痛点最痛；P2 的 `client_id` 归因是
P3 社交的地基，顺序不可换。

**D2 身份复用不另造**：dashboard 登录 = `connect --name` 的 user_name，admin 名单进 config。
不引入独立的 dashboard 账号表。

**D3 前端保持零构建 + 全 vendor**：不上 React/构建链；图表 vendor uPlot，图（graph）类
全部手写 SVG（数据量小）。内网无 CDN 是硬约束。

**D4 写操作只在 serve 内置形态**：独立只读实例（公网 demo）物理上不挂写路由（不是靠
中间件挡，是根本不 include），杜绝配置失误。

**D5 归因列手动迁移**：`trajectories.client_id` 新列由 bridge 入库写入；存量数据写一次性
迁移脚本按 sessions 桶目录名回填，跑完即弃。代码不写"列可能不存在"的兼容分支。

**D6 no-fallback 贯穿**：血缘断链（atom 文件被清理）在 UI 显式标"源已清理"而不是静默跳过；
pin 超 slot 报错；指标算不出就不显示该卡片，不显示 0 或假数。

## 4. 测试

- `metrics` 新聚合方法：内存 registry 喂样本单测（空库/断链/单用户边界）。
- graph/血缘 API：真 git repo fixture（两分支 + 裁决记录）断言 DAG 形状。
- pin：manifest 注入顺序、超量报错、global vs per-user 优先级单测。
- 登录/角色：中间件单测（匿名 403 / 普通用户写 admin 端点 403）。
- E2E：见 §2 验收方式。

## 5. Open Questions（讨论后拍板）

- **Q1 分期认可度**：P1/P2/P3 的切法与顺序是否 OK？第一个 PR 就按 P1 全量，还是再拆
  （比如指标审计+vendor 先单独出）？
- **Q2 普通用户登录机制**：(a) server 给每个 name 发 dashboard token（connect 时打印，安全但
  多一步）；(b) 用户名 + 全局共享口令（弱，内网可接受）；(c) 内网免密、输 name 即登录
  （最顺滑，靠内网边界）。admin 建议无论如何单独强口令。
- **Q3 显式 skill 反馈要不要**：👍/👎/短评是"好评/差劲"通知的事实来源，也是社交属性的核心
  一环；但它是新的产品行为（用户要动手）。做（P3）还是先只播"使用了"？
- **Q4 画像散点的原子向量**：现算（慢、无 schema 变更）还是入库补存（快、加存储）？
  倾向：P3 时在 `ProfileStore` 旁加 points 落盘（更新画像时顺手存，无额外 LLM 调用）。
- **Q5 降维算法**：PCA 先行、t-SNE 手写做可选增强——认可吗？还是必须一步到位 t-SNE？
- **Q6 世界消息的边界**：全局 feed 对所有登录用户可见？匿名（未登录只读实例）是否可见？
  倾向：登录可见、只读实例不挂。
