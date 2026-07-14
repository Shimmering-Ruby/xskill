"""test_skillhub_hybrid_search.py — Part A 混合检索：BM25 + 语义 RRF 融合及保护机制。

覆盖：bigram 分词、BM25 排序性与 name>description 权重、RRF 融合、语义通道降级
三条路（client None / 信号量满 / 超时）、失败冷却、query LRU 命中与 fingerprint 失效、
max_embed=0 关闭语义、端点新字段（source/ux_avg/match）、upload 后 corpus 补 embed。
"""
from __future__ import annotations

import asyncio
import io
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from unittest.mock import AsyncMock

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xskill.recommend import skillhub as skillhub_module
from xskill.recommend.skillhub import EMBED_CACHE_NAME, SkillHub, _tokenize
from xskill.team.server import api as server_api
from xskill.team.server.client_registry import ClientRegistry, safe_dir_name
from xskill.utils.embed_store import EmbedStore

TOKEN = "secret-token"


class ControlledEmbed:
    """按文本精确查表返回向量的 embed client，可控 cosine 顺序并计数 query 调用。"""

    model = "test-embed-model"

    def __init__(self, vectors_by_text: dict[str, list[float]], encode_delay: float = 0.0):
        self.vectors_by_text = vectors_by_text
        self.encode_delay = encode_delay
        self.encode_calls: list[str] = []
        self.fail = False

    def encode(self, text: str) -> np.ndarray:
        self.encode_calls.append(text)
        if self.encode_delay:
            time.sleep(self.encode_delay)
        if self.fail:
            raise RuntimeError("embedding backend unavailable")
        return np.asarray(self.vectors_by_text[text], dtype=float)

    def encode_batch(self, texts: list[str]) -> np.ndarray:
        return np.stack([self.encode(text) for text in texts])


def _write_hub_skill(hub_dir: Path, folder: str, name: str, description: str) -> None:
    skill_dir = hub_dir / folder
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\nbody\n",
        encoding="utf-8")


def _prime_corpus_cache(hub: SkillHub) -> None:
    """用只读语义通道之前，把语料 description 向量灌进 EmbedStore 缓存文件。"""
    descriptions = [
        entry["description"]
        for entry in hub._entries(include_vec=False, require_description=False)
    ]
    EmbedStore(hub.dir / EMBED_CACHE_NAME, hub.embed_client).encode_cached(descriptions)
    hub.embed_client.encode_calls.clear()


# ── 分词 ─────────────────────────────────────────────────────────

def test_tokenize_mixed_chinese_english():
    assert _tokenize("Docker k8s") == ["docker", "k8s"]
    assert _tokenize("你好世界") == ["你好", "好世", "世界"]
    assert _tokenize("a好b") == ["a", "好", "b"]
    assert _tokenize("部署 deploy123") == ["部署", "deploy123"]


# ── BM25 排序性 + name>description 权重 ──────────────────────────

def test_bm25_orders_matches_and_name_outranks_description(tmp_path):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "a", "alpha", "gamma one")       # 词命中在 name
    _write_hub_skill(hub_dir, "b", "beta", "alpha two")        # 词命中在 description
    _write_hub_skill(hub_dir, "c", "delta", "gamma three")     # 不命中
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=None)
    results = hub.search("alpha", limit=5)
    names = [result["display_name"] for result in results]
    assert names == ["alpha", "beta"]
    assert results[0]["bm25_rank"] == 1 and results[1]["bm25_rank"] == 2
    assert all(result["semantic_rank"] is None for result in results)


# ── RRF 融合（构造双通道 rank） ─────────────────────────────────

def test_rrf_fusion_combines_both_channels(tmp_path):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "a", "alpha", "gamma one")
    _write_hub_skill(hub_dir, "b", "beta", "alpha two")
    _write_hub_skill(hub_dir, "c", "delta", "gamma three")
    client = ControlledEmbed({
        "gamma one": [0.8, 0.6, 0.0],   # A 语义
        "alpha two": [0.0, 1.0, 0.0],   # B 语义
        "gamma three": [1.0, 0.0, 0.0],  # C 语义（最贴近 query）
        "alpha": [1.0, 0.2, 0.0],        # query 语义
    })
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=client)
    _prime_corpus_cache(hub)
    results = hub.search("alpha", limit=5)
    # BM25: A#1 B#2；语义: C#1 A#2 B#3 → RRF: A > B > C
    assert [result["display_name"] for result in results] == ["alpha", "beta", "delta"]
    by_name = {result["display_name"]: result for result in results}
    assert (by_name["alpha"]["bm25_rank"], by_name["alpha"]["semantic_rank"]) == (1, 2)
    assert (by_name["beta"]["bm25_rank"], by_name["beta"]["semantic_rank"]) == (2, 3)
    assert (by_name["delta"]["bm25_rank"], by_name["delta"]["semantic_rank"]) == (None, 1)


