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
| **回滚节点 ref 不可达**（评审发现） | `discard_staging` 对被拒 staging 直接 `git branch -D`，`canary_decision` 表不存 sha | 进化图画不出回滚节点，裁决无法定位到 commit | P1：`canary_decision` 加 `staging_sha/main_sha` 列；reject 时保留只读 ref `refs/rejected/<ts>-<sha>`；存量裁决在图上显式标"无法定位到节点"（同 D6 断链风格），不做时间戳模糊匹配 |
| **身份键分裂**（评审发现） | `recommendation_log`/`RecoStore`/push-edit 分支用 client_id（hex），sessions 桶目录名/`watch_dirs.label` 用 user_name 明文 | 用户级 join 跨两种键域，数字对不上 | 拍板 canonical key = **user_name**；归因列命名 `user_key`；聚合层统一经 ClientRegistry 把 client_id 译成 user_name（见 D5） |
| **recommendation_log 注水**（评审发现） | `build_manifest` 每次 `/sync` 对每个 recommended slot 各插一条记录，推荐次数随 sync 频率线性膨胀；且只记 recommended 不记 ranked/pinned | "命中率"分母失真，正砸在"指标可信"卖点上 | P1 审计定义**曝光口径**（user×skill×version 去重或按天去重），并拍板 ranked/pinned 是否计入曝光 |
| **skill_used 单值**（评审发现） | `trajectories.skill_used` 单值列，而 `atom.used_skills` 多值 | 多 skill 轨迹触发计数被系统性低估，波及触发率全链 | P1 审计点名：以 atom 级 `used_skills` 为触发事实源 |
| **client 版本不上报** | RegisterRequest 无 version 字段，sync header 只有 token/client；server 不知道每个 client 跑什么版本 | 连接状态看板的版本列/“落后"标注无数据 | P2：register + sync 携带 `X-Xskill-Version`，server 写 `clients.client_version` 列（touch 时顺带 upsert） |

### 1.3 部署形态约束

dashboard 有两种运行形态：serve 内置挂载（读写皆可）和**独立只读实例**（公网 demo，
systemd 常驻）。控制面的所有写操作（pin 等）只在 serve 内置形态开放；只读实例物理上
不挂写路由——这是安全边界，不是可选项。

**公网只读实例内容白名单**（评审采纳）：只读实例只挂聚合类端点（KPI/趋势/分布）；
`/traj/{id}`、`/atom/{id}`（含轨迹原文切片）、用户画像与用户列表端点**物理不挂载**
（同写路由手法）——公网一道共享 Basic 口令挡不住轨迹原文级敏感内容。语义检索入口
同样只在 serve 内置形态渲染（依赖 embed_client，只读实例无 api_key，见 §2.4）。

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
| 管理 | — | ✓ | pin 管理 + 技能生命周期 + 用户聚类（图⑤） |
| 设置 | — | ✓ | config.yaml 分段表单 + 原文编辑 + 校验并热加载（图⑦） |

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

**贡献来源 + 贡献原子表**（v2 调整：弃"两组柱状图"，改列表内嵌占比条，更直观）：
- 贡献来源：按用户的头像列表 + 内嵌占比条 + 计数；来源模型收为计数 chip 行——都是
  magnitude，单色（teal），不上多色。
- 贡献原子表：意图 / 用户 / 模型 / weightscore 徽章 / 进入版本；点行跳 atom 详情页。
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

### 2.3 用户 & 画像（列表页图⑧ + 详情页图③）

**列表页 = 连接状态看板（图⑧ `mockups/img/8-users-status.png`，v5 补齐）**：每行一个用户——
- **在线状态**：绿点在线 / 灰点离线；在线 = `clients.last_seen` 在 2 个同步周期内
  （last_seen 每次 sync 鉴权 touch，现成数据）；上次活跃相对时间。
- **client 版本**：`clients.client_version` 新列（数据缺口：client 在 register 与每次
  sync 携带 `X-Xskill-Version`，server 写入——P2 轻量埋点）；低于 server 当前分发版本
  标"落后"（amber）。页头汇总"在线 x/N · 版本落后 y"。
- **使用统计**：累计轨迹·原子数、触发次数（= 该用户 atom `used_skills` 命中计数）。
- **harness / 主力模型**：按 `source_harness`/`source_model` 占比 chip（样本不足显式
  标注，不显示误导性 100%）。
- 点行进画像详情页（图③），详情页 header 同步展示版本/最后活跃/harness。

**详情页（图③ `mockups/img/3-user-profile.png`）**：

