# 库清单与表结构

路径默认在 `XSKILL_HOME`（通常是 `~/.xskill`）下。`client_state.db` 按对接的 server 分子目录存放。下面 schema 是运行时迁完后的有效列，不是只抄 CREATE 初稿。

| 库 | 侧 | 干什么 |
| --- | --- | --- |
| `registry.db` | server / 单机 | 管线与看板主库 |
| `team_clients.db` | server | 客户端注册与看板登录相关状态 |
| `team_profile.db` | server | 推荐画像与推荐记录 |
| `client_state.db` | client | 本机轨迹上传进度 |
| `installations.sqlite` | client | 本机 skill 安装账本 |

业务读请求选上表中的库。不要为同一类信息再新建平行库。

外部生态自己的库或用户目录是摄入源或安装目标，不是业务热路径里反复全扫的对象。摄入后业务仍读上表自有库。

## registry.db

管线状态、看板查询、多数投影表都在这里。

### watch_dirs

被监视的轨迹来源目录。

```sql
CREATE TABLE watch_dirs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    path       TEXT UNIQUE NOT NULL,
    label      TEXT DEFAULT '',
    auto_index INTEGER DEFAULT 1,
    ecosystem  TEXT DEFAULT 'manual',
    created_at TEXT DEFAULT (datetime('now'))
);
```

### trajectories

每条被发现的轨迹文件及其处理状态。

```sql
CREATE TABLE trajectories (
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
    process_log   TEXT,
    tasks_extracted INTEGER DEFAULT 0,
    last_offset   INTEGER DEFAULT 0,
    last_atom_id  TEXT,
    file_mtime    REAL DEFAULT 0,
    discovered_at TEXT DEFAULT (datetime('now')),
    indexed_at    TEXT,
    updated_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(watch_dir_id, filename)
);
```

### llm_usage

管线里 LLM 调用的用量与费用记录。

```sql
CREATE TABLE llm_usage (
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
```

### recommendation_log

推荐或排序槽位对用户的曝光记录，供看板算率。同一客户端、skill、侧、sha 去重。

```sql
CREATE TABLE recommendation_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT DEFAULT (datetime('now')),
    client_id TEXT,
    skill     TEXT,
    side      TEXT,
    bucket    TEXT,
    sha       TEXT DEFAULT ''
);
```

### atom_adoption

atom 被正式采纳进 skill 的记录。

```sql
CREATE TABLE atom_adoption (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT DEFAULT (datetime('now')),
    atom_id     TEXT,
    skill       TEXT,
    weightscore INTEGER,
    was_new     INTEGER
);
```

### atom_candidate_pending

atom 仍在候选缓冲、尚未采纳时的投影。盘上候选文件是真相，业务读这张表。

```sql
CREATE TABLE atom_candidate_pending (
    atom_id     TEXT PRIMARY KEY,
    skill       TEXT NOT NULL,
    weightscore INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT DEFAULT (datetime('now'))
);
```

### atom_candidate_pending_meta

pending 投影是否已就绪，以及按 skill 记录候选文件 mtime，供合扫跳过未变文件。

```sql
CREATE TABLE atom_candidate_pending_meta (
    root_key      TEXT PRIMARY KEY,
    backfilled_at TEXT NOT NULL
);
```

### canary_decision

灰度裁决结果（晋级、拒绝、超时丢弃等）。

```sql
CREATE TABLE canary_decision (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT DEFAULT (datetime('now')),
    skill           TEXT,
    action          TEXT,
    main_avg        REAL,
    staging_avg     REAL,
    main_samples    INTEGER,
    staging_samples INTEGER,
    age_days        REAL,
    main_sha        TEXT DEFAULT '',
    staging_sha     TEXT DEFAULT ''
);
```

### skill_prefs

用户或全局对 skill 的钉住、屏蔽偏好。`user_key='*global*'` 表示全局。

```sql
CREATE TABLE skill_prefs (
    user_key   TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    pref       TEXT NOT NULL CHECK(pref IN ('pinned','blocked')),
    set_by     TEXT DEFAULT '',
    ts         TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (user_key, skill_name)
);
```

