"""engine.py — §5 SkillRecommendEngine

面向对象的推荐引擎：维护用户画像 vec_store（ProfileStore）+ skill vec_store
（``.skill_index.pkl``，仅 main+staging 可分发 skill，排除 baby）。

- ``update_user_interest``：atom 触发 → 重扫用户 atom 摘要 → 重新聚类 → upsert 画像。
- ``get_skill_for_client``：recommended 纯相关性（按兴趣中心轮询，每中心每轮
  取 1 个最高分未选 skill，多轮填满）；冷启动或相关性不足时 UX 序回填。
- ``resolve_side``：staging 优先达量（未达量→staging；staging 达量 main 未达量→main；
  双侧达量→``pick_side`` 确定性分流），修复 pickside 饿死。记录双向推荐。
- ``find_friend`` / ``find_tag_for_user`` / ``find_tag_for_skill``。
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
from operator import attrgetter, itemgetter
from pathlib import Path
import threading
from typing import TYPE_CHECKING, Callable, Optional

import numpy as np

from xskill.canary import CanaryConfig, fill_deficit_side, main_sha, pick_side, staging_sha
from xskill.config import recommend_config
from xskill.pipeline.atom import AtomTaskStore
from xskill.recommend.profile_store import ProfileStore
from xskill.recommend.reco_store import RecoStore
from xskill.recommend.skillhub import SearchCorpus, SkillHub
from xskill.skill.repo import SkillRepo
from xskill.utils.embed_store import EmbedStore

# tag 向量的磁盘复用缓存文件（落 traj_root，tag 集稳定，不做 prune）。
TAG_EMBED_CACHE_NAME = ".tag_embed_cache.pkl"

if TYPE_CHECKING:
    from xskill.recommend.client_interest import ClientInterest
    from xskill.recommend.client_user import ClientUser
    from xskill.skill.skill import Skill

logger = logging.getLogger("xskill.recommend.engine")


@dataclass(frozen=True)
class ProfileUpdateResult:
    """一次画像刷新结果，供后台刷新服务累计指标。"""

    changed: bool
    embed_items: int
    source_revision: str
    embed_batches: int = 0
    reused_vector_items: int = 0
    cancelled: bool = False


def _normalize_rows(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=1, keepdims=True)
    n[n == 0] = 1
    return v / n


class SkillRecommendEngine:
    """推荐引擎。team server 进程持有单例。"""

    def __init__(
        self,
        *,
        config: dict,
        skill_dir: Path | str,
        traj_root: Path | str,
        embed_client,
        profile_db: Path | str,
        canary_config: Optional[CanaryConfig] = None,
        client_registry=None,
    ):
        self.config = config
        self.skill_dir = Path(skill_dir)
        self.traj_root = Path(traj_root)
        self.embed_client = embed_client
        recommend_config(config)  # fail-loud：构造期就拒掉畸形 recommend 段
        self.profile_store = ProfileStore(profile_db)
        self.reco_store = RecoStore(profile_db)
        # 显式传入的 canary 配置是**覆盖**（测试/离线 worker 用），None 时
        # canary_cfg 属性每次现取 self.config 的 canary 段（热生效）。
        self._canary_override = canary_config
        self._skill_index_cache: Optional[dict] = None
        self._skillhub_cache: Optional[tuple[tuple, list[dict]]] = None
        # 合并候选池(names/embs/is_hub)客户端无关,只随 skillhub 缓存 / .skill_index
        # / 可分发集合变化。single-flight 缓存:32 个并发 /sync 只在其一变化后重建
        # 一次万行矩阵,不再每请求每 worker 各拼一遍。invalidate_cache 清空。
        self._combined_pool_cache: Optional[tuple] = None
        self._combined_pool_lock = threading.Lock()
        self._repo_search_corpus_cache: Optional[tuple] = None
        self._repo_search_corpus_lock = threading.Lock()
        self._profile_row_cache: dict[str, dict | None] = {}
        self._profile_cache_generation: dict[str, int] = {}
        self._profile_cache_lock = threading.Lock()
        # 保留该属性以兼容仍会清理旧进程内缓存的调用方；画像新鲜度以数据库中的
        # source_revision 为准，进程重启不会导致无谓重算。
        self._profile_fp_cache: dict[str, tuple] = {}
        self.skillhub = SkillHub.from_config(config, embed_client)
        self.client_registry = client_registry  # 用于 user_name → 目录名解析

    # ── 热生效配置（现取，不快照） ─────────────────────────────────
    # ``recommend.quality_ratio`` / ``staging_need`` 与 ``canary`` 都是
    # HOT_RELOAD 段：admin_config_reload 原地 mutate serve 进程的 _config dict
    # （engine 持同一引用），故每次现取即热生效。曾在 __init__ 里快照成
    # self.rcfg / self.staging_need，导致面板改完静默不生效、必须重启 serve
    # （invalidate_cache 只清 skill 索引缓存，不会重解析配置）。

    @property
    def rcfg(self) -> dict:
        """现取 ``recommend`` 段（已校验）。畸形值照常 fail-loud 抛 ValueError。"""
        return recommend_config(self.config)

    @property
    def canary_cfg(self) -> CanaryConfig:
        """构造时显式传入的覆盖优先；否则现取 ``canary`` 段。"""
        if self._canary_override is not None:
            return self._canary_override
        return CanaryConfig.from_dict(self.config.get("canary", {}) or {})

    @property
    def staging_need(self) -> int:
        """推荐侧达量阈值。单一推导来源：``recommend.staging_need``，
        未配（None）时复用 ``canary.min_samples``。"""
        return self.rcfg["staging_need"] or self.canary_cfg.min_samples

    # ─§6 三方 skill 检索池 ────────────────────────────────────────
    def _skillhub_entries(self) -> list[dict]:
        """三方 skill ``{name, vec}``（按内容指纹缓存）。禁用时为空。"""
        fp = self.skillhub.fingerprint()
        # fingerprint() 不变盘时返回同一 tuple 对象,故 `is not` 做 O(1) 失效判断,
        # 不再每请求对万元素 tuple 逐位比较。
        if self._skillhub_cache is None or self._skillhub_cache[0] is not fp:
            self._skillhub_cache = (fp, self.skillhub.index())
        return self._skillhub_cache[1]

    def _combined_relevance(
        self, distributable_skills: Optional[list["Skill"]] = None,
    ) -> tuple[list[str], np.ndarray, dict[str, bool]]:
        """合并检索池：可分发 skill 的 desc 向量 + 三方 skill 向量。

        返回 ``(names, embeddings, is_skillhub)``。三方 skill 标记 True（仅相关性位）。

        结果客户端无关——只随 skillhub 缓存 / ``.skill_index.pkl`` / 可分发集合变化。
        按这三者身份缓存并 single-flight：32 个并发 /sync 只在其一变化后重建一次万行
        候选矩阵(1 万 skill 下每次约 120MB 的 ``repo_embs[keep]`` 拷贝 + O(N) 组装，
        是探针超时的主因之一)，其余请求 O(1) 命中。``invalidate_cache`` 清空。
        """
        self._skillhub_entries()  # 触发/刷新 self._skillhub_cache
        # 一次性捕获 skillhub 缓存代 tuple:hub_entries(建矩阵用)与缓存键必须同代。
        # _skillhub_entries 无锁改写 self._skillhub_cache,若键与数据分两次读,并发下
        # 可能用旧代 hub_entries 建的结果存到新代键上→后续请求读到陈旧推荐。
        hub_cache = self._skillhub_cache
        hub_entries = hub_cache[1]
        idx_path = self.skill_dir / ".skill_index.pkl"
        idx = self._skill_index() if idx_path.is_file() else None
        pool = (
            distributable_skills
            if distributable_skills is not None
            else self._distributable_skills()
        )
        distributable = frozenset(skill.name for skill in pool)
        cache = self._combined_pool_cache
        if (cache is not None and cache[0] is hub_cache
                and cache[1] is idx and cache[2] == distributable):
            return cache[3]
        with self._combined_pool_lock:
            cache = self._combined_pool_cache
            if (cache is not None and cache[0] is hub_cache
                    and cache[1] is idx and cache[2] == distributable):
                return cache[3]
            if idx is not None:
                repo_names = list(idx.get("skill_names") or [])
                repo_embs = np.asarray(idx["embeddings"], dtype=float)
            else:
                repo_names = []
                dim = len(hub_entries[0]["vec"]) if hub_entries else 0
                repo_embs = np.zeros((0, dim), dtype=float)
            # 仅保留可分发 skill（排除 baby / 已删）
            keep = [i for i, name in enumerate(repo_names) if name in distributable]
            names = [repo_names[i] for i in keep]
            embs = repo_embs[keep] if keep else np.zeros(
                (0, repo_embs.shape[1] if repo_embs.ndim == 2 else 0)
            )
            is_hub = {name: False for name in names}
            # 三方 skill 向量先收集成列表、末尾单次 vstack。循环内逐行 np.vstack 是
            # 意外二次复杂度:第 k 行复制整个已累积矩阵(总搬运 O(n²)),且每行一次
            # GIL-held 的 Python 调用。上万 skillhub skill 时把 /sync 的 32 个 worker
            # 线程焊死在核上、饿死事件循环令探针超时。参照 skillhub.py 已有写法。
            hub_vectors: list[np.ndarray] = []
            for entry in hub_entries:
                if entry["name"] in is_hub:
                    continue  # 自有 skill 同名优先
                names.append(entry["name"])
                hub_vectors.append(np.asarray(entry["vec"], dtype=float))
                is_hub[entry["name"]] = True
            if hub_vectors:
                hub_matrix = np.vstack(hub_vectors)
                embs = np.vstack([embs, hub_matrix]) if len(embs) else hub_matrix
            result = (names, embs, is_hub)
            self._combined_pool_cache = (hub_cache, idx, distributable, result)
            return result

    # ── skill 索引 / 池 ───────────────────────────────────────────
    def invalidate_cache(self) -> None:
        """失效 skill 索引 / skillhub 缓存。``/reindex`` 或 skill 增删后调用，
        确保下次检索读最新 ``.skill_index.pkl`` 与三方 skill 目录。"""
        self._skill_index_cache = None
        self._skillhub_cache = None
        self._combined_pool_cache = None
        with self._repo_search_corpus_lock:
            self._repo_search_corpus_cache = None

    def repo_search_corpus(self, catalog) -> SearchCorpus:
        """把可分发 repo skill 投影为 hybrid search 补充语料。

        文档始终进入 BM25；只有 ``.skill_index.pkl`` 中 name/text/vec 同代且
        结构有效时才附加语义向量。
        """
        with self._repo_search_corpus_lock:
            index_path = self.skill_dir / ".skill_index.pkl"
            index = self._skill_index() if index_path.is_file() else None
            cache = self._repo_search_corpus_cache
            if cache is not None and cache[0] is catalog and cache[1] is index:
                return cache[3]

            skills = tuple(catalog.search_by_id.values())
            documents = tuple((
                skill,
                catalog.refs[skill.name][0],
                (skill.description or "").strip(),
            ) for skill in skills)
            fingerprint = tuple(
                (skill.name, main_ref, description)
                for skill, main_ref, description in documents
            )
            if (
                cache is not None and cache[1] is index
                and cache[2] == fingerprint
            ):
                self._repo_search_corpus_cache = (
                    catalog, index, fingerprint, cache[3],
                )
                return cache[3]

            vectors_by_name: dict[str, tuple[str, np.ndarray]] = {}
            current_model = str(getattr(self.embed_client, "model", "") or "")
            if index is not None and index.get("model") == current_model:
                names = list(index.get("skill_names") or [])
                texts = list(index.get("texts") or [])
                raw_vectors = index.get("embeddings")
                try:
                    vectors = (
                        np.asarray(raw_vectors, dtype=float)
                        if raw_vectors is not None else np.empty((0, 0))
                    )
                except (TypeError, ValueError):
                    vectors = np.empty((0, 0))
                if (
                    vectors.ndim == 2
                    and len(names) == len(texts) == vectors.shape[0]
                ):
                    vectors_by_name = {
                        name: (str(text), vectors[row])
                        for row, (name, text) in enumerate(zip(names, texts))
                        if isinstance(name, str)
                    }
                elif names or texts or raw_vectors is not None:
                    logger.warning(
                        "repo skill index shape mismatch; hybrid search uses BM25 only"
                    )

            entries: list[dict] = []
            for skill, main_ref, description in documents:
                entry = {
                    "source": "repo",
                    "name": f"repo:{skill.name}",
                    "skill_id": f"repo:{skill.name}",
                    "repo_name": skill.name,
                    "display_name": str(
                        skill.frontmatter.get("name") or skill.name
                    ),
                    "description": description,
                    "source_path": skill.name,
                    "content_sha": main_ref,
                    "path": skill.path,
                    "ux_side": "main",
                }
                indexed = vectors_by_name.get(skill.name)
                if indexed is not None and indexed[0].strip() == description:
                    entry["vec"] = indexed[1]
                entries.append(entry)

            corpus = tuple(entries)
            self._repo_search_corpus_cache = (
                catalog, index, fingerprint, corpus,
            )
            return corpus

    def search_team_skills(self, query: str, limit: int, catalog) -> list[dict]:
        """在自产 + SkillHub 的统一 BM25/semantic/RRF corpus 中搜索。"""
        results, _meta = self.search_team_skills_with_meta(query, limit, catalog)
        return results

    def search_team_skills_with_meta(
        self, query: str, limit: int, catalog,
    ) -> tuple[list[dict], dict]:
        """同 ``search_team_skills``，附带 SkillHub 检索元信息。"""
        corpus = self.repo_search_corpus(catalog)
        return self.skillhub.search_with_meta(
            query, limit, supplemental=corpus,
        )

    def _skill_index(self) -> dict:
        if self._skill_index_cache is None:
            import pickle
            idx_path = self.skill_dir / ".skill_index.pkl"
            if not idx_path.is_file():
                raise RuntimeError(
                    f"skill 索引不存在: {idx_path}；请跑 `xskill rebuild` 重建"
                )
            with open(idx_path, "rb") as f:
                self._skill_index_cache = pickle.load(f)
        return self._skill_index_cache

    def _repo(self) -> SkillRepo:
        return SkillRepo(self.skill_dir)

    def _distributable_skills(self) -> list["Skill"]:
        """可分发 skill = 有 main 分支（baby-only 不入池）。"""
        return [s for s in self._repo() if main_sha(s.path)]

    @staticmethod
    def _quality_key(
        skill: "Skill", refs: tuple[str, str | None] | None = None,
    ) -> tuple[float, int]:
        """复用 manifest 已读 ref 计算质量排序，避免再次查询 Git。"""
        if refs is None:
            avg = skill.ux_avg(side="main", days=30)
        else:
            main_ref = refs[0]
            rows = skill.recent_ux_scores(side="main", days=30)
            scores = [
                row.get("score") for row in rows
                if row.get("commit_sha") == main_ref
                and isinstance(row.get("score"), (int, float))
            ]
            avg = sum(scores) / len(scores) if scores else None
        return (avg if avg is not None else 0.0, skill.use_count)

    # ── 用户 atom 派生 ────────────────────────────────────────────
    def _client_store_root(self, user_id: str) -> Path:
        """该 client 的 atom store 根。目录名优先用 user_name 明文（可读），
        匿名用 client_id。需 client_registry 解析；未注入时退回 client_id（hex）。"""
        dir_name = user_id
        if self.client_registry is not None:
            try:
                dir_name = self.client_registry.dir_name_for(user_id)
            except Exception:  # pylint: disable=broad-exception-caught
                dir_name = user_id
        return self.traj_root / "clients" / dir_name / "sessions"

    def _user_atoms(self, user_id: str):
        root = self._client_store_root(user_id)
        if not root.is_dir():
            return []
        return list(AtomTaskStore(root=root).all_atoms())

    @staticmethod
    def _used_skills_from_atoms(atoms: list) -> list[dict]:
        """从用户 atom 聚合 ``{name, use_count, avg_score}``。"""
        agg: dict[str, list[float]] = {}
        for atom in atoms:
            for name in (atom.used_skills or []):
                agg.setdefault(name, []).append(
                    float(atom.ux_score) if atom.ux_score is not None else 0.0
                )
        out: list[dict] = []
        for name, scores in agg.items():
            out.append({
                "name": name,
                "use_count": len(scores),
                "avg_score": sum(scores) / len(scores),
            })
        out.sort(key=itemgetter("use_count"), reverse=True)
        return out

    def _user_used_skills(self, user_id: str) -> list[dict]:
        """兼容旧调用；画像刷新路径使用单次 atom 快照。"""
        return self._used_skills_from_atoms(self._user_atoms(user_id))

    # ── 5.2 update_user_interest ─────────────────────────────────
    @staticmethod
    def _atom_revision(atoms: list) -> str:
        """对单次 atom 快照生成稳定版本；内容原地变化也能被发现。"""
        payload = [{
            "atom_id": atom.atom_id,
            "summary": atom.summary or "",
            "used_skills": sorted(atom.used_skills or []),
            "ux_score": atom.ux_score,
            "tags": sorted(atom.tags or []),
        } for atom in sorted(atoms, key=attrgetter("atom_id"))]
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def update_user_interest(
        self,
        client_interest: "ClientInterest",
        task_atom=None,
        *,
        should_commit: Optional[Callable[[], bool]] = None,
    ) -> ProfileUpdateResult:
        """atom 触发：重扫用户 atom 摘要 → 重新聚类 → upsert 画像。

        ``task_atom`` 为触发事件（增量优化预留，当前以 atom store 为单一真源重扫）。
        新鲜度版本持久化到 SQLite；只有完整计算和 upsert 成功后才推进版本。
        """
        del task_atom  # Preserve keyword compatibility; the store is authoritative.
        user_id = client_interest.user_id
        snapshot = sorted(self._user_atoms(user_id), key=attrgetter("atom_id"))
        revision = self._atom_revision(snapshot)
        model = getattr(self.embed_client, "model", "") or ""
        persisted = self.profile_store.get_revision(user_id)
        if (persisted is not None
                and persisted["source_revision"] == revision
                and persisted["embed_model"] == model):
            return ProfileUpdateResult(
                changed=False, embed_items=0, source_revision=revision,
            )

        used_skills = self._used_skills_from_atoms(snapshot)
        atoms = [atom for atom in snapshot if atom.summary]
        if not atoms:
            if should_commit is not None and not should_commit():
                return ProfileUpdateResult(
                    changed=False,
                    embed_items=0,
                    source_revision=revision,
                    cancelled=True,
                )
            self.profile_store.upsert(
                user_id, feature_tensor=None, mean_tensor=None, used_skills=used_skills,
                embed_model=model, source_revision=revision,
            )
            self._publish_profile_cache(
                user_id,
                feature_tensor=None,
                mean_tensor=None,
                used_skills=used_skills,
            )
            return ProfileUpdateResult(
                changed=True, embed_items=0, source_revision=revision,
            )
        if should_commit is not None and not should_commit():
            return ProfileUpdateResult(
                changed=False,
                embed_items=0,
                source_revision=revision,
                cancelled=True,
            )
        vecs, embed_items, reused_items = self._embed_atoms_incremental(user_id, atoms)
        client_interest.reset_points(vecs)
        ft = client_interest.feature_tensor
        mt = client_interest.mean_tensor
        # P3-3.4(Q4):原子点向量 + 逐点元数据随画像顺手落盘,供散点图直读投影
        point_meta = [{"atom_id": a.atom_id, "summary": a.summary,
                       "ux": a.ux_score, "tags": list(a.tags or [])}
                      for a in atoms]
        # stop() 可能在慢 embedding 执行期间发生。后台服务传入的检查必须放在
        # 最终 upsert 之前，避免服务停止后把已经过时的快照写回数据库。
        if should_commit is not None and not should_commit():
            return ProfileUpdateResult(
                changed=False,
                embed_items=embed_items,
                source_revision=revision,
                embed_batches=int(embed_items > 0),
                reused_vector_items=reused_items,
                cancelled=True,
            )
        self.profile_store.upsert(
            user_id, feature_tensor=ft, mean_tensor=mt, used_skills=used_skills,
            points=vecs, point_meta=point_meta,
            embed_model=model, source_revision=revision,
        )
        self._publish_profile_cache(
            user_id,
            feature_tensor=ft,
            mean_tensor=mt,
            used_skills=used_skills,
        )
        return ProfileUpdateResult(
            changed=True, embed_items=embed_items, source_revision=revision,
            embed_batches=int(embed_items > 0),
            reused_vector_items=reused_items,
        )

    def _embed_atoms_incremental(
        self, user_id: str, atoms: list,
    ) -> tuple[np.ndarray, int, int]:
        """只对新增或 summary 变化的原子调 embedding。

        只有 ``atom_id`` 和 ``summary`` 都一致才复用；summary 原地变化时只重算该条。
        换 embedding 模型时 ``load_vector_cache`` 返回空 → 整体重算（护栏在存储层）。
        避免一个攒了上万原子的用户每次新增几条就全量重 embed。
        """
        model = getattr(self.embed_client, "model", "")
        cache = self.profile_store.load_vector_cache_entries(user_id, model)
        missing = [
            atom for atom in atoms
            if atom.atom_id not in cache
            or cache[atom.atom_id]["summary"] != (atom.summary or "")
        ]
        if missing:
            fresh = _normalize_rows(np.asarray(
                self.embed_client.encode_batch([a.summary for a in missing]),
                dtype=float,
            ))
            cache = dict(cache)
            for a, v in zip(missing, fresh):
                cache[a.atom_id] = {"summary": a.summary or "", "vector": v}
        return (
            np.asarray([cache[a.atom_id]["vector"] for a in atoms], dtype=float),
            len(missing),
            len(atoms) - len(missing),
        )

    # ── 5.3 get_skill_for_client ─────────────────────────────────
    def get_skill_for_client(
        self, client_user: "ClientUser", skill_num: int,
        *, exclude_names: Optional[set[str]] = None,
        candidate_pool: Optional[list["Skill"]] = None,
        candidate_refs: Optional[dict[str, tuple[str, str | None]]] = None,
        persist_recommendations: bool = True,
        candidate_pool_quality_ordered: bool = False,
    ) -> list["Skill"]:
        """recommended 纯相关性：按兴趣中心轮询（每中心每轮 1 个），多轮填满
        ``skill_num``；冷启动或相关性不足时 UX 序回填；记录推荐 + resolve side。

        ``exclude_names``：从候选池排除的 skill 名（如已占 ranked 槽位的），供
        ``_pick_recommended`` 在 ranked 之外选 recommended 位用。
        ``candidate_pool_quality_ordered`` 表示调用方已按同一质量键排好候选，
        可避免 manifest 热路径重复读取每个 skill 的评分文件（仅用于 UX 回填）。
        """
        source_pool = (
            list(candidate_pool)
            if candidate_pool is not None
            else self._distributable_skills()
        )
        pool = source_pool
        if exclude_names:
            pool = [s for s in pool if s.name not in exclude_names]

        if candidate_pool_quality_ordered:
            quality_ordered = pool
        else:
            quality_keys = {
                s.name: self._quality_key(
                    s,
                    candidate_refs.get(s.name)
                    if candidate_refs is not None else None,
                )
                for s in pool
            }
            # decorate-sort-undecorate：排序键是"拿 skill.name 查表"，itemgetter
            # 表达不了对象→键的映射，故先把键贴成元组首位再排（禁 lambda）。
            # list.sort 稳定且只比较首位，与原 sorted(reverse=True) 的并列次序一致。
            decorated = [(quality_keys[skill.name], skill) for skill in pool]
            decorated.sort(key=itemgetter(0), reverse=True)
            quality_ordered = [skill for _key, skill in decorated]

        relevance: list["Skill"] = []
        picked: set[str] = set()
        ci = client_user.client_interest
        if ci is not None and ci.feature_tensor is not None:
            names, embs, is_hub = self._combined_relevance(source_pool)
            by_name = {s.name: s for s in pool}  # pool 已排除 exclude_names
            centers = list(ci.feature_tensor)
            if len(centers) > 0 and embs.shape[0] > 0:
                while len(relevance) < skill_num:
                    progress = False
                    for center in centers:
                        if len(relevance) >= skill_num:
                            break
                        sims = embs @ np.asarray(center, dtype=float)
                        order = np.argsort(-sims)
                        for i in order:
                            nm = names[i]
                            if exclude_names and nm in exclude_names:
                                continue
                            if nm in picked:
                                continue
                            if is_hub.get(nm):
                                entry = self.skillhub.entry(nm)
                                if entry is None:
                                    continue
                                relevance.append(entry)
                            elif nm in by_name:
                                relevance.append(by_name[nm])
                            else:
                                continue
                            picked.add(nm)
                            progress = True
                            break  # 每中心每轮只取 1 个
                    if not progress:
                        break

        chosen = relevance
        # 回填：冷启动或相关性不足时从 pool（ux 序）补齐至 skill_num
        if len(chosen) < skill_num:
            for s in quality_ordered:
                if len(chosen) >= skill_num:
                    break
                if s.name not in picked:
                    chosen.append(s)
                    picked.add(s.name)

        chosen = chosen[:skill_num]
        # 记录推荐 + resolve side（双向）
        client_user.recommended_skills = []
        recommendation_records: list[tuple[str, str, str]] = []
        for s in chosen:
            if isinstance(s, dict) and s.get("source") == "skillhub":
                side = "main"
                sha = s["content_sha"]
                skill_name = s["skill_id"]
                rec = {
                    "skill": skill_name,
                    "branch": side,
                    "hash": sha,
                    "source": "skillhub",
                    "display_name": s["display_name"],
                    "source_path": s["source_path"],
                }
            else:
                cached = candidate_refs.get(s.name) if candidate_refs is not None else None
                side = self.resolve_side(s, client_user, refs=cached)
                if cached is not None:
                    sha = cached[1] if side == "staging" else cached[0]
                else:
                    sha = staging_sha(s.path) if side == "staging" else (main_sha(s.path) or "")
                skill_name = s.name
                rec = {"skill": skill_name, "branch": side, "hash": sha}
            recommendation_records.append((skill_name, side, sha))
            client_user.recommended_skills.append(rec)
        if persist_recommendations:
            self.reco_store.record_many(
                user_id=client_user.user_id,
                records=recommendation_records,
            )
        return chosen

    # ── 5.4 resolve_side：staging 优先达量 ───────────────────────
    def _side_count(self, skill_dir: Path, side: str, sha: str) -> int:
        from xskill.canary import recent_scores
        return len(recent_scores(skill_dir, side=side, commit_sha=sha, n=self.staging_need + 1))

    def resolve_side(
        self, skill: "Skill", client_user: "ClientUser",
        *, refs: tuple[str, str | None] | None = None,
    ) -> str:
        """staging 优先达量：未达量→staging；staging 达量 main 未达量→main；双侧达量→pick_side。

        双侧达量时用 ``pick_side(user_id, skill_name, probability)`` 做确定性分流
        （main 分支上的既有机制）。

        「最可能用该 skill 的用户」优先：staging 在推荐链路中只被分给**已被推荐该 skill**
        的用户（``get_skill_for_client`` 按用户画像相关性 + ux 质量选出），即被推荐者本身
        就是该 skill 的最可能用户——故 staging 未达量时直接给 staging 即满足 spec D6 的
        「最可能用户优先消费 staging」。跨用户的显式时间序排序留作后续优化。
        """
        if refs is None:
            m_sha = main_sha(skill.path) or ""
            s_sha = staging_sha(skill.path)
        else:
            m_sha, s_sha = refs
        if not s_sha:
            return "main"
        staging_n = self._side_count(skill.path, "staging", s_sha)
        main_n = self._side_count(skill.path, "main", m_sha)
        fallback = pick_side(
            client_user.user_id, skill.name, self.canary_cfg.probability)
        return fill_deficit_side(
            staging_n=staging_n, main_n=main_n,
            need=self.staging_need, fallback=fallback,
        )

    # ── 5.6 find_friend ──────────────────────────────────────────
    def relevance_search(self, query_vec, top_k: int = 5) -> list[tuple[str, bool]]:
        """在合并检索池（可分发 + 三方 skill）做 KNN，返回 ``(name, is_skillhub)``。

        优先读重活进程维护的 Milvus Lite 索引；不可用时退回原 numpy 全库乘。
        """
        milvus_hits = self._relevance_search_milvus(query_vec, top_k=top_k)
        if milvus_hits is not None:
            return milvus_hits
        names, embs, is_hub = self._combined_relevance()
        if embs.shape[0] == 0:
            return []
        sims = embs @ np.asarray(query_vec, dtype=float)
        order = np.argsort(-sims)[:top_k]
        return [(names[i], is_hub.get(names[i], False)) for i in order]

    def _relevance_search_milvus(
        self, query_vec, *, top_k: int,
    ) -> Optional[list[tuple[str, bool]]]:
        try:
            from xskill.config import XSKILL_HOME
            from xskill.recommend.skill_vector_store import (
                default_vector_db_path,
                pymilvus_available,
                try_open_milvus_lite_index,
                warn_milvus_unavailable_hourly,
            )

            if not pymilvus_available():
                warn_milvus_unavailable_hourly("pymilvus not installed")
                return None
            path = default_vector_db_path(XSKILL_HOME)
            if not path.is_file():
                return None
            index = try_open_milvus_lite_index(path, dim=len(query_vec))
            if index is None:
                return None
            hits = index.search(list(map(float, query_vec)), top_k=top_k)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("milvus relevance_search unavailable", exc_info=True)
            return None
        out: list[tuple[str, bool]] = []
        for catalog_key, _score in hits:
            is_hub = catalog_key.startswith("skillhub:")
            name = catalog_key.split(":", 1)[-1] if ":" in catalog_key else catalog_key
            row = index.get(catalog_key)
            if row and row.get("name"):
                name = row["name"]
            out.append((name, is_hub))
        return out

    def load_client_user(
        self, user_id: str, *, include_recommended: bool = True,
    ) -> "ClientUser":
        """从持久化加载 ``ClientUser``（画像 + used_skills + recommended_skills）。

        无画像行 → 冷启动 ``ClientUser``（client_interest=None）。
        manifest 计算不读取 ``recommended_skills``，因为它只是反查视图，
        不参与候选排序；其他调用默认保持完整加载。
        """
        from xskill.recommend.client_interest import ClientInterest
        from xskill.recommend.client_user import ClientUser
        with self._profile_cache_lock:
            if user_id in self._profile_row_cache:
                row = self._profile_row_cache[user_id]
                generation = None
            else:
                row = None
                generation = self._profile_cache_generation.get(user_id, 0)
        if generation is not None:
            loaded = self.profile_store.load(user_id)
            with self._profile_cache_lock:
                if self._profile_cache_generation.get(user_id, 0) == generation:
                    self._profile_row_cache[user_id] = loaded
                    row = loaded
                else:
                    row = self._profile_row_cache.get(user_id)
        if row is None:
            return ClientUser(user_id)
        ci = ClientInterest(
            user_id,
            feature_tensor=row["feature_tensor"],
            mean_tensor=row["mean_tensor"],
        )
        return ClientUser(
            user_id, client_interest=ci,
            used_skills=row["used_skills"],
            recommended_skills=(
                self.reco_store.skills_for_user(user_id)
                if include_recommended else []
            ),
        )

    def _publish_profile_cache(
        self,
        user_id: str,
        *,
        feature_tensor,
        mean_tensor,
        used_skills: list[dict],
    ) -> None:
        """画像事务提交后发布同一份只读推荐视图。"""
        row = {
            "feature_tensor": feature_tensor,
            "mean_tensor": mean_tensor,
            "used_skills": used_skills,
        }
        with self._profile_cache_lock:
            self._profile_cache_generation[user_id] = (
                self._profile_cache_generation.get(user_id, 0) + 1
            )
            self._profile_row_cache[user_id] = row

    def find_friend(self, client_user: "ClientUser", top_k: int = 5) -> list[str]:
        """按 mean_tensor 相似度检索其他用户。"""
        ci = client_user.client_interest
        if ci is None or ci.mean_tensor is None:
            return []
        mine = np.asarray(ci.mean_tensor, dtype=float)
        others = [(uid, m) for uid, m in self.profile_store.all_means()
                  if uid != client_user.user_id]
        if not others:
            return []
        scored = [(uid, float(np.asarray(mean, dtype=float) @ mine))
                  for uid, mean in others]
        scored.sort(key=itemgetter(1), reverse=True)
        return [uid for uid, _score in scored[:top_k]]

    # ── 5.7 find_tag_for_user / find_tag_for_skill ────────────────
    def _all_tags_with_embeds(self) -> list[tuple[str, np.ndarray]]:
        """收集 traj_root 下所有 atom 的 tag → embedding（去重）。"""
        seen: set[str] = set()
        tags: list[str] = []
        clients_dir = self.traj_root / "clients"
        if not clients_dir.is_dir():
            return []
        for client_dir in sorted(clients_dir.iterdir()):
            if not client_dir.is_dir():
                continue
            root = client_dir / "sessions"
            if not root.is_dir():
                continue
            for atom in AtomTaskStore(root=root).all_atoms():
                for t in (atom.tags or []):
                    if t not in seen:
                        seen.add(t)
                        tags.append(t)
        if not tags:
            return []
        tag_embed_store = EmbedStore(
            self.traj_root / TAG_EMBED_CACHE_NAME, self.embed_client,
        )
        vecs = _normalize_rows(np.asarray(
            tag_embed_store.encode_cached(tags), dtype=float,
        ))
        return list(zip(tags, vecs))

    def find_tag_for_user(self, client_user: "ClientUser", top_k: int = 5) -> list[str]:
        ci = client_user.client_interest
        if ci is None or ci.mean_tensor is None:
            return []
        mine = np.asarray(ci.mean_tensor, dtype=float)
        tag_vecs = self._all_tags_with_embeds()
        if not tag_vecs:
            return []
        scored = [(tag, float(np.asarray(vec, dtype=float) @ mine))
                  for tag, vec in tag_vecs]
        scored.sort(key=itemgetter(1), reverse=True)
        return [tag for tag, _score in scored[:top_k]]

    def find_tag_for_skill(self, skill: "Skill", top_k: int = 10) -> list[str]:
        """该 skill 被路由 atom 的 ``AtomTask.tags`` 按 tag embedding 与 skill 向量的
        相似度排序（atom 级 tag 语义检索），返回最相关 top_k 个 tag。

        与 ``find_tag_for_user`` 同走 tag embedding 索引，只是 query 向量换成 skill 的
        ``vec``（description 向量）。
        """
        clients_dir = self.traj_root / "clients"
        if not clients_dir.is_dir():
            return []
        # 收集该 skill 被路由 atom 的全部 tag（去重）
        seen: set[str] = set()
        for client_dir in sorted(clients_dir.iterdir()):
            if not client_dir.is_dir():
                continue
            root = client_dir / "sessions"
            if not root.is_dir():
                continue
            for atom in AtomTaskStore(root=root).all_atoms():
                if skill.name in (atom.used_skills or []):
                    for t in (atom.tags or []):
                        seen.add(t)
        if not seen:
            return []
        # 按 tag embedding 与 skill vec 的 cosine 相似度排序
        tags = list(seen)
        try:
            skill_vec = np.asarray(skill.vec, dtype=float)
        except Exception:  # pylint: disable=broad-exception-caught
            return tags[:top_k]  # 无 vec（如无 description）→ 退回集合截断
        tag_embed_store = EmbedStore(
            self.traj_root / TAG_EMBED_CACHE_NAME, self.embed_client,
        )
        tag_vecs = _normalize_rows(np.asarray(
            tag_embed_store.encode_cached(tags), dtype=float,
        ))
        sims = tag_vecs @ skill_vec
        order = np.argsort(-sims)
        return [tags[i] for i in order[:top_k]]