**画像散点**：该用户全部原子摘要向量 + skill 向量做 **t-SNE 2D 投影**（numpy 手写,
§6 Q5 修订——PCA 线性投影对高维语义簇分离效果差,改邻域保持的 t-SNE）。原子=圆点按所属兴趣簇着色（≤5 簇 = ≤5 个 categorical 色，天然不超
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
  skillhub 三方）；结果行带来源 chip 和 pin 按钮。**仅 serve 内置形态提供**——query
  编码依赖 embed_client，独立只读实例无 api_key，该形态下不渲染检索入口（评审采纳）。
- **推给我的（skill 自配置，v3）**：每行两个操作——**pin**（持久化推送）与 **✕ 屏蔽**
  （不再推送，进"已屏蔽"折叠组，可随时恢复）。槽位 chip 标注注入类型（pinned·自己 /
  pinned·admin / ranked / recommended）+ 我触发过的次数。admin pin 的条目普通用户不可
  取消/屏蔽（显式置灰说明）。列表遵守 §2.7 数量伸缩原则：默认 top-N + "查看全部 N 个"
  进抽屉（搜索 + 分组 + 滚动）。
- **偏好统一存储**：原 `pins` 表升级为 `skill_prefs(user_id | '*global*', skill_name,
  pref: pinned|blocked, set_by, ts)`；manifest 注入顺序：**blocked 先排除 → pinned 占位
  → ranked → recommended 回填**（pinned 超 slot 报错，no-fallback）。
- **我的贡献去向**（v2 调整：弃漏斗图，改四级步进指标 38→171→9→6，箭头连接、每级
  点击钻取——漏斗形状在这里是伪装饰，数字+箭头更直观）；下方"我贡献的 skill ×
  使用者头像 × 评价（ux 分徽章）"列表。
- **世界消息（v3 重设计，去呆板）**：卡片式动态 feed——头像 + 富文本句（skill 名为可点
  chip、分数为语义色徽章）+ 按"今天/昨天"分组 + hover 显示快捷跳转；只放 skill 名与
  动作，不放轨迹内容。
- **通知（v3：全局 + 浏览器系统级）**：三层——① 顶栏铃铛 + 未读数是**全局组件**，挂在
  所有页面；② 页面内右下角 toast（任意页面都会弹）；③ **浏览器系统通知**（Web
  Notifications API，用户授权一次后，即使浏览器最小化/在别的 tab 也能在系统层弹窗，
  点击唤起对应详情页）。授权入口在铃铛下拉里，未授权时降级为页面内 toast——这是能力
  分层不是 fallback 逻辑。

新 API：`GET /my/manifest`、`/my/contributions`、`/events?target=me`、
`POST /my/prefs`（pin/block/恢复）。

### 2.5 管理页（admin，图⑤ `mockups/img/5-admin.png`）

- **用户 × 推送/配置矩阵**：每行一个用户：被推荐数 / 配置摘要（pinned·blocked 计数
  chip，行内不铺全量）/ 触发率 meter（异常低的用 serious 色）/ "配置…"操作。点行或
  "配置…" → **右侧抽屉**打开该用户的全部被推荐 skill（§2.7 数量伸缩原则：搜索框 +
  分组 tab〔全部/pinned/已屏蔽〕+ 滚动列表，每行可代 pin / 代屏蔽）——量大也不挤不溢出。
  底部"全局 pin…"按钮 + **全局 pin 列表**（可多个，chip 列表逐个可移除；全局 pinned +
  该用户 pinned 合计仍受 slot 上限约束，超量报错——no-fallback）。
- **技能管理（v3 新增）**：skill 生命周期操作表——每行 skill + 状态徽章（在役/灰度中）+
  近 30 日使用数 + 操作：**下线**（停止分发与推荐，数据与 git 历史保留）与**删除**
  （彻底移除，需二次确认并输入 skill 名）。两段式设计：先下线观察、再删除，误删不可逆
  的动作永远隔一道闸。
- **画像聚类 graph**：节点=用户（大小=原子数），边=`mean_tensor` 余弦相似度>0.6
  （粗细=相似度），手写 force-directed（用户十的量级）。点边 → tooltip 显示相似度值 +
  共同 top tag / 共同 skill；孤立节点灰色标"冷启动"。类群旁可加人工注记。

新 API：`GET /admin/users-matrix`、`GET /admin/user/{id}/prefs`、`POST /admin/prefs`
（代 pin/代屏蔽/global）、`POST /admin/skill/{name}/retire`、
`DELETE /admin/skill/{name}`、`GET /admin/cluster-graph`。

### 2.6 总览页（图⑥ `mockups/img/6-overview.png`，v4 补齐）

- **可信 KPI 行**：轨迹/原子/skill/今日成本/平均 ux，每卡带口径 tooltip（与
  `docs/dashboard-metrics.md` 同源）+ 点击跳对应分析页（从"陈列"变"分析入口"）。
