"""
test_ssl_verify.py -- T2S_SSL_VERIFY 环境变量 + agno kwarg 注入
===============================================================
覆盖上一次生产事故：
  - log.step(...) 被当 method 调用（StreamLog 没这方法）
  - agno OpenAIChat 不接受 async_http_client（实际叫 async_client）
  - _ssl_verify() 对各种 env 值（false / 0 / no / off / 大写 / 空）的识别
"""
from __future__ import annotations

import os
import pytest


# ── _ssl_verify: env 值识别 ────────────────────────────────────────────
@pytest.mark.parametrize("val,expect_verify", [
    ("false", False),
    ("FALSE", False),
    ("False", False),
    ("0",     False),
    ("no",    False),
    ("NO",    False),
    ("off",   False),
    ("OFF",   False),
    ("",      True),   # 空值 = 默认开
    ("true",  True),
    ("1",     True),
    ("yes",   True),
    ("anything-else", True),
])
def test_ssl_verify_env(monkeypatch, val, expect_verify):
    from traj2skill.llm_client import _ssl_verify
    if val == "":
        monkeypatch.delenv("T2S_SSL_VERIFY", raising=False)
    else:
        monkeypatch.setenv("T2S_SSL_VERIFY", val)
    assert _ssl_verify() is expect_verify


# ── _inject_verify_off_if_requested: agno kwarg 注入 ──────────────────
class _FakeLog:
    """模拟 StreamLog 的接口：可调用 + 记录事件"""
    def __init__(self):
        self.events = []
    def __call__(self, msg: str, tag: str = "info"):
        self.events.append((tag, msg))


class _ModelHttpClient:
    """agno 新版：接受 http_client / async_client"""
    def __init__(self, id=None, base_url=None, api_key=None,
                 http_client=None, async_client=None, **kw):
        self.http_client = http_client
        self.async_client = async_client


class _ModelAsyncHttpClient:
    """agno 旧版假想：接受 async_http_client（用于覆盖"候选别名"路径）"""
    def __init__(self, id=None, base_url=None, api_key=None,
                 http_client=None, async_http_client=None, **kw):
        self.http_client = http_client
        self.async_http_client = async_http_client


class _ModelNoClientKwargs:
    """某些 model class 完全不接受 http_client"""
    def __init__(self, id=None, base_url=None, api_key=None, **kw):
        pass


def test_inject_verify_off_new_agno(monkeypatch):
    monkeypatch.setenv("T2S_SSL_VERIFY", "false")
    from traj2skill.agent import _inject_verify_off_if_requested
    log = _FakeLog()
    kwargs = {"id": "x", "base_url": "u", "api_key": "k"}
    _inject_verify_off_if_requested(_ModelHttpClient, kwargs, log)
    assert "http_client" in kwargs and kwargs["http_client"] is not None
    assert "async_client" in kwargs and kwargs["async_client"] is not None
    # 并且能用这些 kwarg 真正 new 一个实例，不抛 TypeError（即本次修复的保护目标）
    m = _ModelHttpClient(**kwargs)
    assert m.http_client is not None


def test_inject_verify_off_old_agno(monkeypatch):
    monkeypatch.setenv("T2S_SSL_VERIFY", "false")
    from traj2skill.agent import _inject_verify_off_if_requested
    log = _FakeLog()
    kwargs = {}
    _inject_verify_off_if_requested(_ModelAsyncHttpClient, kwargs, log)
    # 这个假 class 接受 http_client 和 async_http_client，不接受 async_client
    assert "http_client" in kwargs
    assert "async_http_client" in kwargs
    _ModelAsyncHttpClient(**kwargs)  # 不应抛


def test_inject_verify_off_unsupported_class(monkeypatch):
    monkeypatch.setenv("T2S_SSL_VERIFY", "false")
    from traj2skill.agent import _inject_verify_off_if_requested
    log = _FakeLog()
    kwargs = {}
    _inject_verify_off_if_requested(_ModelNoClientKwargs, kwargs, log)
    # 不认任何 http_client kwarg 时不能误塞，不然会 TypeError
    assert "http_client"       not in kwargs
    assert "async_client"      not in kwargs
    assert "async_http_client" not in kwargs
    # 应该打 error 日志提示改用 SSL_CERT_FILE
    assert any(tag == "error" and "SSL_CERT_FILE" in msg for tag, msg in log.events)


def test_inject_verify_on_is_noop(monkeypatch):
    """T2S_SSL_VERIFY 未设或非 false 时不动 kwargs"""
    monkeypatch.delenv("T2S_SSL_VERIFY", raising=False)
    from traj2skill.agent import _inject_verify_off_if_requested
    log = _FakeLog()
    kwargs = {"id": "x"}
    _inject_verify_off_if_requested(_ModelHttpClient, kwargs, log)
    assert kwargs == {"id": "x"}
    assert log.events == []


# ── StreamLog 回归：确认它只能 log(msg, tag)，不能 log.step(...) ─────
def test_streamlog_is_callable_not_has_step():
    """防回归：不要再写 log.step(...)；StreamLog 是 callable 不是 has attr step"""
    from traj2skill.log import StreamLog
    log = StreamLog(verbose=False)
    assert callable(log)
    assert not hasattr(log, "step"), (
        "StreamLog 不应有 .step 属性——历史上踩过 log.step(...) 的坑，"
        "用 log(msg, 'step') 代替"
    )
    log("hello", "step")  # 正确用法
    assert len(log.events) == 1
    assert log.events[0]["tag"] == "step"
