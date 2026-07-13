"""Process-wide guard for the SQLite connection-open path."""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, TypeVar


_ConnectionT = TypeVar("_ConnectionT")
_CONNECT_LOCK = threading.Lock()


def connect_with_lock(
    connect: Callable[..., _ConnectionT],
    /,
    *args: Any,
    **kwargs: Any,
) -> _ConnectionT:
    """Call ``sqlite3.connect`` under the process-wide open-only lock.

    SQLite's Unix VFS uses a process-global mutex while opening/reusing file
    descriptors.  A Python lock makes simultaneous callers wait without
    starving the interpreter.  It is deliberately released before the
    returned connection executes any SQL.
    """
    with _CONNECT_LOCK:
        return connect(*args, **kwargs)
