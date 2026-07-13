"""
pipeline/registry.py -- SQLite 路径注册表 + Registry 实体类
============================================================

管理 ``~/.xskill/registry.db``，**只存路径和状态**，不存内容。

两张表：

- ``watch_dirs``   用户注册的待监听目录
- ``trajectories`` 每条轨迹文件的发现/索引状态

模块底部的 ``Registry`` 类把上面的模块函数包装为 OOP 接口；所有
watch_dir + trajectory 反查走这个类。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from xskill.config import get_registry_db_path
from xskill.types import WatchDir

logger = logging.getLogger("xskill.registry")


class TrajectoryStatus(str, Enum):
    DISCOVERED = "discovered"
    UPDATED = "updated"
    SPLITTING = "splitting"
    SPLIT_DONE = "split_done"
    INDEXED = "indexed"
    DONE = "done"
    FILTERED = "filtered"
    ERROR = "error"
    CLUSTERING = "clustering"
    META_DONE = "meta_done"


class ProcessAction(str, Enum):
    CLUSTERED = "clustered"
    NOT_FIT = "not_fit"

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS watch_dirs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    path       TEXT UNIQUE NOT NULL,
    label      TEXT DEFAULT '',
    auto_index INTEGER DEFAULT 1,
    ecosystem  TEXT DEFAULT 'manual',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trajectories (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_dir_id  INTEGER NOT NULL REFERENCES watch_dirs(id) ON DELETE CASCADE,
    filename      TEXT NOT NULL,
    has_meta      INTEGER DEFAULT 0,
    has_embedding INTEGER DEFAULT 0,
    status        TEXT DEFAULT 'discovered',
    process_action TEXT,
    interest_fingerprint TEXT,
    skill_generated TEXT,
    skill_used    TEXT,
    canary_side   TEXT,
    source_model  TEXT,
    source_harness TEXT,
    user_key      TEXT DEFAULT '',
    ux_score      REAL,
    error_msg     TEXT,
    retry_count   INTEGER DEFAULT 0,
    file_mtime    REAL DEFAULT 0,
    discovered_at TEXT DEFAULT (datetime('now')),
    indexed_at    TEXT,
    updated_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(watch_dir_id, filename)
);

CREATE TABLE IF NOT EXISTS llm_usage (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT DEFAULT (datetime('now')),
    step         TEXT,
    model        TEXT,
    prompt       INTEGER DEFAULT 0,
    completion   INTEGER DEFAULT 0,
    total        INTEGER DEFAULT 0,
    cost_usd     REAL DEFAULT 0,
    price_source TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_ts ON llm_usage(ts);

-- 埋点(instrumentation,在代码里插记录点):三类事件,供看板算衍生率 --
CREATE TABLE IF NOT EXISTS recommendation_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT DEFAULT (datetime('now')),
    client_id TEXT,
    skill     TEXT,
    side      TEXT,          -- main / staging
    bucket    TEXT           -- ranked / recommended
);
CREATE INDEX IF NOT EXISTS idx_reco_skill ON recommendation_log(skill);

CREATE TABLE IF NOT EXISTS atom_adoption (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT DEFAULT (datetime('now')),
    atom_id     TEXT,
    skill       TEXT,
    weightscore INTEGER,
    was_new     INTEGER       -- 1=首次加入 0=覆盖
);
CREATE INDEX IF NOT EXISTS idx_atom_adopt ON atom_adoption(atom_id);

CREATE TABLE IF NOT EXISTS canary_decision (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT DEFAULT (datetime('now')),
    skill           TEXT,
    action          TEXT,     -- promoted / rejected / timeout_discarded
    main_avg        REAL,
    staging_avg     REAL,
    main_samples    INTEGER,
    staging_samples INTEGER,
    age_days        REAL
);

-- P2-2.4 控制面:用户/全局 skill 偏好(pinned|blocked)。
-- user_key='*global*' 为全局 pin/屏蔽(admin 设);其余为 user_name(D5)。
-- 超量校验在写入侧(D8),sync 读路径永不因此报错。
CREATE TABLE IF NOT EXISTS skill_prefs (
    user_key   TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    pref       TEXT NOT NULL CHECK(pref IN ('pinned','blocked')),
    set_by     TEXT DEFAULT '',
    ts         TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (user_key, skill_name)
);

-- P2-2.4c skill 生命周期:retired=下线(停止分发/推荐,数据与 git 历史保留)。
-- 删除是物理动作不入此表;"在役"=无行。
CREATE TABLE IF NOT EXISTS skill_lifecycle (
    skill_name TEXT PRIMARY KEY,
    state      TEXT NOT NULL CHECK(state IN ('retired')),
    set_by     TEXT DEFAULT '',
    ts         TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS skill_trigger_eval (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT DEFAULT (datetime('now')),
    skill        TEXT,        -- skill slug
    version_sha  TEXT,        -- 评测时该 skill 的 main sha(首版/未提交可空)
    exp_id       TEXT,        -- .description_optimization 实验目录号
    train_score  REAL,        -- 中选描述在 train 集触发准确率
    test_score   REAL,        -- 中选描述在 held-out test 集触发准确率(选优依据)
    n_cases      INTEGER,     -- 合成 case 总数
    catalog_size INTEGER      -- 诱饵清单平均大小(竞争对手数)
);
CREATE INDEX IF NOT EXISTS idx_trig_skill ON skill_trigger_eval(skill);
"""