def test_rrf_tie_break_prefers_higher_ux_then_skill_id(tmp_path):
    hub = SkillHub(enabled=False, hub_dir=tmp_path / "hub", embed_client=None)
    entries = [
        {"skill_id": "z-low"}, {"skill_id": "m-high"}, {"skill_id": "a-none"},
    ]
    reciprocal_scores = {0: 0.5, 1: 0.5, 2: 0.5}
    ux_by_id = {"z-low": 1.0, "m-high": 9.0, "a-none": None}
    hub.ux_avg = lambda skill_id, days=30: ux_by_id[skill_id]
    ordered = hub._fuse_and_rank(reciprocal_scores, entries, limit=5)
    # 同分：ux 高者先（m-high），再 ux 低（z-low），None 最低（a-none）
    assert [entries[index]["skill_id"] for index in ordered] == [
        "m-high", "z-low", "a-none"]


def test_search_reuses_cached_ux_values(tmp_path, monkeypatch):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "a", "alpha-one", "alpha shared")
    _write_hub_skill(hub_dir, "b", "alpha-two", "alpha shared")
    loaded_paths: list[Path] = []

    def fake_load_ux_scores(path):
        loaded_paths.append(Path(path))
        return []

    monkeypatch.setattr(skillhub_module, "load_ux_scores", fake_load_ux_scores)
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=None)
    first = hub.search("alpha", limit=5)
    second = hub.search("alpha", limit=5)

    assert len(first) == len(second) == 2
    assert len(loaded_paths) == 2
    assert all(result["ux_avg"] is None for result in second)


def test_search_hot_path_reuses_snapshot_index_without_recopying_entries(
    tmp_path, monkeypatch,
):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "a", "alpha", "alpha shared")
    hub = SkillHub(
        enabled=True, hub_dir=hub_dir, embed_client=None,
        scan_ttl_seconds=60.0,
    )
    first = hub.search("alpha", limit=5)

    def fail_entries(**_kwargs):
        raise AssertionError("hot search rebuilt entries instead of reusing the snapshot index")

    monkeypatch.setattr(hub, "_entries", fail_entries)
    second = hub.search("alpha", limit=5)

    assert first == second


def test_unchanged_expired_snapshot_reuses_search_index(tmp_path):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "a", "alpha", "alpha shared")
    hub = SkillHub(
        enabled=True, hub_dir=hub_dir, embed_client=None,
        scan_ttl_seconds=0.0,
    )

    first_bundle = hub._search_index_bundle()
    second_bundle = hub._search_index_bundle()

    assert second_bundle is first_bundle


def test_search_hot_path_reuses_bm25_ranks(tmp_path, monkeypatch):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "a", "alpha", "alpha shared")
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=None)
    first = hub.search("alpha", limit=5)

    def fail_log(*_args, **_kwargs):
        raise AssertionError("hot search recomputed cached BM25 ranks")

    monkeypatch.setattr(skillhub_module.math, "log", fail_log)
    second = hub.search("alpha", limit=5)

    assert first == second


def test_bm25_rank_cache_is_bounded(tmp_path, monkeypatch):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "a", "alpha beta gamma", "shared")
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=None)
    monkeypatch.setattr(skillhub_module, "SEARCH_RANK_CACHE_CAPACITY", 2)

    hub.search("alpha", limit=5)
    hub.search("beta", limit=5)
    hub.search("gamma", limit=5)

    rank_cache = hub._search_index_bundle()["bm25_rank_cache"]
    assert list(rank_cache) == [("beta",), ("gamma",)]
    result_cache = hub._search_index_bundle()["search_result_cache"]
    assert [cache_key[0] for cache_key in result_cache] == ["beta", "gamma"]


