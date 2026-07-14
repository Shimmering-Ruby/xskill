"""test_skillhub_scan_cache.py —— skillhub 三层扫描缓存（L1 TTL 快照 / L2 剪枝 / L3 备忘录）。

覆盖：TTL 内不重扫；内容不变零重读；改内容/删文件被反映；点目录不遍历；
force_refresh 绕过 TTL；single-flight 并发只扫一次；目录缺失 raise 且补齐后恢复；
以及 dashboard tag_cloud 的 TTL 缓存命中。
"""
from __future__ import annotations

import os
import pathlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import xskill.recommend.skillhub as skillhub_module
from xskill.recommend.skillhub import SkillHub


def _write_hub_skill(hub_dir: Path, rel_path: str, description: str) -> Path:
    skill_dir = hub_dir / rel_path
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {skill_dir.name}\ndescription: {description}\n---\n# {skill_dir.name}\n",
        encoding="utf-8")
    return skill_dir


def _make_hub(hub_dir: Path, *, scan_ttl_seconds: float = 5.0) -> SkillHub:
    return SkillHub(enabled=True, hub_dir=hub_dir, embed_client=None,
                    scan_ttl_seconds=scan_ttl_seconds)


def _count_walks(monkeypatch, *, delay: float = 0.0) -> list[int]:
    walk_calls: list[int] = []
    real_walk = os.walk

    def counting_walk(top, *args, **kwargs):
        walk_calls.append(1)
        if delay:
            time.sleep(delay)
        return real_walk(top, *args, **kwargs)

    monkeypatch.setattr(skillhub_module.os, "walk", counting_walk)
    return walk_calls


def test_ttl_snapshot_scans_disk_once(tmp_path, monkeypatch):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "alpha", "django migration helper")
    hub = _make_hub(hub_dir)
    walk_calls = _count_walks(monkeypatch)

    hub.entry("alpha")
    hub.fingerprint()
    hub.entry("alpha")

    assert len(walk_calls) == 1


def test_unchanged_files_are_not_reread(tmp_path, monkeypatch):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "alpha", "one")
    _write_hub_skill(hub_dir, "nested/beta", "two")
    hub = _make_hub(hub_dir, scan_ttl_seconds=0.0)  # 每次调用都真扫，检验 L3 备忘录

    read_calls: list[str] = []
    real_read_bytes = pathlib.Path.read_bytes

    def counting_read_bytes(self):
        read_calls.append(str(self))
        return real_read_bytes(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", counting_read_bytes)

    hub.fingerprint()
    assert len(read_calls) == 2  # 首轮每个 SKILL.md 读一次
    read_calls.clear()

    hub.fingerprint()
    assert read_calls == []  # mtime/size 未变 → 零重读


def test_changed_content_is_reflected(tmp_path):
    hub_dir = tmp_path / "hub"
    skill_dir = _write_hub_skill(hub_dir, "alpha", "old description")
    hub = _make_hub(hub_dir, scan_ttl_seconds=0.0)

    first = hub.entry("alpha")
    old_sha = first["content_sha"]

    (skill_dir / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: brand new much longer description\n---\n# alpha\n",
        encoding="utf-8")
    future = time.time() + 5
    os.utime(skill_dir / "SKILL.md", (future, future))

    updated = hub.entry("alpha")
    assert updated["content_sha"] != old_sha
    assert updated["description"] == "brand new much longer description"


def test_deleted_skill_disappears(tmp_path):
    hub_dir = tmp_path / "hub"
    skill_dir = _write_hub_skill(hub_dir, "alpha", "one")
    _write_hub_skill(hub_dir, "beta", "two")
    hub = _make_hub(hub_dir, scan_ttl_seconds=0.0)

    assert hub.entry("alpha") is not None
    import shutil
    shutil.rmtree(skill_dir)

    assert hub.entry("alpha") is None
    assert (skill_dir / "SKILL.md") not in hub._file_memo


def test_dot_directories_are_not_traversed(tmp_path, monkeypatch):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "alpha", "visible")
    deep_git_skill = hub_dir / ".git" / "objects" / "hidden"
    deep_git_skill.mkdir(parents=True)
    (deep_git_skill / "SKILL.md").write_text(
        "---\nname: hidden\ndescription: buried in git\n---\n", encoding="utf-8")
    hub = _make_hub(hub_dir)

    visited_dirs: list[str] = []
    real_walk = os.walk

    def recording_walk(top, *args, **kwargs):
        for dirpath, dirnames, filenames in real_walk(top, *args, **kwargs):
            visited_dirs.append(dirpath)
            yield dirpath, dirnames, filenames

    monkeypatch.setattr(skillhub_module.os, "walk", recording_walk)

    entries = hub._entries(include_vec=False, require_description=False)
    names = {entry["display_name"] for entry in entries}
    assert names == {"alpha"}
    assert not any(".git" in visited for visited in visited_dirs)
    assert (deep_git_skill / "SKILL.md") not in hub._file_memo


