"""xskill.agents — LLM 驱动的 agent 层

把四个 agent 及其共享设施从扁平的 ``xskill/`` 根目录收拢成一个子包：

- ``task_agent``            轨迹按用户意图切分为 AtomTask
- ``task_cluster_agent``    把 AtomTask 路由到匹配的 skill
- ``skill_edit_agent``      从攒够的候选合成 / 更新 SKILL.md
- ``generate_agent``        用户点名即时生成或改写 skill（直接提交 main）
- ``skill_scripting_agent`` 实验性：把主干技能改得更偏可执行脚本
- ``user_edit_absorb_agent``  吸收用户手改为 ground truth
- ``agno_factory``          创建 agno.Agent 的共享工厂（模型路由 / SSL）
- ``agent_tools``           agent 工具和运行时依赖

消费方按需 ``from xskill.agents.<module> import <symbol>`` 显式取用——
本 ``__init__`` 不做 eager 再导出，避免引入包级 import 副作用与环依赖。
"""

from __future__ import annotations
