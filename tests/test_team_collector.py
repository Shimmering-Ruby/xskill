import os
import time
from pathlib import Path

from xskill.team.collector import TeamCollector


def test_pending_returns_quiet_unuploaded_md(tmp_path):
    outbox = tmp_path / "outbox"
    bridge = outbox / "cc_sessions"
    bridge.mkdir(parents=True)
    # 一个"静默已久"的 traj_*.md
    old = bridge / "traj_cc_x_001.md"
    old.write_text("# old body", encoding="utf-8")
    old_time = time.time() - 600
    os.utime(old, (old_time, old_time))
    # 一个"刚改过"的 traj_*.md
    fresh = bridge / "traj_cc_x_002.md"
    fresh.write_text("# fresh", encoding="utf-8")

    col = TeamCollector(outbox_dir=outbox, cursor_path=tmp_path / "cursor.json",
                        quiet_seconds=180)
    pending = col.pending()
    ids = {p.traj_id for p in pending}
    assert "traj_cc_x_001" in ids        # 静默够久
    assert "traj_cc_x_002" not in ids    # 太新，可能还在写


def test_mark_uploaded_excludes_next_time(tmp_path):
    outbox = tmp_path / "outbox"
    bridge = outbox / "cc_sessions"
    bridge.mkdir(parents=True)
    md = bridge / "traj_cc_x_001.md"
    md.write_text("# body", encoding="utf-8")
    old = time.time() - 600
    os.utime(md, (old, old))

    col = TeamCollector(outbox_dir=outbox, cursor_path=tmp_path / "cursor.json",
                        quiet_seconds=180)
    p = col.pending()[0]
    col.mark_uploaded(p.traj_id, p.sha256)
    assert col.pending() == []           # 已上传，不再吐

    # 内容变了 → 重新吐（增量）
    md.write_text("# body changed", encoding="utf-8")
    os.utime(md, (old, old))
    assert len(col.pending()) == 1


def test_redaction_applied_to_content(tmp_path):
    outbox = tmp_path / "outbox"
    bridge = outbox / "cc_sessions"
    bridge.mkdir(parents=True)
    md = bridge / "traj_cc_x_001.md"
    md.write_text('key = "sk-abcdEFGH1234567890wxyz"', encoding="utf-8")
    old = time.time() - 600
    os.utime(md, (old, old))
    col = TeamCollector(outbox_dir=outbox, cursor_path=tmp_path / "cursor.json",
                        quiet_seconds=180)
    p = col.pending()[0]
    assert "sk-abcdEFGH1234567890wxyz" not in p.content
    assert "[REDACTED]" in p.content
