"""
opencode-fixture-generator.py — 生成符合 OpenCode schema 的真实 SQLite fixture

来源：1:1 复现 ``~/learn/opencode/packages/opencode/src/session/session.sql.ts``
里 drizzle ORM 定义的 ``session`` / ``message`` / ``part`` / ``project`` 四表
schema（main agent 已读完源码 + 本机 ``~/.local/share/opencode/opencode.db``
实测 ``.schema`` 双向确认）。

用法：
    cd tests/fixtures/opencode && python3 generate.py sample.db
    # 产出 sample.db 入仓（脱敏版，1 session / 2 message / 8 part）

OpenCode 数据模型要点：``message`` 表只存信封（role / model / cost / tokens），
**真实对话内容在 ``part`` 表**——一条 message 对应多条 part，type 有 text /
reasoning / tool / step-start / step-finish / patch。xskill 的 SqliteIngester
读 message + part 还原成 ``## User`` / ``## Assistant`` / ``## Tool Call`` 轨迹。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


# 各表 DDL（来自 ~/.local/share/opencode/opencode.db 实测 .schema）
_SESSION_DDL = """
CREATE TABLE `session` (
    `id` text PRIMARY KEY,
    `project_id` text NOT NULL,
    `parent_id` text,
    `slug` text NOT NULL,
    `directory` text NOT NULL,
    `title` text NOT NULL,
    `version` text NOT NULL,
    `share_url` text,
    `summary_additions` integer,
    `summary_deletions` integer,
    `summary_files` integer,
    `summary_diffs` text,
    `revert` text,
    `permission` text,
    `time_created` integer NOT NULL,
    `time_updated` integer NOT NULL,
    `time_compacting` integer,
    `time_archived` integer,
    `workspace_id` text,
    `path` text
)
"""

_MESSAGE_DDL = """
CREATE TABLE `message` (
    `id` text PRIMARY KEY,
    `session_id` text NOT NULL,
    `time_created` integer NOT NULL,
    `time_updated` integer NOT NULL,
    `data` text NOT NULL
)
"""

_PART_DDL = """
CREATE TABLE `part` (
    `id` text PRIMARY KEY,
    `message_id` text NOT NULL,
    `session_id` text NOT NULL,
    `time_created` integer NOT NULL,
    `time_updated` integer NOT NULL,
    `data` text NOT NULL,
    CONSTRAINT `fk_part_message_id_message_id_fk`
        FOREIGN KEY (`message_id`) REFERENCES `message`(`id`) ON DELETE CASCADE
)
"""

_PROJECT_DDL = """
CREATE TABLE `project` (
    `id` text PRIMARY KEY,
    `directory` text NOT NULL,
    `time_created` integer NOT NULL
)
"""

_INDEXES = """
CREATE INDEX `message_session_time_created_id_idx`
    ON `message` (`session_id`, `time_created`, `id`);
