"""
log.py -- StreamLog 流式日志
=============================
带前缀的流式日志，方便 grep 和观测。
"""

import json
from datetime import datetime
from pathlib import Path


class StreamLog:
    """带前缀的流式日志，方便 grep 和观测"""

    def __init__(self, verbose=True):
        self.verbose = verbose
        self.events = []

    def __call__(self, msg: str, tag: str = "info"):
        entry = {"t": datetime.now().isoformat(), "tag": tag, "msg": msg}
        self.events.append(entry)
        if self.verbose:
            icon = {"step": ">", "tool": "[T]", "decision": "[D]", "git": "[G]",
                    "eval": "[E]", "error": "[!]", "ok": "[+]"}.get(tag, "  ")
            print(f"  {icon} [{tag}] {msg}", flush=True)

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.events, ensure_ascii=False, indent=2), encoding="utf-8")
