# `/sync`、冷启动 SkillEdit 与慢后端韧性设计

状态：实施完成，发布验收中

日期：2026-07-13  
更新：2026-07-14

关联：[Issue #101](https://github.com/SkillNerds/xskill/issues/101)、[PR #76](https://github.com/SkillNerds/xskill/pull/76)、[PR #83](https://github.com/SkillNerds/xskill/pull/83)

实测基线：[2026-07-14 `/sync` 控制面 300×300 压测基线](../reports/2026-07-14-sync-control-plane-300-baseline.md)

修复后结果：[2026-07-14 `/sync` 控制面 300×300 修复后压测报告](../reports/2026-07-14-sync-control-plane-300-postfix.md)

## 1. 摘要

生产环境中，client 数量增加、冷启动集中处理、embedding 变慢或 LLM 首 token 延迟较长时，`/sync` 和 dashboard 会一起无响应。2026-07-14 的真实 300 skill、300 client 压测已经复现该问题，并额外发现了 client 注册表的 SQLite 写锁冲突和 `/status` 对多独立 skill 仓库布局不兼容。

本轮实施采用以下方案：

1. `/sync` 固定只读已有画像并生成 manifest，不读取 atom、不计算 revision、不调用或等待 embedding。
2. 每次 `/sync` 在返回前只向独立的 `queue.Queue` 提交一次 client 刷新请求；固定数量的 daemon worker 在后台读取 atom、判断版本并刷新画像。
3. 同一 client 在 queued/running 状态下的重复请求合并；一次运行期间发生变化时最多补跑一次。
4. 画像新鲜度由持久化的 `source_revision + embed_model` 判断，不再依赖进程内“目录名和文件数”指纹。
5. client 注册表把有效 client 快照保存在内存，认证触达合并后批量写回；数据库启用 WAL、`busy_timeout` 和短写事务。
6. SkillEdit 保留现有 `DirectoryWatcher` 线程池、`max_concurrent` 和同 skill 在途去重；其 300 任务、30 并发路径在基线中已经正确工作，无需重写。
7. 30×30 场景进入普通测试；带 12 秒 LLM 延迟和 23 秒 embedding 延迟的 300×300 场景标记为 `stress`，在 main、相关改动和发版前执行。正式发版前运行结果为 `1 passed in 490.15s`。

## 2. 实测基线与问题边界

基线使用 commit `c90ca129fc1157c267fdd3a6613b3b7796d208df`，运行真实 uvicorn/FastAPI 路由、真实 watcher、真实 Agno/tool-call 执行路径，并挂接本地 OpenAI-compatible LLM/embedding mock。LLM 首次响应返回 `write_file` 和 `commit_baby_to_main` tool call，tool result 后再返回 mock 文本。

| 项目 | 2026-07-14 实测结果 | 判定 |
|---|---:|---|
| 规模 | 300 skills、300 clients | 达到目标规模 |
| SkillEdit LLM | 600/600 完成，300 initial + 300 follow-up | 正常 |
| SkillEdit 最大并发 | 30 | 现有限流正常 |
| SkillEdit 收敛 | 300 main、300 candidates 清空、300 promoted | 正常 |
| embedding | 599 次，599 个唯一输入，重复输入 0 | 重算浪费 0，计划覆盖缺口 1 |
| embedding 最大在途 | 103 | 未受独立 worker 限制 |
| cold `/sync` | 300×200，但日志长尾约 68～71 秒 | SLA 失败 |
| cache-hit `/sync` | 251×200、49×500 | SQLite 锁冲突 |
| one-new-atom `/sync` | 300×200、299 次 embedding | 有 1 个刷新漏算 |
| dashboard | 两个 embedding 饱和阶段均在 2 秒探针内超时 | 控制面受阻 |
| `/status` | 3/3 返回 500 | skill 根目录不是 Git 仓库时未降级 |
| server 线程 | 现场峰值 337 | 300 个请求线程被慢 embedding 长期占用 |

`result.json` 顶层 `success=false` 的直接原因是脚本最后等待 watcher futures 清空超时；但 300 个 skill 已全部晋升。该脚本清理判断需要修正，不能把它与业务数据未收敛混为一项。完整证据和限制见上述基线报告；原始产物位于：

```text
/home/admin/xskill-loadtest-results/full/run-20260714-001342-s300-c300/
```

## 3. 目标与范围

### 3.1 必须实现

1. `/sync` 不读取 atom，不计算 source revision，不调用或等待 embedding。
2. 有旧画像时立即用旧画像生成 manifest；无画像时立即使用现有质量排序降级结果。
3. 画像刷新由独立、固定并发、容量有上限的后台服务执行。
4. 同 client 请求合并；刷新期间再变化最多补跑一次；失败后旧画像继续可用且下次 `/sync` 可重试。
5. 内容变化检测覆盖新增、删除、同 atom id 的 summary 变化，以及 `used_skills`、`ux_score`、`tags` 变化。
6. 300 个 SkillEdit 和 300 个画像刷新同时面对慢后端时，`/sync`、dashboard、health、status 等控制面接口仍满足验收标准。
7. 压力解除后，skill、画像、候选、冷启动信号、队列和在途状态最终收敛。
8. 压测结果统计 LLM、embedding 的调用次数、并发、唯一输入、重复输入、延迟和未完成数量。

### 3.2 本轮不处理

- UMAP/t-SNE 可视化算法；
- LLM 模型质量和 SkillEdit 内容质量；
- SkillEdit 候选阈值和 prompt；
- 把 300 个 SkillEdit 任务映射成 300 个真实工作线程；
- 给 `SyncResponse` 增加 `profile_pending` 字段；
- 把所有同步路由改为 async；正常 `build_manifest` 仍在线程池执行。显式 `total_slots=0` 时 `/sync` 走不读取 Git/SQLite 偏好的 async 快速路径。

## 4. 已决定事项

| 决策项 | 结论 |
|---|---|
| 画像 worker 默认并发 | 4，可配置；300 压测使用 30 |
| worker 实现 | 独立 `queue.Queue` + 固定 daemon threads，不使用 anyio 线程池和 watcher 线程池 |
| 队列容量 | 有上限，可配置；默认 1024 |
| client 更新合并 | queued 时合并到尚未取快照的任务；running 时设置一次补跑标记 |
| 补跑上限 | 每次出队任务最多立即补跑一次；补跑期间的新变化由后续 `/sync` 再提交 |
| 新鲜度存储 | `client_interest.source_revision` 与现有 `embed_model` 一起持久化 |
| revision 算法 | atom 快照稳定 JSON 后做 SHA-256 |
| 无缓存响应 | 保持 HTTP 200 和现有 manifest 降级逻辑 |
| 协议字段 | `SyncResponse` 不变，不新增 `profile_pending` |
| 队列满 | `/sync` 仍返回 200；记录 `queue_full` 和日志；client 保持 idle，后续 `/sync` 可重试 |
| 300 压测执行 | `stress` 标记；main、相关后端改动和发版前运行 |
| SkillEdit | 保留现有最大并发 30 和同 skill 在途去重，不重写 |

## 5. 目标数据流

```mermaid
flowchart LR
    C[多个 client] --> S[/sync]
    S --> A[authenticate_and_touch]
    A --> M[读取旧画像并构建 manifest]
    M --> Q[request_refresh]
    Q --> R[立即返回 SyncResponse]

    Q --> PQ[有界 queue.Queue]
    PQ --> W[固定画像 worker]
    W --> AS[读取一次 atom 快照]
    AS --> RV[计算 source_revision]
    RV --> PS[(profile_store)]
    RV --> E[仅缺失或 summary 变化的 embedding]
    E --> PS

    CS[COLD_START] --> DW[DirectoryWatcher]
    DW --> SE[SkillEdit futures]
    SE --> WP[最多 max_concurrent 个 watcher worker]
    WP --> L[LLM]
```

画像 worker、anyio 请求线程和 watcher SkillEdit 线程是三个相互独立的执行资源。验收和指标必须分别记录，不能只看进程总线程数判断是哪条链路饱和。

## 6. `/sync` 固定缓存路径

`src/xskill/team/server/api.py::team_sync` 的顺序固定为：

1. 校验 join token，并用一次 `authenticate_and_touch(client_id, version)` 验证 client 和更新 `last_seen/client_version`；
2. 读取现有 profile、偏好和 skill 仓库状态，调用 `build_manifest`；
3. 对画像刷新服务调用 `request(client_id)`；
4. 原样返回 `resp.model_dump()`。

第 2 步在第 3 步前完成，保证本次响应只使用调用开始时已经成功落库的画像。`request()` 只操作内存状态和 `put_nowait`，不得读取 client atom 目录。刷新服务未初始化、正在关闭或队列满时只记录指标和日志，不让 `/sync` 失败。

原有 `_ctx.profile_refresh_lock` 和 `_ctx.profile_refresh_inflight` 删除，由刷新服务统一持有状态。`SyncResponse`、HTTP 状态码、manifest 字段和客户端行为保持兼容。

## 7. 后台画像刷新服务

新增 `src/xskill/team/server/profile_refresh.py`，核心对象为 `ProfileRefreshService`：

```python
request(client_id: str) -> bool
metrics() -> dict
wait_idle(timeout: float) -> bool
stop(timeout: float) -> None
```

构造参数至少包含 engine、`workers`、`queue_size` 和日志对象。默认配置：

```yaml
server:
  profile_refresh_workers: 4
  profile_refresh_queue_size: 1024
  profile_refresh_shutdown_timeout: 5
```

### 7.1 client 状态和合并规则

每个 client 只有 `idle`、`queued`、`running` 三种状态，另有 running 期间的一位 `rerun_requested` 标记：

```text
idle --request/put 成功--> queued --worker 取出--> running --完成--> idle
                                            |           |
queued 再 request：合并，不增加队列项          |           +-- request：rerun_requested=true
                                            |                         |
                                            +---- 初次成功后最多补跑一次 --+
```

规则如下：

- idle：`put_nowait(client_id)` 成功后才写为 queued；队列满则保持 idle。
- queued：重复请求只增加 `coalesced`，不新增队列项；worker 尚未读取 atom，所以该任务自然读取最新快照。
- running 首次执行：重复请求只把 `rerun_requested` 置为 true，多次请求仍只记一位；首次刷新成功后由同一 worker 立即读取新快照并补跑一次，不重新占队列。
- 补跑期间：重复请求继续计入 `coalesced`，但不产生第三次连续执行；补跑结束后回到 idle，下一次 `/sync` 可以再次提交。该上限防止一个高频 client 长期占住一个 worker。
- 任意刷新失败：记录 `failed`，不写 source revision，不立即补跑，状态回到 idle；旧画像保留，下一次 `/sync` 重试。
- 不同 client 可以由不同 worker 并发；同一 client 永远只有一个运行实例。

### 7.2 生命周期

- team server 创建推荐引擎后创建并启动刷新服务，再把服务注入 team context。
- worker 使用有名称的 daemon thread，例如 `xskill-profile-refresh-0`，便于测试和线程快照定位。
- `stop(timeout)` 先停止接收新请求，再取消尚未开始的队列项，通知 worker 退出，并在总超时内 join。
- 已进入同步 embedding HTTP 调用的线程无法安全强制中止；超时后记录仍存活 worker，由于线程为 daemon，不阻止进程退出。
- `wait_idle(timeout)` 使用 `Condition` 等状态通知，不用固定 sleep；只供单元测试、压力测试和显式诊断，不得被 `/sync` 调用。
- 测试和重复创建 app 时必须停止旧服务并清理 team context，避免全局状态串到下一用例。

## 8. source revision 与增量 embedding

### 8.1 一次快照

worker 每轮对一个 client 只调用一次 `_user_atoms(user_id)`，后续 revision、used skills、point metadata 和 embedding 都基于这一份内存快照，避免同一轮多次扫描看到不一致数据。

快照按 `atom_id` 排序，每个记录只包含：

```json
{
  "atom_id": "...",
  "summary": "...",
  "used_skills": [],
  "ux_score": null,
  "tags": []
}
```

其中 `used_skills` 和 `tags` 在序列化前排序，缺失列表按空列表处理。使用：

```python
json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
sha256(payload.encode("utf-8")).hexdigest()
```

完整 atom 快照都参与 revision，即使某个 atom 的 summary 为空，因为 `used_skills`、`ux_score` 或 `tags` 仍可能影响画像元数据。该算法可检测：

- 新增或删除 atom；
- atom 数量不变但内容替换；
- 同 atom id 的 summary 修改；
- 仅 `used_skills`、`ux_score`、`tags` 修改。

### 8.2 持久化新鲜度

`client_interest` 表新增：

```sql
source_revision TEXT DEFAULT ''
```

`embed_model` 已存在，继续使用。`ProfileStore._migrate` 幂等加列；`load()` 或专用的 `load_refresh_state()` 返回两者。

worker/engine 比较规则：

```text
stored.source_revision == current_revision
AND stored.embed_model == current_embed_model
```

同时成立才计为 unchanged。画像、points、point_meta、used_skills、`source_revision` 和 `embed_model` 在同一次 upsert 事务中写入；只有 embedding、聚合和 upsert 全部成功后才更新 revision。进程重启后仍能正确判断，无需依赖 `_profile_fp_cache`。

### 8.3 向量复用边界

现有仅按 `atom_id` 复用向量的假设取消。`load_vector_cache` 返回或等价表达：

```text
atom_id -> (stored_summary, vector)
```

只有以下条件同时满足才复用：

1. profile 的 `embed_model` 与当前模型一致；
2. `atom_id` 存在；
3. 当前 summary 与落库 `point_meta.summary` 完全相同。

因此：

- 新增 atom：只计算新增且 summary 非空的 atom；
- 删除 atom：不调用 embedding，重新聚合并在 upsert 时移除旧 point；
- summary 修改：只重算对应 atom；
- 仅 `used_skills`、`ux_score`、`tags` 修改：不重算向量，只重新聚合和落库；
- embed model 修改：所有有 summary 的 atom 重算。

`SkillRecommendEngine.update_user_interest` 保持同步方法和现有调用兼容，但内部改为持久化 revision 判断，并返回本轮结果（changed/unchanged、embedded item 数等）供刷新服务累计指标；`/sync` 不再直接调用它。

## 9. SQLite 并发修复

### 9.1 ClientRegistry

`ClientRegistry` 做以下改动：

1. 初始化时启用 `PRAGMA journal_mode=WAL` 和 `PRAGMA synchronous=NORMAL`；每个连接设置 `busy_timeout`，同时保留 sqlite connect timeout。
2. 启动时加载有效 client id 的内存集合；注册、删除和关闭与该集合共用状态锁，使删除返回后立即撤销认证。
3. `authenticate_and_touch(client_id, version=None) -> bool` 在内存中判断身份，并合并 `last_seen/client_version`，不在每个 `/sync` 请求中写 SQLite。
4. 50ms 窗口内的触达由一个 timer 批量取走，在单个短事务中 `executemany` 写回；失败时合并回待写集合并有限重试，关闭时同步 flush。
5. 注册、删除、偏好等写事务继续使用实例写锁；WAL 允许读写并发。

内存认证与批量写回消除了 300 个请求同时竞争 `team_clients.db` 的热路径。WAL 和 `busy_timeout` 兼顾其他进程或短时交叉访问。验收要求日志中 `database is locked` 为 0。

### 9.2 ProfileStore

`_SqliteStore` 初始化数据库时同样启用 WAL、`synchronous=NORMAL`，每个连接设置 `busy_timeout`。画像仅由固定 worker 写入，最大写并发受到 `profile_refresh_workers` 限制；短事务只包含序列化后的单次 upsert，不在数据库事务中调用 embedding。

### 9.3 CPython GIL 与 SQLite 连接关闭

第一次正式慢速复测捕获到 `sqlite3_close` 持有 GIL 等待 SQLite mutex，同时查询线程持有 SQLite mutex 等待重新取得 GIL 的锁顺序死锁。把所有 SQLite 调用全局串行虽能避开死锁，但在 300 请求下造成超过 100 个超时，因此没有保留。

最终实现使用进程级读写门控：连接打开、execute、fetch、游标关闭、commit 和 rollback 走可并发侧；只有 connection close/finalize 走独占侧。这样 close 不会与正在释放 GIL 的 SQLite 调用重叠，普通 SQL 仍可并发。stdlib sqlite3 与可选 pysqlite3 使用各自匹配的 Connection/Cursor 子类，测试 instrumentation wrapper 使用代理，避免混用两套 C 类型。

## 10. `/status` 的多仓库布局兼容

skill 根目录本身不再保证是 Git 仓库，每个 skill 子目录才是独立仓库。`StatusResponse.git_branch` 改为 `str | None`：

- `current_branch(skill_root)` 成功时返回原值；
- 仅捕获 `dulwich.errors.NotGitRepository` 并返回 `git_branch: null`；
- `skill_dir` 和 `skill_count` 正常返回，HTTP 200；
- 其他异常仍按现有方式记录并返回 500，避免掩盖真实故障。

该变化只放宽响应字段可空，不改变 URL 和其他字段。

## 11. 指标与诊断

刷新服务的计数器和当前值都在自身锁内更新，通过 `/api/v1/stats` 的 `profile_refresh` 字段输出：

| 指标 | 类型 | 含义 |
|---|---|---|
| `queued` | 当前值 | 当前排队 client 数 |
| `running` | 当前值 | 当前运行 client 数 |
| `requested` | 累计 | `/sync` 请求刷新次数 |
| `enqueued` | 累计 | idle client 成功入队次数 |
| `coalesced` | 累计 | queued/running 下被合并的请求次数 |
| `queue_full` | 累计 | 因队列满未入队次数 |
| `completed` | 累计 | 成功刷新并落库次数 |
| `unchanged` | 累计 | revision/model 已新鲜的次数 |
| `failed` | 累计 | 刷新失败次数 |
| `rerun` | 累计 | 首次运行后执行补跑次数 |
| `embed_batches` | 累计 | 调用 `encode_batch` 次数 |
| `embed_items` | 累计 | 实际提交给 embedding 的 summary 数 |
| `reused_vector_items` | 累计 | 按 atom id + summary 复用的向量数 |

压力脚本另外记录：

- 每个 `/sync` 波次的 status、错误样本、p50/p95/p99/max；
- dashboard、health、status、watcher、stats、registry 等探针；
- LLM initial/follow-up、active/max active、延迟；
- embedding phase、active/max active、唯一输入、重复输入和延迟；
- pre-load/peak/final 进程线程数；
- 最终 main refs、candidates、COLD_START、队列和 watcher futures 状态。

embedding 重算浪费按以下口径报告：

```text
重复计算浪费 = 实际 embedding 输入数 - 首次必需的唯一输入数
覆盖缺口     = 场景预期必需输入数 - 实际完成的必需唯一输入数
```

重复浪费和覆盖缺口必须分开，不能把请求失败导致的少算写成“节省”。

## 12. 测试设计

### 12.1 确定性单元测试

使用 `Event`、`Barrier`、`Condition` 和计数器，不用固定 sleep 作为正确性判断。至少覆盖：

1. 无画像缓存时 `/sync` 在 embedding gate 未释放时立即返回 200；
2. 有旧画像时 `/sync` 返回旧画像，后台成功后下一次 sync 读到新画像；
3. atom 未变化时后台判 unchanged，不调用 embedding；
4. 新增一个 atom 只 embed 一个；
5. 删除 atom 不 embed，但画像和 point_meta 正确删除；
6. summary 原地修改只重算一个向量；
7. 仅 `used_skills`、`ux_score`、`tags` 修改不重算向量；
8. embed model 修改触发全部 summary 向量重算；
9. queued 重复请求只保留一个队列项；
10. running 重复请求最多补跑一次；
11. embedding 失败保留旧画像、清理 running，后续请求可重试；
12. 队列满时 `/sync` 仍成功，`queue_full` 增加，后续可重试；
13. `stop()` 在慢 backend 下按超时返回，`wait_idle()` 在正常完成后为 true；
14. 300 个并发 `authenticate_and_touch` 全部成功，无 SQLite lock；
15. 非 Git skill root 的 `/status` 返回 200 和 `git_branch: null`；
16. `SyncResponse` JSON 字段与修改前保持一致。

### 12.2 普通 30×30 测试

普通测试创建 30 个 baby skill 和 30 个无画像 client，验证与 300 场景相同的结构：

- mock LLM 支持 tool call；
- SkillEdit 最大并发不超过配置；
- embedding 被 gate 阻塞时，30 个 `/sync` 与控制面探针先完成；
- 释放后所有 skill 和画像收敛；
- exact embedding items、唯一输入、重算浪费和最终状态符合预期。

该测试的 gate 由测试主动释放，不实际等待 12/23 秒，以适合普通 PR 运行。

### 12.3 300×300 `stress` 测试

`pyproject.toml` 注册 `stress` marker。300 场景放在 `tests/stress/`，默认测试排除；main、相关改动和发版前使用显式命令运行。场景参数：

```text
skills=300
clients=300
watcher.max_concurrent=30
server.profile_refresh_workers=30
llm delay=12s
embedding delay=23s
```

固定 worker 使用 30 是为了让真实 23 秒延迟场景在可接受时间内完成；生产默认仍为 4。测试必须保留真实 uvicorn、真实路由、真实 watcher/Agno/tool-call 路径，只替换 LLM 和 embedding 后端。

脚本无论成功或失败都在 `finally` 写入已经完成的 wave、探针、mock、线程和收敛快照，避免本次基线出现异常时只剩最终 mock 汇总的问题。watcher 清理应直接核对 futures 的 stage 和最终 skill 产物，修正当前错误的 drain 等待条件。

## 13. 验收标准

| 编号 | 验收条件 |
|---|---|
| AC-001 | LLM 和 embedding 均阻塞时，300 个 `/sync` 全部 HTTP 200，无 timeout |
| AC-002 | 三个 300-request 波次 `/sync` p95 < 4 秒，max < 5 秒 |
| AC-003 | dashboard overview/skills、health、status、watcher/status、stats、registry 探针全部 200；dashboard 单探针 < 1.5 秒 |
| AC-004 | `database is locked` 为 0；300 个并发 client 认证更新全部成功 |
| AC-005 | SkillEdit 最大 active <= 30，画像刷新最大 running/embedding active <= 配置 worker 数 |
| AC-006 | 同一 client queued/running 合并正确，一次任务最多补跑一次，队列最终为 0 |
| AC-007 | cold 300 个必需输入和 delta 300 个必需输入最终完成；相同输入重复计算为 0，覆盖缺口为 0 |
| AC-008 | 300 个 skill 全部有 main，candidates 清空，正文和 Git 状态互不串扰 |
| AC-009 | 300 个 profile 的 source revision/embed model 与最终 atom 快照一致 |
| AC-010 | watcher 完成 300 次有效 SkillEdit、profile queue/running 清空、COLD_START 不存在，并能正常停机；不要求持续轮询产生的 no-op futures 瞬时为 0 |
| AC-011 | `/sync` 响应字段与既有 `SyncResponse` 兼容，无新增必填字段 |
| AC-012 | 压测保存每波延迟、接口探针、线程、后端调用计数和最终收敛产物 |

线程数作为诊断数据记录；硬性资源断言使用“慢 embedding 只在命名画像 worker 中运行”和 `profile_refresh.running <= workers`，避免把 uvicorn、watcher、短时 anyio 工作线程混成同一个不稳定总数。

## 14. 变更范围

```yaml
Change Impact Map:
  Change Target: /sync、用户画像刷新、SQLite 并发、status、300×300 压力验证
  Direct Impact:
    - src/xskill/team/server/profile_refresh.py
    - src/xskill/team/server/api.py
    - src/xskill/team/server/client_registry.py
    - src/xskill/api/app.py
    - src/xskill/recommend/engine.py
    - src/xskill/recommend/profile_store.py
    - src/xskill/recommend/_sqlite_base.py
    - src/xskill/config.py
    - config.yaml.server.example
    - tests/test_team_sync_profile.py
    - tests/test_recommend_engine.py
    - tests/test_user_profile.py
    - tests/test_profile_refresh_service.py
    - tests/test_skilledit_parallel.py
    - tests/stress/test_control_plane_300.py
    - scripts/loadtest_300_control_plane.py
    - pyproject.toml
  Indirect Impact:
    - manifest 推荐新鲜度变为最终一致
    - team server startup/shutdown
    - /api/v1/stats 输出增加 profile_refresh
    - client/profile SQLite journal 文件
  No Ripple Effect:
    - UMAP/t-SNE
    - SkillEdit prompt 内容
    - canary 评分规则
    - skill 候选阈值
```

```yaml
Interface Change Matrix:
  Existing: GET /api/v1/team/sync 在请求中同步刷新画像后返回 manifest
  New: GET /api/v1/team/sync 只读缓存、提交后台刷新请求后返回 manifest
  Conversion Required: No
  Compatibility Method: 保持 HTTP 200 和 SyncResponse 字段兼容
```

## 15. 实施顺序

1. [x] 增加 profile schema migration、稳定 revision 和 atom id + summary 向量复用，并补齐 engine/store 单元测试。
2. [x] 增加 `ProfileRefreshService`、状态合并、指标、`wait_idle` 和有限 shutdown，并完成 team app 生命周期接线。
3. [x] 改造 `/sync` 为缓存路径，修复 ClientRegistry 写并发和 `/status` 非 Git 根目录。
4. [x] 重写原同步画像路由测试，增加 30×30 普通测试。
5. [x] 把 300×300 harness 纳入 `stress` 测试，修正异常产物保存和 watcher drain 判断。
6. [x] 运行普通测试、30×30、300×300 真实延迟压力测试，生成修复后报告。
7. [ ] 通过发布验收后构建并发布新版本。

## 16. 发布验收

发布前必须全部满足：

1. 普通全量 pytest 通过，30×30 测试通过；
2. 300×300 `stress` 测试使用真实 12 秒 LLM、23 秒 embedding 延迟通过全部 AC；
3. 修复后报告包含与 2026-07-14 基线的逐项对比、sync/其他接口结果和 embedding 次数/浪费；
4. `python -m build` 生成 wheel 和 sdist，`python -m twine check dist/*` 通过；
5. 在干净环境安装 wheel，`xskill --version`、import、team app startup/shutdown 和关键 smoke test 通过；
6. 确认目标版本未在 PyPI 占用，Git tag、包 metadata 与安装后版本一致；
7. 发布后从 PyPI 安装该精确版本并复验版本与关键 smoke test；
8. tag、commit、压力报告和 PyPI 版本互相可追溯。

任何 300×300 核心验收失败、产物缺失、版本不一致或 PyPI 包安装失败，都停止发布，不以“部分接口正常”替代通过。

## 17. 已知风险与约束

1. Python 同步 HTTP 调用无法安全强制中止；daemon worker 和有限 join 只能保证进程退出不被无限阻塞，不能取消已经发出的 embedding 请求。
2. 默认 4 worker 下，300 个全新画像、每个 23 秒的理论最短完成时间约 28.75 分钟；这是有意的后端保护。`/sync` 可用性与画像最终收敛时间必须分别评价。
3. `queue.Queue` 状态在内存中，进程崩溃会丢失排队任务；画像 source revision 持久化，client 下一次 `/sync` 会重新提交，不会把旧画像误判为最新。
4. WAL 仍然只有一个 writer；短事务、进程内写锁和 `busy_timeout` 能处理本场景，但不能替代将来多进程部署时的专用存储方案。
5. source revision 对列表排序会把 `used_skills`/`tags` 的顺序视为无语义；当前聚合和推荐逻辑本来不依赖该顺序。若将来顺序具有业务含义，需要升级 revision 规则。
6. 补跑期间继续变化不会产生第三次连续刷新，必须依靠后续 `/sync` 再提交；这是防止单 client 长期占用 worker 的明确取舍。
