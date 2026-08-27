"""有 ``XSKILL_OTEL_ENDPOINT`` 就 register + OpenAIInstrumentor，否则什么都不做。"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("xskill.obs")

_READY = False
_FAILED = False


def endpoint() -> str:
    return (os.environ.get("XSKILL_OTEL_ENDPOINT") or "").strip()


def is_enabled() -> bool:
    return bool(endpoint())


def setup() -> bool:
    """进程里只做一次。没 endpoint 或包没装，generate 照跑。"""
    global _READY, _FAILED
    if not is_enabled():
        return False
    if _READY:
        return True
    if _FAILED:
        return False
    try:
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
    except Exception:  # noqa: BLE001
        _FAILED = True
        logger.warning("XSKILL_OTEL_ENDPOINT 已设但探针起不来", exc_info=True)
        return False
    _READY = True
    return True


def reset() -> None:
    global _READY, _FAILED
    _READY = False
    _FAILED = False