# 建表 + 迁移每进程每个 DB 只跑一次。get_connection 在热路径上被高频调用
# （record_usage 每条 embedding 一次、watcher 每次状态翻转一次），此前每次
# 都重放整份 _SCHEMA_SQL + 逐表 _migrate，既烧 CPU 又放大 DB 写锁竞争
# （2026-07-13 复盘：/sync 风暴期 82 个并发线程各自反复建表）。
_SCHEMA_READY: set[str] = set()
_SCHEMA_READY_LOCK = threading.Lock()


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """打开（或创建）注册表 DB。首次调用自动建表。"""
    if db_path is None:
        db_path = get_registry_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # DB 文件不存在（首次 / 被删重建）时无条件重放建表，不看缓存。
    fresh = not db_path.exists()
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    key = str(db_path.resolve())
    with _SCHEMA_READY_LOCK:
        ready = key in _SCHEMA_READY
    if fresh or not ready:
        conn.executescript(_SCHEMA_SQL)
        # Migrate existing DBs that lack new columns
        _migrate(conn)
        with _SCHEMA_READY_LOCK:
            _SCHEMA_READY.add(key)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns missing from older schema versions."""
    # ── trajectories ──
    cur = conn.execute("PRAGMA table_info(trajectories)")
    cols = {row[1] for row in cur.fetchall()}
    # status 列是否本次才补上——决定要不要跑下方那条历史状态回填(只该一次性)。
    status_was_missing = "status" not in cols
    migrations = [
        ("status", "TEXT DEFAULT 'discovered'"),
        ("process_action", "TEXT"),
        ("interest_fingerprint", "TEXT"),
        ("skill_generated", "TEXT"),
        ("ux_score", "REAL"),
        ("error_msg", "TEXT"),
        ("retry_count", "INTEGER DEFAULT 0"),
        ("updated_at", "TEXT"),
        ("process_log", "TEXT"),
        # v2: AtomTask 流水线状态
        ("tasks_extracted", "INTEGER DEFAULT 0"),
        ("last_offset", "INTEGER DEFAULT 0"),
        ("last_atom_id", "TEXT"),
        # 用户 agent 模型(批2,Issue #43 关联):discover 时从 .json sidecar 写入
        ("source_model", "TEXT"),
        # 用户 coding agent(harness):discover 时从 .json sidecar 的 harness 写入。
        # team server 据此按真实 coding agent 分组,替代把所有上传一律标 team_client。
        ("source_harness", "TEXT"),
        # P2-2.1 归因地基(D5):canonical 身份键=user_name。team_client 桶
        # discover 时从 watch_dirs.label(=sessions 桶目录名)写入;存量用
        # scripts/backfill_user_key.py 一次性回填。非 team 目录留空。
        ("user_key", "TEXT DEFAULT ''"),
    ]
    for col, typedef in migrations:
        if col not in cols:
            conn.execute(f"ALTER TABLE trajectories ADD COLUMN {col} {typedef}")

    # ── watch_dirs ──
    # ── recommendation_log ──（审计 P0-2：曝光去重根治注水）
    # 加 sha 列 + (client_id,skill,side,sha) 唯一索引；建索引前一次性清历史重复行
    # （每次 sync 每 slot 插一条的注水数据），只保留每组最早一条（保住首次曝光时间）。
    cur = conn.execute("PRAGMA table_info(recommendation_log)")
    reco_cols = {row[1] for row in cur.fetchall()}
    if "sha" not in reco_cols:
        conn.execute("ALTER TABLE recommendation_log ADD COLUMN sha TEXT DEFAULT ''")
    has_dedup_idx = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_reco_dedup'"
    ).fetchone()
    if not has_dedup_idx:
        conn.execute(
            "DELETE FROM recommendation_log WHERE id NOT IN ("
            " SELECT MIN(id) FROM recommendation_log"
            " GROUP BY client_id, skill, side, COALESCE(sha,''))"
        )
        conn.execute(
            "CREATE UNIQUE INDEX idx_reco_dedup ON"
            " recommendation_log(client_id, skill, side, sha)"
        )

    # ── canary_decision ──（D9：裁决可定位到 commit——进化图数据地基）
    cur = conn.execute("PRAGMA table_info(canary_decision)")
    cd_cols = {row[1] for row in cur.fetchall()}
    for col in ("main_sha", "staging_sha"):
        if col not in cd_cols:
            conn.execute(
                f"ALTER TABLE canary_decision ADD COLUMN {col} TEXT DEFAULT ''")

    cur = conn.execute("PRAGMA table_info(watch_dirs)")
    wd_cols = {row[1] for row in cur.fetchall()}
    if "ecosystem" not in wd_cols:
        conn.execute(
            "ALTER TABLE watch_dirs ADD COLUMN ecosystem TEXT DEFAULT 'manual'"
        )
        # 已有行历史上都是用户手动 register，标 'manual'
        conn.execute("UPDATE watch_dirs SET ecosystem='manual' WHERE ecosystem IS NULL")
    # Backfill status from has_meta/has_embedding —— **只在首次补 status 列时跑一次**。
    # 以前每次 get_connection 都跑这条,会把任何 status='discovered' 的**活行**
    # （rebuild 重置 / error 重试 / 僵尸清理刚翻回的）在下次连接时打回 'indexed'，
    # 导致 watcher 永不重拆（0 atom/0 skill 的真凶,见 test_rebuild_resplit_repro）。
    # 真·一次性迁移只该在那个加列的连接里跑,之后 status 是权威状态,不能再覆盖。
    if status_was_missing:
        conn.execute(
            "UPDATE trajectories SET status='indexed'"
            " WHERE has_embedding=1 AND (status IS NULL OR status='discovered')"
        )
        conn.execute(
            "UPDATE trajectories SET status='meta_done'"
            " WHERE has_meta=1 AND has_embedding=0 AND (status IS NULL OR status='discovered')"
        )
    conn.commit()


# ---------------------------------------------------------------------------
# LLM usage / cost accounting  (Issue #43)  —— 唯一"无家可归"数据的持久化
# ---------------------------------------------------------------------------

def record_usage(*, step: str, model: str, prompt: int, completion: int,
                 total: int, cost_usd: float, price_source: str,
                 db_path: Optional[Path] = None) -> None:
    """追加一条 LLM/embedding 调用的 token+成本记录。旁路 telemetry。"""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO llm_usage(step,model,prompt,completion,total,cost_usd,price_source)"
            " VALUES(?,?,?,?,?,?,?)",
            (step, model, int(prompt), int(completion), int(total),
             float(cost_usd), price_source),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 埋点(instrumentation)：三类事件的记录 + 聚合，供看板算衍生率
# 记录函数走旁路 telemetry——调用点用 try/except 包，记录失败绝不阻断管线。
# ---------------------------------------------------------------------------

GLOBAL_PREF_KEY = "*global*"


class PinQuotaExceeded(ValueError):
    """pinned 总量(用户+全局)超 slot 上限——写入侧拒绝(D8)。"""


def set_skill_pref(*, user_key: str, skill_name: str, pref: str, set_by: str,
                   max_pinned: Optional[int] = None,
                   db_path: Optional[Path] = None) -> None:
    """写入/覆盖一条偏好(pinned|blocked)。

    D8:``max_pinned`` 给定且 pref='pinned' 时在**写入侧**校验——该用户
    pinned + 全局 pinned 合计(去重,含本次)不得超过 max_pinned,超量抛
    ``PinQuotaExceeded``,超量状态根本不可能入库;全局 pin 则对**全员**
    逐一校验合计。sync 读路径永远不需要处理该错误。
    """
    if pref not in ("pinned", "blocked"):
        raise ValueError(f"pref 必须是 pinned|blocked,得到 {pref!r}")
    if not user_key or not skill_name:
        raise ValueError("user_key/skill_name 不能为空")
    conn = get_connection(db_path)
    try:
        if pref == "pinned" and max_pinned is not None:
            _check_pin_quota(conn, user_key=user_key, skill_name=skill_name,
                             max_pinned=max_pinned)
        conn.execute(
            "INSERT INTO skill_prefs(user_key,skill_name,pref,set_by)"
            " VALUES(?,?,?,?)"
            " ON CONFLICT(user_key,skill_name)"
            " DO UPDATE SET pref=excluded.pref, set_by=excluded.set_by,"
            "               ts=datetime('now')",
            (user_key, skill_name, pref, set_by),
        )
        conn.commit()
    finally:
        conn.close()


def _check_pin_quota(conn, *, user_key: str, skill_name: str,
                     max_pinned: int) -> None:
    """pinned 配额校验(含本次写入)。全局 pin 对全员合计逐一校验。"""
    def pinned_set(key: str) -> set:
        return {r["skill_name"] for r in conn.execute(
            "SELECT skill_name FROM skill_prefs WHERE user_key=? AND pref='pinned'",
            (key,),
        ).fetchall()}

    global_pinned = pinned_set(GLOBAL_PREF_KEY)
    if user_key == GLOBAL_PREF_KEY:
        global_pinned = global_pinned | {skill_name}
        user_keys = [r["user_key"] for r in conn.execute(
            "SELECT DISTINCT user_key FROM skill_prefs WHERE user_key!=?",
            (GLOBAL_PREF_KEY,),
        ).fetchall()]
        for uk in user_keys + [None]:  # None=只有全局 pin 的裸用户基线
            merged = global_pinned | (pinned_set(uk) if uk else set())
            if len(merged) > max_pinned:
                raise PinQuotaExceeded(
                    f"全局 pin {skill_name!r} 会使用户 {uk or '(baseline)'} 的"
                    f" pinned 合计 {len(merged)} 超过 slot 上限 {max_pinned}")
    else:
        merged = global_pinned | pinned_set(user_key) | {skill_name}
        if len(merged) > max_pinned:
            raise PinQuotaExceeded(
                f"pin {skill_name!r} 会使 pinned 合计(含全局) {len(merged)}"
                f" 超过 slot 上限 {max_pinned}")


def clear_skill_pref(*, user_key: str, skill_name: str,
                     db_path: Optional[Path] = None) -> bool:
    """删除一条偏好(pin 取消/屏蔽恢复)。返回是否真的删了行。"""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM skill_prefs WHERE user_key=? AND skill_name=?",
            (user_key, skill_name),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def prefs_for(user_key: str, *, db_path: Optional[Path] = None) -> list[dict]:
    """某 user_key(或 '*global*')的全部偏好行。"""
    conn = get_connection(db_path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT user_key, skill_name, pref, set_by, ts FROM skill_prefs"
            " WHERE user_key=? ORDER BY ts",
            (user_key,),
        ).fetchall()]
    finally:
        conn.close()


def effective_prefs(user_key: str, *, db_path: Optional[Path] = None) -> dict:
    """manifest 注入用的合并视图:{'pinned': [...有序...], 'blocked': set,
    'pin_meta': {skill: set_by}}。

    合并规则:全局行先于用户行(admin 全局 pin 排最前);同一 skill 用户行
    与全局行冲突时,**blocked 优先**(任何一侧屏蔽即不分发——全局屏蔽用户
    不能自行恢复;用户自己屏蔽的全局推荐仅对自己生效)。
    """
    conn = get_connection(db_path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT user_key, skill_name, pref, set_by, ts FROM skill_prefs"
            " WHERE user_key IN (?, ?) ORDER BY CASE user_key WHEN ? THEN 0"
            " ELSE 1 END, ts",
            (GLOBAL_PREF_KEY, user_key, GLOBAL_PREF_KEY),
        ).fetchall()]
    finally:
        conn.close()
    blocked = {r["skill_name"] for r in rows if r["pref"] == "blocked"}
    pinned: list[str] = []
    pin_meta: dict = {}
    for r in rows:
        if r["pref"] == "pinned" and r["skill_name"] not in blocked \
                and r["skill_name"] not in pinned:
            pinned.append(r["skill_name"])
            pin_meta[r["skill_name"]] = {
                "set_by": r["set_by"],
                "scope": "global" if r["user_key"] == GLOBAL_PREF_KEY else "user",
            }
    return {"pinned": pinned, "blocked": blocked, "pin_meta": pin_meta}


# ---------------------------------------------------------------------------
# P2-2.4c skill 生命周期
# ---------------------------------------------------------------------------

def retire_skill(*, skill_name: str, set_by: str,
                 db_path: Optional[Path] = None) -> None:
    """下线:停止分发与推荐,数据与 git 历史保留。幂等。"""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO skill_lifecycle(skill_name,state,set_by) VALUES(?,?,?)"
            " ON CONFLICT(skill_name) DO UPDATE SET state='retired',"
            " set_by=excluded.set_by, ts=datetime('now')",
            (skill_name, "retired", set_by),
        )
        conn.commit()
    finally:
        conn.close()


def unretire_skill(*, skill_name: str, db_path: Optional[Path] = None) -> bool:
    """恢复在役。返回是否真的有行被删。"""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM skill_lifecycle WHERE skill_name=?", (skill_name,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def retired_skills(*, db_path: Optional[Path] = None) -> set:
    conn = get_connection(db_path)
    try:
        return {r["skill_name"] for r in conn.execute(
            "SELECT skill_name FROM skill_lifecycle WHERE state='retired'"
        ).fetchall()}
    finally:
        conn.close()


def purge_skill_records(*, skill_name: str,
                        db_path: Optional[Path] = None) -> None:
    """删除 skill 时清掉它的 prefs/lifecycle 行——"删后同名 skill 再生"从
    零开始,不继承旧 pin/屏蔽/下线状态(语义拍板,见 tasks.md 2.4c)。
    评分/推荐等历史埋点(recommendation_log 等)保留作审计。"""
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM skill_prefs WHERE skill_name=?", (skill_name,))
        conn.execute("DELETE FROM skill_lifecycle WHERE skill_name=?", (skill_name,))
        conn.commit()
    finally:
        conn.close()


def record_recommendation(*, client_id: str, skill: str, side: str, bucket: str,
                          sha: str = "", db_path: Optional[Path] = None) -> None:
    """记一次"把 skill(某版本)推荐给某用户"——曝光事件。

    OR IGNORE 命中唯一索引 (client_id,skill,side,sha)：同一曝光对只记首次，
    反复 sync 不再膨胀触发率分母（审计 P0-2）。
    """
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO recommendation_log(client_id,skill,side,bucket,sha)"
            " VALUES(?,?,?,?,?)",
            (client_id, skill, side, bucket, sha or ""),
        )
        conn.commit()
    finally:
        conn.close()


def record_atom_adoption(*, atom_id: str, skill: str, weightscore: int,
                         was_new: bool, db_path: Optional[Path] = None) -> None:
    """记一次"某 atom 被聚进某 skill"。供算原子采纳率(采纳原子/总原子)。"""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO atom_adoption(atom_id,skill,weightscore,was_new) VALUES(?,?,?,?)",
            (atom_id, skill, int(weightscore), 1 if was_new else 0),
        )
        conn.commit()
    finally:
        conn.close()


def record_trigger_eval(*, skill: str, version_sha: Optional[str], exp_id: str,
                        train_score: float, test_score: float, n_cases: int,
                        catalog_size: int,
                        db_path: Optional[Path] = None) -> None:
    """记一次离线探针触发评测结果(中选描述的 train/test 触发准确率)。

    供看板展示 per-skill/版本"离线探针触发率"——区别于 mark_skill_used 记的
    线上真实使用频次,两者语义不同不可混。
    """
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO skill_trigger_eval"
            "(skill,version_sha,exp_id,train_score,test_score,n_cases,catalog_size)"
            " VALUES(?,?,?,?,?,?,?)",
            (skill, version_sha, exp_id, float(train_score), float(test_score),
             int(n_cases), int(catalog_size)),
        )
        conn.commit()
    finally:
        conn.close()


def trigger_eval_for_skill(skill: str, *, db_path: Optional[Path] = None) -> list:
    """取某 skill 的离线触发评测历史(按时间升序),供看板趋势图。"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT ts,version_sha,exp_id,train_score,test_score,n_cases,"
            "catalog_size FROM skill_trigger_eval WHERE skill=? ORDER BY id ASC",
            (skill,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def record_canary_decision(*, skill: str, action: str, main_avg: float,
                           staging_avg: float, main_samples: int,
                           staging_samples: int, age_days: float,
                           main_sha: str = "", staging_sha: str = "",
                           db_path: Optional[Path] = None) -> None:
    """记一次灰度裁决(promoted/rejected/timeout_discarded)。供算晋升率。

    ``main_sha``/``staging_sha`` 是裁决时两侧 HEAD——进化图据此把裁决挂到
    具体 commit（D9）；存量无 sha 的历史裁决在图上显式标"无法定位"。
    """
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO canary_decision(skill,action,main_avg,staging_avg,"
            "main_samples,staging_samples,age_days,main_sha,staging_sha)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (skill, action, main_avg, staging_avg, int(main_samples),
             int(staging_samples), float(age_days),
             main_sha or "", staging_sha or ""),
        )
        conn.commit()
    finally:
        conn.close()


