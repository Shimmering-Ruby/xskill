"""Candidates v2：以 atom_id 为单位累计 weightscore，阈值 ≥ 10 即 ready"""
from __future__ import annotations



from xskill.skill import candidates as C


class TestAddAtomContribution:
    def test_first_add_creates_entry(self):
        data = {"candidates": []}
        data, was_new = C.add_atom_contribution(data, "atom_x_0001", 7)
        assert was_new is True
        assert data["candidates"][0]["atom_id"] == "atom_x_0001"
        assert data["candidates"][0]["weightscore"] == 7
        # v2.1: 不再有 weightscore_total / contributions / promoted 字段
        assert "weightscore_total" not in data["candidates"][0]
        assert "contributions" not in data["candidates"][0]
        assert "promoted" not in data["candidates"][0]

    def test_repeated_add_overwrites(self):
        """v2.1: 同 atom + 同 skill 重复 add → 覆盖 weightscore，不累加。"""
        data = {"candidates": []}
        data, _ = C.add_atom_contribution(data, "atom_x_0001", 4)
        data, was_new = C.add_atom_contribution(data, "atom_x_0001", 8, note="改主意")
        assert was_new is False
        assert len(data["candidates"]) == 1   # 仍是单条
        assert data["candidates"][0]["weightscore"] == 8  # 覆盖为新值
        assert data["candidates"][0]["note"] == "改主意"

    def test_distinct_atoms_create_separate_entries(self):
        data = {"candidates": []}
        data, _ = C.add_atom_contribution(data, "atom_a", 3)
        data, _ = C.add_atom_contribution(data, "atom_b", 4)
        ids = {c["atom_id"] for c in data["candidates"]}
        assert ids == {"atom_a", "atom_b"}


class TestPromotionV2:
    def test_single_atom_at_threshold_is_ready(self):
        data = {"candidates": []}
        data, _ = C.add_atom_contribution(data, "atom_x", 10)
        ready = C.ready_for_promotion_v2(data, threshold=10)
        assert len(ready) == 1

    def test_multi_atom_summing_to_threshold_is_ready(self):
        data = {"candidates": []}
        for aid, s in [("a1", 4), ("a2", 3), ("a3", 4)]:
            data, _ = C.add_atom_contribution(data, aid, s)
        ready = C.ready_for_promotion_v2(data, threshold=10)
        assert len(ready) == 3
        assert sum(c["weightscore"] for c in ready) >= 10

    def test_below_threshold_returns_empty(self):
        data = {"candidates": []}
        data, _ = C.add_atom_contribution(data, "a1", 5)
        data, _ = C.add_atom_contribution(data, "a2", 4)
        assert C.ready_for_promotion_v2(data, threshold=10) == []

    def test_default_threshold_is_ten(self):
        data = {"candidates": []}
        data, _ = C.add_atom_contribution(data, "x", 10)
        assert len(C.ready_for_promotion_v2(data)) == 1
        assert C.ATOM_PROMOTION_THRESHOLD == 10


class TestClearCandidates:
    def test_clear_writes_empty_list(self, tmp_path):
        """SkillEditAgent 成功落盘后整批清空 buffer。"""
        data = {"candidates": []}
        for aid, s in [("a", 5), ("b", 5)]:
            data, _ = C.add_atom_contribution(data, aid, s)
        C.save_candidates(tmp_path, data)
        # 清空
        C.clear_candidates(tmp_path)
        reloaded = C.load_candidates(tmp_path)
        assert reloaded["candidates"] == []


class TestPersistence:
    def test_save_then_load_roundtrip(self, tmp_path):
        skill_dir = tmp_path / "skill_x"
        skill_dir.mkdir()
        data = {"candidates": []}
        data, _ = C.add_atom_contribution(data, "atom_a", 5)
        C.save_candidates(skill_dir, data)
        loaded = C.load_candidates(skill_dir)
        assert loaded["candidates"][0]["atom_id"] == "atom_a"
        assert loaded["candidates"][0]["weightscore"] == 5
