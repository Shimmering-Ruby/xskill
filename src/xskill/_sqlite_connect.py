"""Serialize SQLite calls that can deadlock with CPython's GIL.

CPython releases the GIL in parts of SQLite's Unix VFS and shared-memory
code.  A second thread can otherwise enter ``close()`` while holding the GIL,
wait for a SQLite mutex, and deadlock with the first thread while it waits to
reacquire the GIL.  Connections created here therefore put every SQLite C
call behind one process-wide Python lock.

Write transactions keep ownership of that gate from the statement that starts
a transaction through ``commit()``, ``rollback()`` or ``close()``.  Without
that ownership, another writer could enter SQLite while it waits for the first
writer's uncommitted database lock, preventing the first writer from entering
``commit()``.
"""
from __future__ import annotations

import sqlite3
import threading
import weakref
from collections.abc import Callable, Iterator
from typing import Any, TypeVar


_ConnectionT = TypeVar("_ConnectionT")


class _SQLiteCallGate:
    """Serialize C calls while allowing the active connection to finish.

    Transaction ownership belongs to connection objects, not threads.  That
    distinction supports ``check_same_thread=False`` and prevents a reused
    worker thread from entering an unrelated connection merely because it
    happened to start the current transaction.  Calls made recursively by a
    SQLite callback are allowed on the executing thread.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.Lock())
        self._executing_thread: int | None = None
        self._operation_depth = 0
        self._transaction_tokens: dict[int, weakref.ReferenceType[Any]] = {}

    def _has_transaction(self, token: Any) -> bool:
        reference = self._transaction_tokens.get(id(token))
        return reference is not None and reference() is token

    def _remember_transaction(self, token: Any) -> None:
        token_id = id(token)

        def finalized(reference: weakref.ReferenceType[Any]) -> None:
            with self._condition:
                if self._transaction_tokens.get(token_id) is reference:
                    self._transaction_tokens.pop(token_id, None)
                    self._condition.notify_all()

        self._transaction_tokens[token_id] = weakref.ref(token, finalized)

    def _forget_transaction(self, token: Any) -> bool:
        token_id = id(token)
        reference = self._transaction_tokens.get(token_id)
        if reference is None or reference() is not token:
            return False
        self._transaction_tokens.pop(token_id, None)
        return True

    def enter(self, token: Any) -> None:
        thread_id = threading.get_ident()
        with self._condition:
            while True:
                recursive = self._executing_thread == thread_id
                operation_available = self._executing_thread is None or recursive
                transaction_available = (
                    not self._transaction_tokens
                    or self._has_transaction(token)
                    or recursive
                )
                if operation_available and transaction_available:
                    if self._executing_thread is None:
                        self._executing_thread = thread_id
                    self._operation_depth += 1
                    return
                self._condition.wait()

    def leave(self, token: Any, *, active_transaction: bool) -> None:
        thread_id = threading.get_ident()
        with self._condition:
            if self._executing_thread != thread_id or self._operation_depth <= 0:
                raise RuntimeError("SQLite call gate released by a non-owner")
            if active_transaction:
                self._remember_transaction(token)
            else:
                self._forget_transaction(token)
            self._operation_depth -= 1
            if self._operation_depth == 0:
                self._executing_thread = None
                self._condition.notify_all()

    def abandon(self, token: Any) -> None:
        """Forget a transaction whose unreachable connection is finalized."""
        with self._condition:
            if self._forget_transaction(token):
                self._condition.notify_all()


_SQLITE_CALL_GATE = _SQLiteCallGate()


class _LockedIterator(Iterator[Any]):
    """Guard lazy SQLite-backed iterators such as ``Connection.iterdump``."""

    def __init__(self, connection: "_LockedConnection", iterator: Iterator[Any]):
        self._connection = connection
        self._iterator = iterator

    def __iter__(self) -> "_LockedIterator":
        return self

    def __next__(self) -> Any:
        return self._connection._sqlite_call(lambda: next(self._iterator))


class _LockedBlob:
    """Proxy an incremental BLOB handle through its owning connection."""

    def __init__(self, connection: "_LockedConnection", blob: Any):
        object.__setattr__(self, "_connection", connection)
        object.__setattr__(self, "_blob", blob)

    def _call(self, operation: Callable[[], Any]) -> Any:
        return self._connection._sqlite_call(operation)

    def __getattr__(self, name: str) -> Any:
        attribute = self._call(lambda: getattr(self._blob, name))
        if not callable(attribute):
            return attribute

        def guarded(*args: Any, **kwargs: Any) -> Any:
            return self._call(lambda: attribute(*args, **kwargs))

        return guarded

    def __len__(self) -> int:
        return self._call(lambda: len(self._blob))

    def __getitem__(self, key: Any) -> Any:
        return self._call(lambda: self._blob[key])

    def __setitem__(self, key: Any, value: Any) -> None:
        self._call(lambda: self._blob.__setitem__(key, value))

    def __enter__(self) -> "_LockedBlob":
        self._call(self._blob.__enter__)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> Any:
        return self._call(lambda: self._blob.__exit__(exc_type, exc, traceback))

    def __del__(self) -> None:
        connection = getattr(self, "_connection", None)
        blob = getattr(self, "_blob", None)
        if connection is None or blob is None:
            return
        try:
            connection._sqlite_call(blob.close)
        except Exception:
            pass


class _LockedCursor(sqlite3.Cursor):
    """Cursor subclass that routes execution and result reads through a lock."""

    def _call(self, operation: Callable[[], Any]) -> Any:
        connection = sqlite3.Cursor.connection.__get__(self, type(self))
        return connection._sqlite_call(operation)

    def execute(self, *args: Any, **kwargs: Any) -> "_LockedCursor":
        self._call(lambda: sqlite3.Cursor.execute(self, *args, **kwargs))
        return self

    def executemany(self, *args: Any, **kwargs: Any) -> "_LockedCursor":
        self._call(lambda: sqlite3.Cursor.executemany(self, *args, **kwargs))
        return self

    def executescript(self, *args: Any, **kwargs: Any) -> "_LockedCursor":
        self._call(lambda: sqlite3.Cursor.executescript(self, *args, **kwargs))
        return self

    def fetchone(self) -> Any:
        return self._call(lambda: sqlite3.Cursor.fetchone(self))

    def fetchmany(self, *args: Any, **kwargs: Any) -> Any:
        return self._call(lambda: sqlite3.Cursor.fetchmany(self, *args, **kwargs))

    def fetchall(self) -> Any:
        return self._call(lambda: sqlite3.Cursor.fetchall(self))

    def close(self) -> Any:
        return self._call(lambda: sqlite3.Cursor.close(self))

    def __iter__(self) -> "_LockedCursor":
        return self

    def __next__(self) -> Any:
        return self._call(lambda: sqlite3.Cursor.__next__(self))

    def __del__(self) -> None:
        try:
            self._call(lambda: sqlite3.Cursor.close(self))
        except Exception:
            pass


class _LockedConnection(sqlite3.Connection):
    """Connection subclass serializing SQLite calls and write transactions."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._sqlite_transaction_held = False
        self._sqlite_transaction_owner: int | None = None

    def _in_transaction(self) -> bool:
        try:
            return bool(sqlite3.Connection.in_transaction.__get__(self, type(self)))
        except sqlite3.ProgrammingError:
            return False

    def _sqlite_call(self, operation: Callable[[], Any]) -> Any:
        """Run one C call without allowing a transaction lock inversion."""
        _SQLITE_CALL_GATE.enter(self)
        active_transaction = self._sqlite_transaction_held
        try:
            try:
                result = operation()
            finally:
                active_transaction = self._in_transaction()
                self._sqlite_transaction_held = active_transaction
                self._sqlite_transaction_owner = (
                    threading.get_ident() if active_transaction else None
                )
        finally:
            _SQLITE_CALL_GATE.leave(
                self, active_transaction=active_transaction,
            )
        return result

    def cursor(self, *args: Any, **kwargs: Any) -> _LockedCursor:
        factory = kwargs.pop("factory", _LockedCursor)
        return self._sqlite_call(
            lambda: sqlite3.Connection.cursor(
                self, *args, factory=factory, **kwargs,
            )
        )

    def _statement(self, method: Callable[..., Any], *args: Any, **kwargs: Any):
        def run():
            cursor = sqlite3.Connection.cursor(self, factory=_LockedCursor)
            try:
                method(cursor, *args, **kwargs)
            except BaseException:
                sqlite3.Cursor.close(cursor)
                raise
            return cursor

        return self._sqlite_call(run)

    def execute(self, *args: Any, **kwargs: Any) -> _LockedCursor:
        return self._statement(sqlite3.Cursor.execute, *args, **kwargs)

    def executemany(self, *args: Any, **kwargs: Any) -> _LockedCursor:
        return self._statement(sqlite3.Cursor.executemany, *args, **kwargs)

    def executescript(self, *args: Any, **kwargs: Any) -> _LockedCursor:
        return self._statement(sqlite3.Cursor.executescript, *args, **kwargs)

    def commit(self) -> Any:
        return self._sqlite_call(lambda: sqlite3.Connection.commit(self))

    def rollback(self) -> Any:
        return self._sqlite_call(lambda: sqlite3.Connection.rollback(self))

    def close(self) -> Any:
        return self._sqlite_call(lambda: sqlite3.Connection.close(self))

    def backup(self, target: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(target, _SerializedConnection):
            target = target._connection
        return self._sqlite_call(
            lambda: sqlite3.Connection.backup(self, target, *args, **kwargs)
        )

    def iterdump(self, *args: Any, **kwargs: Any) -> _LockedIterator:
        iterator = self._sqlite_call(
            lambda: sqlite3.Connection.iterdump(self, *args, **kwargs)
        )
        return _LockedIterator(self, iterator)

    def blobopen(self, *args: Any, **kwargs: Any) -> _LockedBlob:
        blob = self._sqlite_call(
            lambda: sqlite3.Connection.blobopen(self, *args, **kwargs)
        )
        return _LockedBlob(self, blob)

    def create_aggregate(self, *args: Any, **kwargs: Any) -> Any:
        return self._sqlite_call(
            lambda: sqlite3.Connection.create_aggregate(self, *args, **kwargs)
        )

    def create_collation(self, *args: Any, **kwargs: Any) -> Any:
        return self._sqlite_call(
            lambda: sqlite3.Connection.create_collation(self, *args, **kwargs)
        )

    def create_function(self, *args: Any, **kwargs: Any) -> Any:
        return self._sqlite_call(
            lambda: sqlite3.Connection.create_function(self, *args, **kwargs)
        )

    def create_window_function(self, *args: Any, **kwargs: Any) -> Any:
        return self._sqlite_call(
            lambda: sqlite3.Connection.create_window_function(
                self, *args, **kwargs,
            )
        )

    def enable_load_extension(self, *args: Any, **kwargs: Any) -> Any:
        return self._sqlite_call(
            lambda: sqlite3.Connection.enable_load_extension(
                self, *args, **kwargs,
            )
        )

    def load_extension(self, *args: Any, **kwargs: Any) -> Any:
        return self._sqlite_call(
            lambda: sqlite3.Connection.load_extension(self, *args, **kwargs)
        )

    def set_authorizer(self, *args: Any, **kwargs: Any) -> Any:
        return self._sqlite_call(
            lambda: sqlite3.Connection.set_authorizer(self, *args, **kwargs)
        )

    def set_progress_handler(self, *args: Any, **kwargs: Any) -> Any:
        return self._sqlite_call(
            lambda: sqlite3.Connection.set_progress_handler(
                self, *args, **kwargs,
            )
        )

    def set_trace_callback(self, *args: Any, **kwargs: Any) -> Any:
        return self._sqlite_call(
            lambda: sqlite3.Connection.set_trace_callback(
                self, *args, **kwargs,
            )
        )

    def getlimit(self, *args: Any, **kwargs: Any) -> Any:
        return self._sqlite_call(
            lambda: sqlite3.Connection.getlimit(self, *args, **kwargs)
        )

    def setlimit(self, *args: Any, **kwargs: Any) -> Any:
        return self._sqlite_call(
            lambda: sqlite3.Connection.setlimit(self, *args, **kwargs)
        )

    def serialize(self, *args: Any, **kwargs: Any) -> Any:
        return self._sqlite_call(
            lambda: sqlite3.Connection.serialize(self, *args, **kwargs)
        )

    def deserialize(self, *args: Any, **kwargs: Any) -> Any:
        return self._sqlite_call(
            lambda: sqlite3.Connection.deserialize(self, *args, **kwargs)
        )

    def __enter__(self) -> "_LockedConnection":
        self._sqlite_call(lambda: sqlite3.Connection.__enter__(self))
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> Any:
        return self._sqlite_call(
            lambda: sqlite3.Connection.__exit__(self, exc_type, exc, traceback)
        )

    def interrupt(self) -> Any:
        # SQLite explicitly supports this call from another thread.  Waiting
        # for the operation lock would make it unable to interrupt that call.
        return sqlite3.Connection.interrupt(self)

    def __del__(self) -> None:
        try:
            self._sqlite_call(lambda: sqlite3.Connection.close(self))
        except Exception:
            # Native connections enforce check_same_thread even for close().
            # Their C-level deallocator still closes the unreachable object,
            # but our process-wide transaction guard must be released here.
            if getattr(self, "_sqlite_transaction_held", False):
                self._sqlite_transaction_held = False
                self._sqlite_transaction_owner = None
                _SQLITE_CALL_GATE.abandon(self)


class _SerializedCursor(Iterator[Any]):
    """Fallback cursor proxy for connect wrappers used by instrumentation."""

    def __init__(self, cursor: Any, connection: "_SerializedConnection"):
        object.__setattr__(self, "_cursor", cursor)
        object.__setattr__(self, "_connection", connection)

    def _call(self, operation: Callable[[], Any]) -> Any:
        return self._connection._sqlite_call(operation)

    def execute(self, *args: Any, **kwargs: Any) -> "_SerializedCursor":
        self._call(lambda: self._cursor.execute(*args, **kwargs))
        return self

    def executemany(self, *args: Any, **kwargs: Any) -> "_SerializedCursor":
        self._call(lambda: self._cursor.executemany(*args, **kwargs))
        return self

    def executescript(self, *args: Any, **kwargs: Any) -> "_SerializedCursor":
        self._call(lambda: self._cursor.executescript(*args, **kwargs))
        return self

    def fetchone(self) -> Any:
        return self._call(self._cursor.fetchone)

    def fetchmany(self, *args: Any, **kwargs: Any) -> Any:
        return self._call(lambda: self._cursor.fetchmany(*args, **kwargs))

    def fetchall(self) -> Any:
        return self._call(self._cursor.fetchall)

    def close(self) -> Any:
        return self._call(self._cursor.close)

    @property
    def connection(self) -> "_SerializedConnection":
        return self._connection

    def __iter__(self) -> "_SerializedCursor":
        return self

    def __next__(self) -> Any:
        return self._call(lambda: next(self._cursor))

    def __getattr__(self, name: str) -> Any:
        return self._call(lambda: getattr(self._cursor, name))

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"_cursor", "_connection"}:
            object.__setattr__(self, name, value)
            return
        self._call(lambda: setattr(self._cursor, name, value))

    def __del__(self) -> None:
        connection = getattr(self, "_connection", None)
        cursor = getattr(self, "_cursor", None)
        if connection is None or cursor is None:
            return
        try:
            connection._sqlite_call(cursor.close)
        except Exception:
            pass


