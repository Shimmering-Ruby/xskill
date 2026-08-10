"""`xskill search` 元信息与 `xskill download` 安装输出测试。"""
from __future__ import annotations


import io
import json
import os
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from xskill import cli
from xskill.team.client.search_slots import DownloadedSkills, SearchSlots


class _Response:
    def __init__(self, status_code: int, *, json_data: dict | None = None,
                 content: bytes = b"", text: str = "",
                 headers: dict | None = None):
        self.status_code = status_code
        self._json_data = json_data
        self.content = content
        self.text = text
        self.headers = headers or {}

    def json(self) -> dict:
        return self._json_data or {}


class _SearchHttp:
    def __init__(self, results: list[dict]):
        self.results = results
        self.calls: list[str] = []

    def get(self, path: str, **_kwargs) -> _Response:
        self.calls.append(path)
        if "search" in path:
            return _Response(200, json_data={"results": self.results})
        if "/entry/" in path:
            skill_id = path.rsplit("/", 1)[-1]
            result = next(
                result for result in self.results
                if result["skill_id"] == skill_id
            )
            return _Response(200, json_data={"result": result})
        skill_id = path.split("/")[-2]
        name = next(
            result["display_name"]
            for result in self.results
            if result["skill_id"] == skill_id
        )
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr(
                "SKILL.md",
                f"---\nname: {name}\ndescription: test\n---\nbody\n",
            )
        return _Response(200, content=archive.getvalue())


def _result(name: str, *, source: str = "skillhub",
            source_path: str | None = None) -> dict:
    return {
        "skill_id": f"{name}@abcdef",
        "display_name": name,
        "description": "  描述中有\n连续的   空白  ",
        "content_sha": "ab",
        "source_path": source_path or f"agentcenter_hub/skills/{name}",
        "source": source,
        "ux_avg": 8.6,
        "match": {"bm25_rank": 1, "semantic_rank": 2},
    }


def _install_home(monkeypatch, tmp_path: Path, home: Path) -> None:
    monkeypatch.setattr(
        "xskill.team.client.search_slots.DownloadedSkills",
        lambda **_kwargs: DownloadedSkills(
            xskill_home=tmp_path / "xskill-home", home_root=home,
        ),
    )


def test_search_download_and_download_agent_flags_parse():
    parser = cli.build_parser()

    plain = parser.parse_args(["search", "docker"])
    legacy = parser.parse_args(["search", "docker", "--download"])
    download = parser.parse_args([
        "download", "skill@sha",
        "--agent", "claude-code", "--agent", "codex", "-y",
    ])

    assert plain.download is False
    assert legacy.download is True
    assert download.agent == ["claude-code", "codex"]
    assert download.yes is True
    with pytest.raises(SystemExit) as invalid_agent:
        parser.parse_args([
            "download", "skill@sha", "--agent", "mystery", "-y",
        ])
    assert invalid_agent.value.code == 2


def test_search_plain_output_is_compact_and_read_only(capsys):
    result = _result("compact")
    result["path"] = "/private/cache/compact"
    result["installations"] = [{
        "ecosystem": "codex",
        "target": "/private/harness/compact",
        "status": "installed",
    }]
    search_http = _SearchHttp([result])

    return_code = cli.cmd_search_hub(
        SimpleNamespace(
            terms=["compact"], top_k=5, json=False, download=False,
        ),
        http=search_http, headers={},
    )

    output = capsys.readouterr().out
    assert return_code == 0
    assert search_http.calls == ["/api/v1/team/skill_hub/search"]
    assert "[1/1] compact" in output
    assert "ID：compact@abcdef" in output
    assert "关键词排名 #1" in output
    assert "语义排名 #2" in output
    assert "xskill download compact@abcdef" in output
    assert "/private/cache" not in output
    assert "/private/harness" not in output
    assert "Codex" not in output
    assert "已安装到" not in output


def test_search_warns_on_bm25_degradation(capsys):
    class _MetaHttp:
        def get(self, *_args, **_kwargs):
            return _Response(200, json_data={
                "results": [_result("bm25-only")],
                "meta": {"corpus_empty": False, "degraded_to_bm25": True},
            })

    return_code = cli.cmd_search_hub(
        SimpleNamespace(
            terms=["bm25"], top_k=5, json=False, download=False,
        ),
        http=_MetaHttp(), headers={},
    )
    captured = capsys.readouterr()
    assert return_code == 0
    assert "bm25-only" in captured.out
    assert "降级为 BM25" in captured.err


def test_search_warns_on_empty_corpus(capsys):
    class _EmptyHttp:
        def get(self, *_args, **_kwargs):
            return _Response(200, json_data={
                "results": [],
                "meta": {"corpus_empty": True, "degraded_to_bm25": False},
            })

    return_code = cli.cmd_search_hub(
        SimpleNamespace(
            terms=["anything"], top_k=5, json=False, download=False,
        ),
        http=_EmptyHttp(), headers={},
    )
    captured = capsys.readouterr()
    assert return_code == 0
    assert "暂无可搜索" in captured.out
    assert "库为空" in captured.err


def test_search_download_uses_legacy_search_slots(
    tmp_path, monkeypatch, capsys,
):
    result = _result("legacy")
    search_http = _SearchHttp([result])
    captured: dict = {}

    class _Slots:
        def __init__(self, **kwargs):
            captured["init"] = kwargs

        def install(self, metadata, archive, **kwargs):
            captured["metadata"] = metadata
            captured["archive"] = archive
            captured["install"] = kwargs
            return {
                "cache_path": tmp_path / "search_skills" / metadata["skill_id"],
                "installations": (),
            }

    monkeypatch.setattr(
        "xskill.team.client.search_slots.SearchSlots", _Slots,
    )
    monkeypatch.setattr("xskill.config.XSKILL_HOME", tmp_path / "xhome")

    return_code = cli.cmd_search_hub(
        SimpleNamespace(
            terms=["legacy"], top_k=5, json=True, download=True,
        ),
        http=search_http, headers={},
    )

    row = json.loads(capsys.readouterr().out)[0]
    assert return_code == 0
    assert search_http.calls == [
        "/api/v1/team/skill_hub/search",
        f"/api/v1/team/skill/{result['skill_id']}/bundle",
    ]
    assert captured["install"] == {
        "query": "legacy", "return_details": True,
    }
    assert row["cache_path"].endswith(result["skill_id"])
    assert "path" in row and row["installations"] == []


