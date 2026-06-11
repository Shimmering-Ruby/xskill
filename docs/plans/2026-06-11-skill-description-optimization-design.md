# SkillEdit 质量闸 + description 触发优化 设计文档

- 状态：草案，待 review
- 目标：把 Anthropic skill-creator 的"description 是唯一触发机制 + 触发准确率优化 + test 集选优防过拟合"那套，落进 xskill 的 skill 产出链路。
- 参考：skill-creator（`improve_description.py` 优化器 prompt、SKILL.md 内嵌的 case 生成指令、`run_loop.py`/`run_eval.py` 爬山环）。

---

## 0. 一句话回答："一条 desc 怎样算通过？这是进化还是筛选？"

**不是过/不过的二元判定，而是"在 held-out test 集上触发准确率最高者胜出"——先进化（improve_description 迭代生成 ≤5 个候选 desc）再筛选（test 集选最高分那个）。进化产生候选、test 集做最终选择，二者都要。** 单条 case 的"通过"= 跑 N 次触发率 ≥ 0.5 且与 `should_trigger` 一致；一条 desc 的"分"= test 集里判对的 case 比例。

---

## 1. 改哪里：质量闸进 SkillEdit prompt；优化器**硬编码进 commit workflow**（不做 agent tool）

### 1.1 description 有两个作者、两个用途（核实结论）

| 阶段 | 谁写 | 用途 | 会被改吗 |
|---|---|---|---|
| baby 创建 | **ClusterAgent** `NewSkillFolder(name, desc)` | **路由**（cluster catalog 决定 atom 往哪归） | 会被 SkillEdit 覆盖 |
| baby→main / main→staging | **SkillEditAgent** 写整份 SKILL.md | **末端触发**（用户 coding agent 的 available_skills） | 每次 promotion 重写 |

→ **优化器只进 SkillEdit 链路**（末端触发 desc，每次 promotion 重写，不会被早期一锤定死）。ClusterAgent 的 baby desc 是会被覆盖的路由草稿，**不上 eval**（cluster 每 atom 触发太频繁太贵），最多把 `NewSkillFolder` 提示词写好点。

### 1.2 为什么是"hardcode 进 commit workflow"，不是 agent tool

关键论证（采纳用户 Q1）：**优化 loop 是确定性代码（test 选优由代码定、不交给弱模型），那 agent 调不调、看不看返回值就都没意义了。** 所以：

- **不做 agent tool**——避免"agent 不调用就没优化"的不可靠。
- **直接插进 `commit_baby_to_main` / `commit_to_staging`**（`skill_tools.py:774/808`，已能拿 `_ctx["llm_client"]`+`_ctx["config"]`）：agent 写完 SKILL.md 调 commit → commit 内部**先跑 description 优化、把 best desc 写回 frontmatter，再 git commit**。每次 promotion 必跑，零依赖 agent 主动性。
- agent 不感知优化过程（对它透明）；可观测性走文件/日志，不走 agent 返回值。

### 1.3 SkillEdit prompt 加"提交前质量闸"（用户那串反思项）

SkillEditAgent system prompt 增一段，写 SKILL.md 时必须自检、并把结论写进 commit message：

1. **价值自检**：这 skill 替用户解决了什么具体问题——精简流程 / 发现问题 / 解决问题 / 统计问题？写出"替用户发现/解决了 X"。
2. **渐进式披露**：description 只放"是什么+何时用"（触发信息），细节进正文/辅助文件；正文不放触发判据。
3. **无孤立脚本**：每个脚本/辅助文件必须被 SKILL.md 正文引用并说明用途——孤儿文件不合格。
4. **description 可触发**：祈使语气 + 聚焦用户意图 + 主动列触发场景(pushy 防 undertrigger) + 100–200 词 + <1024 字符。

（注：第 4 条的最终保证由 1.2 的硬编码优化器兜底；prompt 只是让 agent 先写个像样的初稿。）

---

## 2. 优化机制（确定性代码 loop，`skill/description_opt.py`）

`optimize_description(skill_dir, *, llm, config) -> dict`，在 commit 内部调，流程同 skill-creator `run_loop.py`：

1. **case 生成**：用 skill-creator 的 case 生成指令（reuse，最小改），让 `llm` 产 ~20 条 `{query, should_trigger, topic}`（8–10 正 / 8–10 负，负例重 near-miss）。**缓存到 `.description_optimization/cases.json`**，同一 skill 复用，不每次重生成。
2. **train/test split**：按 `should_trigger` 分层，60% train / 40% test，`seed=42`（同 skill-creator）。
3. **触发判定 = LLM-as-judge**（无 Claude Code 的替换件）：把候选 desc 连同**其它 skill 的 name+desc** 拼成伪 `available_skills` catalog，喂 query 问 `llm`"你会调哪个 skill（或都不调）"，跑 N 次（默认 3）算触发率。**这是真实触发的代理**，机制同构（Claude 末端也是看 catalog 的 name+desc 选）。
4. **进化（improve loop，≤5 轮）**：把 train 集的 FAILED-TO-TRIGGER / FALSE-TRIGGER + 历史所有尝试(含逐 case PASS/FAIL) + 完整 SKILL.md 喂 skill-creator 的 `improve_description` prompt（reuse，最小改），产新 desc；带 <1024 字符硬闸 + 超长重写兜底。
5. **筛选（test 选优）**：原始 desc + ≤5 个进化候选，**按 test 集分**选 `best_description`（**不看 train 分**，防过拟合——Fable 的核心）。
6. 把 best 写回 SKILL.md frontmatter，返回摘要。

