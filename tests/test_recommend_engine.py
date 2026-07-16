"""test_recommend_engine.py — §5 SkillRecommendEngine

TDD: baby 排除、update_user_interest、80/20+回填、staging 优先达量、双向记录、
find_friend、find_tag_*。
"""
from __future__ import annotations

import json
import pickle
import subprocess
from pathlib import Path

import numpy as np

from xskill.canary import append_ux_score, main_sha, staging_sha
from xskill.recommend.client_interest import ClientInterest
from xskill.recommend.client_user import ClientUser
from xskill.recommend.engine import SkillRecommendEngine
from xskill.skill.repo import SkillRepo


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True, check=True)


def _make_main_skill(parent: Path, name: str, desc: str = "d") -> tuple[Path, str]:
    d = parent / name
    d.mkdir(parents=True)
    _git(["init", "-q"], d); _git(["checkout", "-q", "-b", "main"], d)
    _git(["config", "user.email", "t@t"], d); _git(["config", "user.name", "t"], d)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\nmetadata:\n  version: 1\n---\n# {name}\n",
        encoding="utf-8")
    _git(["add", "."], d); _git(["commit", "-q", "-m", "v1"], d)
    return d, main_sha(d)


def _make_baby_skill(parent: Path, name: str) -> Path:
    d = parent / name
    d.mkdir(parents=True)
    _git(["init", "-q"], d); _git(["checkout", "-q", "-b", "baby"], d)
    _git(["config", "user.email", "t@t"], d); _git(["config", "user.name", "t"], d)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: baby\n---\n# {name}\n", encoding="utf-8")
    _git(["add", "."], d); _git(["commit", "-q", "-m", "b"], d)
    return d


def _add_staging(d: Path) -> str:
    _git(["checkout", "-q", "-b", "staging"], d)
    (d / "SKILL.md").write_text(
        (d / "SKILL.md").read_text(encoding="utf-8") + "\nstagings\n", encoding="utf-8")
    _git(["add", "."], d); _git(["commit", "-q", "-m", "stg"], d)
    _git(["checkout", "-q", "main"], d)
    return staging_sha(d)


def _write_index(skill_dir: Path, names: list[str], dim: int):
    embs = np.eye(len(names), dim, dtype=float)  # one-hot
    with open(skill_dir / ".skill_index.pkl", "wb") as f:
        pickle.dump({
            "skill_names": names, "embeddings": embs,
            "atom_feats": np.zeros((len(names), dim)),
            "atom_feat_present": [False] * len(names),
        }, f)


def _write_atom(root: Path, traj_id: str, atom_id: str, *, summary: str,
                used_skills: list[str], tags: list[str] | None = None,
                ux_score: int | None = None):
    tasks = root / traj_id / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    (tasks / f"{atom_id}.json").write_text(json.dumps({
        "atom_id": atom_id, "traj_id": traj_id, "offset_start": 1, "offset_end": 2,
        "intent": "i", "summary": summary, "used_skills": used_skills,
        "tags": tags or [], "ux_score": ux_score,
    }), encoding="utf-8")


class FakeEmbed:
    def __init__(self, dim=5):
        self.dim = dim

    def encode(self, text):
        v = np.zeros(self.dim, dtype=float)
        for i, ch in enumerate(text):
            v[i % self.dim] += ord(ch) % 97
        return v

    def encode_batch(self, texts):
        return np.stack([self.encode(t) for t in texts])


def _engine(tmp_path, skill_dir, traj_root, *, total_samples=3):
    return SkillRecommendEngine(
        config={"recommend": {"quality_ratio": 0.8, "staging_need": total_samples},
                "canary": {"total_samples": total_samples}},
        skill_dir=skill_dir, traj_root=traj_root,
        embed_client=FakeEmbed(dim=5), profile_db=tmp_path / "profile.db",
    )


# ── baby 排除 ────────────────────────────────────────────────────

