"""profile_store.py — §4 用户画像 SQLite 持久化

server 端按 ``user_id`` 持久化每个用户的 ``ClientInterest``（feature_tensor / mean_tensor）
与 ``used_skills``。tensor 以 pickle BLOB 存（numpy 数组）。client（瘦）不存画像。

P3-3.4（Q4 拍板）:原子点向量 ``points``(n,D) + 逐点元数据 ``point_meta``
（atom_id/summary/ux/tags,与 points 行对齐）随画像更新顺手落盘——画像散点
图直接读库投影,不用现算 embedding。
"""
from __future__ import annotations

import json
import pickle
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from xskill.recommend._sqlite_base import _SqliteStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS client_interest (
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
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProfileStore(_SqliteStore):
    """``client_interest`` 表的读写。"""

    _SCHEMA = _SCHEMA

    def _migrate(self, conn) -> None:
        cols = {row[1] for row in conn.execute(
            "PRAGMA table_info(client_interest)").fetchall()}
        if "points" not in cols:
            conn.execute("ALTER TABLE client_interest ADD COLUMN points BLOB")
        if "point_meta" not in cols:
            conn.execute(
                "ALTER TABLE client_interest ADD COLUMN point_meta TEXT DEFAULT '[]'")
        if "embed_model" not in cols:
            conn.execute(
                "ALTER TABLE client_interest ADD COLUMN embed_model TEXT DEFAULT ''")
        if "source_revision" not in cols:
            conn.execute(
                "ALTER TABLE client_interest ADD COLUMN source_revision TEXT DEFAULT ''")

    def upsert(
        self,
        user_id: str,
        *,
        feature_tensor: Optional[np.ndarray],
        mean_tensor: Optional[np.ndarray],
        used_skills: list[dict],
        points: Optional[np.ndarray] = None,
        point_meta: Optional[list[dict]] = None,
        embed_model: str = "",
        source_revision: str = "",
    ) -> None:
        """写画像。``points``/``point_meta`` 与 feature_tensor 同一次计算产出,
        必须一起传（散点图与聚类中心不同源会画出撒谎的图）;冷启动都为 None。
        ``embed_model`` 记录算 points 用的 embedding 模型——增量复用向量前据此
        做护栏（换模型 → 旧向量作废，整体重算）。"""
        if (points is None) != (not point_meta):
            raise ValueError("points 与 point_meta 必须同时给出或同时为空")
        if points is not None and len(point_meta) != int(points.shape[0]):
            raise ValueError(
                f"point_meta 行数 {len(point_meta)} 与 points {points.shape[0]} 不对齐")
        ft_blob = pickle.dumps(feature_tensor) if feature_tensor is not None else None
        mt_blob = pickle.dumps(mean_tensor) if mean_tensor is not None else None
        pt_blob = pickle.dumps(points) if points is not None else None
        used_json = json.dumps(used_skills, ensure_ascii=False)
        meta_json = json.dumps(point_meta or [], ensure_ascii=False)
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO client_interest (user_id, feature_tensor, mean_tensor,"
                " used_skills, points, point_meta, embed_model, source_revision, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET"
                " feature_tensor=excluded.feature_tensor,"
                " mean_tensor=excluded.mean_tensor,"
                " used_skills=excluded.used_skills,"
                " points=excluded.points,"
                " point_meta=excluded.point_meta,"
                " embed_model=excluded.embed_model,"
                " source_revision=excluded.source_revision,"
                " updated_at=excluded.updated_at",
                (user_id, ft_blob, mt_blob, used_json, pt_blob, meta_json,
                 embed_model, source_revision, _now()),
            )
            conn.commit()
        finally:
            conn.close()

    def load_vector_cache_entries(self, user_id: str, embed_model: str) -> dict:
        """增量 embedding 复用源:``{atom_id: {summary, vector}}``。

        仅当已落盘的 ``embed_model`` 与当前一致才返回（换模型 → 空,强制整体
        重算,不混用不同模型的向量）。无画像/无 points → 空 dict。
        """
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT points, point_meta, embed_model FROM client_interest"
                " WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if row is None or not row["points"]:
                return {}
            if (row["embed_model"] or "") != (embed_model or ""):
                return {}  # 模型护栏:旧向量与当前模型不同源,作废
            pts = pickle.loads(row["points"])
            meta = json.loads(row["point_meta"] or "[]")
            return {m.get("atom_id"): {
                        "summary": m.get("summary") or "",
                        "vector": pts[i],
                    }
                    for i, m in enumerate(meta)
                    if i < len(pts) and m.get("atom_id")}
        finally:
            conn.close()

    def load_vector_cache(self, user_id: str, embed_model: str) -> dict:
        """兼容旧接口，返回 ``{atom_id: 向量(D,)}``。"""
        return {
            atom_id: entry["vector"]
            for atom_id, entry in self.load_vector_cache_entries(
                user_id, embed_model,
            ).items()
        }

    def get_revision(self, user_id: str) -> Optional[dict]:
        """返回持久化的来源版本和 embedding 模型；无画像返回 ``None``。"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT source_revision, embed_model FROM client_interest"
                " WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if row is None:
                return None
            return {
                "source_revision": row["source_revision"] or "",
                "embed_model": row["embed_model"] or "",
            }
        finally:
            conn.close()

    def load_points(self, user_id: str) -> Optional[dict]:
        """散点图数据源:``{points (n,D), meta [n], updated_at}``。

        无画像行返回 None;有行但无 points（冷启动/无 atom）返回
        ``points=None``——调用方据此显式标注,不造假点（D6）。
        """
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT points, point_meta, updated_at FROM client_interest"
                " WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if row is None:
                return None
            pts = pickle.loads(row["points"]) if row["points"] else None
            return {
                "points": pts,
                "meta": json.loads(row["point_meta"] or "[]"),
                "updated_at": row["updated_at"],
            }
        finally:
            conn.close()

    def load(self, user_id: str) -> Optional[dict]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT feature_tensor, mean_tensor, used_skills, updated_at"
                " FROM client_interest WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if row is None:
                return None
            ft = pickle.loads(row["feature_tensor"]) if row["feature_tensor"] else None
            mt = pickle.loads(row["mean_tensor"]) if row["mean_tensor"] else None
            return {
                "user_id": user_id,
                "feature_tensor": ft,
                "mean_tensor": mt,
                "used_skills": json.loads(row["used_skills"] or "[]"),
                "updated_at": row["updated_at"],
            }
        finally:
            conn.close()

    def all_means(self) -> list[tuple[str, "np.ndarray"]]:
        """所有有画像用户的 ``(user_id, mean_tensor)``，供 find_friend 检索。"""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT user_id, mean_tensor FROM client_interest WHERE mean_tensor IS NOT NULL"
            ).fetchall()
            out: list[tuple[str, np.ndarray]] = []
            for r in rows:
                if r["mean_tensor"]:
                    out.append((r["user_id"], pickle.loads(r["mean_tensor"])))
            return out
        finally:
            conn.close()
