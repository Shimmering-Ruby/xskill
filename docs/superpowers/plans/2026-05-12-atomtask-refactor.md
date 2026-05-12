# AtomTask 重构实施 Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 traj2skill 的处理粒度从 trajectory（session-level meta 抽取）下沉到 AtomTask（多轮 chat-turn 的最小用户意图单元）。摘要、检索、灰度打分、SKILL 生成全部围绕 AtomTask 重组；旧 traj-level meta + index.pkl 链路整段删除，不留兼容。

**Architecture:**
- 数据层：`AtomTask` dataclass + `AtomTaskStore`（落盘 `<traj-root>/<traj_id>/tasks/atom_*.json` + 向量索引）+ `HybridSearch`（向量 ⊕ BM25 union/dedup）。
- Agent 层：`TaskAgent`（增量拆分 trajectory → AtomTasks，自报 used_skills + ux_score）→ `TaskClusterAgent`（把 atom 归类到现有/新 skill，给 weightscore 0-10）→ `SkillEditAgent`（buffer 累计 weightscore_total ≥ 10 立即触发）。
- 灰度层：`AtomCanary` 以 atom_id 替换 traj_id 作为 `.ux_scores.jsonl` 主键；`canary` 模块的 git 分支 / 判定逻辑（grain-agnostic）原样复用。
- 流水线：`watcher.py` 状态机 `discovered → splitting → split_done → indexed → clustering → done`；`process_atom_task()` 取代 `process_traj()`。
- E2E 验证：**重写** 现有 `tests/test_e2e_xskill_serve_auto.py` 让它跑通新流水线，不再走旧 traj-meta 链路。

**最终验收门槛（整体重构唯一硬 gate）：** Task 1-6 全部完成后必须跑通重写后的 `tests/test_e2e_xskill_serve_auto.py`（subprocess `xskill serve` + HTTP fake-LLM + fake `claude -p` jsonl 的全流程）。任何 task 内部的 unit / 集成测试只是局部 gate；只有这个 E2E 走绿才算整体重构完成。subagent 不准跳过、不准用 `pytest -k` 排除掉它。

按 CLAUDE.md：
- 不做配置/数据兼容（第 4 条）—— 旧 `traj_*.md.meta` 和顶层 `index.pkl` 用一次性脚本 nuke。
- 不写 fallback（第 1 条）—— LLM 输出不合法 / offset 不单调 / 工具失败直接 raise，由 watcher 状态机重试。
- OOP（第 3 条）—— 六个核心组件都是 class，agno agent 工厂以参数注入便于 stub。
- E2E 先写方案再实施（第 2 条）—— Task 6 顶部明列方案。

**Tech Stack:**
- Python 3.11+，agno Agent SDK（DeepSeek 直连走 `agno.models.deepseek.DeepSeek` 子类避免 reasoning_content 丢失）
- 向量：现有 ARK multimodal embedding；关键字：`rank_bm25`
- 测试：pytest + 现有 `tests/_fake_llm_server.py` + `tests/conftest.py` 的 src/ 路径注入

---

## File Structure

**新增：**
- `src/xskill/atom_task.py` — `AtomTask` + `AtomTaskStore`（含向量索引方法）
- `src/xskill/hybrid_search.py` — `HybridSearch`
- `src/xskill/task_agent.py` — `TaskAgent`
- `src/xskill/task_cluster_agent.py` — `TaskClusterAgent` + `build_skill_catalog_block`
- `src/xskill/skill_edit_agent.py` — `SkillEditAgent`
- `src/xskill/atom_canary.py` — `AtomCanary`
- `scripts/migrate_to_atomtask.py` — nuke 旧 meta + index
- `tests/test_atom_task_store.py` / `test_hybrid_search.py` / `test_task_agent.py` / `test_candidates_atom.py` / `test_task_cluster_agent.py` / `test_skill_edit_agent.py` / `test_skill_tools_atom.py` / `test_process_atom.py` / `test_watcher_atom.py` / `test_ux_score_atom.py` / `test_atom_canary.py` / `test_migrate_to_atomtask.py`

**修改：**
- `src/xskill/skill_tools.py` — 新增 `init_context_v2` + atom-era 工具集（atom_task_read/search、read_traj、new_skill_folder、skill_read、add_task_to_skill、score_task、add_task）
- `src/xskill/candidates.py` — 新增 `add_atom_contribution` + `ready_for_promotion_v2` + `ATOM_PROMOTION_THRESHOLD=10`
- `src/xskill/process.py` — 新增 `process_atom_task()`
- `src/xskill/watcher.py` — 状态机改造、`_do_meta/_do_embed/_do_process/_on_*` 切到 atom 阶段
- `src/xskill/registry.py` — `trajectories` 表加 `tasks_extracted INTEGER, last_offset INTEGER, last_atom_id TEXT`
- `src/xskill/ux_score.py` — 新增 `score_atom()`
- `src/xskill/search.py` — 新增 `search_atoms_all()`
- `tests/test_e2e_xskill_serve_auto.py` — **重写**让它跑 AtomTask 流水线
- `tests/test_watcher.py` — 跟新状态机对齐

**删除（不留兼容）：**
- `src/xskill/index.py` 中的 `EXTRACT_PROMPT` / `extract_meta_llm` / `parse_meta` / `validate_meta` / `_process_one_meta` / `build_vector_index_incremental` / `index_dataset` / CLI `main()` —— 整个文件清空后只保留 `AtomTaskStore.rebuild_vector_index` 引用所需的 helper（或干脆删掉 index.py 整个文件，把残留 helper 搬走）。
- `src/xskill/agent.py` 中的 `SYSTEM_PROMPT` 和 `run_agent_agno` / `run_agent` —— 由 `TaskClusterAgent` 取代。