CREATE INDEX `part_session_idx` ON `part` (`session_id`);
CREATE INDEX `part_message_id_id_idx` ON `part` (`message_id`, `id`)
"""


def write_sample_fixture(out_path: Path) -> Path:
    """生成一份给 xskill OpenCode adapter 单测用的 SQLite fixture。

    Deterministic IDs / timestamps，让断言稳定可复现。

    内容：
    - 1 project（cwd = /tmp/opencode-test-workspace）
    - 1 session（指向 project，agent=build，model=deepseek-v4-flash）
    - 2 message（user + assistant），message.data 是 OpenCode 的信封 JSON
    - 8 part：user 1 条 text；assistant 7 条覆盖全部 type
      （step-start / reasoning / tool[completed] / tool[error] / text /
      patch / step-finish）
    """
    if out_path.exists():
        out_path.unlink()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(out_path))
    try:
        conn.executescript(
            ";".join([_SESSION_DDL, _MESSAGE_DDL, _PART_DDL, _PROJECT_DDL,
                      _INDEXES])
        )

        # Deterministic IDs
        proj_id = "prj_aaaaaaaaaaaa"
        sess_id = "ses_bbbbbbbbbbbb"
        msg_user_id = "msg_user_111"
        msg_assist_id = "msg_assist_222"

        # Deterministic timestamps (epoch ms in OpenCode)
        ts_proj = 1777530000000
        ts_sess_create = 1777530036000
        ts_sess_update = 1777530090000
        ts_msg_user = 1777530036589
        ts_msg_assist = 1777530080123

        conn.execute(
            "INSERT INTO project (id, directory, time_created) VALUES (?, ?, ?)",
            (proj_id, "/tmp/opencode-test-workspace", ts_proj),
        )
        conn.execute(
            """INSERT INTO session
               (id, project_id, parent_id, slug, directory, title, version,
                time_created, time_updated)
               VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)""",
            (sess_id, proj_id, "quiet-harbor", "/tmp/opencode-test-workspace",
             "Hello world task", "0.10.0", ts_sess_create, ts_sess_update),
        )

        def _msg(mid: str, ts: int, data: dict) -> None:
            conn.execute(
                "INSERT INTO message (id, session_id, time_created, "
                " time_updated, data) VALUES (?, ?, ?, ?, ?)",
                (mid, sess_id, ts, ts,
                 json.dumps(data, ensure_ascii=False, separators=(",", ":"))),
            )

        def _part(pid: str, mid: str, ts: int, data: dict) -> None:
            conn.execute(
                "INSERT INTO part (id, message_id, session_id, time_created, "
                " time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
                (pid, mid, sess_id, ts, ts,
                 json.dumps(data, ensure_ascii=False, separators=(",", ":"))),
            )

        # ── user message + 1 个 text part ──
        _msg(msg_user_id, ts_msg_user, {
            "role": "user",
            "time": {"created": ts_msg_user},
            "agent": "build",
            "model": {"providerID": "deepseek", "modelID": "deepseek-v4-flash"},
            "summary": {"diffs": []},
        })
        _part("prt_u_0", msg_user_id, ts_msg_user + 100,
              {"type": "text", "text": "列出所有 skill"})

        # ── assistant message + 7 个 part（覆盖全部 type）──
        _msg(msg_assist_id, ts_msg_assist, {
            "parentID": msg_user_id,
            "role": "assistant",
            "mode": "build",
            "agent": "build",
            "path": {"cwd": "/tmp/opencode-test-workspace", "root": "/"},
            "cost": 0.00165858,
            "tokens": {"total": 11710, "input": 9800, "output": 1910},
        })
        _part("prt_a_0", msg_assist_id, ts_msg_assist + 100,
              {"type": "step-start"})
        _part("prt_a_1", msg_assist_id, ts_msg_assist + 200,
              {"type": "reasoning",
               "text": "用户想列出 skill，我先调 skill 工具。"})
        _part("prt_a_2", msg_assist_id, ts_msg_assist + 300, {
            "type": "tool", "tool": "skill", "callID": "call_ok",
            "state": {"status": "completed",
                      "input": {"name": "agent-onboarding"},
                      "output": "skill A / skill B / skill C"}})
        _part("prt_a_3", msg_assist_id, ts_msg_assist + 400, {
            "type": "tool", "tool": "edit", "callID": "call_err",
            "state": {"status": "error",
                      "input": {"filePath": "/tmp/x"},
                      "error": "No changes to apply: oldString and "
                               "newString are identical."}})
        _part("prt_a_4", msg_assist_id, ts_msg_assist + 500,
              {"type": "text", "text": "已为你列出 3 个 skill。"})
        _part("prt_a_5", msg_assist_id, ts_msg_assist + 600,
              {"type": "patch", "hash": "abc123", "files": ["/tmp/x/a.py"]})
        _part("prt_a_6", msg_assist_id, ts_msg_assist + 700,
              {"type": "step-finish", "reason": "stop", "cost": 0.001,
               "tokens": {"total": 100}})

        conn.commit()
    finally:
        conn.close()

    return out_path


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "./sample.db")
    path = write_sample_fixture(out)
    print(f"wrote: {path}")
    print(f"size: {path.stat().st_size} bytes")
    conn = sqlite3.connect(str(path))
    print("rows:")
    for tbl in ("project", "session", "message", "part"):
        n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"  {tbl}: {n}")
    conn.close()