def clear_rebuild_derived_state(
    *, registry_path: Optional[Path] = None,
) -> dict[str, int]:
    """Clear global derived dashboard state for ``xskill rebuild --force``.

    ``llm_usage`` is deliberately retained: it is cost accounting for already
    paid calls, not skill state derived from the current repository contents.
    """
    connection = get_connection(registry_path)
    deleted_counts: dict[str, int] = {}
    try:
        cursor = connection.execute("DELETE FROM recommendation_log")
        deleted_counts["recommendation_log"] = cursor.rowcount
        cursor = connection.execute("DELETE FROM atom_adoption")
        deleted_counts["atom_adoption"] = cursor.rowcount
        cursor = connection.execute("DELETE FROM canary_decision")
        deleted_counts["canary_decision"] = cursor.rowcount
        cursor = connection.execute("DELETE FROM skill_trigger_eval")
        deleted_counts["skill_trigger_eval"] = cursor.rowcount
        connection.commit()
        return deleted_counts
    finally:
        connection.close()


def usage_summary(db_path: Optional[Path] = None) -> dict:
    """跨重启的持久汇总:累计 token/$、今日 $、按 step / model 分解。"""
    conn = get_connection(db_path)
    try:
        tot = conn.execute(
            "SELECT COALESCE(SUM(total),0) t, COALESCE(SUM(cost_usd),0) c, COUNT(*) n"
            " FROM llm_usage"
        ).fetchone()
        # 本地日界（ts 存的是 UTC；'localtime','start of day','utc' = 本地零点的
        # UTC 表示）。旧口径 date('now') 是 UTC 日界，UTC+8 每天 08:00 前错位 8h。
        today = conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) FROM llm_usage"
            " WHERE ts >= datetime('now','localtime','start of day','utc')"
        ).fetchone()[0]
        estimated = conn.execute(
            "SELECT COUNT(*) FROM llm_usage WHERE price_source != 'config'"
        ).fetchone()[0] > 0
        by_step = [dict(r) for r in conn.execute(
            "SELECT step, SUM(total) tokens, SUM(cost_usd) cost, COUNT(*) calls"
            " FROM llm_usage GROUP BY step ORDER BY cost DESC"
        ).fetchall()]
        by_model = [dict(r) for r in conn.execute(
            "SELECT model, SUM(total) tokens, SUM(cost_usd) cost, COUNT(*) calls"
            " FROM llm_usage GROUP BY model ORDER BY cost DESC"
        ).fetchall()]
        return {
            "total_tokens": tot["t"], "total_usd": round(tot["c"], 6),
            "total_calls": tot["n"], "today_usd": round(today, 6),
            "estimated": estimated, "by_step": by_step, "by_model": by_model,
        }
    finally:
        conn.close()


