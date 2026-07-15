"""块 3 画像短命子进程:profile-refresh --once 遍历所有 client、写状态文件、失败可控。"""
from __future__ import annotations

from xskill import _workers
from xskill.utils.status_file import PROFILE_STATUS_FILE, read_status_file


class _FakeRegistry:
    def __init__(self, ids):
        self._ids = ids

    def list(self):
        return [{"client_id": cid} for cid in self._ids]


class _FakeEngine:
    def __init__(self, ids):
        self.client_registry = _FakeRegistry(ids)


class _FakeService:
    instances: list = []

    def __init__(self, engine, **_kwargs):
        self.engine = engine
        self.requested: list[str] = []
        self.stopped = False
        self._metrics = {"completed": 2, "unchanged": 1, "failed": 0}
        _FakeService.instances.append(self)

    def request(self, client_id):
        self.requested.append(client_id)

    def wait_idle(self):
        return True

    @property
    def metrics(self):
        return dict(self._metrics)

    def stop(self, timeout=5.0):  # noqa: ARG002 — 需接 stop(timeout=...) 关键字调用
        self.stopped = True


def _patch(monkeypatch, tmp_path, engine_or_exc):
    _FakeService.instances = []
    monkeypatch.setattr("xskill.config.XSKILL_HOME", tmp_path)

    def fake_load_config():
        return {"server": {}}

    monkeypatch.setattr("xskill.config.load_config", fake_load_config)

    def fake_build(_config):
        if isinstance(engine_or_exc, Exception):
            raise engine_or_exc
        return engine_or_exc

    monkeypatch.setattr(
        "xskill.team.server.engine_factory.build_recommend_engine", fake_build)
    monkeypatch.setattr(
        "xskill.team.server.profile_refresh.ProfileRefreshService", _FakeService)


def test_requests_all_clients_and_writes_ok_status(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path, _FakeEngine(["c1", "c2", "c3"]))
    rc = _workers.run_profile_refresh_once()
    assert rc == 0
    service = _FakeService.instances[0]
    assert service.requested == ["c1", "c2", "c3"]  # 遍历全部 client
    assert service.stopped is True  # finally 里停服务,不留常驻线程
    status = read_status_file(tmp_path / PROFILE_STATUS_FILE)
    assert status["ok"] is True
    assert status["stats"]["clients"] == 3
    assert status["stats"]["completed"] == 2


def test_empty_client_list_is_ok(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path, _FakeEngine([]))
    rc = _workers.run_profile_refresh_once()
    assert rc == 0
    assert read_status_file(tmp_path / PROFILE_STATUS_FILE)["stats"]["clients"] == 0


def test_build_failure_writes_error_status_and_returns_1(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path, RuntimeError("db unavailable"))
    rc = _workers.run_profile_refresh_once()
    assert rc == 1
    status = read_status_file(tmp_path / PROFILE_STATUS_FILE)
    assert status["ok"] is False
    assert "db unavailable" in status["error"]
