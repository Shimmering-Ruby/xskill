"""skills_catalog 投影表：UPSERT / DELETE / backfill / page。"""
from __future__ import annotations

import pytest

from xskill.skill import catalog_store
from xskill.skill.git import commit_baby_to_main_branch, init_skill_repo_on_baby


@pytest.fixture(autouse=True)
def _isolate_registry(tmp_path, monkeypatch):
    registry = tmp_path / "registry.db"
    monkeypatch.setattr(
        "xskill.config.get_registry_db_path",
        lambda: registry,
    )


def test_init_and_graduate_upsert_native_row(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    init_skill_repo_on_baby(
        str(root / "demo"), name="demo", description="baby desc",
    )
    page = catalog_store.page_skills_catalog(root, limit=10)
    assert page["total"] == 1
    assert page["skills"][0]["state"] == "baby"
    assert "baby desc" in page["skills"][0]["description"]

    assert commit_baby_to_main_branch(str(root / "demo"), "graduate")
    page = catalog_store.page_skills_catalog(root, limit=10)
    assert page["skills"][0]["state"] == "main"


def test_delete_native_removes_row(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    init_skill_repo_on_baby(
        str(root / "gone"), name="gone", description="x",
    )
    catalog_store.ensure_skills_catalog(root)
    catalog_store.delete_native_skill("gone")
    assert catalog_store.list_skills_catalog(root) == []


def test_backfill_replaces_stale_native_and_hub(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    init_skill_repo_on_baby(
        str(root / "keep"), name="keep", description="keep me",
    )
    catalog_store.ensure_skills_catalog(root)
    hub = [{
        "display_name": "hub-skill",
        "source_path": "team/x",
        "skill_id": "hub-skill@1",
        "description": "from hub",
        "use_count": 2,
    }]
    count = catalog_store.backfill_skills_catalog(root, skillhub=hub)
    assert count == 2
    rows = catalog_store.list_skills_catalog(root, skillhub=hub)
    assert [row["name"] for row in rows] == ["keep", "hub-skill"]
    assert rows[1]["use_count"] == 2