def test_search_hot_path_reuses_semantic_ranks_and_fusion_groups(
    tmp_path, monkeypatch,
):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "a", "alpha", "alpha shared")
    client = ControlledEmbed({
        "alpha shared": [1.0, 0.0],
        "alpha": [1.0, 0.0],
    })
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=client)
    _prime_corpus_cache(hub)
    first = hub.search("alpha", limit=5)
    hub._search_index_bundle()["search_result_cache"].clear()

    def fail_recompute(*_args, **_kwargs):
        raise AssertionError("hot search recomputed semantic or fusion ranks")

    monkeypatch.setattr(skillhub_module.np, "argsort", fail_recompute)
    monkeypatch.setattr(hub, "_score_groups", fail_recompute)
    second = hub.search("alpha", limit=5)

    assert first == second


def test_search_hot_path_reuses_final_results(tmp_path, monkeypatch):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "a", "alpha", "alpha shared")
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=None)
    first = hub.search("alpha", limit=5)

    def fail_ranking(*_args, **_kwargs):
        raise AssertionError("hot search recomputed final result ranking")

    monkeypatch.setattr(hub, "_rank_score_groups", fail_ranking)
    second = hub.search("alpha", limit=5)

    assert first == second


def test_search_final_result_cache_expires(tmp_path, monkeypatch):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "a", "alpha", "alpha shared")
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=None)
    monkeypatch.setattr(skillhub_module, "SEARCH_RESULT_CACHE_TTL_SECONDS", 0.0)
    original_rank = hub._rank_score_groups
    rank_calls = 0

    def counted_rank(*args, **kwargs):
        nonlocal rank_calls
        rank_calls += 1
        return original_rank(*args, **kwargs)

    monkeypatch.setattr(hub, "_rank_score_groups", counted_rank)
    hub.search("alpha", limit=5)
    hub.search("alpha", limit=5)

    assert rank_calls == 2


def test_cached_search_uses_only_current_in_memory_snapshot(tmp_path, monkeypatch):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "a", "alpha", "alpha shared")
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=None)
    expected = hub.search("alpha", limit=5)

    def fail_search_index_build():
        raise AssertionError("hot cache lookup must not scan or rebuild the index")

    monkeypatch.setattr(hub, "_search_index_bundle", fail_search_index_build)
    assert hub.cached_search("alpha", limit=5) == expected

    hub._scan_snapshot_expires_at = 0.0
    assert hub.cached_search("alpha", limit=5) is None


# ── 语义通道降级三条路 ─────────────────────────────────────────

def test_semantic_degrades_when_embed_client_is_none(tmp_path):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "a", "alpha", "gamma one")
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=None)
    results = hub.search("alpha", limit=5)
    assert results and all(result["semantic_rank"] is None for result in results)


def test_semantic_degrades_when_semaphore_exhausted(tmp_path):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "a", "alpha", "gamma one")
    client = ControlledEmbed({"gamma one": [1.0, 0.0], "alpha": [1.0, 0.0]})
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=client,
                   search_max_embed=1)
    _prime_corpus_cache(hub)
    hub._query_embed_semaphore.acquire()  # 占满唯一名额
    try:
        results = hub.search("alpha", limit=5)
    finally:
        hub._query_embed_semaphore.release()
    assert client.encode_calls == []  # 抢不到即降级，未发 query embed
    assert results and all(result["semantic_rank"] is None for result in results)


def test_semantic_degrades_on_query_embed_timeout(tmp_path):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "a", "alpha", "gamma one")
    client = ControlledEmbed({"gamma one": [1.0, 0.0], "alpha": [1.0, 0.0]},
                             encode_delay=1.0)
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=client,
                   search_timeout_s=0.05)
    _prime_corpus_cache(hub)
    started_at = time.monotonic()
    results = hub.search("alpha", limit=5)
    assert time.monotonic() - started_at < 0.6  # 未被 1s 的 embed 拖住
    assert results and all(result["semantic_rank"] is None for result in results)


def test_concurrent_same_query_uses_one_embedding_request(tmp_path):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "a", "alpha", "gamma one")
    client = ControlledEmbed({"gamma one": [1.0, 0.0], "alpha": [1.0, 0.0]},
                             encode_delay=0.15)
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=client,
                   search_max_embed=4)
    _prime_corpus_cache(hub)
    ready = threading.Barrier(8)

    def search_once():
        ready.wait()
        return hub.search("alpha", limit=5)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = [future.result() for future in
                   [pool.submit(search_once) for _ in range(8)]]

    assert client.encode_calls == ["alpha"]
    assert all(result for result in results)


