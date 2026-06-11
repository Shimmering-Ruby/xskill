# 触发评测改造：真跑代理闭环探针 + 真实技能清单 + 触发率看板

状态：已实现并发版 0.6.1a8（Phase 1 引擎 commit b87415b；Phase 2 看板 commit e272f36）
作者：xskill
日期：2026-06-11
关联：取代 [2026-06-11-skill-description-optimization-design.md](./2026-06-11-skill-description-optimization-design.md) 里的 LLM-as-judge 触发判定

---

## 1. 背景与动机

`0.6.1a7` 发的 description 优化里，触发判定用的是 **LLM-as-judge**——把候选描述 + 几个通用诱饵拼成伪 `available_skills` 清单，问 LLM "你会调哪个 skill"，跑 N 次算触发率。

这条路的两个根本缺陷：

1. **是"意见"不是"行为"**：让模型回答一个元问题（meta-question，关于"会怎么做"的问题），不是让它在真实工具调用循环里真的做一次决策。
2. **诱饵清单不真实**：清单里只有"本 skill + 两个通用诱饵"。技能列表里只有一个相关项，它当然只会触发那一个——触发率虚高、没有区分度。真实环境里一个 skill 要在**一堆语义相近的已安装 skill**中抢触发。

本设计把触发判定换成 **真跑一个 agno 迷你代理的闭环探针（closed-loop probe，闭环=真执行+真观测）**，并用**项目里真实的、语义相关的、已 graduate 到 main 的技能清单**作为竞争环境。触发率作为一类有价值的度量持久化，并在看板可视化、可逐 case 重跑。

**保真度天花板（必须诚实声明）**：探针代理是 agno + DeepSeek/GLM，**不是 Claude Code 本体**。它给的不是"Claude Code 会不会触发"的绝对真值（真值需跑 `claude -p`，daemon 无 Claude 模型）。它的价值在**相对比较**——A、B 两版描述哪个在真实竞争环境里更容易被一个真实代理选中——这个信号比元问题可信得多。

---

## 2. 决策摘要

| # | 决策 | 取舍 |
|---|---|---|
| **D1** | 探针 = agno 代理 + skill-as-tool + `StopAgentRun` | 每个候选 skill 注册成一个工具；代理一旦 call 本 skill 的工具 → 工具里记一笔 triggered 并抛 `agno.exceptions.StopAgentRun`（`stop_execution=True`，agno 内部置 `stop_after_tool_call` 当轮跳出、**不外抛**）→ `agent.run()` 返回后读那一笔。即你说的"检测到调用哪个技能就终止"。 |
| **D2** | 真实诱饵清单 = query 锚点的语义检索 | embed 评测 query，从 **main 分支** skill 里取 cosine 最近的 N 个作诱饵，注入本候选 skill。复用 `skill_manifest` / `profile_reco` 的 cosine 积木，锚点从"client 质心"换成"query 向量"。 |
| **D3** | 只用 main 分支 skill，排除 baby / staging | `[s for s in repo if main_sha(s.path)]`——婴儿分支（baby，无 main 正文的 stub）和旁路（staging/canary 灰度候选）都不进诱饵清单。排序用主分值 `ux_avg(side="main", days=30)`。 |
| **D4** | 数量帽子吃配置 | 诱饵清单大小 = `config.skill_opt.catalog_max_skills`（镜像 CC 的 listing budget；默认值见 §4）。不臆造、从配置读。 |
| **D5** | 假工具，不上 OS 沙箱 | 触发即终止 → 代理全程不执行任何真实动作。工具空间 = 候选 skill 工具（调用=拦截+终止）+ 少数只读/空操作桩。无副作用就无需沙箱；OS 沙箱又重又跨平台分裂，与"兼容 Windows/Linux"冲突。 |
| **D6** | 喂 probe 前按 CC 单条描述上限截断 | 代理看到的就是真实会看到的截断版。上限做成配置 `catalog_desc_cap`；默认值依"源码核实记忆（shrink-to-fit、约 250）"，**实现时再确认一次具体数字**，不照搬文档的 1536。 |
| **D7** | no-fallback：删掉 LLM-judge | 按 CLAUDE.md，整条 `_judge_trigger` 换成 probe，不留并存/降级。source 唯一。 |
| **D8** | 触发率持久化 + 看板 | 离线探针触发率按 skill/版本入库 + 逐 case 落盘；看板加触发率栏 + 逐 case 视图 + **重跑按钮**（受控 action 端点，A 方案）。 |
| **D9** | 成本闸 | 真跑代理一轮比单 judge 贵，吃 `max_llm_calls` 硬上限 + `rate_limit` + 脆弱 API 重试；`runs_per_case` 取均值降方差。 |

**两种"触发率"必须分清**（别混）：

| | 含义 | 来源 |
|---|---|---|
| **离线探针触发率** | 评测时这版描述在合成 case 上被探针触发的比例（描述质量信号） | 本设计 probe 产出 |
| **线上真实触发率** | skill 在生产轨迹里实际被调用的频次（真实使用信号） | 已有 `mark_skill_used` → stats 看板 |

