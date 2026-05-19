#!/usr/bin/env python3
"""rig 公共探针——actions.sh / assertions.sh 调用，把 HTTP 探测和 API 断言集中到一处。

用法：
    probe.py wait_ready <port> <timeout_s>           # 等 /api/v1/health 200
    probe.py assert_ecosystem_present <port> <name>  # 断言 registry 里含某生态
    probe.py assert_path_exists <abs_path>           # 断言路径存在
    probe.py assert_path_glob <pattern>              # 断言 glob 至少匹配一个文件
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx


def wait_ready(port: int, timeout_s: float) -> int:
    url = f"http://127.0.0.1:{port}/api/v1/health"
    deadline = time.time() + timeout_s
    last_err = "n/a"
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=2.0)
            if r.status_code == 200:
                return 0
            last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = repr(e)
        time.sleep(0.5)
    print(f"[probe] daemon NOT ready in {timeout_s}s (last={last_err})", file=sys.stderr)
    return 1


def assert_ecosystem_present(port: int, ecosystem: str) -> int:
    """通过 /api/v1/registry/dirs 查 watch_dirs 里是否含指定 ecosystem 标签的条目。

    server.py 的真实端点是 /api/v1/registry/dirs，返回 {"dirs": [...], "count": N}。
    """
    url = f"http://127.0.0.1:{port}/api/v1/registry/dirs"
    try:
        r = httpx.get(url, timeout=5.0)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[probe] registry/dirs call failed: {e}", file=sys.stderr)
        return 1
    items = data.get("dirs") if isinstance(data, dict) else data
    if not isinstance(items, list):
        print(f"[probe] unexpected registry/dirs payload: {data!r}", file=sys.stderr)
        return 1
    for item in items:
        if item.get("ecosystem") == ecosystem:
            print(f"[probe] ✓ ecosystem '{ecosystem}' present (path={item.get('path')})")
            return 0
    print(f"[probe] ✗ ecosystem '{ecosystem}' NOT found in registry; got: {items}", file=sys.stderr)
    return 1


def assert_path_exists(p: str) -> int:
    if Path(p).exists():
        print(f"[probe] ✓ path exists: {p}")
        return 0
    print(f"[probe] ✗ path missing: {p}", file=sys.stderr)
    return 1


def assert_path_glob(pattern: str) -> int:
    matches = list(Path("/").glob(pattern.lstrip("/")))
    if matches:
        print(f"[probe] ✓ glob matched {len(matches)} file(s): {pattern}")
        return 0
    print(f"[probe] ✗ glob matched nothing: {pattern}", file=sys.stderr)
    return 1


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    cmd = sys.argv[1]
    if cmd == "wait_ready":
        return wait_ready(int(sys.argv[2]), float(sys.argv[3]))
    if cmd == "assert_ecosystem_present":
        return assert_ecosystem_present(int(sys.argv[2]), sys.argv[3])
    if cmd == "assert_path_exists":
        return assert_path_exists(sys.argv[2])
    if cmd == "assert_path_glob":
        return assert_path_glob(sys.argv[2])
    print(f"[probe] unknown cmd: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