def _sidecar_field(md_path: Path, key: str) -> Optional[str]:
    """从 traj_*.md 的同名 .json sidecar 读某字段（model / harness 等）。"""
    try:
        meta = json.loads(md_path.with_suffix(".json").read_text(encoding="utf-8"))
        v = meta.get(key)
        return str(v) if v else None
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def _sidecar_model(md_path: Path) -> Optional[str]:
    """从 traj_*.md 的同名 .json sidecar 读用户 agent 模型(meta['model'])。"""
    return _sidecar_field(md_path, "model")


# 每条轨迹的 coding agent(harness)推断：
#   1) 优先 client 上报的 source_harness（team 上传带）；
#   2) 缺失时,非 team_client 目录的 ecosystem 本身就是 harness
#      （本机 claude_code / codex / opencode sessions 目录）；
#   3) 都没有（团队上传但旧 client 没带 harness）→ 兜底标签（默认 'unknown'，
#      看板可经 config 的 dashboard.default_harness 改成别的已知 harness）。
# 这样既替代了"全是 team_client"的无信息分组,也不需要为本机轨迹回填。
# 兜底标签经 SQL 命名绑定参数 ``:hlabel`` 注入（自由字符串，防注入/引号问题）。
_HARNESS_EXPR = (
    "COALESCE(NULLIF(t.source_harness,''),"
    " CASE WHEN wd.ecosystem NOT IN ('team_client','manual')"
    " THEN wd.ecosystem END, :hlabel)"
)


def harness_share(db_path: Optional[Path] = None, *,
                  unknown_label: str = "unknown") -> list[dict]:
    """用户 coding agent(harness)分布(按轨迹数),供看板按 coding agent 显示占比。

    ``unknown_label``：harness 完全缺失时的归类桶，默认 'unknown'。看板层据
    config 传入 dashboard.default_harness 覆盖；canary/stats 等调用不传，保持
    'unknown' 语义不变。
    """
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            f"SELECT {_HARNESS_EXPR} AS harness, COUNT(*) AS trajs"
            " FROM trajectories t JOIN watch_dirs wd ON t.watch_dir_id=wd.id"
            f" GROUP BY {_HARNESS_EXPR} ORDER BY trajs DESC",
            {"hlabel": unknown_label},
        ).fetchall()
        total = sum(r["trajs"] for r in rows) or 1
        return [{"harness": r["harness"], "trajs": r["trajs"],
                 "pct": round(100 * r["trajs"] / total, 1)} for r in rows]
    finally:
        conn.close()


