"""dashboard/profile_viz.py — P3 画像可视化数据（§2.3 散点 / §2.5 聚类 graph）

- ``user_scatter``:某用户全部原子摘要向量 + 兴趣中心 + used_skills 向量联合
  做纯 numpy t-SNE 2D 投影（Q5 拍板升级:PCA 线性投影对高维簇分离展示效果差,
  改 t-SNE——邻域保持,簇间可读性远好于线性投影）。原子按最近兴趣簇着色,
  簇语义名取簇内 top tag。skill 向量只用 ``.skill_index.pkl`` 里已缓存的
  embedding——dashboard 无 embed_client,算不出的不显示（D6,不现算不造假点）。
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


class _TSNE2D:
    """经典 t-SNE（Van der Maaten & Hinton, 2008），纯 numpy、零外部依赖。

    比线性 PCA 投影更能展示高维语义簇的可分性——按邻域概率匹配布局,而非
    单纯最大化方差方向,同一簇的点会在 2D 里聚在一起而不是被压扁成一条线。
    PCA 初始化（无随机数生成，全程确定性）:同一批向量每次调用坐标一致
    （截图/复现稳定,不依赖不可控的随机布局）。

    点数很小时把困惑度降到 ``(n-1)/3`` 以内,避免二分搜索在稀疏样本上退化；
    n<=2 时没有"邻域结构"可言，直接给一条由数据本身决定的确定性直线。
    """

    def __init__(self, *, perplexity: float = 15.0, n_iter: int = 700):
        self._perplexity = perplexity
        self._n_iter = n_iter

    def fit(self, vectors: np.ndarray) -> np.ndarray:
        """返回 ``(n, 2)`` 低维坐标。"""
        x = np.asarray(vectors, dtype=float)
        n = x.shape[0]
        if n <= 2:
            return self._degenerate_layout(x)
        perplexity = min(self._perplexity, max(1.0, (n - 1) / 3))
        p = self._joint_probabilities(x, perplexity)
        p_early = p * 4.0  # early exaggeration(前 100 轮放大簇间距,论文标准手法)
        y = self._pca_init(x)
        y_inc = np.zeros_like(y)
        gains = np.ones_like(y)  # 自适应增益(参考 van der Maaten 原始实现):
        for it in range(self._n_iter):  # 梯度反号则升、同号则降,收敛更稳,不像固定学习率后期震荡
            q, num = self._low_dim_affinities(y)
            pq = ((p_early if it < 100 else p) - q) * num
            grad = 4 * (y * pq.sum(axis=1, keepdims=True) - pq @ y)
            momentum = 0.5 if it < 250 else 0.8
            gains = np.where((grad > 0) == (y_inc > 0), gains * 0.8, gains + 0.2)
            gains = np.maximum(gains, 0.01)
            y_inc = momentum * y_inc - 200.0 * gains * grad
            y = y + y_inc
            y = y - y.mean(axis=0)
        return y

    @staticmethod
    def _degenerate_layout(x: np.ndarray) -> np.ndarray:
        n = x.shape[0]
        if n == 0:
            return np.zeros((0, 2))
        centered = x - x.mean(axis=0)
        x0 = centered[:, 0] if centered.shape[1] else np.zeros(n)
        return np.stack([x0, np.zeros(n)], axis=1)

    @staticmethod
    def _pca_init(x: np.ndarray) -> np.ndarray:
        centered = x - x.mean(axis=0)
        _, _, vt = np.linalg.svd(centered, full_matrices=False)
        y = centered @ vt[:2].T
        if y.shape[1] < 2:
            y = np.hstack([y, np.zeros((y.shape[0], 2 - y.shape[1]))])
        scale = float(np.abs(y).max())
        return y / scale * 1e-4 if scale > 0 else y

    @staticmethod
    def _pairwise_sq_dists(x: np.ndarray) -> np.ndarray:
        sq = np.sum(x ** 2, axis=1)
        d = sq[:, None] + sq[None, :] - 2 * x @ x.T
        return np.maximum(d, 0.0)

    @classmethod
    def _joint_probabilities(cls, x: np.ndarray, perplexity: float,
                              tol: float = 1e-5, max_iter: int = 50) -> np.ndarray:
        """逐行二分搜索 precision(beta)命中目标困惑度,再对称化成联合概率。"""
        n = x.shape[0]
        sq_dists = cls._pairwise_sq_dists(x)
        target_entropy = float(np.log(perplexity))
        p = np.zeros((n, n))
        for i in range(n):
            others = [j for j in range(n) if j != i]
            di = sq_dists[i, others]
            beta_min, beta_max, beta = -np.inf, np.inf, 1.0
            row = np.zeros(len(others))
            for _ in range(max_iter):
                row = np.exp(-di * beta)
                sum_row = float(row.sum())
                if sum_row <= 1e-12:
                    entropy = 0.0
                else:
                    row = row / sum_row
                    entropy = float(-np.sum(row * np.log(row + 1e-12)))
                diff = entropy - target_entropy
                if abs(diff) < tol:
                    break
                if diff > 0:
                    beta_min = beta
                    beta = beta * 2 if beta_max == np.inf else (beta + beta_max) / 2
                else:
                    beta_max = beta
                    beta = beta / 2 if beta_min == -np.inf else (beta + beta_min) / 2
            p[i, others] = row
        p = p + p.T
        p = p / max(float(p.sum()), 1e-12)
        return np.maximum(p, 1e-12)

    @staticmethod
    def _low_dim_affinities(y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        sq = np.sum(y ** 2, axis=1)
        num = 1.0 / (1.0 + sq[:, None] + sq[None, :] - 2 * y @ y.T)
        np.fill_diagonal(num, 0.0)
        q = np.maximum(num / max(float(num.sum()), 1e-12), 1e-12)
        return q, num


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
        """t-SNE 2D 散点数据。无画像行 → KeyError（端点转 404）;有行无点
        （冷启动）→ ``points=[]`` + ``note``,显式标注不造假。

        points/centers/skill 向量一次性联合投影（t-SNE 没有 PCA 那种线性
        basis 可以对新点复用——邻域结构必须从投影一开始就联合建立,分开投影
        会让簇位置互相对不上）。
        """
        profile = self._store.load(user_key)
        stored = self._store.load_points(user_key)
        if profile is None or stored is None:
            raise KeyError(f"用户 {user_key!r} 无画像")
        if stored["points"] is None or not len(stored["meta"]):
            return {"user": user_key, "updated_at": stored["updated_at"],
                    "points": [], "centers": [], "skills": [], "clusters": [],
                    "note": "画像冷启动:该用户还没有可投影的原子"}

        points = np.asarray(stored["points"], dtype=float)
        meta = stored["meta"]

        centers = profile.get("feature_tensor")
        centers = (np.asarray(centers, dtype=float) if centers is not None
                   else np.zeros((0, points.shape[1])))

        skill_vecs = _skill_index_vecs(self._skill_dir)
        skill_entries = []
        for entry in profile.get("used_skills") or []:
            name = entry.get("name") or ""
            vec = skill_vecs.get(name)
            if vec is None or vec.shape[0] != points.shape[1]:
                continue  # 索引没有该 skill 的缓存向量 → 不画(D6),不现算
            skill_entries.append((name, entry.get("use_count", 0), vec))

        blocks = [points, centers]
        if skill_entries:
            blocks.append(np.vstack([v for _, _, v in skill_entries]))
        combined = np.vstack(blocks)
        coords_all = _TSNE2D().fit(combined)
        coords = coords_all[:len(points)]
        center_coords = coords_all[len(points):len(points) + len(centers)]
        skill_coords = coords_all[len(points) + len(centers):]

        # 簇归属:最近兴趣中心(高维余弦,向量均已 L2 归一——与 2D 投影无关,更可信)
        if len(centers):
            assignment = np.argmax(points @ centers.T, axis=1)
        else:
            assignment = np.zeros(len(meta), dtype=int)

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

        skills = [
            {"name": name,
             "x": round(float(skill_coords[i, 0]), 4),
             "y": round(float(skill_coords[i, 1]), 4),
             "use_count": use_count}
            for i, (name, use_count, _vec) in enumerate(skill_entries)
        ]

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
            "method": "tsne",
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
