# 0.6.1a1 开发文档：rebuild + db 上传入库链路 + 日志治理 + skill 统计面板

- 版本：`0.6.0` → `0.6.1a1`（alpha 1，一趟 inline 实现，不拆多份 spec）
- 状态：草案，待 review 后照此实现
- 作者：xskill maintainer

本版把 6 个需求合成 4 个子项目，一次性实现并发 alpha。每个子项目都自带单元测试
（项目铁律：改代码必带测试；度量先于实现）。

---

## 0. 决策摘要（我替你定的默认，review 时可逐条否决）

| # | 岔路口 | 默认决策 | 理由 |
|---|--------|----------|------|
| D1 | 不带 `--force` 的 rebuild 语义 | **全量重跑 + 稳定覆盖**：重置 cursor 从 `.md` 整条重跑；atom_id 由 `traj+offset` 派生保持稳定，重复 atom 覆盖不追加 | 换强模型时连「拆 atom」也要重跑；幂等避免 candidates 熵增 |
| D2 | `--force` 行为 | 额外**先清空 skill 仓 + 清空已拆原子**（删各 skill 目录 + 删 `<traj_id>/tasks/atom_*.json`）再全量重跑 | "删除重建"语义；原子也要重拆，否则换强模型只重 cluster 不重拆 |
| D3 | db 上传端口 | **现有 `serve` 的 FastAPI 加一条路由** `/api/v1/ingest/upload`，复用 Bearer token | alpha 最省；要隔离攻击面再拆独立端口（留接口） |
| D4 | `read` 生态判定 | **显式 `--eco ngagent`** + 按 client/用户分子目录落盘 | 同名 `ngagent.db` 不撞；v0 只先打通 ngagent |
| D5 | 工具调用次数 / token 消耗 | **轨迹分析**（按 atom offset 区间数工具调用、估 token），不新增埋点 | 你已确认；`.md` 里本就录了 |
| D6 | 日志测试判据 | ①跑完一条真实 pipeline 后每个声明的 `.log` 非空 ②关键事件落对 component 文件 ③不串台 | 三条都断言 |
| D7 | 真实触发次数来源 | 改为**从 `trajectories` 表统计**（`skill_used` 计数 / 按 `skill_sha` 分组），不再读 frontmatter `use_count` | frontmatter 是陈旧手填值 |
| D8 | 前端图表 | **确定性产出 SVG**（无外部图表库），骨架由代码生成 | 遵守"结构类骨架由确定性工具产出"；零新依赖 |
| D9 | 文档/spec 不拆 | 全部 inline 一趟做 | 你的要求 |
| D10 | header 注入范围 | **扩展到所有已装 skill**（不只灰度中的），被调用时都打 `sha` header | 否则历史 main 版本触发统计不到、版本曲线大半为空 |
| D11 | 按用户切片 | **支持**：`trajectories` 加冗余 `client_id` 列 | 面板可按用户维度统计 |
| D12 | `--force` 清原子 | 删各 `<traj_id>/tasks/atom_*.json` 强制重拆 | 换强模型要连拆分一起重做 |

---

## 1. 子项目 A — `xskill rebuild`：本地重蒸馏

### 目标
拿现有原始轨迹（`~/.xskill/<eco>_sessions/traj_*.md`）重跑整条 pipeline，换强模型重新生成 skill。

### CLI
```
xskill rebuild [--force] [--eco ECO] [--traj TRAJ_ID]
```
- 默认：全量重跑所有已注册轨迹（D1）。
- `--force`：先清空 skill 仓再重跑（D2）。
- `--eco` / `--traj`：可选过滤，缩小到某生态或单条轨迹（便于调试）。

### 机制
1. （仅 `--force`）**双清空**：① 删 `<skill_dir>` 下所有 skill 目录；② 删各轨迹的已拆原子 `~/.xskill/<eco>_sessions/<traj_id>/tasks/atom_*.json`。原子也清，强制下一轮 TaskAgent 用新模型**重拆**（否则只重 cluster、拆分仍是旧模型产物）。
2. 把目标 `trajectories` 行状态**重置回 `discovered`、offset 归零**（复用 `registry.update_traj_status` / `update_traj_offset`），让下一轮 watcher 从头重拆。
3. rebuild 命令本身**不内联跑 pipeline**：它只做"重置 + 清仓"，把实际重跑交给正在运行的 `serve` watcher（30s 内自动捡起）。若 daemon 未运行，提示用户先 `xskill serve`。
   - 备选（评估后决定）：rebuild 直接同步驱动一轮 `PipelineRunner._scan_once()` 跑到收敛，给离线一次性场景用。alpha 先做"重置 + 提示"，把同步驱动列为 D 待评估项。