---

## Self-Review 前置说明

六个 task 之间是**严格顺序依赖**（每个 task 顶部声明 Depends）。Task 内步骤是逻辑章节而非分钟级动作；推荐按以下节奏：

1. 看本 task 的"Acceptance"——这是合并标准。
2. 看"Implementation outline"——每个子点都附实际代码或精确变更点；按子点开 commit 子节点（可在 task 内部多 commit，但任务整体作为一个 PR/分支段落 review）。
3. 跑"Test gates"列出的 pytest 命令；全部绿后再 merge。

Task 之间换 task 之前必须 `pytest tests/ -x -q` 全绿。

---

## Task 1 — AtomTask 数据层（model + store + 向量索引 + HybridSearch）

**Depends:** —

**Acceptance:**
1. `AtomTask` dataclass 存得下 atom_id / traj_id / offset_start/end / intent / summary / tags / used_skills / ux_score / pre_atom_id / post_atom_id / context_prefix / raw_segment 全部字段，JSON 来回 roundtrip 无损。
2. `AtomTaskStore` 把 atom 落在 `<root>/<traj_id>/tasks/atom_*.json`，`list_by_traj` 排序稳定，`last_offset` 返回该 traj 当前最大 offset_end，`all_atoms()` 跨 traj 迭代。
3. `AtomTaskStore.rebuild_vector_index(embed)` 把所有 atom 的 `summary or intent` 嵌入并 L2 归一化存到 `<root>/index.pkl`；`vector_search(query, embed, top_k)` 返回 `{atom_id, similarity}` 列表。
4. `HybridSearch(store, embed).search(query, top_k)` 同时跑向量 top-k 和 BM25 top-k，用 atom_id 做 union+dedup，每条结果带 `sources: ["vector"|"keyword"]` 标记，不做 rerank。
5. 测试覆盖：roundtrip / list 排序 / offset / 索引 build & query / hybrid union+dedup + sources 字段。

**Implementation outline:**

- 新建 `src/xskill/atom_task.py`：
  ```python
  @dataclass
  class AtomTask:
      atom_id: str
      traj_id: str
      offset_start: int
      offset_end: int
      intent: str
      summary: str
      tags: list[str] = field(default_factory=list)
      used_skills: list[str] = field(default_factory=list)
      ux_score: int | None = None
      pre_atom_id: str | None = None
      post_atom_id: str | None = None
      context_prefix: str = ""
      raw_segment: str = ""
  ```
  落盘路径 `<root>/<traj_id>/tasks/<atom_id>.json`。`AtomTaskStore` 暴露：`save(atom)` / `load(atom_id)`（跨 traj_id 子目录 lookup）/ `list_by_traj(traj_id)` / `all_atoms()` 生成器 / `last_offset(traj_id)` / `last_atom_id(traj_id)` / `rebuild_vector_index(embed)` / `vector_search(query, embed, top_k=5)`。

- `rebuild_vector_index` 行为：把 `[a.summary or a.intent for a in all_atoms()]` 整批 encode_batch → L2 归一 → `index.pkl` 存 `{atom_ids, embeddings, model, dim}`。

- 新建 `src/xskill/hybrid_search.py`：
  ```python
  @dataclass
  class HybridSearch:
      store: AtomTaskStore
      embed_client: object
      def search(self, query, top_k=5) -> list[dict]: ...
      def _keyword_search(self, query, top_k) -> list[dict]: ...
  ```
  关键字分词用 `re.compile(r"[\w]+", re.UNICODE)`（中英混合 token），效果不行后续再上 jieba。结果格式：`{"atom_id": str, "sources": ["vector"|"keyword"], "vector_similarity"?: float, "bm25_score"?: float}`。

- `pyproject.toml` 加 `rank-bm25` 依赖。

- 测试文件 `tests/test_atom_task_store.py` 和 `tests/test_hybrid_search.py`。提供一个 `_FakeEmbed` 类（hash → 8 维向量），把它放在 `tests/test_atom_task_store.py` 顶部并允许其他测试文件 `from tests.test_atom_task_store import _FakeEmbed`。覆盖：
  - `AtomTask.from_json(a.to_json()) == a`
  - `store.list_by_traj(...)` 返回按 `atom_id` 排序
  - `store.last_offset(...) == 0` 当无 atom
  - `store.last_offset(...) == max(offset_end)` 当有 atom
  - 文件落在 `<root>/<traj_id>/tasks/<atom_id>.json`
  - `rebuild_vector_index` + `vector_search` 命中精确 match
  - `HybridSearch` 至少返回 union 关键字命中的所有项 + dedup + `sources` 字段

**Test gates:**
- `pytest tests/test_atom_task_store.py tests/test_hybrid_search.py -v`
- `pytest tests/ -x -q`（原有测试全绿）

**Commit shape:**
- `feat(atom): AtomTask dataclass + AtomTaskStore 落盘`
- `feat(atom): AtomTaskStore 向量索引（summary 嵌入）`
- `feat(search): HybridSearch 向量+BM25 union/dedup`

---

## Task 2 — TaskAgent：增量拆分 trajectory → AtomTasks

**Depends:** Task 1

