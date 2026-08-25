"""obs/tracing.py —— 可选 OpenTelemetry 埋点，默认整条关掉
========================================================

产品默认不开：``XSKILL_OTEL`` 没设时 :func:`span` 返回一个空壳
context manager，:func:`setup` 直接返回 False，import 也不会去碰
opentelemetry。装不装 ``xskill[obs]`` 都不影响正常跑。

开关（环境变量）：

``XSKILL_OTEL``               ``1`` / ``true`` 打开
``XSKILL_OTEL_JOB``          这趟实验的 job 名，落进 span 属性和 features.json
``XSKILL_OTEL_SESSION``      Phoenix session.id；缺省跟 job 名走
``XSKILL_OTEL_OUT``          输出目录：features.json 与 spans.jsonl
``XSKILL_OTEL_ENDPOINT``     OTLP HTTP 接收端；缺省读 ``PHOENIX_COLLECTOR_ENDPOINT``
``XSKILL_OTEL_PROJECT``      Phoenix 项目名（默认 ``xskill-generate``）
``XSKILL_OTEL_CONSOLE``      ``1`` 时同时打 stdout，排查埋点自己用
``XSKILL_OTEL_CAPTURE_CONTENT`` ``1`` 时记截断后的提示词与回答正文

为什么给 Phoenix 打 OTLP 而不是接 openinference 的自动埋点：自动埋点跟着
agno 版本走，而这里要量的是 xskill 自己的东西——``context.compact`` 到底
触发了几次、``read_file`` 打在哪条轨迹上。手写 span 的名字和属性稳定，
跨 agno 版本不漂。

隐私：属性只记工具名、计数、轨迹 id、路径 basename。提示词正文要显式开
``XSKILL_OTEL_CAPTURE_CONTENT`` 才记，且截断。任何情况都不记 API key。
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger("xskill.obs")

# OpenInference（Phoenix 的语义约定）用这个属性决定一条 span 画成什么。
SPAN_KIND = "openinference.span.kind"
KIND_AGENT = "AGENT"
KIND_CHAIN = "CHAIN"
KIND_LLM = "LLM"
KIND_TOOL = "TOOL"

_DEFAULT_PROJECT = "xskill-generate"
_CONTENT_MAX_CHARS = 4000

_SETUP_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "ready": False, "tracer": None, "failed": False, "provider": None,
}


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def is_enabled() -> bool:
    """obs 层总开关。关掉时所有埋点是空操作。"""
    return _truthy(os.environ.get("XSKILL_OTEL"))


def capture_content() -> bool:
    return is_enabled() and _truthy(os.environ.get("XSKILL_OTEL_CAPTURE_CONTENT"))


def job_name() -> str:
    return (os.environ.get("XSKILL_OTEL_JOB") or "unnamed").strip() or "unnamed"


def project_name() -> str:
    return (
        os.environ.get("XSKILL_OTEL_PROJECT") or _DEFAULT_PROJECT
    ).strip() or _DEFAULT_PROJECT


def session_id() -> str:
    """Phoenix Sessions 视图按 ``session.id`` 归组；缺省就是 job 名。"""
    return (
        os.environ.get("XSKILL_OTEL_SESSION") or job_name()
    ).strip() or job_name()


def out_dir() -> Path | None:
    raw = (os.environ.get("XSKILL_OTEL_OUT") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    path.mkdir(parents=True, exist_ok=True)
    return path


def clip(text: Any, limit: int = _CONTENT_MAX_CHARS) -> str:
    """截断长文本，并标出原长度——省得看的人以为模型只说了这么点。"""
    body = str(text or "")
    if len(body) <= limit:
        return body
    return f"{body[:limit]}\n...[truncated, {len(body):,} chars total]"


class _JsonlSpanExporter:
    """把结束的 span 逐条追加成 JSON Lines。

    留一份本地文件是为了不依赖 Phoenix 活着：容器跑完就退，面板可能还没起，
    有 spans.jsonl 就能事后重画瀑布。
    """

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, spans):  # noqa: ANN001 — OTel SDK 接口
        from opentelemetry.sdk.trace.export import SpanExportResult
        rows = []
        for sp in spans:
            ctx = sp.get_span_context()
            parent = getattr(sp, "parent", None)
            rows.append({
                "name": sp.name,
                "span_id": f"{ctx.span_id:016x}",
                "trace_id": f"{ctx.trace_id:032x}",
                "parent_span_id": (
                    f"{parent.span_id:016x}" if parent is not None else None
                ),
                "start_ns": sp.start_time,
                "end_ns": sp.end_time,
                "seconds": (
                    round((sp.end_time - sp.start_time) / 1e9, 6)
                    if sp.end_time and sp.start_time else None
                ),
                "status": str(
                    getattr(getattr(sp, "status", None), "status_code", "")
                ),
                "attributes": dict(sp.attributes or {}),
            })
        try:
            with self._lock, self._path.open("a", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(
                        json.dumps(row, ensure_ascii=False, default=str) + "\n"
                    )
        except OSError:
            logger.debug("span jsonl export failed", exc_info=True)
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        del timeout_millis
        return True


def setup() -> bool:
    """建 tracer provider。幂等；缺依赖或建不起来就退回无埋点。

    返回 True 表示 span 真的会被记录。
    """
    if not is_enabled():
        return False
    with _SETUP_LOCK:
        if _STATE["ready"]:
            return True
        if _STATE["failed"]:
            return False
        try:
            _build_provider()
        except Exception:  # noqa: BLE001 — 埋点起不来不许影响正常跑
            _STATE["failed"] = True
            logger.warning(
                "XSKILL_OTEL 已开但 OpenTelemetry 起不来，本趟不记 span"
                "（装 'xskill[obs]' 可修）",
                exc_info=True,
            )
            return False
        _STATE["ready"] = True
        return True


class _LoggedExporter:
    """给 OTLP exporter 包一层：失败要看得见，不能只 debug。"""

    def __init__(self, inner, label: str):
        self._inner = inner
        self._label = label

    def export(self, spans):  # noqa: ANN001
        try:
            result = self._inner.export(spans)
        except Exception:  # noqa: BLE001
            logger.warning("OTel export %s 抛错（%s spans）", self._label, len(spans), exc_info=True)
            raise
        ok = str(result)
        if "FAILURE" in ok:
            logger.warning("OTel export %s 失败：%s（%s spans）", self._label, result, len(spans))
        else:
            logger.debug("OTel export %s 成功（%s spans）", self._label, len(spans))
        return result

    def shutdown(self) -> None:
        shutdown = getattr(self._inner, "shutdown", None)
        if callable(shutdown):
            shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        flush = getattr(self._inner, "force_flush", None)
        if callable(flush):
            return bool(flush(timeout_millis))
        return True


def _build_provider() -> None:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        ConsoleSpanExporter,
        SimpleSpanProcessor,
    )

    project = project_name()
    sid = session_id()
    resource = Resource.create({
        "service.name": "xskill",
        # Phoenix 按这个资源属性分项目；同一个面板可以并排放多轮实验。
        "openinference.project.name": project,
        "xskill.job": job_name(),
        # Sessions 视图认这两个；只写 job 名不够，面板会把 trace 摊成未分组。
        "session.id": sid,
        "openinference.session.id": sid,
    })
    provider = TracerProvider(resource=resource)
    _STATE["provider"] = provider

    endpoint = (
        os.environ.get("XSKILL_OTEL_ENDPOINT")
        or os.environ.get("PHOENIX_COLLECTOR_ENDPOINT")
        or ""
    ).strip()
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        url = endpoint.rstrip("/")
        if not url.endswith("/v1/traces"):
            url = f"{url}/v1/traces"
        # SimpleSpanProcessor：容器 --rm 一退进程就没了，批量导出经常来不及冲。
        # 本机已经跑通的 standalone demo 也是一结束就送。
        provider.add_span_processor(
            SimpleSpanProcessor(
                _LoggedExporter(OTLPSpanExporter(endpoint=url), url)
            )
        )
        logger.info("OTel spans → %s (project=%s session=%s)", url, project, sid)

    output = out_dir()
    if output is not None:
        # SimpleSpanProcessor：span 一结束就落盘，跑一半被打断也留得下来。
        provider.add_span_processor(
            SimpleSpanProcessor(_JsonlSpanExporter(output / "spans.jsonl"))
        )

    if _truthy(os.environ.get("XSKILL_OTEL_CONSOLE")):
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    _STATE["tracer"] = trace.get_tracer("xskill.obs")
    atexit.register(shutdown)


def shutdown() -> None:
    """把还在批里的 span 冲出去。容器跑完立刻退，不冲就丢。"""
    if not _STATE["ready"]:
        return
    try:
        provider = _STATE.get("provider")
        if provider is None:
            from opentelemetry import trace
            provider = trace.get_tracer_provider()
        flush = getattr(provider, "force_flush", None)
        if callable(flush):
            flush(10_000)
    except Exception:  # noqa: BLE001
        logger.warning("otel flush failed", exc_info=True)


class _NullSpan:
    """关掉埋点时顶替 span 的空壳，调用方不用到处判空。"""

    def set_attribute(self, key: str, value: Any) -> None:
        del key, value

    def set_attributes(self, attributes: dict) -> None:
        del attributes

    def record_exception(self, exc: BaseException) -> None:
        del exc


_NULL_SPAN = _NullSpan()


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """``with span("tool.read_file", **attrs) as sp:``

    关掉埋点（或 OTel 起不来）时进的是空壳，几乎零开销。

    只兜 span 自己建不起来的情况。被包住的代码抛出的异常照原样往外走
    ——OTel 会把它记成 span 的 error 状态，这正是要看的东西。
    """
    if not setup():
        yield _NULL_SPAN
        return
    tracer = _STATE["tracer"]
    if tracer is None:
        yield _NULL_SPAN
        return
    clean = {k: v for k, v in attributes.items() if v is not None}
    clean.setdefault("xskill.job", job_name())
    sid = session_id()
    clean.setdefault("session.id", sid)
    clean.setdefault("openinference.session.id", sid)
    try:
        scope = tracer.start_as_current_span(name, attributes=clean)
    except Exception:  # noqa: BLE001 — 埋点建不起来就当没开
        logger.debug("otel span %s could not start", name, exc_info=True)
        yield _NULL_SPAN
        return
    with scope as sp:
        yield sp
