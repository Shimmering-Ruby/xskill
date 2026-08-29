"""OfficeQA Full 的公开 UID manifest 与来源元数据回归测试。"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "officeqa" / "manifests" / "officeqa_full.json"


def _load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_officeqa_full_manifest_is_complete_and_contains_no_gated_payload():
    manifest = _load_manifest()
    samples = manifest["samples"]

    assert manifest["benchmark"] == "officeqa_full"
    assert manifest["sample_count"] == len(samples) == 246
    assert manifest["difficulty_counts"] == {"easy": 113, "hard": 133}
    assert Counter(sample["difficulty"] for sample in samples) == {
        "easy": 113,
        "hard": 133,
    }
    assert samples == sorted(samples, key=lambda sample: sample["uid"])
    assert len({sample["uid"] for sample in samples}) == len(samples)
    assert all(set(sample) == {"uid", "difficulty"} for sample in samples)


def test_officeqa_full_manifest_provenance_is_pinned():
    manifest = _load_manifest()
    source = manifest["source"]
    scorer = manifest["scorer"]

    assert source["revision"] == "8ecbf18d3833daf4750a903d14963e4c4c1d4cd8"
    assert source["file"] == "officeqa_full.csv"
    assert source["git_blob_sha1"] == "b9edb082f3143783634b5efc8c6258055a281b1e"
    assert source["sha256"] == (
        "b0b270d15acdd04dcdc6ca389f089010ffe2b8453dbb400343229ea73b66c6d7"
    )
    assert scorer["commit"] == "7b9a3c154ef9fb40215bb67934afc43e6799de16"
    assert scorer["sha256"] == (
        "0d91698c87df6d889339aac36f63ae0966607f169890b0bf8b472b26bfe8138f"
    )
    assert scorer["tolerance"] == 0.0

    canonical = json.dumps(
        manifest["samples"],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    assert hashlib.sha256(canonical).hexdigest() == manifest["samples_sha256"]


def test_readme_does_not_call_the_historical_subset_official():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "OfficeQA 使用官方划分的 1/4 子集" not in readme
    assert "OfficeQA 上游没有“官方 1/4”划分" in readme
