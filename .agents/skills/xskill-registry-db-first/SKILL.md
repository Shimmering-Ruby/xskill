---
name: xskill-registry-db-first
description: >-
  XSkill 全库数据访问铁律（server 与 client 通用）：磁盘或外部源为真相，由有限
  同步入口维护各 SQLite 与盘的一致性；一切查询与业务读路径只走对应 DB，
  禁止子功能各自全量扫盘。适用于改 dashboard、recommend、team、pipeline、
  ecosystems、install ledger、client upload 状态，以及 code review 发现
  iterdir(skill_dir)、逐文件读 jsonl、重复打开第三方 DB 全表扫。负向：不替代
  合法的 sync、backfill、rebuild、collector 拉取。
---

# XSkill：盘真相、库查询（全 DB，不只 registry）

## 设计思路（先记住）

1. **真相在盘或外部源**（skill 仓、`.candidates.yml`、`.ux_scores.jsonl`、生态 DB、安装目录）。
2. **有限入口**负责把真相投影进对应 SQLite（事件钩子、定时 sync、ensure、backfill）。
3. **所有查询、面板、业务读路径只打 DB**；不得每个子功能自己从头扫盘再聚合。
4. **Server 与 Client 同一原则**；只是库文件不同、职责不同。

本 skill 覆盖 **全部** xskill 自有库与常见外部源，不是只讲 `registry.db`。

## 何时使用

改任何「读数据」代码，或审查是否又引入扫盘读路径时，先对照本 skill。

## 铁律（通用）

1. **先认库再动手**：`registry` / `team_clients` / `team_profile` / `client_state` / `installations` 选对再改；禁止再造平行 `.db` 或旁路 json 第二真相。
2. **盘存内容、库存投影**：热读走投影表；写盘后走 `notify_*` 或既有 sync worker；**禁止**「查库失败就静默全盘扫」的回退。
3. **多库必显式 `db_path`**：独立 home / 镜像 / agent 实例禁止 `pooled_connection(None)` 误摸全局 `~/.xskill`。
4. **跨库漂移要有 reconcile**：改 `team_clients`（如 ingest 暂停）须对齐 `registry.watch_dirs`；安装态只走 `installations.sqlite`。
5. **全量扫只许同步入口**：collector、backfill、`ensure_*` 一次、rebuild、migrate；定时一致性优先挂 `_workers`（如 `ux-scores-sync`、`profile-refresh`），勿塞进 web 热路径。

## 库清单（自有）

路径均相对 `XSKILL_HOME`（`~/.xskill` 或 bench home），除非另行注明。

| 库文件 | 侧 | 职责（查询应走这里） | 典型模块 |
|---|---|---|---|
| `registry.db` | server / 单机 serve | 轨迹与 watch、采纳、skills_catalog、ux_scores 投影、recommendation_log、lifecycle、事件等管线与看板主库 | `pipeline/registry.py`、`catalog_store`、`ux_scores_store`、dashboard |
| `team_clients.db` | server | 客户端注册、last_seen、dashboard token、用户名映射 | `team/server/client_registry.py` |
| `team_profile.db` | server | 推荐画像与 RecoStore；引擎读写画像经此库 | `team/server/engine_factory.py`、`recommend/*` |
| `client_state.db` | client | 上传游标、已上传集合、collector 进度（按 server 分子目录） | `team/client/collector.py`、`upload_state.py` |
| `installations.sqlite` | client | 生态安装账本与卸装任务；禁止再扫用户 skills 目录「猜安装」 | `ecosystems/install_ledger.py` |
| `chat_sessions.db` | （路径已定，包内基本未接线） | 勿新建平行会话库；要用先接此路径 | `config.CHAT_DB` |

配置里已有盘→库周期例：`ux_scores_sync_interval`（`.ux_scores.jsonl` → `registry.db`）、`profile-refresh` → `team_profile.db`。新投影应同样：**写出口同步 + 可选定时 reconcile（挂 `_workers`）**。

