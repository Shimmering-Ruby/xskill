"""skill_manifest.py — 给一个 client 现算它该持有的 ≤100 个 skill slot（SP1）

server 端**不存"账本表"**。manifest = ``pick_side`` 纯函数 + skill git
状态（has_staging / main_sha / staging_sha）的实时投影，每次 sync 现算。

slot 结构 = 80 ranked + 20 recommended：
- ranked      —— 按 ux_score（main 侧近 30 天均分）滑窗取高分。
- recommended —— SP3 = 用户画像质心推荐位：基于该 client 用过的 skill 的质心，
                 从候选里取 cosine 最近邻（``profile_reco.py``）。无画像
                 （冷启动）或非 team server 调用 → 退回 ux 排序往下取。

灰度归因：某 skill 有 staging 分支 → side = pick_side(client_id, name, p)，
确定性伪随机，同 client 同 skill 在整轮灰度内 side 钉死。无 staging → main。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from xskill.canary import main_sha, pick_side, staging_sha
from xskill.skill.skill import Skill
from xskill.skill.repo import SkillRepo
from xskill.team.shared.protocol import SkillSlot, SyncResponse

_logger = logging.getLogger("xskill.team.manifest")

# §5 SkillRecommendEngine 单例（team server init 时 set_recommend_engine 注入）。
# 为 None 时退回既有 pick_side + RECOMMENDER 画像路径（非 team / 测试场景）。
_engine = None


@dataclass(frozen=True)
class _CatalogSnapshot:
    """一次 skill 仓扫描的不可变结果。"""

    skills: tuple[Skill, ...]
    refs: dict[str, tuple[str, str | None]]
    built_at: float


@dataclass
class _CatalogEntry:
    condition: threading.Condition = field(default_factory=threading.Condition)
    snapshot: _CatalogSnapshot | None = None
    refreshing: bool = False
    generation: int = 0
    last_error: BaseException | None = None
    error_generation: int = 0


class _ManifestCatalogCache:
    """按 skill 根目录隔离的短 TTL、single-flight 仓快照缓存。

    过期刷新失败时不回退到旧快照；异常也不会替换最后一次成功结果。下次
    请求会重新尝试刷新，因此旧结果不会被无限返回。
    """

    def __init__(
        self, *, ttl_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._entries: dict[str, _CatalogEntry] = {}
        self._entries_lock = threading.Lock()

    @staticmethod
    def _key(skill_dir: Path) -> str:
        return str(skill_dir.expanduser().resolve())

    def _entry(self, skill_dir: Path) -> _CatalogEntry:
        key = self._key(skill_dir)
        with self._entries_lock:
            return self._entries.setdefault(key, _CatalogEntry())

    def get(self, skill_dir: Path) -> _CatalogSnapshot:
        entry = self._entry(skill_dir)
        with entry.condition:
            while True:
                now = self.clock()
                if (
                    entry.snapshot is not None
                    and now - entry.snapshot.built_at < self.ttl_seconds
                ):
                    return entry.snapshot
                if not entry.refreshing:
                    entry.refreshing = True
                    attempt_generation = entry.generation + 1
                    break
                waited_generation = entry.generation
                while entry.refreshing and entry.generation == waited_generation:
                    entry.condition.wait()
                if (
                    entry.generation > waited_generation
                    and entry.error_generation == entry.generation
                    and entry.last_error is not None
                ):
                    raise entry.last_error

        try:
            snapshot = self._scan(skill_dir)
        except BaseException as exc:
            with entry.condition:
                entry.generation = attempt_generation
                entry.last_error = exc
                entry.error_generation = attempt_generation
                entry.refreshing = False
                entry.condition.notify_all()
            raise

        with entry.condition:
            entry.snapshot = snapshot
            entry.generation = attempt_generation
            entry.last_error = None
            entry.error_generation = 0
            entry.refreshing = False
            entry.condition.notify_all()
            return snapshot

    def _scan(self, skill_dir: Path) -> _CatalogSnapshot:
        refs: dict[str, tuple[str, str | None]] = {}
        skills: list[Skill] = []
        for skill in SkillRepo(skill_dir):
            current_main = main_sha(skill.path)
            if not current_main:
                continue
            refs[skill.name] = (current_main, staging_sha(skill.path))
            skills.append(skill)
        skills.sort(
            key=lambda skill: _rank_key(skill, main_ref=refs[skill.name][0]),
            reverse=True,
        )
        return _CatalogSnapshot(
            skills=tuple(skills), refs=refs, built_at=self.clock(),
        )

    def clear(self, skill_dir: Path | None = None) -> None:
        with self._entries_lock:
            if skill_dir is None:
                self._entries.clear()
            else:
                self._entries.pop(self._key(skill_dir), None)


_catalog_cache = _ManifestCatalogCache()


def invalidate_manifest_cache(skill_dir: Path | str | None = None) -> None:
    """显式失效 manifest 仓快照；不传路径时清空全部根目录。"""
    _catalog_cache.clear(None if skill_dir is None else Path(skill_dir))


def _reset_manifest_cache_for_tests(
    *, ttl_seconds: float = 1.0,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """测试钩子：替换缓存，以便注入时钟并隔离用例。"""
    global _catalog_cache
    _catalog_cache = _ManifestCatalogCache(ttl_seconds=ttl_seconds, clock=clock)


def set_recommend_engine(eng) -> None:
    """team server 启动时注入 ``SkillRecommendEngine`` 单例。"""
    global _engine
    _engine = eng


def get_recommend_engine():
    """返回已注入的引擎（未注入时 None）。供 api 层在 /sync 刷新用户画像用。"""
    return _engine


def _rank_key(skill: Skill, *, main_ref: str | None = None) -> tuple[float, int]:
    """排序键：(main 侧近 30 天 ux 均分, use_count)，都缺则 (0.0, 0)。"""
    if main_ref is None:
        avg = skill.ux_avg(side="main", days=30)
    else:
        rows = skill.recent_ux_scores(side="main", days=30)
        scores = [
            row.get("score") for row in rows
            if row.get("commit_sha") == main_ref
            and isinstance(row.get("score"), (int, float))
        ]
        avg = sum(scores) / len(scores) if scores else None
    return (avg if avg is not None else 0.0, skill.use_count)


def _resolve_slot(
    skill: Skill | dict, client_id: str, probability: float, bucket: str,
    refs: dict[str, tuple[str, str | None]] | None = None,
) -> SkillSlot | None:
    """对一个 skill 现算它对该 client 的 side + sha。"""
    if isinstance(skill, dict) and skill.get("source") == "skillhub":
        eng = get_recommend_engine()
        if eng is None or eng.skillhub.skill_path(skill["skill_id"]) is None:
            return None
        current_sha = eng.skillhub.content_sha(skill["skill_id"])
        if not current_sha:
            return None
        return SkillSlot(
            skill_name=skill["skill_id"],
            side="main",
            sha=current_sha,
            bucket=bucket,
            source="skillhub",
            display_name=skill.get("display_name"),
            source_path=skill.get("source_path"),
        )
    cached_main, cached_staging = (
        refs[skill.name] if refs is not None else
        (main_sha(skill.path) or "", staging_sha(skill.path))
    )
    if cached_staging:
        if _engine is not None:
            from xskill.recommend.client_user import ClientUser
            side = _engine.resolve_side(
                skill, ClientUser(client_id), refs=(cached_main, cached_staging),
            )
        else:
            side = pick_side(client_id, skill.name, probability)
        sha = cached_staging if side == "staging" else cached_main
    else:
        side = "main"
        sha = cached_main
    if not sha:
        raise RuntimeError(f"skill {skill.name!r}: cannot resolve sha for side={side}")
    return SkillSlot(skill_name=skill.name, side=side, sha=sha, bucket=bucket)


def build_manifest(
    *,
    client_id: str,
    skill_dir: Path | str,
    probability: float,
    ranked_slots: int = 80,
    total_slots: int = 100,
    traj_root: Path | str | None = None,
    prefs: dict | None = None,
    retired: set | None = None,
) -> SyncResponse:
    """为 ``client_id`` 现算 manifest。skill 总数不足 total_slots 时全发。

    只分发**已 graduate 到 main 分支**的 skill。``baby`` 分支上的 stub
    （cluster 建了目录但 SkillEditAgent 还没跑过、没正文）没有 main，本来
    就不该下发给 client——这里直接过滤掉，不是 fallback 而是正确的可分发
    集合判定。

    P2-2.4 注入顺序（含 ``prefs``/``retired`` 时）：**blocked 先排除 →
    pinned 占位 → ranked → recommended 回填**。
    - ``prefs`` = ``registry.effective_prefs`` 的合并视图（调用方现查，
      None = 无控制面语义，纯 ranked+recommended）。
    - ``retired`` = 下线 skill 集合，无条件不分发（即便被 pin）。
    - pinned 超量在**写入侧**拒绝（D8）,这里对"pin 的 skill 已不可分发"
      只跳过不报错——sync 路径绝不 500。

    slot 分段：
    - pinned —— prefs 里的钉住 skill（全局在前），占位不参与排名。
    - ranked —— 按 ux 滑窗均分降序取高分（配额 = min(ranked_slots, 余量)）。
    - recommended —— SP3 画像推荐位：基于该 client 用过的 skill 的
      质心，从「distributable 且不在 pinned/ranked、且 client 没用过」的候选
      里取 cosine 最近邻。``traj_root`` 为 None（非 team server 调用）或该
      client 没有任何带 used_skills 的 atom（冷启动、无画像）时，退回 ux
      排序往下接着取——这不是 fallback，是画像不存在时的正确定义。
    """
    skill_dir = Path(skill_dir)
    if total_slots <= 0:
        # 控制面压测和显式禁用分发的部署不需要触碰 skill 仓；否则短 TTL
        # 过期时仍会让一批无槽位请求等待一次无意义的全量扫描。
        return SyncResponse(slots=[], server_time=time.time())
    prefs = prefs or {"pinned": [], "blocked": set()}
    retired = retired or set()
    excluded = set(prefs.get("blocked") or set()) | retired

    catalog = _catalog_cache.get(skill_dir)
    distributable = [s for s in catalog.skills if s.name not in excluded]
    skills = distributable
    by_name = {s.name: s for s in distributable}

    # pinned 占位:不存在/尚不可分发(无 main)/已下线的 pin 跳过——读路径
    # 绝不 throw(D8),写入侧校验已把守常规超量。
    pinned = [by_name[n] for n in (prefs.get("pinned") or []) if n in by_name]
    pinned = pinned[:total_slots]
    pinned_names = {s.name for s in pinned}

    remaining = total_slots - len(pinned)
    non_pinned = [s for s in skills if s.name not in pinned_names]
    ranked = non_pinned[:min(ranked_slots, remaining)]
    taken_names = pinned_names | {s.name for s in ranked}
    reco_slots = max(remaining - len(ranked), 0)

    # exclude 集合并入 blocked/retired:推荐引擎有自己的候选索引(skillhub 等),
    # 不经 distributable 过滤,必须显式排除
    chosen = pinned + ranked + _pick_recommended(
        client_id=client_id,
        skill_dir=skill_dir,
        ranked=ranked,
        ranked_names=taken_names | excluded,
        ux_ordered=non_pinned,
        reco_slots=reco_slots,
        traj_root=traj_root,
        candidate_pool=list(catalog.skills),
        candidate_refs=catalog.refs,
    )

    slots: list[SkillSlot] = []
    for idx, skill in enumerate(chosen):
        if idx < len(pinned):
            bucket = "pinned"
        elif idx < len(pinned) + len(ranked):
            bucket = "ranked"
        else:
            bucket = "recommended"
        slot = _resolve_slot(
            skill, client_id, probability, bucket, refs=catalog.refs,
        )
        if slot is not None:
            slots.append(slot)
    # 埋点：只记画像推荐位(recommended bucket)——推荐触发率衡量的就是这部分命中。
    # best-effort，记录失败绝不阻断同步。
    try:
        from xskill.pipeline.registry import record_recommendation
        for s in slots:
            if s.bucket == "recommended":
                record_recommendation(client_id=client_id, skill=s.skill_name,
                                      side=s.side or "main", bucket=s.bucket,
                                      sha=s.sha or "")
    except Exception:  # pylint: disable=broad-exception-caught
        _logger.debug("recommendation telemetry skipped", exc_info=True)
    return SyncResponse(slots=slots, server_time=time.time())


def _pick_recommended(
    *,
    client_id: str,
    skill_dir: Path,
    ranked: list[Skill],
    ranked_names: set[str],
    ux_ordered: list[Skill],
    reco_slots: int,
    traj_root: Path | str | None,
    candidate_pool: list[Skill] | None = None,
    candidate_refs: dict[str, tuple[str, str | None]] | None = None,
) -> list[Skill]:
    """选 ``recommended`` bucket 的 skill。

    候选 = distributable 里不在 ranked-80 的（``recommend`` 内部再排除该
    client 已用过的）。有画像 → 按质心 cosine 最近邻；无画像 / 非 team
    server 调用 → 退回 ux 排序往下接着取。
    """
    if reco_slots <= 0:
        return []

    ux_tail = [s for s in ux_ordered if s.name not in ranked_names]
    if traj_root is None:
        return ux_tail[:reco_slots]  # 非 team server：无 traj_root，按 ux 取

    # §5 优先走 SkillRecommendEngine（注入时）；否则退回既有 RECOMMENDER 画像路径。
    if _engine is not None:
        # 索引缺失时（rebuild --force 后到 /reindex 前的窗口）不能进引擎——
        # _combined_relevance 会 raise。退回 ux_tail（与既有 RECOMMENDER 守卫一致）。
        if not (skill_dir / ".skill_index.pkl").is_file() and not _engine._skillhub_entries():
            return ux_tail[:reco_slots]
        user = _engine.load_client_user(client_id)
        if user.client_interest is not None and user.client_interest.feature_tensor is not None:
            picked = _engine.get_skill_for_client(
                user, reco_slots, exclude_names=ranked_names,
                candidate_pool=candidate_pool, candidate_refs=candidate_refs,
            )
            # get_skill_for_client 已记录推荐 + resolve side；只取 reco_slots 个
            return picked[:reco_slots]
        # 冷启动（无画像）→ ux_tail（与既有 RECOMMENDER 冷启动语义一致）
        # team server 的 /sync 必须保持纯缓存路径，不得再落到下方会
        # 扫描 atom store 的旧 RECOMMENDER。
        return ux_tail[:reco_slots]

    # 延迟 import：profile_reco 依赖 numpy + atom store，非 team 路径不付代价。
    from xskill.team.server.profile_reco import RECOMMENDER

    skill_index_path = skill_dir / ".skill_index.pkl"
    if not skill_index_path.is_file():
        # 没建 skill 向量索引 → 算不出质心。退回 ux 排序。
        return ux_tail[:reco_slots]

    candidate_names = [s.name for s in ux_tail]
    reco_names = RECOMMENDER.recommend(
        client_id=client_id,
        traj_root=Path(traj_root),
        skill_index_path=skill_index_path,
        candidate_names=candidate_names,
        limit=reco_slots,
    )
    if reco_names is None:
        return ux_tail[:reco_slots]  # 冷启动：无画像，退回 ux 排序

    by_name = {s.name: s for s in ux_tail}
    picked = [by_name[n] for n in reco_names if n in by_name]
    if len(picked) < reco_slots:
        # 画像推荐出的候选不足 reco_slots（候选池本身就小）→ 用 ux 排序补齐。
        # 不是 error-masking：候选池耗尽是真实情况，补齐保证 slot 数稳定。
        picked_names = {s.name for s in picked}
        for s in ux_tail:
            if len(picked) >= reco_slots:
                break
            if s.name not in picked_names:
                picked.append(s)
    return picked[:reco_slots]
