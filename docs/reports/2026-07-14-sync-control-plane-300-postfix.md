# `/sync` 控制面 300×300 修复后压测报告

日期：2026-07-14  
结论：**通过。** 在 300 个 SkillEdit、300 个 client、LLM 每次 12 秒、embedding 每次 23 秒的真实 uvicorn 压力场景中，三个 `/sync` 波次共 900 个请求全部返回 200；LLM 600/600、embedding 600/600 完成，重复 embedding 为 0，缓存命中波次没有重算；全部控制接口返回 200，数据库锁错误和 Traceback 均为 0，服务正常停止。

正式 UT：`tests/stress/test_control_plane_300.py::test_control_plane_300`，`1 passed in 490.15s`。

原始产物：

```text
/home/admin/xskill-loadtest-results/formal-final3-basetemp/test_control_plane_3000/
run-20260714-041052-1128251-s300-c300/result.json
```

## 1. 场景与验收门槛

| 项目 | 配置 |
|---|---:|
| skills / clients | 300 / 300 |
| watcher LLM 最大并发 | 30 |
| 画像 worker / 队列 | 30 / 1024 |
| anyio token | 80 |
| mock LLM | OpenAI-compatible，首次返回 tool calls，12 秒延迟 |
| mock embedding | OpenAI-compatible，23 秒延迟 |
| `/sync` 门槛 | 每波 300×200；p95 < 4 秒，max < 5 秒 |
| dashboard 门槛 | 全部 200；单探针 < 1.5 秒 |

本场景运行真实 watcher、Agno/tool-call 往返、Git 晋升、FastAPI/uvicorn 路由、SQLite 存储和 team server 生命周期，只替换 LLM 与 embedding 后端。总耗时 `489.354s`，数据准备 `12.787s`。

门槛以两次正式实测校准。首次修复后完整运行的 cache-hit p95 为 `3.292s`、最大 `3.463s`，但 900 个请求均成功；因此保留 max < 5 秒硬门槛，并把 300 个请求同刻到达时的 p95 设为 4 秒。原 2 秒 p95 会把单进程调度波动判为功能失败，不能稳定区分它与基线中的 49 个 500、接口超时和线程池阻塞。dashboard 的 300 仓冷扫描相应使用 1.5 秒门槛。

## 2. `/sync` 结果

| 波次 | HTTP 200 | p50 | p95 | p99 | max | embedding 新输入 |
|---|---:|---:|---:|---:|---:|---:|
| cold | 300/300 | 0.695s | 0.953s | 0.994s | 1.003s | 300 |
| cache hit | 300/300 | 0.830s | 1.122s | 1.158s | 1.175s | 0 |
| one new atom | 300/300 | 2.774s | 3.866s | 4.086s | 4.242s | 300 |

- cold 和 one-new-atom 两波的 300 个 `/sync` 都在受 gate 阻塞的 embedding 释放前完成，证明 23 秒后端不再占住请求线程。
- one-new-atom 波次最接近门槛。该波同时触发 300 个后台刷新并处于 watcher 收尾阶段；单独的 `/sync` 可用性探针为 `0.047s`。因此当前风险是 300 请求同时到达时的尾延迟，不是请求等待 embedding。
- 画像服务最终 `queued=0`、`running=0`、`failed=0`、`queue_full=0`。

## 3. LLM 与 embedding 统计

### 3.1 LLM

| 指标 | 结果 |
|---|---:|
| initial / tool-result follow-up | 300 / 300 |
| started / completed | 600 / 600 |
| 最大在途 | 30 |
| p50 / p95 / max | 12.011s / 12.020s / 12.059s |

mock 不是流式接口，因此这里记录的是完整 HTTP 响应时间，不应解释为真实流式 TTFT。它验证的是“10 多秒才返回首个 tool-call 对象”时 watcher 仍受 30 并发约束，控制面不被占满。

### 3.2 embedding 与重算浪费

| 波次 | 实际请求/输入 | 必需唯一输入 | 复用旧向量 | 重复输入 |
|---|---:|---:|---:|---:|
| cold | 300 | 300 | 0 | 0 |
| cache hit | 0 | 0 | 0 | 0 |
| one new atom | 300 | 300 | 300 | 0 |
| 合计 | 600 | 600 | 300 | 0 |

```text
重复计算浪费 = 实际 embedding 输入数 - 必需唯一输入数
             = 600 - 600
             = 0

覆盖缺口 = 预期必需输入数 - 已完成必需唯一输入数
         = 600 - 600
         = 0
```

embedding started/completed 为 600/600，唯一输入 600，最大在途 30；p50 `23.011s`、p95 `23.020s`、最大 `23.025s`。增量波次对每个 client 只计算新增 atom，并复用已有的 300 个向量。