### skill_lifecycle

skill 生命周期。当前主要是下线（retired）。没有行表示在役。

```sql
CREATE TABLE skill_lifecycle (
    skill_name TEXT PRIMARY KEY,
    state      TEXT NOT NULL CHECK(state IN ('retired')),
    set_by     TEXT DEFAULT '',
    ts         TEXT DEFAULT (datetime('now'))
);
```

### skills_catalog

skill 列表投影，供看板分页与筛选。盘上 skill 目录是真相。

```sql
CREATE TABLE skills_catalog (
    catalog_key       TEXT PRIMARY KEY,
    root_key          TEXT NOT NULL DEFAULT '',
    name              TEXT NOT NULL,
    repo_name         TEXT NOT NULL DEFAULT '',
    source            TEXT NOT NULL CHECK(source IN ('native','skillhub')),
    state             TEXT NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    version           INTEGER NOT NULL DEFAULT 0,
    candidates_count  INTEGER NOT NULL DEFAULT 0,
    main_sha          TEXT NOT NULL DEFAULT '',
    staging_sha       TEXT NOT NULL DEFAULT '',
    distributable     INTEGER NOT NULL DEFAULT 0,
    search_id         TEXT NOT NULL DEFAULT '',
    hub               TEXT NOT NULL DEFAULT '',
    skill_id          TEXT NOT NULL DEFAULT '',
    use_count         INTEGER NOT NULL DEFAULT 0,
    updated_at        TEXT DEFAULT (datetime('now'))
);
```

### skills_catalog_meta

某 skill 根是否已做过目录投影灌库。

```sql
CREATE TABLE skills_catalog_meta (
    root_key      TEXT PRIMARY KEY,
    backfilled_at TEXT NOT NULL,
    skillhub_key  TEXT NOT NULL DEFAULT ''
);
```

### events / event_targets / event_reads

通知与世界消息。事件本体在 `events`，收件人在 `event_targets`，每用户已读游标在 `event_reads`。

```sql
CREATE TABLE events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT DEFAULT (datetime('now')),
    kind    TEXT NOT NULL CHECK(kind IN ('feedback','push_edit','canary','pin')),
    actor   TEXT DEFAULT '',
    skill   TEXT DEFAULT '',
    traj_id TEXT DEFAULT '',
    payload TEXT DEFAULT '{}'
);

CREATE TABLE event_targets (
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    user_key TEXT NOT NULL,
    PRIMARY KEY (event_id, user_key)
);

CREATE TABLE event_reads (
    user_key     TEXT PRIMARY KEY,
    last_read_id INTEGER NOT NULL DEFAULT 0
);
```

### skill_trigger_eval

离线触发评测结果，供看板看描述触发准确率。

```sql
CREATE TABLE skill_trigger_eval (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT DEFAULT (datetime('now')),
    skill        TEXT,
    version_sha  TEXT,
    exp_id       TEXT,
    train_score  REAL,
    test_score   REAL,
    n_cases      INTEGER,
    catalog_size INTEGER
);
```

### scatter_cache

画像散点图的物化缓存，端点以读库为主。

```sql
CREATE TABLE scatter_cache (
    user_key    TEXT NOT NULL,
    method      TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    payload     TEXT NOT NULL,
    computed_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (user_key, method)
);
```

### ux_scores / ux_scores_meta

体验分投影。盘上按 skill 的体验分文件是真相，定时合扫入库。meta 记同步进度与文件 mtime。

