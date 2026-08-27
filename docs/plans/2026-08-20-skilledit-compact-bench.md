# SkillEdit 压缩回放评测：执行文档

日期：2026-08-20
状态：格子已改成 30 次（2 类 × 3 版 × 5 次）。8 条轨迹只是两类原材料，不再按每条乘一遍。未经点头不开官方 API。

## 一句话

在本机用 DeepSeek 官方 flash 回放 8 条真实轨迹（4 大、4 小）的 SkillEdit，压缩窗与现网一致，比较三版提示词，主分只有 commit 率、压缩次数、读完就 compact 率。

## 范围

做：只跑 SkillEdit `maybe_run`。8 条真实轨迹是两类原材料（4 大、4 小）。提示词三版、两类各跑 5 次，一共 30 次回放。

不做：8 条 × 三版 × 5 次那种 120 回放（那是误把原材料乘进格子里）。拆分、聚类、现网 cache-migration 加试、SKILL.md 质量分、对齐现网紧限流、改产品默认 prompt（v3 只活在评测夹具里）。以后再说。

## 冻结口径

压缩（现网 ROUND 头 `spill@37.1k | compact@38.3k`）：

- `max_context` = 43648
- `compact_token_limit` = 38300
- `compact_keep_recent_messages` = 6
- 限流：rpm 240、request_burst 8、max_inflight 8
- spill：关（`llm.spill=false`）。走仓库里的 SkillEditAgent 和 ContextManager，只换提示词，不另写一套外置。

模型（只读 `~/.aikey`，不回显、不落盘副本）：

- `DEEPSEEK_BASE_URL_OPENAI` → `https://api.deepseek.com`
- `DEEPSEEK_MODEL_FAST` → `deepseek-v4-flash`
- `DEEPSEEK_API_KEY`

数据：

- 根目录：`~/.xskill/opencode_sessions`、`codex_sessions`、`cc_sessions`
- 入选条件：存在 `traj_id.md`，且 `traj_id/tasks/atom_*.json` 至少一个
- 大 4 条：按 md 行数最长
- 小 4 条：按 md 行数最短，且至少 50 行，不与大 4 重复
- 每条轨迹一个 baby skill，该 traj 下全部 atom 进 candidates，每条 weightscore 先给 10

提示词（只换 instructions，工具不变）：

- v1：`scripts/bench/skilledit_prompts/v1_pre_v7.txt`（`c470f83^`，白名单四段结构）
- v2：`scripts/bench/skilledit_prompts/v2_current.txt`（当前 HEAD v7）
- v3：`scripts/bench/skilledit_prompts/v3_converge.txt`（v2 加收敛四条）

跑法：

- 夹具路径全部显式注入 `/tmp/skilledit-bench-*`，不写本机正在用的 skill 仓
- 一条记录 = 类别 × 提示词 × 第 k 次（k=1..5）。2 类 × 3 版 × 5 次 = 30。第 k 次从该类 4 条原材料里轮转取一条
- 单次最多 80 次模型调用或 20 分钟，轮次按整次回放合计（不是每个 baby turn 各 80）
- 触帽先在共享标志上记 `capped`，再抛错；baby 的 `except Exception` 吞掉异常也能读到
- 夹具只拷清单里的 atom，源目录多了或少了当场报错
- 每条回放先追加 `results.jsonl`，再重写 `results.json`，失败也有结果行
- 结束时按 v1/v2/v3 × 大/小/合计打三列主分总表
- `edit.batch_size` = 4

## 主分怎么算

commit 率：该次回放至少一次 `TURN END | COMMITTED`，或 `maybe_run` 返回真，记 1，否则 0。某格 = 5 次里的成功次数 / 5。

压缩次数：该次回放里 `Compacted context` 成功次数。5 次报均值和最大值。

读完就 compact 率：同一 ROUND 既有读类 TOOL RESULT（atom_task_read、read_traj、read_file、skill_read）又有成功 compact，的次数，除以该次回放成功 compact 次数。没有 compact 的回放记「无 compact」，不进平均。