def model_share(db_path: Optional[Path] = None, *,
                unknown_label: str = "unknown") -> list[dict]:
    """用户 agent 模型分布(按轨迹数),供 server stats 显示占比。source_model 缺失
    → ``unknown_label``（默认 'unknown'，经命名参数 ``:mlabel`` 注入）。

    注意：canary 的 ``eligible_models`` 把 'unknown' 当“未归属、留在 main”的哨兵，
    所以那条路径必须用默认 'unknown'——只有看板展示层才传入 config 的覆盖值。
    """
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT COALESCE(source_model,:mlabel) AS model, COUNT(*) AS trajs"
            " FROM trajectories GROUP BY COALESCE(source_model,:mlabel)"
            " ORDER BY trajs DESC",
            {"mlabel": unknown_label},
        ).fetchall()
        total = sum(r["trajs"] for r in rows) or 1
        return [{"model": r["model"], "trajs": r["trajs"],
                 "pct": round(100 * r["trajs"] / total, 1)} for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Watch directory management
# ---------------------------------------------------------------------------

def register_dir(
    dir_path: str | Path,
    label: str = "",
    auto_index: bool = True,
    ecosystem: str = "manual",
    *,
    db_path: Optional[Path] = None,
) -> int:
    """注册一个目录。幂等：已存在则更新 label/auto_index/ecosystem，返回 id。

    ``ecosystem`` 标记目录来源，便于 list / search 时区分：
      - ``manual`` (默认)：用户手动 ``xskill registry add`` 注册的
      - ``claude_code``：daemon 启动时自动发现的 Claude Code 会话桥接目录
      - 未来：``codex``、``opencode`` 等
    """
    dir_path = str(Path(dir_path).resolve())
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO watch_dirs (path, label, auto_index, ecosystem)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(path) DO UPDATE SET"
            "   label=excluded.label,"
            "   auto_index=excluded.auto_index,"
            "   ecosystem=excluded.ecosystem",
            (dir_path, label, int(auto_index), ecosystem),
        )
        conn.commit()
        row = conn.execute("SELECT id FROM watch_dirs WHERE path=?", (dir_path,)).fetchone()
        return row["id"]
    finally:
        conn.close()


