"""
sandbox/registry.py — 沙箱注册表
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import Sandbox

_REGISTRY: dict[str, type[Sandbox]] = {}


def register(name: str):
    """装饰器: @register("swe_smith")"""
    def wrapper(cls):
        _REGISTRY[name] = cls
        return cls
    return wrapper


def get_sandbox(name: str, **kwargs) -> Sandbox:
    """按名称获取沙箱实例"""
    if name not in _REGISTRY:
        available = list(_REGISTRY.keys()) or ["(none)"]
        raise ValueError(f"未知沙箱: {name}。可用: {available}")
    return _REGISTRY[name](**kwargs)


def available_sandboxes() -> list[str]:
    return list(_REGISTRY.keys())
