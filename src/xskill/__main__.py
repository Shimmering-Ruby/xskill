"""python -m xskill 入口。

Windows 常驻任务用 ``pythonw.exe -m xskill connect --foreground`` 拉起——
没有 console 窗口的 pythonw 下没法依赖 console_scripts 的 xskill.exe（PATH
可能不含 Scripts 目录），``-m xskill`` 走当前解释器最稳。
"""
from __future__ import annotations

import sys

from xskill.cli import main

if __name__ == "__main__":
    sys.exit(main() or 0)