### 幂等关键点
- `TaskAgent` 产出的 atom_id 必须由 `traj_id + offset_start` 派生（确认现状：agent.md 约定 `atom_<traj_id>_NNNN`，NNNN 为序号——需核实重拆是否稳定复现序号；若不稳定，改为 offset 派生）。
- TCA 写 `.candidates.yml` 时按 atom_id 去重覆盖（核实 `skill/candidates.py` 是否已 upsert；若为 append，本版补 upsert）。

### 改动文件
- `src/xskill/cli.py`：加 `rebuild` 子命令 + `cmd_rebuild`。
- `src/xskill/pipeline/registry.py`：加 `reset_trajectories(eco=None, traj_id=None)` 批量重置。
- `src/xskill/skill/repo.py`：加 `wipe_all_skills()`（`--force` 用）。
- `src/xskill/skill/candidates.py`：（如需）candidates upsert 去重。

### 测试
- `reset_trajectories` 把指定行翻回 `discovered`、offset=0，不动其它行。
- `wipe_all_skills` 删干净 skill 目录。
- candidates 同 atom_id 重复加 → 只留一份（覆盖）。
- CLI：`rebuild` 无 daemon 时给出明确提示；`--force` 触发清仓。

---

## 2. 子项目 B — db 上传入库链路

链路：`Windows 脚本 ──HTTP POST──▶ /api/v1/ingest/upload ──落盘 spool──▶ xskill read <spool> ──▶ SqliteIngester`

### B-1. `xskill read <LOCAL_PATH>`
```
xskill read <LOCAL_PATH> --eco ngagent [--recursive]
```
- 扫描 `<LOCAL_PATH>` 下的 `*.db`，对每个文件用**参数化路径**的 `SqliteIngester` 入库到 `~/.xskill/<eco>_sessions/`。
- 现状 `SqliteIngester` 走 `spec.path_resolver` 固定路径 → 本版让 ingester 支持显式 `db_path` 覆盖（不删旧逻辑，加可选入参）。
- traj_id：沿用 `spec.traj_id_prefix`（`traj_ng_`）+ 内部 session id；子目录隔离保证多用户 `ngagent.db` 不撞。

改动：
- `src/xskill/ecosystems/opencode.py`（`SqliteIngester`）：构造支持显式 `db_path`。
- `src/xskill/cli.py`：加 `read` 子命令 + `cmd_read`（按 `--eco` 选 spec，遍历目录调 ingester）。

### B-2. HTTP 上传端口（D3：现有 server 加路由）
`POST /api/v1/ingest/upload`
- 鉴权：复用 team server Bearer token（`client_registry`）。
- body：`multipart/form-data`，字段 `file`（db 文件）+ `eco`（默认 ngagent）+ 可选 `client_id`。
- 行为：把上传文件原子落盘到 spool 目录 `~/.xskill/uploads/<eco>/<client_id>/<原名或时间戳>.db`，返回落盘路径 + 大小。
- 落盘后**自动触发一次** `read` 入库（best-effort，吞错不阻塞响应；遵守"后台刷新绝不阻塞"），或返回提示让管理员手动 `xskill read`。alpha 默认自动触发。

改动：
- `src/xskill/api/app.py`：加 `api_ingest_upload` 路由（`UploadFile`）。
- `src/xskill/config.py`：加 `uploads_dir()`。

### B-3. Windows 上传脚本
`scripts/upload_ngagent_db.ps1`（PowerShell，零依赖，不碰 sshpass/密码）：
- 自动定位 `$env:LOCALAPPDATA` 或 `~/.local/share/opencode/db/ngagent.db`。
- `Invoke-RestMethod -Uri http://7.220.144.233:<port>/api/v1/ingest/upload -Method Post -InFile <db> -Headers @{Authorization="Bearer <token>"} -ContentType multipart/form-data`。
- 参数化 server 地址 / token / db 路径，带用法注释。
- 注：服务器地址 `7.220.144.233`、ssh 端口 `9960` 来自原始需求；HTTP 端口取 `serve` 实际监听端口（文档里标 `<port>`，发布时填实）。**scp+密码方案放弃**（Windows 无 sshpass，HTTP 方案取代）。