**不是 SQLite、别当成业务库乱扫：** `.skill_index.pkl` / embed cache（向量）；atom 文件树（内容真相，列表类查询仍走投影）。

## 外部源（只读摄入，不是业务热路径去扫）

| 源 | 侧 | 说明 |
|---|---|---|
| `opencode.db` / `ngagent.db` 等 | client collector | 生态会话真相；由 collector / `db_ingest` 桥成轨迹后，业务读 `registry`，不要每个 API 直接扫生态 DB |
| 用户生态 skills 目录（claude/codex/…） | client | 安装真相配合 `installations.sqlite`；cleanup/reconcile 走账本，禁止无账本全目录盲删盲扫当查询 |

## 按读需求选库（示例）

| 要读什么 | 走哪 | 不要 |
|---|---|---|
| skill 列表 / 分页 / state | `registry.db` → `skills_catalog` | 扫 `skill/` |
| atom 已采纳去向 | `registry.db` → `atom_adoption` | 扫盘 |
| atom 在途 pending | `registry.db` 投影表（待建 `atom_candidate_pending`）；写出口挂 candidates 落盘闸 | per-atom `iterdir`+`load_candidates` |
| 推荐曝光 / 历史推荐矩阵曝光侧 | `registry.db` → `recommendation_log` | 扫盘 |
| UX / 触发 | `registry.db` → `ux_scores`（同步自 jsonl） | 请求里全盘扫 jsonl |
| 当前「推给我的」槽位 | `build_manifest`（live 组装；catalog 短 TTL）；画像读 `team_profile.db` | 当「再扫一遍 skill 仓」 |
| 客户端是否在线 | `team_clients.db` | 扫连接日志 |
| 本机装了哪些 skill 到哪些生态 | `installations.sqlite` | 遍历 `~/.claude/skills` 当唯一真相 |
| 客户端上传进度 | `client_state.db` | 扫本地轨迹目录猜已传 |

「推给我的」是 **live `build_manifest`**（无每人槽位物化表），但输入应来自库与缓存，不是贡献去向那种全库 `iterdir`。

## 同步入口形态（写新投影时照抄）

1. **事件钩子**（首选）：唯一落盘闸旁 upsert（例：candidates 保存旁更新 pending 表；catalog 的 native notify）。
2. **ensure / 冷启动一次 backfill**：带 meta flag，只跑一次全量扫。
3. **定时 reconcile**（可选）：类似 `ux_scores_sync_interval`，修漂移，不替代钩子。

查询代码里只允许 `SELECT`；发现漂移 → 修同步入口，不要在查询里补扫盘。

## 禁止模式

```python
# 任意业务/看板读路径禁止
for d in Path(skill_dir).iterdir():
    load_candidates(d)
    load_ux_scores(d)

# 客户端查询禁止
for dest in ecosystem_skills_root.iterdir():  # 用 installations 账本
    ...
```

已知债：`TrajExplorer._atom_destinations` 查完 `atom_adoption` 后仍全库扫 pending →
拖慢 `/my/contributions/trajs`。修法：pending 投影 + candidates 写出口同步。

## Agent 检查清单

1. 信息在上表哪一库？有 → 只走该库 API/SQL。
2. 没有 → 选对库建最小投影，列出全部写/删出口并挂钩子，再写读路径。
3. 是否 server 与 client 都会碰？两侧各自库文件都要考虑（勿假设只有 `registry.db`）。
4. PR 是否在热路径新增扫盘？有 → 打回。
5. 前端同页切换是否重复打重接口？应用缓存 payload。

## 相关符号

- 主库：`pipeline/registry.py`、`skill/catalog_store.py`、`pipeline/ux_scores_store.py`
- Server：`team/server/client_registry.py`、`team/server/engine_factory.py`（`team_profile.db`）
- Client：`team/client/collector.py`、`team/client/upload_state.py`、`ecosystems/install_ledger.py`
- 债：`dashboard/explore.py` → `_atom_destinations`
