"""历史会话轨迹与原子任务（Atom）检索模块。

提供会话轨迹（trajectory）与原子任务（Atom）的高效检索能力：
- 会话轨迹检索（traj search）：基于会话首问及元数据构建轻量索引，支持团队与本地会话检索；
- 原子任务检索（atom search）：基于 Atom 的意图（intent）与摘要（summary）进行语义及关键词混合检索；
- 支持按工号列表（--name）过滤检索范围，统一输出标准结果格式。
"""
from __future__ import annotations

import logging
import math
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("xskill.traj_search")

SearchOne = Callable[..., list[dict[str, Any]]]
SearchAll = Callable[..., list[dict[str, Any]]]
FindClientId = Callable[[str], str | None]
DirNameFor = Callable[[str], str]

_TRAJ_MD_NAME = re.compile(r"^traj_[A-Za-z0-9_-]+\.md$")
_TRAJ_READ_MAX_BYTES = 65_536
_SESSION_TEXT_LIMIT = 2_000
SESSION_INDEX_NAME = ".xskill_traj_session_index.sqlite"
SESSION_INDEX_REFRESH_LIMIT = 400


def parse_search_names(raw: str | None) -> list[str]:
    """解析逗号分隔的工号或用户名参数，去重并保持原始顺序。"""
    names: list[str] = []
    seen: set[str] = set()
    for part in str(raw or "").split(","):
        name = part.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def format_session_hit(
    *,
    traj_id: str,
    user: str,
    query: str,
    turns: int,
    score: float,
) -> dict[str, Any]:
    """格式化会话轨迹检索结果项。"""
    return {
        "kind": "traj",
        "traj_id": str(traj_id or ""),
        "user": user,
        "query": str(query or ""),
        "turns": int(turns or 0),
        "score": float(score),
        "sources": ["keyword"],
    }


def format_traj_hit(raw: dict[str, Any], *, user: str = "") -> dict[str, Any]:
    """格式化 Atom 检索结果项，输出标准元数据字段。"""
    vector = raw.get("vector_similarity")
    bm25 = raw.get("bm25_score")
    if vector is None:
        score = float(bm25 or 0.0)
    else:
        score = float(vector)
    used = raw.get("used_skills") or []
    if not used:
        atom = raw.get("atom")
        if atom is not None:
            used = getattr(atom, "used_skills", None) or []
    if not isinstance(used, list):
        used = []
    sources = raw.get("sources") or []
    if not isinstance(sources, list):
        sources = []
    atom = raw.get("atom")
    start = raw.get("offset_start")
    end = raw.get("offset_end")
    if start is None and atom is not None:
        start = getattr(atom, "offset_start", None)
    if end is None and atom is not None:
        end = getattr(atom, "offset_end", None)
    return {
        "kind": "atom",
        "traj_id": str(raw.get("traj_id") or ""),
        "atom_id": str(raw.get("atom_id") or ""),
        "intent": str(raw.get("intent") or ""),
        "summary": str(raw.get("summary") or ""),
        "offset_start": _as_line_offset(start),
        "offset_end": _as_line_offset(end),
        "score": score,
        "vector_similarity": vector,
        "bm25_score": bm25,
        "sources": [str(item) for item in sources],
        "user": user,
        "used_skills": [str(item) for item in used],
    }


