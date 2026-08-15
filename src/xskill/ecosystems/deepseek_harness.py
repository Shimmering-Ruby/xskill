"""
ecosystems/deepseek_harness.py -- DeepSeek Harness (dsh) 生态适配
=================================================================

把蒸馏出的 Skill 装进 DeepSeek Harness 的 user-dsh skill 目录
（``~/.dsh/skills/<name>/``，dsh 的 skill-filesystem provider 以 rank 400
扫描该根，目录包内 ``SKILL.md`` 为其原生格式之一），并把 dsh 的明文
session JSONL（``~/.dsh/sessions/--<normalized-cwd>--/<encoded-id>/
session.jsonl``）桥接回 xskill 的标准 ``traj_*.md`` 格式。

上游契约（deepseek-ai/deepseek-harness）：

- skill 发现：``@deepseek-ai/dsh-skill-filesystem`` 扫 ``<dshHome>/skills``
  （rank 400 user-dsh；``.system`` 子目录被跳过）。目录包 ``SKILL.md`` 与
  扁平 ``<name>.md`` 均可；本模块安装目录包，与其他生态一致。
- session 存储：``@deepseek-ai/dsh-session-persistence-jsonl``。默认
  ``session.jsonl.zstd``（zstd 帧序列，**本期不支持**，检测到即跳过并
  记日志）；``compression: 'none'`` 时为明文 ``session.jsonl``，首行是
  ``{"type": "session", ...}`` 的 SessionHeader（带 ``cwd``），随后每行一个
  ``{type, seq, time, data}`` 的 SessionEvent。``assistant/chunk`` 与打包行
  （``text-chunks`` / ``reasoning-chunks`` / ``tool-call-chunks``）是重放
  数据，装配后的 ``assistant/message`` 才是权威文本，桥接时跳过。

限制：仅探测默认位置 ``~/.dsh``；``$DSH_HOME`` 自定义位置暂不识别
（探测表是静态 home 相对路径，env 覆盖会破坏测试与多用户隔离，待后续
提案单独处理）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable, Optional

from xskill.ecosystems._shared import (
    EcosystemSpec,
    JsonlIngester,
    _install_all_with,
    _install_skill_into,
)

logger = logging.getLogger("xskill.ecosystems")


# ─────────────────────────────────────────────────────────────────
# Path helpers
# ─────────────────────────────────────────────────────────────────


def _dsh_sessions_path(home: Path) -> Path:
    """dsh session 根目录：``<home>/.dsh/sessions``。

    实际文件在 ``<this>/--<normalized-cwd>--/<encoded-id>/session.jsonl``
    （明文模式）或 ``session.jsonl.zstd``（默认压缩模式，本期不桥接）。
    """
    return home / ".dsh" / "sessions"


def _dsh_skills_path(home: Path) -> Path:
    """dsh user-dsh skill 根目录：``<home>/.dsh/skills``。

    每个 skill 落到 ``<this>/<name>/SKILL.md``；dsh 的 skill-filesystem
    provider 默认 ``watchFollowSymlinks: true``，symlink 安装可被其
    watcher 正常发现。
    """
    return home / ".dsh" / "skills"


# ─────────────────────────────────────────────────────────────────
# Installer
# ─────────────────────────────────────────────────────────────────


def install_to_deepseek_harness(
    skill_path: Path | str,
    target_root: Path | str | None = None,
    side: str = "main",
) -> Path:
    """把一个 skill 装到 ``<target_root>/.dsh/skills/<name>``。

    走与 ``install_to_claude_code`` / ``install_to_cursor`` 同形的 per-skill
    symlink-first 三阶 fallback（POSIX symlink → Windows junction → copy）。
    专用 ``~/.dsh/skills`` 而不是共享的 ``~/.agents/skills``：后者已被
    Codex / OpenCode / OpenClaw 使用，与 dsh 的 user-agents 扫描（rank 500）
    重叠，共享目录的 reverse-sync 语义会互相干扰（见 #144 / #35）。
    """
    root = Path(target_root) if target_root else Path.home()
    return _install_skill_into(
        Path(skill_path),
        _dsh_skills_path(root),
        side,
        ecosystem_label="deepseek_harness",
    )


def install_all_to_deepseek_harness(
    skill_dir: Path | str,
    target_root: Path | str | None = None,
    names: Iterable[str] | None = None,
) -> list[Path]:
    """Install every skill under ``skill_dir`` (each subdir = one skill) to
    DeepSeek Harness's user skill root (``<target_root>/.dsh/skills``). If
    ``names`` is given, restrict to those.
    """
    return _install_all_with(
        install_to_deepseek_harness, skill_dir, target_root, names,
    )


# ─────────────────────────────────────────────────────────────────
# dsh-specific trajectory helpers
# ─────────────────────────────────────────────────────────────────


def _dsh_session_id_from_path(jsonl_path: Path) -> str:
    """``…/<encoded-id>/session.jsonl`` → ``<encoded-id>``。

    dsh 的 transcript 文件名固定为 ``session.jsonl``，session 标识在父目录名
    （injectively escaped 的 session id）。"""
    return jsonl_path.parent.name


def _read_cwd_from_dsh_jsonl(content: str) -> str:
    """从首个 SessionHeader 行读 ``cwd``。

    首个逻辑行是 ``{"type": "session", "id": …, "cwd": …, …}``；``cwd`` 可选
    （无 cwd 的 session 落在 ``_no-cwd/`` 项目目录），缺失返回空串。"""
    for raw_line in content.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            header = json.loads(raw_line)
        except json.JSONDecodeError:
            return ""
        if isinstance(header, dict) and header.get("type") == "session":
            return str(header.get("cwd") or "")
        return ""
    return ""


# ─────────────────────────────────────────────────────────────────
# Ecosystem spec
# ─────────────────────────────────────────────────────────────────

DSH_SPEC = EcosystemSpec(
    name="deepseek_harness",
    source_kind="jsonl",
    sessions_path=_dsh_sessions_path,
    # --<normalized-cwd>--/<encoded-id>/session.jsonl；仅明文模式。
    # session.jsonl.zstd 不在 glob 内 —— zstd 帧序列不能按行读，本期明确
    # 不支持（README 有说明），glob 收窄即天然跳过。
    sessions_glob="*/*/session.jsonl",
    session_id_from_path=_dsh_session_id_from_path,
    cwd_from_content=_read_cwd_from_dsh_jsonl,
    adapter_format="deepseek_harness_session_jsonl",
    traj_id_prefix="traj_dsh_",
    skills_install_path=_dsh_skills_path,
    label="deepseek_harness",
)


# ─────────────────────────────────────────────────────────────────
# Trajectory adapter
# ─────────────────────────────────────────────────────────────────

# 打包行标签（``packChunks`` 写出的 chunk-run 压缩行）与重放事件：装配后的
# ``assistant/message`` 才是权威文本，这些行跳过不进 timeline。
_REPLAY_ROW_TAGS = {"text-chunks", "reasoning-chunks", "tool-call-chunks"}


def _text_from_message_content(content) -> str:
    """Message.content → 纯文本。兼容 string 与 ContentBlock 数组两种形态。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for block in content:
            if isinstance(block, str):
                chunks.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if text:
                    chunks.append(str(text))
        return "\n".join(chunks)
    return ""