**Acceptance:**
1. `TaskAgent(llm, store, max_context_chars=30000).run(traj_id, traj_path)` 拿一条 `traj.md`，让 LLM 拆成 1~N 个 AtomTask，落盘 + 写好 pre/post 链接。
2. **增量**：当 `store.last_offset(traj_id) > 0`，prompt 里附"上一段 AtomTask 摘要"作为衔接，原文用 `context_prefix`（头 200 字 + `[省略 X 字符]` 占位）+ 从 `last_offset` 起的 delta；不发整篇 traj。
3. 切分边界 cut signal 在 SYSTEM_PROMPT 里**只**写"用户意图切换"，可适当提及 Skill 工具调用作为软提示，**不作为硬性切分点**。
4. SYSTEM_PROMPT 含**严格 ux_score 分档表**（10 一次到位 / 8 绕一弯 / 5 部分完成 / 2 未完成 / 1 副作用，且 `used_skills` 非空时降档/起步规则明列）；此分数当作"TaskAgent 阶段自评 ux_score"，后续灰度打分还会再来一次（Task 5）。
5. XML 解析校验：offset 必须单调递增（`offset_start ≥ prev_offset_end`，`offset_end > offset_start`），任一字段缺失/非法直接 `raise ValueError`，**不写 fallback**。
6. 测试用 stub LLM 覆盖：首轮 → 两个 atom + 链接正确；二轮（追加 traj 内容）→ 只拆 delta，prompt 含"上一段摘要"和"省略"占位符，落盘后 store 中 atom 总数 = 旧 + 新。

**Implementation outline:**

- SYSTEM_PROMPT 关键内容（中文）：

  > 你是 AtomTask 拆分员。给你一段 agent 与用户的对话轨迹（markdown），按"用户意图切换"切成 1~N 个 AtomTask……
  >
  > 切分原则（按优先级）：
  > 1. 只在**用户意图切换**处切（"另外/接下来/对了" + 新动作 = 切；澄清/修正/加细节 = 不切）。
  > 2. **不要**因为 tool 切换、代码块出现、子目录变化而切。
  > 3. 一段轨迹只完成一件事 → 输出 1 个 atom。不要硬凑数量。
  > 4. 可适当关注 Skill 工具完整调用过程（"## Tool Call: Skill"），把整次 skill 调用包含在所属 atom 里，但不作为硬切分点。
  >
  > 字段输出（XML）：offset_start / offset_end / intent / summary / tags / used_skills / ux_score。
  >
  > **ux_score 严格分档表**：
  >   10 一次到位：用户提需求 → agent 一步给出正确产出 → 接受无澄清。
  >    9 接近一次到位：仅一处细节澄清。
  >    8 正确完成但绕了 1 个小弯。
  >    7 正确完成，2-3 次澄清/修正，无明显不耐烦。
  >    6 完成度边界（"这就行吧"）。
  >    5 部分完成：核心需求达成但遗漏明显细节。
  >    4 多次错误后才接近正确，用户≥2 次否定词。
  >    3 任务勉强完成但用户明显失望。
  >    2 任务未完成 / 反复 blocker / 用户放弃。
  >    1 完全失败或副作用（删错文件、改坏代码、误推送）。
  >
  > `used_skills` 非空时同时看：调了 skill 一步到位 → 起步 8；调了 skill 但绕弯/错误 → ≤5。
  >
  > 永远质量驱动，不要按"做了多少事/步数"打分。
  >
  > 输出严格 XML：`<atoms><atom><offset_start>…</offset_start>…</atom></atoms>`，标签外不得有任何文字。

- 解析层用四个正则：`<atom>...</atom>` 切块，每块内 `<offset_start>` / `<offset_end>` / `<intent>` / `<summary>` / `<ux_score>` + `<tag>` 列表 + `<skill>` 列表。任何一项缺失或 offset 非单调直接抛。

- 链接逻辑：先按返回顺序生成新 atom 列表 → 内部相邻 atom 互填 pre/post → 再把第一个 atom 的 `pre_atom_id` 指向 `store.last_atom_id(traj_id)`，并把那个 prior_atom 的 `post_atom_id` 改写回 store。

- `context_prefix(text, offset)`：`offset ≤ 200` 直接给 `text[:offset]`；否则 `text[:200] + f"\n\n[省略 {offset - 200} 字符]\n\n"`。

**Test gates:**
- `pytest tests/test_task_agent.py -v`
- `pytest tests/ -x -q`

**Commit shape:**
- `feat(task-agent): 按用户意图切换增量拆分 AtomTask`

---

## Task 3 — Candidates v2 + Cluster/Edit Agent + atom-era 工具集

**Depends:** Tasks 1, 2

