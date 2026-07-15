"""块 4 热循环缓存:_build/consumed 索引正确性 + O(skills) 读盘证明。

回归目标:watcher 一轮 scan 曾对每个未打 ``clustered`` 标记的 atom 调
``find_atom_entry_in_any_skill``(每次遍历所有 skill 读盘),O(atoms×skills) 烧核握 GIL。
改为每轮一次性 ``build_atom_consumed_index`` + O(1) 命中。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from xskill.pipeline.runner import DirectoryWatcher
from xskill.skill.candidates import (
    CANDIDATES_FILENAME,
    LazyConsumedIndex,
    build_atom_consumed_index,
)


def _write_candidates(skill_dir: Path, name: str, atoms: list[tuple[str, int]]) -> None:
    skill_path = skill_dir / name
    skill_path.mkdir(parents=True)
    (skill_path / CANDIDATES_FILENAME).write_text(
        yaml.safe_dump(
            {"candidates": [{"atom_id": aid, "weightscore": ws} for aid, ws in atoms]}
        ),
        encoding="utf-8",
    )


class _FakeAtom:
    def __init__(self, atom_id: str, clustered: bool):
        self.atom_id = atom_id
        self.clustered = clustered


def test_build_index_maps_atom_to_skill_and_weightscore(tmp_path):
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    _write_candidates(skill_dir, "alpha", [("atom_1", 5), ("atom_2", 3)])
    _write_candidates(skill_dir, "beta", [("atom_3", 8)])
    index = build_atom_consumed_index(skill_dir)
    assert index == {
        "atom_1": ("alpha", 5),
        "atom_2": ("alpha", 3),
        "atom_3": ("beta", 8),
    }


def test_build_index_empty_when_skill_dir_missing(tmp_path):
    assert build_atom_consumed_index(tmp_path / "missing") == {}


def test_build_index_skips_unparseable_file_and_logs(tmp_path, caplog):
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    _write_candidates(skill_dir, "good", [("atom_ok", 1)])
    bad = skill_dir / "bad"
    bad.mkdir()
    (bad / CANDIDATES_FILENAME).write_text("{ not: valid: yaml: :", encoding="utf-8")
    with caplog.at_level("WARNING"):
        index = build_atom_consumed_index(skill_dir)
    assert index == {"atom_ok": ("good", 1)}
    assert any("candidates" in record.getMessage() for record in caplog.records)


def test_atom_consumed_fast_path_does_not_build_index(tmp_path):
    watcher = DirectoryWatcher()
    try:
        index = LazyConsumedIndex(tmp_path / "skills")  # 目录不存在也无所谓,快路径不碰它
        assert watcher._atom_consumed(_FakeAtom("a1", clustered=True), index) is True
        assert index._index is None  # clustered → 惰性索引根本没构建
    finally:
        watcher.stop()


def test_atom_consumed_fallback_hits_lazy_index(tmp_path):
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    _write_candidates(skill_dir, "alpha", [("a2", 0)])
    watcher = DirectoryWatcher()
    try:
        index = LazyConsumedIndex(skill_dir)
        assert watcher._atom_consumed(_FakeAtom("a2", clustered=False), index) is True
        assert watcher._atom_consumed(_FakeAtom("a3", clustered=False), index) is False
    finally:
        watcher.stop()


def test_lazy_index_zero_disk_when_all_clustered_then_builds_once(tmp_path, monkeypatch):
    """稳态全 clustered → 一次盘都不读(1 万 skill 零成本);遇未打标 atom 才建一次并复用。"""
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    for skill_index in range(3):
        _write_candidates(skill_dir, f"skill_{skill_index}", [(f"atom_{skill_index}", 1)])

    reads = {"count": 0}
    real_read_text = Path.read_text

    def counting_read_text(self, *args, **kwargs):
        if self.name == CANDIDATES_FILENAME:
            reads["count"] += 1
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)

    watcher = DirectoryWatcher()
    try:
        index = LazyConsumedIndex(skill_dir)
        for i in range(100):  # 全 clustered:查 100 个 atom
            watcher._atom_consumed(_FakeAtom(f"c{i}", clustered=True), index)
        assert reads["count"] == 0  # 稳态零读盘 —— 惰性索引未构建

        # 一旦有未打标 atom → 建一次索引(每个 .candidates.yml 各读一次)
        watcher._atom_consumed(_FakeAtom("x", clustered=False), index)
        assert reads["count"] == 3
        for i in range(50):  # 再多查也不重读(本轮复用)
            watcher._atom_consumed(_FakeAtom(f"y{i}", clustered=False), index)
        assert reads["count"] == 3
    finally:
        watcher.stop()
