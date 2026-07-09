# Design — Dashboard 重设计 0号提案（看板→分析工具 + 控制面）

> 状态：0号提案（可审阅的完整设计：信息架构 + 每类数据的展示方式 + 页面示意图）。
> 示意图源文件 `mockups/panels.html`（自包含 HTML，改后用 playwright 重截），
> 截图在 `mockups/img/`。§6 Open Questions 拍板后写 `specs/` SHALL 条款。

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
| push-edit | client 改本地 skill → `/push-edit` → `user-staging/<client_id>` 分支 | "修改意见"事件的事实源（D7） |

### 1.2 数据缺口（诚实清单）

| 缺口 | 现状 | 影响 | 修法 |
| --- | --- | --- | --- |
| **traj 无 client_id** | `trajectories` 表无该列；CS 模式下只能靠 sessions 桶目录名（=user_name）反查 | 推荐触发率只能 skill 粗粒度；"我触发过什么"、"我的 skill 被谁用了"算不准 | P2 加列，bridge 入库时写入；存量手动迁移（目录名→回填），不做兼容读 |
| **无事件流** | 各埋点表是孤立的宽表，没有统一 events | 通知/世界消息无处可读 | P3 新 `events` 表 + 埋点（事实源全部已有，见 D7） |
| **原子点向量不落盘** | `ProfileStore` 只存聚类中心，原子 embedding 现算 | 画像散点图要么现算（慢）要么补存 | P3 决策点（§6 Q4） |
| **指标口径 bug** | 用户判定"很多指标代码层面不通、数字不可信"（具体清单待审计产出） | 看板可信度为负 | P1 第一任务：审计 |
| **CDN 依赖** | index.html 从 jsdelivr 拉 Tabler CSS | 内网部署样式裸奔 | P1 vendor 化 |

### 1.3 部署形态约束

dashboard 有两种运行形态：serve 内置挂载（读写皆可）和**独立只读实例**（公网 demo，
systemd 常驻）。控制面的所有写操作（pin 等）只在 serve 内置形态开放；只读实例物理上
不挂写路由——这是安全边界，不是可选项。

## 2. 信息架构与页面设计（0号提案主体）

### 2.0 导航与角色

左侧栏七个一级分区，按角色裁剪：

| 分区 | 普通用户 | admin | 定位 |
| --- | --- | --- | --- |
| 总览 | ✓ | ✓ | 审计后的可信 KPI + 趋势，每卡可点进对应分析页 |
| 技能库 | ✓ | ✓ | 列表 → **skill 分析页**（图①，本设计核心页） |
| 轨迹 & 原子 | ✓ | ✓ | 列表 → traj 详情 → atom 详情（图②） |
| 用户 & 画像 | ✓（全员可见） | ✓ | 用户列表 → 用户画像页（图③）；互相可见是社交属性的一部分 |
| 灰度 Canary | ✓ | ✓ | v0 现有页改造（裁决历史 + 跳 skill 进化图） |
| 我的 | ✓ | ✓ | 普通用户控制面（图④），登录后默认落地页 |
| 管理 | — | ✓ | pin 管理 + 用户聚类（图⑤） |

### 2.1 Skill 分析页（图① `mockups/img/1-skill-detail.png`）

回答"这个 skill 从哪来、演化得怎么样、谁在用、值不值"。四个区：

**KPI 行**：平均 ux（带 vs 上一版本 delta）/ 被推荐→被触发（命中率）/ 贡献原子数（几个用户）/
版本数（main·staging 拆分）/ 当前灰度状态（观察中/裁决倒计时）。

**进化路径（git tree+node）**：双泳道 commit DAG（左 main、右 staging），时间自下而上。
- 节点色 = canary 裁决（status 色板：晋升=绿、回滚=红、灰度观察中=黄、普通提交=灰）；
  回滚节点直接标注原因（"5.9<7.3 表格解析劣化"）。
- push-edit 产生的用户修改节点标注作者（"alice push-edit 用户修改"）。
- 交互：点任意节点 → 右侧抽屉展示该版本文件树 + 与父节点的 diff（复用既有
  `/skill/{name}/diff`）；HEAD 节点空心描边突出。
- 渲染：手写 SVG（commit 量级几十，不需要图库）。数据源：`git log --all --parents` +
  `canary_decision` + `aggregate_ux_by_version`。新 API `GET /skill/{name}/graph`。

