"""轨迹检索与 Atom 检索的装配。

``search traj`` 对着已上传的 ``traj_*.md`` 抽 ``## Initial Query`` /
``## User``，做 BM25。不经拆分代理，不打 embedding。

``search atom`` 走现成 Atom 混合检索（向量 + BM25，字段是 intent 和
summary）。没拆完的轨迹不会出现。

两条路都按工号收窄 sessions 目录，对外字段不含路径和原文。
"""
from __future__ import annotations

import logging
import math
import re
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


def parse_search_names(raw: str | None) -> list[str]:
    """把 ``--name 张三,李四`` 收成去空白的工号列表，保持用户写下的顺序。"""
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
    """会话级命中：traj_id、首问、工号。没有 Atom、没有行号。"""
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
    """把 ``search`` 的 Atom 命中收成对外字段，去掉路径和原文。"""
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
    # 有向量分的排前面；只有 BM25 的排后面。同档再按分数、traj_id。
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
    """用 registry 的 watch 目录 label（工号目录名）当命中上的 user。"""
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
    """工号 → ``<traj_root>/clients/<dir>/sessions``。不认识的工号进 unknown。"""
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
    """``<traj_root>/clients/<工号目录>/sessions``。"""
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


def watch_session_dirs() -> list[tuple[str, Path]]:
    """本机 registry 里已登记的轨迹目录。"""
    from xskill.pipeline.registry import list_watch_dirs

    found: list[tuple[str, Path]] = []
    for row in list_watch_dirs():
        path = row.get("path")
        if not path:
            continue
        directory = Path(path)
        if directory.is_dir():
            found.append((str(row.get("label") or directory.name), directory))
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


def session_corpus_empty(
    dataset_dirs: list[tuple[str, Path]] | None = None,
) -> bool:
    dirs = dataset_dirs if dataset_dirs is not None else watch_session_dirs()
    for _user, directory in dirs:
        if _iter_traj_md(directory):
            return False
    return True


def _session_bm25_scores(
    query_tokens: list[str], corpus: list[list[str]],
) -> list[float]:
    """和 skillhub 一样用恒正 IDF，小语料不会整表打成 0。"""
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
    """对 ``traj_*.md`` 的用户首问做 BM25。不读 Atom，不打 embedding。"""
    from xskill.utils.search import tokenize_search_text

    dirs = dataset_dirs if dataset_dirs is not None else watch_session_dirs()
    docs: list[dict[str, Any]] = []
    for user, directory in dirs:
        for path in _iter_traj_md(Path(directory)):
            first_query, turns = _read_session_query(path)
            text = f"{path.stem} {first_query}".strip()
            docs.append({
                "traj_id": path.stem,
                "user": user,
                "query": first_query,
                "turns": turns,
                "text": text,
            })
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
    """在指定 sessions 目录或全量 registry 上跑 Atom 混合检索。"""
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
