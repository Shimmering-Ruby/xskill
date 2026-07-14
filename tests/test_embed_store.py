"""EmbedStore —— 按内容哈希复用 embedding 的磁盘缓存。

验证：
  1. 命中项不再调 embed 服务，只算新增/变更文本。
  2. 缓存跨实例（重启）存活。
  3. 换 embedding 模型整体失效。
  4. 分块即时落盘：中途失败已算部分不回头。
  5. flush_pruned 只保留本轮请求过的哈希。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading

import numpy as np
import pytest

from xskill.utils.embed_store import EmbedStore


class _CountingEmbed:
    def __init__(self, model="fake-embed", fail_on=None):
        self.model = model
        self.encoded: list[str] = []
        self.fail_on = fail_on

    def encode_batch(self, texts):
        for text in texts:
            if self.fail_on is not None and text == self.fail_on:
                raise RuntimeError(f"boom on {text}")
        self.encoded.extend(texts)
        return np.stack([
            np.full(4, float(len(text)), dtype=np.float32) for text in texts
        ])


def test_only_missing_texts_are_encoded(tmp_path):
    embed_client = _CountingEmbed()
    store = EmbedStore(tmp_path / "cache.pkl", embed_client)

    first = store.encode_cached(["aa", "bbb"])
    assert first.shape == (2, 4)
    assert embed_client.encoded == ["aa", "bbb"]

    second = store.encode_cached(["aa", "bbb", "cccc"])
    assert second.shape == (3, 4)
    assert embed_client.encoded == ["aa", "bbb", "cccc"], "命中项不得重算"
    assert np.array_equal(second[0], first[0])


def test_cache_survives_restart(tmp_path):
    cache_path = tmp_path / "cache.pkl"
    EmbedStore(cache_path, _CountingEmbed()).encode_cached(["aa", "bbb"])

    reopened_client = _CountingEmbed()
    reopened = EmbedStore(cache_path, reopened_client)
    result = reopened.encode_cached(["aa", "bbb"])

    assert result.shape == (2, 4)
    assert reopened_client.encoded == [], "重启后全命中，不得重算"


def test_model_change_invalidates_cache(tmp_path):
    cache_path = tmp_path / "cache.pkl"
    EmbedStore(cache_path, _CountingEmbed(model="m1")).encode_cached(["aa"])

    new_model_client = _CountingEmbed(model="m2")
    EmbedStore(cache_path, new_model_client).encode_cached(["aa"])

    assert new_model_client.encoded == ["aa"], "换模型必须整体重算"


def test_partial_progress_persists_on_failure(tmp_path):
    cache_path = tmp_path / "cache.pkl"
    failing_client = _CountingEmbed(fail_on="poison")
    store = EmbedStore(cache_path, failing_client)
    store.SAVE_EVERY = 1

    with pytest.raises(RuntimeError):
        store.encode_cached(["aa", "bbb", "poison"])
    assert failing_client.encoded == ["aa", "bbb"]

    resumed_client = _CountingEmbed()
    resumed = EmbedStore(cache_path, resumed_client)
    resumed.encode_cached(["aa", "bbb", "poison"])
    assert resumed_client.encoded == ["poison"], "中断前已算部分不得重算"


def test_flush_pruned_drops_untouched(tmp_path):
    cache_path = tmp_path / "cache.pkl"
    store = EmbedStore(cache_path, _CountingEmbed())
    store.encode_cached(["stale", "kept"])

    pruning_client = _CountingEmbed()
    pruning_store = EmbedStore(cache_path, pruning_client)
    pruning_store.encode_cached(["kept"])
    pruning_store.flush_pruned()

    final_client = _CountingEmbed()
    final_store = EmbedStore(cache_path, final_client)
    final_store.encode_cached(["kept", "stale"])
    assert final_client.encoded == ["stale"], "被 prune 的条目应重算，保留的命中"


def test_empty_input_returns_empty(tmp_path):
    store = EmbedStore(tmp_path / "cache.pkl", _CountingEmbed())
    assert store.encode_cached([]).shape == (0, 0)


def test_corrupt_cache_file_is_ignored(tmp_path):
    cache_path = tmp_path / "cache.pkl"
    cache_path.write_bytes(b"not a pickle")
    embed_client = _CountingEmbed()

    result = EmbedStore(cache_path, embed_client).encode_cached(["aa"])

    assert result.shape == (1, 4)
    assert embed_client.encoded == ["aa"]


def test_concurrent_instances_merge_updates_without_losing_vectors(tmp_path):
    cache_path = tmp_path / "cache.pkl"
    first = EmbedStore(cache_path, _CountingEmbed())
    second = EmbedStore(cache_path, _CountingEmbed())
    ready = threading.Barrier(2)

    def encode(store, text):
        ready.wait()
        store.encode_cached([text])

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(encode, first, "first"),
                   pool.submit(encode, second, "second")]
        for future in futures:
            future.result()

    verifier = _CountingEmbed()
    result = EmbedStore(cache_path, verifier).encode_cached(["first", "second"])
    assert result.shape == (2, 4)
    assert verifier.encoded == []
