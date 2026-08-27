# 交接：只用 OpenTelemetry 重跑一条 SkillEdit compact bench

日期：2026-08-21
状态：一条大轨迹 + 提示词 v2 已跑完，临时可视化页还挂着。下一个 agent 先读本文再动手。未经用户点头不要开 30 次回放，也不要接 Phoenix、Langfuse、OpenLIT。

## 用户要的是什么

用户先问有没有 OpenTelemetry 一类的 LLM 或 Agno 可观测库，用来发现 agent 行为模式、看时间分布。后来明确说：不要把银河系转进来，就只用 OTel，给一个临时页可视化。并从已经调研过的 benchmark 里挑一条重跑。

那次 benchmark 是 2026-08-20 的 SkillEdit 压缩回放：8 条真实轨迹是原材料（4 大、4 小），三版提示词，格子是 2 类 × 3 版 × 5 次 = 30 次。任务书在 `docs/plans/2026-08-20-skilledit-compact-bench.md`。不要再理解成 8 × 3 × 5 = 120。

本轮只重跑了 1 条，用来看 OTel 能看见什么。

## 环境边界（必守）

- Agent 进不了格力现网。现网 Dashboard 是 `http://7.220.144.233:9961`。本机 Docker、本机 `xskill serve`、`hub.xskill.wiki` 都不是现网，不能当线上结论。
- 回放只用 `~/.aikey` 里的官方 DeepSeek（`DEEPSEEK_BASE_URL_OPENAI`、`DEEPSEEK_MODEL_FAST`、`DEEPSEEK_API_KEY`）。不读、不打印 `~/.xskill/config.yaml` 里的 key，不把密钥写进对话或 HTML。
- 2026-08-21 14:36 第一次跑官方接口返回 402 Insufficient Balance。用户说充钱后再跑，14:38 重跑成功。若再 402，停掉空转重试，问用户，不要换别的 endpoint 除非用户点头。
- 夹具必须在 `/tmp/...` 隔离目录，禁止改本机正在用的 `~/.xskill/skill`。
- 对用户说话少用 Markdown 墙，不要用一对星号加粗，不要用斜杠把并列词糊在一起。
- XQ 一律理解为 XSkill。

## 已经做完的

### 埋点（只用 OTel SDK）

没有装 Phoenix、Langfuse、OpenLIT、openinference。venv 里只加了：

- `opentelemetry-api==1.44.0`
- `opentelemetry-sdk==1.44.0`
- `opentelemetry-semantic-conventions==0.65b0`

xskill 的 venv 没有 `pip` 可执行文件，安装用：

```bash
uv pip install --python /home/admin/xskill/.venv/bin/python opentelemetry-api opentelemetry-sdk
```

不要把这两包装进 `pyproject.toml`，除非用户要求产品化。

埋点是临时脚本，不改仓库里的 `agno_factory` 或 `SkillEditAgent` 默认行为。脚本在 `/tmp/otel-skilledit-viz/run_one.py`，做法是：

- `JsonFileExporter` + `SimpleSpanProcessor`，每个 span 结束就写 `spans.json`
- 补丁 `skilledit_compact_replay.wrap_factory`：每次 `model.invoke` 开 `llm.invoke`，工具用 Agno `tool_hooks` 开 `tool.<name>`
- 补丁 `context_budget._compact_history_in_place` → `context.compact`（挂在所属的 `llm.invoke` 下面）
- 补丁 `SkillEditAgent.maybe_run`、`_run_baby_batch`、`_trace_run` → 编排层
- 最外层 `skilledit.replay`

属性只记计数、工具名、短参数，不落 prompt 正文，不落 API key。

### 挑了哪一条

清单：`scripts/bench/real/skilledit_manifest.json`

挑的是大类第 1 条 + 当前 HEAD 提示词 v2（和 08-20 冒烟同一条，方便对照）：

- traj_id：`traj_oc_xskill-debug_ses_1da2`
- 4484 行，1 个 atom，atom 正文约 16604 字
- 提示词：`scripts/bench/skilledit_prompts/v2_current.txt`
- 回放入口：`scripts/bench/skilledit_compact_replay.py` 的 `run_one`

08-20 冒烟（同一条、v2）：212.4 秒，21 轮，commit 成功，compact 0 次。

08-21 OTel 重跑：573.1 秒，39 轮，commit 成功，compact 5 次，读完就 compact 率 0.8。没有触帽（帽是 80 轮或 20 分钟）。

### 这次 span 里能看见的行为

92 个 span：

- `llm.invoke` 39
- 工具 44：`read_file` 25，`read_traj` 8，`grep_files` 4，`write_file` 2，`list_files` 2，`atom_task_read` 1，`skill_read` 1，`commit_baby` 1
- `context.compact` 5，各自大约 48 到 78 秒，合计大约 331 秒，都嵌在某次 `llm.invoke` 里，所以瀑布图上会有特别长的黄条
- 编排 3 + 根 1（见下）

墙钟几乎全是模型。工具合计大约 0.12 秒。模式是先 `atom_task_read` 和 `skill_read`，再连打 `read_traj` 和 `read_file`，顶到 compact，最后才写文件并 `commit_baby`。这和 08-20 调研里「三版提示词都在读轨迹上打转」对得上。

ROUND 头仍是现网窗：`spill@37.1k | compact@38.3k`。

### 「编排」是什么

不是模型，也不是工具。是回放脚本套在 SkillEdit 外面的四层控制流，一层包一层：

