"""
skill/trigger_probe.py — 真跑代理的闭环触发探针（closed-loop trigger probe）
═══════════════════════════════════════════════════════════════════
取代 description_opt 里的 LLM-as-judge。判定"一个描述会不会触发"的方式从
"问 LLM 你会调哪个"（意见/元问题）换成 **真跑一个 agno 迷你代理**：把候选
skill + 一批语义相关的真实 skill 各注册成一个工具（工具说明=各自描述），喂
用户 query，代理一旦 call 了"本 skill 的工具"——记一笔、当场终止本轮
（``StopAgentRun``）。这是真实工具调用循环里的一次真实决策。

设计见 docs/plans/2026-06-11-trigger-probe-real-agent-and-dashboard.md。

保真度天花板：探针代理是 agno + 用户自配模型（DeepSeek/GLM），**不是 Claude
Code 本体**。它给的不是绝对真值，而是"A、B 两版描述哪个在真实竞争环境里更易被
真实代理选中"的相对信号——比元问题可信。

无副作用 → 无需沙箱（D5）：触发即终止，代理全程不执行任何真实动作；工具空间
只有 skill 触发工具（调用=拦截+终止）+ 几个只读空操作桩。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable

import numpy as np

from xskill.canary import main_sha
from xskill.skill.repo import SkillRepo

logger = logging.getLogger("xskill.skill_edit_agent")

# 探针单轮工具调用上限：给代理一两步决定够了，超了即视作"没触发本 skill"。
_PROBE_TOOL_CALL_LIMIT = 4

_PROBE_SYSTEM_PROMPT = (
    "You are a coding agent. Below (as your available tools) is the "
    "available_skills list: each `use_*` tool represents a skill, and its "
    "description tells you when that skill applies. Read the user's request "
    "and decide which single skill (if any) best fits. If one fits, invoke "
    "its `use_*` tool. If none fit, do NOT invoke any `use_*` tool — just "
    "say so briefly. Base the decision solely on the skill descriptions and "
    "the user's intent."
)


def _slug_to_tool(name: str) -> str:
    """skill name → 合法 python 标识符工具名 ``use_<slug>``。"""
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower() or "skill"
    if slug[0].isdigit():
        slug = "s_" + slug
    return "use_" + slug


def _truncate(text: str, cap: int) -> str:
    text = (text or "").strip()
    if cap > 0 and len(text) > cap:
        return text[:cap]
    return text


# ═══════════════════════════════════════════════════════════════════
# 真实诱饵清单（D2/D3/D4/D6）：query 锚点 → main 分支 → cosine top-N → 截断
# ═══════════════════════════════════════════════════════════════════

def build_probe_catalog(
    query: str,
    skill_name: str,
    *,
    skill_root: Path,
    embed_client: Any,
    max_skills: int,
    desc_cap: int,
) -> list[dict]:
    """组装探针的诱饵清单：与 query 语义最近的若干 **main 分支** skill。

    - 只取已 graduate 到 main 的 skill（``main_sha`` 非空）——排除 baby（无正文
      stub）与 staging/canary 旁路候选。
    - 用 ``.skill_index.pkl``（L2 归一 embedding 矩阵）对 query 向量算 cosine，
      降序取前 ``max_skills``。
    - 每条 description 按 ``desc_cap`` 截断（镜像 Claude Code 的单条上限——代理
      看到的就是真实会看到的截断版）。
    - 排除本 skill 自身（它由 probe_trigger 注入候选描述）。

    返回 ``[{"name", "description"}, ...]``；索引缺失/为空时返回 ``[]``（探针
    退化为"只有本 skill"，调用方应知此时触发率无区分度）。
    """
    skill_root = Path(skill_root)
    index_path = skill_root / ".skill_index.pkl"
    if not index_path.is_file():
        logger.warning("build_probe_catalog: .skill_index.pkl 缺失 (%s)，"
                       "诱饵清单为空", skill_root)
        return []

    import pickle
    with open(index_path, "rb") as f:
        index = pickle.load(f)
    names: list[str] = list(index.get("skill_names") or [])
    embeddings = index.get("embeddings")
    if not names or embeddings is None or len(names) != len(embeddings):
        logger.warning("build_probe_catalog: skill_index 结构异常，诱饵清单为空")
        return []

    # main 分支过滤 + name→description
    main_desc: dict[str, str] = {}
    for sk in SkillRepo(skill_root):
        if sk.name == skill_name:
            continue
        if main_sha(sk.path):
            main_desc[sk.name] = sk.description

    # query 向量 L2 归一 → cosine = embeddings @ q
    q = np.asarray(embed_client.encode(query), dtype=np.float32)
    norm = float(np.linalg.norm(q))
    if norm == 0.0:
        return []
    q = q / norm
    sims = np.asarray(embeddings, dtype=np.float32) @ q
    order = np.argsort(-sims)

    catalog: list[dict] = []
    for row in order:
        nm = names[int(row)]
        if nm == skill_name or nm not in main_desc:
            continue
        desc = _truncate(main_desc[nm], desc_cap)
        if not desc:
            continue
        catalog.append({"name": nm, "description": desc})
        if len(catalog) >= max_skills:
            break
    return catalog


# ═══════════════════════════════════════════════════════════════════
# 探针（D1/D5）：skill-as-tool + StopAgentRun
# ═══════════════════════════════════════════════════════════════════

def _make_skill_tool(
    tool_name: str, skill_real_name: str, description: str, record: dict,
) -> Callable:
    """造一个代表某 skill 的工具：调用即记 triggered + 抛 StopAgentRun 终止本轮。"""
    from agno.exceptions import StopAgentRun

    def _tool(reason: str = "") -> str:  # noqa: ARG001 (reason 给模型填，不用)
        record["triggered"] = skill_real_name
        raise StopAgentRun("skill triggered")

    _tool.__name__ = tool_name
    _tool.__doc__ = description or f"Use the {skill_real_name} skill."
    return _tool


def _stub_read_file(path: str = "") -> str:  # noqa: ARG001
    """Read a file's contents (read-only)."""
    return ""