**Acceptance:**
1. `candidates.py` 新增 `ATOM_PROMOTION_THRESHOLD = 10`、`add_atom_contribution(data, atom_id, weightscore, note="")`、`ready_for_promotion_v2(data, threshold=10)`。schema：每条 candidate = `{atom_id, weightscore_total, contributions:[{score,ts,note}], promoted, promoted_at}`。
2. `skill_tools.py` 新增 `init_context_v2(skill_dir, store, embed_client, traj_root)` 和八个工具：`atom_task_read(atom_id)`、`atom_task_search(query, top_k=5)`、`read_traj(traj_id, offset_start, offset_end)`、`new_skill_folder(skill_name)`、`skill_read(skill_name)`、`add_task_to_skill(skill_name, atom_id, weightscore)`、`score_task(atom_id, score)`、`add_task(atom_id, ...)`。
3. `task_cluster_agent.py` 暴露 `build_skill_catalog_block(skill_dir, max_chars)` 和 `TaskClusterAgent` 类。catalog 行为：name 不限；剩余预算 / 条数 ≥ 75 字符（≈25 token）→ desc 截到 min(per_desc, 300)；不足 75 → 全部丢 desc 只留 `- <name>`。SYSTEM_PROMPT 含**严格 weightscore 分档表**：10 立即触发；8-9 高质量；6-7 中等；4-5 弱（写入 candidates 但不期望单独触发）；2-3 不要写；1 不写。
4. `skill_edit_agent.py` 暴露 `SkillEditAgent` 类，`maybe_run()` 检查单个 skill_dir 的 candidates，buffer 累计 `weightscore_total` ≥ 阈值时启动 agno agent（含 `atom_task_read / read_traj / skill_read / write_file` 四工具）写或更新 SKILL.md，结束后把所有 pending candidates 标 promoted。
5. 删除 `src/xskill/agent.py` 旧的 `SYSTEM_PROMPT / run_agent_agno / run_agent`（整个文件保留空 helper 或直接删，import 的地方一并清掉）。
6. 测试：
   - `test_candidates_atom.py`：单 atom 累加分到 ≥10 → ready；多 atom 累计 ≥10 → ready；buffer < 10 → empty。
   - `test_skill_tools_atom.py`：八个工具的 happy path + 边界（read_traj 越界 → error，atom_task_read 找不到 → error，add_task_to_skill 在不存在 skill → error，weightscore 1-10 外 → error）。
   - `test_task_cluster_agent.py`：`build_skill_catalog_block` 三种 budget 场景（短列表全 desc / 中等截断 desc / 极端只留 name）；`TaskClusterAgent.process(atom)` 用 stub agno agent 验证 user_msg 含 atom_id + skill 路由表。
   - `test_skill_edit_agent.py`：buffer < 10 → 不触发；≥ 10 → 触发并把所有 pending 标 promoted。

**Implementation outline:**

- candidates.py 改动：在 `ATOM_PROMOTION_THRESHOLD = 10` 常量下面加两个独立函数（不要碰旧的 `add_or_merge` / `ready_for_promotion`，让旧 unit test 继续过；新 pipeline 只用 v2 接口）。

- skill_tools.py 改动：保留旧 `_ctx` 和旧工具（暂不删，watcher 切换在 Task 4），新增 `_ctx_v2` 和八个新工具。工具签名严格按 acceptance 第 2 条；返回都是字符串（agno 工具默认契约）。`read_traj` 校验：`offset_end > offset_start`、`offset_start ≥ 0`、`offset_end ≤ len(file)`，违反返回 `"error: ..."`。`add_task_to_skill` 返回末尾追加 ` weightscore_total=<N>`，方便 agent 判进度。

- task_cluster_agent.py `build_skill_catalog_block` 算法：
  ```
  names_descs = [(name, desc) for each skill dir under skill_dir]
  name_cost = sum(len("- "+n)+1 for n,_ in names_descs)
  remaining = max_chars - name_cost
  per_desc = remaining // len(names_descs)
  if per_desc < 75:  # _DESC_MIN_CHARS
      return "\n".join(f"- {n}" for n,_ in names_descs)
  cap = min(per_desc, 300)
  return "\n".join(f"- {n}: {desc[:cap]+('…' if len(desc)>cap else '')}" if desc else f"- {n}"
                   for n,desc in names_descs)
  ```

- TaskClusterAgent SYSTEM_PROMPT 关键内容：

  > 你是 TaskClusterAgent。我给你一个 AtomTask；你决定它是否值得被某 skill 收录，归到哪个已有 skill（用 `add_task_to_skill`），或新建一个 skill（`new_skill_folder` + `add_task_to_skill`）。
  >
  > 可用工具：AtomTaskRead / AtomTaskSearch / ReadTraj / SkillRead / NewSkillFolder / add_task_to_skill。
  >
  > 当前可见 skill 路由表（name: description）：
  > {skill_catalog}
  >
  > **weightscore 严格分档表**（永远质量驱动，不要凑条数）：
  >   10 这一个 atom 就足以单独或强烈支撑该 skill 的核心场景（罕见；含可机械执行的、跨多类相似问题都成立的修复决策）。给 10 立即触发 SkillEdit。
  >    8-9 高质量贡献：完整覆盖某 skill 关键阶段，具体命令/路径/函数名 + 可核验的产出 + 用户成功反馈。两个 8 分相加即触发 SkillEdit。
  >    6-7 中等贡献：只覆盖一个子阶段或某 warning；需别的 atom 补齐才有意义。
  >    4-5 弱贡献：相关问题但执行细节模糊；进 candidates 但不期望单独触发。
  >    2-3 边缘相关：不要写。
  >    1 完全不相关：不要写。
  >
  > 新建 skill 的硬门槛：单 atom weightscore < 7 不要新建（会污染 skill 列表）。
  >
  > 硬禁止：低质 atom 别加；不要伪造 atom_id；不要直接写 SKILL.md（那是 SkillEditAgent 职责）。

- SkillEditAgent SYSTEM_PROMPT 关键内容（写 SKILL.md 的硬规则与旧 agent.py 一致并保留）：

  > 你是 SkillEditAgent。一个 skill 的 candidates buffer 已积累若干 atom 贡献（累计 weightscore ≥ 10），触发一次 SKILL.md 整理。
  >
  > 输入是 atom_id 列表（按 weightscore_total 倒序）+ 它们的 contributions 历史。
  >
  > 流程：用 AtomTaskRead 读 atom；必要时用 ReadTraj(traj_id, offset_start, offset_end) 看原文；按 v2 schema 写或更新 SKILL.md（frontmatter + `## <阶段名>` body + `> ⚠️` warning 紧贴对应 step + 中文）。
  >
  > 唯一可信来源是 AtomTaskRead；不要发明命令/函数。SKILL.md ≤ 400 行；长参考材料放 references/，脚本放 scripts/。

