# 触发探针端到端修复报告（fix/trigger-probe-e2e）

设计文档：`docs/plans/2026-06-11-trigger-probe-real-agent-and-dashboard.md`
现场证据：`run_xskill/e1_rebuild/daemon.log` + `xskill_home/.xskill/registry.db`（只读勘查）
改动文件：`src/xskill/skill/trigger_probe.py`、`src/xskill/skill/description_opt.py`、
`tests/test_trigger_probe.py`、`tests/test_description_opt_e2e.py`、
`tests/test_agno_factory_probe_smoke.py`（新）、`scripts/probe_offline_smoke.py`（新）。
未碰 ingest / watcher / 选优算法本身。全程 mock，零真 API。

---

## 断点 1：诱饵清单静默为空（确认属实，已修）

**诊断**：`build_probe_catalog` 在 `.skill_index.pkl` 缺失时直接 `return []`，
只打一条 WARNING 就继续打分。`rebuild --force` 会清掉 index.pkl（`wipe_all_skills`
显式 unlink），所以这是**必现路径**：daemon.log 379–398 行连续 20 条
"诱饵清单为空"，随后 iter 0 train_score=0.750 ——零竞争环境的分数没有区分度，
却被当正常分参与选优。

**修复**（`trigger_probe.py::build_probe_catalog`）：
1. 先数 main 分支竞争者（`SkillRepo` + `main_sha`，排除本 skill）。
2. 索引缺失且**有**竞争者 → 调既有的 `rebuild_skill_index(skill_dir, embed_client)`
   现场重建（embed_client 调用方已传入，不另建），重建后走正常 cosine 检索。
3. 竞争者为 0（全库只有本 skill）→ 降级**无竞争模式**：显式 WARNING（含
   `catalog_size=0` 字样），不触 embedding，返回空清单。
4. 重建失败（embedding 后端炸等）→ 同样显式 WARNING 降级，绝不抛、绝不静默。

**显式标记**（`description_opt.py::optimize_description`）：诱饵清单全空时
打一条汇总 WARNING；返回 dict、`summary.json`、registry 行均带
`catalog_size` + `no_competition`，看板/复盘可据此降权——不许悄悄当正常分。

## 断点 2：触发评测未落库（勘查推翻——链路本来是通的，已用测试钉死）

**诊断**：建表（`registry.py` 的 `skill_trigger_eval` + 索引）、写入
（`record_trigger_eval`）、接线（`optimize_description` 尾部调用）**全部存在**。
直接查那次真实 run 的库：

```
xskill_home/.xskill/registry.db → skill_trigger_eval 有 1 行：
  skill=codex-single-task-workflow  exp_id=001  train=0.75  test=0.75
  n_cases=20  catalog_size=0  version_sha=''（按设计：commit 前评测，父版本可空）
```

逐 case 落盘也齐：`.description_optimization/001_*/{topic}/iterN_{split}_NN.json`
全部在（D8 满足）。"全程 0 条"的报告很可能查了**默认 `~/.xskill/registry.db`**，
而那次 daemon 的 HOME 被重定向到 `xskill_home/`，库在另一处。

**动作**：不改写入逻辑；新增测试把链路钉死（防回归 + 防再误判）：
- `test_no_competition_mode_marked_in_result_and_registry`：registry 行
  `catalog_size=0` + 结果/summary 显式标记 + WARNING。
- `test_per_case_results_archived_on_disk`：逐 case json 字段齐全
  （query/should/did/passed/triggered_skill/catalog/runs）。
- 既有 `test_trigger_eval_persisted` 继续覆盖正常入库。

## 断点 3：daemon 注入链路（核实是通的，补了真 agno 集成冒烟）

**核实结论**（代码证据）：
- serve 启动：`api/app.py` `@app.on_event("startup")` → `create_llm_client` /
  `create_embed_client`（失败 fail-loud raise，不带病跑）→ `init_context(...)`
  填 `_ctx`。
- commit 钩子：`skill_tools.py::_run_description_optimization` 从 `_ctx` 取
  llm/embed，**现场** `make_default_factory(config)` 传给 `optimize_description`。
- 模型配置：`make_default_factory` 读 `config["llm"]`，`config["llm_skill"]`
  非空字段覆盖；DeepSeek 直连路由到 agno `DeepSeek` 类（reasoning_content 回传）。
- agno 依赖：`requirements.txt` 与 `pyproject.toml` 均声明。
- 真实 run 佐证：daemon.log 中 description_opt 完整跑过 6 轮 iter（探针真在转）。

