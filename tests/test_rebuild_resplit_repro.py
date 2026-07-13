"""Hermetic reproduction of the "rebuild produces nothing" bug.

Drives the FULL cycle in-process with a stub LLM:
  1. split -> cluster -> done   (first pass: atoms + skill produced)
  2. reset_trajectories()       (the `xskill rebuild --force` core)
  3. re-scan the watcher        (SHOULD re-split and yield > 0 atoms)

Covers both single-machine (server_mode=False) and TEAM SERVER
(server_mode=True), since the user deploys `serve --server`.

ROOT CAUSE (proven by test_root_cause_migrate_backfill below):
`reset_trajectories` correctly sets status='discovered' (+ zeros offset,
deletes atom files & index.pkl). But it leaves `has_embedding=1` on the row.
The very next `registry.get_connection()` runs `_migrate()`, whose backfill
   UPDATE trajectories SET status='indexed'
    WHERE has_embedding=1 AND (status IS NULL OR status='discovered')
silently flips the just-reset row back to 'indexed'. The watcher only submits
split for status in ('discovered','updated') -- never 'indexed' -- so split
never re-runs, tasks_extracted stays 0, no new atoms, no new skills.

Run:
    python3.11 -m pytest tests/test_rebuild_resplit_repro.py -q
"""
from __future__ import annotations

import sqlite3
import time

from xskill.pipeline.atom import AtomTaskStore
from xskill.pipeline.registry import (
    register_dir,
    discover_trajectories,
    update_traj_status,
    mark_indexed,
    get_connection,
    get_status_counts,
    get_trajs_by_status,
    reset_trajectories,
)
from xskill.pipeline.runner import DirectoryWatcher

from tests.test_task_agent import _TRAJ_MD, _AutoSplitLLM
from tests.test_watcher_atom import _StubAgno
from tests.test_atom_task_store import _FakeEmbed


# ════════════════════════════════════════════════════════════════════
# Full-cycle reproduction via the real DirectoryWatcher
# ════════════════════════════════════════════════════════════════════

def _make_watcher(*, db, wd, skill_dir, store, server_mode):
    return DirectoryWatcher(
        llm=_AutoSplitLLM(),
        embed_client=_FakeEmbed(),
        config={"llm": {"base_url": "x", "model": "y", "api_key": "z"}},
        skill_dir=skill_dir,
        poll_interval=0.0,
        max_concurrent=4,
        db_path=db,
        store=store,
        agno_agent_factory=_StubAgno,
        home_root=wd.parent,
        server_mode=server_mode,
    )


def _wd_id_of(db):
    from xskill.pipeline.registry import list_watch_dirs
    return list_watch_dirs(db_path=db)[0]["id"]


def _drive_until_done(watcher, db, fname, rounds=30):
    for _ in range(rounds):
        watcher._scan_once()
        for _ in range(40):
            if not watcher._futures:
                break
            time.sleep(0.05)
            watcher._harvest()
        if fname in get_trajs_by_status(_wd_id_of(db), "done", db_path=db):
            return True
    return False


def _full_cycle(tmp_path, *, server_mode, eco):
    db = tmp_path / "reg.db"
    wd = tmp_path / "wd"
    wd.mkdir()
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (wd / "traj_x.md").write_text(_TRAJ_MD, encoding="utf-8")

    register_dir(wd, label="client-a", ecosystem=eco, db_path=db)
    store = AtomTaskStore(root=wd)
    watcher = _make_watcher(db=db, wd=wd, skill_dir=skill_dir, store=store,
                            server_mode=server_mode)

    # Pass 1
    assert _drive_until_done(watcher, db, "traj_x.md"), "first pass -> done"
    assert len(store.list_by_traj("traj_x")) == 2

    # Pass 2: rebuild --force core
    assert len(reset_trajectories(eco=eco, db_path=db)) == 1
    assert store.list_by_traj("traj_x") == [], "reset deletes atom files"

    # Pass 3: re-scan -> SHOULD re-split
    _drive_until_done(watcher, db, "traj_x.md")
    return db, store, watcher


def test_rebuild_resplits_single_machine(tmp_path):
    """EXPECTED to pass once bug fixed. Currently FAILS (0 re-split atoms)."""
    db, store, _ = _full_cycle(tmp_path, server_mode=False, eco="claude_code")
    second = store.list_by_traj("traj_x")
    assert len(second) > 0, (
        "REBUILD BUG: re-scan after reset produced 0 atoms; "
        f"status counts={get_status_counts(db_path=db)} "
        "(row flipped back to 'indexed' by _migrate backfill -> "
        "watcher never re-submits split)"
    )
    assert len(second) == 2


def test_rebuild_resplits_server_mode(tmp_path):
    """TEAM SERVER path: same bug -- split/cluster submission is mode-agnostic."""
    db, store, _ = _full_cycle(tmp_path, server_mode=True, eco="team_client")
    second = store.list_by_traj("traj_x")
    assert len(second) > 0, (
        "REBUILD BUG (server mode): re-scan after reset produced 0 atoms; "
        f"status counts={get_status_counts(db_path=db)}"
    )
    assert len(second) == 2