本设计新增的是**离线探针触发率**；看板里两者并排但语义不同，不可当成一回事。

---

## 3. 架构：外科手术式替换

description 优化骨架不动（case 生成 → 分层 train/test 切分 → 触发判定 → improve 进化 → **test 集选优** → 写回 → 归档）。**只换其中一个函数**：

```
description_opt._judge_trigger(query, skill_name, desc, catalog_others)   # 删
    ↓
trigger_probe.probe_trigger(query, skill_name, desc, catalog, *, factory) # 换
```

`catalog`（诱饵清单）的组装从"传入的 other_skill_catalog/通用诱饵"换成 **D2 的 query 锚点真实检索**。

### 3.1 新模块 `src/xskill/skill/trigger_probe.py`

```
probe_trigger(query, skill_name, candidate_desc, catalog, *,
              agno_agent_factory, desc_cap) -> str   # 返回触发的 skill name 或 "NONE"
build_probe_catalog(query, skill_name, *, skill_repo, embed_client,
                    max_skills, desc_cap) -> list[dict]  # D2/D3/D4/D6
```

- 工具：对 `[本 skill] + catalog` 每个 skill 造一个 `use_<slug>()` 工具，docstring = 该 skill 截断后的 description（agno 用 docstring 当工具说明，正好等价于"看着描述决定调不调"）。工具体：记 `triggered=name` + 抛 `StopAgentRun`。
- 桩工具：`read_file/list_files` 等返回空，给代理一个合理动作空间但零副作用。
- 系统提示词：镜像 CC——"你是编程代理，下面是 available_skills，根据用户请求决定调用哪个 skill（用对应工具），不合适就什么都不调"。
- 跑 `agent.run(query)`；读 `triggered`（None → "NONE"）。

### 3.2 改 `description_opt.py`

- `_judge_trigger` 删除。
- `_score_description` 内层从 `_judge_trigger` 改调 `probe_trigger`，catalog 由 `build_probe_catalog(query,...)` 现算（每个 query 的竞争对手可不同——这正是你要的"不同技能列表触发率不同"）。
- 需要 agno 工厂 + embed_client + skill_repo：从 `_ctx` 取（`make_default_factory(config)` 造工厂）。

### 3.3 触发率持久化（registry.db）

新表（手动建、不兼容老库）：

```
skill_trigger_eval(
  skill_name, version_sha, exp_id, ts,
  train_score, test_score, n_cases, catalog_size
)
```

- 每次优化 run 落一行（per skill/版本的离线触发率 + 趋势）。
- 逐 case 明细继续落 `.description_optimization/{exp}/{topic}/{job}.json`（含 catalog 快照、每轮判定）——看板逐 case 视图读盘，重跑按钮改这些盘文件 + 追一行 DB。

### 3.4 看板（Phase 2）

- 后端（`dashboard/mount.py`）：
  - `GET /dashboard/api/skills/<name>/trigger` —— 读 `skill_trigger_eval` 出 per-版本触发率 + 趋势。
  - `GET /dashboard/api/skills/<name>/trigger/cases?exp=<id>` —— 读盘出逐 case 明细。
  - `POST /dashboard/api/skills/<name>/trigger/rerun`（**A 方案 action 端点**）—— 现起 probe 重跑指定 case，吃 `max_llm_calls`/`rate_limit`，走现有 Basic 口令，best-effort、失败不崩看板。
- 前端（`static/app.js` + `index.html`）：skill 详情页加"触发率"栏（per-版本数字 + SVG 趋势）+ 逐 case 表（should/did/通过）+ 每行"重跑"按钮。

---

## 4. 配置（新增 `skill_opt` 字段）

```yaml
skill_opt:
  # ... 既有字段 ...
  catalog_max_skills: 12   # 诱饵清单数量帽子(镜像 CC listing budget)
  catalog_desc_cap:  256   # 单条 description 截断上限(CC 实测值,实现时复核)
  rerun_enabled:     true  # 看板"重跑单 case"action 端点开关
```

---

## 5. 测试方案

- **单测** `tests/test_trigger_probe.py`：mock agno 工厂/代理，模拟"调了某 skill 工具" / "什么都没调"，断言 `probe_trigger` 返回正确 name/NONE；`build_probe_catalog` 只取 main、按相似度排、吃数量帽子、按 cap 截断 description。
- **单测** description_opt：原 judge 用例改为 probe（mock probe），断言 test 集选优逻辑不变。
- **单测** 持久化：`skill_trigger_eval` 写读、看板端点 200 + 形状。
- **E2E**：复用 docker e2e，确认 commit 流程跑 probe 不崩、不阻断 commit。
- 全程吃 `max_llm_calls` + 重试；看板重跑端点走口令 + 预算闸。

---

## 6. 分期

- **Phase 1（引擎）**：`trigger_probe.py` + `build_probe_catalog` + 改 `description_opt`（删 judge）+ `skill_trigger_eval` 持久化 + 单测/E2E/pylint。
- **Phase 2（看板）**：触发率栏 + 逐 case 视图 + 重跑 action 端点 + 前端。

每期独立可发、可验。