### 测试
- `SqliteIngester(db_path=...)` 能读任意路径 db 并产出轨迹。
- `read` 遍历目录批量入库；空目录/无 db 时报错（不 fallback）。
- 上传路由：带 token 上传成功落盘 + 触发 read；无 token 401。
- PowerShell 脚本：语法 lint（不进 CI，手测说明写文档）。

---

## 3. 子项目 C — 日志治理 + 日志测试

### 现状问题
~30 个 logger 名只有 ~13 个映射到独立文件；核心 agent（`xskill.task_agent` / `xskill.task_cluster_agent` / `xskill.skill_edit_agent`）全挤 `xskill.log`；部分声明的 `.log`（git_lock/httpx 等）几乎不写 → 空文件 = unfunctional。

### 改动
1. 重整 `src/xskill/utils/logging.py` 的 `_PER_LOGGER_FILES`：
   - **补**核心 agent 专属文件：`xskill.task_agent.log` / `xskill.task_cluster_agent.log` / `xskill.skill_edit_agent.log`。
   - **砍/并**几乎不写的：把 `git_lock` 并入 watcher 或 catch-all；`httpx`/`httpcore`/`openai` 维持但默认 WARNING（已是）。
   - 保证每个声明的文件都有真实写入点。
2. 统一 logger 命名：核对源码里 `getLogger("...")` 名与映射表一致（如 `xskill.process` vs `process`）。

### 测试（D6 三条判据）
- `tests/test_logging_granularity.py`：
  - **无空文件**：用临时 logs 目录 + 模拟各 component 各打一条 → 每个声明文件非空。
  - **落对文件**：watcher 事件只进 `xskill.watcher.log`，不出现在 `xskill.server.log`（不串台）。
  - **关键事件覆盖**：拆分/cluster/edit/canary 决策/install 五类事件各能在对应文件找到至少一条（用 pipeline 假跑或直接打日志断言路由）。

---

## 4. 子项目 D — skill 使用统计 + 详情面板

分两层：D1 数据层（后端），D2 视图层（前端）。

### D1 数据层

> **版本归属机制（核实结论）**：用户用的是哪个 skill 版本，靠 daemon 的 install history 按 `start_t` 时间戳对齐——daemon 每次装/翻牌记 `(skill, side, sha=该 side 分支 HEAD commit)`，session 进来按开始时间 lookup 出当时装的 sha，真用到才把 `<!-- xskill:skill=X side=Y sha=Z -->` 注到 traj.md 顶部（`claude_code.py:657-712`）。用户归属 = 轨迹所属 watch_dir 的 `label`（= team 模式 client_id；`api.py:105-110`），`trajectories` 表无 client_id 列，需 join `watch_dirs.label`。

**两个现状限制 → D1 必须补：**
- **限制① sha 没落库**：sha 只活在 traj.md 头 + 评分链路，`registry.py:558` 写轨迹只存 `skill_used`+`canary_side`。→ 新增 `trajectories.skill_sha` 列（migration），从 header 解析落库（解析点在 harvest 写 `skill_used`+`canary_side` 处一并写）。
- **限制② 头只在灰度期 + 真用到时才打**：纯 main（非灰度）日常使用不注入 header（`claude_code.py:638-644`），导致历史 main 版本触发统计不到、版本曲线大半为空。→ **扩展注入逻辑：对所有已装 skill（不只灰度中的）在被调用时都打 header**，让每个 main 版本也有 sha 标记。
- **按用户切片（已确认包含）**：给 `trajectories` 加冗余 `client_id` 列（migration，从 watch_dir.label 回填 + harvest 时写入），面板支持按用户维度统计触发/UX/token/tools。

