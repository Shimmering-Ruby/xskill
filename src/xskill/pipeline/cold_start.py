"""冷启动一次性批量 flush 信号。

冷启动不是可配置的线上状态机，也没有多轮概念。``xskill rebuild`` 写
``~/.xskill/COLD_START``，watcher 等 rebuild 触发的轨迹重新拆分/聚类完成后，
按既有 ``ATOM_PROMOTION_THRESHOLD`` 做一次 SkillEdit 扫描并删除该文件。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


COLD_START_FILENAME = "COLD_START"


@dataclass(frozen=True)
class ColdStartSignal:
    """管理一次 cold-start flush 的文件信号。"""

    home_root: Path

    @property
    def file_path(self) -> Path:
        return self.home_root / COLD_START_FILENAME

    @property
    def exists(self) -> bool:
        return self.file_path.exists()

    def create(self) -> Path:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.touch()
        return self.file_path

    def ready_to_flush(self, *, pipeline_idle: bool) -> bool:
        return self.exists and pipeline_idle

    def consume(self) -> None:
        if self.exists:
            self.file_path.unlink()
