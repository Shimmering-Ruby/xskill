"""tests/rate_limit/ 共享 fixture。"""
from __future__ import annotations

import pytest

from tests._fake_llm_server import FakeLLMServer
from xskill.utils.rate_limit import reset_buckets_for_testing


@pytest.fixture
def fake_server():
    """启动一个隔离的 FakeLLMServer,test 结束自动 stop。"""
    with FakeLLMServer() as srv:
        yield srv


@pytest.fixture(autouse=True)
def isolate_buckets():
    """每个 test 跑前清空 _BUCKETS 注册表,避免跨 test 污染。"""
    reset_buckets_for_testing()
    yield
    reset_buckets_for_testing()
