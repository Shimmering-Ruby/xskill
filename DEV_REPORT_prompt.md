# DEV REPORT — SkillEdit 提示词知识提炼改造

分支：`feat/skill-edit-knowledge-prompt`（基于 `7895156`）
改动文件：`src/xskill/agents/skill_edit_agent.py`（`SYSTEM_PROMPT_TEMPLATE`）、`tests/test_skill_edit_agent.py`

## 背景

线下提示词工程实验裁定：现行 SkillEdit 提示词的写作指导（"完整可执行 skill"+"步骤精确到
命令/文件/函数"）是伪技能（pseudo-skill，把一次执行过程复述成 skill）的成因。需替换为
**知识提炼型内容规则 + 结构与证据纪律**的合成版。本次只动 `SYSTEM_PROMPT_TEMPLATE` 里
"写作指导"性质的段落，管线契约部分原样保留。

## 替换前后对照表

| 项目 | 改前 | 改后 |
|---|---|---|
| 目标段（"你的目标"） | "整理出一份**完整可执行**的 skill" | "从轨迹里**提炼可泛化的知识**……价值 = 读它的人少踩多少坑、少试多少次错，而不是把一次执行过程复述一遍" |
| body 结构示例 | `## <阶段名>` + `1. 步骤：精确到命令/文件/函数` | V4 四段固定结构：`## 核心原则`(≤3) / `## 领域规则`(三件套 规则·机制·适用条件) / `## 坑位清单`(错误模式｜症状｜根因｜修法 表格) / `## 工具`(每个 scripts/ 文件一行) |
| ⚠️ 警告示例 | "引用 atom 证据，不要凭空猜" | 保留 atom 证据引用要求，**新增**末尾证据强度标注 `[实证：N 条轨迹]`/`[单例]`/`[推断]` |
| 内容白名单 | 无 | **新增**三类内容：领域规则 / 可复用工具 / 坑位清单（四元组俱全） |
| 反模式黑名单 | 无 | **新增**：任务流程复述 / 实例细节搬运 / 触发表述抄题面（出现即不合格） |
| 泛化自检闸 | 无 | **新增**："换一道同领域、不同题目的任务，这条还成立吗？"不成立删掉；每条写适用条件 |
| 参数化禁兜底 | 无 | **新增**：禁止 `else 'somefile.xlsx'` 类硬编码默认值，宁可缺参报错 |
| 失败挖掘 | 无 | **新增**三规则：死因回溯(四元组+机制) / 成败差分(同题对照必做差分) / 无症状死亡深挖 |
| 证据纪律 | 无 | **新增**：每条规则/坑位标证据强度三档；仅 `[推断]` 支撑 ≤2 条 |
| 长度预算 | 旧 `## 硬禁止` 里 "SKILL.md ≤ 400 行" | 改为正文 **≤200 行** + 删减铁律（先删最弱可泛化性内容；强规则适用场景不许截断；**宁删整条弱规则不砍强规则半条**）。`## 硬禁止` 中 400 行表述改指向新长度预算 |
| `## 硬禁止` 的标题禁令 | 禁 `## trigger/触发条件/pitfalls/陷阱`，pitfalls 只能内联 | 改为：禁 `## trigger/触发条件`；坑位写进新的 `## 坑位清单` 表格，规则附带警告仍用 `> ⚠️` 内联 |

实验术语（V2/V4/P1）未写入提示词正文，仅本报告对照使用。

## 保留不动（管线契约，非写作指导）清单

- `# 当前场景` 块及 `{scenario_block}` / `{branch_now}` 占位符
- commit 工具协议（`commit_baby_to_main` / `commit_to_staging` 二选一 + commit message 要求）
- `# 隐私守护` 全段（敏感信息占位符化、`[REDACTED]` 保持原样）
- frontmatter schema 字段（name/description/compatibility/metadata.*）
- `# 可用工具` 清单（AtomTaskRead/ReadTraj/SkillRead/list_files/write_file/commit_*）
- `# 提交前质量闸`（价值自检/渐进式披露/无孤立脚本/description 可触发）
- `# 硬禁止` 里的 atom 唯一可信来源、不发明用户群体、不写 `.git/`/`.candidates.yml`

## 测试

新增测试类 `TestWritingDisciplineInPrompt`（12 条），断言模板含关键纪律词
（反模式/泛化自检/坑位四元组四列/证据强度三档/`somefile.xlsx` 禁兜底/失败挖掘三词/
≤200 行+删减铁律/四段结构）且不含旧表述（"完整可执行"、"精确到命令/文件/函数"），
并校验管线契约部分仍在、模板 `.format()` 不报 KeyError。

- `tests/test_skill_edit_agent.py`：**21 passed**（9 原有行为 + 12 新纪律断言）。
- 全量套件（venv 编辑安装 + `--timeout=180`）：**801 passed, 5 failed, 8 errors**。
  - 5 failed（test_server_watcher_startup / test_team_reconnect_identity）+ 3 errors
    （test_server_chat_removed / test_server_no_llm_fallback）+ 8 collection errors
    （test_cli_*、test_team_cli_*、test_rebuild、test_team_create_app）**全部为既有的
    测试隔离/收集顺序 flaky**：
    - 把这些模块单独/小组运行**全部通过**（cli 8 模块 + skill_edit = 42 passed；
      server/team 7 模块 = 23 passed）。
    - 在改动前 HEAD（git stash 后）跑同一套也是 `4 failed, 790 passed, 8 errors`，
      失败集合相同，与本次纯字符串改动无关。
  - canary e2e（`tests/e2e`）按要求隔离，未纳入本次全量数字。
- `pytest-timeout` 已按要求启用（`--timeout` 防吊死）。

## 复跑命令

```bash
python3.11 -m venv /tmp/t2s-venv
/tmp/t2s-venv/bin/python -m pip install -e ".[dev]"
# 注：worktree 缺 setuptools_scm 生成的 _version.py（gitignored），
# 从主仓 cp src/xskill/_version.py 过来即可
/tmp/t2s-venv/bin/python -m pytest tests/test_skill_edit_agent.py --timeout=60 -q
```
