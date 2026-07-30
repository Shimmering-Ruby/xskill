"""InstallLedger：安装账 DB 化与卸装 supersede 根因回归。"""
from __future__ import annotations

import json
from pathlib import Path

from xskill.ecosystems.install_ledger import (
    InstallLedger,
    dest_key,
    get_default_ledger,
    remove_owned_dest,
    reset_default_ledger,
)
from xskill.ecosystems.installation import (
    COPY_INSTALL_MARKER_NAME,
    install_metadata_path,
    read_install_metadata,
    write_install_metadata,
)


def _skill_source(tmp_path: Path, name: str = "demo") -> Path:
    src = tmp_path / "src" / name
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        "---\nname: demo\ndescription: d\n---\nbody\n",
        encoding="utf-8",
    )
    return src


def test_record_install_no_sidecar_in_user_dir(tmp_path):
    src = _skill_source(tmp_path)
    dest = tmp_path / "eco" / "demo"
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text(
        (src / "SKILL.md").read_text(encoding="utf-8"), encoding="utf-8",
    )
    write_install_metadata(dest, src, "copy")
    assert not install_metadata_path(dest).exists()
    assert not (dest / COPY_INSTALL_MARKER_NAME).exists()
    meta = read_install_metadata(dest)
    assert meta is not None
    assert meta["mode"] == "copy"
    assert meta["installation_id"]
    assert "generation" in meta


