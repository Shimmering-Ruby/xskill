"""process.py -- AtomTask 流水线核心入口
========================================

v2 (AtomTask) 流水线下，对一个 atom 的"cluster → 触发 SkillEdit"是单一原子操作；
不存在"轨迹整篇喂 LLM"概念。tasks.py / watcher.py 都调本模块的
``process_atom_task``，传入已 split + indexed 完毕的 atom_id。

旧 ``process_traj`` (v1) 在 AtomTask 重构中删除——配套的 SkillAgent / eval /
canary-on-traj 逻辑都已被 TaskClusterAgent / SkillEditAgent / AtomCanary 取代。
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("xskill.process")


def process_atom_task(*, atom_id: str, config: dict, skill_dir: Path,
                      store, embed_client, agno_agent_factory) -> dict:
    """处理一个 AtomTask：只跑 cluster，**不跑 edit**。

    edit 触发由 watcher 每轮独立扫描所有 skill 目录完成（见
    ``watcher.DirectoryWatcher._check_pending_skill_edits``）。把 edit 从
    cluster 解耦后，即便某次 cluster 抛异常，buffer 已满阈值的 skill 仍能
    在后续 watcher 轮次中被检出 + 触发——不会因为某个 atom cluster 失败
    错失整批 candidates 的 promote 机会。

    Args:
        atom_id: AtomTask 主键
        config: xskill 配置（含 llm 段）
        skill_dir: skill 根目录（其下每个子目录是一个 skill 仓库）
        store: AtomTaskStore（持有所有 atom + 索引）
        embed_client: 向量客户端（HybridSearch 用）
        agno_agent_factory: callable(*, instructions, tools) -> agno-like Agent。
                            生产环境用 ``agno_factory.make_default_factory(config)``；
                            单测注入 stub。

    Returns:
        dict 含 keys: action / atom_id / cluster_log
    """
    from xskill.task_cluster_agent import TaskClusterAgent
    from xskill import skill_tools as ST

    atom = store.load(atom_id)
    traj_root = store.root

    ST.init_context_v2(
        skill_dir=skill_dir, store=store,
        embed_client=embed_client, traj_root=traj_root,
    )

    cluster = TaskClusterAgent(
        skill_dir=skill_dir, store=store,
        agno_agent_factory=agno_agent_factory,
        llm_cfg=config.get("llm", {}),
        tools=[
            ST.atom_task_read, ST.atom_task_search, ST.read_traj,
            ST.skill_read, ST.new_skill_folder, ST.add_task_to_skill,
            ST.score_task,
        ],
    )
    cluster_content = cluster.process(atom)

    return {
        "action": "clustered",
        "atom_id": atom_id,
        "cluster_log": (cluster_content or "")[:500],
    }
