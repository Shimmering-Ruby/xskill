# 行为验收 — Dashboard P3（社交 + 画像可视化）

> Given/When/Then 面向"打开页面→点什么→看到什么"。自动化子集在
> `tests/test_dashboard_p3.py`（21 例）与 `tests/test_recommend_engine.py`
> 的 points 落盘用例；标注 [人工] 的需真实浏览器/多用户环境确认。

## A. events 埋点（3.1，D7）

- [x] A1 bob 的轨迹打分落盘且 atom 命中 skill-x → 产生一条 feedback 事件
      （actor=bob，payload 含均分/原子数/好评徽章/side/sha）；同轨迹再有
      atom 命中同 skill 不重发（(skill,traj) 去重）。
- [x] A2 通知只发给 skill-x 累计 weightscore ≥ 3 的贡献者；ws=1 的琐碎
      贡献者不收；alice 触发自己贡献的 skill 不通知自己（世界消息仍可见）。
- [x] A3 client push-edit 成功 → push_edit 事件（分支引用 + ref_sha），
      贡献者收通知。
- [x] A4 canary 裁决落盘 → canary 事件（晋升/回滚/超时丢弃 + 两侧均分）。
- [x] A5 用户自 pin / admin 代 pin → pin 事件；admin 代 pin 时被配置用户
      也收通知；block 不产生事件。
- [x] A6 事件发送失败不阻断打分/推送/裁决主链路（旁路 try/except，
      与 record_canary_decision 同款约定）。

## B. 通知与已读（3.2 读侧）

- [x] B1 匿名请求 /events → 401；scope=me 返回我的通知 + 未读数；
      scope=world 返回全量 feed；before_id 分页。
- [x] B2 已读是每用户游标：标记 last_id 后未读归零；游标只前进，
      倒退标记是 no-op；通知列表带 read 布尔。

## C. 通知前端三层（3.2，D10）

- [x] C1 登录后所有页面顶栏出现铃铛 + 未读数徽标（30s 轮询）；打开下拉
      列出通知并把当前最大 id 标已读，徽标清零。
- [x] C2 新事件到达 → 右下角 toast（首轮对齐水位不弹历史，最多弹 3 条）。
- [ ] C3 [人工] HTTPS 部署下铃铛下拉出现"开启系统通知"，授权后页面最小化
      时新事件弹系统级通知，点击唤起对应 skill 页；内网 http 下显示
      "系统通知需 HTTPS 部署"（能力分层，不是错误）。
- [x] C4 世界消息在"我的"页：卡片 feed，头像 + skill chip（可点跳分析页）
      + 语义徽章（好评 emerald/差劲 rose/晋升 emerald/回滚 rose/pin violet）
      + 按今天/昨天分组 + "加载更早"分页；只含 skill 名与动作。
- [x] C5 push-edit 事件"看 diff"→ 跳 skill 页并把该分支引用的 diff 灌进
      预览区。

## D. 画像散点（3.4，Q4/Q5）

- [x] D1 引擎更新画像时顺手落盘原子点向量 + 对齐元数据（atom_id/summary/
      ux/tags）；行不对齐在写入侧抛错；冷启动落空值。
- [x] D2 用户 & 画像页点用户行 → 加载散点卡片：PCA 2D 投影，原子按兴趣簇
      着色（≤5 簇），簇标签=簇内 top tag，◆=兴趣中心，▲=skill 向量，
      标题显示保留方差。
- [x] D3 skill ▲ 只用 .skill_index.pkl 缓存 embedding；索引缺失/维度不匹配
      → 不显示并标注（D6 不现算不造假）。
- [x] D4 无画像 → 404 提示；有画像无点 → 显式"冷启动"说明。
- [x] D5 hover 原子点 → 预览卡（atom_id + summary + ux）；点击原子点跳
      atom 详情；点击 ▲ 跳 skill 分析页。

## E. admin 聚类 graph（3.5）

- [x] E1 仅 admin 可见（普通用户 403）；画像库不存在 → 404 提示。
- [x] E2 节点=用户（大小 ∝ 原子数），边=mean_tensor 余弦相似度>0.6
      （粗细 ∝ 相似度）；悬停边显示相似度 + 共同标签/共同 skill。
- [x] E3 无边节点灰色标"冷启动"；维度不一致的画像不连边不崩。
- [x] E4 手写 force 布局，初始位置确定性（可复现）；点节点跳该用户散点。

## F. 回归

- [x] F1 P1/P2 既有端点与页面不受影响（test_dashboard_* 全套通过）。
- [x] F2 静态资产仍零外联（新 Tailwind 类已重新编译内联,BUILD.md 流程）。
