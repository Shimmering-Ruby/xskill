# `/sync` 控制面 300×300 压测基线

日期：2026-07-14  
结论：**当前版本不能通过设计中的 300-skill/300-client 验收。** 300 个 SkillEdit 最终全部晋升，LLM 并发限制也有效；但 `/sync` 仍同步等待 embedding，控制面在两次慢 embedding 压力下出现超时，快速 cache-hit 波次出现 49 个 SQLite 锁冲突导致的 HTTP 500，`/status` 三次均为 500。顶层 `success=false` 的直接原因则是压测脚本最后等待 watcher futures 清空超时，不能据此反推 300 个 SkillEdit 未完成。

## 1. 测试身份与环境

| 项目 | 实测值 |
|---|---|
| 被测 commit | `c90ca129fc1157c267fdd3a6613b3b7796d208df` |
| 执行机 | `iZt4n2cjzdw2ikuf6gmeozZ`，Linux `5.10.134-17.2.al8.x86_64`，4 vCPU |
| 时间 | 目录时间 `2026-07-14 00:13:42 +08:00`；server `00:13:56` ready；`00:21:29` shutdown |
| 总耗时 | `467.578 s`；其中数据准备 `12.646 s` |
| 规模 | 300 skills、300 clients |
| watcher | `max_concurrent=30`，poll interval `0.5 s` |
| anyio 请求线程 token | 300 |
| mock LLM | OpenAI-compatible chat completions，支持 tool call；配置延迟 12 s |
| mock embedding | OpenAI-compatible embeddings；配置延迟 23 s |

证据来源：`result.json`、`xskill-server.log`、压测脚本 `scripts/loadtest_300_control_plane.py`。由于异常路径只把最终 mock 快照写入 `result.json`，每波客户端请求延迟摘要没有落盘；本报告不提供不存在的每波 p50/p95/p99。

## 2. 主要结果

### 2.1 300 个 SkillEdit 已完成数据收敛

- mock LLM 共收到并完成 600 次请求：300 次首次请求返回 `write_file` 和 `commit_baby_to_main` tool calls，300 次 tool-result 后续请求返回最终文本。
- LLM 最大并发为 30，符合 watcher 的 `max_concurrent=30`；没有创建 300 个 LLM 工作线程。
- 日志中各有 300 条 `baby → main graduated`、300 条 `SkillEditAgent done + 1 candidate(s) removed` 和 300 条 `SkillEditAgent promoted`。
- 产物复核为 300 个 skill 目录、300 个 `main` ref、300 个空 `.candidates.yml`、300 个 `SKILL.md`；`COLD_START` 信号已不存在。最后一个 skill 在 `00:21:12` 记录 promoted。

因此，SkillEdit 的业务数据已收敛。脚本随后等待 `/api/v1/watcher/status` 的 `in_flight` 变为 0，连续轮询 30 s 后仍未满足，最终报 `TimeoutError: timeout waiting for watcher futures to drain`。这是**最终清理状态/脚本断言问题**；它值得修复并加入回归断言，但不是 300 个 SkillEdit 未晋升的证据。

### 2.2 LLM 观测

| 指标 | 值 |
|---|---:|
| started / completed | 600 / 600 |
| initial / follow-up | 300 / 300 |
| max active | 30 |
| latency min / p50 / p95 | 12.000 / 12.011 / 12.109 s |
| latency p99 / max / mean | 101.058 / 101.086 / 16.463 s |

这里的 latency 是 mock 从收到请求到返回响应的墙钟时间。首次请求还受 `llm_release` gate 控制，因而 p99/max 约 101 s；12 s 是配置的最短等待，不应把 101 s 写成模型自身推理耗时，也不应把这个非流式 mock 指标当成真实流式 TTFT。

### 2.3 embedding 次数、并发和重算

| 指标 | 值 |
|---|---:|
| started / completed | 599 / 599 |
| cold phase | 300 |
| cache-hit phase | 0 |
| one-new-atom phase | 299 |
| unique inputs | 599 |
| duplicate input calls | 0 |
| max active | 103 |
| latency min / p50 / p95 | 23.000 / 23.010 / 23.020 s |
| latency p99 / max / mean | 23.021 / 23.025 / 23.010 s |

本报告把 embedding 浪费定义为：

```text
embedding waste = 实际输入数 - 首次必需的唯一输入数
                = 599 - 599
                = 0
```

也就是说，没有相同输入被重复计算，cache-hit 波次也没有调用 embedding。场景原计划覆盖 300 个 cold 输入和 300 个 new-atom 输入，共 600 个；实际只有 599 个到达 mock backend。少的一个是某次 `/sync` 在到达 embedding backend 前失败导致的漏算，不是重算浪费。因此应同时报告：**重复浪费 0，计划覆盖缺口 1**。

mock 自身记录的单次 backend 墙钟耗时稳定在约 23.0 s。server 日志中的 tqdm 行在 cold 压力尾部显示约 68–71 s（另有少量超过 72 s）的端到端处理现象，这是请求排队、连接/线程调度和 23 s backend 等待叠加后的日志观测，不是 mock backend 单次耗时，也不是已落盘的 `/sync` 延迟分位数。

embedding 最大在途数达到 103，说明当前 `/sync` 没有使用设计中的独立、低并发画像刷新 worker。运行时现场观测到 server 进程线程峰值 337；该值因异常返回路径没有进入最终 JSON，但与脚本在慢 embedding 阶段读取 `/proc/<pid>/status` 的方式及 300 个 anyio token 相符。

## 3. `/sync` 与其他接口