- 删除 `src/xskill/agent.py` 中的内容（让 `run_agent` 不再被 import；watcher 在 Task 4 会改）。

**Test gates:**
- `pytest tests/test_candidates_atom.py tests/test_skill_tools_atom.py tests/test_task_cluster_agent.py tests/test_skill_edit_agent.py -v`
- `pytest tests/ -x -q`

**Commit shape:**
- `feat(candidates): atom_id + weightscore 累计的 v2 schema`
- `feat(tools): atom-era 工具集（init_context_v2 + 8 个工具）`
- `feat(cluster): TaskClusterAgent + 20% budget skill 路由表`
- `feat(edit): SkillEditAgent + weightscore≥10 触发`
- `refactor: 删除旧 SkillAgent (agent.py)`

---

## Task 4 — process / watcher / registry 流水线切换

**Depends:** Tasks 1, 2, 3

**Acceptance:**
1. `process.py` 新增 `process_atom_task(*, atom_id, config, skill_dir, store, embed_client, agno_agent_factory) -> dict`：调 `init_context_v2`，跑 `TaskClusterAgent.process(atom)`，再遍历每个 skill 子目录调 `SkillEditAgent.maybe_run()`，返回 `{action, atom_id, edited_skills, cluster_log}`。**删除**旧的 `process_traj()`（不留兼容）。
2. `watcher.py` 状态机改造：旧 `meta_extracting / meta_done / processing` 改成 `splitting / split_done / clustering`；`_do_meta → _do_split`（调 `TaskAgent.run`），`_do_embed → _do_atom_index`（调 `store.rebuild_vector_index`），`_do_process → _do_cluster`（对该 traj 新生成的每个 atom 调 `process_atom_task`）；`_on_*` 回调字段同步更新。`DirectoryWatcher.__init__` 新增 `agno_agent_factory` 和 `store_root` 两个参数。冷启动 gate 门槛从"未 indexed 的 traj 数"改成"未 indexed 的 atom 数"——因为索引对象现在是 atom。
3. `registry.py`：`trajectories` 表加三列 `tasks_extracted INTEGER DEFAULT 0, last_offset INTEGER DEFAULT 0, last_atom_id TEXT`；`_migrate` 函数把这三列加进现有 DB（ALTER TABLE）。新增 helper：`update_traj_offset(wd_id, fname, last_offset, last_atom_id, tasks_extracted)`。
4. 旧 `tests/test_watcher.py` 跟新状态名对齐（重写需要改的 case），其他原有测试不变。
5. 新增 `tests/test_process_atom.py`：stub agno agent + stub store + 验证 cluster 调过 / `SkillEditAgent.maybe_run` 调过。
6. 新增 `tests/test_watcher_atom.py`：用 `tests/test_task_agent.py` 中的 `_SPLIT_XML` stub 跑一遍 watcher `_scan_once + harvest` 循环，断言 atom 落盘 + 状态进 done。

**Implementation outline:**

- `process_atom_task` 主体（去 fallback、去保护性 try/except）：
  ```python
  atom = store.load(atom_id)
  ST.init_context_v2(skill_dir=skill_dir, store=store,
                     embed_client=embed_client, traj_root=store.root)
  cluster = TaskClusterAgent(skill_dir=skill_dir, store=store,
      agno_agent_factory=agno_agent_factory, llm_cfg=config.get("llm",{}),
      tools=[ST.atom_task_read, ST.atom_task_search, ST.read_traj,
             ST.skill_read, ST.new_skill_folder, ST.add_task_to_skill,
             ST.score_task])
  cluster_content = cluster.process(atom)
  edited = []
  for d in sorted(skill_dir.iterdir()):
      if not d.is_dir() or d.name.startswith("."): continue
      editor = SkillEditAgent(skill_dir=d, store=store,
          agno_agent_factory=agno_agent_factory,
          llm_cfg=config.get("llm",{}), traj_root=store.root)
      if editor.maybe_run(): edited.append(d.name)
  return {"action":"clustered","atom_id":atom_id,
          "edited_skills":edited,"cluster_log":cluster_content[:500]}
  ```

- watcher 改造要点：保留 `_loop` / `_harvest` / `_scan_dir` 大骨架；把 `_do_meta` 的内部换成 `TaskAgent(llm=self.llm, store=self.store).run(traj_id, traj_path)`；`_do_embed` 换成 `self.store.rebuild_vector_index(self.embed_client)`；`_do_process` 接收 traj_id 但内部 `for atom in store.list_by_traj(traj_id)` 逐个 `process_atom_task(...)`。`_ACTION_STATUS` 改成：
  ```python
  _ACTION_STATUS_V2 = {"clustered":"done","skip":"indexed","error":"error"}
  ```
  `cold_start_threshold` 含义改：现在 pending 算未 indexed 的 atom 数（== `len(get_trajs_by_status(wd, "split_done"))` —— 因为 atom 索引以 traj 为批次重建，traj 处于 split_done 时它的 atom 都还没 embed）。

- `_score_new` 改成扫每个新 atom（不是新 traj），仍按 `xskill:` header 决定是否打分；这块是 Task 5 的边界——本 task 暂时让 `_score_new` 在 traj 路径上空跑（pass）保留 hook。

**Test gates:**
- `pytest tests/test_process_atom.py tests/test_watcher_atom.py tests/test_watcher.py -v`
- `pytest tests/ -x -q`

