"""存量 copy 安装基线的安全审计与修复回归。"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from xskill import cli
from xskill.ecosystems.install_ledger import get_default_ledger
from xskill.ecosystems.installation import (
    CopyBaselineRepairStatus,
    read_install_metadata,
    repair_copy_install_baseline,
    write_install_metadata,
)


def _installed_copy(tmp_path: Path, name: str = "demo") -> tuple[Path, Path]:
    source = tmp_path / "source" / name
    dest = tmp_path / "ecosystem" / name
    source.mkdir(parents=True)
    dest.mkdir(parents=True)
    content = b"---\r\nname: demo\r\ndescription: test\r\n---\r\nbody\r\n"
    (source / "SKILL.md").write_bytes(content)
    (dest / "SKILL.md").write_bytes(content)
    write_install_metadata(dest, source, "copy")
    return source, dest


def _replace_with_blob_lf_baseline(dest: Path) -> dict[str, str]:
    legacy = {
        "SKILL.md": hashlib.sha256(
            b"---\nname: demo\ndescription: test\n---\nbody\n",
        ).hexdigest(),
    }
    identity = hashlib.sha256(json.dumps(
        legacy,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")).hexdigest()
    assert get_default_ledger().update_copy_baseline(
        dest,
        file_fingerprints=legacy,
        baseline_identity=identity,
    )
    return legacy


def test_repair_copy_baseline_heals_matching_legacy_bytes(tmp_path):
    source, dest = _installed_copy(tmp_path)
    legacy = _replace_with_blob_lf_baseline(dest)
    before = read_install_metadata(dest)
    assert before is not None

    status = repair_copy_install_baseline(dest)

    assert status == CopyBaselineRepairStatus.REPAIRED
    after = read_install_metadata(dest)
    assert after is not None
    assert after["file_fingerprints"] != legacy
    assert after["file_fingerprints"]["SKILL.md"] == hashlib.sha256(
        (source / "SKILL.md").read_bytes(),
    ).hexdigest()
    assert after["generation"] == before["generation"]
    assert after["installation_id"] == before["installation_id"]


def test_repair_copy_baseline_dry_run_does_not_write(tmp_path):
    _source, dest = _installed_copy(tmp_path)
    legacy = _replace_with_blob_lf_baseline(dest)

    status = repair_copy_install_baseline(dest, apply=False)

    assert status == CopyBaselineRepairStatus.REPAIRABLE
    after = read_install_metadata(dest)
    assert after is not None
    assert after["file_fingerprints"] == legacy


def test_current_copy_baseline_only_scans_dest_once(
    tmp_path, monkeypatch,
):
    from xskill.ecosystems import installation

    source, dest = _installed_copy(tmp_path)
    real_scan = installation._safe_copy_file_fingerprints
    scanned_roots: list[Path] = []

    def count_scan(root, **kwargs):
        scanned_roots.append(Path(root))
        return real_scan(root, **kwargs)

    monkeypatch.setattr(
        installation, "_safe_copy_file_fingerprints", count_scan,
    )

    status = repair_copy_install_baseline(dest)

    assert status == CopyBaselineRepairStatus.CURRENT
    assert scanned_roots == [dest]
    assert source not in scanned_roots


def test_repair_copy_baseline_uses_two_stable_tree_passes(
    tmp_path, monkeypatch,
):
    from xskill.ecosystems import installation

    source, dest = _installed_copy(tmp_path)
    _replace_with_blob_lf_baseline(dest)
    real_scan = installation._safe_copy_file_fingerprints
    scanned_roots: list[Path] = []

    def count_scan(root, **kwargs):
        scanned_roots.append(Path(root))
        return real_scan(root, **kwargs)

    monkeypatch.setattr(
        installation, "_safe_copy_file_fingerprints", count_scan,
    )

    status = repair_copy_install_baseline(dest)

    assert status == CopyBaselineRepairStatus.REPAIRED
    assert scanned_roots.count(dest) == 2
    assert scanned_roots.count(source) == 2


def test_repair_copy_baseline_refuses_pending_dest_edit(tmp_path):
    _source, dest = _installed_copy(tmp_path)
    legacy = _replace_with_blob_lf_baseline(dest)
    (dest / "SKILL.md").write_bytes(b"user edit\r\n")

    status = repair_copy_install_baseline(dest)

    assert status == CopyBaselineRepairStatus.DIVERGED
    after = read_install_metadata(dest)
    assert after is not None
    assert after["file_fingerprints"] == legacy


def test_repair_copy_baseline_refuses_source_advance(tmp_path):
    source, dest = _installed_copy(tmp_path)
    legacy = _replace_with_blob_lf_baseline(dest)
    (source / "SKILL.md").write_bytes(b"source v2\r\n")

    status = repair_copy_install_baseline(dest)

    assert status == CopyBaselineRepairStatus.DIVERGED
    after = read_install_metadata(dest)
    assert after is not None
    assert after["file_fingerprints"] == legacy


def test_repair_copy_baseline_refuses_missing_skill_entrypoint(tmp_path):
    source, dest = _installed_copy(tmp_path)
    legacy = _replace_with_blob_lf_baseline(dest)
    (source / "SKILL.md").unlink()
    (dest / "SKILL.md").unlink()

    status = repair_copy_install_baseline(dest)

    assert status == CopyBaselineRepairStatus.INVALID
    after = read_install_metadata(dest)
    assert after is not None
    assert after["file_fingerprints"] == legacy


def test_repair_copy_baseline_refuses_open_removal(tmp_path):
    _source, dest = _installed_copy(tmp_path)
    legacy = _replace_with_blob_lf_baseline(dest)
    ledger = get_default_ledger()
    assert ledger.begin_removal(dest) is not None

    status = repair_copy_install_baseline(dest)

    assert status == CopyBaselineRepairStatus.CONCURRENT
    after = read_install_metadata(dest)
    assert after is not None
    assert after["file_fingerprints"] == legacy


def test_repair_copy_baseline_rolls_back_dest_race(
    tmp_path, monkeypatch,
):
    from xskill.ecosystems import installation

    _source, dest = _installed_copy(tmp_path)
    legacy = _replace_with_blob_lf_baseline(dest)
    real_scan = installation._safe_copy_file_fingerprints
    full_dest_scans = 0

    def race_scan(root, **kwargs):
        nonlocal full_dest_scans
        if Path(root) == dest and not kwargs:
            full_dest_scans += 1
            if full_dest_scans == 2:
                (dest / "SKILL.md").write_bytes(b"racing edit\r\n")
        return real_scan(root, **kwargs)

    monkeypatch.setattr(
        installation, "_safe_copy_file_fingerprints", race_scan,
    )

    status = repair_copy_install_baseline(dest)

    assert status == CopyBaselineRepairStatus.FAILED
    after = read_install_metadata(dest)
    assert after is not None
    assert after["file_fingerprints"] == legacy


def test_repair_copy_baseline_reports_concurrent_reinstall(
    tmp_path, monkeypatch,
):
    source, dest = _installed_copy(tmp_path)
    _replace_with_blob_lf_baseline(dest)
    ledger = get_default_ledger()
    real_swap = ledger.compare_and_swap_copy_baseline
    swap_calls = 0

    def reinstall_after_swap(*args, **kwargs):
        nonlocal swap_calls
        swapped = real_swap(*args, **kwargs)
        swap_calls += 1
        if swapped and swap_calls == 1:
            write_install_metadata(dest, source, "copy")
        return swapped

    monkeypatch.setattr(
        ledger, "compare_and_swap_copy_baseline", reinstall_after_swap,
    )

    status = repair_copy_install_baseline(dest)

    assert status == CopyBaselineRepairStatus.CONCURRENT
    current = read_install_metadata(dest)
    assert current is not None
    assert current["file_fingerprints"]["SKILL.md"] == hashlib.sha256(
        (dest / "SKILL.md").read_bytes(),
    ).hexdigest()


def test_repair_baselines_cli_reports_safe_dry_run(tmp_path, capsys):
    _source, dest = _installed_copy(tmp_path)
    _replace_with_blob_lf_baseline(dest)
    args = cli.build_parser().parse_args([
        "repair-baselines", "--skill", "demo", "--dry-run", "--json",
    ])

    return_code = cli.cmd_repair_baselines(args)

    payload = json.loads(capsys.readouterr().out)
    assert return_code == 0
    assert payload["dry_run"] is True
    assert payload["counts"]["repairable"] == 1
    assert payload["results"][0]["skill"] == "demo"
    after = read_install_metadata(dest)
    assert after is not None
    assert after["file_fingerprints"]["SKILL.md"] != hashlib.sha256(
        (dest / "SKILL.md").read_bytes(),
    ).hexdigest()


def test_repair_baselines_cli_main_does_not_require_config(
    monkeypatch, capsys,
):
    monkeypatch.setattr(
        sys, "argv", ["xskill", "repair-baselines", "--json"],
    )

    return_code = cli.main()

    payload = json.loads(capsys.readouterr().out)
    assert return_code == 0
    assert payload["results"] == []


def test_active_target_listing_does_not_decode_baseline_json(tmp_path):
    _source, dest = _installed_copy(tmp_path)
    ledger = get_default_ledger()
    conn = ledger._conn()
    try:
        conn.execute(
            "UPDATE installations SET file_fingerprints_json=? "
            "WHERE dest_key=?",
            ("not-json", str(dest)),
        )
        conn.commit()
    finally:
        conn.close()

    targets = ledger.list_active_install_targets(
        mode="copy", skill_name="demo",
    )

    assert targets == [{"dest_key": str(dest), "skill_name": "demo"}]


def test_copy_baseline_cas_rejects_reinstalled_generation(tmp_path):
    source, dest = _installed_copy(tmp_path)
    ledger = get_default_ledger()
    before = read_install_metadata(dest)
    assert before is not None
    write_install_metadata(dest, source, "copy")
    current = read_install_metadata(dest)
    assert current is not None

    updated = ledger.compare_and_swap_copy_baseline(
        dest,
        expected_generation=before["generation"],
        expected_installation_id=before["installation_id"],
        expected_baseline_identity=before["baseline_identity"],
        file_fingerprints=before["file_fingerprints"],
        baseline_identity=before["baseline_identity"],
    )

    assert updated is False
    assert read_install_metadata(dest) == current
