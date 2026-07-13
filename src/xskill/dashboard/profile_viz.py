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


def skillhub_index_for(db_path: Optional[Path]) -> Path:
    """三方 skill 向量缓存 ``<skillhub_dir>/.skillhub_index.pkl`` 的旁推定位。

    该缓存由 ``SkillHub.index()`` 算好向量时落盘（自产 skill 有 ``.skill_index.pkl``，
    三方 skill 走这份）——dashboard 端无 embed_client，只能读缓存把用户用过的三方
    skill 画成散点 ▲（D6:读不到不画,绝不现算）。定位约定同 ``profile_db_for``:
    独立只读实例（显式 db_path）按 registry 同级的 ``skillhub_skills`` 旁推,serve
    内置挂载（db_path=None）走 config 的 ``skillhub.dir``。
    """
    if db_path is not None:
        return Path(db_path).parent / "skillhub_skills" / ".skillhub_index.pkl"
    from xskill.config import get_config, skillhub_config
    return skillhub_config(get_config())["dir"] / ".skillhub_index.pkl"


_PCA_PREDIM = 50  # 高维 embedding 进降维算法前的 PCA 预降维目标维度
_SCATTER_MAX_POINTS = 300  # 散点单次投影的原子数上限(超出按兴趣中心分层抽样)


def _stratified_sample_indices(assignment: np.ndarray, cap: int) -> list[int]:
    """按簇（兴趣中心归属）分层抽样,返回选中的原始下标（升序,确定性无随机）。

    每簇配额 ∝ 簇大小且至少 1（小簇不被整个抽没,兴趣分布不失真）;簇内用
    均匀取样跨整个簇铺开,而非只取前几个。``assignment`` 的簇数 = 兴趣中心数
    （≤5）,故配额总和 ≈ cap。
    """
    n = len(assignment)
    if n <= cap:
        return list(range(n))
    clusters: dict[int, list[int]] = {}
    for i, c in enumerate(assignment):
        clusters.setdefault(int(c), []).append(i)
    selected: list[int] = []
    for idxs in clusters.values():
        size = len(idxs)
        quota = max(1, int(cap * size / n))
        picks = np.unique(np.linspace(0, size - 1, quota).round().astype(int))
        selected.extend(idxs[p] for p in picks)
    return sorted(selected)


def _preprocess_embeddings(x: np.ndarray, pca_dim: int = _PCA_PREDIM) -> np.ndarray:
    """高维文本 embedding 的标准前置（t-SNE / UMAP 共用）:

    - **各向异性修正**:文本 embedding 全体共享一个强"公共方向"（任意两条
      文本余弦普遍 0.5+ 的底噪来源）,减去全体均值再逐行重归一化,语义差异
      才主导 pairwise 距离;
    - **PCA 预降维到 ≤``pca_dim`` 维**:去掉长尾噪声维度,pairwise 距离更能
      反映主要语义结构,计算也更快（2048 维原始空间直接算距离噪声占比高）。
    """
    x = x - x.mean(axis=0)
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    norms[norms == 0] = 1
    x = x / norms
    if x.shape[1] > pca_dim and x.shape[0] > 2:
        _, _, vt = np.linalg.svd(x, full_matrices=False)
        x = x @ vt[:pca_dim].T
    return x