**Commit shape:**
- `feat(registry): tasks_extracted / last_offset / last_atom_id 列`
- `feat(process): process_atom_task 取代 process_traj`
- `feat(watcher): 流水线切到 splitting / split_done / clustering`

---

## Task 5 — ux_score 按 atom + AtomCanary 主键切换

**Depends:** Tasks 1, 4

**Acceptance:**
1. `ux_score.py` 新增 `score_atom(llm, *, atom, side) -> {"score": int|None, "reasons": str}`。SYSTEM_PROMPT 复用 Task 2 的严格分档表（10/9/8…/1），prompt body 把 `atom.context_prefix + atom.raw_segment` 截断送 LLM，并显式列 `used_skills`、`side`、`intent`、`summary`。**保留**旧 `score_trajectory` 以让旧 E2E 测试在 Task 6 重写前还能跑（Task 6 完成后再删）。
2. `atom_canary.py` 暴露 `AtomCanary` 类：`append(*, atom_id, skill_name, side, commit_sha, score, reasons) -> bool`（幂等，按 `(atom_id, skill_name, side)` 三元组去重）、`recent(*, side, commit_sha, n)`、`check_and_decide(*, config) -> dict`（直接代理 `canary.check_and_decide`）。底层文件 `.ux_scores.jsonl` 主键字段从 `traj_id` 换成 `atom_id`，其他字段（skill_name / side / commit_sha / score / reasons / scored_at）保留。
3. watcher 的 `_score_new` 改成"对该 traj 的每个 atom 检查 `xskill:` header → 调 `score_atom` → `AtomCanary.append`"；遍历的是 `store.list_by_traj(traj_id)` 而不是 traj 文件本身。`mark_skill_used` 仍按 traj 粒度记（registry 表里没有 atom 行），但 canary 记录 atom 级。
4. 测试：
   - `test_ux_score_atom.py`：stub LLM 返回 `{"score":9,"reasons":"一步到位"}` → 解析正确；返回越界（99）→ `score=None`；返回非 JSON → `score=None`。
   - `test_atom_canary.py`：append 幂等 + recent 按 commit_sha 过滤 + check_and_decide 在两侧各 5 条 + staging 均分 ≥ main 时返回 `promoted`。

**Implementation outline:**

- `score_atom` 主体：
  ```python
  body = _truncate((atom.context_prefix or "") + "\n\n" + (atom.raw_segment or ""))
  prompt = (f"side={side}\nused_skills={atom.used_skills}\n"
            f"intent={atom.intent}\nsummary={atom.summary}\n\n"
            f"# 对话片段\n{body}\n\n请按系统指令打分。")
  raw = llm.chat(prompt, system=SYSTEM_PROMPT_ATOM)
  data = _parse_score(raw)
  ...
  ```

- AtomCanary 主体：直接复用 `canary.UX_SCORES_FILENAME` 和 `canary.load_ux_scores`，只在 append 时主键字段写 `atom_id`，recent 时按 `side + commit_sha` 过滤（与原 `recent_scores` 一致）。

- watcher `_score_new` 改造伪码：
  ```python
  for atom in store.list_by_traj(traj_id):
      header = parse_traj_header(text[atom.offset_start:atom.offset_end] or text[:500])
      # 实际仍用 traj.md 顶部的 header（一条 traj 的 header 全局生效，atom 共享）
      if not header or not header.get("skill"): continue
      result = score_atom(llm=self.llm, atom=atom, side=header["side"])
      if result["score"] is None: continue
      AtomCanary(skill_dir=self.skill_dir/header["skill"]).append(
          atom_id=atom.atom_id, skill_name=header["skill"], side=header["side"],
          commit_sha=header.get("sha",""), score=result["score"],
          reasons=result["reasons"],
      )
      AtomCanary(skill_dir=self.skill_dir/header["skill"]).check_and_decide(...)
  ```
  （header 仍在 traj 顶部，atom 共享）

**Test gates:**
- `pytest tests/test_ux_score_atom.py tests/test_atom_canary.py -v`
- `pytest tests/ -x -q`

**Commit shape:**
- `feat(score): atom 粒度 ux_score + used_skills 信号`
- `feat(canary): AtomCanary 把 ux_scores 主键换成 atom_id`

---

## Task 6 — 重写 E2E 测试 + 旧数据 nuke + 整体回归

**Depends:** Tasks 1-5

**E2E 测试方案（CLAUDE.md 第 2 条要求先写方案）：**

`tests/test_e2e_xskill_serve_auto.py` 现在验证旧 traj-level 流水线（traj.md 整篇喂 LLM 抽 meta → SkillAgent 走 path-A/B/C）。**完全重写**为验证新 AtomTask 流水线的同名文件，断言以下用户视角通路：