**得分趋势折线**：x=时间（日），y=ux 日均。main 实线 / staging 虚线（两 series，
categorical 蓝/青）；竖虚线 = 版本切换点（标 v3/v4/v5）。交互：crosshair + tooltip
（当日 main/staging 均分 + 贡献原子数），点击数据点下钻当日 atom 列表（复用 `/ux/atoms`）。
新 API `GET /skill/{name}/ux/daily`。

**血缘构成 + 贡献原子表**：
- 构成：两组水平条形（按用户 / 按来源模型），单一 sequential 蓝——这是 magnitude 不是
  identity，不上多色。
- 贡献原子表：原子 id / 意图 / 用户 / 模型 / weightscore / 进入版本；点行跳 atom 详情页。
- 断链（原子文件已清理）显式标"源已清理"，不静默省略（no-fallback）。
- 新 API `GET /skill/{name}/lineage`（聚合 `.candidates.yml` + `atom_adoption` + traj 归因）。

### 2.2 轨迹 & 原子详情页（图② `mockups/img/2-traj-atom.png`）

**traj 详情**：元信息行（harness/模型/用户/日期/状态/原子数/平均 ux）+ **原子时间线**
（水平节点链，链表序 `pre/post_atom_id`；节点下标注 intent 缩略 + ux + used_skills；
点击选中展开下方详情卡）。

**atom 详情卡**：全字段表（intent/summary/tags/used_skills/去向/offset）+ **原文切片**
（按 offset 从 traj 原文截取，只读，等宽字体）。"去向"行直接链到 skill 血缘分区。

**关系图**：traj—atom—skill 二部图，分层布局（左轨迹、中原子、右 skill），贡献边加粗
高亮并标 weightscore。手写 SVG，点任意节点跳对应详情页。

新 API：`GET /traj/{id}`、`GET /traj/{id}/atoms`、`GET /atom/{id}`（含原文切片）。

### 2.3 用户画像页（图③ `mockups/img/3-user-profile.png`）

**画像散点**：该用户全部原子摘要向量 + skill 向量做 **PCA 2D 投影**（numpy SVD；t-SNE
为可选增强，§6 Q5）。原子=圆点按所属兴趣簇着色（≤5 簇 = ≤5 个 categorical 色，天然不超
色板上限）；兴趣中心=◆（同簇深一档）；skill 向量=▲黑色三角。簇旁直接标注簇语义名
（取簇内 top tag）。交互：hover 原子点 → tooltip 预览（atom id + summary + ux）；点击跳
atom 详情；点击 ▲ 跳 skill 分析页。

**tag 构成**：水平条形 + 占比%（212 原子的 top tags + "其他"折叠）。个人页用条形图保证
占比可读；标签云保留在团队总览页做氛围导航——两者不冲突。

**推荐 × 触发表**：每行一个被推荐 skill：来源 chip（团队蒸馏/skillhub 三方/admin 指派）/
推荐次数 / 触发次数 / 命中率 meter 条 / 最近触发 / **结论列**（status 色+文字：高价值 /
正常 / 常推不用→建议停推 / 零触发→建议停推）。这是"低价值 skill 识别"的落地形态：
结论直接可执行，不是又一个裸数字。

新 API：`GET /user/{id}/profile`、`/user/{id}/scatter`、`/user/{id}/reco-trigger`。

### 2.4 「我的」控制面（图④ `mockups/img/4-my-console.png`）

普通用户登录后的默认落地页：

- **语义检索 + pin**：检索框（复用 `relevance_search`，检索池=团队 main/staging +
  skillhub 三方）；结果行带来源 chip 和 pin 按钮。
- **推给我的**：表格，槽位 chip 标注 manifest 注入类型（pinned·自己 / pinned·admin /
  ranked / recommended）+ 我触发过的次数 + pin/取消 pin 操作。admin pin 的条目普通用户
  不可自行取消（显式置灰说明）。
- **我的贡献去向**：四级漏斗（我贡献的轨迹 → 切出原子 → 进入 skill → 被 N 人使用），
  ordinal 蓝 ramp（≥step250），每级点击钻取明细；下方"我贡献的 skill × 使用者 × 评价
  （ux 分）"表。
