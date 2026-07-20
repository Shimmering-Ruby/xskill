"""子进程启动辅助：统一抑制 Windows 控制台黑窗。"""
from __future__ import annotations

import subprocess
import sys


def windowless_subprocess_kwargs(
    *, extra_creationflags: int = 0,
) -> dict[str, int]:
    """返回可展开进 ``subprocess.run/Popen`` 的 kwargs，让控制台子进程在
    Windows 上不弹黑窗；非 Windows 返回空 dict。

    Windows 下父进程无 console 附着时（服务 / pythonw / detached daemon），
    每次起 ``cmd`` / ``git`` / ``pip`` 等控制台程序都会闪一个可见窗口。
    """
    if sys.platform == "win32":
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return {"creationflags": no_window | extra_creationflags}
    return {}