1. **自动 detect + bridge**：`xskill serve` 起来后扫到 `~/.claude/projects/`，自动 register `~/.xskill/cc_sessions/`；用户跑两次 `claude -p` 后，cc_sessions 下出现 `traj_cc_<project>_<sid8>.md`。
2. **AtomTask 拆分**：watcher 把每条 traj 推到 `splitting` → `split_done` → `indexed`。fake LLM 返回稳定 `<atoms>...</atoms>`；落盘后 `cc_sessions/<traj>/tasks/atom_*.json` 有 ≥1 个文件，且 `pre_atom_id/post_atom_id` 链表完整。
3. **AtomTask 索引**：`cc_sessions/index.pkl` 存在，`xskill search --query "..."` CLI 返回 atom_id 命中（这里改 search CLI 走 `search_atoms_all`）。
4. **Cluster → Edit 全链路**：fake LLM 模拟 cluster agent 给 `add_task_to_skill("X", atom_id, 10)`（单 atom 直接打满阈值）；watcher 跑完 cluster 步后 `~/.xskill/skill/X/SKILL.md` 被 SkillEditAgent 写过（mtime 比初始 bootstrap 新）。
5. **灰度翻牌 + atom 打分**：bootstrap `skill X` 含 main v1 + staging v2 → daemon 翻牌 → 用户跑 ≥10 次 `claude -p`，每条 traj 拆出至少 1 个 atom → watcher 用 fake ux-scorer 给 atom 打分 → `.ux_scores.jsonl` 里 record 全部带 `atom_id` 字段（不是 `traj_id`） → check_and_decide 返回 `promoted` → main 替换为 v2。
6. **下游验证**：再跑一次 `claude -p`，system prompt 看到 v2 内容。

Fake LLM responder 复用 `tests/_fake_llm_server.py`，加三种 responder：`atom_split_responder`（返回固定 `<atoms>` XML）、`cluster_responder`（返回 stub tool-use call `add_task_to_skill`）、`ux_score_responder`（返回 `{"score":N}`，N 按调用次数轮流 main=6 / staging=9 模拟 staging 胜出）。

**Acceptance:**
1. 重写后的 `tests/test_e2e_xskill_serve_auto.py` 完整运行通过，不依赖任何旧 traj-level 函数（grep 验证文件内不含 `process_traj` / `extract_meta_llm` / `run_agent` 字样）。
2. `scripts/migrate_to_atomtask.py` 提供 `nuke_legacy(root: Path) -> dict`：递归删除 `traj_*.md.meta` 和 `index.pkl`（顶层 + 子目录都扫），保留 `traj_*.md` 和 `traj_*.json`。CLI `python scripts/migrate_to_atomtask.py <root> [--dry-run]`。
3. `src/xskill/index.py` 中除 `AtomTaskStore` 不直接依赖的内容外全部删除（确认 `import xskill.index` 在 codebase 内已无引用——`grep -r "from xskill.index" src/`）。如果完全可删，删整个文件并把 `xskill/__init__.py` 里相关 export 清掉。
4. `src/xskill/ux_score.py` 中的旧 `score_trajectory` / `score_and_record`（traj 粒度）删除——E2E 不再走它，watcher 走 `score_atom`。
5. `src/xskill/agent.py` 整体删除或留空（确认无残留 import）。
6. CLAUDE.md 第 1 条复核：grep 全 codebase 不含 `try.*except.*pass`、不含 `fallback` 字样的兜底逻辑（合理 except 例如 JSON 解析仍保留，但不接 pass / 不 swallow）。
7. `pytest tests/ -v` 全绿。新流水线手动跑一遍 `xskill serve` + 真实 claude -p 在 8.219.96.11:3000 环境之外的本地测试 box 上跑，cc_sessions 下能看到 atom_*.json 生成。**禁止动 ~/work 下的生产代码**（按 user memory）。

**Implementation outline:**

- 旧 E2E 文件备份：`mv tests/test_e2e_xskill_serve_auto.py tests/test_e2e_xskill_serve_auto.py.old-traj`（git 历史里保留）→ 提交 → 然后重写新文件。new file 顶部 mock data 部分按新 schema 重新组织：

  ```python
  # ── A. fake LLM 响应集 ──
  ATOM_SPLIT_XML = "<atoms><atom>...</atom></atoms>"  # TaskAgent 拆分响应
  CLUSTER_TOOL_CALLS = [...]                          # cluster agent 的工具调用脚本
  UX_SCORE_RESPONSES = ["...","..."]                  # 按调用计数轮流返回的 score JSON

  # ── B. Skill v1 / v2 bootstrap（沿用旧文件的常量名） ──
  SKILL_V1_BODY = "..."
  SKILL_V2_BODY = "..."
  ```

  测试主体 `test_full_atomtask_pipeline_with_canary_flip` 跑：
  1. 起 fake LLM server（同 base_url 同时处理 split / cluster / score 三种 prompt）。
  2. 创建 fake `~/.claude/projects/<proj-hash>/<sid>.jsonl`（用现有 `_session_used_skill` 的 fixture 模式）。
  3. 启 `xskill serve`（subprocess）。
  4. `requests.get("/api/v1/registry/dirs")` 等到 cc_sessions 被自动 register。
  5. 用 fake claude jsonl 模拟用户跑 12 轮 prompt（同 SKILL.md v1/v2 测试一样）。
  6. 轮询 `cc_sessions/index.pkl` 出现 + `cc_sessions/<traj>/tasks/atom_*.json` ≥ 1。
  7. 断言 `.ux_scores.jsonl` 全 record 含 `atom_id` key 且 main+staging 各 ≥ 5 条。
  8. 断言 `skill/X/SKILL.md` 内容含 v2 标识（v2 已 promoted）。

- `scripts/migrate_to_atomtask.py` 主体：
  ```python
  def nuke_legacy(root: Path) -> dict:
      removed = {"meta":0, "index":0}
      for p in Path(root).rglob("traj_*.md.meta"): p.unlink(); removed["meta"] += 1
      for p in Path(root).rglob("index.pkl"):       p.unlink(); removed["index"] += 1
      return removed
  ```

  CLI 入口含 `--dry-run` 仅打印计数；正式跑后 watcher 启动会自动重建 atom 索引。