class TestPool:
    def test_baby_excluded(self, tmp_path):
        skill_dir = tmp_path / "skills"
        _make_main_skill(skill_dir, "s0")
        _make_baby_skill(skill_dir, "baby1")
        _write_index(skill_dir, ["s0", "baby1"], dim=5)
        eng = _engine(tmp_path, skill_dir, tmp_path / "traj")
        pool = eng._distributable_skills()
        assert {s.name for s in pool} == {"s0"}

    def test_supplied_manifest_pool_avoids_second_repo_scan(self, tmp_path, monkeypatch):
        """暖画像推荐复用 manifest 候选池和 refs，不再次遍历仓或查询 git。"""
        skill_dir = tmp_path / "skills"
        refs = {}
        for name in ("s0", "s1", "s2"):
            path, sha = _make_main_skill(skill_dir, name)
            refs[name] = (sha, None)
        _write_index(skill_dir, ["s0", "s1", "s2"], dim=5)
        eng = _engine(tmp_path, skill_dir, tmp_path / "traj")
        pool = list(SkillRepo(skill_dir))
        user = ClientUser(
            "u1", client_interest=ClientInterest(
                "u1", feature_tensor=np.asarray([[1, 0, 0, 0, 0]], dtype=float),
                mean_tensor=np.asarray([1, 0, 0, 0, 0], dtype=float),
            ),
        )

        def unexpected_scan():
            raise AssertionError("manifest 候选池已提供，不应再次扫描 SkillRepo")

        monkeypatch.setattr(eng, "_distributable_skills", unexpected_scan)
        import xskill.skill.skill as skill_module
        monkeypatch.setattr(
            skill_module._canary, "main_sha",
            lambda _path: (_ for _ in ()).throw(
                AssertionError("manifest refs 已提供，不应再次查询 Git")
            ),
        )
        chosen = eng.get_skill_for_client(
            user, 2, candidate_pool=pool, candidate_refs=refs,
        )

        assert len(chosen) == 2

    def test_combined_relevance_aligns_and_dedups(self, tmp_path, monkeypatch):
        """_combined_relevance:矩阵行与 names 严格对齐、同名 skill 保留自有向量。

        锁死修 O(n²) 逐行 vstack(engine.py:137)后的语义:上万 skillhub skill 时
        逐行 np.vstack 会把 /sync 的 32 个 worker 焊死在核上;改成末尾单次 vstack
        后结果必须与旧语义逐位等价。
        """
        skill_dir = tmp_path / "skills"
        for name in ("s0", "s1"):
            _make_main_skill(skill_dir, name)
        _write_index(skill_dir, ["s0", "s1"], dim=5)
        eng = _engine(tmp_path, skill_dir, tmp_path / "traj")

        # 在真实接缝(skillhub.fingerprint / index)打桩,让真正的 _skillhub_entries
        # 管理 self._skillhub_cache(fingerprint 返回稳定对象→缓存命中判定生效)。
        hub_fp = ("hub-fingerprint",)
        hub_entries = [
            {"name": "hubA", "vec": np.asarray([0, 0, 1, 0, 0], dtype=float)},
            {"name": "s0", "vec": np.asarray([9, 9, 9, 9, 9], dtype=float)},
            {"name": "hubB", "vec": np.asarray([0, 0, 0, 1, 0], dtype=float)},
        ]

        def fake_fingerprint():
            return hub_fp

        def fake_index():
            return hub_entries

        monkeypatch.setattr(eng.skillhub, "fingerprint", fake_fingerprint)
        monkeypatch.setattr(eng.skillhub, "index", fake_index)
        names, embs, is_hub = eng._combined_relevance()

        assert names == ["s0", "s1", "hubA", "hubB"]
        assert embs.shape == (4, 5)
        assert np.allclose(embs[0], [1, 0, 0, 0, 0])  # s0 保留自有 one-hot,不用 hub 的 [9..]
        assert np.allclose(embs[1], [0, 1, 0, 0, 0])
        assert np.allclose(embs[2], [0, 0, 1, 0, 0])  # hubA
        assert np.allclose(embs[3], [0, 0, 0, 1, 0])  # hubB
        assert is_hub == {"s0": False, "s1": False, "hubA": True, "hubB": True}

    def test_combined_relevance_hub_only_when_no_repo_index(self, tmp_path, monkeypatch):
        """无 .skill_index.pkl(rebuild 窗口):池 = 纯三方 skill,单次 vstack 直接成矩阵。"""
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        eng = _engine(tmp_path, skill_dir, tmp_path / "traj")

        hub_fp = ("fp2",)
        hub_entries = [
            {"name": "hubA", "vec": np.asarray([1, 0], dtype=float)},
            {"name": "hubB", "vec": np.asarray([0, 1], dtype=float)},
        ]

        def fake_fingerprint():
            return hub_fp

        def fake_index():
            return hub_entries

        monkeypatch.setattr(eng.skillhub, "fingerprint", fake_fingerprint)
        monkeypatch.setattr(eng.skillhub, "index", fake_index)
        names, embs, is_hub = eng._combined_relevance()

        assert names == ["hubA", "hubB"]
        assert embs.shape == (2, 2)
        assert np.allclose(embs, [[1, 0], [0, 1]])
        assert is_hub == {"hubA": True, "hubB": True}

    def test_combined_relevance_is_cached_and_invalidated(self, tmp_path, monkeypatch):
        """候选池客户端无关:重复调用命中缓存返回同一对象(不重拼万行矩阵);
        invalidate_cache 后重建。这是消除 /sync 每请求每 worker 重算的核心。"""
        skill_dir = tmp_path / "skills"
        for name in ("s0", "s1"):
            _make_main_skill(skill_dir, name)
        _write_index(skill_dir, ["s0", "s1"], dim=5)
        eng = _engine(tmp_path, skill_dir, tmp_path / "traj")

        hub_fp = ("fp3",)
        hub_entries = [{"name": "hubA", "vec": np.asarray([0, 0, 1, 0, 0], dtype=float)}]

        def fake_fingerprint():
            return hub_fp

        def fake_index():
            return hub_entries

        monkeypatch.setattr(eng.skillhub, "fingerprint", fake_fingerprint)
        monkeypatch.setattr(eng.skillhub, "index", fake_index)
        first = eng._combined_relevance()
        second = eng._combined_relevance()
        assert first is second  # 命中缓存:同一 (names, embs, is_hub) 对象

        eng.invalidate_cache()
        third = eng._combined_relevance()
        assert third is not first  # 失效后重建
        assert third[0] == first[0] == ["s0", "s1", "hubA"]

    def test_combined_relevance_key_and_data_same_generation(self, tmp_path, monkeypatch):
        """并发 race 回归:hub_entries(建矩阵的数据)与缓存键必须同代。模拟另一线程
        在本次调用期间把 self._skillhub_cache 推进到新代——结果与所存键必须都取新代,
        不能出现"用旧代数据建的结果存到新代键上"(会让新上传的 skill 长期不可见)。"""
        skill_dir = tmp_path / "skills"
        _make_main_skill(skill_dir, "s0")
        _write_index(skill_dir, ["s0"], dim=5)
        eng = _engine(tmp_path, skill_dir, tmp_path / "traj")

        gen_old = (("fpOLD",),
                   [{"name": "hubOLD", "vec": np.asarray([0, 0, 1, 0, 0], dtype=float)}])
        gen_new = (("fpNEW",),
                   [{"name": "hubNEW", "vec": np.asarray([0, 0, 0, 1, 0], dtype=float)}])

        def racing_entries():
            # 模拟并发另一线程已把缓存推进到新代;但返回旧代数据(旧实现会误用它)。
            eng._skillhub_cache = gen_new
            return gen_old[1]

        monkeypatch.setattr(eng, "_skillhub_entries", racing_entries)
        names, _embs, _is_hub = eng._combined_relevance()

        assert eng._combined_pool_cache[0] is gen_new  # 键为新代
        assert "hubNEW" in names and "hubOLD" not in names  # 数据也必须是新代


