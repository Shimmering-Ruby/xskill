# Dashboard P1 行为验收 / Behavioral Acceptance

> 面向人工验收：打开页面 → 做什么 → 应看到什么。自动化子集由
> `tests/e2e/test_dashboard_p1.py`（playwright）覆盖，编号一致。
> 前置：CS server + ≥2 个模拟 client 喂过数据（e2e fixture），或本机有真实 registry。

## A. 指标可信（metric-audit）

- [ ] A1 打开总览页，逐卡 hover ⓘ：tooltip 文案与 `docs/dashboard-metrics.md` 对应条目一致。
- [ ] A2 用空库启动 dashboard：衍生率卡片不渲染；页面无 "0%"、"NaN"、"Infinity" 字样。
- [ ] A3 构造一条含 3 个 atom 的轨迹（atom 分别用了 skill A/A/B）：A、B 的触发计数都出现，B 不丢。
- [ ] A4 同一 client 对同一 skill 版本连续 sync 多次：该 (user, skill) 曝光计数不随 sync 次数增长。
- [ ] A5 审计判死的指标在前端不可见（对照 dashboard-metrics.md 的"下线"清单）。

## B. skill 进化图（skill-evolution-graph）

- [ ] B1 打开一个有 staging 历史的 skill 详情页：能看到 main/staging 双泳道 commit 图。
- [ ] B2 历史上晋升的节点为绿色且有合入连线；被拒的节点为红色并直接标注 "x.x < y.y" 原因。
- [ ] B3 点击任意节点：出现该版本相对父节点的 diff。
- [ ] B4 新触发一次 canary 拒绝：`canary_decision` 新行含 staging_sha/main_sha；被拒 commit 经 `git log refs/rejected/...` 可达；刷新后图上出现该红色节点。
- [ ] B5 存量（无 sha）的历史裁决在图侧栏显示"裁决无法定位到节点"，不错误挂到某个 commit 上。

## C. 血缘与趋势（skill-lineage）

- [ ] C1 skill 详情页血缘分区：贡献来源按用户/按模型的计数与占比正确（与手工 SQL 对账）。
- [ ] C2 点击贡献原子列表任一行：跳到该 atom 详情页。
- [ ] C3 手动删除一个贡献原子的 JSON 文件后刷新：该行显示"源已清理"，仍保留 weightscore，不消失。
- [ ] C4 得分趋势为 main/staging 两条按日折线，版本切换点有标注；点击数据点出现当日 atom 列表。

## D. 轨迹/原子详情（traj-atom-explorer）

- [ ] D1 打开一条多 atom 轨迹详情页：原子时间线按链表序（pre/post_atom_id）排列，与文件名序不同也正确。
- [ ] D2 点击时间线节点：下方展开该 atom 全字段。
- [ ] D3 atom 详情的原文切片与原 .md 文件 offset 区间的文本一致（抽查首尾各 50 字符）。
- [ ] D4 atom 的"去向"链接跳到对应 skill 的血缘分区。
- [ ] D5 关系图中只有真实有贡献记录的 atom→skill 画高亮贡献边并标 weightscore。
- [ ] D6 删除轨迹原文后打开 atom 详情：原文区显式报"源已清理"，页面其余部分正常。

## E. 总览进度与连接状态（overview-pipeline-progress）

- [ ] E1 投递一条新轨迹（未处理）：总览"待拆分轨迹" +1；watcher 开始处理后流转到"拆分中"。
- [ ] E2 冷启动启用时进度条显示 N/M 且与 cold_start 状态一致；未启用时该区块整体不出现。
- [ ] E3 候选孵化列表中 weightscore 与该 skill `.candidates.yml` 累积值一致，进度条比例正确。
- [ ] E4 用户列表页：刚 sync 过的用户显示在线（绿点）；停掉某 client 超过 2 个同步周期后刷新显示离线，上次活跃时间正确。
- [ ] E5 用户行的轨迹·原子计数、harness/模型占比与 registry 手工对账一致；单轨迹用户的模型列标"样本不足"。
- [ ] E6 P1 阶段版本列不出现（版本上报是 P2）。

## F. 工程与部署

- [ ] F1 断网环境（无法出公网）打开 dashboard：样式完整，无 CDN 请求（DevTools Network 全部同源）。
- [ ] F2 `make test` 全绿（新增聚合/端点单测在内）。
- [ ] F3 公网只读实例形态：`/traj/*`、`/atom/*`、用户列表端点返回 404（物理未挂载），聚合端点正常。