class _TSNE2D:
    """经典 t-SNE（Van der Maaten & Hinton, 2008），纯 numpy、零外部依赖。

    比线性 PCA 投影更能展示高维语义簇的可分性——按邻域概率匹配布局,而非
    单纯最大化方差方向,同一簇的点会在 2D 里聚在一起而不是被压扁成一条线。
    PCA 初始化（无随机数生成，全程确定性）:同一批向量每次调用坐标一致
    （截图/复现稳定,不依赖不可控的随机布局）。

    前置各向异性修正 + PCA 预降维见 ``_preprocess_embeddings``。
    exaggeration 用 4x/100 轮——在真实 demo 数据上按 tag 分组网格实测,
    4x/100 的簇间/簇内比是 sklearn 默认 12x/250 的两倍多（几十点的小样本
    量下重放大会把布局压坏,大数据集的默认参数不适用）。

    点数很小时把困惑度降到 ``(n-1)/3`` 以内,避免二分搜索在稀疏样本上退化；
    n<=2 时没有"邻域结构"可言，直接给一条由数据本身决定的确定性直线。
    """

    _EXAGGERATION = 4.0      # early exaggeration 系数（小样本实测优于 12）
    _EXAGGERATION_ITER = 100  # exaggeration 阶段轮数
    _MOMENTUM_SWITCH = 250   # 动量 0.5→0.8 的切换轮(论文标准,与放大阶段解耦)

    def __init__(self, *, perplexity: float = 15.0, n_iter: int = 700):
        self._perplexity = perplexity
        self._n_iter = n_iter

    def fit(self, vectors: np.ndarray) -> np.ndarray:
        """返回 ``(n, 2)`` 低维坐标。"""
        x = np.asarray(vectors, dtype=float)
        n = x.shape[0]
        if n <= 2:
            return self._degenerate_layout(x)
        x = _preprocess_embeddings(x)
        perplexity = min(self._perplexity, max(1.0, (n - 1) / 3))
        p = self._joint_probabilities(x, perplexity)
        p_early = p * self._EXAGGERATION  # early exaggeration:放大簇间距
        y = self._pca_init(x)
        y_inc = np.zeros_like(y)
        gains = np.ones_like(y)  # 自适应增益(参考 van der Maaten 原始实现):
        for it in range(self._n_iter):  # 梯度反号则升、同号则降,收敛更稳,不像固定学习率后期震荡
            q, num = self._low_dim_affinities(y)
            pq = ((p_early if it < self._EXAGGERATION_ITER else p) - q) * num
            grad = 4 * (y * pq.sum(axis=1, keepdims=True) - pq @ y)
            momentum = 0.5 if it < self._MOMENTUM_SWITCH else 0.8
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