# ── update_user_interest ─────────────────────────────────────────

class TestUpdateUserInterest:
    def test_atom_updates_profile(self, tmp_path):
        skill_dir = tmp_path / "skills"
        _make_main_skill(skill_dir, "s0")
        _write_index(skill_dir, ["s0"], dim=5)
        traj_root = tmp_path / "traj"
        sessions = traj_root / "clients" / "u1" / "sessions"
        _write_atom(sessions, "traj_1", "atom_traj_1_0001",
                    summary="fix django migration", used_skills=["s0"], ux_score=8)
        eng = _engine(tmp_path, skill_dir, traj_root)
        ci = ClientInterest("u1")
        eng.update_user_interest(ci, task_atom=None)
        row = eng.profile_store.load("u1")
        assert row is not None
        assert row["feature_tensor"] is not None  # 有 atom → 有画像
        assert row["used_skills"][0]["name"] == "s0"
        assert row["used_skills"][0]["avg_score"] == 8.0

    def test_no_atoms_cold_start(self, tmp_path):
        skill_dir = tmp_path / "skills"
        _make_main_skill(skill_dir, "s0")
        _write_index(skill_dir, ["s0"], dim=5)
        eng = _engine(tmp_path, skill_dir, tmp_path / "traj")
        ci = ClientInterest("u1")
        eng.update_user_interest(ci)
        row = eng.profile_store.load("u1")
        assert row["feature_tensor"] is None
        # P3-3.4:冷启动无点,散点读侧显式拿到 points=None
        assert eng.profile_store.load_points("u1")["points"] is None

    def test_points_persisted_with_profile(self, tmp_path):
        """P3-3.4(Q4):更新画像顺手落盘原子点 + 对齐的元数据(散点数据源)。"""
        skill_dir = tmp_path / "skills"
        _make_main_skill(skill_dir, "s0")
        _write_index(skill_dir, ["s0"], dim=5)
        traj_root = tmp_path / "traj"
        sessions = traj_root / "clients" / "u1" / "sessions"
        _write_atom(sessions, "traj_1", "atom_traj_1_0001",
                    summary="fix django migration", used_skills=["s0"],
                    ux_score=8)
        _write_atom(sessions, "traj_1", "atom_traj_1_0002",
                    summary="tune nginx cache", used_skills=[], ux_score=6)
        eng = _engine(tmp_path, skill_dir, traj_root)
        eng.update_user_interest(ClientInterest("u1"))
        got = eng.profile_store.load_points("u1")
        assert got["points"].shape[0] == 2
        assert [m["atom_id"] for m in got["meta"]] == \
            ["atom_traj_1_0001", "atom_traj_1_0002"]
        assert got["meta"][0]["ux"] == 8
        assert got["points"].shape[0] == len(got["meta"])  # 行对齐不变量

    def test_incremental_embedding_only_new_atoms(self, tmp_path):
        """增量:新增原子只 embed 新的那条,老原子按 atom_id 复用落盘向量。"""
        skill_dir = tmp_path / "skills"
        _make_main_skill(skill_dir, "s0")
        _write_index(skill_dir, ["s0"], dim=5)
        traj_root = tmp_path / "traj"
        sessions = traj_root / "clients" / "u1" / "sessions"
        _write_atom(sessions, "traj_1", "atom_traj_1_0001",
                    summary="fix django migration", used_skills=[], ux_score=8)
        eng = _engine(tmp_path, skill_dir, traj_root)

        embedded: list[list[str]] = []
        real_batch = eng.embed_client.encode_batch
        eng.embed_client.encode_batch = lambda texts: (embedded.append(list(texts))
                                                        or real_batch(texts))
        eng.update_user_interest(ClientInterest("u1"))
        assert embedded == [["fix django migration"]]  # 首次:全部

        # 新增一条原子 → 只 embed 新的
        _write_atom(sessions, "traj_1", "atom_traj_1_0002",
                    summary="tune nginx cache", used_skills=[], ux_score=6)
        embedded.clear()
        eng.update_user_interest(ClientInterest("u1"))
        assert embedded == [["tune nginx cache"]]  # 只算新原子,老的复用
        got = eng.profile_store.load_points("u1")
        assert [m["atom_id"] for m in got["meta"]] == \
            ["atom_traj_1_0001", "atom_traj_1_0002"]

    def test_model_change_forces_full_reembed(self, tmp_path):
        """换 embedding 模型 → 缓存向量作废,整体重算(护栏)。"""
        skill_dir = tmp_path / "skills"
        _make_main_skill(skill_dir, "s0")
        _write_index(skill_dir, ["s0"], dim=5)
        traj_root = tmp_path / "traj"
        sessions = traj_root / "clients" / "u1" / "sessions"
        _write_atom(sessions, "traj_1", "atom_traj_1_0001",
                    summary="a", used_skills=[], ux_score=8)
        _write_atom(sessions, "traj_1", "atom_traj_1_0002",
                    summary="b", used_skills=[], ux_score=6)
        eng = _engine(tmp_path, skill_dir, traj_root)
        eng.embed_client.model = "m1"
        eng.update_user_interest(ClientInterest("u1"))

        embedded: list[list[str]] = []
        real_batch = eng.embed_client.encode_batch
        eng.embed_client.encode_batch = lambda texts: (embedded.append(list(texts))
                                                        or real_batch(texts))
        # 同模型再跑(强制指纹失效:清 in-memory 缓存)→ 全复用,不 embed
        eng._profile_fp_cache.clear()
        eng.update_user_interest(ClientInterest("u1"))
        assert embedded == []  # 模型一致 + 无新原子 → 零 embedding
        # 换模型 → 全量重算
        eng._profile_fp_cache.clear()
        eng.embed_client.model = "m2"
        embedded.clear()
        eng.update_user_interest(ClientInterest("u1"))
        assert embedded == [["a", "b"]]

    def test_persisted_revision_survives_engine_restart(self, tmp_path):
        skill_dir = tmp_path / "skills"
        _make_main_skill(skill_dir, "s0")
        _write_index(skill_dir, ["s0"], dim=5)
        traj_root = tmp_path / "traj"
        sessions = traj_root / "clients" / "u1" / "sessions"
        _write_atom(sessions, "traj_1", "atom_1", summary="same",
                    used_skills=[], ux_score=5)
        first = _engine(tmp_path, skill_dir, traj_root)
        first.update_user_interest(ClientInterest("u1"))

        second = _engine(tmp_path, skill_dir, traj_root)
        embedded = []
        second.embed_client.encode_batch = lambda texts: embedded.append(texts)
        result = second.update_user_interest(ClientInterest("u1"))
        assert result.changed is False
        assert result.embed_items == 0
        assert embedded == []

    def test_delete_and_metadata_change_do_not_reembed(self, tmp_path):
        skill_dir = tmp_path / "skills"
        _make_main_skill(skill_dir, "s0")
        _write_index(skill_dir, ["s0"], dim=5)
        traj_root = tmp_path / "traj"
        sessions = traj_root / "clients" / "u1" / "sessions"
        _write_atom(sessions, "traj_1", "atom_1", summary="one",
                    used_skills=[], ux_score=5, tags=["old"])
        _write_atom(sessions, "traj_1", "atom_2", summary="two",
                    used_skills=[], ux_score=6)
        eng = _engine(tmp_path, skill_dir, traj_root)
        eng.update_user_interest(ClientInterest("u1"))

        embedded = []
        real_batch = eng.embed_client.encode_batch
        eng.embed_client.encode_batch = lambda texts: (
            embedded.append(list(texts)) or real_batch(texts)
        )
        _write_atom(sessions, "traj_1", "atom_1", summary="one",
                    used_skills=["s0"], ux_score=9, tags=["new"])
        metadata_result = eng.update_user_interest(ClientInterest("u1"))
        assert metadata_result.changed is True
        assert metadata_result.embed_items == 0
        assert embedded == []
        assert eng.profile_store.load("u1")["used_skills"][0]["name"] == "s0"

        (sessions / "traj_1" / "tasks" / "atom_2.json").unlink()
        delete_result = eng.update_user_interest(ClientInterest("u1"))
        assert delete_result.changed is True
        assert delete_result.embed_items == 0
        assert embedded == []
        assert len(eng.profile_store.load_points("u1")["meta"]) == 1

    def test_in_place_summary_change_reembeds_one(self, tmp_path):
        skill_dir = tmp_path / "skills"
        _make_main_skill(skill_dir, "s0")
        _write_index(skill_dir, ["s0"], dim=5)
        traj_root = tmp_path / "traj"
        sessions = traj_root / "clients" / "u1" / "sessions"
        _write_atom(sessions, "traj_1", "atom_1", summary="before",
                    used_skills=[], ux_score=5)
        _write_atom(sessions, "traj_1", "atom_2", summary="stable",
                    used_skills=[], ux_score=6)
        eng = _engine(tmp_path, skill_dir, traj_root)
        eng.update_user_interest(ClientInterest("u1"))

        embedded = []
        real_batch = eng.embed_client.encode_batch
        eng.embed_client.encode_batch = lambda texts: (
            embedded.append(list(texts)) or real_batch(texts)
        )
        _write_atom(sessions, "traj_1", "atom_1", summary="after",
                    used_skills=[], ux_score=5)
        result = eng.update_user_interest(ClientInterest("u1"))
        assert result.embed_items == 1
        assert embedded == [["after"]]

    def test_cancel_after_embedding_does_not_write_profile(self, tmp_path):
        skill_dir = tmp_path / "skills"
        _make_main_skill(skill_dir, "s0")
        _write_index(skill_dir, ["s0"], dim=5)
        traj_root = tmp_path / "traj"
        sessions = traj_root / "clients" / "u1" / "sessions"
        _write_atom(sessions, "traj_1", "atom_1", summary="slow embed",
                    used_skills=[], ux_score=5)
        eng = _engine(tmp_path, skill_dir, traj_root)
        checks = iter([True, False])

        result = eng.update_user_interest(
            ClientInterest("u1"), should_commit=lambda: next(checks),
        )

        assert result.cancelled is True
        assert result.embed_items == 1
        assert eng.profile_store.load("u1") is None