**缺口与补法**：此前所有探针单测用 `_FakeAgent` 替身，没有任何测试证明
**真 agno Agent** 能注册 probe 工具、执行时被 `StopAgentRun` 优雅终止、
record 闭包正确捕获。新增 `tests/test_agno_factory_probe_smoke.py`（4 测试）：
真 `make_default_factory` 构造真 `agno.agent.Agent`，仅在最低层把
`model.invoke` 换成脚本化 `ModelResponse`（零网络），验证 self/decoy/none
三种触发路径 + 模型配置确实来自 `config.llm`。

## 离线验证脚本（评测场景冒烟入口）

`scripts/probe_offline_smoke.py`：自动生成 fixtures（目标 skill + 3 个真 git
main 分支诱饵）→ **故意删掉 index.pkl**（模拟 rebuild --force 后状态）→
mock LLM 生成 case → 真 agno Agent 探针（模型层=确定性词袋决策）→ 完整
train/test 选优 → 打印逐 case 触发结果 + registry 行 + SMOKE OK/FAILED 判定。

实测输出（本机，约 5 秒）：索引现场重建成功；烂描述 iter0 train=0.5、
优化后 iter1 test=1.0 中选；`catalog_size=3 (no_competition=False)`；
registry 落 1 行；退出码 0。支持 `--skill-dir` 指向现成 skill 复用。

## 测试证据

- 新增/改动测试：`test_trigger_probe.py` +4（重建/无竞争/重建失败/空仓警告），
  `test_description_opt_e2e.py` +2（无竞争标记入库、逐 case 落盘），
  `test_agno_factory_probe_smoke.py` +4（真 agno 冒烟）。
- TDD：重建与无竞争标记的测试先红（`AssertionError: 重建后索引必须落盘` 等），
  实现后转绿。
- 全量：`python3.11 -m pytest tests/ -q` → **849 passed**（3 分 01 秒，0 失败）。
- 冒烟：`python3.11 scripts/probe_offline_smoke.py` → SMOKE OK。

## 遗留风险

1. **历史脏数据**：已入库的零竞争评分（如那行 catalog_size=0、0.75 无区分度）
   仍在库里——可识别（catalog_size=0）但未清洗；看板应对 `catalog_size=0` 行
   标灰/降权（本分支未动 dashboard 前端）。
2. **无竞争 WARNING 每 query 一条**：竞争者为 0 时 `build_probe_catalog` 按
   query 调用、每次都警告（与旧行为同量级噪音）；`optimize_description` 已加
   一条汇总 WARNING，未做逐条去重。
3. **重建的索引含 baby/staging**：`rebuild_skill_index` 索引所有有 SKILL.md
   的目录，main 过滤在 catalog 层做——功能正确，索引略大，未改（该函数是
   ingest/搜索共用件，本分支不碰）。
4. **冒烟保真度**：脚本化词袋模型只验"链路通"，不验真实 LLM 的触发保真度；
   真模型行为差异仍需 live 评测（`pytest -m live_agent` 一类）兜底。
5. **version_sha 为空**：commit 前评测按设计记父版本（首版为空）；看板按
   版本聚合时需把空 sha 当"首版"处理。

## 挂死缺陷修复（agno 遥测出网 + 不可达端点无超时）

### 症状与评审定位

独立评审报告：真 agno Agent（2.5.13）在 model 初始化阶段遇到不可达端点会
无限挂死。受害者：`test_agno_factory_probe_smoke.py`（120s+ 超时）和
`scripts/probe_offline_smoke.py`（120-180s 超时跑不完）。

### 复核后的真根因（与评审定位有偏差）

逐步计时 + httpx 请求埋点（instrumentation，在代码里插记录点）实测：

- **黑洞 base_url（127.0.0.1:9）本身不挂**：agno model 构造是惰性的（不建连），
  工厂构造测试单跑只要 0.46s；`model.invoke` 在测试里已被脚本替身换掉。
- **真凶是 agno 遥测**：agno Agent 默认 `telemetry=True`，每次 `agent.run()`
  结束后**同步** POST `https://os-api.agno.com/telemetry/runs`。无外网/DNS
  慢的环境下该请求每次阻塞 3~60s+（防火墙丢包环境则无限挂死）。探针每 case
  跑一次 agent.run → 逐 case 累加，正好呈现"120s+ 吊死"的观感。
  实测受害测试文件 4 例 74.3s，其中 decoy 单例 61s 全耗在遥测 POST 上。

### 修法（三层防御，全部落地）

1. **关遥测（根治）**：`make_default_factory` 对 Agent 构造
   `kwargs.setdefault("telemetry", False)`——生产/探针都不该把运行数据报给
   厂商，更不能让它挂死流程。
