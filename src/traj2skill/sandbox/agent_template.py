"""
sandbox/agent_template.py — 注入 Docker 容器的 agent 脚本模板
══════════════════════════════════════════════════════════════════
生成一个自包含的 Python 脚本，在容器内通过 OpenAI 兼容 API 驱动 LLM 解决问题。
"""

AGENT_SCRIPT_TEMPLATE = r'''#!/usr/bin/env python3
"""Auto-generated agent script for sandbox evaluation."""

import json, os, re, subprocess, sys, time

# ── Config (injected at generation time) ──
BASE_URL = "{base_url}"
MODEL = "{model}"
API_KEY = "{api_key}"
MAX_TURNS = 30
TIMEOUT_PER_COMMAND = 60

# ── Problem ──
PROBLEM_STATEMENT = """{problem_statement}"""

# ── Skill (empty string = baseline) ──
SKILL_MD = """{skill_md}"""

# ═══════════════════════════════════════════════════════════════
# LLM Client (minimal, no external deps beyond stdlib + openai)
# ═══════════════════════════════════════════════════════════════

try:
    from openai import OpenAI
    client = OpenAI(base_url=BASE_URL, api_key=API_KEY or "no-key")
except ImportError:
    # fallback: pip install openai in container
    subprocess.run([sys.executable, "-m", "pip", "install", "openai", "-q"],
                   capture_output=True, timeout=60)
    from openai import OpenAI
    client = OpenAI(base_url=BASE_URL, api_key=API_KEY or "no-key")


def llm_chat(messages: list[dict], max_tokens: int = 2000) -> str:
    try:
        resp = client.chat.completions.create(
            model=MODEL, messages=messages,
            max_tokens=max_tokens, temperature=0.0,
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        return f"[LLM ERROR] {{e}}"


# ═══════════════════════════════════════════════════════════════
# Tools
# ═══════════════════════════════════════════════════════════════

def tool_bash(command: str) -> str:
    """Execute a bash command."""
    try:
        r = subprocess.run(
            ["bash", "-c", command], capture_output=True, text=True,
            timeout=TIMEOUT_PER_COMMAND, cwd="/testbed",
        )
        out = r.stdout[-3000:] if len(r.stdout) > 3000 else r.stdout
        err = r.stderr[-1000:] if len(r.stderr) > 1000 else r.stderr
        return f"stdout:\n{{out}}\nstderr:\n{{err}}\nexit_code: {{r.returncode}}"
    except subprocess.TimeoutExpired:
        return "[TIMEOUT]"
    except Exception as e:
        return f"[ERROR] {{e}}"


def tool_read_file(path: str) -> str:
    """Read a file."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            content = f.read()
        if len(content) > 8000:
            return content[:8000] + f"\n... (truncated, {{len(content)}} chars total)"
        return content
    except Exception as e:
        return f"[ERROR] {{e}}"


def tool_write_file(path: str, content: str) -> str:
    """Write content to a file."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Written {{len(content)}} chars to {{path}}"
    except Exception as e:
        return f"[ERROR] {{e}}"


TOOL_MAP = {{
    "bash": tool_bash,
    "read_file": tool_read_file,
    "write_file": tool_write_file,
}}

TOOL_DESC = """Available tools (call by name):
- bash(command: str): Run a shell command in /testbed
- read_file(path: str): Read file content
- write_file(path: str, content: str): Write/overwrite a file

Call format (strict):
<tool_call>{{"name": "tool_name", "args": {{"param": "value"}}}}</tool_call>

When done with all changes, output <done/> on its own line."""


# ═══════════════════════════════════════════════════════════════
# Agent Loop
# ═══════════════════════════════════════════════════════════════

def build_system_prompt() -> str:
    parts = [
        "You are a coding agent. Fix the bug described below by editing files in /testbed.",
        "Work step by step: read relevant code, understand the bug, make minimal changes, verify.",
        "",
        TOOL_DESC,
    ]
    if SKILL_MD:
        parts.extend([
            "",
            "## Relevant Skill (use this as guidance)",
            SKILL_MD,
        ])
    return "\n".join(parts)


def run_agent():
    messages = [
        {{"role": "system", "content": build_system_prompt()}},
        {{"role": "user", "content": f"## Problem\n{{PROBLEM_STATEMENT}}\n\nPlease fix this issue."}},
    ]

    for turn in range(MAX_TURNS):
        resp = llm_chat(messages)
        messages.append({{"role": "assistant", "content": resp}})

        if "<done/>" in resp:
            break

        # Extract tool call
        tc_match = re.search(r'<tool_call>\s*(\{{.*?\}})\s*</tool_call>', resp, re.DOTALL)
        if tc_match:
            try:
                call = json.loads(tc_match.group(1))
                name = call.get("name", "")
                args = call.get("args", {{}})
                if name in TOOL_MAP:
                    result = TOOL_MAP[name](**args)
                else:
                    result = f"Unknown tool: {{name}}"
                messages.append({{"role": "user", "content": f"Tool result:\n{{result}}"}})
            except Exception as e:
                messages.append({{"role": "user", "content": f"Tool error: {{e}}"}})
        else:
            if turn < MAX_TURNS - 1:
                messages.append({{"role": "user", "content": "Continue. Use tools or output <done/> when finished."}})

    print("[AGENT DONE]")


if __name__ == "__main__":
    run_agent()
'''


def generate_agent_script(
    problem_statement: str,
    llm_config: dict,
    skill_md: str = "",
) -> str:
    """生成注入容器的 agent 脚本"""
    # 转义三引号
    problem_safe = problem_statement.replace('"""', '\\"\\"\\"')
    skill_safe = skill_md.replace('"""', '\\"\\"\\"')

    return AGENT_SCRIPT_TEMPLATE.format(
        base_url=llm_config.get("base_url", ""),
        model=llm_config.get("model", ""),
        api_key=llm_config.get("api_key", ""),
        problem_statement=problem_safe,
        skill_md=skill_safe,
    )