def test_download_repeated_agents_with_yes_is_noninteractive(
    tmp_path, monkeypatch, capsys,
):
    result = _result("selected")
    search_http = _SearchHttp([result])
    captured: dict = {}

    class _Downloads:
        def __init__(self, **kwargs):
            captured["init"] = kwargs

        def install(self, metadata, archive, **kwargs):
            captured["metadata"] = metadata
            captured["archive"] = archive
            captured["install"] = kwargs
            return {
                "path": tmp_path / metadata["skill_id"],
                "installations": (),
            }

    monkeypatch.setattr(
        "xskill.team.client.search_slots.DownloadedSkills", _Downloads,
    )
    monkeypatch.setattr(
        cli, "_detected_download_agents",
        lambda: pytest.fail("explicit --agent must not run detection"),
    )
    monkeypatch.setattr(
        cli, "_prompt_download_agents",
        lambda *_args: pytest.fail("-y must not prompt"),
    )

    return_code = cli.cmd_download(
        SimpleNamespace(
            skill_id=result["skill_id"], json=True,
            agent=["claude-code", "codex", "claude_code"], yes=True,
        ),
        http=search_http, headers={},
    )

    output = json.loads(capsys.readouterr().out)
    assert return_code == 0
    assert captured["install"]["ecosystems"] == ["claude_code", "codex"]
    assert captured["install"]["return_details"] is True
    assert output["path"].endswith(result["skill_id"])


def test_download_yes_without_agents_selects_detected(
    tmp_path, monkeypatch, capsys,
):
    result = _result("auto-selected")
    captured: dict = {}

    class _Downloads:
        def __init__(self, **_kwargs):
            pass

        def install(self, metadata, _archive, **kwargs):
            captured.update(kwargs)
            return {
                "path": tmp_path / metadata["skill_id"],
                "installations": (),
            }

    monkeypatch.setattr(
        "xskill.team.client.search_slots.DownloadedSkills", _Downloads,
    )
    monkeypatch.setattr(
        cli, "_detected_download_agents", lambda: ["nga3", "cursor"],
    )

    return_code = cli.cmd_download(
        SimpleNamespace(
            skill_id=result["skill_id"], json=True, agent=[], yes=True,
        ),
        http=_SearchHttp([result]), headers={},
    )

    assert return_code == 0
    assert captured["ecosystems"] == ["nga3", "cursor"]
    json.loads(capsys.readouterr().out)


def test_download_yes_without_detected_agents_still_persists(
    tmp_path, monkeypatch, capsys,
):
    result = _result("download-only")
    captured: dict = {}

    class _Downloads:
        def __init__(self, **_kwargs):
            pass

        def install(self, metadata, _archive, **kwargs):
            captured.update(kwargs)
            return {
                "path": tmp_path / metadata["skill_id"],
                "installations": (),
            }

    monkeypatch.setattr(
        "xskill.team.client.search_slots.DownloadedSkills", _Downloads,
    )
    monkeypatch.setattr(cli, "_detected_download_agents", lambda: [])

    return_code = cli.cmd_download(
        SimpleNamespace(
            skill_id=result["skill_id"], json=True, agent=[], yes=True,
        ),
        http=_SearchHttp([result]), headers={},
    )

    captured_output = capsys.readouterr()
    assert return_code == 0
    assert captured["ecosystems"] == []
    assert "仅持久下载" in captured_output.err
    json.loads(captured_output.out)


def test_download_interactive_multiselects_detected_agents(
    tmp_path, monkeypatch, capsys,
):
    result = _result("interactive")
    captured: dict = {}

    class _Downloads:
        def __init__(self, **_kwargs):
            pass

        def install(self, metadata, _archive, **kwargs):
            captured.update(kwargs)
            return {
                "path": tmp_path / metadata["skill_id"],
                "installations": (),
            }

    monkeypatch.setattr(
        "xskill.team.client.search_slots.DownloadedSkills", _Downloads,
    )
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda: True)
    monkeypatch.setattr(
        cli, "_detected_download_agents",
        lambda: ["claude_code", "codex", "cursor"],
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("1,3\n"))

    return_code = cli.cmd_download(
        SimpleNamespace(
            skill_id=result["skill_id"], json=True, agent=[], yes=False,
        ),
        http=_SearchHttp([result]), headers={},
    )

    captured_output = capsys.readouterr()
    assert return_code == 0
    assert captured["ecosystems"] == ["claude_code", "cursor"]
    assert "Claude Code" in captured_output.err
    assert "Cursor" in captured_output.err
    json.loads(captured_output.out)


def test_download_without_yes_rejects_non_tty_before_network(
    monkeypatch, capsys,
):
    result = _result("non-tty")
    search_http = _SearchHttp([result])
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda: False)

    return_code = cli.cmd_download(
        SimpleNamespace(
            skill_id=result["skill_id"], json=False,
            agent=["codex"], yes=False,
        ),
        http=search_http, headers={},
    )

    assert return_code == 2
    assert search_http.calls == []
    assert "--agent" in capsys.readouterr().err


