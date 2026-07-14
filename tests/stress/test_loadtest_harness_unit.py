"""Fast regression tests for the release stress-test harness itself."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import threading
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS_PATH = REPO_ROOT / "scripts" / "loadtest_300_control_plane.py"
SPEC = importlib.util.spec_from_file_location("xskill_loadtest_harness", HARNESS_PATH)
assert SPEC is not None and SPEC.loader is not None
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


class _Response:
    status_code = 200
    text = "{}"

    def json(self):
        return {"slots": []}


class _BlockedSyncClient:
    def __init__(self, release: threading.Event):
        self.release = release

    async def get(self, _path, **_kwargs):
        while not self.release.is_set():
            await asyncio.sleep(0.001)
        return _Response()


class _GatedState:
    def __init__(self):
        self.embed_release = threading.Event()
        self.phase = "unset"

    def set_embed_phase(self, phase: str, *, released: bool) -> None:
        self.phase = phase
        if released:
            self.embed_release.set()
        else:
            self.embed_release.clear()

    def snapshot(self):
        request_count = 0 if self.phase == "unset" else 1
        return {
            "embedding": {
                "request_count": request_count,
                "completed_requests": 0,
                "input_item_count": request_count,
                "active": request_count,
                "max_active": request_count,
                "requests_by_phase": ({self.phase: 1} if request_count else {}),
                "items_by_phase": ({self.phase: 1} if request_count else {}),
                "unique_inputs": request_count,
                "duplicate_input_calls": 0,
                "latency": HARNESS._latency_summary([]),
            },
        }


def test_gated_wave_releases_embedding_before_gather(monkeypatch, tmp_path) -> None:
    """A synchronous-embedding regression must fail, not deadlock the harness."""
    state = _GatedState()
    client = _BlockedSyncClient(state.embed_release)
    wave = {}
    checkpoints = []
    artifact = tmp_path / "result.json"

    def checkpoint() -> None:
        checkpoints.append(dict(wave))
        HARNESS._write_result_snapshot({"waves": {"blocked_sync": wave}}, artifact)

    async def no_probes(*_args, **_kwargs):
        return []

    monkeypatch.setattr(HARNESS, "_control_plane_probes", no_probes)

    async def scenario():
        return await HARNESS.run_sync_wave(
            client,
            phase="blocked_sync",
            client_rows=[{"client_id": "client-1"}],
            join_token="token",
            state=state,
            server_pid=-1,
            profile_workers=1,
            gated_embedding=True,
            wave=wave,
            checkpoint=checkpoint,
            sync_max_s=0.02,
        )

    result = asyncio.run(asyncio.wait_for(scenario(), timeout=0.5))
    assert state.embed_release.is_set()
    assert result["all_sync_completed_before_embedding_release"] is False
    assert result["saturation"]["pending_sync_requests"] == 1
    assert result["sync"]["statuses"] == {"200": 1}
    assert len(checkpoints) >= 3
    persisted = json.loads(artifact.read_text(encoding="utf-8"))
    assert persisted["waves"]["blocked_sync"]["sync"]["statuses"] == {"200": 1}