### 3.1 三个 300-request 波次

| 波次 | HTTP 200 | HTTP 500 | embedding calls | 结论 |
|---|---:|---:|---:|---|
| cold | 300 | 0 | 300 | 请求最终完成，但日志长尾约 68–71 s，远超设计的 5 s max SLA |
| cache hit | 251 | 49 | 0 | 快速路径仍因认证阶段 SQLite 并发写发生 49 个 500 |
| one new atom | 300 | 0 | 299 | HTTP 全部返回 200，但少一个计划中的 backend 调用 |

日志合计另有一个 cache 阶段 `/sync` 探针返回 200，因此 server access log 总数为 852 个 200、49 个 500，共 901 条。cold 和 one-new-atom 压力阶段各自发出的额外 `/sync` 可用性探针在 2 s 内超时，未形成 access log。

49 个 500 均有 `pysqlite3.dbapi2.OperationalError: database is locked` 证据。堆栈定位到：

```text
team_sync -> _auth -> ClientRegistry.touch -> conn.execute
```

即每个 `/sync` 在认证阶段更新 client 活跃时间，300 个并发请求竞争 `team_clients.db` 写锁。该故障与 embedding 重算无关，且说明 cache hit 也不是可靠快速路径。

### 3.2 控制面探针

压测脚本在 cold 饱和、cache 空闲、one-new-atom 饱和三个时间点各并发探测一次，单探针 timeout 为 2 s。

| 接口 | cold 饱和 | cache 空闲 | one-new-atom 饱和 |
|---|---|---|---|
| `/api/v1/health` | 200 | 200 | 200 |
| `/api/v1/watcher/status` | 200 | 200 | 200 |
| `/api/v1/stats` | 200 | 200 | 200 |
| `/api/v1/registry/dirs` | 200 | 200 | 200 |
| `/api/v1/status` | 500 | 500 | 500 |
| `/api/v1/dashboard/overview` | timeout | 200 | timeout |
| `/api/v1/dashboard/skills` | timeout | 200 | timeout |
| 新增 `/api/v1/team/sync` 探针 | timeout | 200 | timeout |

dashboard 和新增 `/sync` 探针只在 cache 空闲阶段留下 200 access log；两次 embedding 饱和阶段均在客户端 2 s 限时内未响应。这直接验证了设计文档所述的共享 anyio 请求线程阻塞。health、watcher/status、stats 和 registry/dirs 仍可返回，不代表同步路由和 dashboard 正常。

`/status` 的三次 500 是另一项独立应用兼容性问题。堆栈为 `api_status -> current_branch(skill_dir) -> dulwich.Repo`，最终抛出 `NotGitRepository`：本场景的 skill root 下有 300 个独立 git 仓库，但 skill root 自身不是 git 仓库。接口没有处理该合法布局。

## 4. 故障边界

| 分类 | 本次证据 | 判定 |
|---|---|---|
| 应用故障 | cache-hit `/sync` 49/300 返回 500，根因为 `ClientRegistry.touch` SQLite 锁冲突 | 必须修复 |
| 应用故障 | 两个慢 embedding 压力点的 dashboard 与新增 `/sync` 探针超时 | 必须按设计把画像刷新移出请求线程 |
| 应用故障 | `/status` 在多独立 skill repo 布局下 3/3 返回 500 | 必须兼容或降级返回 |
| SLA 失败 | cold 日志长尾约 68–71 s；没有可用的每波客户端分位数 | 修复后必须让脚本无论成功失败都保存 wave 摘要 |
| 已通过 | 600/600 LLM 完成，最大并发 30；300/300 skill 晋升并清空 candidates | 保留为回归条件 |
| 脚本最终断言 | 数据收敛后，watcher `in_flight` 未在 30 s 内变为 0，顶层 `success=false` | 单独修复状态清理或断言，不能与上述应用 500/timeout 混为一项 |

## 5. 修复后验收表

| 检查项 | 当前基线 | 修复后通过条件 |
|---|---|---|
| 300-request cold `/sync` | 300×200，但长尾约 68–71 s | 300×200、0 timeout；p95 < 2 s、max < 5 s |
| cache-hit `/sync` | 251×200、49×500 | 300×200；`database is locked` 为 0 |
| one-new-atom `/sync` | 300×200，backend 299 calls | 300×200；后台刷新最终覆盖 300 个唯一 new-atom 输入 |
| dashboard 可用性 | 两个饱和阶段 overview/skills 均 timeout | 三阶段均 200、0 timeout；p95 < 1 s |
| health 与控制面 | health 等为 200；`/status` 3×500 | 所有列出的控制面接口均 200，`/status` 支持多 repo root |
| embedding 重算 | 599 actual、599 unique、0 duplicate；计划缺口 1 | cold 300 + delta 300 首次必需输入最终完成；duplicate=0，waste=0 |
| embedding 并发 | max active 103 | 不超过配置的独立画像刷新 worker 数，并保存队列/在途指标 |
| LLM/SkillEdit | 600 完成，max active 30；300 skills 收敛 | 保持 600 完成、max active <=30、300 main、300 candidates empty、无串扰 |
| 线程 | 现场峰值 337 | 记录 pre-load/peak；`/sync` 不再因 embedding 把请求线程推到 300-token 饱和 |
| 清理状态 | watcher drain 30 s timeout | `in_flight=0`、后台画像队列=0、COLD_START 不存在，脚本正常退出 |
| 诊断产物 | 异常时丢失 wave latency/probe 明细 | `finally` 中始终保存每波 status、latency、probe、线程峰值和收敛快照 |

