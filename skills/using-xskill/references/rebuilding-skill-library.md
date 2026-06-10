# xskill — Rebuilding the Skill Library

`xskill rebuild` re-distills Skills **from the trajectories already collected** — without
re-collecting anything. The classic reason to run it is "I switched to a stronger model
and want the whole library regenerated."

## What `rebuild` actually does (and what it does NOT)

- It only **resets database state** for the matched trajectories
  (`status='discovered'`, `last_offset=0`, atoms/skills optionally wiped). It does **not**
  itself re-distill.
- The running daemon's watcher does the real work — it picks the reset trajectories up on
  its next 30s scan and re-runs TaskAgent → TaskClusterAgent → SkillEditAgent.
  **No `xskill serve` running = nothing is rebuilt.**
- Two modes, differing only in whether old products are cleared:
  - **default** — reset trajectory state, **keep** existing atoms and skills (re-read deltas).
  - **`--force`** — additionally wipe the skill repo + delete every `atom_*.json`, then
    re-split from scratch. This is the "regenerate the whole library with a new model" path.
- Scope flags for partial/debug runs: `--eco <name>` (one ecosystem), `--traj <id>` (one
  trajectory).

## Known gotcha: model staleness (as of 0.6.1a1)

The daemon caches its LLM client at startup. If you change the model in `config.yaml` and
run `rebuild` **without restarting `serve`**, the rebuild runs with the **old** model.
**Always restart `xskill serve` after a model change, before `rebuild --force`.**
(A model-diff check + single-instance guard is slated for a later release.)

## A prompt you can hand to a model to do this correctly

```text
# 任务：正确地 rebuild xskill 的 skill 库

你要用 xskill 的 `rebuild` 命令，从已有的原始轨迹重新蒸馏 skill 库。
动手前先建立正确心智模型，再按步骤执行，最后验证收敛。

## 必须先理解的机制（否则会做错）

1. rebuild 只重跑「蒸馏」，不重新采集轨迹。原始轨迹（~/.xskill/*_sessions/*.md）
   是输入，rebuild 不动它们，只是让蒸馏管线对着它们再跑一遍。
2. `rebuild` 命令本身只改数据库状态，不干活。它把轨迹状态重置回
   discovered、last_offset=0。真正重拆重聚的是 `xskill serve` 里那个
   每 30s 扫描一次的 watcher。**daemon 没运行 = rebuild 之后什么都不会发生。**
3. 蒸馏管线有三段，rebuild 后会按顺序重跑：
   - TaskAgent：把每条轨迹切成 AtomTask（语义片段）
   - TaskClusterAgent：每个 atom 路由到已有 skill 的 .candidates.yml，或建新 baby skill
   - SkillEditAgent：某 skill 的 candidates 累计 weightscore≥10 时，写/更新 SKILL.md
4. 两种模式，区别只在「清不清旧产物」：
   - 默认（不带 --force）：只重置轨迹状态，**保留**已拆的 atom 和已有 skill。
     适合「轨迹有新增内容、想让 watcher 重读增量」。
   - `--force`：额外清空 skill 仓 + 删掉所有 atom_*.json，然后全量重拆。
     **换强模型、想从零重生成整个 skill 库时用这个。**

## 执行步骤

第 1 步 · 确认目标
  - 是「换了更强的模型、想整库重蒸馏」？→ 用 --force
  - 是「轨迹有增量、只想补蒸馏」？→ 不带 --force
  - 只想验证某条轨迹/某生态？→ 加 --traj <id> 或 --eco <name> 缩小范围

第 2 步 · 确认 daemon 在跑（这是最容易漏的一步）
  - 换了模型时：先改 ~/.xskill/config.yaml，再**重启** `xskill serve`，否则 daemon
    用的还是启动时缓存的旧模型，rebuild 会拿旧模型重生成。
  - 跑 `xskill serve`（standalone）或确认已有 daemon 进程。
  - rebuild 命令末尾会提示 daemon 状态；若打印「⚠ 未检测到运行中的 daemon」，
    说明 rebuild 只是改了状态、不会自动重跑——必须先把 serve 起起来。

第 3 步 · 执行 rebuild
  - 整库换模型重生成：`xskill rebuild --force`
  - 增量补蒸馏：`xskill rebuild`
  - 缩范围调试：`xskill rebuild --force --traj <traj_id>`
  - 读它的输出：会打印「重置 N 条轨迹（含清原子/保留原子）」和 daemon 是否在跑。

第 4 步 · 等待并观察 watcher 重跑
  - watcher 每 30s 扫一次，重置过的轨迹会被当作新轨迹全量重拆。
  - 看日志（xskill.* 命名空间）确认 TaskAgent→TaskClusterAgent→SkillEditAgent
    依次触发；大库需要多轮 30s 周期才能跑完所有轨迹。

第 5 步 · 验证收敛
  - skill 仓里出现重新生成的 SKILL.md（--force 时数量应从 0 重新长出来）。
  - 轨迹对应目录下重新出现 atom_*.json。
  - 抽查一两个 skill 的 SKILL.md 内容，确认是新模型的产物、质量符合预期。

## 禁止事项
  - 不要手动删 ~/.xskill 下的轨迹原始 .md（那是输入，删了就没法重蒸馏）。
  - 不要在 daemon 没起来的情况下以为 rebuild 跑完了——它只改了 DB 状态。
  - 换模型时不要不重启 serve 就直接 rebuild（会用旧模型）。
  - 不要对生产库直接 --force 而不先确认这是你要的范围；想试先用 --traj 缩小。
```