def unregister_dir(dir_path: str | Path, *, db_path: Optional[Path] = None) -> bool:
    """移除目录及其轨迹记录。返回 True 表示找到并删除。

    级联删轨迹的同时清对应 ``atom_adoption`` 行——否则采纳率分子留历史累计、
    分母被删小，比率虚高（审计 P1-7）。
    """
    dir_path = str(Path(dir_path).resolve())
    conn = get_connection(db_path)
    try:
        stems = [
            (r["filename"][:-3] if r["filename"].endswith(".md") else r["filename"])
            for r in conn.execute(
                "SELECT t.filename FROM trajectories t"
                " JOIN watch_dirs w ON t.watch_dir_id=w.id WHERE w.path=?",
                (dir_path,),
            ).fetchall()
        ]
        for stem in stems:
            conn.execute("DELETE FROM atom_adoption WHERE atom_id GLOB ?",
                         (f"atom_{stem}_*",))
        cur = conn.execute("DELETE FROM watch_dirs WHERE path=?", (dir_path,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_watch_dirs(*, db_path: Optional[Path] = None) -> list[dict]:
    """返回所有注册目录及统计信息。"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT w.*, "
            "  (SELECT COUNT(*) FROM trajectories t WHERE t.watch_dir_id=w.id) AS traj_count,"
            "  (SELECT COUNT(*) FROM trajectories t WHERE t.watch_dir_id=w.id AND t.has_embedding=1) AS indexed_count"
            " FROM watch_dirs w ORDER BY w.id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_watch_dir(dir_path: str | Path, *, db_path: Optional[Path] = None) -> dict | None:
    """查询单个目录记录。"""
    dir_path = str(Path(dir_path).resolve())
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM watch_dirs WHERE path=?", (dir_path,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Trajectory tracking
# ---------------------------------------------------------------------------

# mtime 变更检测时**不触碰**的中间态：split / cluster 正在 in-flight 跑，
# 此刻翻 updated 会和在飞 future 的状态回写打架。留着旧 mtime,等它落定下一轮
# scan 再检出变更（续写重拆最终收敛,不丢更新）。
_ACTIVE_STATUSES = ("splitting", "clustering")


def discover_trajectories(
    watch_dir_id: int,
    dir_path: Path,
    *,
    db_path: Optional[Path] = None,
) -> list[str]:
    """扫描目录中的 traj_*.md，upsert 到 DB。返回新发现的文件名列表。

    续写重拆触发：已存在的文件若 mtime 增大（客户端追加内容后重传覆盖写,
    mtime 变更），把它从"已落定"状态翻回 ``updated``——watcher 下一轮会像
    ``discovered`` 一样重新提交 split，TaskAgent 用 ``last_offset`` 续接点
    只拆新增内容。``updated`` 不计入返回的 new_files（只统计真·新文件）。
    """
    dir_path = Path(dir_path)
    conn = get_connection(db_path)
    new_files: list[str] = []
    try:
        # P2-2.1 归因(D5):入库即把 watch_dir 的 label 写进 user_key,聚合层
        # 不再 JOIN watch_dirs.label——source 唯一。CS 模式各用户桶(label=
        # sessions 桶目录名=user_name/client_id)不论 ecosystem 是 team_client
        # 还是 bridge 检出的真实生态(ngagent 等)都归因;无 label 的本地目录
        # 留空,聚合层显示 '(local)'。
        wd = conn.execute(
            "SELECT label FROM watch_dirs WHERE id=?",
            (watch_dir_id,),
        ).fetchone()
        user_key = (wd["label"] or "") if wd else ""

        existing = {
            row["filename"]: row
            for row in conn.execute(
                "SELECT filename, status, process_action, file_mtime FROM trajectories"
                " WHERE watch_dir_id=?",
                (watch_dir_id,),
            ).fetchall()
        }

        for md in sorted(dir_path.glob("traj_*.md")):
            if md.name.endswith(".meta"):
                continue
            mtime = md.stat().st_mtime
            row = existing.get(md.name)
            if row is None:
                conn.execute(
                    "INSERT INTO trajectories"
                    " (watch_dir_id, filename, file_mtime, source_model,"
                    "  source_harness, user_key)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (watch_dir_id, md.name, mtime, _sidecar_model(md),
                     _sidecar_field(md, "harness"), user_key),
                )
                new_files.append(md.name)
                continue

            stored_mtime = row["file_mtime"] or 0
            if mtime <= stored_mtime:
                continue  # 没变化
            status = row["status"]
            if status in _ACTIVE_STATUSES:
                # 正在 split/cluster——别打架,留旧 mtime,落定后下一轮再检出。
                continue
            if status == "discovered":
                # 还没开拆,后续 split 会读到最新内容（last_offset=0 全量拆）。
                # 只更 mtime,不必翻 updated。
                conn.execute(
                    "UPDATE trajectories SET file_mtime=?"
                    " WHERE watch_dir_id=? AND filename=?",
                    (mtime, watch_dir_id, md.name),
                )
                continue
            if (
                status == TrajectoryStatus.FILTERED.value
                and row["process_action"] == ProcessAction.NOT_FIT.value
            ):
                # Interest-filtered trajectories re-enter only after interests change.
                conn.execute(
                    "UPDATE trajectories SET file_mtime=?"
                    " WHERE watch_dir_id=? AND filename=?",
                    (mtime, watch_dir_id, md.name),
                )
                continue
            # 已落定（done/indexed/split_done/error/filtered/updated）+ 内容变更
            # → 翻 updated,等下一轮重新 split（续接点续拆）。
            conn.execute(
                "UPDATE trajectories SET status='updated', file_mtime=?,"
                " updated_at=datetime('now')"
                " WHERE watch_dir_id=? AND filename=?",
                (mtime, watch_dir_id, md.name),
            )

        conn.commit()
        return new_files
    finally:
        conn.close()


def mark_meta_done(
    watch_dir_id: int, filename: str, *, db_path: Optional[Path] = None
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE trajectories SET has_meta=1 WHERE watch_dir_id=? AND filename=?",
            (watch_dir_id, filename),
        )
        conn.commit()
    finally:
        conn.close()


def mark_indexed(
    watch_dir_id: int, filename: str, *, db_path: Optional[Path] = None
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE trajectories SET has_embedding=1, indexed_at=?"
            " WHERE watch_dir_id=? AND filename=?",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), watch_dir_id, filename),
        )
        conn.commit()
    finally:
        conn.close()


def mark_skill_used(
    watch_dir_id: int,
    filename: str,
    skill_used: str,
    canary_side: str,
    *,
    db_path: Optional[Path] = None,
) -> None:
    """记录该轨迹触发了哪个 skill / 哪个灰度 side。

    设计：skill 版本(sha) / 用户**不落 trajectories 列**,而是看板 metrics 查询时
    从 traj .md 头 `<!-- xskill:skill=X side=Y sha=Z -->` 分析式解析(版本)、JOIN
    watch_dirs.label 现算(用户)。与工具调用/ token 同属"按轨迹文本现算",保持
    "分析而非埋点"一致——免迁移、不改这条打分热路径的写入语义。
    """
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE trajectories SET skill_used=?, canary_side=?"
            " WHERE watch_dir_id=? AND filename=?",
            (skill_used, canary_side, watch_dir_id, filename),
        )
        conn.commit()
    finally:
        conn.close()


def get_unindexed(
    watch_dir_id: int, *, db_path: Optional[Path] = None
) -> list[str]:
    """返回缺少 meta 或 embedding 的文件名。"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT filename FROM trajectories"
            " WHERE watch_dir_id=? AND (has_meta=0 OR has_embedding=0)"
            " ORDER BY filename",
            (watch_dir_id,),
        ).fetchall()
        return [r["filename"] for r in rows]
    finally:
        conn.close()


def get_needs_meta(
    watch_dir_id: int, *, db_path: Optional[Path] = None
) -> list[str]:
    """返回缺少 meta 的文件名。"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT filename FROM trajectories"
            " WHERE watch_dir_id=? AND has_meta=0"
            " ORDER BY filename",
            (watch_dir_id,),
        ).fetchall()
        return [r["filename"] for r in rows]
    finally:
        conn.close()


def get_needs_embedding(
    watch_dir_id: int, *, db_path: Optional[Path] = None
) -> list[str]:
    """返回有 meta 但缺 embedding 的文件名。"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT filename FROM trajectories"
            " WHERE watch_dir_id=? AND has_meta=1 AND has_embedding=0"
            " ORDER BY filename",
            (watch_dir_id,),
        ).fetchall()
        return [r["filename"] for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Cross-dataset search support
# ---------------------------------------------------------------------------

def all_index_paths(*, db_path: Optional[Path] = None) -> list[Path]:
    """返回所有注册目录中实际存在 index.pkl 的路径。"""
    conn = get_connection(db_path)
    try:
        rows = conn.execute("SELECT path FROM watch_dirs ORDER BY id").fetchall()
        result = []
        for r in rows:
            p = Path(r["path"])
            if (p / "index.pkl").is_file():
                result.append(p)
        return result
    finally:
        conn.close()


def find_traj_file(
    traj_id: str,
    suffix: str = ".md",
    *,
    db_path: Optional[Path] = None,
) -> Path | None:
    """跨所有注册的 watch dir 查找 ``<traj_id><suffix>``。

    各 dir 先按"扁平布局"（``<wd>/<traj_id><suffix>``）直查，未命中再递归
    rglob。返回第一个命中；都没有就返回 None 并打 warning。

    用于替代历史上写死的 ``skill_dir.parent.parent / "data"`` 反推路径。
    那条 v1 假设在轨迹搬到 Registry 注册任意目录后已失效，会让
    eval / candidate / SWE-bench 收集等多处静默拿不到源轨迹。
    """
    filename = f"{traj_id}{suffix}"
    watch_dirs = list_watch_dirs(db_path=db_path)
    if not watch_dirs:
        logger.warning(
            "find_traj_file(%s): no watch dirs registered; "
            "run `xskill registry add <path>` to register a trajectory directory",
            filename,
        )
        return None
    searched: list[str] = []
    for wd in watch_dirs:
        wd_path = Path(wd["path"])
        if not wd_path.is_dir():
            continue
        searched.append(str(wd_path))
        direct = wd_path / filename
        if direct.is_file():
            return direct
        for hit in wd_path.rglob(filename):
            return hit
    logger.warning(
        "find_traj_file(%s): not found in any registered watch dir "
        "(searched %d dir(s): %s)",
        filename, len(searched), ", ".join(searched) or "(none reachable)",
    )
    return None


# ---------------------------------------------------------------------------
# Status management
# ---------------------------------------------------------------------------

_NOW = "datetime('now')"


def update_traj_status(
    watch_dir_id: int,
    filename: str,
    status: str,
    *,
    process_action: str | None = None,
    skill_generated: str | None = None,
    ux_score: float | None = None,
    error_msg: str | None = None,
    retry_count: int | None = None,
    db_path: Optional[Path] = None,
) -> None:
    """更新轨迹状态及关联字段。

    ``retry_count`` 显式传入时覆盖列上的值——cluster 阶段 partial-fail
    会算好"重试次数 + 1"再回写，沿着 ``retry_count < max_retries``
    继续重试，超过门槛后兜底标 done + WARNING。
    """
    conn = get_connection(db_path)
    try:
        sets = ["updated_at=datetime('now')"]
        vals: list = []
        if status is not None:
            sets.append("status=?")
            vals.append(status)
        if process_action is not None:
            sets.append("process_action=?")
            vals.append(process_action)
        if skill_generated is not None:
            sets.append("skill_generated=?")
            vals.append(skill_generated)
        if ux_score is not None:
            sets.append("ux_score=?")
            vals.append(ux_score)
        if error_msg is not None:
            sets.append("error_msg=?")
            vals.append(error_msg)
        if retry_count is not None:
            sets.append("retry_count=?")
            vals.append(int(retry_count))
        vals.extend([watch_dir_id, filename])
        conn.execute(
            f"UPDATE trajectories SET {', '.join(sets)}"
            " WHERE watch_dir_id=? AND filename=?",
            vals,
        )
        conn.commit()
    finally:
        conn.close()


def get_traj_retry_count(
    watch_dir_id: int, filename: str, *, db_path: Optional[Path] = None,
) -> int:
    """返回 ``trajectories.retry_count``。行不存在 / 列为 NULL → 0。

    cluster partial-fail 重试用：先读当前 retry_count，+1 后回写
    ``update_traj_status(..., retry_count=N+1)``。和 ``increment_retry``
    的差异是这里**只读不写**，由调用方决定何时 +1。
    """
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT retry_count FROM trajectories"
            " WHERE watch_dir_id=? AND filename=?",
            (watch_dir_id, filename),
        ).fetchone()
        if row is None:
            return 0
        return int(row["retry_count"] or 0)
    finally:
        conn.close()


