"""dashboard/profile_viz.py — P3 画像可视化数据（§2.3 散点 / §2.5 聚类 graph）

- ``user_scatter``:某用户全部原子摘要向量 + 兴趣中心 + used_skills 向量做
  numpy SVD PCA 2D 投影（Q5 拍板:PCA 先行）。原子按最近兴趣簇着色,簇语义
  名取簇内 top tag。skill 向量只用 ``.skill_index.pkl`` 里已缓存的 embedding
  ——dashboard 无 embed_client,算不出的不显示（D6,不现算不造假点）。
- ``cluster_graph``:节点=有画像的用户（大小=原子数）,边=mean_tensor 余弦
  相似度 > 阈值（粗细=相似度）。边注 = 共同 top tag + 共同 skill;无边节点
  前端灰标"冷启动"。布局在前端手写 force-directed（用户十的量级）。
"""
from __future__ import annotations

import logging
import pickle
from collections import Counter
from pathlib import Path
from typing import Optional

import numpy as np

from xskill.recommend.profile_store import ProfileStore

logger = logging.getLogger("xskill.dashboard.profile_viz")

SIMILARITY_THRESHOLD = 0.6   # §2.5:mean_tensor 余弦相似度连边阈值


def profile_db_for(db_path: Optional[Path]) -> Path:
    """画像库与 registry.db 同在 XSKILL_HOME 下(``team_profile.db``,见
    api/app.py 引擎构造)——同 ``router._skill_dir_for`` 的旁推约定,独立只读
    实例与 serve 内置挂载都能解析。"""
    if db_path is not None:
        return Path(db_path).parent / "team_profile.db"
    from xskill.config import XSKILL_HOME
    return XSKILL_HOME / "team_profile.db"


