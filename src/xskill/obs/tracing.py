"""有 ``XSKILL_OTEL_ENDPOINT`` 就建 OTLP tracer，没有就是空壳。"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger("xskill.obs")

_STATE: dict[str, Any] = {"ready": False, "failed": False, "tracer": None}


def endpoint() -> str:
    return (os.environ.get("XSKILL_OTEL_ENDPOINT") or "").strip()


def is_enabled() -> bool:
    return bool(endpoint())


class _NullSpan:
    def set_attribute(self, *_a: object, **_k: object) -> None:
        return None

    def __enter__(self) -> _NullSpan:
        return self

    def __exit__(self, *_a: object) -> bool:
        return False


def setup() -> bool:
    if not is_enabled():
        return False
    if _STATE["ready"]:
        return True
    if _STATE["failed"]:
        return False
    try:
        _build()
    except Exception:  # noqa: BLE001 — 打点起不来不许挡 generate
        _STATE["failed"] = True
        logger.warning("XSKILL_OTEL_ENDPOINT 已设但 OpenTelemetry 起不来", exc_info=True)
        return False
    _STATE["ready"] = True
    return True


def _build() -> None:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    url = endpoint().rstrip("/")
    if not url.endswith("/v1/traces"):
        url = f"{url}/v1/traces"
    provider = TracerProvider(resource=Resource.create({"service.name": "xskill"}))
    provider.add_span_processor(SimpleSpanProcessor(_Quiet(OTLPSpanExporter(endpoint=url))))
    trace.set_tracer_provider(provider)
    _STATE["tracer"] = trace.get_tracer("xskill.generate")
    _STATE["provider"] = provider


class _Quiet:
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def export(self, spans: Any) -> Any:
        try:
            return self._inner.export(spans)
        except Exception:  # noqa: BLE001
            logger.warning("OTel 送不出去，generate 继续跑", exc_info=True)
            return 0

    def shutdown(self) -> None:
        shutdown = getattr(self._inner, "shutdown", None)
        if callable(shutdown):
            shutdown()

    def force_flush(self, timeout_millis: int = 10_000) -> bool:
        flush = getattr(self._inner, "force_flush", None)
        return bool(flush(timeout_millis)) if callable(flush) else True


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    if not setup():
        yield _NullSpan()
        return
    attrs = {key: value for key, value in attributes.items() if value not in (None, "")}
    with _STATE["tracer"].start_as_current_span(name, attributes=attrs) as current:
        yield current


def shutdown() -> None:
    if not _STATE["ready"]:
        return
    try:
        provider = _STATE.get("provider")
        if provider is not None:
            provider.force_flush(10_000)
    except Exception:  # noqa: BLE001
        logger.debug("otel flush failed", exc_info=True)


def reset() -> None:
    """单测用：清掉进程里的 tracer 状态。"""
    _STATE["ready"] = False
    _STATE["failed"] = False
    _STATE["tracer"] = None
    _STATE.pop("provider", None)


def clip(text: Any, limit: int = 2000) -> str:
    raw = str(text or "")
    if len(raw) <= limit:
        return raw
    return raw[:limit] + "…"
