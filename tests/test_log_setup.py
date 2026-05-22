"""tests/test_log_setup.py — log file routing"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_root_logger():
    """每个 test 跑完把 root logger handlers 清干净——xskill 装的 handler
    会污染下一个 test 的 caplog 等。"""
    yield
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_xskill_managed", False):
            root.removeHandler(h)
    # 清掉所有 named logger 上 xskill 加的 handler
    for name in list(logging.Logger.manager.loggerDict.keys()):
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            if getattr(h, "_xskill_managed", False):
                lg.removeHandler(h)


def test_creates_log_files_for_each_component(tmp_path):
    from xskill.utils.logging import configure_logging
    configure_logging(tmp_path, debug=False, quiet=False, stdout=False)

    logging.getLogger("xskill.watcher").info("watcher said hi")
    logging.getLogger("xskill.canary").info("canary said hi")
    logging.getLogger("agno").warning("agno noise")
    logging.getLogger("xskill.ecosystems").info("ecosystems said hi")

    # 强制 flush（RotatingFileHandler 用 buffered IO）
    for h in logging.getLogger().handlers:
        h.flush()
    for name in ("xskill.watcher", "xskill.canary", "agno", "xskill.ecosystems"):
        for h in logging.getLogger(name).handlers:
            h.flush()

    assert (tmp_path / "xskill.watcher.log").read_text(encoding="utf-8").find("watcher said hi") >= 0
    assert (tmp_path / "xskill.canary.log").read_text(encoding="utf-8").find("canary said hi") >= 0
    assert (tmp_path / "agno.log").read_text(encoding="utf-8").find("agno noise") >= 0
    assert (tmp_path / "xskill.ecosystems.log").read_text(encoding="utf-8").find("ecosystems said hi") >= 0


def test_xskill_log_aggregates_all_xskill_messages(tmp_path):
    """xskill.* 下游所有子 logger 的消息都应当冒泡进 xskill.log 全合并视图。"""
    from xskill.utils.logging import configure_logging
    configure_logging(tmp_path, debug=False, quiet=False, stdout=False)

    logging.getLogger("xskill.watcher").info("from watcher")
    logging.getLogger("xskill.canary").info("from canary")

    for h in logging.getLogger().handlers:
        h.flush()
    for name in ("xskill", "xskill.watcher", "xskill.canary"):
        for h in logging.getLogger(name).handlers:
            h.flush()

    aggregate = (tmp_path / "xskill.log").read_text(encoding="utf-8")
    assert "from watcher" in aggregate
    assert "from canary" in aggregate


def test_idempotent_no_duplicate_handlers(tmp_path):
    from xskill.utils.logging import configure_logging
    configure_logging(tmp_path, stdout=False)
    n_first = sum(1 for h in logging.getLogger().handlers
                  if getattr(h, "_xskill_managed", False))
    configure_logging(tmp_path, stdout=False)
    n_second = sum(1 for h in logging.getLogger().handlers
                   if getattr(h, "_xskill_managed", False))
    assert n_first == n_second, "calling configure_logging twice should not add new handlers"


def test_quieter_loggers_default_to_warning(tmp_path):
    from xskill.utils.logging import configure_logging
    configure_logging(tmp_path, stdout=False)
    for noisy in ("httpx", "httpcore", "openai"):
        assert logging.getLogger(noisy).level == logging.WARNING, noisy


def test_debug_flag_sets_root_to_debug(tmp_path):
    from xskill.utils.logging import configure_logging
    configure_logging(tmp_path, debug=True, stdout=False)
    assert logging.getLogger().level == logging.DEBUG
    assert logging.getLogger("xskill.watcher").level == logging.DEBUG