def test_force_refresh_bypasses_ttl(tmp_path):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "alpha", "one")
    hub = _make_hub(hub_dir, scan_ttl_seconds=5.0)

    assert hub.entry("alpha") is not None
    _write_hub_skill(hub_dir, "beta", "two")

    assert hub.entry("beta") is None  # TTL 内旧快照看不到新 skill
    assert hub.entry("beta", force_refresh=True) is not None


def test_single_flight_concurrent_entry(tmp_path, monkeypatch):
    hub_dir = tmp_path / "hub"
    for index in range(5):
        _write_hub_skill(hub_dir, f"skill_{index}", f"desc {index}")
    hub = _make_hub(hub_dir)
    walk_calls = _count_walks(monkeypatch, delay=0.05)

    barrier = threading.Barrier(8)

    def call_entry():
        barrier.wait()
        return hub.entry("skill_0")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = [future.result() for future in
                   [pool.submit(call_entry) for _ in range(8)]]

    assert all(result is not None for result in results)
    assert len(walk_calls) == 1


def test_missing_dir_raises_then_recovers(tmp_path):
    hub_dir = tmp_path / "hub"
    hub = _make_hub(hub_dir)

    with pytest.raises(FileNotFoundError):
        hub.entry("alpha")
    with pytest.raises(FileNotFoundError):
        hub.fingerprint()

    _write_hub_skill(hub_dir, "alpha", "now here")
    assert hub.entry("alpha") is not None


def test_tag_cloud_ttl_cache_hit(tmp_path, monkeypatch):
    from xskill.pipeline.atom import AtomTask, AtomTaskStore
    from xskill.pipeline.registry import get_connection
    from xskill.dashboard.metrics import DashboardMetrics
    import xskill.dashboard.metrics as dashboard_metrics

    watch_dir = tmp_path / "wd"
    watch_dir.mkdir()
    store = AtomTaskStore(root=watch_dir)
    store.save(AtomTask(
        atom_id="atom_t_0000", traj_id="t", offset_start=1, offset_end=2,
        intent="i", summary="s", tags=["django", "nginx"], used_skills=[], ux_score=7,
        pre_atom_id=None, post_atom_id=None, context_prefix="", raw_segment=""))
    db_path = tmp_path / "tg.db"
    conn = get_connection(db_path)
    conn.execute("INSERT INTO watch_dirs(path,label,ecosystem) VALUES(?,?,?)",
                 (str(watch_dir), "w", "claude_code"))
    conn.commit()
    conn.close()

    dashboard_metrics._tag_cloud_cache.clear()
    traversal_calls: list[int] = []
    real_all_atoms = AtomTaskStore.all_atoms

    def counting_all_atoms(self):
        traversal_calls.append(1)
        yield from real_all_atoms(self)

    monkeypatch.setattr(AtomTaskStore, "all_atoms", counting_all_atoms)

    metrics = DashboardMetrics(db_path=db_path)
    first = {row["tag"]: row["count"] for row in metrics.tag_cloud()}
    second = {row["tag"]: row["count"] for row in metrics.tag_cloud()}

    assert first == second == {"django": 1, "nginx": 1}
    assert len(traversal_calls) == 1