def _stub_list_files(path: str = ".") -> str:  # noqa: ARG001
    """List files under a directory (read-only)."""
    return ""


def probe_trigger(
    query: str,
    skill_name: str,
    candidate_desc: str,
    catalog: list[dict],
    *,
    agno_agent_factory: Callable[..., Any],
    desc_cap: int,
) -> str:
    """真跑一轮代理，返回它触发的 skill name（或 "NONE"）。

    参数
    ----
    query: 评测查询。
    skill_name / candidate_desc: 被测 skill 及其候选描述（注入诱饵清单首位）。
    catalog: build_probe_catalog 产的真实诱饵清单 ``[{"name","description"}]``。
    agno_agent_factory: ``(*, instructions, tools, **kwargs) -> agno Agent``。
    desc_cap: 候选描述喂给代理前的截断上限（与诱饵同一上限）。
    """
    record: dict = {}
    tools: list[Callable] = []

    # 候选 skill 在首位（描述同样按 cap 截断，保证与诱饵同一可见条件）
    self_tool = _slug_to_tool(skill_name)
    tools.append(_make_skill_tool(
        self_tool, skill_name, _truncate(candidate_desc, desc_cap), record,
    ))
    used_tool_names = {self_tool}
    for entry in catalog:
        nm = entry["name"]
        tname = _slug_to_tool(nm)
        # 工具名撞车（slug 冲突）→ 加后缀去重，保证一一对应
        base = tname
        i = 2
        while tname in used_tool_names:
            tname = f"{base}_{i}"
            i += 1
        used_tool_names.add(tname)
        tools.append(_make_skill_tool(tname, nm, entry["description"], record))

    # 只读空操作桩：给代理合理动作空间，零副作用
    tools.append(_stub_read_file)
    tools.append(_stub_list_files)

    agent = agno_agent_factory(
        instructions=[_PROBE_SYSTEM_PROMPT],
        tools=tools,
        tool_call_limit=_PROBE_TOOL_CALL_LIMIT,
    )
    try:
        agent.run(query)
    except Exception as exc:  # noqa: BLE001
        # StopAgentRun 已被 agno 内部吞掉；这里兜真实异常（网络等）——记日志，
        # 当作"未触发"，绝不让一条 case 崩掉整个优化。
        logger.warning("probe_trigger 代理异常（视作未触发）: %s", exc)

    return record.get("triggered") or "NONE"