- **蒸馏管线实时进度**（v4 新增，回应"冷启动蒸馏进度不可见"）：五阶段步进
  （待拆分轨迹 → TaskAgent 拆分中 → 聚类分派中 → 候选累积 → 蒸馏/灰度中），
  活跃阶段脉冲点标记，点击阶段查看队列明细；数据源 = `trajectories.status` 计数 +
  watcher 在途状态。
- **冷启动屏障进度条**：已收集 N / 目标 M 条轨迹（`pipeline/cold_start.py` 状态）。
- **候选孵化进度**：每个候选 skill 的 weightscore 进度条（x / 阈值 10，
  `.candidates.yml` 累积值），baby 状态标签、贡献原子数、最后累积时间；
  "全部 N 个候选 →" 进列表。
- **近 30 日新增原子/日趋势** + **生态分布**占比条。

### 2.8 设置页（admin，图⑦ `mockups/img/7-settings.png`，v4 新增）

- **分段表单**：dashboard / canary / recommend / skillhub 段的关键字段以表单渲染
  （开关/数值），与右侧原文编辑器双向同步。
- **原文编辑器**：config.yaml 语法高亮，修改行高亮标注；"仅校验" 与 **"校验并热加载"**
  两个动作。校验失败：不落盘、不生效、直接展示错误（no-fallback，不存在"部分生效"）。
- **热加载范围显式声明**：dashboard/canary/recommend/skillhub 段即时生效（server 重读
  config 并重建相应组件）；llm/watch_dirs 段涉及进程级资源，页面明确标注"改动需重启
  serve"，不做静默半生效。
- 仅 admin 可见；写端点只在 serve 内置形态挂载（同 D4）。
- 新 API：`GET /admin/config`、`POST /admin/config/validate`、`POST /admin/config/reload`。

### 2.7 可视化与视觉规范（全站统一，v2）

- **设计体系**：Tailwind（D3）。中性色 slate 系（bg slate-50 / 卡片白 + ring-slate-200 /
  正文 slate-800 / 辅助 slate-400），品牌 accent teal（导航选中、主按钮、占比条、贡献边），
  语义色 emerald=晋升/好评、rose=回滚/停推、amber=灰度观察/走低、violet=admin 动作、
  sky=recommended。卡片 rounded-2xl + ring，数值 tabular-nums。
- **克制用图**：只有"趋势"和"空间结构"才用图——折线（得分趋势）、散点（画像投影）、
  DAG（进化）、关系图（血缘/聚类）；其余一律用带占比条/徽章/头像的**列表和表格**表达
  （比柱状图更直观：血缘来源、tag 构成、命中率、贡献去向都是这一类）。**单轴**，不做
  双 y 轴，不为装饰造图。
- **状态永不只靠颜色**：徽章 = 色 + 文字（"晋升"“回滚”“建议停推"）。
- **交互默认**：折线 crosshair+tooltip；散点/节点 hover 预览卡；一切图形节点可点击跳
  详情——"分析工具"的钻取原则：任何聚合数字都能追到明细。
- **数量伸缩原则（v3）**：任何可能变多的列表（某人被推荐的 skill、pin/屏蔽清单、事件
  feed）行内只放摘要（top-N chip + "+12" 计数），全量一律进**右侧抽屉**：搜索过滤 +
  分组 tab + 滚动（超过百级虚拟滚动）。禁止行内铺满换行导致挤压溢出。
- **实现**：折线用 vendor 的 uPlot（~45KB 单文件）；DAG/时间线/二部图/散点/聚类 graph
  手写 SVG（数据量都在百级以下）。样式为编译产物 CSS，运行时零依赖、无 CDN。

## 3. 需求 → 设计映射（点评压缩版）

| 用户需求 | 结论 | 落点 |
| --- | --- | --- |
| skill git tree+node + diff | ✅ 数据全在 | §2.1 进化路径，P1 |
| 得分趋势 + 血缘（原子/用户/轨迹/模型） | ✅ 链路可拉通；用户维度 P2 归因后精确 | §2.1，P1 |
| 指标不可信 → 审计 | ✅ P1 第一件事 | 逐指标「定义/SQL 口径/数据源/已知误差」→ `docs/dashboard-metrics.md`；保留/修复/删除三分类 |
| 折线/柱状图 | ✅ | §2.7 规范，P1 |
| 画像降维散点（tsne?） | ⚠️ 无重包约束 | §2.3：t-SNE（§6 Q5 修订，从 PCA 升级），P3 |
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

**D3 前端用 Tailwind CSS（PR review 拍板），运行时仍零依赖**：样式体系上 Tailwind——
开发期用其构建工具把用到的类编译成一份静态 CSS，vendor 进 `static/`（运行时无 Node、
无 CDN，不违内网约束）。不上 React/构建链之外的运行时框架；图表 vendor uPlot，
graph 类手写 SVG。示意图 `mockups/panels.html` 即按 Tailwind 制作（`mockups/tailwind.js`
为本地化的 Play 脚本，仅示意图用），其结构与类名可直接演化为实现骨架。