2. **模型层显式网络超时**：`build_chat_model` 给 agno model 注入
   `timeout=httpx.Timeout(request_timeout, connect=connect_timeout)`（直通
   openai SDK client）+ `max_retries=client_max_retries`。配置（`llm` 段，
   全可选）：`request_timeout`（默认 60s）/ `connect_timeout`（默认 10s）/
   `client_max_retries`（默认 0——瞬时重试统一由 `_wrap_with_retry` 负责，
   client 层再 retry 会与之相乘）。不可达端点从"无限挂"变成秒级抛清晰异常。
3. **探针单 case 墙钟兜底**：`probe_trigger` 新参 `case_timeout`（配置
   `skill_opt.probe_case_timeout`，默认 60s，0=关闭），`agent.run` 放守护
   线程跑、`join(timeout)`，超时记 WARNING 视作"未触发"——任何底层组件
   （网络/SDK/agno 内部）的意外阻塞都不能挂死优化循环。`description_opt`
   与 `rerun_probe_case` 全程透传该配置（只动超时管道，不碰评分算法）。

### 测试与脚本修正

- `test_factory_builds_real_agno_agent`：加断言 `agent.telemetry is False`、
  `agent.model.timeout is not None`（挂死缺陷回归）。
- 新增 `test_blackhole_unresponsive_endpoint_fails_loud_within_seconds`：
  本地"陷阱"端口（接受 TCP 永不回包，模拟防火墙 DROP）上**真发请求**，
  实测 3.2s 抛 `ModelProviderError: Request timed out`（断言 <15s）。
  注意调用约定：工厂包装链 `traced_invoke(messages, **kwargs)` 只收一个
  位置参数，`model.invoke` 必须**关键字传参**（与 agno 内部一致）。
- 新增 `test_blackhole_refused_endpoint_fails_fast`：拒连黑洞秒级抛错（<10s）。
- 测试共享 `_CFG` 加 `request_timeout: 3 / connect_timeout: 2 / max_retries: 1`，
  任何漏 mock 的真出网都秒级 fail-loud。
- `probe_offline_smoke.py`：加 55s 墙钟看门狗（超时打印 FAIL + 各线程栈 +
  `os._exit(2)`），结尾打印 `RESULT: PASS/FAIL (elapsed, exit code)`。

### 实测耗时（修复前 → 后）

| 受害者 | 修复前 | 修复后 |
| --- | --- | --- |
| test_agno_factory_probe_smoke.py（4→6 例） | 74.3s（decoy 单例 61s） | 6 passed in ~1-5s |
| scripts/probe_offline_smoke.py | 120-180s 超时跑不完 | RESULT: PASS, 1.8s |
| 核心逻辑 test_trigger_probe + description_opt_e2e | — | 20 passed in 1.4s（未动算法，保持绿） |

### 全量测试与既有失败的归属判定（非本次改动引入）

全量 `make test`（`pytest tests/ --ignore=docker_e2e --ignore=live`，本次加
`--timeout=120` 防吊死）：**1 failed, 850 passed in 225.90s**。唯一失败是
`test_e2e_xskill_serve_auto.py::test_canary_flip_promote_and_install_new_version`
（"canary controller promote staging→main" 轮询 180s 预算耗尽）。

**复核结论：该失败是负载诱发的偶发（flaky），非确定性故障，与本次改动无关。**
证据（本次亲测，非沿用前一版报告的推断）：

- **单独跑必过**：把该 e2e 单测隔离跑，clean HEAD（ff1e2cf，stash 掉本次 7 个
  改动）**1 passed in 69.42s**；带本次全部改动**1 passed in 68.75s**——两者都过、
  耗时几乎一致。说明本次改动不碰它、也没拖慢它。
- **失败只在满负载全量套件里出现**：该 e2e 用 180s 轮询预算等 canary 控制器把
  staging 升 main，全量套件并发跑时系统负载高 → 轮询超预算 → 偶发超时。它驱动真
  claude CLI + 假 LLM + daemon 全链路，对机器负载敏感，是已知的 flaky e2e。
- **去掉该 flaky 后全绿**：全量套件 deselect 这一条 → **850 passed, 1 deselected
  in 103.97s**，其余每条确定性通过。

故判定：该失败属既有 flaky e2e（机器负载相关），不在本分支处理范围，与挂死修复无关。
本次改动相关的所有测试（探针冒烟 6 例、核心 trigger/desc 逻辑、3 条挂死回归）全部
稳定通过。基线对比：套件实际收集 851 条，本次新增 2 条挂死回归测试（数量只增不减）。