1. `skilledit.replay`：整次回放（夹具、换提示词、收分）。属性有 traj_id、prompt=v2、size=large、traj_lines=4484、atom_chars=16604、commit_ok、invoke_rounds=39、compact_count=5。
2. `skilledit.maybe_run`：守门（candidates、staging、jam），决定要不要编辑。这次 `maybe_ok=true`。
3. `skilledit.baby_batch`：一批 baby。属性 `batch.n=4`，`batch.ids=atom_traj_oc_xskill-debug_ses_1da2_0001`（清单里这条 traj 只有 1 个 atom）。
4. `skilledit.agent_run`：一次 `agno agent.run`。`user_chars=1561`。39 次模型和 44 次工具都挂在它下面。

这四条「含下级」都是大约 572 到 573 秒，几乎等于墙钟。那是包进去的时间。扣掉子 span 之后，四层自耗加起来大约 1 秒（git、夹具、收分）。页面时间分布已改成按自耗计，灰条不会再看起来像烧了十几分钟。

对应产品代码：

- `SkillEditAgent.maybe_run`：`src/xskill/agents/skill_edit_agent.py`
- `_run_baby_batch`、`_trace_run`（里面调 `agent.run`）
- 回放：`scripts/bench/skilledit_compact_replay.py`

### 临时可视化页

目录：`/tmp/otel-skilledit-viz/`

- `index.html`：瀑布、自耗时间分布、编排层卡片、点 span 看属性和子节点
- `spans.json`：92 条 span
- `status.json`、`result.json`
- `run_one.py`：重跑脚本
- `fixture/`：隔离夹具，skill 日志在 `fixture/logs/agents/skill_edit_agents/skills/bench-traj-oc-xskill-debug-ses-1da2.log`

页面：http://8.219.96.11:8873/

本机：`python3 -m http.server 8873 --bind 0.0.0.0`，cwd 必须是 `/tmp/otel-skilledit-viz`，pid 以当时为准（启动过一次是 1821371）。

SWAS 防火墙规则已经 50 条满了，不能再 `CreateFirewallRule`。8873 是复用已有规则 `openllmetry-vs-newapi-20260811`。不要新开端口。不要杀 8876、8877、8878 上别人的 http.server。若 8873 挂了，先确认该端口仍在 `ListFirewallRules` 里，再在同一端口重新 `serve_html.sh /tmp/otel-skilledit-viz 8873`。

放行脚本：`~/.agents/skills/display-html-aliyun/SKILL.md`

## 重跑一条（用户点头后再打官方 API）

```bash
cd /tmp/otel-skilledit-viz
/home/admin/xskill/.venv/bin/python /tmp/otel-skilledit-viz/run_one.py
```

改轨迹或提示词：编辑 `run_one.py` 里的 `CASE_TRAJ`、`PROMPT`。合法 traj 只从 `scripts/bench/real/skilledit_manifest.json` 取。帽子仍走 bench 公共件：80 轮或 20 分钟。

只搭夹具不打模型：

```bash
cd /home/admin/xskill
.venv/bin/python scripts/bench/skilledit_compact_replay.py --prepare-only
```

不要自己开：

```bash
.venv/bin/python scripts/bench/skilledit_compact_replay.py --all
```

## 仓库里相关文件（只读，除非用户要产品化）

- 任务书：`docs/plans/2026-08-20-skilledit-compact-bench.md`
- 回放：`scripts/bench/skilledit_compact_replay.py`
- 公共件：`scripts/bench/skilledit_lib.py`（压缩窗、aikey、夹具、计分）
- 提示词：`scripts/bench/skilledit_prompts/v1_pre_v7.txt`、`v2_current.txt`、`v3_converge.txt`
- 清单：`scripts/bench/real/skilledit_manifest.json`
- 工厂：`src/xskill/agents/agno_factory.py`（已 `telemetry=False`，不要再打开 Agno 上报）
- 人读 trace：`src/xskill/agents/agent_trace.py`（按 traj 或 skill 落日志，不是 OTel span）
- 压缩：`src/xskill/agents/context_budget.py`

本轮没有改上述仓库文件，没有 git commit。

## 另外 7 条原材料（还没用 OTel 跑）

大：`traj_oc_xskill-debug_ses_1da2`（已跑）、`traj_oc_traj2skill_ses_1d64`、`traj_oc_traj2skill_ses_1d96`、`traj_oc_leaderboard_ses_10c9`（atom 正文约 158642 字，更可能打满 compact，也更贵更慢）

小：`traj_oc_admin_ses_1d6a`、`traj_oc_admin_ses_0e2e`、`traj_oc_admin_ses_1d4b`、`traj_oc_traj2skill_ses_1da3`

三版提示词：v1 白名单四段，v2 当前 HEAD，v3 = v2 加收敛四条。

## 下一个 agent 可以接着做的（要用户点头）

用户还没指定下一件执行。交接时可能被要求做其中一件：

- 保持只用 OTel，再挑一条（例如小轨迹，或 leaderboard 那条大的）对照时间分布
- 把编排层、自耗、点选细节再补一版（页面已有第一版）
- 把临时埋点收进仓库，成为可选的 bench 开关。未点头不要改产品默认
- 继续 08-20 的 30 次回放格子。那是另一件事，先问，且会烧官方额度
- 用现有 `spans.json` 做工具转移、阶段耗时统计，不必再打模型

不要做的：

- 为了「更完整」引入 Phoenix、Langfuse、OpenLIT、ClickHouse、Grafana
- 把 `/tmp/otel-skilledit-viz` 里的夹具或日志当成现网证据
- 打印或提交 `~/.aikey`
- 新开 SWAS 防火墙端口（已满）
- 静默开全量 30 次

## 给用户回报时怎么说

先给页面地址 http://8.219.96.11:8873/ 。说明只用了 OTel、跑的是哪条、commit 和 compact 数字。编排要讲清楚是外套不是耗时主体。现网结论一句都不要下。
