"""
agent.py -- Skill Agent (agno)
===============================
SYSTEM_PROMPT + run_agent_agno + run_agent wrapper.
"""

import json, os
from traj2skill.log import StreamLog

# ===================================================================
# Agent System Prompt
# ===================================================================

SYSTEM_PROMPT = """你是一个 Skill 管理 Agent。你的任务是分析新进来的 agent 执行轨迹，
判断是否需要创建新的 skill 或更新已有 skill。

## 你的能力
- search_similar_trajs(query, top_k, filter): 检索相似历史轨迹
- search_skills(query, top_k): 检索已有 skill
- read_file(path): 读取任意文件
- create_skill(skill_name): 创建新 skill 目录
- write_file(path, content): 写入/修改文件 (仅 ./skill/ 目录下)

注意：摘要生成和向量化由系统在 eval 通过合入后自动完成，你不需要管。

## 决策流程

1. 先仔细阅读新轨迹
2. 用 search_skills 检索是否已有覆盖此场景的 skill
3. 用 search_similar_trajs 找相似的成功和失败轨迹

然后判断:

### 情况 A: 已有高度相似的 skill
  -> 读取该 skill, 判断新轨迹是否提供了新信息
  -> 有新 pitfall 或优化 -> update skill.md
  -> 没有 -> 不做任何操作

### 情况 B: 无高度相似 skill, 但从轨迹和检索结果中能看出一类可复用的任务模式
  -> 创建新 skill
  -> 从多条相似轨迹的交集提取 subgoal
  -> 从失败轨迹提取 pitfalls

### 情况 C: 信号不足, 无法提炼出有价值的 skill
  -> 不做任何操作

判断相似度时，不要依赖具体数值阈值，而是读取 skill 内容后根据 trigger 和 steps 的覆盖范围做语义判断。

## Skill 编写规范

skill.md 必须包含:
  ## trigger
  1-2 句描述适用场景

  ## steps
  按 subgoal 分段, 每段包含具体工具调用步骤

  ## pitfalls
  来自失败轨迹的错误模式和修复方式

约束:
- skill 名称用英文下划线 (如 fix_orm_n_plus_one)
- subgoal 2~8 个
- 步骤必须具体到工具名和操作, 不能笼统
- 写完 skill.md 即可，摘要和索引由系统自动处理

## Trigger 粒度自检
写完 trigger 后自问: 这个 trigger 是否只适用于当前这一条轨迹？如果是, 太细, 放宽。
是否任何 bug 修复都能命中？如果是, 太粗, 收紧。
好的 trigger 应该能覆盖"同一类"问题, 但不会误命中无关场景。"""


# ===================================================================
# Agent 执行 (agno)
# ===================================================================

def run_agent_agno(new_traj_md: str, new_traj_meta: dict, config: dict, log: StreamLog) -> dict:
    """用 agno Agent 执行"""
    from agno.agent import Agent
    from agno.models.openai import OpenAIChat
    from traj2skill.skill_tools import (
        search_similar_trajs, search_skills, read_file,
        create_skill, write_file,
    )

    llm_cfg = config.get("llm", {})
    model = OpenAIChat(
        id=llm_cfg.get("model", "gpt-4o"),
        base_url=llm_cfg.get("base_url", ""),
        api_key=llm_cfg.get("api_key") or os.environ.get("LLM_API_KEY", ""),
        role_map={
            "system": "system",
            "user": "user",
            "assistant": "assistant",
            "tool": "tool",
            "model": "assistant",
        },
    )

    user_prompt = f"""## 新轨迹内容
{new_traj_md[:6000]}

## 新轨迹 Meta
{json.dumps(new_traj_meta, ensure_ascii=False, indent=2)[:2000]}

请分析这条轨迹，按决策流程执行。"""

    agent = Agent(
        model=model,
        tools=[
            search_similar_trajs,
            search_skills,
            read_file,
            create_skill,
            write_file,
        ],
        instructions=[SYSTEM_PROMPT],
        system_message_role="system",
        markdown=True,
    )

    stream = agent.run(user_prompt, stream=True, stream_events=True)

    content = ""
    in_reasoning = False
    for chunk in stream:
        event = getattr(chunk, 'event', None)

        # 思考 token
        reasoning = getattr(chunk, 'reasoning_content', None)
        if event == 'RunContent' and reasoning:
            if not in_reasoning:
                print("  [thinking] ", end="", flush=True)
                in_reasoning = True
            print(reasoning, end="", flush=True)

        # 回复 token
        text = getattr(chunk, 'content', None)
        if event == 'RunContent' and text:
            if in_reasoning:
                print(flush=True)
                in_reasoning = False
            print(text, end="", flush=True)
            content += text

        # 工具调用开始
        if event == 'ToolCallStarted':
            if in_reasoning:
                print(flush=True)
                in_reasoning = False
            tool = getattr(chunk, 'tool', None)
            if tool:
                name = getattr(tool, 'tool_name', '?')
                args = getattr(tool, 'tool_args', {})
                short_args = ", ".join(f"{k}={repr(v)[:40]}" for k, v in args.items()) if args else ""
                log(f"{name}({short_args})", "tool")

        # 工具调用完成
        if event == 'ToolCallCompleted':
            tool = getattr(chunk, 'tool', None)
            result_text = getattr(chunk, 'content', '') or ''
            if tool:
                name = getattr(tool, 'tool_name', '?')
                # 取结果摘要
                tool_result = getattr(tool, 'tool_result', None)
                if tool_result:
                    result_str = str(tool_result)
                    if result_str.startswith('[') or result_str.startswith('{'):
                        try:
                            data = json.loads(result_str)
                            if isinstance(data, list):
                                preview = f"[{len(data)} results]"
                            elif isinstance(data, dict) and 'results' in data:
                                preview = f"[{len(data['results'])} results]"
                            else:
                                preview = result_str[:80]
                        except Exception:
                            preview = result_str[:80]
                    else:
                        preview = result_str.split('\n')[0][:80]
                    log(f"{name} -> {preview}", "tool")

    if in_reasoning:
        print(flush=True)
    if content:
        print(flush=True)

    log("Agent done", "ok")

    return {"content": content, "framework": "agno"}


def run_agent(new_traj_md: str, new_traj_meta: dict, config: dict, log: StreamLog) -> dict:
    """用 agno Agent 执行"""
    return run_agent_agno(new_traj_md, new_traj_meta, config, log)
