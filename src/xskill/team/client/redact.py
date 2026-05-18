"""redact.py — 上传前的最小代码脱敏 hook（SP1）

只防最常见的明文凭证泄漏：``sk-`` 风格的 key、``password/token/secret/api_key``
赋值字面量。db 文件、智能检测、模型侧意识留到 SP2。

设计：纯函数 + 幂等。命中即整体替换为 ``[REDACTED]``，不做部分遮掩——
SP1 的目标是"别让明文密钥裸奔到 server"，不是精细化脱敏。
"""
from __future__ import annotations

import re

# sk- / ghp_ / AKIA 等带固定前缀的长 token
_PREFIXED_TOKEN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{12,}"
    r"|ghp_[A-Za-z0-9]{16,}"
    r"|AKIA[0-9A-Z]{12,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,})\b"
)

# password / passwd / pass / token / secret / api_key = "...." 形式的赋值。
# 关键字边界用 (?<![A-Za-z]) / (?![A-Za-z]) 而非 \b——这样 DB_PASS / OPENAI_API_KEY
# 这类带下划线的环境变量名也能命中（\b 在 _PASS 处不成立）。
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z])(password|passwd|pass|secret|token|api[_-]?key)(?![A-Za-z])"
    r"(\s*[:=]\s*)"
    r"(\"[^\"]{6,}\"|'[^']{6,}'|[^\s\"']{6,})"
)

_REDACTED = "[REDACTED]"


def redact_text(text: str) -> str:
    """对一段轨迹文本做最小脱敏。幂等：``[REDACTED]`` 自身不会被再次命中。"""
    text = _PREFIXED_TOKEN.sub(_REDACTED, text)
    text = _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", text)
    return text
