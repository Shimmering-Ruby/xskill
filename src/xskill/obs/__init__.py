"""Generate 可选 OTel：只认 ``XSKILL_OTEL_ENDPOINT``，有值才往外送。"""
from xskill.obs.tracing import endpoint, is_enabled, setup, shutdown, span

__all__ = ["endpoint", "is_enabled", "setup", "shutdown", "span"]
