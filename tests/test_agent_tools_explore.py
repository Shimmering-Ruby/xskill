"""SkillEditAgent 探索工具（#91）：读根扩展 / 敏感文件 denylist / grep_files /
skill_read 文件树。"""
from __future__ import annotations

from pathlib import Path

from xskill.agents import agent_tools


def _setup_home(tmp_path, monkeypatch):
    xskill_home = tmp_path / ".xskill"
    xskill_home.mkdir()
    monkeypatch.setattr("xskill.config.XSKILL_HOME", xskill_home)
    skill_dir = xskill_home / "skill"
    skill_dir.mkdir()
    agent_tools.init_atom_task_tool_context(
        skill_dir=skill_dir, atom_store=None, default_traj_root=tmp_path,
    )
    agent_tools.init_skill_authoring_tool_context(
        skill_dir, skill_dir, {"skill_opt": {"enabled": False}},
    )
    return xskill_home, skill_dir


class TestReadRoots:
    def test_read_file_allows_xskill_home(self, tmp_path, monkeypatch):
        xskill_home, _skill_dir = _setup_home(tmp_path, monkeypatch)
        trajectory_file = xskill_home / "cc_sessions" / "traj_a.md"
        trajectory_file.parent.mkdir()
        trajectory_file.write_text("trajectory evidence line\n", encoding="utf-8")

        out = agent_tools.read_file.entrypoint(str(trajectory_file))

        assert "trajectory evidence line" in out

    def test_read_file_denies_outside_roots(self, tmp_path, monkeypatch):
        _setup_home(tmp_path, monkeypatch)
        outside_file = tmp_path / "outside.txt"
        outside_file.write_text("nope\n", encoding="utf-8")

        out = agent_tools.read_file.entrypoint(str(outside_file))

        assert out.startswith("error: outside allowed read roots")

    def test_read_file_denies_sensitive_files(self, tmp_path, monkeypatch):
        xskill_home, _skill_dir = _setup_home(tmp_path, monkeypatch)
        for sensitive_name in (
            "config.yaml", "team_client.json", "team_server.json",
            "my_api_key.txt", "join-token.log",
        ):
            sensitive_file = xskill_home / sensitive_name
            sensitive_file.write_text("s3cret\n", encoding="utf-8")
            out = agent_tools.read_file.entrypoint(str(sensitive_file))
            assert out.startswith("error: sensitive file"), sensitive_name
            assert "s3cret" not in out

    def test_read_file_allows_benign_names_with_substring(self, tmp_path, monkeypatch):
        xskill_home, _skill_dir = _setup_home(tmp_path, monkeypatch)
        benign_file = xskill_home / "monkey_notes.md"
        benign_file.write_text("monkey business\n", encoding="utf-8")

        out = agent_tools.read_file.entrypoint(str(benign_file))

        assert "monkey business" in out

    def test_list_files_allows_xskill_home(self, tmp_path, monkeypatch):
        xskill_home, _skill_dir = _setup_home(tmp_path, monkeypatch)
        logs_dir = xskill_home / "logs"
        logs_dir.mkdir()
        (logs_dir / "watcher.log").write_text("log line\n", encoding="utf-8")

        listing = agent_tools.list_files.entrypoint(str(logs_dir))

        assert "watcher.log" in listing

    def test_list_files_denies_outside_roots(self, tmp_path, monkeypatch):
        _setup_home(tmp_path, monkeypatch)

        listing = agent_tools.list_files.entrypoint("/etc")

        assert listing.startswith("error: list_files restricted")


