"""manifest skill 仓快照缓存的并发与新鲜度回归。"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest

import xskill.team.server.skill_manifest as manifest


@dataclass
class _FakeSkill:
    path: Path
    score: float = 0.0
    uses: int = 0
    current_main: str | None = None

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def use_count(self) -> int:
        return self.uses

    def ux_avg(self, *, side: str, days: int) -> float:
        assert side == "main"
        assert days == 30
        return self.score

    def recent_ux_scores(self, *, side: str, days: int) -> list[dict]:
        assert side == "main"
        assert days == 30
        return ([{"commit_sha": self.current_main, "score": self.score}]
                if self.current_main is not None else [])


@pytest.fixture(autouse=True)
def _isolated_cache():
    manifest._reset_manifest_cache_for_tests()
    yield
    manifest._reset_manifest_cache_for_tests()


def _install_fake_repo(monkeypatch, roots: dict[Path, list[_FakeSkill]], refs: dict):
    for skills in roots.values():
        for skill in skills:
            skill.current_main = refs[skill.path][0]
    monkeypatch.setattr(
        manifest, "SkillRepo", lambda root: roots[Path(root)],
    )
    monkeypatch.setattr(manifest, "main_sha", lambda path: refs[Path(path)][0])
    monkeypatch.setattr(manifest, "staging_sha", lambda path: refs[Path(path)][1])


def test_300_concurrent_manifests_scan_100_skill_repo_once(tmp_path, monkeypatch):
    """300 个同步请求共用一次全量扫描，而不是做 300×100 次 git 查询。"""
    root = tmp_path / "skills"
    root.mkdir()
    skills = [_FakeSkill(root / f"skill-{i:03d}", score=float(i)) for i in range(100)]
    refs = {s.path: (f"main-{i}", None) for i, s in enumerate(skills)}
    for skill in skills:
        skill.current_main = refs[skill.path][0]
    monkeypatch.setattr(manifest, "SkillRepo", lambda _root: skills)

    calls = {"main": 0, "staging": 0}
    calls_lock = threading.Lock()
    scan_started = threading.Event()
    release_scan = threading.Event()

    def counted_main(path):
        with calls_lock:
            calls["main"] += 1
            current = calls["main"]
        if current == 1:
            scan_started.set()
            assert release_scan.wait(timeout=5)
        return refs[Path(path)][0]

    def counted_staging(path):
        with calls_lock:
            calls["staging"] += 1
        return refs[Path(path)][1]

    monkeypatch.setattr(manifest, "main_sha", counted_main)
    monkeypatch.setattr(manifest, "staging_sha", counted_staging)

    def request(i):
        return manifest.build_manifest(
            client_id=f"client-{i:03d}", skill_dir=root, probability=0.2,
            ranked_slots=100, total_slots=100,
        )

    with ThreadPoolExecutor(max_workers=300) as pool:
        futures = [pool.submit(request, i) for i in range(300)]
        assert scan_started.wait(timeout=5)
        release_scan.set()
        responses = [f.result(timeout=15) for f in futures]

    assert calls == {"main": 100, "staging": 100}
    assert all(len(response.slots) == 100 for response in responses)
    assert all(slot.side == "main" for slot in responses[0].slots)


def test_catalog_ranking_reuses_ref_read_by_scan(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    skills = [
        _FakeSkill(root / "low", score=1.0, current_main="main-low"),
        _FakeSkill(root / "high", score=9.0, current_main="main-high"),
    ]
    refs = {skill.path: (skill.current_main, None) for skill in skills}
    calls = {"main": 0, "staging": 0}
    monkeypatch.setattr(manifest, "SkillRepo", lambda _root: skills)

    def counted_main(path):
        calls["main"] += 1
        return refs[Path(path)][0]

    def counted_staging(path):
        calls["staging"] += 1
        return refs[Path(path)][1]

    monkeypatch.setattr(manifest, "main_sha", counted_main)
    monkeypatch.setattr(manifest, "staging_sha", counted_staging)
    response = manifest.build_manifest(
        client_id="client", skill_dir=root, probability=0.0,
        ranked_slots=2, total_slots=2,
    )

    assert [slot.skill_name for slot in response.slots] == ["high", "low"]
    assert calls == {"main": 2, "staging": 2}


def test_zero_slots_never_scans_repo(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()

    def unexpected_repo(_root):
        raise AssertionError("total_slots=0 不应扫描 skill 仓")

    monkeypatch.setattr(manifest, "SkillRepo", unexpected_repo)
    response = manifest.build_manifest(
        client_id="client", skill_dir=root, probability=0.2,
        ranked_slots=0, total_slots=0,
    )

    assert response.slots == []


def test_cache_ttl_makes_new_main_and_staging_refs_visible(tmp_path, monkeypatch):
    now = [0.0]
    manifest._reset_manifest_cache_for_tests(
        ttl_seconds=1.0, clock=lambda: now[0],
    )
    root = tmp_path / "skills"
    root.mkdir()
    skill = _FakeSkill(root / "alpha")
    current = {skill.path: ("main-v1", None)}
    _install_fake_repo(monkeypatch, {root: [skill]}, current)

    first = manifest.build_manifest(
        client_id="client", skill_dir=root, probability=1.0,
    ).slots[0]
    current[skill.path] = ("main-v2", "staging-v2")

    now[0] = 0.5
    still_cached = manifest.build_manifest(
        client_id="client", skill_dir=root, probability=1.0,
    ).slots[0]
    now[0] = 1.0
    refreshed = manifest.build_manifest(
        client_id="client", skill_dir=root, probability=1.0,
    ).slots[0]

    assert (first.side, first.sha) == ("main", "main-v1")
    assert (still_cached.side, still_cached.sha) == ("main", "main-v1")
    assert (refreshed.side, refreshed.sha) == ("staging", "staging-v2")


def test_cache_isolated_by_skill_root(tmp_path, monkeypatch):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    skill_a = _FakeSkill(root_a / "same-name")
    skill_b = _FakeSkill(root_b / "same-name")
    refs = {
        skill_a.path: ("sha-from-a", None),
        skill_b.path: ("sha-from-b", None),
    }
    _install_fake_repo(
        monkeypatch, {root_a: [skill_a], root_b: [skill_b]}, refs,
    )

    slot_a = manifest.build_manifest(
        client_id="client", skill_dir=root_a, probability=0.0,
    ).slots[0]
    slot_b = manifest.build_manifest(
        client_id="client", skill_dir=root_b, probability=0.0,
    ).slots[0]

    assert slot_a.sha == "sha-from-a"
    assert slot_b.sha == "sha-from-b"


def test_refresh_failure_does_not_publish_partial_or_stale_snapshot(
    tmp_path, monkeypatch,
):
    now = [0.0]
    manifest._reset_manifest_cache_for_tests(
        ttl_seconds=1.0, clock=lambda: now[0],
    )
    root = tmp_path / "skills"
    root.mkdir()
    skill = _FakeSkill(root / "alpha")
    refs = {skill.path: ("main-v1", None)}
    _install_fake_repo(monkeypatch, {root: [skill]}, refs)
    assert manifest.build_manifest(
        client_id="client", skill_dir=root, probability=0.0,
    ).slots[0].sha == "main-v1"

    now[0] = 2.0
    monkeypatch.setattr(
        manifest, "main_sha", lambda _path: (_ for _ in ()).throw(RuntimeError("git failed")),
    )
    with pytest.raises(RuntimeError, match="git failed"):
        manifest.build_manifest(
            client_id="client", skill_dir=root, probability=0.0,
        )

    refs[skill.path] = ("main-v2", None)
    monkeypatch.setattr(manifest, "main_sha", lambda path: refs[Path(path)][0])
    assert manifest.build_manifest(
        client_id="client", skill_dir=root, probability=0.0,
    ).slots[0].sha == "main-v2"


def test_refresh_failure_is_shared_by_single_flight_waiters(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    skill = _FakeSkill(root / "alpha", current_main="main-v1")
    scan_started = threading.Event()
    release_scan = threading.Event()
    calls = {"main": 0}
    monkeypatch.setattr(manifest, "SkillRepo", lambda _root: [skill])

    def failing_main(_path):
        calls["main"] += 1
        scan_started.set()
        assert release_scan.wait(timeout=5)
        raise RuntimeError("git failed")

    monkeypatch.setattr(manifest, "main_sha", failing_main)

    def request(_index):
        return manifest.build_manifest(
            client_id="client", skill_dir=root, probability=0.0,
        )

    with ThreadPoolExecutor(max_workers=50) as pool:
        futures = [pool.submit(request, i) for i in range(50)]
        assert scan_started.wait(timeout=5)
        entry = manifest._catalog_cache._entry(root)
        deadline = time.monotonic() + 5
        while len(entry.condition._waiters) < 49 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert len(entry.condition._waiters) == 49
        release_scan.set()
        for future in futures:
            with pytest.raises(RuntimeError, match="git failed"):
                future.result(timeout=5)

    assert calls["main"] == 1


def test_prefs_and_retired_are_applied_per_request_on_cached_pool(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    skills = [
        _FakeSkill(root / "alpha", score=3),
        _FakeSkill(root / "beta", score=2),
        _FakeSkill(root / "gamma", score=1),
    ]
    refs = {s.path: (f"sha-{s.name}", None) for s in skills}
    _install_fake_repo(monkeypatch, {root: skills}, refs)

    first = manifest.build_manifest(
        client_id="client", skill_dir=root, probability=0.0,
        ranked_slots=2, total_slots=3,
    )
    controlled = manifest.build_manifest(
        client_id="client", skill_dir=root, probability=0.0,
        ranked_slots=2, total_slots=3,
        prefs={"pinned": ["gamma"], "blocked": {"alpha"}},
        retired=set(),
    )

    assert [s.skill_name for s in first.slots] == ["alpha", "beta", "gamma"]
    assert [(s.skill_name, s.bucket) for s in controlled.slots] == [
        ("gamma", "pinned"), ("beta", "ranked"),
    ]
