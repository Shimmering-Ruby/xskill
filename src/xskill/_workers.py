"""内部短命子进程 worker(**非用户 CLI**)。

watcher / 画像重计算拆成短命子进程:web 进程的 ``IntervalSubprocessScheduler`` 用
``[sys.executable, "-m", "xskill._workers", <kind>]`` spawn 一个全新解释器进程,跑一轮
即退,GIL 与 web 事件循环彻底隔离。

这些是**内部管道**,刻意不注册进 ``xskill`` 用户 CLI(``cli.build_parser``)——用户
``xskill --help`` 看不到它们。调度器直接调本模块的 SDK 函数(``run_sweep_once`` /
``run_profile_refresh_once``),或经 ``python -m xskill._workers`` 入口。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger("xskill._workers")


def run_sweep_once(*, server: bool = False, home: str | None = None) -> int:
    """跑一轮 watcher sweep(采集→拆分→聚类→灰度)即退,状态落 watcher_status.json。

    非 server 先对本机各生态一次性入库(ingest_detected_ecosystems_once),再
    ``build_watcher`` + ``run_once_and_drain``(一轮 = daemon 一个 poll)。多阶段流水线
    靠调度器反复 spawn 逐轮推进。team_server 模式跳过本机生态采集。
    """
    from xskill.config import XSKILL_HOME, get_skill_dir, load_config
    from xskill.pipeline.watcher_factory import (
        build_watcher, ingest_detected_ecosystems_once,
    )
    from xskill.utils.status_file import WATCHER_STATUS_FILE, write_status_file

    config = load_config()
    home_root = Path(home).expanduser().resolve() if home else Path.home()
    status_path = XSKILL_HOME / WATCHER_STATUS_FILE
    watcher = None
    try:
        if not server:
            ingest_detected_ecosystems_once(config, home_root, get_skill_dir())
        watcher = build_watcher(config, home_root=home_root, server_mode=server)
        watcher.run_once_and_drain()
        write_status_file(status_path, watcher.stats, ok=True)
        return 0
    except Exception as exc:  # noqa: BLE001 — 顶层任务边界,落状态文件+日志后报错
        logger.exception("sweep once failed")
        write_status_file(status_path, watcher.stats if watcher is not None else {},
                          ok=False, error=str(exc))
        return 1


def run_profile_refresh_once() -> int:
    """遍历所有 client 重算画像落库即退,状态落 profile_refresh_status.json。

    复用 ProfileRefreshService(短命形态:批量提交所有 client → wait_idle → stop),
    含散点物化子系统,进程退出即销毁,不留常驻线程。``update_user_interest`` 自带
    revision 未变早退,冷启动全量跑一遍也只对变化的 client 真算增量批量 embedding。
    """
    from xskill.config import XSKILL_HOME, load_config, profile_refresh_config
    from xskill.team.server.engine_factory import build_recommend_engine
    from xskill.team.server.profile_refresh import ProfileRefreshService
    from xskill.utils.status_file import PROFILE_STATUS_FILE, write_status_file

    config = load_config()
    pr_cfg = profile_refresh_config(config)
    status_path = XSKILL_HOME / PROFILE_STATUS_FILE
    service = None
    try:
        engine = build_recommend_engine(config)
        client_ids = [row["client_id"] for row in engine.client_registry.list()]
        # 批量任务:settle_delay=0 立即算(settle 是给在线 sync 突发让路用的,批量无此需求)。
        service = ProfileRefreshService(
            engine, workers=pr_cfg["workers"], queue_size=pr_cfg["queue_size"],
            settle_delay=0, autostart=True,
        )
        for client_id in client_ids:
            service.request(client_id)
        service.wait_idle()
        metrics = dict(service.metrics)
        metrics["clients"] = len(client_ids)
        write_status_file(status_path, metrics, ok=True)
        return 0
    except Exception as exc:  # noqa: BLE001 — 顶层任务边界,落状态文件+日志后报错
        logger.exception("profile refresh once failed")
        write_status_file(status_path, {}, ok=False, error=str(exc))
        return 1
    finally:
        if service is not None:
            service.stop(timeout=pr_cfg["shutdown_timeout"])


def main(argv: list[str] | None = None) -> int:
    """``python -m xskill._workers <kind>`` 入口(供调度器 spawn)。"""
    import argparse

    from xskill.config import get_logs_dir
    from xskill.utils.logging import configure_logging

    parser = argparse.ArgumentParser(prog="xskill._workers")
    sub = parser.add_subparsers(dest="kind", required=True)
    p_sweep = sub.add_parser("sweep")
    p_sweep.add_argument("--server", action="store_true")
    p_sweep.add_argument("--home", default=None)
    sub.add_parser("profile-refresh")
    args = parser.parse_args(argv)

    configure_logging(get_logs_dir(), debug=False, quiet=False, stdout=True)
    if args.kind == "sweep":
        return run_sweep_once(server=args.server, home=args.home)
    return run_profile_refresh_once()


if __name__ == "__main__":
    sys.exit(main())
