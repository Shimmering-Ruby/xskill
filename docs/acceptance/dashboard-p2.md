# 行为验收 — Dashboard P2（身份 + 控制面 + Pin）

> Given/When/Then 面向"打开页面→点什么→看到什么"。自动化子集在
> `tests/test_dashboard_console_p2.py`（15 例）；标注 [E2E] 的留 Docker 场景。

## A. 身份与登录（2.2, Q2a/D2）

- [x] A1 `xskill connect <srv> --token .. --name alice` 成功后，终端打印
      `dashboard 登录: 用户名 alice + token <hex>`；重连拿到同一 token（幂等）。
- [x] A2 打开看板 → 左下角"登录" → 输 alice + token → 侧栏出现"我的"，
      左下角显示 `alice user`。
- [x] A3 admin 名单（`dashboard.admins`）内用户 + `dashboard.admin_password`
      登录 → 出现"管理/设置"导航；口令错 → 401 提示。admin_password 留空 =
      admin 登录关闭。
- [x] A4 未登录请求 /my/* → 401；普通用户请求 /admin/* → 403（中间件单测）。
- [x] A5 退出登录 → 会话 cookie 失效，admin 端点回 401。

## B. 归因地基（2.1, D5）

- [x] B1 team 桶新轨迹入库 → `trajectories.user_key` = sessions 桶目录名
      （user_name）；非 team 目录留空，聚合层显示 `(local)`。
- [x] B2 `scripts/backfill_user_key.py` 一次性回填存量，幂等（重复跑 0 行）。
- [x] B3 `metrics.skill_by_user` 等口径改读 user_key，不再 JOIN watch_dirs.label。

## C. 我的控制面（2.3/2.4/2.5/2.6）

- [x] C1 "推给我的"列表每行带注入类型 chip：pinned·自己 / pinned·admin /
      pinned·全局 / ranked / recommended。
- [x] C2 点 pin → 该 skill 进 pinned 占位；点 ✕ → 进"已屏蔽"折叠组，可恢复。
- [x] C3 admin 代 pin / 全局 pin 的条目显示"锁定"置灰，用户取消/屏蔽 → 403。
- [x] C4 pinned 超 slot 上限 → 写入时 409（2.4d/D8），报错文案含合计与上限；
      全局 pin 对全员合计校验。sync 路径永不因此报错。
- [x] C5 贡献去向：四级步进 trajs→atoms→被采纳→进入 skill；下方"我贡献的
      skill × 使用者 × 均分"列表。
- [x] C6 推荐×触发表结论列：高价值 / 正常 / 常推不用→建议停推 / 零触发→建议
      停推（阈值注释在 console.py，与展示同源）。

## D. Manifest 注入（2.4）

- [x] D1 注入顺序：blocked 先排除 → pinned 占位（全局在前）→ ranked →
      recommended 回填；bucket 字段新增 `pinned`。
- [x] D2 retired skill 无条件不分发，即便被 pin。
- [x] D3 pin 的 skill 已被删/不可分发 → 跳过不报错（读路径 D8）。
- [ ] D4 [E2E] 真 client sync 后本地 slot 含 pinned 项。

## E. 管理页（2.5/2.4c）

- [x] E1 用户矩阵：版本列（未上报显式标注）/被推荐数/触发率/pinned·blocked
      计数/停推建议 chips；"配置…"开抽屉代 pin/代屏蔽。
- [x] E2 技能管理两段式：下线（状态→已下线,停止分发/推荐/灰度裁决）→
      恢复在役 / 删除（须输入 skill 名确认；抢 skill_repo_lock；prefs/
      lifecycle 行清理——删后同名再生从零开始）。
- [x] E3 retire 后 canary check_and_decide 返回 action='retired' 短路。

## F. 设置页（2.9）

- [x] F1 原文编辑 + 仅校验 / 校验并热加载；校验失败不落盘、不生效、直接
      展示错误（无部分生效）。
- [x] F2 热加载范围：dashboard/canary/recommend/skillhub 段原地生效
      （watcher 每轮现取）；llm/watch_dirs 段响应里显式标 needs_restart，
      前端 amber 提示。
- [x] F3 落盘为原子写（tmp+rename）。

## G. 版本上报（2.10）

- [x] G1 register/sync 携带版本 → clients.client_version；旧 client 未上报
      显示"未上报"，不伪造。
- [x] G2 连接状态看板版本列：低于 server 版本标"落后"（amber）；dev 版本
      解析失败按不落后处理。

## H. 部署形态（2.7/D4）

- [x] H1 登录/控制面写路由仅 serve 内置形态挂载；独立只读实例
      （serve_builtin=False）物理 404。