def _as_line_offset(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _hit_sort_key(hit: dict[str, Any]) -> tuple:
    # 优先排序包含语义向量分的结果，同档次按综合得分及轨迹 ID 降序排列
    has_vector = 0 if hit.get("vector_similarity") is None else 1
    return (
        -has_vector,
        -float(hit.get("score") or 0.0),
        str(hit.get("traj_id") or ""),
    )


def _session_sort_key(hit: dict[str, Any]) -> tuple:
    return (-float(hit.get("score") or 0.0), str(hit.get("traj_id") or ""))


def _path_name(path: Path) -> str:
    return path.name


def user_label_for_dataset(dataset_dir: str | Path) -> str:
    """根据监控目录路径获取对应的用户标签。"""
    from xskill.pipeline.registry import list_watch_dirs

    target = str(Path(dataset_dir).expanduser().resolve())
    for row in list_watch_dirs():
        path = row.get("path")
        if not path:
            continue
        if str(Path(path).expanduser().resolve()) == target:
            return str(row.get("label") or "")
    return ""


def resolve_named_session_dirs(
    names: list[str],
    *,
    traj_root: Path,
    find_client_id: FindClientId,
    dir_name_for: DirNameFor,
) -> tuple[list[tuple[str, Path]], list[str]]:
    """根据成员工号或用户名解析对应的会话存储目录。"""
    found: list[tuple[str, Path]] = []
    unknown: list[str] = []
    root = Path(traj_root)
    for name in names:
        client_id = find_client_id(name)
        if not client_id:
            unknown.append(name)
            continue
        try:
            dir_name = dir_name_for(client_id)
        except ValueError:
            unknown.append(name)
            continue
        found.append((name, root / "clients" / dir_name / "sessions"))
    return found, unknown


def iter_client_session_dirs(traj_root: Path) -> list[tuple[str, Path]]:
    """遍历服务端下所有客户端成员的会话目录。"""
    found: list[tuple[str, Path]] = []
    clients = Path(traj_root) / "clients"
    if not clients.is_dir():
        return found
    try:
        children = list(clients.iterdir())
    except OSError:
        return found
    children.sort(key=_path_name)
    for child in children:
        sessions = child / "sessions"
        if sessions.is_dir():
            found.append((child.name, sessions))
    return found


def _resolved_dir_key(directory: Path) -> str | None:
    try:
        if directory.is_dir():
            return str(directory.resolve())
    except OSError:
        return None
    return None


def iter_local_bridge_session_dirs(
    xskill_home: Path | None = None,
) -> list[tuple[str, Path]]:
    """遍历本机各个 agent 适配器生成的会话轨迹桥接目录。"""
    from xskill.config import XSKILL_HOME

    root = Path(xskill_home) if xskill_home is not None else XSKILL_HOME
    found: list[tuple[str, Path]] = []
    if not root.is_dir():
        return found
    try:
        children = list(root.iterdir())
    except OSError:
        return found
    children.sort(key=_path_name)
    for child in children:
        if child.is_dir() and child.name.endswith("_sessions"):
            found.append((child.name, child))
    return found


def watch_session_dirs() -> list[tuple[str, Path]]:
    """获取本机当前所有可用于检索与读取的会话轨迹目录。"""
    from xskill.pipeline.registry import list_watch_dirs

    found: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for row in list_watch_dirs():
        path = row.get("path")
        if not path:
            continue
        directory = Path(path)
        key = _resolved_dir_key(directory)
        if key is None or key in seen:
            continue
        seen.add(key)
        found.append((str(row.get("label") or directory.name), directory))
    for label, directory in iter_local_bridge_session_dirs():
        key = _resolved_dir_key(directory)
        if key is None or key in seen:
            continue
        seen.add(key)
        found.append((label, directory))
    return found


def _iter_traj_md(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    found: list[Path] = []
    try:
        children = list(directory.iterdir())
    except OSError:
        return []
    for path in children:
        if path.is_file() and _TRAJ_MD_NAME.match(path.name):
            found.append(path)
    found.sort(key=_path_name)
    return found


def _read_session_query(path: Path) -> tuple[str, int]:
    from xskill.pipeline.trajectory import extract_user_sections

    try:
        raw = path.read_bytes()[:_TRAJ_READ_MAX_BYTES]
        text = raw.decode("utf-8", errors="replace")
    except OSError:
        return "", 0
    sections = extract_user_sections(text)
    first = " ".join((sections[0] if sections else "").split())
    if len(first) > _SESSION_TEXT_LIMIT:
        first = first[:_SESSION_TEXT_LIMIT]
    return first, len(sections)


def session_index_path(directory: Path) -> Path:
    return Path(directory) / SESSION_INDEX_NAME


def _open_session_index(directory: Path) -> sqlite3.Connection:
    from xskill._sqlite_connect import connect_with_lock

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    conn = connect_with_lock(
        sqlite3.connect, str(session_index_path(directory)), timeout=10,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS session_docs ("
        " traj_id TEXT PRIMARY KEY,"
        " first_query TEXT NOT NULL DEFAULT '',"
        " turns INTEGER NOT NULL DEFAULT 0,"
        " file_mtime REAL NOT NULL DEFAULT 0,"
        " file_size INTEGER NOT NULL DEFAULT 0)"
    )
    return conn


def _write_session_row(
    conn: sqlite3.Connection,
    traj_id: str,
    first_query: str,
    turns: int,
    file_mtime: float,
    file_size: int,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO session_docs"
        " (traj_id, first_query, turns, file_mtime, file_size)"
        " VALUES (?, ?, ?, ?, ?)",
        (traj_id, first_query, int(turns), float(file_mtime), int(file_size)),
    )


def upsert_session_file(directory: Path, path: Path) -> None:
    """提取轨迹文件的首问与交互轮数，更新至对应的会话索引库。"""
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        logger.warning("session index skip missing file %s", path)
        return
    first_query, turns = _read_session_query(path)
    conn = _open_session_index(directory)
    try:
        _write_session_row(
            conn,
            path.stem,
            first_query,
            turns,
            float(stat.st_mtime),
            int(stat.st_size),
        )
        conn.commit()
    finally:
        conn.close()


def _stale_file_name(item: tuple[Path, float, int]) -> str:
    return item[0].name


def refresh_session_index(
    directory: Path, *, limit: int | None = SESSION_INDEX_REFRESH_LIMIT,
) -> int:
    """根据文件修改时间与大小增量更新会话索引库，返回未处理完成的文件数。"""
    directory = Path(directory)
    if not directory.is_dir():
        return 0
    current: dict[str, tuple[Path, float, int]] = {}
    for path in _iter_traj_md(directory):
        try:
            stat = path.stat()
        except OSError:
            continue
        current[path.stem] = (path, float(stat.st_mtime), int(stat.st_size))
    index_path = session_index_path(directory)
    if not current and not index_path.is_file():
        return 0
    conn = _open_session_index(directory)
    try:
        existing = {
            str(row["traj_id"]): (
                float(row["file_mtime"] or 0),
                int(row["file_size"] or 0),
            )
            for row in conn.execute(
                "SELECT traj_id, file_mtime, file_size FROM session_docs"
            )
        }
        stale: list[tuple[Path, float, int]] = []
        for traj_id, item in current.items():
            old = existing.get(traj_id)
            if old is None or old[0] != item[1] or old[1] != item[2]:
                stale.append(item)
        stale.sort(key=_stale_file_name)
        remaining = len(stale)
        if limit is None:
            budget = remaining
        else:
            budget = max(0, int(limit))
        for path, mtime, size in stale[:budget]:
            first_query, turns = _read_session_query(path)
            _write_session_row(conn, path.stem, first_query, turns, mtime, size)
            remaining -= 1
        for traj_id in existing:
            if traj_id not in current:
                conn.execute(
                    "DELETE FROM session_docs WHERE traj_id=?",
                    (traj_id,),
                )
        conn.commit()
        return remaining
    finally:
        conn.close()


def load_session_docs(directory: Path, *, user: str) -> list[dict[str, Any]]:
    """从会话索引库中加载指定目录的会话记录。"""
    directory = Path(directory)
    if not session_index_path(directory).is_file():
        return []
    conn = _open_session_index(directory)
    try:
        rows = conn.execute(
            "SELECT traj_id, first_query, turns FROM session_docs"
            " ORDER BY traj_id"
        ).fetchall()
    finally:
        conn.close()
    docs: list[dict[str, Any]] = []
    for row in rows:
        traj_id = str(row["traj_id"] or "")
        first_query = str(row["first_query"] or "")
        docs.append({
            "traj_id": traj_id,
            "user": user,
            "query": first_query,
            "turns": int(row["turns"] or 0),
            "text": f"{traj_id} {first_query}".strip(),
        })
    return docs


def session_index_count(directory: Path) -> int:
    directory = Path(directory)
    if not session_index_path(directory).is_file():
        return 0
    conn = _open_session_index(directory)
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM session_docs").fetchone()
    finally:
        conn.close()
    return int((row["n"] if row is not None else 0) or 0)


def session_corpus_empty(
    dataset_dirs: list[tuple[str, Path]] | None = None,
) -> bool:
    dirs = dataset_dirs if dataset_dirs is not None else watch_session_dirs()
    for _user, directory in dirs:
        if session_index_count(Path(directory)):
            return False
    return True


def _session_bm25_scores(
    query_tokens: list[str], corpus: list[list[str]],
) -> list[float]:
    """计算查询词与会话文档集合的 BM25 相关性得分。"""
    k1 = 1.2
    b = 0.75
    doc_count = len(corpus)
    if doc_count == 0:
        return []
    avg_len = sum(len(doc) for doc in corpus) / doc_count
    if avg_len == 0:
        return [0.0] * doc_count
    query_set = set(query_tokens)
    doc_freq = {
        token: sum(1 for doc in corpus if token in set(doc))
        for token in query_set
    }
    scores: list[float] = []
    for doc in corpus:
        length = len(doc)
        tf: dict[str, int] = {}
        for token in doc:
            tf[token] = tf.get(token, 0) + 1
        score = 0.0
        for token in query_set:
            freq = tf.get(token, 0)
            if freq <= 0:
                continue
            idf = math.log(
                1.0 + (doc_count - doc_freq[token] + 0.5) / (doc_freq[token] + 0.5)
            )
            denom = freq + k1 * (1.0 - b + b * length / avg_len)
            score += idf * freq * (k1 + 1.0) / denom
        scores.append(score)
    return scores


def _score_index_pair(item: tuple[int, float]) -> tuple:
    return (-item[1], item[0])


def search_session_trajectories(
    query: str,
    *,
    top_k: int = 5,
    dataset_dirs: list[tuple[str, Path]] | None = None,
) -> list[dict[str, Any]]:
    """在指定的会话目录中执行会话轨迹检索并按相关度排序。"""
    from xskill.utils.search import tokenize_search_text

    dirs = dataset_dirs if dataset_dirs is not None else watch_session_dirs()
    docs: list[dict[str, Any]] = []
    for user, directory in dirs:
        docs.extend(load_session_docs(Path(directory), user=user))
    tokens = tokenize_search_text(query)
    if not docs or not tokens:
        return []
    corpus = [tokenize_search_text(str(doc["text"])) for doc in docs]
    scores = _session_bm25_scores(tokens, corpus)
    ranked = sorted(enumerate(scores), key=_score_index_pair)
    limit = max(1, int(top_k))
    hits: list[dict[str, Any]] = []
    for index, score in ranked[:limit]:
        if score <= 0:
            continue
        doc = docs[index]
        hits.append(format_session_hit(
            traj_id=str(doc["traj_id"]),
            user=str(doc["user"]),
            query=str(doc["query"]),
            turns=int(doc["turns"]),
            score=float(score),
        ))
    hits.sort(key=_session_sort_key)
    return hits


def search_indexed_trajectories(
    query: str,
    *,
    top_k: int = 5,
    dataset_dirs: list[tuple[str, Path]] | None = None,
    search_one: SearchOne | None = None,
    search_all_fn: SearchAll | None = None,
) -> list[dict[str, Any]]:
    """在指定会话目录中执行 Atom 混合检索。"""
    from xskill.utils.search import search as default_search_one
    from xskill.utils.search import search_all as default_search_all

    one = search_one or default_search_one
    all_fn = search_all_fn or default_search_all
    limit = max(1, int(top_k))
    merged: list[dict[str, Any]] = []

    if dataset_dirs is None:
        raw_hits = all_fn(query_text=query, top_k=limit)
        for raw in raw_hits:
            dataset = raw.get("dataset_dir") or ""
            user = str(raw.get("user") or "") or user_label_for_dataset(dataset)
            merged.append(format_traj_hit(raw, user=user))
        merged.sort(key=_hit_sort_key)
        return merged[:limit]

    for user, path in dataset_dirs:
        directory = Path(path)
        if not directory.is_dir():
            continue
        try:
            raw_hits = one(
                dataset_dir=directory,
                query_text=query,
                top_k=limit,
            )
        except Exception:
            logger.warning("atom search skipped directory %s", directory, exc_info=True)
            continue
        for raw in raw_hits:
            merged.append(format_traj_hit(raw, user=user))
    merged.sort(key=_hit_sort_key)
    return merged[:limit]


search_indexed_atoms = search_indexed_trajectories