# ── get_skill_for_client 80/20 + 回填 ─────────────────────────────

class TestGetSkill:
    def _setup5(self, tmp_path):
        skill_dir = tmp_path / "skills"
        shas = {}
        for i in range(5):
            _, sh = _make_main_skill(skill_dir, f"s{i}", desc=f"desc {i}")
            shas[f"s{i}"] = sh
        _write_index(skill_dir, [f"s{i}" for i in range(5)], dim=5)
        return skill_dir, shas

    def test_standard_80_20(self, tmp_path):
        skill_dir, shas = self._setup5(tmp_path)
        # s0..s3 有 ux 分；s4 无
        for i in range(4):
            append_ux_score(skill_dir / f"s{i}", traj_id="t", skill_name=f"s{i}",
                            side="main", commit_sha=shas[f"s{i}"], score=5 + i, reasons="r")
        traj_root = tmp_path / "traj"
        eng = _engine(tmp_path, skill_dir, traj_root)
        # 用户 feature_tensor = [one-hot(s4)] → 相关性应选 s4
        ci = ClientInterest("u1")
        ci._feature_tensor = np.array([[0.0, 0.0, 0.0, 0.0, 1.0]])  # s4 one-hot
        ci._mean_tensor = np.array([0.0, 0.0, 0.0, 0.0, 1.0])
        user = ClientUser("u1", client_interest=ci)
        # 预置画像让 find 不报错
        eng.profile_store.upsert("u1", feature_tensor=ci._feature_tensor,
                                 mean_tensor=ci._mean_tensor, used_skills=[])
        skills = eng.get_skill_for_client(user, skill_num=5)
        names = [s.name for s in skills]
        # 质量 4 个（s0..s3）+ 相关性 1 个（s4）
        assert set(names) == {"s0", "s1", "s2", "s3", "s4"}

    def test_backfill_when_quality_small(self, tmp_path):
        skill_dir, shas = self._setup5(tmp_path)
        # 只有 s0 有 ux 分
        append_ux_score(skill_dir / "s0", traj_id="t", skill_name="s0",
                        side="main", commit_sha=shas["s0"], score=9, reasons="r")
        eng = _engine(tmp_path, skill_dir, tmp_path / "traj")
        user = ClientUser("u1")  # 冷启动无画像
        skills = eng.get_skill_for_client(user, skill_num=5)
        assert len(skills) == 5  # 质量 1 + 回填 4
        assert "s0" in [s.name for s in skills]