**D4 写操作只在 serve 内置形态**：独立只读实例（公网 demo）物理上不挂写路由（不是靠
中间件挡，是根本不 include），杜绝配置失误。

**D5 归因列手动迁移（评审修订）**：全站 canonical 身份键拍板为 **user_name**，归因列
命名 **`trajectories.user_key`**（不叫 client_id——避免与 hex client_id 键域混淆）。
bridge 入库时写入；存量按 sessions 桶目录名回填（目录名本就是 user_name），一次性脚本
跑完即弃。凡是存 client_id 的表（`recommendation_log`/`RecoStore`），聚合层统一经
ClientRegistry 译成 user_name 再 join。列上线后 `metrics.skill_by_user` 等 watch_dir
label JOIN 口径**废弃改读新列**——source 唯一，不留两条归因链路。代码不写"列可能
不存在"的兼容分支。

**D6 no-fallback 贯穿**：血缘断链在 UI 显式标"源已清理"而不是静默跳过；pin 超 slot
报错；指标算不出就不显示该卡片，不显示 0 或假数。

**D7 评价与修改意见不新增反馈 UI（PR review 已拍板）**：好评/差劲从**原子打分**获得
（他人 atom `used_skills` 命中 × ux_score）；修改意见就是既有的 **push-edit 事件**
（client 改本地 skill → `user-staging/<client_id>` 分支）。events 只做既有事实源的消费者，
不引入 👍/👎/短评这类新的用户动作。
**事件扇出规则**（评审采纳，写入 P3 spec）：按 traj 去重（同一轨迹多 atom 命中同一
skill 只发一条）；通知发给 weightscore ≥ 阈值的贡献者；本人触发本人贡献的 skill 不通知。

**D8 pinned 超量校验在写入侧（评审采纳）**：slot 上限的不变量由 `POST /my/prefs` 与
admin pin（global pin 需对全员合计校验）在**写入时拒绝**，超量状态根本不可能入库；
`build_manifest`/sync 路径永远不需要处理该错误——在 sync 时 throw 会让该用户此后每次
同步 500、skill 分发断供，违背"后台链路绝不阻塞"的既有裁决。

**D9 canary 裁决可定位（评审采纳）**：`canary_decision` 表新增 `staging_sha`/`main_sha`
列（新裁决写入）；`discard_staging` 拒绝分支前保留只读 ref `refs/rejected/<ts>-<sha>`，
使进化图能画出回滚节点并 diff。存量无 sha 的裁决在图上显式标"裁决无法定位到节点"。

**D10 Web Notifications 是 HTTPS 增强而非基线（评审采纳）**：Web Notifications 要求
secure context，内网 `http://host:port` 下 API 不存在。通知基线 = 全局铃铛 + 页面内
toast 两层；浏览器系统通知仅在 HTTPS 部署时提供，验收文档写明该前置条件。

## 5. 测试与验收

- `metrics` 新聚合方法：内存 registry 喂样本单测（空库/断链/单用户边界）。
- graph/血缘 API：真 git repo fixture（两分支 + 裁决记录）断言 DAG 形状。
- pin：manifest 注入顺序、超量报错、global vs per-user 优先级单测。
- 登录/角色：中间件单测（匿名 403 / 普通用户写 admin 端点 403）。
- **行为验收**：每期 PR 附 `docs/acceptance/dashboard-<phase>.md`（Given/When/Then，
  面向"打开页面→点什么→看到什么"）；可自动化子集用 playwright 写 E2E 挂进 `make e2e`
  Docker 场景（CS server + 2 模拟 client 喂数据 → 断言页面行为）。openspec `specs/` 的
  Scenario 与验收文档同源。

## 6. Open Questions —— 已全部拍板（2026-07-09 用户指示开发启动，未另行反对的按评审/设计倾向采纳）

- **Q1 → 已拍板**：P1 内部按评审建议排序——"指标审计 + vendor"先做（尽早兑现信任
  修复），图形页随后；是否拆成两个 PR 视体量在实现中决定。
- **Q2 → 已拍板 (a)**：connect 成功时 server 为该 user_name 发 dashboard token 并在
  client 侧打印；dashboard 登录 = user_name + token。admin 单独强口令。
- ~~Q3~~ → **D7**。
- **Q4 → 已拍板**：P3 时在 `ProfileStore` 旁加 points 落盘（更新画像时顺手存）。
- **Q5 → 已拍板（修订）**：PCA 先行上线后发现线性投影对高维语义簇分离效果差
  （用户反馈"看着没意义"），改为 t-SNE 手写实装（numpy，非可选增强）。
- **Q6 → 已拍板**：世界消息登录可见；公网只读实例不挂。
- **Q7 页面设计**：八张示意图历经五轮 review 迭代，随各轮意见定稿。