def _pca_2d(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """居中 SVD PCA。返回 ``(mean (D,), components (2,D), explained)``。

    点数或维度不足 2 时主轴不满 2 根——缺的补零轴（一维数据画在 x 轴上,
    诚实反映"没有第二主轴"）。
    """
    mean = points.mean(axis=0)
    centered = points - mean
    _, singular, vt = np.linalg.svd(centered, full_matrices=False)
    components = np.zeros((2, points.shape[1]), dtype=float)
    n_axes = min(2, vt.shape[0])
    components[:n_axes] = vt[:n_axes]
    total = float((singular ** 2).sum())
    explained = float((singular[:n_axes] ** 2).sum() / total) if total > 0 else 0.0
    return mean, components, explained


def _project(vectors: np.ndarray, mean: np.ndarray,
             components: np.ndarray) -> np.ndarray:
    return (np.asarray(vectors, dtype=float) - mean) @ components.T


def _skill_index_vecs(skill_dir: Optional[Path]) -> dict[str, np.ndarray]:
    """读 ``<skill_dir>/.skill_index.pkl`` 的已缓存 skill embedding。
    索引不存在/不可读 → 空(散点图不画 skill 三角,D6 不现算)。"""
    if not skill_dir:
        return {}
    idx_path = Path(skill_dir) / ".skill_index.pkl"
    if not idx_path.is_file():
        return {}
    try:
        with open(idx_path, "rb") as f:
            idx = pickle.load(f)
        names = idx.get("skill_names") or []
        embeddings = idx.get("embeddings")
        if embeddings is None:
            return {}
        return {n: np.asarray(embeddings[i], dtype=float)
                for i, n in enumerate(names)}
    except (OSError, pickle.UnpicklingError, ValueError, IndexError):
        logger.warning("skill index unreadable: %s", idx_path, exc_info=True)
        return {}


class ProfileViz:
    """画像可视化数据装配（读 ProfileStore + skill 索引,纯 numpy,无 LLM）。"""

    def __init__(self, profile_db: Path, *, skill_dir: Optional[Path] = None,
                 db_path: Optional[Path] = None):
        self._store = ProfileStore(profile_db)
        self._skill_dir = skill_dir
        self._db_path = db_path

    # ── §2.3 画像散点 ────────────────────────────────────────────

    def user_scatter(self, user_key: str) -> dict:
        """PCA 2D 散点数据。无画像行 → KeyError（端点转 404）;有行无点
        （冷启动）→ ``points=[]`` + ``note``,显式标注不造假。"""
        profile = self._store.load(user_key)
        stored = self._store.load_points(user_key)
        if profile is None or stored is None:
            raise KeyError(f"用户 {user_key!r} 无画像")
        if stored["points"] is None or not len(stored["meta"]):
            return {"user": user_key, "updated_at": stored["updated_at"],
                    "points": [], "centers": [], "skills": [], "clusters": [],
                    "explained": 0.0,
                    "note": "画像冷启动:该用户还没有可投影的原子"}

        points = np.asarray(stored["points"], dtype=float)
        meta = stored["meta"]
        mean, components, explained = _pca_2d(points)
        coords = _project(points, mean, components)

        # 簇归属:最近兴趣中心(向量均已 L2 归一,内积即余弦)
        centers = profile.get("feature_tensor")
        if centers is not None:
            centers = np.asarray(centers, dtype=float)
            assignment = np.argmax(points @ centers.T, axis=1)
            center_coords = _project(centers, mean, components)
        else:
            assignment = np.zeros(len(meta), dtype=int)
            center_coords = np.zeros((0, 2))

        out_points = []
        cluster_tags: dict[int, Counter] = {}
        for i, m in enumerate(meta):
            cluster = int(assignment[i])
            out_points.append({
                "x": round(float(coords[i, 0]), 4),
                "y": round(float(coords[i, 1]), 4),
                "atom_id": m.get("atom_id") or "",
                "summary": (m.get("summary") or "")[:200],
                "ux": m.get("ux"),
                "cluster": cluster,
            })
            tag_counter = cluster_tags.setdefault(cluster, Counter())
            for tag in (m.get("tags") or []):
                tag = str(tag).strip().lower()
                if tag:
                    tag_counter[tag] += 1

        clusters = [
            {"cluster": c,
             "label": (cluster_tags.get(c) or Counter()).most_common(1)[0][0]
             if cluster_tags.get(c) else f"簇 {c}"}
            for c in range(max(len(center_coords), 1))
        ]

        skill_vecs = _skill_index_vecs(self._skill_dir)
        skills = []
        for entry in profile.get("used_skills") or []:
            name = entry.get("name") or ""
            vec = skill_vecs.get(name)
            if vec is None or vec.shape[0] != points.shape[1]:
                continue  # 索引没有该 skill 的缓存向量 → 不画(D6),不现算
            pos = _project(vec.reshape(1, -1), mean, components)[0]
            skills.append({"name": name,
                           "x": round(float(pos[0]), 4),
                           "y": round(float(pos[1]), 4),
                           "use_count": entry.get("use_count", 0)})

        return {
            "user": user_key,
            "updated_at": stored["updated_at"],
            "points": out_points,
            "centers": [{"cluster": i,
                         "x": round(float(center_coords[i, 0]), 4),
                         "y": round(float(center_coords[i, 1]), 4)}
                        for i in range(len(center_coords))],
            "clusters": clusters,
            "skills": skills,
            "explained": round(explained, 3),
        }

    # ── §2.5 admin 用户聚类 graph ────────────────────────────────

    def cluster_graph(self,
                      threshold: float = SIMILARITY_THRESHOLD) -> dict:
        """节点=有画像用户,边=mean_tensor 余弦相似度>threshold。

        边注:共同 top tag（双方各自 top-5 标签的交集）+ 共同 used skill。
        节点大小数据=该用户已落盘原子点数（与散点图同源,不另起口径）。
        """
        means = self._store.all_means()
        users = [u for u, _ in means]
        vectors = []
        for _, m in means:
            v = np.asarray(m, dtype=float).ravel()
            norm = float(np.linalg.norm(v))
            vectors.append(v / norm if norm > 0 else v)

        nodes = []
        top_tags: dict[str, list[str]] = {}
        used_names: dict[str, set] = {}
        for user in users:
            profile = self._store.load(user) or {}
            stored = self._store.load_points(user) or {"meta": []}
            tag_counter: Counter = Counter()
            for m in stored["meta"]:
                for tag in (m.get("tags") or []):
                    tag = str(tag).strip().lower()
                    if tag:
                        tag_counter[tag] += 1
            top_tags[user] = [t for t, _ in tag_counter.most_common(5)]
            used_names[user] = {e.get("name") or ""
                                for e in profile.get("used_skills") or []} - {""}
            nodes.append({"user": user,
                          "atoms": len(stored["meta"]),
                          "top_tags": top_tags[user][:3]})

        edges = []
        connected: set[str] = set()
        for i in range(len(users)):
            for j in range(i + 1, len(users)):
                if vectors[i].shape != vectors[j].shape:
                    continue  # 维度不一致(换过 embedding 模型)——不可比,不连边
                sim = float(vectors[i] @ vectors[j])
                if sim <= threshold:
                    continue
                a, b = users[i], users[j]
                edges.append({
                    "source": a, "target": b, "sim": round(sim, 3),
                    "common_tags": [t for t in top_tags[a]
                                    if t in top_tags[b]][:3],
                    "common_skills": sorted(used_names[a] & used_names[b])[:3],
                })
                connected.update((a, b))
        for node in nodes:
            node["isolated"] = node["user"] not in connected  # 前端灰标"冷启动"
        return {"threshold": threshold, "nodes": nodes, "edges": edges}
