"""test_dashboard_metrics.py —— DashboardMetrics 衍生指标"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import xskill.dashboard.metrics as dashboard_metrics
import xskill.skill.catalog_store as catalog_store
from xskill.pipeline.registry import (
    TrajectoryStatus,
    get_connection,
    harness_share,
    model_share,
)
from xskill.dashboard.metrics import (
    DashboardMetrics, skills_catalog, skills_catalog_page)

SUCCESSFULLY_SPLIT_STATUSES = (
    TrajectoryStatus.SPLIT_DONE,
    TrajectoryStatus.INDEXED,
    TrajectoryStatus.CLUSTERING,
    TrajectoryStatus.DONE,
)


@pytest.fixture(autouse=True)
def _isolate_skills_catalog_registry(tmp_path, monkeypatch):
    """技能清单投影表写入隔离 registry，避免污染本机 DB / 串测。"""
    registry = tmp_path / "_skills_catalog_registry.db"
    monkeypatch.setattr(
        "xskill.config.get_registry_db_path",
        lambda: registry,
    )
    monkeypatch.setattr(
        "xskill.pipeline.registry.get_registry_db_path",
        lambda: registry,
    )


def _seed_team(db):
    """一个 team server：自有 claude_code 本机目录 + 一个 team_client 上传目录。
    team 上传的轨迹有的带 source_harness（新 client），有的没带（旧 client）。"""
    conn = get_connection(db)
    conn.execute("INSERT INTO watch_dirs(id,path,label,ecosystem) VALUES(1,'/cc','cc','claude_code')")
    conn.execute("INSERT INTO watch_dirs(id,path,label,ecosystem) VALUES(2,'/tc','client-a','team_client')")
    rows = [  # (wd, source_harness)
        (1, None),          # 本机 cc 目录 → harness 从 ecosystem 推断 claude_code
        (2, 'codex'),       # team 上传，新 client 带了 harness
        (2, None),          # team 上传，旧 client 没带 → unknown
    ]
    for i, (wd, hn) in enumerate(rows):
        conn.execute("INSERT INTO trajectories(watch_dir_id,filename,status,source_harness)"
                     " VALUES(?,?,?,?)", (wd, f"traj_{i}.md", "done", hn))
    conn.commit()
    conn.close()


def test_harness_share_derives_from_ecosystem(tmp_path):
    db = tmp_path / "h.db"
    _seed_team(db)
    share = {h["harness"]: h["trajs"] for h in harness_share(db)}
    assert share == {"claude_code": 1, "codex": 1, "unknown": 1}
    # 内部标签 team_client 绝不暴露给用户
    assert "team_client" not in share


def test_by_ecosystem_replaces_team_client_with_harness(tmp_path):
    db = tmp_path / "h.db"
    _seed_team(db)
    ecos = {r["ecosystem"]: r["trajs"] for r in DashboardMetrics(db_path=db).by_ecosystem()}
    assert "team_client" not in ecos          # 不再把内部标签当生态
    assert ecos.get("claude_code") == 1
    assert ecos.get("codex") == 1             # team 上传带 harness → 归 codex
    assert ecos.get("unknown") == 1           # team 上传无 harness → unknown


def test_harness_share_custom_unknown_label(tmp_path):
    # config.dashboard.default_harness 覆盖：缺 harness 的轨迹归到指定桶，不再叫 unknown
    db = tmp_path / "h.db"
    _seed_team(db)
    share = {h["harness"]: h["trajs"] for h in harness_share(db, unknown_label="claude_code")}
    # 那条无 harness 的 team 上传并入 claude_code（本机 1 + 兜底 1）
    assert share == {"claude_code": 2, "codex": 1}
    assert "unknown" not in share


def test_by_ecosystem_custom_unknown_label(tmp_path):
    db = tmp_path / "h.db"
    _seed_team(db)
    m = DashboardMetrics(db_path=db, unknown_harness="codex")
    ecos = {r["ecosystem"]: r["trajs"] for r in m.by_ecosystem()}
    assert ecos.get("codex") == 2             # 自带 codex 1 + 兜底并入 1
    assert "unknown" not in ecos


def test_by_model_custom_unknown_label(tmp_path):
    db = tmp_path / "h.db"
    conn = get_connection(db)
    conn.execute("INSERT INTO watch_dirs(id,path,label,ecosystem) VALUES(1,'/cc','cc','claude_code')")
    conn.execute("INSERT INTO trajectories(watch_dir_id,filename,status,source_model)"
                 " VALUES(1,'a.md','done','deepseek-v4-pro')")
    conn.execute("INSERT INTO trajectories(watch_dir_id,filename,status,source_model)"
                 " VALUES(1,'b.md','done',NULL)")
    conn.commit(); conn.close()
    models = {r["model"]: r["trajs"] for r in
              DashboardMetrics(db_path=db, unknown_model="deepseek-v4-flash").by_model()}
    assert models == {"deepseek-v4-pro": 1, "deepseek-v4-flash": 1}


def test_unknown_label_is_sql_injection_safe(tmp_path):
    # 自由字符串经命名绑定参数注入：带引号/分号的标签原样出现，不破坏 SQL
    db = tmp_path / "h.db"
    _seed_team(db)
    weird = "o'brien; DROP TABLE trajectories;--"
    share = {h["harness"]: h["trajs"] for h in harness_share(db, unknown_label=weird)}
    assert share.get(weird) == 1              # 兜底标签原样作为分组键
    # 表没被删：再查一次仍有数据
    assert sum(h["trajs"] for h in harness_share(db)) == 3


def test_model_share_default_label_unchanged(tmp_path):
    # 不传 unknown_label → 仍是 'unknown'（保护 canary/stats 的哨兵语义）
    db = tmp_path / "h.db"
    conn = get_connection(db)
    conn.execute("INSERT INTO watch_dirs(id,path,label,ecosystem) VALUES(1,'/cc','cc','claude_code')")
    conn.execute("INSERT INTO trajectories(watch_dir_id,filename,status,source_model) VALUES(1,'a.md','done',NULL)")
    conn.commit(); conn.close()
    assert model_share(db)[0]["model"] == "unknown"


def _seed(db):
    conn = get_connection(db)
    conn.execute("INSERT INTO watch_dirs(path,label,ecosystem) VALUES('/cc','cc','claude_code')")
    conn.execute("INSERT INTO watch_dirs(path,label,ecosystem) VALUES('/oc','oc','opencode')")
    rows = [  # (wd, status, atoms, skill_generated, retry, ux, model)
        (1, 'done', 6, 'nginx-skill', 0, 8.0, 'deepseek-v4-pro'),
        (1, 'done', 4, '', 1, 7.0, 'deepseek-v4-flash'),
        (1, 'splitting', 2, None, 0, None, 'deepseek-v4-flash'),
        (2, 'done', 3, 'oc-skill', 0, 7.5, 'deepseek-v4-flash'),
    ]
    for wd, st, a, sg, rt, ux, m in rows:
        conn.execute("INSERT INTO trajectories(watch_dir_id,filename,status,tasks_extracted,"
                     "skill_generated,retry_count,ux_score,source_model) VALUES(?,?,?,?,?,?,?,?)",
                     (wd, f"f{a}{st}", st, a, sg, rt, ux, m))
    conn.commit()
    conn.close()


def test_overview_ratios(tmp_path):
    db = tmp_path / "r.db"
    _seed(db)
    o = DashboardMetrics(db_path=db).overview()
    assert o["trajs"] == 4 and o["atoms"] == 15
    assert o["avg_atoms_per_traj"] == 4.33          # 已成功拆分：13/3
    # 终态口径（审计 P2-10）：3 done + 1 splitting(在途,不进分母) → 100%
    assert o["success_rate"] == 100.0
    assert o["retry_rate"] == 25.0                  # 1 retried / 4
    # trajectories.ux_score 是死列（审计 P1-5）：无使用记录 → None 不显示假数
    assert o["avg_ux"] is None and o["ux_n"] == 0
    assert "skill_yield" not in o                   # 指标已下线（审计 P2-8）


def test_overview_empty_db_no_zerodiv(tmp_path):
    db = tmp_path / "e.db"
    get_connection(db).close()
    o = DashboardMetrics(db_path=db).overview()
    assert o == {"trajs": 0, "atoms": 0, "avg_atoms_per_traj": None,
                 "success_rate": 0.0, "filtered": 0, "retry_rate": 0.0,
                 "avg_ux": None, "ux_n": 0}


def test_overview_avg_atoms_uses_successfully_split_trajectories(tmp_path):
    db = tmp_path / "split-average.db"
    conn = get_connection(db)
    conn.execute(
        "INSERT INTO watch_dirs(id,path,label,ecosystem)"
        " VALUES(1,'/cc','cc','claude_code')")
    rows = [
        ("split_done", 2),
        ("indexed", 5),
        ("clustering", 11),
        ("done", 23),
        ("splitting", 101),
    ]
    for index, (status, atoms) in enumerate(rows):
        conn.execute(
            "INSERT INTO trajectories(watch_dir_id,filename,status,tasks_extracted)"
            " VALUES(1,?,?,?)",
            (f"traj-{index}.md", status, atoms),
        )
    conn.commit()
    conn.close()

    overview = DashboardMetrics(db_path=db).overview()

    assert overview["trajs"] == 5
    assert overview["atoms"] == 142
    assert overview["avg_atoms_per_traj"] == 10.25


@pytest.mark.parametrize(
    ("included_status", "included_atoms"),
    [
        (TrajectoryStatus.SPLIT_DONE, 3),
        (TrajectoryStatus.INDEXED, 7),
        (TrajectoryStatus.CLUSTERING, 13),
        (TrajectoryStatus.DONE, 29),
    ],
)
def test_overview_avg_atoms_includes_each_split_status_and_excludes_all_others(
    tmp_path,
    included_status,
    included_atoms,
):
    db = tmp_path / f"{included_status.value}-average.db"
    conn = get_connection(db)
    conn.execute(
        "INSERT INTO watch_dirs(id,path,label,ecosystem)"
        " VALUES(1,'/cc','cc','claude_code')")
    excluded_statuses = [
        status for status in TrajectoryStatus
        if status not in SUCCESSFULLY_SPLIT_STATUSES
    ]
    rows = [(included_status.value, included_atoms)]
    rows.extend(
        (status.value, 100 + index * 17)
        for index, status in enumerate(excluded_statuses)
    )
    for index, (status, atoms) in enumerate(rows):
        conn.execute(
            "INSERT INTO trajectories(watch_dir_id,filename,status,tasks_extracted)"
            " VALUES(1,?,?,?)",
            (f"traj-{index}.md", status, atoms),
        )
    conn.commit()
    conn.close()

    overview = DashboardMetrics(db_path=db).overview()

    assert TrajectoryStatus.FILTERED in excluded_statuses
    assert overview["trajs"] == len(rows)
    assert overview["atoms"] == sum(atoms for _, atoms in rows)
    assert overview["avg_atoms_per_traj"] == float(included_atoms)


def test_overview_avg_atoms_is_zero_when_split_trajectories_have_no_atoms(tmp_path):
    db = tmp_path / "zero-split-average.db"
    conn = get_connection(db)
    conn.execute(
        "INSERT INTO watch_dirs(id,path,label,ecosystem)"
        " VALUES(1,'/cc','cc','claude_code')")
    conn.execute(
        "INSERT INTO trajectories(watch_dir_id,filename,status,tasks_extracted)"
        " VALUES(1,'split.md','split_done',0)")
    conn.execute(
        "INSERT INTO trajectories(watch_dir_id,filename,status,tasks_extracted)"
        " VALUES(1,'pending.md','splitting',9)")
    conn.commit()
    conn.close()

    overview = DashboardMetrics(db_path=db).overview()

    assert overview["trajs"] == 2
    assert overview["atoms"] == 9
    assert overview["avg_atoms_per_traj"] == 0.0


def test_overview_avg_atoms_is_none_without_split_trajectories(tmp_path):
    db = tmp_path / "no-split-average.db"
    conn = get_connection(db)
    conn.execute(
        "INSERT INTO watch_dirs(id,path,label,ecosystem)"
        " VALUES(1,'/cc','cc','claude_code')")
    conn.execute(
        "INSERT INTO trajectories(watch_dir_id,filename,status,tasks_extracted)"
        " VALUES(1,'pending.md','splitting',7)")
    conn.execute(
        "INSERT INTO trajectories(watch_dir_id,filename,status,tasks_extracted)"
        " VALUES(1,'failed.md','error',11)")
    conn.commit()
    conn.close()

    overview = DashboardMetrics(db_path=db).overview()

    assert overview["trajs"] == 2
    assert overview["atoms"] == 18
    assert overview["avg_atoms_per_traj"] is None


def test_by_ecosystem(tmp_path):
    db = tmp_path / "r.db"
    _seed(db)
    rows = {r["ecosystem"]: r for r in DashboardMetrics(db_path=db).by_ecosystem()}
    assert rows["claude_code"]["trajs"] == 3 and rows["claude_code"]["atoms"] == 12
    assert "skills" not in rows["claude_code"]   # skill_generated 死列已下线
    assert rows["opencode"]["trajs"] == 1


def test_skills_catalog_lists_skills(tmp_path):
    """技能库清单：分析式读 skill 目录,不依赖埋点 → 永远有内容。"""
    from xskill.skill.git import init_skill_repo_on_baby, commit_baby_to_main_branch
    sd = tmp_path / "skill"
    sd.mkdir()
    # 一个 baby、一个已 graduate 到 main
    init_skill_repo_on_baby(str(sd / "wip-skill"), name="wip-skill", description="草稿描述")
    init_skill_repo_on_baby(str(sd / "ready-skill"), name="ready-skill", description="正式描述")
    commit_baby_to_main_branch(str(sd / "ready-skill"), "graduate")
    (sd / ".hidden").mkdir()  # 隐藏目录应被跳过

    cat = skills_catalog(sd)
    names = {s["name"]: s for s in cat}
    assert set(names) == {"wip-skill", "ready-skill"}
    assert names["wip-skill"]["state"] == "baby"
    assert names["ready-skill"]["state"] == "main"
    assert "正式描述" in names["ready-skill"]["description"]
    # main 排在 baby 前
    assert cat[0]["name"] == "ready-skill"


def test_skills_catalog_empty_dir(tmp_path):
    assert skills_catalog(tmp_path / "nope") == []


def test_skills_catalog_native_source_tag(tmp_path):
    """向后兼容：不传 skillhub 时,自产条目统一带 source='native',无 skillhub 混入。"""
    from xskill.skill.git import init_skill_repo_on_baby
    sd = tmp_path / "skill"; sd.mkdir()
    init_skill_repo_on_baby(str(sd / "wip-skill"), name="wip-skill", description="草稿")
    cat = skills_catalog(sd)
    assert len(cat) == 1
    assert cat[0]["source"] == "native"
    assert all(s.get("source") != "skillhub" for s in cat)


def _make_skillhub(tmp_path, name, description, sub="vendor/tool"):
    """构造启用态 SkillHub：hub_dir 下放一个三方 SKILL.md（embed_client 无需真实,
    技能库列表走 include_vec=False 分支）。"""
    from xskill.recommend.skillhub import SkillHub
    hub_dir = tmp_path / "hub"
    skdir = hub_dir / sub
    skdir.mkdir(parents=True)
    (skdir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n正文\n", encoding="utf-8")
    return SkillHub(enabled=True, hub_dir=hub_dir, embed_client=None)


def test_skills_catalog_merges_skillhub(tmp_path):
    """技能库列表既含自产(native)又含三方(skillhub)条目,字段符合契约。"""
    from xskill.skill.git import init_skill_repo_on_baby, commit_baby_to_main_branch
    sd = tmp_path / "skill"; sd.mkdir()
    init_skill_repo_on_baby(str(sd / "ready-skill"), name="ready-skill", description="正式描述")
    commit_baby_to_main_branch(str(sd / "ready-skill"), "graduate")
    hub = _make_skillhub(tmp_path, "vendor-skill", "三方能力描述", sub="vendor/tool")

    cat = skills_catalog(sd, skillhub=hub)
    by_source: dict = {}
    for s in cat:
        by_source.setdefault(s["source"], []).append(s)
    assert set(by_source) == {"native", "skillhub"}

    native = by_source["native"][0]
    assert native["name"] == "ready-skill" and native["state"] == "main"

    hub_row = by_source["skillhub"][0]
    assert hub_row["name"] == "vendor-skill"          # 展示名取 display_name
    assert hub_row["state"] == "skillhub"             # 无 git 分支
    assert hub_row["hub"] == "vendor/tool"            # skillhub 目录下相对路径
    assert hub_row["skill_id"].startswith("vendor-skill@")  # name@path_hash
    assert "三方能力描述" in hub_row["description"]
    assert hub_row["use_count"] == 0                  # 无使用记录 → 0
    # 自产排在三方之前
    assert cat.index(native) < cat.index(hub_row)


def test_skills_catalog_skillhub_none_is_noop(tmp_path):
    """skillhub=None（缺省/禁用）→ no-op：只有自产,不报错。"""
    from xskill.skill.git import init_skill_repo_on_baby
    sd = tmp_path / "skill"; sd.mkdir()
    init_skill_repo_on_baby(str(sd / "wip"), name="wip", description="d")
    assert skills_catalog(sd, skillhub=None) == skills_catalog(sd)
    # 禁用态 SkillHub 也应 no-op（其 _entries 内部判 enabled）
    from xskill.recommend.skillhub import SkillHub
    disabled = SkillHub(enabled=False, hub_dir=tmp_path / "nohub", embed_client=None)
    cat = skills_catalog(sd, skillhub=disabled)
    assert all(s["source"] == "native" for s in cat)


def test_skills_catalog_accepts_entry_list(tmp_path):
    """skillhub 入参也可直接是条目列表（契约：SkillHub 对象或 entries）。"""
    entries = [{
        "source": "skillhub", "name": "x@abc123", "skill_id": "x@abc123",
        "display_name": "x", "source_path": "team/x", "description": "e",
        "use_count": 5,
    }]
    cat = skills_catalog(tmp_path / "nope", skillhub=entries)
    assert len(cat) == 1
    row = cat[0]
    assert row["source"] == "skillhub" and row["hub"] == "team/x"
    assert row["skill_id"] == "x@abc123" and row["use_count"] == 5
    assert row["name"] == "x"


def _write_catalog_skill(root, name, description, branch="main"):
    skill = root / name
    (skill / ".git" / "refs" / "heads").mkdir(parents=True)
    (skill / ".git" / "refs" / "heads" / branch).write_text("sha\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n"
        "metadata:\n  version: 1\n---\nbody\n",
        encoding="utf-8",
    )
    return skill


def test_skills_catalog_concurrent_calls_for_300_skills_scan_once(
        tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    for i in range(300):
        _write_catalog_skill(root, f"skill-{i:03d}", f"description {i}")

    original = catalog_store.scan_skills_catalog
    calls = 0
    calls_lock = threading.Lock()

    def counted(*args, **kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(catalog_store, "scan_skills_catalog", counted)
    barrier = threading.Barrier(32)

    def load():
        barrier.wait()
        return skills_catalog(root)

    with ThreadPoolExecutor(max_workers=32) as pool:
        results = list(pool.map(lambda _i: load(), range(32)))
    assert calls == 1
    assert all(len(rows) == 300 for rows in results)


def test_skills_catalog_upsert_picks_up_disk_mutations(tmp_path):
    """投影表：仅改盘不 UPSERT 时列表保持旧值；写出口 UPSERT 后立即可见。"""
    root = tmp_path / "skills"
    root.mkdir()
    skill = _write_catalog_skill(root, "alpha", "version one", branch="main")

    first = skills_catalog(root)[0]
    assert first["description"] == "version one"

    (skill / ".git" / "refs" / "heads" / "staging").write_text("sha2\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: version two\n"
        "metadata:\n  version: 2\n---\nbody\n",
        encoding="utf-8",
    )
    (skill / ".candidates.yml").write_text(
        "candidates:\n  - summary: one\n  - summary: two\n", encoding="utf-8")

    still_stale = skills_catalog(root)[0]
    assert (still_stale["state"], still_stale["description"], still_stale["candidates"]) == (
        "main", "version one", 0)

    catalog_store.upsert_native_skill(skill, db_path=tmp_path / "_skills_catalog_registry.db")
    refreshed = skills_catalog(root)[0]
    assert (refreshed["state"], refreshed["description"], refreshed["version"],
            refreshed["candidates"]) == ("staging", "version two", 2, 2)


def test_skills_catalog_isolates_roots_and_skillhub_inputs(tmp_path):
    root_a = tmp_path / "a"; root_a.mkdir()
    root_b = tmp_path / "b"; root_b.mkdir()
    _write_catalog_skill(root_a, "same", "root a")
    _write_catalog_skill(root_b, "same", "root b")
    hub_a = [{"display_name": "hub-a", "source_path": "a/tool",
              "skill_id": "hub-a@1", "description": "A"}]
    hub_b = [{"display_name": "hub-b", "source_path": "b/tool",
              "skill_id": "hub-b@2", "description": "B"}]

    rows_a = skills_catalog(root_a, skillhub=hub_a)
    rows_b = skills_catalog(root_b, skillhub=hub_b)
    assert [(row["name"], row["description"]) for row in rows_a] == [
        ("same", "root a"), ("hub-a", "A")]
    assert [(row["name"], row["description"]) for row in rows_b] == [
        ("same", "root b"), ("hub-b", "B")]


def test_skills_catalog_equivalent_skillhub_instances_share_backfill(
        tmp_path, monkeypatch):
    from xskill.recommend.skillhub import SkillHub

    root = tmp_path / "skills"; root.mkdir()
    hub_root = tmp_path / "hub"; hub_root.mkdir()
    _write_catalog_skill(hub_root, "vendor", "third party")
    first_hub = SkillHub(enabled=True, hub_dir=hub_root, embed_client=None)
    second_hub = SkillHub(enabled=True, hub_dir=hub_root, embed_client=object())
    original = catalog_store.scan_skills_catalog
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(catalog_store, "scan_skills_catalog", counted)
    assert skills_catalog(root, skillhub=first_hub) == skills_catalog(
        root, skillhub=second_hub)
    assert calls == 1


def test_skills_catalog_failed_backfill_does_not_poison_meta(tmp_path):
    from xskill.recommend.skillhub import SkillHub

    root = tmp_path / "skills"; root.mkdir()
    hub_root = tmp_path / "hub"
    hub = SkillHub(enabled=True, hub_dir=hub_root, embed_client=None)
    with pytest.raises(FileNotFoundError):
        skills_catalog(root, skillhub=hub)

    _write_catalog_skill(hub_root, "vendor", "available now")
    rows = skills_catalog(root, skillhub=hub)
    assert [(row["name"], row["source"]) for row in rows] == [
        ("vendor", "skillhub")]


@pytest.mark.flaky(reruns=3, reruns_delay=1)
def test_skills_catalog_concurrent_failure_is_shared(
        tmp_path, monkeypatch):
    root = tmp_path / "skills"; root.mkdir()
    original = catalog_store.scan_skills_catalog
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def failing(*_args, **_kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
        entered.set()
        assert release.wait(timeout=5)
        raise FileNotFoundError("catalog unavailable")

    monkeypatch.setattr(catalog_store, "scan_skills_catalog", failing)

    def load_error():
        try:
            skills_catalog(root)
        except BaseException as exc:  # 返回异常供主线程检查，不让 worker 中断
            return exc
        raise AssertionError("expected catalog failure")

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(load_error) for _ in range(16)]
        assert entered.wait(timeout=5)
        time.sleep(0.05)
        release.set()
        errors = [future.result(timeout=5) for future in futures]

    assert calls == 1
    assert all(isinstance(error, FileNotFoundError) for error in errors)

    monkeypatch.setattr(catalog_store, "scan_skills_catalog", original)
    assert skills_catalog(root) == []


def test_skills_catalog_isolates_multiple_roots(tmp_path):
    for index in range(3):
        root = tmp_path / f"skills-{index}"
        root.mkdir()
        _write_catalog_skill(root, f"skill-{index}", f"description {index}")
        assert len(skills_catalog(root)) == 1


def test_skills_catalog_returns_independent_copies(tmp_path):
    root = tmp_path / "skills"; root.mkdir()
    _write_catalog_skill(root, "alpha", "original")
    first = skills_catalog(root)
    first[0]["description"] = "caller mutation"
    first.append({"name": "injected"})

    second = skills_catalog(root)
    assert len(second) == 1
    assert second[0]["name"] == "alpha"
    assert second[0]["description"] == "original"


def _fake_scan_rows(root, count):
    root_key = catalog_store.catalog_root_key(root)
    rows = []
    for index in range(count):
        state = "main" if index % 2 else "staging"
        name = f"s{index:04d}"
        rows.append({
            "catalog_key": f"native:{name}",
            "root_key": root_key,
            "name": name,
            "repo_name": name,
            "source": "native",
            "state": state,
            "description": "",
            "version": 1,
            "candidates": 0,
            "candidates_count": 0,
            "main_sha": "",
            "staging_sha": "",
            "distributable": 1,
            "search_id": name,
            "hub": "",
            "skill_id": "",
            "use_count": 0,
        })
    return rows


def test_skills_catalog_page_counts_and_page_isolation(tmp_path, monkeypatch):
    """分页：total/by_state 按全量；改返回页不污染下一请求。"""
    root = tmp_path / "skills"
    root.mkdir()
    rows = _fake_scan_rows(root, 300)

    def build_rows(*_args, **_kwargs):
        return [dict(entry) for entry in rows]

    monkeypatch.setattr(catalog_store, "scan_skills_catalog", build_rows)

    page = skills_catalog_page(root, limit=10, offset=20)
    assert page["total"] == 300
    assert page["by_state"] == {"staging": 150, "main": 150}
    assert [entry["name"] for entry in page["skills"]] == [
        f"s{index:04d}" for index in range(20, 30)]
    assert page["offset"] == 20 and page["limit"] == 10

    page["skills"][0]["description"] = "caller mutation"
    fresh = skills_catalog_page(root, limit=10, offset=20)
    assert fresh["skills"][0]["description"] == ""
    page["by_state"]["main"] = -1
    assert skills_catalog_page(root, limit=1)["by_state"]["main"] == 150


def test_skills_catalog_page_backfills_once_across_requests(tmp_path, monkeypatch):
    """多次翻页只触发一次扫盘 backfill。"""
    root = tmp_path / "skills"
    root.mkdir()
    calls = 0

    def counted(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _fake_scan_rows(root, 50)

    monkeypatch.setattr(catalog_store, "scan_skills_catalog", counted)
    skills_catalog_page(root, limit=10, offset=0)
    skills_catalog_page(root, limit=10, offset=10)
    skills_catalog_page(root, name="s0007")
    assert calls == 1


def test_skills_catalog_page_name_filter_returns_matches(tmp_path, monkeypatch):
    """name 定向查返回匹配条目；total 为过滤后条数，by_state 仍按全量。"""
    root = tmp_path / "skills"
    root.mkdir()

    def build_thousand(*_args, **_kwargs):
        return _fake_scan_rows(root, 1000)

    monkeypatch.setattr(catalog_store, "scan_skills_catalog", build_thousand)
    page = skills_catalog_page(root, name="s0512")
    assert [entry["name"] for entry in page["skills"]] == ["s0512"]
    assert page["total"] == 1
    assert page["by_state"] == {"main": 500, "staging": 500}


def test_users_lists_team_clients(tmp_path):
    db = tmp_path / "u.db"
    conn = get_connection(db)
    conn.execute("INSERT INTO watch_dirs(id,path,label,ecosystem) VALUES(1,'/a','alice','team_client')")
    conn.execute("INSERT INTO watch_dirs(id,path,label,ecosystem) VALUES(2,'/b','bob','team_client')")
    conn.execute("INSERT INTO watch_dirs(id,path,label,ecosystem) VALUES(3,'/cc','local','claude_code')")
    conn.execute("INSERT INTO trajectories(watch_dir_id,filename,tasks_extracted) VALUES(1,'t1.md',3)")
    conn.execute("INSERT INTO trajectories(watch_dir_id,filename,tasks_extracted) VALUES(1,'t2.md',2)")
    conn.execute("INSERT INTO trajectories(watch_dir_id,filename,tasks_extracted) VALUES(2,'t3.md',1)")
    conn.commit(); conn.close()
    users = {u["client_id"]: u for u in DashboardMetrics(db_path=db).users()}
    assert set(users) == {"alice", "bob"}      # 本机 claude_code 不算"团队用户"
    assert users["alice"]["trajs"] == 2 and users["alice"]["atoms"] == 5
    assert users["bob"]["trajs"] == 1


def test_tag_cloud_aggregates_atom_tags(tmp_path):
    from xskill.pipeline.atom import AtomTask, AtomTaskStore
    wd = tmp_path / "wd"; wd.mkdir()
    store = AtomTaskStore(root=wd)
    for i, tags in enumerate([["django", "migrate"], ["django", "orm"], ["nginx"]]):
        store.save(AtomTask(
            atom_id=f"atom_t_{i:04d}", traj_id="t", offset_start=1, offset_end=2,
            intent="i", summary="s", tags=tags, used_skills=[], ux_score=7,
            pre_atom_id=None, post_atom_id=None, context_prefix="", raw_segment=""))
    db = tmp_path / "tg.db"
    conn = get_connection(db)
    conn.execute("INSERT INTO watch_dirs(path,label,ecosystem) VALUES(?,?,?)",
                 (str(wd), "w", "claude_code"))
    conn.commit(); conn.close()
    cloud = {t["tag"]: t["count"] for t in DashboardMetrics(db_path=db).tag_cloud()}
    assert cloud["django"] == 2
    assert cloud["migrate"] == 1 and cloud["nginx"] == 1


def test_by_model(tmp_path):
    db = tmp_path / "r.db"
    _seed(db)
    rows = {r["model"]: r for r in DashboardMetrics(db_path=db).by_model()}
    assert rows["deepseek-v4-flash"]["trajs"] == 3
    assert "skills" not in rows["deepseek-v4-pro"]  # 死列已下线


# ── L1:使用打分事实源(.ux_scores.jsonl)的短时缓存 + 单飞 ──────────────

def _write_ux_scores(root, skill_name, records):
    """在 <root>/<skill>/.ux_scores.jsonl 落几条打分记录（写入侧的真实形态）。"""
    import json
    skill = root / skill_name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / ".ux_scores.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8")
    return skill


def _count_ux_reads(monkeypatch):
    """统计 load_ux_scores 的真实读盘次数（= 每个 skill 目录一次）。"""
    import xskill.canary as canary_module
    reads: list[str] = []
    reads_lock = threading.Lock()
    real_load = canary_module.load_ux_scores

    def counting_load(skill_dir):
        with reads_lock:
            reads.append(str(skill_dir))
        return real_load(skill_dir)

    monkeypatch.setattr(canary_module, "load_ux_scores", counting_load)
    return reads


def test_load_usage_records_scans_disk_once_across_repeated_calls(
        tmp_path, monkeypatch):
    """L1 头号:8 个端点各调一次 load_usage_records,只准扫一次盘。"""
    root = tmp_path / "skills"
    for index in range(20):
        _write_ux_scores(root, f"skill-{index:02d}", [
            {"skill_name": f"skill-{index:02d}", "side": "main",
             "commit_sha": "sha1", "score": 8, "scored_at": "2026-07-01T00:00:00",
             "atom_id": f"atom_traj{index}_0001"},
        ])
    dashboard_metrics._usage_records_cache.clear()
    reads = _count_ux_reads(monkeypatch)

    first = dashboard_metrics.load_usage_records(root)
    for _ in range(7):
        assert dashboard_metrics.load_usage_records(root) == first

    assert len(first) == 20
    assert len(reads) == 20          # 一次扫描 = 每个 skill 目录读一次
    assert first[0]["traj_id"] == "traj0"   # 归一化口径不变


def test_load_usage_records_concurrent_calls_share_one_scan(
        tmp_path, monkeypatch):
    """单飞:缓存到期瞬间的并发请求波次只扫一次盘,不惊群。"""
    root = tmp_path / "skills"
    for index in range(30):
        _write_ux_scores(root, f"skill-{index:02d}", [
            {"skill_name": f"skill-{index:02d}", "side": "main",
             "commit_sha": "sha1", "score": 5, "scored_at": "2026-07-01T00:00:00",
             "atom_id": f"atom_traj{index}_0001"},
        ])
    dashboard_metrics._usage_records_cache.clear()
    reads = _count_ux_reads(monkeypatch)
    barrier = threading.Barrier(32)

    def load():
        barrier.wait()
        return dashboard_metrics.load_usage_records(root)

    with ThreadPoolExecutor(max_workers=32) as pool:
        results = [future.result(timeout=10)
                   for future in [pool.submit(load) for _ in range(32)]]

    assert len(reads) == 30          # 30 个 skill 各读一次 = 只扫了一遍
    assert all(len(records) == 30 for records in results)


def test_load_usage_records_ttl_expiry_picks_up_new_scores(
        tmp_path, monkeypatch):
    """TTL 内读缓存,过期后能看到新写入的打分（缓存不会把看板钉死在旧数据）。"""
    root = tmp_path / "skills"
    _write_ux_scores(root, "alpha", [
        {"skill_name": "alpha", "side": "main", "commit_sha": "sha1",
         "score": 6, "scored_at": "2026-07-01T00:00:00",
         "atom_id": "atom_traj_0001"},
    ])
    dashboard_metrics._usage_records_cache.clear()
    monkeypatch.setattr(
        dashboard_metrics._usage_records_cache, "ttl_seconds", 0.02)

    assert len(dashboard_metrics.load_usage_records(root)) == 1
    _write_ux_scores(root, "alpha", [
        {"skill_name": "alpha", "side": "main", "commit_sha": "sha1",
         "score": 6, "scored_at": "2026-07-01T00:00:00",
         "atom_id": "atom_traj_0001"},
        {"skill_name": "alpha", "side": "staging", "commit_sha": "sha2",
         "score": 9, "scored_at": "2026-07-02T00:00:00",
         "atom_id": "atom_traj_0002"},
    ])
    assert len(dashboard_metrics.load_usage_records(root)) == 1   # TTL 内仍是旧的
    time.sleep(0.04)
    refreshed = dashboard_metrics.load_usage_records(root)
    assert [record["sha"] for record in refreshed] == ["sha1", "sha2"]


def test_load_usage_records_isolates_skill_dirs(tmp_path):
    """不同 skill_dir 各自成键,不会串味。"""
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    _write_ux_scores(root_a, "alpha", [
        {"skill_name": "alpha", "side": "main", "commit_sha": "sha-a",
         "score": 1, "scored_at": "2026-07-01T00:00:00"}])
    _write_ux_scores(root_b, "alpha", [
        {"skill_name": "alpha", "side": "main", "commit_sha": "sha-b",
         "score": 2, "scored_at": "2026-07-01T00:00:00"}])
    dashboard_metrics._usage_records_cache.clear()

    assert [r["sha"] for r in dashboard_metrics.load_usage_records(root_a)] == ["sha-a"]
    assert [r["sha"] for r in dashboard_metrics.load_usage_records(root_b)] == ["sha-b"]


def test_load_usage_records_returns_independent_copies(tmp_path):
    """调用方改自己那份改不到缓存(缓存内的记录永不被改写)。"""
    root = tmp_path / "skills"
    _write_ux_scores(root, "alpha", [
        {"skill_name": "alpha", "side": "main", "commit_sha": "sha1",
         "score": 6, "scored_at": "2026-07-01T00:00:00"}])
    dashboard_metrics._usage_records_cache.clear()

    first = dashboard_metrics.load_usage_records(root)
    first[0]["skill"] = "caller mutation"
    first[0]["score"] = 999
    first.append({"skill": "injected"})

    second = dashboard_metrics.load_usage_records(root)
    assert len(second) == 1
    assert (second[0]["skill"], second[0]["score"]) == ("alpha", 6)


def test_dashboard_panels_share_one_usage_scan(tmp_path, monkeypatch):
    """overview / canary / skill 详情 三块面板共用一次扫描（各自独立调用）。"""
    db = tmp_path / "p.db"
    conn = get_connection(db)
    conn.execute("INSERT INTO watch_dirs(id,path,label,ecosystem)"
                 " VALUES(1,'/cc','cc','claude_code')")
    conn.execute("INSERT INTO trajectories(watch_dir_id,filename,status,"
                 "tasks_extracted) VALUES(1,'traj0.md','done',2)")
    conn.commit()
    conn.close()
    root = tmp_path / "skills"
    _write_ux_scores(root, "alpha", [
        {"skill_name": "alpha", "side": "main", "commit_sha": "sha1",
         "score": 8, "scored_at": "2026-07-01T00:00:00",
         "atom_id": "atom_traj0_0001"}])
    dashboard_metrics._usage_records_cache.clear()
    reads = _count_ux_reads(monkeypatch)

    metrics = DashboardMetrics(db_path=db, skill_dir=root)
    overview = metrics.overview()
    sides = metrics.canary_sides()
    detail = metrics.skill_detail("alpha")

    assert len(reads) == 1           # 三块面板 + drill-in 只读了一次盘
    assert (overview["avg_ux"], overview["ux_n"]) == (8.0, 1)
    assert sides == [{"side": "main", "uses": 1, "avg_ux": 8.0}]
    assert detail["total_triggers"] == 1
