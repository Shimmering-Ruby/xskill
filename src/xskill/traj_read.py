"""按行号读轨迹原文：``xskill traj read`` 与 ``xskill atom read``。

行号是 1-based 半开区间 ``[start, end)``，和 Atom、atom search 卡片一致。
每次最多 ``TRAJ_READ_MAX_LINES`` 行。返回里带当前窗口和总窗口，
对外字段不含路径。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from xskill.traj_search import watch_session_dirs

logger = logging.getLogger("xskill.traj_read")

TRAJ_READ_MAX_LINES = 200
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def is_safe_read_id(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text) and _SAFE_ID.match(text) is not None


def _as_offset(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_read_offsets(
    offset_start: Any, offset_end: Any,
) -> tuple[int | None, int | None, str | None]:
    """解析用户传入的行号。不合法时返回错误中文。"""
    start = _as_offset(offset_start)
    end = _as_offset(offset_end)
    if offset_start not in (None, "") and start is None:
        return None, None, "offset_start 必须是整数"
    if offset_end not in (None, "") and end is None:
        return None, None, "offset_end 必须是整数"
    if start is not None and start < 1:
        return None, None, "offset_start 必须从 1 起"
    if end is not None and start is not None and end <= start:
        return None, None, "offset_end 必须大于 offset_start"
    if end is not None and start is None and end < 2:
        return None, None, "offset_end 必须大于 offset_start"
    return start, end, None


def find_traj_md(
    traj_id: str,
    dataset_dirs: list[tuple[str, Path]] | None = None,
) -> tuple[str, Path] | None:
    """在 sessions 目录里找 ``<traj_id>.md``。只做直查，不递归。"""
    if not is_safe_read_id(traj_id):
        return None
    dirs = dataset_dirs if dataset_dirs is not None else watch_session_dirs()
    filename = f"{traj_id}.md"
    for user, directory in dirs:
        path = Path(directory) / filename
        if path.is_file():
            return user, path
    return None


def find_atom_record(
    atom_id: str,
    dataset_dirs: list[tuple[str, Path]] | None = None,
) -> tuple[str, Any, Path] | None:
    """在 sessions 目录的 AtomTaskStore 里找 atom。返回工号、atom、目录。"""
    if not is_safe_read_id(atom_id):
        return None
    from xskill.pipeline.atom import AtomTaskStore

    dirs = dataset_dirs if dataset_dirs is not None else watch_session_dirs()
    for user, directory in dirs:
        root = Path(directory)
        if not root.is_dir():
            continue
        store = AtomTaskStore(root=root)
        try:
            atom = store.load(atom_id)
        except FileNotFoundError:
            continue
        except Exception:
            logger.warning("atom load skipped directory %s", root, exc_info=True)
            continue
        return user, atom, root
    return None


def _page_window(
    *,
    want_start: int | None,
    want_end: int | None,
    bound_start: int,
    bound_end: int,
    max_lines: int,
) -> tuple[int, int, bool]:
    start = bound_start if want_start is None else want_start
    start = max(start, bound_start)
    if start >= bound_end:
        return bound_end, bound_end, False
    if want_end is None:
        end = min(bound_end, start + max_lines)
    else:
        end = min(bound_end, want_end)
    truncated = end - start > max_lines
    if truncated:
        end = start + max_lines
    if end < start:
        end = start
    return start, end, truncated or (want_end is not None and want_end > end)


def read_md_lines(
    path: Path,
    *,
    offset_start: int | None = None,
    offset_end: int | None = None,
    bound_start: int | None = None,
    bound_end: int | None = None,
    max_lines: int = TRAJ_READ_MAX_LINES,
) -> dict[str, Any]:
    """读 md 的一行窗口。一次顺序扫描，只留当前页。"""
    known_bound = bound_start is not None and bound_end is not None
    if known_bound:
        page_start, page_end, _truncated = _page_window(
            want_start=offset_start,
            want_end=offset_end,
            bound_start=int(bound_start),
            bound_end=int(bound_end),
            max_lines=max_lines,
        )
        total_start = int(bound_start)
        total_end = int(bound_end)
    else:
        page_start = 1 if offset_start is None else offset_start
        if page_start < 1:
            page_start = 1
        raw_end = page_start + max_lines if offset_end is None else offset_end
        page_end = min(raw_end, page_start + max_lines)
        total_start = 1
        total_end = page_end
    file_lines = 0
    collected: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                file_lines += 1
                if page_start <= file_lines < page_end:
                    collected.append(line)
    except OSError as exc:
        raise FileNotFoundError(str(path)) from exc
    if not known_bound:
        total_start = 1
        total_end = file_lines + 1
        page_start, page_end, _truncated = _page_window(
            want_start=offset_start,
            want_end=offset_end,
            bound_start=1,
            bound_end=total_end,
            max_lines=max_lines,
        )
    truncated = page_end < total_end and (
        offset_end is None or offset_end > page_end or page_end - page_start >= max_lines
    )
    text = "".join(collected)
    if collected and not text.endswith("\n"):
        text += "\n"
    return {
        "offset_start": page_start,
        "offset_end": page_end,
        "total_start": total_start,
        "total_end": total_end,
        "total_lines": max(0, total_end - total_start),
        "file_lines": file_lines,
        "truncated": bool(truncated),
        "text": text,
    }


def read_trajectory(
    traj_id: str,
    *,
    offset_start: int | None = None,
    offset_end: int | None = None,
    dataset_dirs: list[tuple[str, Path]] | None = None,
) -> dict[str, Any] | None:
    found = find_traj_md(traj_id, dataset_dirs)
    if found is None:
        return None
    user, path = found
    window = read_md_lines(
        path, offset_start=offset_start, offset_end=offset_end,
    )
    window.update({
        "kind": "traj",
        "traj_id": traj_id,
        "user": user,
    })
    return window


def read_atom(
    atom_id: str,
    *,
    offset_start: int | None = None,
    offset_end: int | None = None,
    dataset_dirs: list[tuple[str, Path]] | None = None,
) -> dict[str, Any] | None:
    found = find_atom_record(atom_id, dataset_dirs)
    if found is None:
        return None
    user, atom, directory = found
    traj_id = str(getattr(atom, "traj_id", "") or "")
    atom_start = int(getattr(atom, "offset_start", 0) or 0)
    atom_end = int(getattr(atom, "offset_end", 0) or 0)
    if atom_start < 1:
        atom_start = 1
    if atom_end <= atom_start:
        atom_end = atom_start
    path = Path(directory) / f"{traj_id}.md"
    if not path.is_file():
        return None
    window = read_md_lines(
        path,
        offset_start=offset_start,
        offset_end=offset_end,
        bound_start=atom_start,
        bound_end=atom_end,
    )
    window.update({
        "kind": "atom",
        "traj_id": traj_id,
        "atom_id": str(getattr(atom, "atom_id", "") or atom_id),
        "user": user,
        "intent": str(getattr(atom, "intent", "") or ""),
        "summary": str(getattr(atom, "summary", "") or ""),
    })
    return window
