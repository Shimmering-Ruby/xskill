"""Small four-pool configs for focused unit tests."""

from __future__ import annotations


def pool_config(
    workers: int = 2,
    *,
    batch_size: int = 8,
    split_workers: int | None = None,
    cluster_workers: int | None = None,
    edit_workers: int | None = None,
    embed_workers: int | None = None,
) -> dict:
    return {
        "split": {"workers": split_workers or workers, "llm_weight": 6},
        "cluster": {
            "workers": cluster_workers or workers,
            "batch_size": batch_size,
            "llm_weight": 3,
        },
        "edit": {"workers": edit_workers or workers, "llm_weight": 1},
        "embed": {"workers": embed_workers or workers},
    }
