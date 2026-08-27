---
name: xskill-dev
description: Use when developing xskill itself — editing agent tool docstrings, changing the generate agent tool surface, or verifying what schema the Agno framework actually sends to the model. Covers the dump_schema workflow and where tool descriptions come from.
---

# Developing xskill

## 工具描述从哪来

Agno 框架直接把 `@tool` 装饰的 Python 函数 docstring 当作发给模型的工具描述，
参数说明也从 docstring 里解析。也就是说 docstring 就是模型上下文的一部分：

- 写给模型看，不写内部实现细节（内部细节放代码注释）。
- 系统提示词里不要再抄一遍工具清单，框架会自动注入 schema，抄了会漂移。
- generate 代理的轨迹工具在 `src/xskill/agents/traj_tools.py`，
  通用工具在 `src/xskill/agents/agent_tools.py`，
  wiki 工具在 `src/xskill/agents/llm_wiki.py`。

## 改完 docstring 必跑：dump_schema

改任何工具的 docstring 或签名之后，跑一次导出脚本，看 Agno 实际生成的
schema 是什么样，确认模型看到的和你想的一致：

```bash
/home/admin/xskill/.venv/bin/python \
  scratch/standalone-generate/tool_surface/dump_schema.py
```

输出写在同目录 `SCHEMA.txt`。对照检查：

- 每个工具的 description 是否完整、有没有被截断或混进实现细节；
- 参数名、类型、必填项是否与函数签名一致；
- 新增或删除工具后，工具总数是否符合预期（generate 面当前是 16 个）。

`SCHEMA.txt` 可以进 code review diff，reviewer 能直接看到模型侧的变化。

## 相关材料

- 工具面设计与取舍：`docs/plans/2026-08-27-generate-tool-surface.md`
- 独立实验台（Phoenix 观测、变体对比）：`scratch/standalone-generate/`，
  入口 `run_experiment.py`，`product` 变体加载产品 traj_tools 与产品
  SYSTEM_PROMPT，是验证产品行为的首选变体。