def update_traj_log(
    watch_dir_id: int,
    filename: str,
    log_json: str,
    *,
    db_path: Optional[Path] = None,
) -> None:
    """Store process log (JSON string) for a trajectory."""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE trajectories SET process_log=?, updated_at=datetime('now')"
            " WHERE watch_dir_id=? AND filename=?",
            (log_json, watch_dir_id, filename),
        )
        conn.commit()
    finally:
        conn.close()


def get_traj_log(
    watch_dir_id: int,
    filename: str,
    *,
    db_path: Optional[Path] = None,
) -> str | None:
    """Retrieve the stored process log JSON for a trajectory."""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT process_log FROM trajectories WHERE watch_dir_id=? AND filename=?",
            (watch_dir_id, filename),
        ).fetchone()
        return row["process_log"] if row else None
    finally:
        conn.close()


def update_traj_offset(
    watch_dir_id: int,
    filename: str,
    *,
    last_offset: int,
    last_atom_id: str | None,
    tasks_extracted: int,
    db_path: Optional[Path] = None,
) -> None:
    """更新轨迹的 AtomTask 增量进度指针。

    watcher 每次跑完 TaskAgent 后调，让下次 scan 用最新的 offset 决定 delta。
    ``last_atom_id`` 为 None 表示当前轨迹还没切出任何 atom（罕见——通常拆出
    至少 1 个）。
    """
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE trajectories SET last_offset=?, last_atom_id=?, "
            "tasks_extracted=?, updated_at=datetime('now')"
            " WHERE watch_dir_id=? AND filename=?",
            (int(last_offset), last_atom_id, int(tasks_extracted),
             watch_dir_id, filename),
        )
        conn.commit()
    finally:
        conn.close()


def mark_not_fit(
    watch_dir_id: int,
    filename: str,
    reason: str,
    interest_fingerprint: str,
    *,
    db_path: Optional[Path] = None,
) -> None:
    """Mark a trajectory as terminally filtered by the configured interests."""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE trajectories SET status=?, process_action=?, error_msg=?, "
            "interest_fingerprint=?, updated_at=datetime('now')"
            " WHERE watch_dir_id=? AND filename=?",
            (
                TrajectoryStatus.FILTERED.value,
                ProcessAction.NOT_FIT.value,
                reason,
                interest_fingerprint,
                watch_dir_id,
                filename,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def reset_not_fit_for_interest_change(
    *,
    old_interest_fingerprint: str,
    new_interest_fingerprint: str,
    db_path: Optional[Path] = None,
) -> int:
    """Reset only stale filtered/not_fit trajectories for re-evaluation."""
    if old_interest_fingerprint == new_interest_fingerprint:
        return 0
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT t.id, t.filename, w.path FROM trajectories t "
            "JOIN watch_dirs w ON t.watch_dir_id = w.id "
            "WHERE t.status=? AND t.process_action=? "
            "AND (t.interest_fingerprint IS NULL OR t.interest_fingerprint != ?)",
            (
                TrajectoryStatus.FILTERED.value,
                ProcessAction.NOT_FIT.value,
                new_interest_fingerprint,
            ),
        ).fetchall()
        directories_seen: set[str] = set()
        for row in rows:
            conn.execute(
                "UPDATE trajectories SET status=?, process_action=NULL, "
                "error_msg=NULL, interest_fingerprint=NULL, last_offset=0, "
                "last_atom_id=NULL, tasks_extracted=0, has_meta=0, "
                "has_embedding=0, indexed_at=NULL, updated_at=datetime('now') "
                "WHERE id=?",
                (TrajectoryStatus.DISCOVERED.value, row["id"]),
            )
            trajectory_stem = (
                row["filename"][:-3]
                if row["filename"].endswith(".md")
                else row["filename"]
            )
            tasks_directory = Path(row["path"]) / trajectory_stem / "tasks"
            if tasks_directory.is_dir():
                for atom_file in tasks_directory.glob("atom_*.json"):
                    atom_file.unlink()
            directories_seen.add(row["path"])
        conn.commit()
        for directory_path in directories_seen:
            index_path = Path(directory_path) / "index.pkl"
            if index_path.is_file():
                index_path.unlink()
        return len(rows)
    finally:
        conn.close()