class _UMAP2D:
    """UMAP（McInnes, Healy & Melville, 2018），纯 numpy、零外部依赖。

    与 t-SNE 的核心区别:UMAP 建的是**模糊单纯复形**（fuzzy simplicial set）
    ——先对每个点用平滑 kNN 距离（局部连通半径 ``rho`` + 带宽 ``sigma``）算
    邻接隶属度,对称化成模糊并集图,再最小化高低维两张模糊图之间的**交叉熵**
    （吸引力沿边、斥力靠负采样）。相比 t-SNE 的 KL 散度,交叉熵项让 UMAP 更
    保全局结构、簇更紧凑分离,这也是别人拿它可视化 MNIST feature 效果好的原因。

    小样本（几十点）下用**全批梯度**代替 umap-learn 的随机负采样——点少时
    全批既确定又够快,且直接优化同一套交叉熵目标（``a``/``b`` 曲线拟合
    ``min_dist``/``spread``,吸引/斥力系数用参考实现同款公式）。不追求与
    umap-learn 逐位一致（那份靠 Numba+随机负采样），追求的是算法本身一致。

    确定性:谱初始化（图拉普拉斯特征向量,退化时回退 PCA 初始化）,全程无
    随机数——同一批向量每次坐标一致。
    """

    def __init__(self, *, n_neighbors: int = 15, min_dist: float = 0.1,
                 spread: float = 1.0, n_epochs: int = 500,
                 gamma: float = 1.0, init_alpha: float = 1.0):
        self._n_neighbors = n_neighbors
        self._min_dist = min_dist
        self._spread = spread
        self._n_epochs = n_epochs
        self._gamma = gamma
        self._init_alpha = init_alpha

    def fit(self, vectors: np.ndarray) -> np.ndarray:
        """返回 ``(n, 2)`` 低维坐标。"""
        x = np.asarray(vectors, dtype=float)
        n = x.shape[0]
        if n <= 2:
            return _TSNE2D._degenerate_layout(x)
        x = _preprocess_embeddings(x)
        graph = self._fuzzy_simplicial_set(x)
        a, b = self._fit_ab(self._min_dist, self._spread)
        y = self._spectral_init(graph)
        return self._optimize(y, graph, a, b)

    # ── 高维模糊图 ────────────────────────────────────────────────
    def _fuzzy_simplicial_set(self, x: np.ndarray) -> np.ndarray:
        """平滑 kNN 隶属度 + 模糊并集对称化,返回 (n,n) 加权邻接。"""
        n = x.shape[0]
        k = min(self._n_neighbors, n - 1)
        sq = _TSNE2D._pairwise_sq_dists(x)
        dist = np.sqrt(np.maximum(sq, 0.0))
        membership = np.zeros((n, n))
        target = np.log2(k) if k > 1 else 1.0
        for i in range(n):
            order = np.argsort(dist[i])
            neighbors = [j for j in order if j != i][:k]
            dneigh = dist[i, neighbors]
            rho = float(dneigh[dneigh > 0].min()) if np.any(dneigh > 0) else 0.0
            sigma = self._smooth_knn_sigma(dneigh, rho, target)
            membership[i, neighbors] = np.exp(
                -np.maximum(dneigh - rho, 0.0) / sigma)
        # 模糊并集（probabilistic t-conorm）: P = A + Aᵀ − A∘Aᵀ
        return membership + membership.T - membership * membership.T

    @staticmethod
    def _smooth_knn_sigma(dneigh: np.ndarray, rho: float, target: float,
                          tol: float = 1e-5, max_iter: int = 64) -> float:
        """二分搜索带宽 sigma,使 Σ exp(−(d−rho)₊/sigma) 命中 log2(k)。"""
        lo, hi, sigma = 0.0, np.inf, 1.0
        d = np.maximum(dneigh - rho, 0.0)
        for _ in range(max_iter):
            psum = float(np.exp(-d / sigma).sum())
            if abs(psum - target) < tol:
                break
            if psum > target:
                hi = sigma
                sigma = (lo + hi) / 2
            else:
                lo = sigma
                sigma = sigma * 2 if hi == np.inf else (lo + hi) / 2
        return max(sigma, 1e-3)

    # ── a,b 曲线拟合 min_dist/spread ─────────────────────────────
    @staticmethod
    def _fit_ab(min_dist: float, spread: float) -> tuple[float, float]:
        """拟合 1/(1+a·d^{2b}) 逼近目标隶属函数 ψ(d)（grid + 局部精修,确定性）。"""
        xs = np.linspace(0, 3 * spread, 300)
        psi = np.where(xs <= min_dist, 1.0, np.exp(-(xs - min_dist) / spread))
        a_grid = np.geomspace(0.05, 8.0, 60)
        b_grid = np.linspace(0.3, 1.6, 60)
        best, best_sse = (1.0, 1.0), np.inf
        for a in a_grid:
            for b in b_grid:
                pred = 1.0 / (1.0 + a * np.power(xs, 2 * b))
                sse = float(np.sum((pred - psi) ** 2))
                if sse < best_sse:
                    best_sse, best = sse, (float(a), float(b))
        return best

    # ── 谱初始化 ─────────────────────────────────────────────────
    @staticmethod
    def _spectral_init(graph: np.ndarray) -> np.ndarray:
        """归一化图拉普拉斯的第 2、3 特征向量;退化→PCA 初始化。"""
        n = graph.shape[0]
        deg = graph.sum(axis=1)
        deg[deg == 0] = 1e-12
        d_inv_sqrt = 1.0 / np.sqrt(deg)
        lap = np.eye(n) - (d_inv_sqrt[:, None] * graph * d_inv_sqrt[None, :])
        try:
            _vals, vecs = np.linalg.eigh((lap + lap.T) / 2)
            y = vecs[:, 1:3]
            if y.shape[1] < 2 or float(np.abs(y).max()) < 1e-9:
                raise np.linalg.LinAlgError("degenerate spectral init")
        except np.linalg.LinAlgError:
            return _TSNE2D._pca_init(graph) * 1e4  # 回退:图当特征做 PCA
        scale = float(np.abs(y).max())
        return y / scale * 10.0  # 缩放到 ±10（umap 同款初始尺度）

    # ── 交叉熵布局（全批）────────────────────────────────────────
    _MAX_STEP_NORM = 4.0  # 单点每轮位移范数上限(对齐 umap-learn 逐负样本 clip 的量级)

    def _optimize(self, y: np.ndarray, graph: np.ndarray,
                  a: float, b: float) -> np.ndarray:
        n = y.shape[0]
        neg = 1.0 - graph
        np.fill_diagonal(neg, 0.0)
        # 全批斥力项数=n,随 n 线性膨胀;缩回 umap-learn 负采样等价量级(~5*k),避免大 n 时首轮步长爆炸把点甩飞。
        neighbor_k = min(self._n_neighbors, n - 1)
        neg_row_mass = float(neg.sum(axis=1).mean())
        rep_scale = (5.0 * neighbor_k / neg_row_mass) if neg_row_mass > 0 else 1.0
        for epoch in range(self._n_epochs):
            alpha = self._init_alpha * (1.0 - epoch / self._n_epochs)
            diff = y[:, None, :] - y[None, :, :]     # (n,n,2)
            d2 = np.sum(diff ** 2, axis=2)           # (n,n)
            pos = d2 > 0
            d2b = np.zeros((n, n))
            d2b[pos] = np.power(d2[pos], b)
            denom = a * d2b + 1.0
            # 吸引力系数（沿边,权重=模糊隶属度）
            att = np.zeros((n, n))
            att[pos] = (-2.0 * a * b * np.power(d2[pos], b - 1.0)) / denom[pos]
            # 斥力系数（负采样,权重=非隶属度,缩放见上）
            rep = np.zeros((n, n))
            rep[pos] = (2.0 * self._gamma * b) / ((0.001 + d2[pos]) * denom[pos])
            coeff = graph * att + rep_scale * neg * rep  # (n,n)
            step = np.clip(coeff[:, :, None] * diff, -4.0, 4.0).sum(axis=1)
            step_norm = np.linalg.norm(step, axis=1, keepdims=True)
            step = step * np.minimum(1.0, self._MAX_STEP_NORM / np.maximum(step_norm, 1e-12))
            y = y + alpha * step
        return y - y.mean(axis=0)


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