- **世界消息**：团队动态 feed（"m00323121 使用了 bob 蒸馏的 nginx-subpath-proxy，打了
  9.1 分"/"alice 对 3gpp-spec-lookup 提交了修改（push-edit 分支）"/灰度裁决/全局 pin）。
  只放 skill 名与动作，不放轨迹内容。
- **通知气泡**：右下角 toast（新事件轮询到即弹，点击跳转）+ 顶部未读计数。

新 API：`GET /my/manifest`、`/my/contributions`、`/events?target=me`、`POST /my/pin`。

### 2.5 管理页（admin，图⑤ `mockups/img/5-admin.png`）

- **用户 × 推送/pin 矩阵**：每行一个用户：被推荐数 / 已 pin 列表（chip 标注 pin 来源）/
  触发率 meter（异常低的用 serious 色）/ "代 pin…"操作。底部"全局 pin…"按钮 + 当前
  全局 pin 状态说明。
- **画像聚类 graph**：节点=用户（大小=原子数），边=`mean_tensor` 余弦相似度>0.6
  （粗细=相似度），手写 force-directed（用户十的量级）。点边 → tooltip 显示相似度值 +
  共同 top tag / 共同 skill；孤立节点灰色标"冷启动"。类群旁可加人工注记。

新 API：`GET /admin/users-matrix`、`POST /admin/pin`（per-user/global）、
`GET /admin/cluster-graph`。

### 2.6 总览页（v0 改造，无新 mockup）

指标审计（§3 A3）后重排：只留可信 KPI，每卡带口径 tooltip（与 `docs/dashboard-metrics.md`
同源）+ 点击跳对应分析页（从"陈列"变"分析入口"）。加近 30 日轨迹/原子/成本三条趋势
折线与按生态/模型分组柱状。

### 2.7 可视化规范（全站统一）

- **色板**：dataviz 参考色板（已通过 CVD/对比度验证：categorical 8 槽按固定顺序取用、
  sequential 单蓝 ramp、status 四色只用于状态永不当 series 用）。vendor 进 static/，
  以 CSS 变量落地，同套变量出深色模式。
- **图型选择原则**：magnitude→条形（单蓝）；趋势→折线（≤2 series）；识别→categorical
  且 ≤5；状态→status 色+图标+文字（永不只靠颜色）；构成→水平条形+百分比。**单轴**，
  不做双 y 轴。
- **交互默认**：折线带 crosshair+tooltip；条形/散点/节点 per-mark hover；一切图形节点
  可点击跳详情——"分析工具"的钻取原则：任何聚合数字都能追到明细。
- **实现**：折线/条形用 vendor 的 uPlot（~45KB 单文件）；DAG/时间线/二部图/散点/聚类
  graph 手写 SVG（数据量都在百级以下）。零构建、无 CDN。

## 3. 需求 → 设计映射（点评压缩版）

| 用户需求 | 结论 | 落点 |
| --- | --- | --- |
| skill git tree+node + diff | ✅ 数据全在 | §2.1 进化路径，P1 |
| 得分趋势 + 血缘（原子/用户/轨迹/模型） | ✅ 链路可拉通；用户维度 P2 归因后精确 | §2.1，P1 |
| 指标不可信 → 审计 | ✅ P1 第一件事 | 逐指标「定义/SQL 口径/数据源/已知误差」→ `docs/dashboard-metrics.md`；保留/修复/删除三分类 |
| 折线/柱状图 | ✅ | §2.7 规范，P1 |
| 画像降维散点（tsne?） | ⚠️ 无重包约束 | §2.3：PCA 先行，t-SNE 可选（§6 Q5），P3 |
| 每用户 tag 占比 | ✅ | §2.3 条形+%，P1 可先做团队级 |
| 推荐×触发、低价值识别 | ⚠️ 分两步 | §2.3 表格；P1 skill 粗粒度、P2 用户级精确+停推建议 |
| 轨迹/原子详情 + 关系图 | ✅ 纯读 | §2.2，P1 |
| 角色区分 | ✅ | §2.0；登录机制 §6 Q2 |
| 语义检索 + pin | ✅ | §2.4；pins 表 + manifest 注入 pinned→ranked→recommended，超量报错 |
| 我的贡献去向 + 被谁使用 | ✅ 差异化功能 | §2.4 漏斗，P2 |
| 通知气泡 + 世界消息 | ✅ 事实源已有（D7） | §2.4，P3 |
| admin 看推送/代 pin/全局 pin | ✅ | §2.5，P2 |
| admin 用户聚类 graph | ✅ | §2.5，P3 |
| 行为验收文档 | ✅ 升格为交付物 | §5 |