def test_embed_failure_cooldown_skips_repeated_backend_calls(tmp_path, monkeypatch):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "a", "alpha", "gamma one")
    client = ControlledEmbed({
        "gamma one": [1.0, 0.0],
        "alpha": [1.0, 0.0],
        "beta": [0.0, 1.0],
    })
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=client,
                   search_max_embed=1)
    _prime_corpus_cache(hub)
    monkeypatch.setattr(skillhub_module, "QUERY_EMBED_FAILURE_COOLDOWN_SECONDS", 0.02)

    client.fail = True
    first = hub.search("alpha", limit=5)
    second = hub.search("beta", limit=5)
    assert client.encode_calls == ["alpha"]
    assert first and all(result["semantic_rank"] is None for result in first)
    assert second == []

    time.sleep(0.03)
    client.fail = False
    hub.search("beta", limit=5)
    assert client.encode_calls == ["alpha", "beta"]


# ── query LRU：命中零调用 + fingerprint 失效 ────────────────────

def test_query_lru_hit_then_invalidated_by_fingerprint(tmp_path):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "a", "alpha", "gamma one")
    client = ControlledEmbed({"gamma one": [1.0, 0.0], "alpha": [1.0, 0.0]})
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=client,
                   scan_ttl_seconds=0.0)
    _prime_corpus_cache(hub)
    hub.search("alpha", limit=5)
    hub.search("alpha", limit=5)
    assert client.encode_calls == ["alpha"]  # 第二次命中 LRU，零 embed 调用
    _write_hub_skill(hub_dir, "b", "beta", "beta desc")  # 改 corpus → fingerprint 变
    hub.search("alpha", limit=5)
    assert client.encode_calls == ["alpha", "alpha"]  # fingerprint 变 → LRU miss


# ── max_embed=0 关闭语义 ───────────────────────────────────────

def test_max_embed_zero_disables_semantic_channel(tmp_path):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "a", "alpha", "gamma one")
    client = ControlledEmbed({"gamma one": [1.0, 0.0], "alpha": [1.0, 0.0]})
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=client,
                   search_max_embed=0)
    _prime_corpus_cache(hub)
    results = hub.search("alpha", limit=5)
    assert client.encode_calls == []
    assert results and all(result["semantic_rank"] is None for result in results)


# ── 端点新字段：source / ux_avg / match ─────────────────────────

def _make_team_client(tmp_path: Path, *, skillhub) -> TestClient:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(exist_ok=True)
    server_api.init_team_context(
        join_token=TOKEN,
        client_registry=ClientRegistry(tmp_path / "clients.db"),
        skill_dir=skill_dir,
        traj_root=tmp_path / "team_traj",
        probability=0.2, ranked_slots=80, total_slots=100,
        register_dir=lambda path, label: None,
        skillhub=skillhub,
    )
    app = FastAPI()
    app.include_router(server_api.router)
    return TestClient(app)


def _register(client: TestClient, user_name: str | None = None) -> tuple[str, dict]:
    body = {"token": TOKEN, "client_label": "t", "hostname": "h"}
    if user_name:
        body["user_name"] = user_name
    response = client.post("/api/v1/team/register", json=body)
    assert response.status_code == 200
    client_id = response.json()["client_id"]
    return client_id, {"X-Xskill-Token": TOKEN, "X-Xskill-Client": client_id}


def _zip_dir(src: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for path in sorted(src.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(src).as_posix())
    return buf.getvalue()


def test_endpoint_adds_source_ux_and_match_fields(tmp_path):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "docker-helper", "docker-helper",
                     "Manage docker containers")
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=None)
    http = _make_team_client(tmp_path, skillhub=hub)
    _cid, headers = _register(http)
    response = http.get("/api/v1/team/skill_hub/search",
                        params={"query": "docker"}, headers=headers)
    assert response.status_code == 200
    top = response.json()["results"][0]
    assert top["source"] == "skillhub"
    assert top["ux_avg"] is None
    assert top["match"] == {"bm25_rank": 1, "semantic_rank": None}


