"""
traj_meta.py -- 轨迹 header 解析
==================================

从轨迹 markdown 头部提取 ``<!-- t2s:... -->`` 元数据注释。

约定格式::

    <!-- t2s:skill=fix_django_migration side=staging sha=a1b2c3d4 -->

所有字段均可选，按 key=value 空格分隔。
"""

from __future__ import annotations

import re

_T2S_RE = re.compile(
    r"<!--\s*t2s:"
    r"((?:\s*\w+=\S+)+)"
    r"\s*-->"
)

_KV_RE = re.compile(r"(\w+)=(\S+)")


def parse_traj_header(md_text: str) -> dict | None:
    """解析轨迹中的 ``<!-- t2s:... -->`` 元数据注释。

    只扫描前 500 字符。返回 ``None`` 表示无 t2s 标记。
    返回示例::

        {"skill": "fix_django", "side": "staging", "sha": "a1b2c3d4"}
    """
    m = _T2S_RE.search(md_text[:500])
    if not m:
        return None
    pairs = _KV_RE.findall(m.group(1))
    if not pairs:
        return None
    return dict(pairs)