def test_download_cancel_stops_before_network(monkeypatch, capsys):
    result = _result("cancel")
    search_http = _SearchHttp([result])
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda: True)
    monkeypatch.setattr(
        cli, "_detected_download_agents", lambda: ["codex", "cursor"],
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("q\n"))

    return_code = cli.cmd_download(
        SimpleNamespace(
            skill_id=result["skill_id"], json=False, agent=[], yes=False,
        ),
        http=search_http, headers={},
    )

    assert return_code == 0
    assert search_http.calls == []
    assert "已取消" in capsys.readouterr().err


def test_only_detected_ngagent_and_nga3_are_printed(
    tmp_path, monkeypatch, capsys,
):
    home = tmp_path / "home"
    (home / ".cac" / "projects").mkdir(parents=True)
    ngagent_db = home / ".local" / "share" / "opencode" / "db" / "ngagent.db"
    ngagent_db.parent.mkdir(parents=True)
    ngagent_db.touch()
    _install_home(monkeypatch, tmp_path, home)
    results = [
        _result("repo-skill", source="repo", source_path="repo/repo-skill"),
        _result(
            "upload-skill", source="上传者:alice",
            source_path="user_skill_hub/alice/upload-skill",
        ),
    ]

    search_http = _SearchHttp(results)
    return_codes = [
        cli.cmd_download(
            SimpleNamespace(
                skill_id=result["skill_id"], json=False,
                agent=["nga3", "ngagent"], yes=True,
            ),
            http=search_http, headers={},
        )
        for result in results
    ]

    output = capsys.readouterr().out
    assert return_codes == [0, 0]
    assert "CodeAgent3 / NGA3" in output
    assert "NGAgent" in output
    assert str(home / ".cac" / "skills" / "repo-skill@abcdef") in output
    assert str(
        home / ".config" / "opencode" / "skills" / "repo-skill@abcdef"
    ) in output
    assert "Claude Code" not in output
    assert "Codex" not in output
    assert "OpenCode" not in output
    assert "OpenClaw" not in output
    assert not (home / ".agents").exists()
    assert not (home / ".claude").exists()
    assert "XSkill 自蒸馏生成" in output
    assert "上传者:alice（用户上传）" in output
    assert "描述中有 连续的 空白" in output
    assert output.count("完成：1 个 skill") == 2


def test_shared_target_keeps_each_harness_record(tmp_path):
    home = tmp_path / "home"
    (home / ".codex" / "sessions").mkdir(parents=True)
    opencode_db = home / ".local" / "share" / "opencode" / "opencode.db"
    opencode_db.parent.mkdir(parents=True)
    opencode_db.touch()
    (home / ".openclaw" / "agents").mkdir(parents=True)
    slots = SearchSlots(
        xskill_home=tmp_path / "xskill-home", home_root=home,
    )
    result = _result("shared")
    archive = _SearchHttp([result]).get(
        f"/api/v1/team/skill/{result['skill_id']}/bundle"
    ).content

    details = slots.install(
        result, archive, query="shared", return_details=True,
    )

    records = list(details["installations"])
    shared_target = str(home / ".agents" / "skills" / result["skill_id"])
    shared_records = [
        record for record in records if record["target"] == shared_target
    ]
    assert {record["ecosystem"] for record in shared_records} == {
        "codex", "opencode", "openclaw",
    }
    assert all(record["status"] == "installed" for record in shared_records)
    assert all(record["mode"] == "copy" for record in shared_records)


def test_explicit_ecosystems_bypass_detection_and_use_fixed_order(
    tmp_path, monkeypatch,
):
    from xskill.team.client.daemon import install_skill_to_ecosystems

    repo_dir = tmp_path / "explicit"
    repo_dir.mkdir()
    (repo_dir / "SKILL.md").write_text(
        "---\nname: explicit\ndescription: test\n---\nbody\n",
        encoding="utf-8",
    )
    calls: list[str] = []
    monkeypatch.setattr(
        "xskill.ecosystems.detect_known_ecosystems",
        lambda **_kwargs: pytest.fail(
            "explicit ecosystems must bypass detection"
        ),
    )
    monkeypatch.setattr(
        "xskill.ecosystems.install_to_codex",
        lambda *_args, **_kwargs: calls.append("codex"),
    )
    monkeypatch.setattr(
        "xskill.ecosystems.install_to_openclaw",
        lambda *_args, **_kwargs: calls.append("openclaw"),
    )

    records = install_skill_to_ecosystems(
        repo_dir, home_root=tmp_path / "home",
        ecosystems=["openclaw", "codex"],
    )

    assert calls == ["codex", "openclaw"]
    assert [record["ecosystem"] for record in records] == [
        "codex", "openclaw",
    ]


def test_detected_install_failure_is_visible(
    tmp_path, monkeypatch, capsys,
):
    home = tmp_path / "home"
    (home / ".cac" / "projects").mkdir(parents=True)
    ngagent_db = home / ".local" / "share" / "opencode" / "db" / "ngagent.db"
    ngagent_db.parent.mkdir(parents=True)
    ngagent_db.touch()
    _install_home(monkeypatch, tmp_path, home)

    def fail_ngagent(*_args, **_kwargs):
        raise PermissionError("target is read-only")

    monkeypatch.setattr(
        "xskill.ecosystems.install_to_ngagent", fail_ngagent,
    )
    result = _result("partial")

    return_code = cli.cmd_download(
        SimpleNamespace(
            skill_id=result["skill_id"], json=False,
            agent=["nga3", "ngagent"], yes=True,
        ),
        http=_SearchHttp([result]), headers={},
    )

    output = capsys.readouterr().out
    assert return_code == 0
    assert "[失败] NGAgent 安装失败" in output
    assert "目标目录不可写，请检查目录权限" in output
    assert "target is read-only" not in output
    assert "[成功] CodeAgent3 / NGA3" in output