## 4. Decisions

**D1 分期与 PR 切法**：P1 纯读分析（指标审计/进化 graph/血缘/详情页/图表/vendor）→
P2 身份+归因+pin → P3 events+社交+画像可视化。每期独立 PR、独立验收文档。
P2 的 `client_id` 归因是 P3 社交的地基，顺序不可换。

**D2 身份复用不另造**：dashboard 登录 = `connect --name` 的 user_name，admin 名单进
config（`dashboard.admins`）。不引入独立的 dashboard 账号表。

**D3 前端保持零构建 + 全 vendor**：不上 React/构建链；图表 vendor uPlot，图（graph）类
全部手写 SVG。内网无 CDN 是硬约束。示意图 `mockups/panels.html` 即按此约束制作
（纯 HTML+SVG，无任何外部依赖），可直接演化为实现骨架。

**D4 写操作只在 serve 内置形态**：独立只读实例（公网 demo）物理上不挂写路由（不是靠
中间件挡，是根本不 include），杜绝配置失误。

**D5 归因列手动迁移**：`trajectories.client_id` 新列由 bridge 入库写入；存量数据写一次性
迁移脚本按 sessions 桶目录名回填，跑完即弃。代码不写"列可能不存在"的兼容分支。

**D6 no-fallback 贯穿**：血缘断链在 UI 显式标"源已清理"而不是静默跳过；pin 超 slot
报错；指标算不出就不显示该卡片，不显示 0 或假数。

**D7 评价与修改意见不新增反馈 UI（PR review 已拍板）**：好评/差劲从**原子打分**获得
（他人 atom `used_skills` 命中 × ux_score）；修改意见就是既有的 **push-edit 事件**
（client 改本地 skill → `user-staging/<client_id>` 分支）。events 只做既有事实源的消费者，
不引入 👍/👎/短评这类新的用户动作。

## 5. 测试与验收

- `metrics` 新聚合方法：内存 registry 喂样本单测（空库/断链/单用户边界）。
- graph/血缘 API：真 git repo fixture（两分支 + 裁决记录）断言 DAG 形状。
- pin：manifest 注入顺序、超量报错、global vs per-user 优先级单测。
- 登录/角色：中间件单测（匿名 403 / 普通用户写 admin 端点 403）。
- **行为验收**：每期 PR 附 `docs/acceptance/dashboard-<phase>.md`（Given/When/Then，
  面向"打开页面→点什么→看到什么"）；可自动化子集用 playwright 写 E2E 挂进 `make e2e`
  Docker 场景（CS server + 2 模拟 client 喂数据 → 断言页面行为）。openspec `specs/` 的
  Scenario 与验收文档同源。

## 6. Open Questions（讨论后拍板）

- **Q1 分期认可度**：P1/P2/P3 的切法与顺序是否 OK？第一个 PR 就按 P1 全量，还是再拆？
- **Q2 普通用户登录机制**：(a) server 给每个 name 发 dashboard token（connect 时打印）；
  (b) 用户名 + 全局共享口令；(c) 内网免密、输 name 即登录。admin 无论如何单独强口令。
- ~~Q3 显式 skill 反馈~~ → **已拍板见 D7**。
- **Q4 画像散点的原子向量**：现算（慢、无 schema 变更）还是入库补存（快、加存储）？
  倾向：P3 时在 `ProfileStore` 旁加 points 落盘（更新画像时顺手存，无额外 LLM 调用）。
- **Q5 降维算法**：PCA 先行、t-SNE 手写做可选增强——认可吗？还是必须一步到位 t-SNE？
- **Q6 世界消息的边界**：全局 feed 对所有登录用户可见？倾向：登录可见、只读实例不挂。
- **Q7 页面设计本身**（新，随示意图）：五张示意图（§2.1–2.5）的布局/图型选择是否认可？
  哪些区块要加/删/换形态？在 PR 上圈图点评即可。
