"""有 ``XSKILL_OTEL_ENDPOINT`` 就 register Phoenix + OpenAI 自动打点。"""
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
        from opentelemetry import trace
        from openinference.instrumentation.openai import OpenAIInstrumentor
        from phoenix.otel import register

        url = endpoint().rstrip("/")
        if not url.endswith("/v1/traces"):
            url = f"{url}/v1/traces"
        provider = register(
            project_name="xskill-generate",
            endpoint=url,
            protocol="http/protobuf",
        )
        OpenAIInstrumentor().instrument(tracer_provider=provider)
        _STATE["tracer"] = trace.get_tracer("xskill.generate")
        _STATE["provider"] = provider
    except Exception:  # noqa: BLE001 — 打点起不来不许挡 generate
        _STATE["failed"] = True
        logger.warning("XSKILL_OTEL_ENDPOINT 已设但探针起不来", exc_info=True)
        return False
    _STATE["ready"] = True
    return True


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
        flush = getattr(provider, "force_flush", None)
        if callable(flush):
            flush(10_000)
    except Exception:  # noqa: BLE001
        logger.debug("otel flush failed", exc_info=True)


def reset() -> None:
    _STATE["ready"] = False
    _STATE["failed"] = False
    _STATE["tracer"] = None
    _STATE.pop("provider", None)


def clip(text: Any, limit: int = 2000) -> str:
    raw = str(text or "")
    if len(raw) <= limit:
        return raw
    return raw[:limit] + "…"