class TestGrepFiles:
    def _seed_corpus(self, xskill_home):
        corpus_dir = xskill_home / "cc_sessions"
        corpus_dir.mkdir(exist_ok=True)
        (corpus_dir / "traj_a.md").write_text(
            "line one\nDOCKER_RESTART needed here\n", encoding="utf-8")
        (corpus_dir / "traj_b.md").write_text("nothing here\n", encoding="utf-8")
        (corpus_dir / "api_key.txt").write_text(
            "DOCKER_RESTART secret ctx\n", encoding="utf-8")
        return corpus_dir

    def test_finds_matches_with_paths_and_line_numbers(self, tmp_path, monkeypatch):
        xskill_home, _skill_dir = _setup_home(tmp_path, monkeypatch)
        corpus_dir = self._seed_corpus(xskill_home)

        out = agent_tools.grep_files.entrypoint(
            "DOCKER_RESTART", path=str(corpus_dir),
        )

        assert out.startswith("engine: ")
        assert f"{corpus_dir}/traj_a.md:2:" in out

    def test_sensitive_files_filtered_from_hits(self, tmp_path, monkeypatch):
        xskill_home, _skill_dir = _setup_home(tmp_path, monkeypatch)
        corpus_dir = self._seed_corpus(xskill_home)

        out = agent_tools.grep_files.entrypoint(
            "DOCKER_RESTART", path=str(corpus_dir),
        )

        assert "api_key.txt" not in out

    def test_denies_path_outside_roots(self, tmp_path, monkeypatch):
        _setup_home(tmp_path, monkeypatch)

        out = agent_tools.grep_files.entrypoint("x", path="/etc")

        assert out.startswith("error: grep_files restricted")

    def test_falls_back_to_grep_when_rg_missing(self, tmp_path, monkeypatch):
        xskill_home, _skill_dir = _setup_home(tmp_path, monkeypatch)
        corpus_dir = self._seed_corpus(xskill_home)
        real_which = agent_tools.shutil.which
        monkeypatch.setattr(
            agent_tools.shutil, "which",
            lambda name: None if name == "rg" else real_which(name),
        )

        out = agent_tools.grep_files.entrypoint(
            "DOCKER_RESTART", path=str(corpus_dir),
        )

        assert "engine: grep" in out
        assert "traj_a.md:2:" in out

    def test_falls_back_to_python_when_no_grep_either(self, tmp_path, monkeypatch):
        xskill_home, _skill_dir = _setup_home(tmp_path, monkeypatch)
        corpus_dir = self._seed_corpus(xskill_home)
        monkeypatch.setattr(agent_tools.shutil, "which", lambda name: None)

        out = agent_tools.grep_files.entrypoint(
            "DOCKER_RESTART", path=str(corpus_dir),
        )

        assert "engine: python" in out
        assert "traj_a.md:2:" in out
        assert "api_key.txt" not in out

    def test_python_fallback_filters_symlinked_sensitive_file(
        self, tmp_path, monkeypatch,
    ):
        xskill_home, _skill_dir = _setup_home(tmp_path, monkeypatch)
        corpus_dir = xskill_home / "cc_sessions"
        corpus_dir.mkdir()
        secret_file = xskill_home / "config.yaml"
        secret_file.write_text("llm_api_key: SECRET_VALUE\n", encoding="utf-8")
        (corpus_dir / "notes.md").symlink_to(secret_file)
        monkeypatch.setattr(agent_tools.shutil, "which", lambda name: None)

        out = agent_tools.grep_files.entrypoint(
            "SECRET_VALUE", path=str(corpus_dir),
        )

        assert "llm_api_key" not in out, "符号链接不得绕过敏感过滤"
        assert "notes.md:1" not in out

    def test_no_matches_reports_engine(self, tmp_path, monkeypatch):
        xskill_home, _skill_dir = _setup_home(tmp_path, monkeypatch)
        corpus_dir = self._seed_corpus(xskill_home)

        out = agent_tools.grep_files.entrypoint(
            "NO_SUCH_TOKEN_ANYWHERE", path=str(corpus_dir),
        )

        assert "(no matches" in out


class TestSkillReadTree:
    def test_returns_body_and_auxiliary_file_tree(self, tmp_path, monkeypatch):
        _xskill_home, skill_dir = _setup_home(tmp_path, monkeypatch)
        skill_path = skill_dir / "my-skill"
        (skill_path / "references").mkdir(parents=True)
        (skill_path / "SKILL.md").write_text(
            "---\nname: my-skill\n---\n# body here\n", encoding="utf-8")
        (skill_path / "references" / "notes.md").write_text(
            "ref note\n", encoding="utf-8")
        (skill_path / "candidates.yml").write_text(
            "candidates: []\n", encoding="utf-8")

        out = agent_tools.skill_read.entrypoint("my-skill")

        assert f"skill_dir: {skill_path.resolve()}" in out
        assert "body here" in out
        assert "references/notes.md" in out
        assert "candidates.yml" in out and "list_candidates" in out
        assert "read_file" in out

    def test_git_internals_excluded_from_tree(self, tmp_path, monkeypatch):
        _xskill_home, skill_dir = _setup_home(tmp_path, monkeypatch)
        skill_path = skill_dir / "git-skill"
        (skill_path / ".git" / "objects").mkdir(parents=True)
        (skill_path / ".git" / "HEAD").write_text("ref: x\n", encoding="utf-8")
        (skill_path / "SKILL.md").write_text(
            "---\nname: git-skill\n---\n# g\n", encoding="utf-8")

        out = agent_tools.skill_read.entrypoint("git-skill")

        assert ".git" not in out.split("skill_dir:")[1]

    def test_missing_skill_md_keeps_placeholder_but_lists_files(
        self, tmp_path, monkeypatch,
    ):
        _xskill_home, skill_dir = _setup_home(tmp_path, monkeypatch)
        skill_path = skill_dir / "baby-skill"
        skill_path.mkdir()
        (skill_path / "candidates.yml").write_text(
            "candidates: []\n", encoding="utf-8")

        out = agent_tools.skill_read.entrypoint("baby-skill")

        assert "no SKILL.md" in out
        assert "candidates.yml" in out