# ════════════════════════════════════════════════════════════════════
# Root-cause isolation: _migrate() backfill flips reset rows to 'indexed'
# ════════════════════════════════════════════════════════════════════

def test_root_cause_migrate_backfill_undoes_reset(tmp_path):
    """Minimal, watcher-free proof of the exact mechanism.

    reset_trajectories sets status='discovered' but leaves has_embedding=1.
    The next get_connection() -> _migrate() backfill silently re-flips it to
    'indexed'. This is the single defect that makes rebuild a no-op.
    """
    db = tmp_path / "reg.db"
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "traj_x.md").write_text("## User\nhi\n", encoding="utf-8")
    wid = register_dir(wd, ecosystem="claude_code", db_path=db)
    discover_trajectories(wid, wd, db_path=db)

    # Simulate a finished trajectory: indexed (has_embedding=1) -> done.
    mark_indexed(wid, "traj_x.md", db_path=db)
    update_traj_status(wid, "traj_x.md", "done", db_path=db)

    reset_trajectories(eco="claude_code", db_path=db)

    # Read with RAW sqlite (no _migrate) -> reset value is intact.
    raw = sqlite3.connect(str(db))
    raw.row_factory = sqlite3.Row
    status_no_migrate = raw.execute(
        "SELECT status FROM trajectories").fetchone()["status"]
    raw.close()
    assert status_no_migrate == "discovered", (
        "reset_trajectories itself writes 'discovered' correctly"
    )

    # Any subsequent registry op opens get_connection() -> runs _migrate().
    get_connection(db).close()

    raw = sqlite3.connect(str(db))
    raw.row_factory = sqlite3.Row
    status_after_migrate = raw.execute(
        "SELECT status FROM trajectories").fetchone()["status"]
    raw.close()

    # 回归断言（修复后）：_migrate 的回填已改成"只在首次补 status 列时跑一次",
    # 不再每次连接把活的 'discovered' 行打回 'indexed'。reset 后状态必须**保持
    # discovered**，watcher 才会重新派 split。
    assert status_after_migrate == "discovered", (
        "回归：reset_trajectories 写的 'discovered' 不应被 _migrate 回填覆盖"
        f"（实得 {status_after_migrate!r}）——见 registry._migrate 的 status_was_missing 闸"
    )


# ── 补 guard 测试：迁移闸 + reset 清标志 ────────────────────────────

def test_legacy_migration_backfills_status_once(tmp_path):
    """旧库(无 status 列)首次连接 → 一次性回填生效：has_embedding=1 → indexed。
    确认把回填改成"只跑一次"没有破坏对真·老库的迁移。"""
    db = tmp_path / "old.db"
    raw = sqlite3.connect(str(db))
    raw.execute("CREATE TABLE watch_dirs (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " path TEXT UNIQUE, label TEXT)")
    raw.execute("CREATE TABLE trajectories (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " watch_dir_id INTEGER, filename TEXT, has_meta INTEGER DEFAULT 0,"
                " has_embedding INTEGER DEFAULT 0, UNIQUE(watch_dir_id, filename))")
    raw.execute("INSERT INTO watch_dirs (path) VALUES ('/x')")
    raw.execute("INSERT INTO trajectories (watch_dir_id, filename, has_embedding)"
                " VALUES (1, 'traj_a.md', 1)")
    raw.execute("INSERT INTO trajectories (watch_dir_id, filename, has_meta)"
                " VALUES (1, 'traj_b.md', 1)")
    raw.commit()
    raw.close()

    conn = get_connection(db)  # 触发 _migrate：补 status 列 + 一次性回填
    rows = dict(conn.execute(
        "SELECT filename, status FROM trajectories").fetchall())
    conn.close()
    assert rows["traj_a.md"] == "indexed"     # has_embedding=1 → indexed
    assert rows["traj_b.md"] == "meta_done"   # has_meta=1,无 embedding → meta_done


def test_reset_clears_embedding_meta_flags(tmp_path):
    """reset_trajectories 必须清掉 has_embedding/has_meta——否则 _migrate 的回填
    谓词仍可能命中(belt-and-suspenders)。"""
    db = tmp_path / "reg.db"
    d = tmp_path / "ng_sessions"
    d.mkdir()
    (d / "traj_x.md").write_text("## User\nhi\n", encoding="utf-8")
    wid = register_dir(d, ecosystem="ngagent", db_path=db)
    discover_trajectories(wid, d, db_path=db)
    mark_indexed(wid, "traj_x.md", db_path=db)          # 置 has_embedding=1
    update_traj_status(wid, "traj_x.md", "done", db_path=db)

    reset_trajectories(eco="ngagent", db_path=db)

    conn = get_connection(db)
    row = conn.execute(
        "SELECT has_embedding, has_meta, status FROM trajectories"
        " WHERE filename='traj_x.md'").fetchone()
    conn.close()
    assert row["has_embedding"] == 0 and row["has_meta"] == 0
    assert row["status"] == "discovered"   # 且没被回填打回