# ── resolve_side staging 优先达量 ─────────────────────────────────

class TestResolveSide:
    def _skill_with_staging(self, tmp_path, name="sx"):
        skill_dir = tmp_path / "skills"
        d, msh = _make_main_skill(skill_dir, name)
        ssh = _add_staging(d)
        _write_index(skill_dir, [name], dim=5)
        return skill_dir, d, msh, ssh

    def test_staging_under_quota_pushes_staging(self, tmp_path):
        skill_dir, d, msh, ssh = self._skill_with_staging(tmp_path)
        eng = _engine(tmp_path, skill_dir, tmp_path / "traj", total_samples=3)
        # 无任何 ux 分 → staging 未达量 → staging
        user = ClientUser("u1")
        s = eng._distributable_skills()[0]
        assert eng.resolve_side(s, user) == "staging"

    def test_staging_full_main_under_pushes_main(self, tmp_path):
        skill_dir, d, msh, ssh = self._skill_with_staging(tmp_path)
        # staging 3 分（达量），main 0 分
        for i in range(3):
            append_ux_score(d, traj_id=f"t{i}", skill_name="sx", side="staging",
                            commit_sha=ssh, score=7, reasons="r")
        eng = _engine(tmp_path, skill_dir, tmp_path / "traj", total_samples=3)
        user = ClientUser("u1")
        s = eng._distributable_skills()[0]
        assert eng.resolve_side(s, user) == "main"

    def test_both_full_defers_to_router(self, tmp_path):
        skill_dir, d, msh, ssh = self._skill_with_staging(tmp_path)
        for i in range(3):
            append_ux_score(d, traj_id=f"tm{i}", skill_name="sx", side="main",
                            commit_sha=msh, score=7, reasons="r")
            append_ux_score(d, traj_id=f"ts{i}", skill_name="sx", side="staging",
                            commit_sha=ssh, score=7, reasons="r")
        eng = _engine(tmp_path, skill_dir, tmp_path / "traj", total_samples=3)
        user = ClientUser("u1")
        s = eng._distributable_skills()[0]
        side = eng.resolve_side(s, user)
        assert side in ("main", "staging")  # router 决定，但确定性钉死