def test_endpoint_serves_hot_result_without_anyio_worker(tmp_path, monkeypatch):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "docker-helper", "docker-helper",
                     "Manage docker containers")
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=None)
    http = _make_team_client(tmp_path, skillhub=hub)
    _cid, headers = _register(http)
    first = http.get("/api/v1/team/skill_hub/search",
                     params={"query": "docker"}, headers=headers)
    assert first.status_code == 200

    async def fail_worker(*_args, **_kwargs):
        raise AssertionError("hot result must not enter the AnyIO worker pool")

    monkeypatch.setattr(server_api, "run_in_threadpool", fail_worker)
    second = http.get("/api/v1/team/skill_hub/search",
                      params={"query": "docker"}, headers=headers)
    assert second.status_code == 200
    assert second.json() == first.json()

    cooperative_yield = AsyncMock()
    monkeypatch.setattr(server_api.asyncio, "sleep", cooperative_yield)
    direct = asyncio.run(server_api.team_skill_hub_search(
        query="docker",
        x_xskill_token=headers["X-Xskill-Token"],
        x_xskill_client=headers["X-Xskill-Client"],
    ))
    assert direct == first.json()
    assert cooperative_yield.await_count == 2


def test_endpoint_source_marks_uploader(tmp_path):
    hub_dir = tmp_path / "hub"
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=None)
    hub_dir.mkdir(parents=True, exist_ok=True)
    http = _make_team_client(tmp_path, skillhub=hub)
    cid, headers = _register(http, user_name="alice")
    src = tmp_path / "src"
    src.mkdir()
    (src / "SKILL.md").write_text(
        "---\nname: terraform-ops\ndescription: Provision cloud infra terraform\n"
        "---\nbody\n", encoding="utf-8")
    upload = http.post("/api/v1/team/skill_hub/upload",
                       files={"file": ("s.zip", _zip_dir(src), "application/zip")},
                       headers=headers)
    assert upload.status_code == 200
    owner = safe_dir_name("alice", cid)
    response = http.get("/api/v1/team/skill_hub/search",
                        params={"query": "terraform"}, headers=headers)
    hit = response.json()["results"][0]
    assert hit["source"] == f"上传者:{owner}"


# ── upload 后 corpus 补 embed ───────────────────────────────────

def test_upload_backfills_corpus_embedding(tmp_path):
    hub_dir = tmp_path / "hub"
    hub_dir.mkdir(parents=True, exist_ok=True)
    description = "Provision cloud infra terraform"
    client = ControlledEmbed({description: [0.3, 0.4, 0.5]})
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=client)
    http = _make_team_client(tmp_path, skillhub=hub)
    _cid, headers = _register(http, user_name="bob")
    src = tmp_path / "src"
    src.mkdir()
    (src / "SKILL.md").write_text(
        f"---\nname: terraform-ops\ndescription: {description}\n---\nbody\n",
        encoding="utf-8")
    upload = http.post("/api/v1/team/skill_hub/upload",
                       files={"file": ("s.zip", _zip_dir(src), "application/zip")},
                       headers=headers)
    assert upload.status_code == 200
    for _attempt in range(100):
        cached = EmbedStore(hub_dir / EMBED_CACHE_NAME, client).cached_vectors(
            [description])
        if cached[0] is not None:
            break
        time.sleep(0.02)
    assert cached[0] is not None  # best-effort 补齐后向量进入 corpus 缓存


def test_backfill_invalidates_search_index_built_before_vector_exists(tmp_path):
    hub_dir = tmp_path / "hub"
    _write_hub_skill(hub_dir, "a", "alpha", "gamma one")
    client = ControlledEmbed({"gamma one": [1.0, 0.0], "alpha": [1.0, 0.0]})
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=client)

    assert hub.search("alpha")[0]["semantic_rank"] is None
    assert hub.backfill_description_embedding("gamma one") is True
    for _attempt in range(100):
        if hub._search_index is None:
            break
        time.sleep(0.02)

    results = hub.search("alpha")
    assert results[0]["semantic_rank"] == 1


def test_upload_backfill_shares_configured_embedding_capacity(tmp_path):
    hub_dir = tmp_path / "hub"
    hub_dir.mkdir()
    client = ControlledEmbed({"first": [1.0, 0.0], "second": [0.0, 1.0]},
                             encode_delay=0.2)
    hub = SkillHub(enabled=True, hub_dir=hub_dir, embed_client=client,
                   search_max_embed=1)

    assert hub.backfill_description_embedding("first") is True
    assert hub.backfill_description_embedding("second") is False