def test_search_json_is_metadata_only(
    tmp_path, monkeypatch, capsys,
):
    home = tmp_path / "home"
    _install_home(monkeypatch, tmp_path, home)
    result = _result("json-skill")

    return_code = cli.cmd_search_hub(
        SimpleNamespace(terms=["json"], top_k=5, json=True),
        http=_SearchHttp([result]), headers={},
    )

    rows = json.loads(capsys.readouterr().out)
    assert return_code == 0
    assert "path" not in rows[0]
    assert "cache_path" not in rows[0]
    assert "installations" not in rows[0]
    assert rows[0]["match"] == result["match"]
    assert rows[0]["source"] == result["source"]


def test_human_output_is_encodable_by_real_cp936_stream(monkeypatch):
    output_bytes = io.BytesIO()
    cp936_stdout = io.TextIOWrapper(
        output_bytes, encoding="cp936", errors="strict",
    )
    monkeypatch.setattr(sys, "stdout", cp936_stdout)
    row = _result("windows-\N{GRINNING FACE}")
    row["name"] = row["display_name"]
    row["description"] = "包含 emoji \N{ROCKET} 的描述"
    row["source_path"] = "user_skill_hub/\N{CAT FACE}/windows-output"
    row["installations"] = [{
        "ecosystem": "ngagent",
        "target": (
            "C:\\Users\\\N{GRINNING FACE}\\.config\\opencode\\skills"
            "\\windows-output"
        ),
        "status": "installed",
        "mode": "copy",
    }, {
        "ecosystem": "nga3",
        "target": r"C:\Users\tester\.cac\skills\windows-output",
        "status": "failed",
        "error_code": "TARGET_PERMISSION_DENIED",
        "error": "目标目录不可写，请检查目录权限",
    }]

    cli._render_search_results([row], "openqa \N{FIRE}")
    cp936_stdout.flush()

    rendered = output_bytes.getvalue().decode("cp936")
    assert "[成功] NGAgent [copy]" in rendered
    assert "[失败] CodeAgent3 / NGA3 安装失败" in rendered
    assert "\\U0001f600" in rendered
    assert "\\U0001f680" in rendered
    assert "\\U0001f431" in rendered
    assert "\\U0001f525" in rendered


def test_install_exception_secret_never_enters_output_or_ledger(
    tmp_path, monkeypatch, capsys, caplog,
):
    home = tmp_path / "home"
    ngagent_db = home / ".local" / "share" / "opencode" / "db" / "ngagent.db"
    ngagent_db.parent.mkdir(parents=True)
    ngagent_db.touch()
    _install_home(monkeypatch, tmp_path, home)

    def fail_with_secret(*_args, **_kwargs):
        raise RuntimeError(
            "Authorization: Bearer very-secret-token /root/private/path"
        )

    monkeypatch.setattr(
        "xskill.ecosystems.install_to_ngagent", fail_with_secret,
    )
    caplog.set_level("WARNING", logger="xskill.team.client")
    result = _result("safe-error")

    return_code = cli.cmd_download(
        SimpleNamespace(
            skill_id=result["skill_id"], json=False,
            agent=["ngagent"], yes=True,
        ),
        http=_SearchHttp([result]), headers={},
    )

    output = capsys.readouterr().out
    ledger_text = (
        tmp_path / "xskill-home" / "downloads.json"
    ).read_text(encoding="utf-8")
    assert return_code == 0
    assert "Authorization" not in output
    assert "very-secret-token" not in output
    assert "/root/private/path" not in output
    assert "Authorization" not in ledger_text
    assert "very-secret-token" not in ledger_text
    assert "/root/private/path" not in ledger_text
    assert "Authorization" not in caplog.text
    assert "very-secret-token" not in caplog.text
    assert "/root/private/path" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    record = json.loads(ledger_text)[0]["installations"][0]
    assert record["error_code"] == "INSTALLER_ERROR"
    assert record["error"] == "安装器执行失败，请查看本机 xskill 日志"


