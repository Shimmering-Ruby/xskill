"""冷启动一次性批量 flush 控制器。

线上路径里不引入评测用的多轮概念。冷启动只是一种用户显式请求：
``xskill rebuild`` 重置 stock 轨迹后写 request 文件，watcher 等这批轨迹重新
拆分、聚类完成，再按现有 ``ATOM_PROMOTION_THRESHOLD`` 做一次 SkillEdit 扫描。

也保留 barrier 文件给外部编排使用：如果外部系统自己知道批量导入已经结束，可
直接 touch barrier 文件触发下一轮 watcher scan 里的 flush。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_REQUEST_FILENAME = "COLD_START_REQUEST"
DEFAULT_BARRIER_FILENAME = "COLD_START_FLUSH"


def _section(config: dict | None) -> dict:
    sec = (config or {}).get("cold_start", {}) or {}
    return sec if isinstance(sec, dict) else {}


def _configured_path(sec: dict, key: str, default_base: Path, filename: str) -> Path:
    value = sec.get(key)
    return Path(value).expanduser() if value else Path(default_base) / filename


def request_path_from_config(config: dict | None, default_base: Path) -> Path:
    sec = _section(config)
    return _configured_path(
        sec, "request_path", default_base, DEFAULT_REQUEST_FILENAME,
    )


def barrier_path_from_config(config: dict | None, default_base: Path) -> Path:
    sec = _section(config)
    return _configured_path(
        sec, "barrier_path", default_base, DEFAULT_BARRIER_FILENAME,
    )


def request_cold_start_flush(config: dict | None, default_base: Path) -> Path:
    """请求 watcher 在当前重建批次处理完成后做一次 cold-start flush。"""
    path = request_path_from_config(config, default_base)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


@dataclass
class ColdStartController:
    """回答 watcher 当前是否该 hold SkillEdit，以及是否该执行一次 flush。"""

    enabled: bool = False
    explicitly_disabled: bool = False
    request_path: Path | None = None
    barrier_path: Path | None = None

    @classmethod
    def from_config(
        cls,
        config: dict | None,
        default_base: Path,
        *,
        server_mode: bool = False,
    ) -> "ColdStartController":
        """从 ``config['cold_start']`` 构造。

        ``enabled`` 未配置时，standalone 的 rebuild 默认请求 cold start，team
        server 的 rebuild 默认不请求。只有显式 ``enabled: false`` 才禁用文件
        信号；没有文件时不会 hold 正常在线 SkillEdit。
        """
        sec = _section(config)
        explicitly_disabled = (
            "enabled" in sec and not bool(sec.get("enabled"))
        )
        if "enabled" in sec:
            enabled = bool(sec.get("enabled"))
        else:
            enabled = not server_mode
        return cls(
            enabled=enabled,
            explicitly_disabled=explicitly_disabled,
            request_path=request_path_from_config(config, default_base),
            barrier_path=barrier_path_from_config(config, default_base),
        )

    @property
    def request_reached(self) -> bool:
        return (
            self.request_path is not None
            and self.request_path.exists()
        )

    @property
    def direct_barrier_reached(self) -> bool:
        return (
            self.barrier_path is not None
            and self.barrier_path.exists()
        )

    @property
    def active(self) -> bool:
        """有本次 cold-start 请求时才 hold；默认开启本身不改变在线路径。"""
        return not self.explicitly_disabled and (
            self.request_reached or self.direct_barrier_reached
        )

    def barrier_reached(self, *, pipeline_idle: bool) -> bool:
        """外部 barrier 立即触发；rebuild request 等流水线空闲后触发。"""
        if self.explicitly_disabled:
            return False
        if self.direct_barrier_reached:
            return True
        return self.request_reached and pipeline_idle

    def consume_barrier(self) -> None:
        """消费本次请求；后续 rebuild 可再写 request 文件触发下一次。"""
        for path in (self.request_path, self.barrier_path):
            if path is not None and path.exists():
                path.unlink()