# ── 双向记录 ─────────────────────────────────────────────────────

class TestBidirectionalRecord:
    def test_recorded_both_ways(self, tmp_path):
        skill_dir = tmp_path / "skills"
        _, sh = _make_main_skill(skill_dir, "s0")
        _write_index(skill_dir, ["s0"], dim=5)
        eng = _engine(tmp_path, skill_dir, tmp_path / "traj")
        user = ClientUser("u1")
        eng.get_skill_for_client(user, skill_num=1)
        assert len(user.recommended_skills) == 1
        rec = user.recommended_skills[0]
        assert rec["skill"] == "s0"
        # 反查：s0 main 被推给了 u1
        assert "u1" in eng.reco_store.users_for_skill("s0", rec["branch"])


# ── find_friend / find_tag ───────────────────────────────────────

class TestFindFriendAndTag:
    def test_find_friend_returns_similar(self, tmp_path):
        skill_dir = tmp_path / "skills"
        _make_main_skill(skill_dir, "s0")
        _write_index(skill_dir, ["s0"], dim=5)
        eng = _engine(tmp_path, skill_dir, tmp_path / "traj")
        # 两个用户画像，u1 与 u2 mean 相同、与 u3 不同
        m = np.array([1.0, 0.0, 0.0, 0.0, 0.0])
        eng.profile_store.upsert("u2", feature_tensor=np.array([[1.0, 0, 0, 0, 0]]),
                                 mean_tensor=m, used_skills=[])
        eng.profile_store.upsert("u3", feature_tensor=np.array([[0.0, 1.0, 0, 0, 0]]),
                                 mean_tensor=np.array([0.0, 1.0, 0, 0, 0]), used_skills=[])
        ci = ClientInterest("u1")
        ci._feature_tensor = np.array([[1.0, 0, 0, 0, 0]])
        ci._mean_tensor = m
        user = ClientUser("u1", client_interest=ci)
        friends = eng.find_friend(user)
        assert "u2" in friends
        assert friends[0] == "u2"  # 最相似

    def test_find_tag_for_skill(self, tmp_path):
        skill_dir = tmp_path / "skills"
        _make_main_skill(skill_dir, "s0")
        _write_index(skill_dir, ["s0"], dim=5)
        traj_root = tmp_path / "traj"
        sessions = traj_root / "clients" / "u1" / "sessions"
        _write_atom(sessions, "traj_1", "atom_1", summary="x",
                    used_skills=["s0"], tags=["django", "migration"])
        eng = _engine(tmp_path, skill_dir, traj_root)
        s = eng._distributable_skills()[0]
        tags = eng.find_tag_for_skill(s)
        assert set(tags) >= {"django", "migration"}

    def test_find_tag_for_user(self, tmp_path):
        skill_dir = tmp_path / "skills"
        _make_main_skill(skill_dir, "s0")
        _write_index(skill_dir, ["s0"], dim=5)
        traj_root = tmp_path / "traj"
        sessions = traj_root / "clients" / "u1" / "sessions"
        _write_atom(sessions, "traj_1", "atom_1", summary="django migration",
                    used_skills=["s0"], tags=["django", "migration"])
        eng = _engine(tmp_path, skill_dir, traj_root)
        ci = ClientInterest("u1")
        eng.update_user_interest(ci)  # 建画像
        user = ClientUser("u1", client_interest=ci)
        tags = eng.find_tag_for_user(user)
        assert isinstance(tags, list)