### 成本 gating（必须，记取 rpm:8 的教训）

- 只在 **baby→main 首发** + **main→staging** 跑；不在每次 cluster 跑。
- `config.skill_opt` 可配：`enabled`(默认 true)、`runs_per_case`(3)、`max_iters`(5)、`n_cases`(20)、`max_llm_calls`(硬上限,到顶提前停)。
- cases 缓存复用，避免每次重生成。
- 走 daemon 既有 rate_limit 桶，不另开并发。

---

## 3. 可观测性（用户 Q2：每次 try 是啥 desc、指标如何、结果如何）

全程 archive 到 skill 目录下的 `.description_optimization/`：

```
~/.xskill/skill/{skill}/.description_optimization/
  cases.json                                  # 20 条生成的 eval case(缓存复用)
  {exp_id}_{ts}/                              # 每次优化一个实验目录
    summary.json                             # train/test 划分、每个候选 desc 的 train/test 分、选中谁、为什么
    attempts.jsonl                           # 每轮 try: {iter, description, train_score, test_score}
    {topic}/{job_name}.json                  # 单 case 原始触发数据(用户指定结构):
        # {should_trigger, did_trigger, query, topic, triggered_skill, runs:[...]}
```

同时往 `xskill.skill_edit_agent` logger 打 INFO 里程碑（每轮 try 的 train/test 分 + 最终选中），落 `xskill.skill_edit_agent.log`，`tail -f` 可看。

---

## 4. 提示词（用户 Q4：尽量用官方的，最小改动）

- **case 生成**：直接搬 skill-creator SKILL.md 里那段"Create 20 eval queries…"指令，仅把输出格式钉成 `[{"query","should_trigger","topic"}]`（加 topic 字段）。
- **improve_description**：直接搬 `improve_description.py` 原文，仅替换占位符（skill_name / current_description / scores_summary / skill_content）+ 把"appears in Claude's available_skills"措辞保留（我们 LLM-judge 也是同构语境）。
- 两个 prompt 原文存 `src/xskill/agents/prompts/skill_creator_*.txt`，代码 format 注入，**不改写作原则部分**。

---

## 5. E2E 测试（用户 Q3：确保功能正确）

`tests/test_description_opt_e2e.py`，用 fake LLM（`tests/_fake_llm_server.py` 同款 stub）：

1. **机制端到端**：造一个 skill（差 description）+ 一组 fake case（部分 should/should-not）→ stub LLM judge 按预设规则判触发 + stub improve 返回更好的 desc → 跑 `optimize_description` → 断言：① `.description_optimization/` 落了 cases.json + summary.json + 单 case json（结构对）；② best_description 写回 frontmatter；③ best 按 **test 分**选（构造 train/test 分歧的场景，验证选的是 test 高分那个而非 train）。
2. **<1024 字符闸**：stub 返回超长 desc → 断言触发重写兜底、最终 <1024。
3. **gating**：`enabled=false` → optimize 不跑、desc 不变。
4. **commit 集成**：`commit_baby_to_main` 内部确实调了 optimize（desc 在 commit 后被优化过）。
5. **防过拟合回归**：train 选 A、test 选 B 的构造场景 → 必须选 B。

---

## 6. 文件改动清单

- 新增 `src/xskill/skill/description_opt.py`：`optimize_description` + LLM-judge 触发判定 + train/test + 进化/筛选 + archive。
- 新增 `src/xskill/agents/prompts/skill_creator_case_gen.txt` / `skill_creator_improve_description.txt`（官方原文）。
- 改 `src/xskill/agents/skill_tools.py`：`commit_baby_to_main` / `commit_to_staging` 内部 commit 前调 `optimize_description`（gating 包住）。
- 改 `src/xskill/agents/skill_edit_agent.py`：system prompt 加 1.3 的质量闸自检段。
- 改 `src/xskill/config.py`：`skill_opt` 配置段。
- 测试：`tests/test_description_opt_e2e.py` + `description_opt` 单测。

---

## 7. Backlog（先不做）

- **真 coding-agent 触发评测**：用真实用户的 coding agent（claude -p 流式早停）代替 LLM-judge——更准但要复用 canary 拉起真 agent 的基础设施，单独立项。LLM-judge 是 v1 的代理。

---

## 决策摘要（待 review 拍板）

| # | 点 | 决策 |
|---|---|---|
| D1 | 优化器形态 | **硬编码进 commit workflow**，非 agent tool（loop 确定性、agent 看返回无意义） |
| D2 | 触发判定 | **LLM-as-judge**（伪 catalog + 问 LLM 选哪个），无 Claude Code |
| D3 | 选优 | **test 集分最高者胜**，代码定，不看 train 分（防过拟合） |
| D4 | 进化 vs 筛选 | **都要**：improve_description 迭代产候选(进化) + test 选优(筛选) |
| D5 | 放哪 | SkillEdit 链路（baby→main / main→staging）；ClusterAgent baby desc 不上 eval |
| D6 | 提示词 | 官方原文 + 占位符，不改写作原则 |
| D7 | 成本 | gating + cases 缓存 + max_llm_calls 硬上限,走既有 rate_limit |
| D8 | 可观测 | 全程 archive 到 `.description_optimization/` + skill_edit logger |