def _skillhub_index_vecs(cache_path: Optional[Path]) -> dict[str, np.ndarray]:
    """读三方 skill 向量缓存（``skillhub.index()`` 落盘,结构对齐自产索引）。
    缓存不存在/不可读 → 空(散点图不画三方 skill 三角,D6 不现算)。"""
    if not cache_path:
        return {}
    cache_path = Path(cache_path)
    if not cache_path.is_file():
        return {}
    try:
        with open(cache_path, "rb") as f:
            idx = pickle.load(f)
        names = idx.get("skill_names") or []
        embeddings = idx.get("embeddings")
        if embeddings is None:
            return {}
        return {n: np.asarray(embeddings[i], dtype=float)
                for i, n in enumerate(names)}
    except (OSError, pickle.UnpicklingError, ValueError, IndexError):
        logger.warning("skillhub index unreadable: %s", cache_path, exc_info=True)
        return {}


class ProfileViz:
    """画像可视化数据装配（读 ProfileStore + skill 索引,纯 numpy,无 LLM）。"""

    def __init__(self, profile_db: Path, *, skill_dir: Optional[Path] = None,
                 db_path: Optional[Path] = None,
                 skillhub_index: Optional[Path] = None):
        self._store = ProfileStore(profile_db)
        self._skill_dir = skill_dir
        self._db_path = db_path
        self._skillhub_index = skillhub_index  # 三方 skill 向量缓存路径(读不到→不画)

    # ── §2.3 画像散点 ────────────────────────────────────────────

    _PROJECTORS = {"tsne": _TSNE2D, "umap": _UMAP2D}

    def user_scatter(self, user_key: str, method: str = "tsne") -> dict:
        """降维 2D 散点数据。``method`` ∈ {tsne, umap}（默认 tsne）。无画像行 →
        KeyError（端点转 404）;有行无点（冷启动）→ ``points=[]`` + ``note``,
        显式标注不造假。

        points/centers/skill 向量一次性联合投影（两种算法都没有 PCA 那种线性
        basis 可以对新点复用——邻域结构必须从投影一开始就联合建立,分开投影
        会让簇位置互相对不上）。
        """
        projector_cls = self._PROJECTORS.get(method)
        if projector_cls is None:
            raise ValueError(f"未知投影算法 {method!r}（可选 tsne/umap）")
        profile = self._store.load(user_key)
        stored = self._store.load_points(user_key)
        if profile is None or stored is None:
            raise KeyError(f"用户 {user_key!r} 无画像")
        if stored["points"] is None or not len(stored["meta"]):
            return {"user": user_key, "updated_at": stored["updated_at"],
                    "points": [], "centers": [], "skills": [], "clusters": [],
                    "method": method,
                    "note": "画像冷启动:该用户还没有可投影的原子"}

        points = np.asarray(stored["points"], dtype=float)
        meta = stored["meta"]

        centers = profile.get("feature_tensor")
        centers = (np.asarray(centers, dtype=float) if centers is not None
                   else np.zeros((0, points.shape[1])))

        # 自产 skill 向量取自 .skill_index.pkl,三方(skillhub)取自 skillhub 缓存;
        # 同名以自产优先。两个缓存都拿不到 → 不画(D6),绝不现场调 embedding。
        native_vecs = _skill_index_vecs(self._skill_dir)
        hub_vecs = _skillhub_index_vecs(self._skillhub_index)
        skill_entries = []  # (name, use_count, vec, source)
        for entry in profile.get("used_skills") or []:
            name = entry.get("name") or ""
            if name in native_vecs:
                vec, source = native_vecs[name], "native"
            elif name in hub_vecs:
                vec, source = hub_vecs[name], "skillhub"
            else:
                continue  # 两个缓存都没有该 skill 的向量 → 不画(D6),不现算
            if vec.shape[0] != points.shape[1]:
                continue  # 维度不一致(换过 embedding 模型)→ 不画,不报错
            skill_entries.append((name, entry.get("use_count", 0), vec, source))

        # 簇归属:最近兴趣中心(高维余弦,向量均已 L2 归一——投影前算,分层抽样也据它)
        if len(centers):
            assignment = np.argmax(points @ centers.T, axis=1)
        else:
            assignment = np.zeros(len(meta), dtype=int)

        # 点数超上限 → 按兴趣中心分层抽样(每簇留代表点),把投影成本压在上限内。
        # 兴趣中心 ◆/skill ▲ 永远全保留;页面显式标注"显示 N/M",不假装全画了。
        total = len(points)
        sampled = total > _SCATTER_MAX_POINTS
        if sampled:
            sel = _stratified_sample_indices(assignment, _SCATTER_MAX_POINTS)
            points = points[sel]
            meta = [meta[i] for i in sel]
            assignment = assignment[sel]

        blocks = [points, centers]
        if skill_entries:
            blocks.append(np.vstack([v for _, _, v, _ in skill_entries]))
        combined = np.vstack(blocks)
        coords_all = projector_cls().fit(combined)
        coords = coords_all[:len(points)]
        center_coords = coords_all[len(points):len(points) + len(centers)]
        skill_coords = coords_all[len(points) + len(centers):]

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
             "use_count": use_count,
             "source": source}  # 共同数据契约:native / skillhub(据向量来自哪个缓存)
            for i, (name, use_count, _vec, source) in enumerate(skill_entries)
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
            "method": method,
            "total": total,          # 该用户原子总数
            "shown": len(out_points),  # 实际投影/渲染的点数
            "sampled": sampled,      # True → 已分层抽样,页面标注"显示 N/M"
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