def reset_trajectories(
    *,
    eco: Optional[str] = None,
    traj_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> int:
    """删除已拆 atom + 重置状态，让 watcher 从头重拆（``xskill rebuild`` 用）。

    **关键正确性点**：TaskAgent 的续接点取自 atom **文件**
    （``AtomTaskStore.last_offset`` = 各 ``<traj_id>/tasks/atom_*.json`` 的
    ``max(offset_end)``），**不读 DB 的 ``last_offset`` 列**。所以只翻 DB 状态
    而不删 atom 文件 → ``last_offset ≥ EOF`` → TaskAgent 直接返回空 → 重拆失效
    （0.6.1a1 的洞）。因此本函数**必删 atom 文件**，这才是真正触发重拆的动作。

    同时删该目录的 ``index.pkl``（atom 的向量索引）——否则 atom 已删而索引仍留
    陈旧 embedding，cluster 阶段向量检索会命中已不存在的 atom。

    DB ``status`` 翻回 ``discovered`` 让 watcher 下轮重新排 split；轨迹级
    skill / canary / UX 派生字段一并清空，避免 rebuild 后看板继续挂旧 skill。

    Args:
        eco: 只重置该生态（``watch_dirs.ecosystem``）的轨迹；None=全部。
        traj_id: 只重置该轨迹（按文件名 stem 匹配）；None=不按轨迹过滤。

    Returns:
        被重置的轨迹行数。
    """
    conn = get_connection(db_path)
    try:
        query_text = (
            "SELECT t.id, t.filename, w.path FROM trajectories t "
            "JOIN watch_dirs w ON t.watch_dir_id = w.id WHERE 1=1"
        )
        query_parameters: list = []
        if eco:
            query_text += " AND w.ecosystem = ?"
            query_parameters.append(eco)
        if traj_id:
            query_text += " AND (t.filename = ? OR t.filename = ?)"
            query_parameters += [traj_id, f"{traj_id}.md"]
        trajectory_rows = conn.execute(query_text, query_parameters).fetchall()

        directories_seen: set[str] = set()
        for trajectory_row in trajectory_rows:
            conn.execute(
                "UPDATE trajectories SET status='discovered', process_action=NULL, "
                "error_msg=NULL, interest_fingerprint=NULL, last_offset=0, "
                "last_atom_id=NULL, tasks_extracted=0, "
                "has_meta=0, has_embedding=0, indexed_at=NULL, "
                "skill_generated=NULL, skill_used=NULL, canary_side=NULL, "
                "ux_score=NULL, "
                "updated_at=datetime('now') WHERE id=?",
                (trajectory_row["id"],),
            )
            trajectory_stem = (
                trajectory_row["filename"][:-3]
                if trajectory_row["filename"].endswith(".md")
                else trajectory_row["filename"]
            )
            tasks_directory = Path(trajectory_row["path"]) / trajectory_stem / "tasks"
            if tasks_directory.is_dir():
                for atom_file in tasks_directory.glob("atom_*.json"):
                    atom_file.unlink()
            # atom 文件已删、tasks_extracted 已归零——采纳事件一并清，
            # 否则采纳率分子留历史累计、分母归零后比率虚高（审计 P1-7）。
            conn.execute("DELETE FROM atom_adoption WHERE atom_id GLOB ?",
                         (f"atom_{trajectory_stem}_*",))
            directories_seen.add(trajectory_row["path"])
        conn.commit()
        # 清各目录的陈旧向量索引（AtomTaskStore.INDEX_FILE = "index.pkl"）。
        for directory_path in directories_seen:
            index_path = Path(directory_path) / "index.pkl"
            if index_path.is_file():
                index_path.unlink()
        return len(trajectory_rows)
    finally:
        conn.close()


def get_trajs_by_status(
    watch_dir_id: int,
    status: str,
    *,
    limit: int = 0,
    max_retries: int = 3,
    db_path: Optional[Path] = None,
) -> list[str]:
    """按状态查询文件名。error 状态自动过滤超过 max_retries 的。"""
    conn = get_connection(db_path)
    try:
        sql = "SELECT filename FROM trajectories WHERE watch_dir_id=? AND status=?"
        params: list = [watch_dir_id, status]
        if status == "error":
            sql += " AND retry_count < ?"
            params.append(max_retries)
        sql += " ORDER BY filename"
        if limit > 0:
            sql += f" LIMIT {limit}"
        rows = conn.execute(sql, params).fetchall()
        return [r["filename"] for r in rows]
    finally:
        conn.close()


def increment_retry(
    watch_dir_id: int, filename: str, *, db_path: Optional[Path] = None
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE trajectories SET retry_count = retry_count + 1"
            " WHERE watch_dir_id=? AND filename=?",
            (watch_dir_id, filename),
        )
        conn.commit()
    finally:
        conn.close()


def get_status_counts(
    watch_dir_id: int | None = None, *, db_path: Optional[Path] = None
) -> dict[str, int]:
    """返回各状态的轨迹数量。watch_dir_id=None 时统计全部。"""
    conn = get_connection(db_path)
    try:
        if watch_dir_id is not None:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM trajectories"
                " WHERE watch_dir_id=? GROUP BY status",
                (watch_dir_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM trajectories GROUP BY status"
            ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}
    finally:
        conn.close()


# =============================================================================
# Registry 实体类 —— 包装上面的模块函数为 OOP 接口
# =============================================================================
# 所有 watch_dir + trajectory 反查走这个类。


class Registry:
    """监听目录注册表 + 轨迹处理状态查询。

    数据存于 ~/.xskill/registry.db。所有方法直接代理本模块函数；
    本类只负责 Pythonic 接口与 dataclass 包装。
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path  # None = 用 config 默认

    # ─── watch_dir 管理 ───────────────────────────────────────────
    def add(self, path: str | Path, label: str = "",
            ecosystem: str = "manual") -> WatchDir:
        p = Path(path).expanduser().resolve()
        if not p.is_dir():
            raise NotADirectoryError(f"not a directory: {p}")
        register_dir(p, label=label, ecosystem=ecosystem,
                     db_path=self._db_path)
        row = get_watch_dir(p, db_path=self._db_path)
        if not row:
            raise RuntimeError(f"register_dir succeeded but row missing: {p}")
        return self._row_to_watch_dir(row, traj_count=0, indexed_count=0)

    def remove(self, path: str | Path) -> bool:
        p = Path(path).expanduser().resolve()
        return unregister_dir(p, db_path=self._db_path)

    def list(self) -> list[WatchDir]:
        rows = list_watch_dirs(db_path=self._db_path)
        return [self._row_to_watch_dir(r) for r in rows]

    def get(self, path: str | Path) -> Optional[WatchDir]:
        p = Path(path).expanduser().resolve()
        row = get_watch_dir(p, db_path=self._db_path)
        return self._row_to_watch_dir(row) if row else None

    @staticmethod
    def _row_to_watch_dir(row: dict, **overrides) -> WatchDir:
        return WatchDir(
            id=row["id"],
            path=Path(row["path"]),
            label=row.get("label", ""),
            auto_index=bool(row.get("auto_index", 1)),
            traj_count=overrides.get("traj_count", row.get("traj_count", 0)),
            indexed_count=overrides.get("indexed_count", row.get("indexed_count", 0)),
            ecosystem=row.get("ecosystem", "manual"),
        )

    # ─── trajectory 反查 ────────────────────────────────────────
    def trajectory_status(self, traj_path: str | Path) -> Optional[dict]:
        """返回某条 traj 在 trajectories 表里的全部字段（含 skill_used / canary_side / ux_score）。
        未找到返回 None。"""
        traj_path = Path(traj_path).resolve()
        wd_path = str(traj_path.parent)
        conn = get_connection(self._db_path)
        try:
            row = conn.execute(
                "SELECT t.* FROM trajectories t "
                "JOIN watch_dirs w ON t.watch_dir_id = w.id "
                "WHERE w.path = ? AND t.filename = ?",
                (wd_path, traj_path.name),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def trajectories_using(self, skill_name: str) -> list[Path]:
        """反查：曾用过某个 skill 的所有轨迹路径。
        skill_used 字段是逗号分隔，故 LIKE 匹配。"""
        conn = get_connection(self._db_path)
        try:
            rows = conn.execute(
                "SELECT w.path AS wd_path, t.filename "
                "FROM trajectories t JOIN watch_dirs w ON t.watch_dir_id=w.id "
                "WHERE t.skill_used = ? OR t.skill_used LIKE ? "
                "   OR t.skill_used LIKE ? OR t.skill_used LIKE ?",
                (skill_name,
                 f"{skill_name},%",
                 f"%,{skill_name}",
                 f"%,{skill_name},%"),
            ).fetchall()
            return [Path(r["wd_path"]) / r["filename"] for r in rows]
        finally:
            conn.close()
