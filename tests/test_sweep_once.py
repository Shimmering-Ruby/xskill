"""块 2 watcher 短命子进程:sweep --once 跑一轮即退、server 跳过采集、失败可控;
ingester 一次性入库调 run_once() 而非 start()(不起常驻线程)。"""
from __future__ import annotations

from xskill import _workers
from xskill.pipeline import watcher_factory
from xskill.utils.status_file import WATCHER_STATUS_FILE, read_status_file


class _FakeWatcher:
    def __init__(self):
        self.drained = False
        self.stats = {"polls": 1, "new_trajs": 0}

    def run_once_and_drain(self):
        self.drained = True


def _patch_sweep(monkeypatch, tmp_path, *, build_exc=None):
    monkeypatch.setattr("xskill.config.XSKILL_HOME", tmp_path)

    def fake_load_config():
        return {"watcher": {}, "server": {}}

    def fake_get_skill_dir():
        return tmp_path / "skill"

    monkeypatch.setattr("xskill.config.load_config", fake_load_config)
    monkeypatch.setattr("xskill.config.get_skill_dir", fake_get_skill_dir)

    built = []

    def fake_build(_config, **_kwargs):
        if build_exc is not None:
            raise build_exc
        watcher = _FakeWatcher()
        built.append(watcher)
        return watcher

    ingest_calls = []

    def fake_ingest(_config, home_root, skill_dir):
        ingest_calls.append((home_root, skill_dir))

    monkeypatch.setattr(
        "xskill.pipeline.watcher_factory.build_watcher", fake_build)
    monkeypatch.setattr(
        "xskill.pipeline.watcher_factory.ingest_detected_ecosystems_once", fake_ingest)
    return built, ingest_calls


def test_sweep_server_mode_skips_ingest_and_writes_ok(tmp_path, monkeypatch):
    built, ingest_calls = _patch_sweep(monkeypatch, tmp_path)
    rc = _workers.run_sweep_once(server=True)
    assert rc == 0
    assert ingest_calls == []  # server 模式跳过本机生态采集
    assert built[0].drained is True  # 跑了一轮 run_once_and_drain
    status = read_status_file(tmp_path / WATCHER_STATUS_FILE)
    assert status["ok"] is True
    assert status["stats"] == {"polls": 1, "new_trajs": 0}


def test_sweep_standalone_runs_ecosystem_ingest(tmp_path, monkeypatch):
    _built, ingest_calls = _patch_sweep(monkeypatch, tmp_path)
    rc = _workers.run_sweep_once(server=False)
    assert rc == 0
    assert len(ingest_calls) == 1  # 非 server 模式跑一次生态一次性入库


def test_sweep_failure_writes_error_status_and_returns_1(tmp_path, monkeypatch):
    _patch_sweep(monkeypatch, tmp_path, build_exc=RuntimeError("llm down"))
    rc = _workers.run_sweep_once(server=True)
    assert rc == 1
    status = read_status_file(tmp_path / WATCHER_STATUS_FILE)
    assert status["ok"] is False
    assert "llm down" in status["error"]


class _FakeIngester:
    calls = {"run_once": 0, "start": 0}

    def __init__(self, *args, **kwargs):
        pass

    def run_once(self):
        _FakeIngester.calls["run_once"] += 1
        return []

    def start(self):
        _FakeIngester.calls["start"] += 1


def test_run_once_and_drain_sequence_and_pool_shutdown(monkeypatch):
    """run_once_and_drain = _scan_once → 排空线程池 → _harvest,完事线程池已关(不留活)。"""
    import pytest

    from xskill.pipeline.runner import DirectoryWatcher

    watcher = DirectoryWatcher()
    order = []

    def fake_scan():
        order.append("scan")

    def fake_harvest():
        order.append("harvest")

    monkeypatch.setattr(watcher, "_scan_once", fake_scan)
    monkeypatch.setattr(watcher, "_harvest", fake_harvest)
    watcher.run_once_and_drain()
    assert order == ["scan", "harvest"]
    # 线程池已 shutdown:再 submit 抛 RuntimeError(无残留可复用的池)。
    with pytest.raises(RuntimeError):
        watcher._pool.submit(len, [])


def test_ingest_once_calls_run_once_not_start(tmp_path, monkeypatch):
    """生态一次性入库对每个检测到的生态调 run_once(),绝不 start()(不起常驻线程)。"""
    _FakeIngester.calls = {"run_once": 0, "start": 0}
    bridge = tmp_path / "bridge"

    def fake_detect(home_root=None):  # noqa: ARG001 — 需接 home_root= 关键字调用
        return [{"ecosystem": "codex", "bridge": bridge, "source": "test"}]

    def fake_register(*_args, **_kwargs):
        return None

    def fake_install(*_args, **_kwargs):
        return []

    monkeypatch.setattr("xskill.ecosystems.detect_known_ecosystems", fake_detect)
    monkeypatch.setattr("xskill.pipeline.registry.register_dir", fake_register)
    monkeypatch.setattr("xskill.ecosystems.install_all_to_codex", fake_install)
    monkeypatch.setattr("xskill.ecosystems.JsonlIngester", _FakeIngester)
    monkeypatch.setattr("xskill.config.XSKILL_HOME", tmp_path)

    watcher_factory.ingest_detected_ecosystems_once(
        {"watcher": {}}, tmp_path, tmp_path / "skill")

    assert _FakeIngester.calls["run_once"] == 1
    assert _FakeIngester.calls["start"] == 0