class _SerializedConnection:
    """Fallback proxy when a test/instrumentation wrapper hides the subclass."""

    def __init__(self, connection: Any):
        object.__setattr__(self, "_connection", connection)
        object.__setattr__(self, "_sqlite_transaction_held", False)
        object.__setattr__(self, "_sqlite_transaction_owner", None)

    def _in_transaction(self) -> bool:
        try:
            return bool(getattr(self._connection, "in_transaction"))
        except (AttributeError, sqlite3.ProgrammingError):
            return False

    def _sqlite_call(self, operation: Callable[[], Any]) -> Any:
        _SQLITE_CALL_GATE.enter(self)
        active_transaction = self._sqlite_transaction_held
        try:
            try:
                result = operation()
            finally:
                active_transaction = self._in_transaction()
                self._sqlite_transaction_held = active_transaction
                self._sqlite_transaction_owner = (
                    threading.get_ident() if active_transaction else None
                )
        finally:
            _SQLITE_CALL_GATE.leave(
                self, active_transaction=active_transaction,
            )
        return result

    def _wrap_cursor(self, cursor: Any) -> Any:
        if cursor is None or isinstance(cursor, _SerializedCursor):
            return cursor
        if hasattr(cursor, "fetchone") or isinstance(cursor, sqlite3.Cursor):
            return _SerializedCursor(cursor, self)
        return cursor

    def cursor(self, *args: Any, **kwargs: Any) -> Any:
        return self._wrap_cursor(
            self._sqlite_call(lambda: self._connection.cursor(*args, **kwargs))
        )

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        return self._wrap_cursor(
            self._sqlite_call(lambda: self._connection.execute(*args, **kwargs))
        )

    def executemany(self, *args: Any, **kwargs: Any) -> Any:
        return self._wrap_cursor(
            self._sqlite_call(lambda: self._connection.executemany(*args, **kwargs))
        )

    def executescript(self, *args: Any, **kwargs: Any) -> Any:
        return self._wrap_cursor(
            self._sqlite_call(lambda: self._connection.executescript(*args, **kwargs))
        )

    def commit(self) -> Any:
        return self._sqlite_call(self._connection.commit)

    def rollback(self) -> Any:
        return self._sqlite_call(self._connection.rollback)

    def close(self) -> Any:
        return self._sqlite_call(self._connection.close)

    def __getattr__(self, name: str) -> Any:
        attribute = self._sqlite_call(lambda: getattr(self._connection, name))
        if not callable(attribute):
            return attribute

        def guarded(*args: Any, **kwargs: Any) -> Any:
            return self._wrap_cursor(
                self._sqlite_call(lambda: attribute(*args, **kwargs))
            )

        return guarded

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_sqlite_") or name == "_connection":
            object.__setattr__(self, name, value)
            return
        self._sqlite_call(lambda: setattr(self._connection, name, value))

    def __enter__(self) -> "_SerializedConnection":
        self._sqlite_call(self._connection.__enter__)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> Any:
        return self._sqlite_call(
            lambda: self._connection.__exit__(exc_type, exc, traceback)
        )

    def __del__(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection is None:
            return
        try:
            self._sqlite_call(connection.close)
        except Exception:
            if getattr(self, "_sqlite_transaction_held", False):
                self._sqlite_transaction_held = False
                self._sqlite_transaction_owner = None
                _SQLITE_CALL_GATE.abandon(self)


def connect_with_lock(
    connect: Callable[..., _ConnectionT],
    /,
    *args: Any,
    **kwargs: Any,
) -> _ConnectionT:
    """Open SQLite using the process-wide serialized connection classes."""
    factory = kwargs.get("factory")
    if factory not in (None, _LockedConnection):
        raise ValueError("connect_with_lock requires its serialized connection factory")
    kwargs["factory"] = _LockedConnection
    open_token = object()
    _SQLITE_CALL_GATE.enter(open_token)
    try:
        connection = connect(*args, **kwargs)
    finally:
        _SQLITE_CALL_GATE.leave(open_token, active_transaction=False)
    if isinstance(connection, (_LockedConnection, _SerializedConnection)):
        return connection
    return _SerializedConnection(connection)