def test_stale_copy_is_not_current_when_new_version_install_fails(
    tmp_path, monkeypatch,
):
    home = tmp_path / "home"
    ngagent_db = home / ".local" / "share" / "opencode" / "db" / "ngagent.db"
    ngagent_db.parent.mkdir(parents=True)
    ngagent_db.touch()
    slots = SearchSlots(
        xskill_home=tmp_path / "xskill-home", home_root=home,
    )
    first_result = _result("versioned")
    first_archive = io.BytesIO()
    with zipfile.ZipFile(first_archive, "w") as zip_file:
        zip_file.writestr(
            "SKILL.md",
            "---\nname: versioned\ndescription: test\n---\nversion one\n",
        )
    slots.install(
        first_result, first_archive.getvalue(), query="versioned",
    )
    target = (
        home / ".config" / "opencode" / "skills"
        / first_result["skill_id"]
    )

    def fail_new_copy(*_args, **_kwargs):
        raise PermissionError("Authorization: Bearer update-secret")

    monkeypatch.setattr(
        "xskill.ecosystems.install_to_ngagent", fail_new_copy,
    )
    second_result = dict(first_result)
    second_result["content_sha"] = "cd"
    second_archive = io.BytesIO()
    with zipfile.ZipFile(second_archive, "w") as zip_file:
        zip_file.writestr(
            "SKILL.md",
            "---\nname: versioned\ndescription: test\n---\nversion two\n",
        )

    details = slots.install(
        second_result, second_archive.getvalue(),
        query="versioned", return_details=True,
    )

    record = details["installations"][0]
    assert "version one" in (
        target / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "version two" in (
        Path(details["cache_path"]) / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert record["status"] == "failed"
    assert record["error_code"] == "TARGET_PERMISSION_DENIED"
    serialized = json.dumps(record, ensure_ascii=False)
    assert "update-secret" not in serialized
    assert "Authorization" not in serialized


def test_stale_copy_auxiliary_file_is_not_current_when_reinstall_fails(
    tmp_path, monkeypatch,
):
    home = tmp_path / "home"
    ngagent_db = home / ".local" / "share" / "opencode" / "db" / "ngagent.db"
    ngagent_db.parent.mkdir(parents=True)
    ngagent_db.touch()
    slots = SearchSlots(
        xskill_home=tmp_path / "xskill-home", home_root=home,
    )
    result = _result("aux-versioned")
    first_archive = io.BytesIO()
    with zipfile.ZipFile(first_archive, "w") as zip_file:
        zip_file.writestr(
            "SKILL.md",
            "---\nname: aux-versioned\ndescription: test\n---\nsame body\n",
        )
        zip_file.writestr("references/note.md", "version one\n")
    slots.install(result, first_archive.getvalue(), query="aux")
    target = (
        home / ".config" / "opencode" / "skills"
        / result["skill_id"]
    )

    def fail_new_copy(*_args, **_kwargs):
        raise PermissionError("Authorization: Bearer auxiliary-secret")

    monkeypatch.setattr(
        "xskill.ecosystems.install_to_ngagent", fail_new_copy,
    )
    second_archive = io.BytesIO()
    with zipfile.ZipFile(second_archive, "w") as zip_file:
        zip_file.writestr(
            "SKILL.md",
            "---\nname: aux-versioned\ndescription: test\n---\nsame body\n",
        )
        zip_file.writestr("references/note.md", "version two\n")

    details = slots.install(
        result, second_archive.getvalue(),
        query="aux", return_details=True,
    )

    record = details["installations"][0]
    assert (target / "references" / "note.md").read_text(
        encoding="utf-8",
    ) == "version one\n"
    assert (
        Path(details["cache_path"]) / "references" / "note.md"
    ).read_text(encoding="utf-8") == "version two\n"
    assert record["status"] == "failed"
    assert record["error_code"] == "TARGET_PERMISSION_DENIED"
    assert "auxiliary-secret" not in json.dumps(
        record, ensure_ascii=False,
    )


def test_invalid_git_head_is_reported_as_safe_install_failure(
    tmp_path, caplog,
):
    from xskill.skill.git import init_skill_repo_on_baby
    from xskill.team.client.daemon import install_skill_to_ecosystems

    repo_dir = tmp_path / "private" / "damaged-skill"
    init_skill_repo_on_baby(
        str(repo_dir), "damaged-skill", "damaged ref test",
    )
    (repo_dir / ".git" / "HEAD").write_text(
        "c" * 40, encoding="ascii",
    )
    home = tmp_path / "home"
    (home / ".openclaw" / "agents").mkdir(parents=True)

    with caplog.at_level("WARNING"):
        records = install_skill_to_ecosystems(repo_dir, home_root=home)

    assert len(records) == 1
    assert records[0]["status"] == "failed"
    assert records[0]["error_code"] == "GIT_HEAD_INVALID"
    assert records[0]["error"] == (
        "Git HEAD 校验失败，请检查 skill 仓库完整性"
    )
    serialized = json.dumps(records[0], ensure_ascii=False)
    assert "cccccccc" not in serialized
    assert all(record.exc_info is None for record in caplog.records)


def test_retry_repairs_missing_metadata_on_correct_link(
    tmp_path, monkeypatch,
):
    from xskill.ecosystems import _fallback as install_fallback
    from xskill.ecosystems.installation import (
        InstallationMetadataError,
        read_install_metadata,
    )
    from xskill.team.client.daemon import install_skill_to_ecosystems

    repo_dir = tmp_path / "metadata-retry"
    repo_dir.mkdir()
    (repo_dir / "SKILL.md").write_text(
        "---\nname: metadata-retry\ndescription: test\n---\nbody\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    (home / ".codex" / "sessions").mkdir(parents=True)
    original_write_metadata = install_fallback._write_install_meta
    write_attempts = 0

    def fail_first_metadata_write(dest, source, mode):
        nonlocal write_attempts
        write_attempts += 1
        if write_attempts == 1:
            raise InstallationMetadataError(
                "INSTALL_METADATA_WRITE_FAILED",
            )
        return original_write_metadata(dest, source, mode)

    monkeypatch.setattr(
        install_fallback,
        "_write_install_meta",
        fail_first_metadata_write,
    )

    first_records = install_skill_to_ecosystems(
        repo_dir, home_root=home,
    )
    target = home / ".agents" / "skills" / repo_dir.name
    assert first_records[0]["status"] == "failed"
    assert target.is_symlink()
    assert read_install_metadata(target) is None

    second_records = install_skill_to_ecosystems(
        repo_dir, home_root=home,
    )

    assert second_records[0]["status"] == "installed"
    metadata = read_install_metadata(target)
    assert metadata is not None
    assert metadata["mode"] == "symlink"
    assert metadata["source"] == str(repo_dir.resolve())


def test_retry_repairs_partial_metadata_on_correct_link(
    tmp_path, monkeypatch,
):
    from xskill.ecosystems import _fallback as install_fallback
    from xskill.ecosystems.installation import (
        InstallationMetadataError,
        install_metadata_path,
        read_install_metadata,
    )
    from xskill.team.client.daemon import install_skill_to_ecosystems

    repo_dir = tmp_path / "partial-metadata-retry"
    repo_dir.mkdir()
    (repo_dir / "SKILL.md").write_text(
        "---\nname: partial-metadata-retry\ndescription: test\n---\nbody\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    (home / ".codex" / "sessions").mkdir(parents=True)
    original_write_metadata = install_fallback._write_install_meta
    write_attempts = 0

    def leave_partial_metadata(dest, source, mode):
        nonlocal write_attempts
        write_attempts += 1
        if write_attempts == 1:
            install_metadata_path(dest).write_text(
                '{"mode":"symlink", broken',
                encoding="utf-8",
            )
            raise InstallationMetadataError(
                "INSTALL_METADATA_WRITE_FAILED",
            )
        return original_write_metadata(dest, source, mode)

    monkeypatch.setattr(
        install_fallback,
        "_write_install_meta",
        leave_partial_metadata,
    )
    first_records = install_skill_to_ecosystems(
        repo_dir, home_root=home,
    )
    assert first_records[0]["status"] == "failed"

    second_records = install_skill_to_ecosystems(
        repo_dir, home_root=home,
    )

    assert second_records[0]["status"] == "installed"
    target = home / ".agents" / "skills" / repo_dir.name
    metadata = read_install_metadata(target)
    assert metadata is not None
    assert metadata["source"] == str(repo_dir.resolve())


def test_shared_correct_link_repairs_metadata_for_all_harnesses(
    tmp_path, monkeypatch,
):
    from xskill.ecosystems import _fallback as install_fallback
    from xskill.ecosystems.installation import (
        InstallationMetadataError,
        read_install_metadata,
    )
    from xskill.team.client.daemon import install_skill_to_ecosystems

    repo_dir = tmp_path / "shared-metadata-repair"
    repo_dir.mkdir()
    (repo_dir / "SKILL.md").write_text(
        "---\nname: shared-metadata-repair\ndescription: test\n---\nbody\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    (home / ".codex" / "sessions").mkdir(parents=True)
    opencode_db = (
        home / ".local" / "share" / "opencode" / "opencode.db"
    )
    opencode_db.parent.mkdir(parents=True)
    opencode_db.touch()
    original_write_metadata = install_fallback._write_install_meta
    write_attempts = 0

    def fail_first_metadata_write(dest, source, mode):
        nonlocal write_attempts
        write_attempts += 1
        if write_attempts == 1:
            raise InstallationMetadataError(
                "INSTALL_METADATA_WRITE_FAILED",
            )
        return original_write_metadata(dest, source, mode)

    monkeypatch.setattr(
        install_fallback,
        "_write_install_meta",
        fail_first_metadata_write,
    )

    records = install_skill_to_ecosystems(repo_dir, home_root=home)

    assert {record["ecosystem"] for record in records} == {
        "codex", "opencode",
    }
    assert {record["status"] for record in records} == {"installed"}
    target = home / ".agents" / "skills" / repo_dir.name
    metadata = read_install_metadata(target)
    assert metadata is not None
    assert metadata["source"] == str(repo_dir.resolve())


def test_recent_auxiliary_edit_remains_safe_install_failure(
    tmp_path,
):
    from xskill.ecosystems.installation import read_install_metadata
    from xskill.skill.git import init_skill_repo_on_baby
    from xskill.team.client.daemon import install_skill_to_ecosystems

    repo_dir = tmp_path / "recent-user-edit"
    init_skill_repo_on_baby(
        str(repo_dir), "recent-user-edit", "recent edit test",
    )
    home = tmp_path / "home"
    (home / ".openclaw" / "agents").mkdir(parents=True)
    first_records = install_skill_to_ecosystems(
        repo_dir, home_root=home,
    )
    assert first_records[0]["status"] == "installed"
    target = home / ".agents" / "skills" / repo_dir.name
    user_file = target / "references" / "user-note.md"
    user_file.parent.mkdir(exist_ok=True)
    user_file.write_text("USER EDIT IN PROGRESS\n", encoding="utf-8")
    metadata = read_install_metadata(target)
    assert metadata is not None
    recent_mtime = metadata["installed_at"] + 2.0
    user_file.touch()
    import os
    os.utime(user_file, (recent_mtime, recent_mtime))

    second_records = install_skill_to_ecosystems(
        repo_dir, home_root=home,
    )

    assert second_records[0]["status"] == "failed"
    assert second_records[0]["error_code"] == "USER_EDIT_IN_PROGRESS"
    assert user_file.read_text(
        encoding="utf-8",
    ) == "USER EDIT IN PROGRESS\n"


def test_damaged_metadata_is_not_reported_installed(
    tmp_path, monkeypatch, caplog,
):
    from xskill.ecosystems.installation import install_metadata_path
    from xskill.team.client.daemon import install_skill_to_ecosystems

    repo_dir = tmp_path / "damaged-metadata"
    repo_dir.mkdir()
    (repo_dir / "SKILL.md").write_text(
        "---\nname: damaged-metadata\ndescription: test\n---\nbody\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    ngagent_db = home / ".local" / "share" / "opencode" / "db" / "ngagent.db"
    ngagent_db.parent.mkdir(parents=True)
    ngagent_db.touch()
    target = home / ".config" / "opencode" / "skills" / repo_dir.name
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("stale\n", encoding="utf-8")
    install_metadata_path(target).write_text(
        '{"secret":"metadata-secret", broken',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "xskill.ecosystems.install_to_ngagent",
        lambda *_args, **_kwargs: target / "SKILL.md",
    )

    with caplog.at_level("WARNING"):
        records = install_skill_to_ecosystems(repo_dir, home_root=home)

    assert len(records) == 1
    assert records[0]["status"] == "failed"
    assert records[0]["error_code"] == "INSTALL_METADATA_INVALID"
    assert "metadata-secret" not in caplog.text
    assert str(target) not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_uninstall_skips_damaged_metadata_and_continues_other_targets(
    tmp_path, caplog,
):
    from xskill.ecosystems.installation import (
        install_metadata_path,
        write_install_metadata,
    )
    from xskill.team.client.daemon import uninstall_skill_from_ecosystems

    home = tmp_path / "home"
    source = tmp_path / "source" / "cleanup-skill"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("body\n", encoding="utf-8")
    bad_target = home / ".claude" / "skills" / source.name
    good_target = home / ".config" / "opencode" / "skills" / source.name
    for target in (bad_target, good_target):
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("body\n", encoding="utf-8")
    install_metadata_path(bad_target).write_text(
        '{"secret":"cleanup-secret", broken',
        encoding="utf-8",
    )
    write_install_metadata(good_target, source, "copy")

    with caplog.at_level("WARNING"):
        removed = uninstall_skill_from_ecosystems(
            source.name,
            home_root=home,
            source_dir=source,
        )

    assert good_target in removed
    assert not good_target.exists()
    assert bad_target.exists()
    assert "cleanup-secret" not in caplog.text
    assert str(bad_target) not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_uninstall_symlink_unlinks_only_link_target(tmp_path):
    from xskill.team.client import daemon

    source = tmp_path / "source"
    source.mkdir()
    (source / "SKILL.md").write_text("source\n", encoding="utf-8")
    target = tmp_path / "skills" / "linked"
    target.parent.mkdir()
    target.symlink_to(source, target_is_directory=True)

    assert daemon._remove_owned_install_target(target, source) is True
    assert not target.is_symlink()
    assert (source / "SKILL.md").read_text(encoding="utf-8") == "source\n"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO unsupported")
def test_uninstall_rejects_fifo_target(tmp_path):
    from xskill.team.client import daemon

    target = tmp_path / "skills" / "special"
    target.parent.mkdir()
    os.mkfifo(target)

    assert daemon._remove_owned_install_target(target) is False
    assert target.exists()


def test_runner_stops_absorb_after_reverse_sync_failure(
    tmp_path, monkeypatch,
):
    from xskill.agents import user_edit_absorb_agent as user_absorb
    from xskill.pipeline.runner import DirectoryWatcher

    skill_dir = tmp_path / "skills"
    skill_path = skill_dir / "failed-reverse"
    skill_path.mkdir(parents=True)
    (skill_path / "SKILL.md").write_text("body\n", encoding="utf-8")
    watcher = DirectoryWatcher(
        skill_dir=skill_dir,
        home_root=tmp_path / "home",
    )
    monkeypatch.setattr(
        user_absorb,
        "reverse_sync_openclaw_dest",
        lambda *_args, **_kwargs: user_absorb.ReverseSyncStatus.FAILED,
    )

    def fail_if_detection_runs(*_args, **_kwargs):
        raise AssertionError("FAILED 后不得继续 detect/absorb/install")

    monkeypatch.setattr(
        user_absorb, "detect_user_edits", fail_if_detection_runs,
    )
    monkeypatch.setattr(watcher, "_factory", lambda: object())

    watcher._check_user_edits()


def test_link_modes_do_not_read_install_metadata(monkeypatch):
    from xskill.ecosystems import installation

    class _LinkTarget:
        def __init__(self, is_symlink):
            self._is_symlink = is_symlink

        def is_symlink(self):
            return self._is_symlink

    def fail_metadata_read(_target):
        raise AssertionError("link mode must not read install metadata")

    monkeypatch.setattr(
        installation, "read_install_metadata", fail_metadata_read,
    )
    symlink_target = _LinkTarget(True)
    assert installation.installed_mode(symlink_target) == "symlink"

    junction_target = _LinkTarget(False)

    def is_test_junction(target):
        return target is junction_target

    monkeypatch.setattr(
        installation, "is_link_or_junction",
        is_test_junction,
    )
    assert installation.installed_mode(junction_target) == "junction"


def test_later_shared_target_success_corrects_earlier_attempt_failure(
    tmp_path, monkeypatch,
):
    home = tmp_path / "home"
    (home / ".codex" / "sessions").mkdir(parents=True)
    (home / ".openclaw" / "agents").mkdir(parents=True)
    slots = SearchSlots(
        xskill_home=tmp_path / "xskill-home", home_root=home,
    )
    result = _result("shared-recovery")
    archive = _SearchHttp([result]).get(
        f"/api/v1/team/skill/{result['skill_id']}/bundle"
    ).content

    def fail_codex(*_args, **_kwargs):
        raise PermissionError("Authorization: Bearer secret")

    monkeypatch.setattr("xskill.ecosystems.install_to_codex", fail_codex)

    details = slots.install(
        result, archive, query="shared", return_details=True,
    )

    shared_records = list(details["installations"])
    assert {record["ecosystem"] for record in shared_records} == {
        "codex", "openclaw",
    }
    assert all(record["status"] == "installed" for record in shared_records)
    assert all(record["mode"] == "copy" for record in shared_records)
    assert all("error" not in record for record in shared_records)
    assert all("error_code" not in record for record in shared_records)


def test_trae_partial_install_is_corrected_per_target(
    tmp_path, monkeypatch,
):
    from xskill.ecosystems._fallback import install_dir

    home = tmp_path / "home"
    (home / ".trae-cn").mkdir(parents=True)
    (home / ".trae").mkdir(parents=True)
    slots = SearchSlots(
        xskill_home=tmp_path / "xskill-home", home_root=home,
    )
    result = _result("trae-partial")
    archive = _SearchHttp([result]).get(
        f"/api/v1/team/skill/{result['skill_id']}/bundle"
    ).content

    def install_first_trae_target(skill_path, *, target_root, side):
        assert side == "main"
        first_target = (
            Path(target_root) / ".trae-cn" / "skills" / Path(skill_path).name
        )
        first_target.parent.mkdir(parents=True)
        install_dir(
            Path(skill_path), first_target,
            force_mode="copy", auto_reset=True,
        )
        raise PermissionError("Authorization: Bearer trae-secret")

    monkeypatch.setattr(
        "xskill.ecosystems.install_to_trae", install_first_trae_target,
    )

    details = slots.install(
        result, archive, query="trae", return_details=True,
    )

    records = {
        Path(record["target"]): record
        for record in details["installations"]
    }
    first_target = home / ".trae-cn" / "skills" / result["skill_id"]
    second_target = home / ".trae" / "skills" / result["skill_id"]
    assert records[first_target]["status"] == "installed"
    assert records[first_target]["mode"] == "copy"
    assert "error" not in records[first_target]
    assert records[second_target]["status"] == "failed"
    assert records[second_target]["error_code"] == "TARGET_PERMISSION_DENIED"
    assert records[second_target]["error"] == "目标目录不可写，请检查目录权限"
    serialized = json.dumps(list(records.values()), ensure_ascii=False)
    assert "trae-secret" not in serialized
    assert "Authorization" not in serialized


def test_structured_search_error_is_safe_and_correlated(capsys):
    class _ErrorHttp:
        def get(self, *_args, **_kwargs):
            return _Response(
                500,
                json_data={
                    "code": "SKILL_HUB_SEARCH_FAILED",
                    "message": "Authorization: Bearer response-secret",
                    "request_id": "search-0123456789abcdef",
                    "retryable": False,
                },
                text="Authorization: Bearer raw-secret /root/private",
                headers={"X-Request-ID": "search-0123456789abcdef"},
            )

    return_code = cli.cmd_search_hub(
        SimpleNamespace(terms=["error"], top_k=5, json=False),
        http=_ErrorHttp(), headers={},
    )

    captured = capsys.readouterr()
    assert return_code == 1
    assert "HTTP 500" in captured.err
    assert "服务器执行 SkillHub 搜索时发生异常" in captured.err
    assert "search-0123456789abcdef" in captured.err
    assert "response-secret" not in captured.err
    assert "raw-secret" not in captured.err
    assert "/root/private" not in captured.err


def test_structured_search_error_json_is_machine_readable(capsys):
    class _ErrorHttp:
        def get(self, *_args, **_kwargs):
            return _Response(
                503,
                json_data={
                    "code": "SKILL_HUB_SOURCE_UNAVAILABLE",
                    "message": "do not trust raw server text",
                    "request_id": "search-fedcba9876543210",
                    "retryable": True,
                },
                headers={"X-Request-ID": "search-fedcba9876543210"},
            )

    return_code = cli.cmd_search_hub(
        SimpleNamespace(terms=["error"], top_k=5, json=True),
        http=_ErrorHttp(), headers={},
    )

    payload = json.loads(capsys.readouterr().out)
    assert return_code == 1
    assert payload == {"error": {
        "http_status": 503,
        "code": "SKILL_HUB_SOURCE_UNAVAILABLE",
        "message": "SkillHub 数据源暂时不可用",
        "request_id": "search-fedcba9876543210",
        "retryable": True,
    }}


@pytest.mark.parametrize(
    ("body_request_id", "header_request_id"),
    [
        (
            "search-0123456789abcdef-extra",
            "search-0123456789abcdef",
        ),
        (
            "search-0123456789abcdef",
            "search-fedcba9876543210",
        ),
        (
            "search-Authorization-Bearer-secret",
            "search-/root/private",
        ),
    ],
)
def test_untrusted_or_mismatched_request_ids_are_not_shown(
    body_request_id, header_request_id, capsys,
):
    class _ErrorHttp:
        def get(self, *_args, **_kwargs):
            return _Response(
                500,
                json_data={
                    "code": "SKILL_HUB_SEARCH_FAILED",
                    "message": "safe",
                    "request_id": body_request_id,
                },
                headers={"X-Request-ID": header_request_id},
            )

    return_code = cli.cmd_search_hub(
        SimpleNamespace(terms=["error"], top_k=5, json=False),
        http=_ErrorHttp(), headers={},
    )

    captured = capsys.readouterr()
    assert return_code == 1
    assert "错误编号" not in captured.err
    assert body_request_id not in captured.err
    assert header_request_id not in captured.err


def test_cp936_json_output_with_emoji_is_valid(
    tmp_path, monkeypatch,
):
    home = tmp_path / "home"
    _install_home(monkeypatch, tmp_path, home)
    result = _result(
        "json-\N{GRINNING FACE}",
        source_path="user_skill_hub/\N{CAT FACE}/json-skill",
    )
    result["description"] = "emoji \N{ROCKET}"
    output_bytes = io.BytesIO()
    cp936_stdout = io.TextIOWrapper(
        output_bytes, encoding="cp936", errors="strict",
    )
    monkeypatch.setattr(sys, "stdout", cp936_stdout)

    return_code = cli.cmd_search_hub(
        SimpleNamespace(
            terms=["query-\N{FIRE}"], top_k=5, json=True,
        ),
        http=_SearchHttp([result]), headers={},
    )
    cp936_stdout.flush()

    payload = json.loads(output_bytes.getvalue().decode("cp936"))
    assert return_code == 0
    assert payload[0]["display_name"] == "json-\N{GRINNING FACE}"
    assert payload[0]["description"] == "emoji \N{ROCKET}"
    assert payload[0]["source_path"] == (
        "user_skill_hub/\N{CAT FACE}/json-skill"
    )


def test_search_error_json_parse_log_is_safe(caplog):
    class _BadJsonResponse:
        status_code = 502
        headers = {}

        @staticmethod
        def json():
            raise ValueError(
                "Authorization: Bearer parse-secret /root/private/body"
            )

    caplog.set_level("WARNING", logger="xskill.cli")

    safe_error = cli._safe_search_http_error(_BadJsonResponse())

    assert safe_error["http_status"] == 502
    assert safe_error["code"] == "HTTP_ERROR"
    assert "http_status=502" in caplog.text
    assert "error_type=ValueError" in caplog.text
    assert "Authorization" not in caplog.text
    assert "parse-secret" not in caplog.text
    assert "/root/private/body" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