- index.py / agent.py / 旧 score 函数清理：
  - 跑 `grep -r "from xskill.index" src/ tests/` 找所有引用 → 改 import 或删除 → 最后删 `src/xskill/index.py`。
  - 跑 `grep -r "from xskill.agent" src/ tests/` 同上。
  - 跑 `grep -r "score_trajectory\|score_and_record" src/ tests/` 同上。
  - 跑 `grep -r "process_traj" src/ tests/` 确保 watcher 切干净。

- `tests/test_migrate_to_atomtask.py`：单测 `nuke_legacy` 删 meta + 删 index + 不删 .md。

**Test gates:**
- `python scripts/migrate_to_atomtask.py /tmp/fake-root --dry-run`（手验）
- `pytest tests/test_migrate_to_atomtask.py tests/test_e2e_xskill_serve_auto.py -v`
- `pytest tests/ -v`（全套绿）
- 本地（非 ~/work）跑 `xskill serve` + 一次 claude -p，verify cc_sessions 下出现 atom_*.json

**Commit shape:**
- `chore(migrate): nuke 旧 traj-level meta + index 脚本`
- `refactor: 删除 index.py / agent.py / score_trajectory 旧链路`
- `test(e2e): 重写 test_e2e_xskill_serve_auto 验 AtomTask 全链路`

---

## Self-Review

### 1. Spec coverage

| 用户原述要求 | 覆盖位置 |
|---|---|
| Session 粒度太大；引入 AtomTask 作最小提炼原子 | Task 1（数据层）+ Task 2（拆分） |
| AtomTask 落盘 `cc-sessions/{traj-name}/tasks/`，不用 sqlite | Task 1 acceptance #2 |
| 维护 offset 增量；只发 LastAtom + delta | Task 2 acceptance #2 + prompt 实现 |
| `add_task` / `score_task` 工具 | Task 3 acceptance #2 |
| TaskAgent 自报 used_skill + ux_score | Task 2 acceptance #4 + prompt |
| TaskClusterAgent + 全 skill_names sysprompt（20% budget） | Task 3 acceptance #3 + `build_skill_catalog_block` |
| `add_task_to_skill(skill, atom_id, weightscore)` | Task 3 acceptance #2 |
| 单 atom 10 分立即触发 SkillEdit | Task 3 acceptance #4（threshold=10 + prompt 鼓励高分） |
| 严格分档表，不卡条数用质量说话 | Task 2 SYSTEM_PROMPT + Task 3 cluster prompt + Task 5 score_atom prompt |
| 灰度按 atom 粒度打分 | Task 5 acceptance #2-3 |
| 冷启动 used_skills 空 → 灰度跳过 | watcher `_score_new` 沿用 `xskill:` header 判（Task 5 outline） |
| 混合检索 union+dedup，无 rerank | Task 1 acceptance #4 |
| `ReadTraj(traj_id, offset_start, offset_end)` | Task 3 acceptance #2 |
| 不留老配置兼容 | Task 6 acceptance #2-5 + 旧文件整体删除 |
| 不写 fallback / OOP / 先写 E2E 方案 | 全 plan 贯穿；Task 6 顶部 E2E 方案 |

### 2. Placeholder scan

无 TBD / TODO / "类似 Task N" / "添加适当错误处理"。每个 acceptance 项都列出具体函数名 + 字段名 + 返回 shape；每个 SYSTEM_PROMPT 关键内容都直接写在 plan body 里（不靠"实现时再写"）。

### 3. Type / 命名一致性

- `AtomTask` 12 个字段（Task 1）→ Task 2 拆分时填充 → Task 3 工具 / Cluster / Edit Agent 消费 → Task 5 `score_atom` 读 → Task 6 E2E 断言。字段名全程一致。
- 工具函数命名一致：`atom_task_read` / `atom_task_search` / `read_traj` / `new_skill_folder` / `skill_read` / `add_task_to_skill` / `score_task` / `add_task`（snake_case 在 Python 内部；agno tool 装饰器若需 PascalCase 可在 wrap 时做映射，但函数名稳定）。
- candidates v2 字段：`atom_id` / `weightscore_total` / `contributions:[{score,ts,note}]` / `promoted` / `promoted_at` —— Task 3 定义、Task 6 grep 验证（无 `pattern` / `supporting_trajs` 字段残留进新 schema）。
- 状态机：`discovered / splitting / split_done / indexed / clustering / done / error` —— Task 4 落实，Task 6 E2E 测断言。

### 4. 风险点

- **agno tool 名规范**：旧 `skill_tools` 用 snake_case 直接给 agno；新工具沿用同模式。若 agno 版本对工具名有大小写要求需在 wrap 时调整——Task 3 实施时先 grep 旧代码确认。
- **HybridSearch 关键字分词**：`re.compile(r"[\w]+", re.UNICODE)` 对中文不切词，会把整段中文当一个 token。Task 1 验收只看 union+dedup 结构；中文检索效果走 Task 6 E2E 时再观察，必要时引 jieba（不在本 plan 范围）。
- **watcher cold-start gate 含义改变**：从"等所有 traj 索引完"到"等所有 atom 索引完"。原 deferral 计数可能不准——Task 4 acceptance 不强约束，留作 E2E 阶段观察。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-12-atomtask-refactor.md`. Two execution options:

**1. Subagent-Driven (recommended)** - 每个 task 起独立 subagent 跑 + 两阶段 review；适合现在这种"6 个大 task 各自独立可验"的形态。

**2. Inline Execution** - 当前 session 顺序跑 Task 1→6，每完成一个 task 暂停 checkpoint 等你 review。

Which approach?