def test_reinstall_supersedes_pending_removal(tmp_path):
    """根因序列：卸装 pending → 同 dest 重装 → job superseded，generation+1。"""
    ledger = get_default_ledger()
    src = _skill_source(tmp_path)
    dest = tmp_path / "eco" / "demo"
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("x", encoding="utf-8")
    write_install_metadata(dest, src, "copy")
    first = read_install_metadata(dest)
    assert first is not None
    g1 = int(first["generation"])

    job = ledger.begin_removal(dest)
    assert job is not None
    assert job["state"] == "pending"
    assert ledger.get_open_removal(dest) is not None

    # 重装（不先完成卸装）
    write_install_metadata(dest, src, "copy")
    second = read_install_metadata(dest)
    assert second is not None
    assert int(second["generation"]) == g1 + 1
    assert second["installation_id"] != first["installation_id"]
    assert ledger.get_open_removal(dest) is None
    # 旧 job 应为 superseded
    conn = ledger._conn()
    try:
        row = conn.execute(
            "SELECT state FROM removal_jobs WHERE job_id=?",
            (job["job_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["state"] == "superseded"


def test_remove_owned_dest_tombstones(tmp_path):
    src = _skill_source(tmp_path)
    dest = tmp_path / "eco" / "demo"
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("x", encoding="utf-8")
    write_install_metadata(dest, src, "copy")
    assert remove_owned_dest(dest, src) is True
    assert not dest.exists()
    assert read_install_metadata(dest) is None
    row = get_default_ledger().read_install_row(dest)
    assert row is not None
    assert row["status"] == "tombstone"


def test_manifest_churn_no_stale_open_job(tmp_path):
    """清单进出两次：不应留下 pending/deleting job。"""
    ledger = get_default_ledger()
    src = _skill_source(tmp_path)
    dest = tmp_path / "eco" / "demo"
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("x", encoding="utf-8")

    write_install_metadata(dest, src, "copy")
    job = ledger.begin_removal(dest)
    assert job is not None
    # 再次进入清单 = 重装
    write_install_metadata(dest, src, "copy")
    # 再次离开
    assert remove_owned_dest(dest, src) is True
    assert ledger.list_open_removals() == []


def test_migrate_sidecars_imports_and_deletes(tmp_path):
    eco = tmp_path / "skills"
    eco.mkdir()
    dest = eco / "foo"
    dest.mkdir()
    (dest / "SKILL.md").write_text("hi", encoding="utf-8")
    (dest / COPY_INSTALL_MARKER_NAME).write_text(
        json.dumps({
            "schema_version": 1,
            "installation_id": "a" * 32,
            "content_identity": "b" * 64,
            "baseline_identity": "c" * 64,
        }),
        encoding="utf-8",
    )
    sidecar = eco / f".xskill-install-meta-foo.json"
    sidecar.write_text(
        json.dumps({
            "mode": "copy",
            "source": str(tmp_path / "src" / "foo"),
            "source_sha": "",
            "installed_at": 1.0,
            "installation_id": "a" * 32,
            "content_identity": "b" * 64,
            "baseline_identity": "c" * 64,
            "file_fingerprints": {"SKILL.md": "d" * 64},
        }),
        encoding="utf-8",
    )
    txn = eco / (
        ".xskill-install-meta-foo.json.removal-transaction-"
        + ("e" * 24)
    )
    txn.write_text(
        json.dumps({
            "schema_version": 1,
            "transaction_id": "e" * 24,
            "installation_id": "a" * 32,
            "content_identity": "b" * 64,
            "mode": "copy",
            "target_identity": [1, 2, 3],
            "state": "prepared",
        }),
        encoding="utf-8",
    )
    ledger = get_default_ledger()
    stats = ledger.migrate_from_sidecars([eco])
    assert stats["installs_imported"] >= 1
    assert not sidecar.exists()
    assert not txn.exists()
    assert not (dest / COPY_INSTALL_MARKER_NAME).exists()
    meta = ledger.read_install(dest)
    assert meta is not None
    assert meta["installation_id"] == "a" * 32


def test_multi_ecosystem_independent_dest_keys(tmp_path):
    ledger = get_default_ledger()
    src = _skill_source(tmp_path)
    d1 = tmp_path / "claude" / "demo"
    d2 = tmp_path / "agents" / "demo"
    for d in (d1, d2):
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("x", encoding="utf-8")
        write_install_metadata(d, src, "copy")
    assert dest_key(d1) != dest_key(d2)
    assert ledger.read_install(d1) is not None
    assert ledger.read_install(d2) is not None
    assert remove_owned_dest(d1, src) is True
    assert ledger.read_install(d2) is not None


def test_crash_fs_deleted_db_active_reconcile_via_removal(tmp_path):
    """FS 已删但 DB 仍 active：再卸一次应收尾为 tombstone。"""
    src = _skill_source(tmp_path)
    dest = tmp_path / "eco" / "demo"
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("x", encoding="utf-8")
    write_install_metadata(dest, src, "copy")
    # 模拟 FS 已删、DB 未更新
    import shutil
    shutil.rmtree(dest)
    assert remove_owned_dest(dest, src) is True
    row = get_default_ledger().read_install_row(dest)
    assert row["status"] == "tombstone"


def test_remove_owned_dest_refuses_copy_without_ledger_row(tmp_path):
    """copy 无账本行：拒删，目录留给用户。"""
    src = _skill_source(tmp_path)
    dest = tmp_path / "eco" / "orphan"
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("user kept\n", encoding="utf-8")

    assert remove_owned_dest(dest, src) is False
    assert dest.is_dir()
    assert (dest / "SKILL.md").read_text(encoding="utf-8") == "user kept\n"


def test_remove_owned_dest_refuses_when_source_mismatches(tmp_path):
    """账本 source 与传入 source 不符（被其他来源接管）：拒删。"""
    owned = _skill_source(tmp_path, "owned")
    other = _skill_source(tmp_path, "other")
    dest = tmp_path / "eco" / "demo"
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text(
        (other / "SKILL.md").read_text(encoding="utf-8"), encoding="utf-8",
    )
    write_install_metadata(dest, other, "copy")

    assert remove_owned_dest(dest, owned) is False
    assert dest.is_dir()
    assert (dest / "SKILL.md").read_text(encoding="utf-8") == (
        other / "SKILL.md"
    ).read_text(encoding="utf-8")


def test_remove_owned_dest_refuses_out_of_band_content_replacement(tmp_path):
    """同路径带外替换后：指纹不符，拒删用户新内容。"""
    src = _skill_source(tmp_path)
    dest = tmp_path / "eco" / "demo"
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text(
        (src / "SKILL.md").read_text(encoding="utf-8"), encoding="utf-8",
    )
    write_install_metadata(dest, src, "copy")

    old_target = dest.with_name("old-target-kept-for-test")
    dest.replace(old_target)
    dest.mkdir()
    (dest / "SKILL.md").write_text("new user data\n", encoding="utf-8")

    assert remove_owned_dest(dest, src) is False
    assert (dest / "SKILL.md").read_text(
        encoding="utf-8",
    ) == "new user data\n"
    assert old_target.is_dir()


def test_refresh_baseline_after_owned_write_allows_removal(tmp_path):
    """我们自己在 record 后再写 dest，refresh 后应仍能卸装。"""
    from xskill.ecosystems.installation import refresh_copy_install_baseline

    src = _skill_source(tmp_path)
    dest = tmp_path / "eco" / "demo"
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text(
        (src / "SKILL.md").read_text(encoding="utf-8"), encoding="utf-8",
    )
    write_install_metadata(dest, src, "copy")
    before = read_install_metadata(dest)
    assert before is not None
    generation = int(before["generation"])
    installation_id = before["installation_id"]

    (dest / ".xskill-install-meta.json").write_text(
        '{"ecosystem":"openclaw"}\n', encoding="utf-8",
    )
    assert remove_owned_dest(dest, src) is False
    assert dest.is_dir()

    assert refresh_copy_install_baseline(dest) is True
    after = read_install_metadata(dest)
    assert after is not None
    assert int(after["generation"]) == generation
    assert after["installation_id"] == installation_id
    assert ".xskill-install-meta.json" in after["file_fingerprints"]
    assert remove_owned_dest(dest, src) is True
    assert not dest.exists()