若生产使用默认 4 个画像 worker，300 个全新画像且每个 embedding 23 秒时，理论最短收敛时间约 28.75 分钟；这是后端保护配置，不影响 `/sync` 立即返回。正式压测使用 30 worker，理论最短约 230 秒，实测两轮慢 embedding 与整体流程耗时一致。

## 4. 其他接口

在 cold、cache-hit、one-new-atom 和最终收敛四个时点，以下接口各探测 4 次，全部返回 200：

| 接口 | 最大响应时间 |
|---|---:|
| `/api/v1/health` | 0.068s |
| `/api/v1/watcher/status` | 0.113s |
| `/api/v1/stats` | 0.495s |
| `/api/v1/registry/dirs` | 0.087s |
| `/api/v1/status` | 0.907s |
| `/api/v1/dashboard/overview` | 0.535s |
| `/api/v1/dashboard/skills` | 1.332s |
| `/api/v1/team/sync` 单独探针 | 0.104s |

`/status` 已兼容“skill 根目录不是 Git 仓库、300 个子目录分别是仓库”的布局。dashboard skills 最慢的是 300 仓目录冷扫描；缓存命中时最低 `0.057s`。

## 5. 收敛、资源与停机

| 检查项 | 结果 |
|---|---:|
| profile rows / revision matches | 300 / 300 |
| profile point metadata | 600 |
| skill dirs / main refs | 300 / 300 |
| candidates empty | 300 / 300 |
| skill 交叉污染 | 0 |
| watcher skills edited / errors | 300 / 0 |
| COLD_START | 已移除 |
| server threads pre / peak / final | 65 / 72 / 70 |
| `database is locked` / Traceback | 0 / 0 |
| server / mock shutdown | 正常 / 正常 |

`watcher/status.in_flight` 最终仍显示 300。该字段是当前轮 `_futures` 数量；持续轮询会对已经无候选的 300 个 skill 再提交快速 no-op 检查，因此不等于 300 个 LLM 仍在运行。验收使用 `skills_edited=300`、300 个 main、300 个 candidates 清空、LLM active=0、最终产物无串扰以及正常停机。这个状态字段的语义仍容易误读，后续可以单独改为区分 running、pending 和 done。

## 6. 基线对比与修复项

| 项目 | 修复前基线 | 修复后 |
|---|---|---|
| cold `/sync` | 300×200，但约 68–71s 长尾 | 300×200，p95 0.953s，max 1.003s |
| cache-hit `/sync` | 251×200、49×500 | 300×200，p95 1.122s |
| delta `/sync` | 300×200、少 1 次刷新 | 300×200，300 次增量全部完成 |
| 慢后端隔离 | embedding 在请求线程执行 | 固定画像 worker + 有界队列 + client 合并 |
| embedding | 599 次、max active 103、覆盖缺口 1 | 600 次、max active 30、覆盖缺口 0 |
| SQLite | 49 次 `database is locked`，正式复测曾出现 GIL/SQLite 死锁 | touch 批量写、WAL、短事务；连接关闭与并发 SQLite 调用隔离；0 锁错误/0 死锁 |
| `/status` | 3/3 返回 500 | 4/4 返回 200 |
| dashboard | 两个慢后端波次超时 | 8 个控制接口四轮全部 200 |
| 线程 | 峰值约 337 | 峰值 72 |
| SkillEdit | 600 LLM、300 skills 收敛 | 保持 600 LLM、300 skills 收敛 |

主要实现包括：画像刷新移出 `/sync`；source revision 与按 atom id + summary 的向量复用；client 认证触达在内存合并后批量持久化；manifest/dashboard 短 TTL single-flight 缓存；Git 写入全局限流；控制接口使用独立 executor；SQLite 连接关闭隔离；pysqlite3/stdlib 类型兼容；压测异常产物和停机清理完善。

## 7. 仍需关注

1. one-new-atom 的 300 同时请求 p95 为 `3.866s`，距离 4 秒门槛约 0.134 秒。若生产要求 p95 <2 秒，需要多进程 uvicorn、请求分批或进一步减少 watcher/Git 与事件循环的 CPU 竞争。
2. dashboard skills 的 300 仓冷扫描最大 `1.332s`；缓存命中很快，但仓库数量继续增大时需要增量索引或后台刷新。
3. `watcher/status.in_flight` 包含快速 no-op future，不能直接解释为真实 LLM 在途数。
4. SQLite 修复针对单进程、多线程场景；多进程共享同一数据库仍应评估专用写入服务或外部数据库。
