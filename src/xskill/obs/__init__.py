"""obs —— generate 行为观测层（OpenTelemetry + Phoenix + 特征 JSON）
==================================================================

一句话：给 ``xskill generate`` 这趟跑装个可选的行为记录仪，量出
"compact 了几次、工具打了多少下、读了哪些轨迹"，默认整条关掉。

为什么要有这层：generate 的提示词和工具要调优，但改之前得先说得出当前
版本在干什么。日志（``agent_trace``）是给人读的连续文本，数不出次数；
usage 记的是 token 和钱，看不见"读完就 compact"这种行为模式。这里补的
是可聚合的那一份。

用法（产品代码里）::

    from xskill import obs

    with obs.agent_run("generate", job="baseline-01"):
        agent.run(user_msg)

用法（实验脚本里）：设 ``XSKILL_OTEL=1``、``XSKILL_OTEL_JOB=<job 名>``、
``XSKILL_OTEL_OUT=<输出目录>``，跑完目录里就有 ``features.json`` 和
``spans.jsonl``；再给 ``XSKILL_OTEL_ENDPOINT`` 指到 Phoenix 就能看瀑布。

关掉时的开销：几个环境变量读取加一个空壳 context manager。不 import
opentelemetry，不建 provider，不写文件。
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

from xskill.obs.features import (
    FeatureCollector,
    collector,
    features_path,
    reset_collector,
    traj_id_from_path,
)
from xskill.obs.tracing import (
    KIND_AGENT,
    KIND_CHAIN,
    KIND_LLM,
    KIND_TOOL,
    SPAN_KIND,
    capture_content,
    clip,
    is_enabled,
    job_name,
    out_dir,
    project_name,
    session_id,
    setup,
    shutdown,
    span,
)

logger = logging.getLogger("xskill.obs")

__all__ = [
    "FeatureCollector",
    "KIND_AGENT",
    "KIND_CHAIN",
    "KIND_LLM",
    "KIND_TOOL",
    "SPAN_KIND",
    "agent_run",
    "capture_content",
    "clip",
    "collector",
    "dump_features",
    "features_path",
    "is_enabled",
    "job_name",
    "out_dir",
    "project_name",
    "reset_collector",
    "session_id",
    "setup",
    "shutdown",
    "span",
    "traj_id_from_path",
]


def dump_features() -> None:
    """把这趟的特征写到 ``XSKILL_OTEL_OUT/features.json``。没配目录就不写。"""
    if not is_enabled():
        return
    target = features_path()
    if target is None:
        return
    try:
        collector().dump(target)
        logger.info("generate 行为特征已写入 %s", target)
    except OSError:
        logger.warning("特征 JSON 写盘失败: %s", target, exc_info=True)


@contextmanager
def agent_run(agent: str, *, job: str | None = None, **attributes) -> Iterator[Any]:
    """一次 ``agent.run()`` 的最外层：根 span + 特征收集 + 落盘。

    关掉埋点时什么都不做，只把 body 跑完，yield ``None``。
    打开时 yield 根 span，方便调用方补 ``input.value`` / ``output.value``。
    """
    if not is_enabled():
        yield None
        return
    name = job or job_name()
    features = collector()
    features.start(job=name, agent=agent)
    try:
        sid = session_id()
        with span(
            f"{agent}.run",
            **{
                SPAN_KIND: KIND_AGENT,
                "xskill.agent": agent,
                "session.id": sid,
                "openinference.session.id": sid,
                **attributes,
            },
        ) as root:
            try:
                yield root
            except BaseException as exc:
                features.finish(error=f"{type(exc).__name__}: {exc}")
                raise
            features.finish()
            snapshot = features.as_dict()
            root.set_attributes({
                "xskill.llm_rounds": snapshot["llm_rounds"],
                "xskill.compact_count": snapshot["compact_count"],
                "xskill.tool_call_total": snapshot["tool_call_total"],
                "xskill.read_traj_count": snapshot["read_traj_count"],
            })
    finally:
        dump_features()
        shutdown()