解析复用 `scripts/bench/skilledit_lib.py` 里与诊断脚本同一套正则，不另造定义。

## 目录

- 本文：`docs/plans/2026-08-20-skilledit-compact-bench.md`
- 公共库：`scripts/bench/skilledit_lib.py`
- 挑样：`scripts/bench/skilledit_pick_cases.py`
- 回放：`scripts/bench/skilledit_compact_replay.py`
- 基础设施自检：`scripts/bench/skilledit_check_infra.py`
- 提示词：`scripts/bench/skilledit_prompts/*.txt`
- 选样清单（不含原文）：`scripts/bench/real/skilledit_manifest.json`

## 命令

```bash
cd /home/admin/xskill
.venv/bin/python scripts/bench/skilledit_pick_cases.py
.venv/bin/python scripts/bench/skilledit_check_infra.py
# 冒烟一条（会打官方 API）：
# .venv/bin/python scripts/bench/skilledit_compact_replay.py --smoke
# 30 次（点头后再跑）：
# .venv/bin/python scripts/bench/skilledit_compact_replay.py --all
```

## 验收标准

分两档。先过基础设施，再过一次冒烟，最后才开 30 次。

### A. 基础设施（不打模型，必须全过）

1. 三份提示词文件存在，都含 `{scenario_block}` 和 `{branch_now}`。v1 含「坑位清单」，v2 含「通用核心」，v3 含「收敛（本轮必须遵守）」。v3 除追加段外与 v2 相同。
2. `skilledit_pick_cases.py` 写出 8 条清单：`size=large` 恰好 4，`size=small` 恰好 4。每条有 traj_id、md 行数、atom_id 列表、atom 字符合计。大的最小行数大于小的最大行数。小的行数都 ≥ 50。清单里没有 atom 正文、没有 traj 正文。
3. `skilledit_check_infra.py` 读 `~/.aikey` 成功：打印 `DEEPSEEK_API_KEY=set` 和 key 长度，不打印 key 本身。base_url 是 `https://api.deepseek.com`，model 是 `deepseek-v4-flash`。
4. 用冻结的 max_context 和 compact_token_limit 算出来 spill=37100、compact=38300。把这两项塞进 `SkillEditAgent._trace_limits` 得到同样的数。
5. `--prepare-only` 能为清单里第一条搭出隔离目录：baby 仓、`.candidates.yml` 里 atom 数与清单一致且总分 ≥ 10、atom JSON 和 traj.md 都能读到。不调用 LLM。
6. 对一份合成 skill_edit 日志，计分器给出 commit=0、compact_ok=1、same_round_read_compact=1，读完就 compact 率=1.0。
7. 脚本不读取、不打印 `~/.xskill/config.yaml` 里的 api_key。

### B. 冒烟（打一次官方 API，基础设施过后再做）

1. `skilledit_compact_replay.py --smoke` 用 v2、一条大轨迹、k=1 跑完或触帽。
2. 该条 log 第一行 ROUND 头是 `spill@37.1k | compact@38.3k`。
3. 产出一条 JSON 结果，三列主分都有字段，capped 为真或假。
4. 隔离目录在 ` /tmp/skilledit-bench-* ` 下，本机 `~/.xskill/skill` 没有被这条回放改掉。

### C. 全量（30 次，冒烟过后再做）

1. 每个提示词 10 次（大 5 + 小 5）都有结果行，失败也要落盘原因，不许静默丢。
2. 总表按 v1/v2/v3 × 大/小/合计报三列主分。
3. 小轨迹的压缩次数均值应明显低于大轨迹；若小轨迹 compact 均值和大轨迹一个量级，先查窗是否配错，再谈提示词。

## 基础设施完成的定义

A 的 1–7 全部由 `.venv/bin/python scripts/bench/skilledit_check_infra.py` 一次跑完并 exit 0。这之前不打官方 API，不跑 30 次。