新增 `dashboard/metrics.py` 方法：
- `skill_detail(name)`：聚合单 skill——真实总触发（`COUNT(trajectories WHERE skill_used=name)`，替代 frontmatter use_count，D7）、版本列表（git log）、各版本触发/UX。
- `skill_version_stats(name)`：按 `skill_sha` 分组 → 每版本：触发次数、AVG(ux_score)、token 消耗、平均工具调用次数。
  - token/工具调用：对该版本命中的每条 traj，取其 atom 的 `offset_start..offset_end` 区间，从 `.md` 解析**工具调用条数**与**估算 token**（轨迹分析，D5）。
- `skill_timeseries(name, sha=None)`：时间序列。
  - `sha` 给定 → **版本内瞬时**序列（该版本各 traj 按时间排）。
  - `sha=None` → **跨版本进化**序列（按版本顺序，每版本一个聚合点：UX/token/tools 均值）。

新增只读文件 API（详情页文件树/预览用）：
- `GET /api/v1/skills/{name}/tree`：列 skill 目录文件树（相对路径 + 类型）。
- `GET /api/v1/skills/{name}/file?path=...`：读单文件内容（防越权：限定在 skill 目录内）。
- 版本/diff：复用现有 `/skills/{name}/log` + `/skills/{name}/diff`。

新增统计 API：
- `GET /api/v1/skills/{name}/stats`：返回 `skill_version_stats` + `skill_timeseries`。

### D2 视图层（`dashboard/static/`，原生 JS）
1. **技能列表 → 详情可点进**：列表每个 skill 加链接进详情视图（前端路由或 `?skill=name` 查询参数切换面板）。
2. **详情页布局**：
   - 顶部：skill 名 / state / 真实总触发次数。
   - 左侧：**文件目录树 + 内容预览**（点文件 → 右侧 `<pre>` 预览，调 `/tree` + `/file`）。
   - 中部：**版本列表**，版本间 **红绿 diff**（调 `/diff`，前端把 `+`/`-` 行渲染成绿/红）。
   - 下部：**两组曲线**（D8 确定性 SVG）：
     - 每个版本一组「版本内瞬时」曲线（UX / token / 工具调用）。
     - 整个 skill 一组「跨版本进化趋势」曲线。
3. SVG 折线由小工具函数 `sparkline(points)` 确定性生成（给定数据必出同图），AI/手不画结构。

### 改动文件
- `src/xskill/pipeline/registry.py`：`skill_sha` migration + 写入。
- `src/xskill/dashboard/metrics.py`：`skill_detail` / `skill_version_stats` / `skill_timeseries`。
- `src/xskill/dashboard/router.py`：`/tree` `/file` `/stats` 路由。
- `src/xskill/dashboard/static/index.html` + `app.js`：详情页 + 文件树 + diff + SVG 图。
- `src/xskill/utils/traj_analysis.py`（新）：从 `.md` 区间数工具调用 / 估 token。

### 测试
- `skill_version_stats` 在造好的 trajectories + atom + .md fixture 上算出正确的每版本触发/UX/token/tools。
- traj_analysis：给定一段含 N 个工具调用的 `.md` 区间 → 数出 N。
- `/tree` `/file` 越权防御：`path=../../etc/passwd` → 拒绝。
- `skill_timeseries` 版本内 vs 跨版本两种模式返回结构正确。

---

## 5. 发布 0.6.1a1

1. 全部子项目实现 + `make test` 全绿。
2. 发版前 `make e2e`（Docker E2E）通过。
3. 版本号：`0.6.0` → `0.6.1a1`（vcs-versioning 由 tag 驱动；打 `v0.6.1a1` tag）。
4. 发布顺序（铁律）：**先 push GitHub → 再 twine 传 PyPI**。
5. commit message 中英双语，省略 Co-Authored-By。

---

## 6. 实现顺序（inline 一趟）

C（日志，独立最快）→ B（read/上传/脚本，链路）→ A（rebuild，复用 B 的 ingest 重入）→ D1（数据层）→ D2（前端）→ 测试全绿 → e2e → 发版。

---

## 7. 验收度量（度量先于实现）

| 子项目 | 单调收敛指标 |
|--------|-------------|
| A | rebuild 单测 + 重置/清仓/幂等用例未通过数 → 0 |
| B | read/上传/ingester 参数化用例未通过数 → 0 |
| C | 日志三判据用例未通过数 → 0 |
| D | metrics/traj_analysis/越权/timeseries 用例未通过数 → 0 |
| 整体 | `make test` + `make e2e` 全绿 |