```sql
CREATE TABLE ux_scores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name  TEXT NOT NULL,
    side        TEXT NOT NULL,
    commit_sha  TEXT NOT NULL DEFAULT '',
    score       REAL NOT NULL,
    scored_at   TEXT NOT NULL,
    atom_id     TEXT NOT NULL DEFAULT '',
    traj_id     TEXT NOT NULL DEFAULT '',
    reasons     TEXT NOT NULL DEFAULT '',
    user_model  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE ux_scores_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

## team_clients.db

### clients

已加入团队的客户端。含最近在线、用户名、看板登录凭证、是否暂停摄入等。

```sql
CREATE TABLE clients (
    client_id      TEXT PRIMARY KEY,
    label          TEXT DEFAULT '',
    hostname       TEXT DEFAULT '',
    user_name      TEXT DEFAULT '',
    client_version TEXT DEFAULT '',
    dashboard_token TEXT DEFAULT '',
    ingest_paused  INTEGER NOT NULL DEFAULT 0,
    ingest_paused_at TEXT,
    ingest_paused_by TEXT DEFAULT '',
    ingest_pause_reason TEXT DEFAULT '',
    joined_at      TEXT NOT NULL,
    last_seen      TEXT NOT NULL
);
```

## team_profile.db

画像与推荐记录共用这一文件。

### client_interest

每个用户的兴趣画像。张量与散点坐标以二进制存放，元数据为文本。

```sql
CREATE TABLE client_interest (
    user_id         TEXT PRIMARY KEY,
    feature_tensor  BLOB,
    mean_tensor     BLOB,
    used_skills     TEXT DEFAULT '[]',
    points          BLOB,
    point_meta      TEXT DEFAULT '[]',
    embed_model     TEXT DEFAULT '',
    source_revision TEXT DEFAULT '',
    updated_at      TEXT NOT NULL
);
```

### recommendations

某用户当前被推荐了哪些 skill 分支与版本。

```sql
CREATE TABLE recommendations (
    user_id     TEXT NOT NULL,
    skill_name  TEXT NOT NULL,
    side        TEXT NOT NULL,
    sha         TEXT NOT NULL,
    ts          TEXT NOT NULL,
    PRIMARY KEY (user_id, skill_name, side)
);
```

## client_state.db

客户端本机库，路径在对接某 server 的子目录下。

### trajectory_upload_state

每条本地轨迹是否已上传、内容哈希、等待去抖等。

```sql
CREATE TABLE trajectory_upload_state (
    trajectory_id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    harness_name TEXT DEFAULT '',
    model_name TEXT DEFAULT '',
    file_size_bytes INTEGER,
    file_modified_time_nanoseconds INTEGER,
    file_changed_time_nanoseconds INTEGER,
    original_content_hash TEXT,
    cleaned_content_hash TEXT,
    uploaded_cleaned_content_hash TEXT,
    uploaded_at_seconds REAL,
    waiting_content_hash TEXT,
    waiting_started_at_seconds REAL,
    first_seen_at_seconds REAL NOT NULL,
    last_seen_at_seconds REAL NOT NULL,
    updated_at_seconds REAL NOT NULL
);
```

### client_state_metadata

键值杂项，例如是否完成过旧 JSON 状态迁移。

```sql
CREATE TABLE client_state_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

## installations.sqlite

客户端安装账本，通常与安装逻辑同目录。

### installations

某个安装目标上当前（或墓碑）安装记录。

```sql
CREATE TABLE installations (
    dest_key TEXT PRIMARY KEY,
    skill_name TEXT NOT NULL,
    mode TEXT NOT NULL,
    source TEXT NOT NULL,
    source_sha TEXT NOT NULL DEFAULT '',
    installation_id TEXT NOT NULL,
    content_identity TEXT NOT NULL,
    baseline_identity TEXT,
    file_fingerprints_json TEXT,
    generation INTEGER NOT NULL DEFAULT 1,
    installed_at REAL NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'tombstone'))
);
```

### removal_jobs

异步卸装任务及其状态机。

```sql
CREATE TABLE removal_jobs (
    job_id TEXT PRIMARY KEY,
    dest_key TEXT NOT NULL,
    expected_generation INTEGER NOT NULL,
    expected_installation_id TEXT NOT NULL,
    expected_content_identity TEXT NOT NULL,
    expected_target_identity_json TEXT,
    state TEXT NOT NULL CHECK(state IN (
        'pending', 'deleting', 'done', 'superseded', 'aborted'
    )),
    mode TEXT NOT NULL,
    updated_at REAL NOT NULL,
    last_error TEXT
);
```
