"""
entities/registry.py — Registry 实体类
═════════════════════════════════════════════
包装 xskill.registry 模块函数为 OOP 接口。
所有 watch_dir + trajectory 反查走这个类。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from xskill import registry as _r
from xskill.types import WatchDir


class Registry:
    """监听目录注册表 + 轨迹处理状态查询。

    数据存于 ~/.xskill/registry.db。所有方法直接代理底层模块函数；
    本类只负责 Pythonic 接口与 dataclass 包装。
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path  # None = 用 config 默认

    # ─── watch_dir 管理 ───────────────────────────────────────────
    def add(self, path: str | Path, label: str = "") -> WatchDir:
        p = Path(path).expanduser().resolve()
        if not p.is_dir():
            raise NotADirectoryError(f"not a directory: {p}")
        wid = _r.register_dir(p, label=label, db_path=self._db_path)
        row = _r.get_watch_dir(p, db_path=self._db_path)
        if not row:
            raise RuntimeError(f"register_dir succeeded but row missing: {p}")
        return self._row_to_watch_dir(row, traj_count=0, indexed_count=0)

    def remove(self, path: str | Path) -> bool:
        p = Path(path).expanduser().resolve()
        return _r.unregister_dir(p, db_path=self._db_path)

    def list(self) -> list[WatchDir]:
        rows = _r.list_watch_dirs(db_path=self._db_path)
        return [self._row_to_watch_dir(r) for r in rows]

    def get(self, path: str | Path) -> Optional[WatchDir]:
        p = Path(path).expanduser().resolve()
        row = _r.get_watch_dir(p, db_path=self._db_path)
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
        )

    # ─── trajectory 反查 ────────────────────────────────────────
    def trajectory_status(self, traj_path: str | Path) -> Optional[dict]:
        """返回某条 traj 在 trajectories 表里的全部字段（含 skill_used / canary_side / ux_score）。
        未找到返回 None。"""
        traj_path = Path(traj_path).resolve()
        wd_path = str(traj_path.parent)
        conn = _r.get_connection(self._db_path)
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
        conn = _r.get_connection(self._db_path)
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
