"""Generate 可选观测：只认 ``XSKILL_OTEL_ENDPOINT``。"""
from xskill.obs.tracing import endpoint, is_enabled, setup

__all__ = ["endpoint", "is_enabled", "setup"]
