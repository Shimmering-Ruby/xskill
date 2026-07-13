"""embed_store.py — 按 (embedding 模型, 文本 sha256) 复用向量的磁盘缓存。

命中直接读盘；未命中分块现算并即时落盘（中断续算不回头）；换模型整体失效。
"""
from __future__ import annotations

import hashlib
import os
import pickle
import threading
from pathlib import Path

import numpy as np


class EmbedStore:
    """``cache_path`` 上的「文本哈希 → 向量」缓存，只对未命中文本现算。"""

    SAVE_EVERY = 16

    def __init__(self, cache_path: Path | str, embed_client):
        self.cache_path = Path(cache_path)
        self.embed_client = embed_client
        self.model_id = str(getattr(embed_client, "model", "") or "")
        self._vectors: dict[str, np.ndarray] = {}
        self._touched: set[str] = set()
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        try:
            with open(self.cache_path, "rb") as cache_file:
                data = pickle.load(cache_file)
        except (OSError, pickle.PickleError, EOFError, AttributeError):
            return
        # 缓存损坏或换了 embedding 模型 → 整体作废，从零重建。
        if not isinstance(data, dict) or data.get("model") != self.model_id:
            return
        vectors = data.get("vectors")
        if isinstance(vectors, dict):
            self._vectors = vectors

    def _save(self) -> None:
        # tmp 名带 pid：多进程写同一缓存时各写各的临时文件，replace 原子收尾。
        temp_path = self.cache_path.with_suffix(f".tmp.{os.getpid()}")
        with open(temp_path, "wb") as cache_file:
            pickle.dump(
                {"model": self.model_id, "vectors": self._vectors}, cache_file,
            )
        os.replace(temp_path, self.cache_path)

    def encode_cached(self, texts: list[str]) -> np.ndarray:
        """返回 ``(len(texts), dim)`` 向量矩阵；未命中的分块现算并即时落盘。"""
        if not texts:
            return np.empty((0, 0), dtype=np.float32)
        with self._lock:
            text_hashes = [
                hashlib.sha256(text.encode("utf-8")).hexdigest() for text in texts
            ]
            self._touched.update(text_hashes)
            missing: dict[str, str] = {}
            for text, text_hash in zip(texts, text_hashes):
                if text_hash not in self._vectors and text_hash not in missing:
                    missing[text_hash] = text
            missing_items = list(missing.items())
            for chunk_start in range(0, len(missing_items), self.SAVE_EVERY):
                chunk = missing_items[chunk_start:chunk_start + self.SAVE_EVERY]
                chunk_vectors = np.asarray(
                    self.embed_client.encode_batch(
                        [text for _text_hash, text in chunk],
                    ),
                    dtype=np.float32,
                )
                for (text_hash, _text), vector in zip(chunk, chunk_vectors):
                    self._vectors[text_hash] = vector
                self._save()
            return np.stack([self._vectors[h] for h in text_hashes])

    def flush_pruned(self) -> None:
        """只保留本实例生命周期内被请求过的哈希，防陈旧条目无限积累。

        仅当本轮调用覆盖了完整语料（索引全量重建）时使用；按子集查询的
        调用方不要调，否则会把其他调用方的缓存修剪掉。
        """
        with self._lock:
            self._vectors = {
                text_hash: vector
                for text_hash, vector in self._vectors.items()
                if text_hash in self._touched
            }
            self._save()
