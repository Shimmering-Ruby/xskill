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
