#!/usr/bin/env python3
"""Install the v0.6.28a1 wheel, migrate four-pool config, and verify it.

Copy this file to ``/tmp/x.py`` on the target host, then run:

    python3 /tmp/x.py --wheel /tmp/xskill-0.6.28a1-py3-none-any.whl
    python3 /tmp/x.py --rollback

The script never prints config values, API keys, process environments, or
dashboard credentials.  It is intentionally Linux-specific because the
pre-release observation host uses ``/proc`` for process discovery.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml


TARGET_VERSION = "0.6.28a1"
ROLLBACK_VERSION = "0.6.27"
POOL_DEFAULTS = {
    "split": {"workers": 24, "llm_weight": 6},
    "cluster": {"workers": 8, "batch_size": 8, "llm_weight": 3},
    "edit": {"workers": 4, "llm_weight": 1},
    "embed": {"workers": 4},
}


def _positive_int(value, default):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return default
    return value


def migrate_config(config):
    """Return a migrated copy without exposing or changing secret fields."""
    if not isinstance(config, dict):
        raise ValueError("config.yaml 顶层必须是 mapping")
    migrated = dict(config)

    watcher = dict(migrated.get("watcher") or {})
    legacy_batch_size = watcher.pop("cluster_batch_size", None)
    watcher.pop("max_concurrent", None)
    watcher["poll_interval"] = watcher.get("poll_interval", 5)
    migrated["watcher"] = watcher

    pools = {name: dict(values) for name, values in POOL_DEFAULTS.items()}
    pools["cluster"]["batch_size"] = _positive_int(
        legacy_batch_size,
        pools["cluster"]["batch_size"],
    )
    migrated["agent_worker"] = {"pools": pools}

    llm = dict(migrated.get("llm") or {})
    rate = dict(llm.get("rate_limit") or {})
    legacy_burst = rate.pop("burst", None)
    rate["rpm"] = _positive_int(rate.get("rpm"), 240)
    rate["request_burst"] = _positive_int(
        rate.get("request_burst"),
        _positive_int(legacy_burst, 8),
    )
    rate["max_inflight"] = _positive_int(rate.get("max_inflight"), 8)
    if "tpm" in rate:
        rate["token_burst"] = _positive_int(
            rate.get("token_burst"),
            _positive_int(legacy_burst, max(1, int(rate["tpm"]) // 6)),
        )
    llm["rate_limit"] = rate
    migrated["llm"] = llm

    llm_skill = migrated.get("llm_skill")
    if isinstance(llm_skill, dict) and isinstance(llm_skill.get("rate_limit"), dict):
        llm_skill = dict(llm_skill)
        skill_rate = dict(llm_skill["rate_limit"])
        skill_burst = skill_rate.pop("burst", None)
        if "request_burst" not in skill_rate and skill_burst is not None:
            skill_rate["request_burst"] = skill_burst
        if "tpm" in skill_rate and "token_burst" not in skill_rate:
            skill_rate["token_burst"] = _positive_int(
                skill_burst,
                max(1, int(skill_rate["tpm"]) // 6),
            )
        llm_skill["rate_limit"] = skill_rate
        migrated["llm_skill"] = llm_skill

    embedding = dict(migrated.get("embedding") or {})
    embedding_rate = dict(embedding.get("rate_limit") or {})
    embedding_rate["max_inflight"] = _positive_int(
        embedding_rate.get("max_inflight"), 4,
    )
    embedding["rate_limit"] = embedding_rate
    migrated["embedding"] = embedding
    return migrated


def _atomic_write_yaml(path, config):
    path.parent.mkdir(parents=True, exist_ok=True)
    original_mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            yaml.safe_dump(
                config,
                handle,
                allow_unicode=True,
                sort_keys=False,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, original_mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _package_version():
    try:
        return importlib.metadata.version("xskill")
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def _run_pip(*arguments):
    subprocess.run(
        [sys.executable, "-m", "pip", *arguments],
        check=True,
    )


def _prepare_rollback_wheel(backup_dir):
    backup_dir.mkdir(parents=True, exist_ok=True)
    wheels = sorted(backup_dir.glob(f"xskill-{ROLLBACK_VERSION}-*.whl"))
    if wheels:
        return wheels[-1]
    _run_pip(
        "download", "--only-binary=:all:", "--no-deps",
        "--dest", str(backup_dir), f"xskill=={ROLLBACK_VERSION}",
    )
    wheels = sorted(backup_dir.glob(f"xskill-{ROLLBACK_VERSION}-*.whl"))
    if not wheels:
        raise RuntimeError("未能准备 v0.6.27 回退 wheel")
    return wheels[-1]


def _read_cmdline(pid):
    try:
        raw = (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except OSError:
        return []
    return [part.decode("utf-8", errors="surrogateescape") for part in raw.split(b"\0") if part]


def _serve_processes():
    processes = []
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        raise RuntimeError("预发布迁移脚本需要 Linux /proc")
    for entry in proc_root.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        argv = _read_cmdline(int(entry.name))
        if "serve" not in argv:
            continue
        if not any("xskill" in part for part in argv):
            continue
        try:
            cwd = Path(os.readlink(entry / "cwd"))
            environ = {}
            for item in (entry / "environ").read_bytes().split(b"\0"):
                if b"=" not in item:
                    continue
                key, value = item.split(b"=", 1)
                environ[key.decode(errors="surrogateescape")] = value.decode(
                    errors="surrogateescape",
                )
        except OSError:
            cwd = Path.cwd()
            environ = dict(os.environ)
        processes.append({
            "pid": int(entry.name),
            "argv": argv,
            "cwd": cwd,
            "environ": environ,
        })
    return processes


def _agent_worker_pids():
    pids = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        argv = _read_cmdline(int(entry.name))
        if "agent-worker" in argv and any("xskill._workers" in part for part in argv):
            pids.append(int(entry.name))
    return pids


def _port_from_argv(argv):
    if "--port" in argv:
        index = argv.index("--port")
        if index + 1 < len(argv):
            return int(argv[index + 1])
    return 8000


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _restart_serve(captured, log_path):
    if not captured:
        raise RuntimeError("未发现正在运行的 xskill serve；未擅自改变启动参数")
    if len(captured) != 1:
        raise RuntimeError(f"发现 {len(captured)} 个 xskill serve，拒绝不明确的重启")
    old = captured[0]
    os.kill(old["pid"], signal.SIGTERM)
    deadline = time.time() + 30
    while _pid_alive(old["pid"]) and time.time() < deadline:
        time.sleep(0.2)
    if _pid_alive(old["pid"]):
        raise RuntimeError("xskill serve 在 30 秒内未响应 SIGTERM")

    # A system supervisor may already have restarted it.  If not, relaunch the
    # exact captured argv/cwd/environment without printing any environment.
    deadline = time.time() + 5
    while time.time() < deadline:
        replacements = _serve_processes()
        if replacements:
            return replacements[0]
        time.sleep(0.2)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log_handle:
        process = subprocess.Popen(
            old["argv"],
            cwd=str(old["cwd"]),
            env=old["environ"],
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return {**old, "pid": process.pid}


def _get_json(url, timeout=3):
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _verify(port, timeout=120):
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            worker_payload = _get_json(f"{base_url}/api/v1/agent-worker/status")
            watcher_payload = _get_json(f"{base_url}/api/v1/watcher/status")
            worker = worker_payload.get("stats", worker_payload)
            watcher = watcher_payload.get("stats", watcher_payload)
            pools = worker.get("pools") or {}
            if set(pools) != {"split", "cluster", "edit", "embed"}:
                raise RuntimeError("状态接口尚未报告四个池")
            for name, pool in pools.items():
                required = {
                    "workers", "queue_capacity", "running", "queued",
                    "completed", "failed", "occupancy",
                }
                if not required.issubset(pool):
                    raise RuntimeError(f"{name} 池状态字段不完整")
                if pool["queue_capacity"] != pool["workers"] * 2:
                    raise RuntimeError(f"{name} 池等待容量不是 workers × 2")
            if not worker.get("pid") or "scans" not in watcher:
                raise RuntimeError("agent-worker 或 watcher 状态尚未就绪")
            process_pids = _agent_worker_pids()
            if int(worker["pid"]) not in process_pids:
                raise RuntimeError("状态 PID 与 agent-worker 进程不一致")
            return worker
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
            last_error = error
            time.sleep(1)
    raise RuntimeError(f"服务健康检查超时: {last_error}")


def _write_manifest(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _load_manifest(path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("迁移记录格式无效")
    return payload


def install(args):
    config_path = args.config.expanduser().resolve()
    if not config_path.is_file():
        raise RuntimeError(f"配置文件不存在: {config_path}")
    wheel = args.wheel
    if wheel is None:
        matches = sorted(Path("/tmp").glob("xskill-0.6.28a1-*.whl"))
        if len(matches) != 1:
            raise RuntimeError("请用 --wheel 指定唯一的 v0.6.28a1 wheel")
        wheel = str(matches[0])

    state_dir = config_path.parent
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = state_dir / f"config.yaml.v0.6.27.bak.{timestamp}"
    rollback_dir = state_dir / "rollback" / TARGET_VERSION
    captured = _serve_processes()
    port = _port_from_argv(captured[0]["argv"]) if len(captured) == 1 else 8000

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    migrated = migrate_config(config)
    if args.dry_run:
        print("配置迁移预检通过；未写文件、未安装、未重启。")
        return

    rollback_wheel = _prepare_rollback_wheel(rollback_dir)
    shutil.copy2(config_path, backup_path)
    _atomic_write_yaml(config_path, migrated)
    _run_pip("install", "--no-deps", "--force-reinstall", wheel)
    installed = _package_version()
    if installed != args.expected_version:
        raise RuntimeError(
            f"安装版本不符: expected={args.expected_version} actual={installed}",
        )
    _write_manifest(args.manifest.expanduser(), {
        "target_version": args.expected_version,
        "rollback_version": ROLLBACK_VERSION,
        "config_path": str(config_path),
        "config_backup": str(backup_path),
        "rollback_wheel": str(rollback_wheel),
        "port": port,
        "created_at": int(time.time()),
    })
    _restart_serve(captured, state_dir / "logs" / "v0.6.28a1-restart.log")
    worker = _verify(port)
    pool_summary = ", ".join(
        f"{name}={values['workers']}"
        for name, values in worker["pools"].items()
    )
    print(f"v{installed} 已安装并通过检查；agent-worker PID={worker['pid']}；{pool_summary}")
    print("回退命令: python3 /tmp/x.py --rollback")


def rollback(args):
    manifest = _load_manifest(args.manifest.expanduser())
    config_path = Path(manifest["config_path"])
    backup_path = Path(manifest["config_backup"])
    rollback_wheel = Path(manifest["rollback_wheel"])
    if not backup_path.is_file() or not rollback_wheel.is_file():
        raise RuntimeError("回退所需的配置备份或 v0.6.27 wheel 不存在")
    captured = _serve_processes()
    shutil.copy2(backup_path, config_path)
    _run_pip("install", "--no-deps", "--force-reinstall", str(rollback_wheel))
    installed = _package_version()
    if installed != ROLLBACK_VERSION:
        raise RuntimeError(f"回退版本不符: expected={ROLLBACK_VERSION} actual={installed}")
    _restart_serve(captured, config_path.parent / "logs" / "v0.6.27-rollback.log")
    print(f"已恢复 v{installed} 和原 watcher 配置。")


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", help="v0.6.28a1 wheel 的本地路径或 pip 可识别 URL")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--expected-version", default=TARGET_VERSION)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path.home() / ".xskill" / "config.yaml",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path.home() / ".xskill" / "v0.6.28a1-migration.json",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.rollback:
            rollback(args)
        else:
            install(args)
    except Exception as error:  # deployment boundary: concise, no secret values
        print(f"失败: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
