"""import 收尾：账本坏了也要把技能装进 harness。"""
from __future__ import annotations

import json
from pathlib import Path

from xskill.team.client import import_follow


class _FakeResponse:
    def __init__(self, status_code: int = 200, content: bytes = b"bundle"):
        self.status_code = status_code
        self.content = content
        self.text = "ok"


class _FakeHttp:
    def get(self, url, headers=None):
        del url, headers
        return _FakeResponse()


def test_follow_installs_harness_when_history_is_corrupt(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skill"
    dest = skill_dir / "xskill-install-server"
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("# imported\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    history_path = tmp_path / "history.jsonl"
    history_path.write_text(
        "\n".join(
            json.dumps({"skill": f"old-{index}"}) for index in range(5)
        )
        + "\n"
        + json.dumps({
            "skill": "atlas-dependency-analysis",
            "append_sequence": 2,
        })
        + "\n",
        encoding="utf-8",
    )
    installed: list[Path] = []

    monkeypatch.setattr(import_follow, "apply_repo_bundle", lambda *_a, **_k: None)

    def fake_install(repo, *, home_root):
        del home_root
        installed.append(Path(repo))
        return []

    monkeypatch.setattr(
        import_follow, "install_skill_to_ecosystems", fake_install,
    )

    import_follow.follow_imported_skill(
        http=_FakeHttp(),
        headers={},
        skill_dir=skill_dir,
        name="xskill-install-server",
        sha="deadbeef",
        home_root=home,
        history_path=history_path,
    )
    assert installed == [dest]
