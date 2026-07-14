"""复现/度量 SQLite 连接 finalize convoy。

模拟 embedding 高峰：N 个写线程持续打 ``record_usage``（每次 LLM/embedding
调用的真实路径），同时一个"看板受害者"线程反复调 ``usage_summary`` 量延迟，
并采样 ``/proc/self/task/*/wchan`` 统计 park 在 futex 上的线程数。

用法（对比改造前后，用 PYTHONPATH 指向不同源码树跑两次）::

    PYTHONPATH=src python3 scripts/bench/bench_sqlite_finalize_convoy.py
"""
from __future__ import annotations

import json
import os
import statistics
import tempfile
import threading
import time
from pathlib import Path

WRITER_THREADS = 16
DURATION_SECONDS = 10.0


def _wchan_futex_ratio() -> tuple[int, int]:
    task_dir = Path(f"/proc/{os.getpid()}/task")
    parked = total = 0
    for task in task_dir.iterdir():
        try:
            channel = (task / "wchan").read_text()
        except OSError:
            continue
        total += 1
        if "futex" in channel:
            parked += 1
    return parked, total


def main() -> None:
    from xskill.pipeline.registry import record_usage, usage_summary

    with tempfile.TemporaryDirectory(prefix="xskill-bench-") as tmp:
        db_path = Path(tmp) / "registry.db"
        stop = threading.Event()
        write_counts = [0] * WRITER_THREADS

        def writer(index: int) -> None:
            while not stop.is_set():
                record_usage(step="bench", model="m", prompt=10, completion=10,
                             total=20, cost_usd=0.0, price_source="config",
                             db_path=db_path)
                write_counts[index] += 1

        victim_latencies: list[float] = []
        futex_samples: list[tuple[int, int]] = []

        def victim() -> None:
            while not stop.is_set():
                started = time.perf_counter()
                usage_summary(db_path=db_path)
                victim_latencies.append(time.perf_counter() - started)
                futex_samples.append(_wchan_futex_ratio())
                time.sleep(0.05)

        threads = [threading.Thread(target=writer, args=(i,), daemon=True)
                   for i in range(WRITER_THREADS)]
        threads.append(threading.Thread(target=victim, daemon=True))
        for thread in threads:
            thread.start()
        time.sleep(DURATION_SECONDS)
        stop.set()
        for thread in threads:
            thread.join(timeout=10)

        ordered = sorted(victim_latencies)
        peak_parked = max(sample[0] for sample in futex_samples)
        print(json.dumps({
            "writer_threads": WRITER_THREADS,
            "duration_s": DURATION_SECONDS,
            "record_usage_ops": sum(write_counts),
            "record_usage_ops_per_s": round(sum(write_counts) / DURATION_SECONDS),
            "victim_reads": len(ordered),
            "victim_p50_ms": round(statistics.median(ordered) * 1000, 2),
            "victim_p95_ms": round(ordered[int(len(ordered) * 0.95) - 1] * 1000, 2),
            "victim_max_ms": round(ordered[-1] * 1000, 2),
            "peak_futex_parked_threads": peak_parked,
            "sampled_threads": futex_samples[-1][1],
        }, indent=2))


if __name__ == "__main__":
    main()
