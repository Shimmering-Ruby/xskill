r"""HybridSearch —— 向量 + BM25 关键字检索，结果 union + dedup（不做 rerank）。

设计：
- 两路检索各自取 top_k，按 atom_id 去重合并。
- 每条结果的 ``sources`` 字段标明命中通道（``"vector"`` / ``"keyword"``），
  方便上层调用方按需做二次排序或过滤。
- 不做 reciprocal-rank fusion / 不做 score normalization——按用户要求"union+dedup"。

中英混合分词用 ``re.compile(r"[\w]+", re.UNICODE)``：拉丁字符按词切，中文整段
当一个 token。这对纯中文检索效果差（整句一个 token，BM25 等同于精确匹配），
后续若有需要再换 jieba；目前先看 E2E 表现。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from rank_bm25 import BM25Okapi

from xskill.atom_task import AtomTaskStore


_WORD_RE = re.compile(r"[\w]+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _WORD_RE.findall(text or "")]


@dataclass
class HybridSearch:
    store: AtomTaskStore
    embed_client: Any

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        vec_hits = self.store.vector_search(query, self.embed_client, top_k=top_k)
        kw_hits = self._keyword_search(query, top_k=top_k)

        merged: dict[str, dict] = {}
        for h in vec_hits:
            entry = merged.setdefault(
                h["atom_id"], {"atom_id": h["atom_id"], "sources": []},
            )
            entry["sources"].append("vector")
            entry["vector_similarity"] = h["similarity"]
        for h in kw_hits:
            entry = merged.setdefault(
                h["atom_id"], {"atom_id": h["atom_id"], "sources": []},
            )
            entry["sources"].append("keyword")
            entry["bm25_score"] = h["score"]
        return list(merged.values())

    def _keyword_search(self, query: str, top_k: int) -> list[dict]:
        atoms = list(self.store.all_atoms())
        if not atoms:
            return []
        tokens = _tokenize(query)
        if not tokens:
            return []
        corpus = [_tokenize(a.summary or a.intent) for a in atoms]
        bm25 = BM25Okapi(corpus)
        scores = bm25.get_scores(tokens)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        out: list[dict] = []
        for i, s in ranked[:top_k]:
            if s <= 0:
                continue
            out.append({"atom_id": atoms[i].atom_id, "score": float(s)})
        return out