def _adapt_deepseek_harness_session_jsonl(
    content: str, metadata: dict,
) -> tuple[str, dict]:
    """Convert a DeepSeek Harness plaintext session JSONL to markdown + metadata.

    行格式（``@deepseek-ai/dsh-session-persistence-jsonl``）：

    - 首行 SessionHeader：``{"type": "session", "version", "id", "cwd"?, …}``
    - 事件行：``{"type": "<event-type>", "seq", "time", "data": …}``，取
      ``user/message``（data 即 UserMessage）、``assistant/message``
      （data.message 为 AssistantMessage）、``tool/call``（data.name /
      data.arguments）进 timeline；``assistant/chunk``、``turn/*``、
      ``step/*``、打包行等其余类型跳过。
    """
    timeline: list[dict] = []
    tool_names: list[str] = []
    first_user_query = ""
    session_id = ""
    cwd = ""
    agent_preset = ""
    t = 0

    for raw_line in content.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue

        rtype = record.get("type")

        if rtype == "session":
            session_id = str(record.get("id") or "")
            cwd = str(record.get("cwd") or "")
            agent_preset = str(record.get("agentPreset") or "")
            continue
        if rtype in _REPLAY_ROW_TAGS:
            continue

        data = record.get("data")
        if not isinstance(data, dict):
            continue

        role = ""
        body = ""
        if rtype == "user/message":
            role = "user"
            body = _text_from_message_content(data.get("content"))
        elif rtype == "assistant/message":
            role = "assistant"
            message = data.get("message")
            if isinstance(message, dict):
                body = _text_from_message_content(message.get("content"))
        elif rtype == "tool/call":
            role = "assistant"
            name = str(data.get("name") or "tool")
            if name not in tool_names:
                tool_names.append(name)
            body = f"[tool_call: {name}]"
        # 其余类型（turn/* step/* tool/result assistant/chunk llm/* …）
        # 是结构 / 重放 / 结果数据，不进 timeline。

        body = body.strip()
        if not body:
            continue
        if role == "user" and not first_user_query:
            first_user_query = body[:500]
        timeline.append({"t": t, "role": role, "content": body[:2000]})
        t += 1

    lines: list[str] = ["# DeepSeek Harness Trajectory", ""]
    if first_user_query:
        lines.append("## Initial Query")
        lines.append("")
        lines.append(first_user_query)
        lines.append("")
    for entry in timeline:
        lines.append("## User" if entry["role"] == "user" else "## Assistant")
        lines.append("")
        lines.append(entry["content"])
        lines.append("")
    md = "\n".join(lines)

    meta = dict(metadata)
    meta.setdefault("source", "deepseek_harness_session_jsonl")
    meta.setdefault("category", "deepseek_harness_session")
    if session_id:
        meta.setdefault("session_id", session_id)
    if cwd:
        meta.setdefault("cwd", cwd)
    if agent_preset:
        meta.setdefault("agent_preset", agent_preset)
    meta["timeline"] = timeline
    meta["tool_names"] = tool_names
    meta["total_turns"] = len(timeline)
    if first_user_query:
        meta.setdefault("query", first_user_query)

    return md, meta


# ─────────────────────────────────────────────────────────────────
# Ingest — bridge dsh plaintext session JSONL into xskill traj dir
# ─────────────────────────────────────────────────────────────────


def ingest_deepseek_harness_sessions(
    target_traj_dir: Path | str,
    *,
    home_root: Path | str | None = None,
    seen_sessions: Optional[set[str]] = None,
) -> list[dict]:
    """Bridge DeepSeek Harness plaintext session JSONLs into xskill's
    trajectory directory.

    Scans ``<home_root>/.dsh/sessions/*/*/session.jsonl``（仅
    ``compression: 'none'`` 的明文 transcript；默认的 ``session.jsonl.zstd``
    本期不解码）and submits any session whose encoded-id directory is not in
    ``seen_sessions`` as a new trajectory under ``target_traj_dir``.
    """
    return JsonlIngester(DSH_SPEC).scan_and_bridge(
        target_traj_dir=Path(target_traj_dir),
        home_root=Path(home_root) if home_root else None,
        seen_sessions=seen_sessions,
    )
