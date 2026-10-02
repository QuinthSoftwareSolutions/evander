"""Bounded SQLite operations and idle, transaction-free WAL lifetime leases.

The read-only anchor prevents an operation's close from becoming SQLite's last
writable close (and synchronously checkpointing the entire WAL). Anchors never
hold a snapshot and are shared by transient repository instances in one process.
"""

from __future__ import annotations

import atexit
import fcntl
import logging
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)
DEFAULT_BUSY_TIMEOUT_MS = 30_000
FOREGROUND_TIMEOUT_MS = 100
MAINTENANCE_TIMEOUT_MS = 100
_MAX_IDLE_DATABASES = 32
_guard = threading.RLock()
_path_locks: dict[Path, threading.RLock] = {}
_initialized: set[tuple[Path, int, int, str]] = set()
_pid = os.getpid()
_foreground_wait_budget: ContextVar[float | None] = ContextVar(
    "sqlite_foreground_wait_budget", default=None
)
_storage_wait_deadline: ContextVar[float | None] = ContextVar(
    "sqlite_storage_wait_deadline", default=None
)


class SqliteOperationDeadlineExceeded(sqlite3.OperationalError):
    sqlite_errorcode = sqlite3.SQLITE_BUSY
    sqlite_errorname = "SQLITE_BUSY"

    def __init__(self, message: str, *, lock_wait: bool = False) -> None:
        super().__init__(message)
        self.lock_wait = lock_wait


def is_storage_contention(error: BaseException) -> bool:
    """Identify occupied writer/repository slots separately from execution timeouts."""
    if isinstance(error, SqliteOperationDeadlineExceeded):
        return error.lock_wait or (
            error.__cause__ is not None and is_storage_contention(error.__cause__)
        )
    return isinstance(error, sqlite3.OperationalError) and (
        (getattr(error, "sqlite_errorcode", 0) or 0) & 0xFF
        in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
        or str(error).lower() in {"database is locked", "database table is locked"}
    )


def is_storage_busy(error: BaseException) -> bool:
    return isinstance(error, SqliteOperationDeadlineExceeded) or is_storage_contention(error)


@contextmanager
def foreground_storage_wait_budget(seconds: float = FOREGROUND_TIMEOUT_MS / 1000) -> Iterator[None]:
    """Bound lock waits in retry-capable workers; query execution is independent."""
    previous = _foreground_wait_budget.get()
    token = _foreground_wait_budget.set(min(seconds, previous) if previous is not None else seconds)
    try:
        yield
    finally:
        _foreground_wait_budget.reset(token)


@contextmanager
def storage_lock(lock) -> Iterator[None]:
    """Share a lock-wait allowance without imposing a query execution deadline."""
    deadline = _storage_wait_deadline.get()
    if deadline is None:
        budget = _foreground_wait_budget.get()
        deadline = time.monotonic() + (
            DEFAULT_BUSY_TIMEOUT_MS / 1000 if budget is None else budget
        )
    if not lock.acquire(timeout=max(0, deadline - time.monotonic())):
        raise SqliteOperationDeadlineExceeded(
            "SQLite repository lock wait deadline exceeded", lock_wait=True
        )
    token = _storage_wait_deadline.set(deadline)
    try:
        yield
    finally:
        _storage_wait_deadline.reset(token)
        lock.release()


@dataclass
class _Anchor:
    connection: sqlite3.Connection
    identity: tuple[Path, int, int]
    leases: int = 0
    retired: bool = False


_anchors: OrderedDict[tuple[Path, int, int], _Anchor] = OrderedDict()


def _identity(path: Path) -> tuple[Path, int, int]:
    stat = path.stat()
    return path, stat.st_dev, stat.st_ino


def _check_process() -> None:
    global _pid, _guard, _path_locks, _initialized, _anchors
    if _pid != os.getpid():
        # Inherited SQLite handles must never be used by the forked process.
        _pid = os.getpid()
        _guard = threading.RLock()
        _path_locks = {}
        _initialized = set()
        _anchors = OrderedDict()


def _path_lock(path: Path) -> threading.RLock:
    _check_process()
    with _guard:
        return _path_locks.setdefault(path, threading.RLock())


def _retire(anchor: _Anchor) -> None:
    anchor.retired = True
    _anchors.pop(anchor.identity, None)
    if not anchor.leases:
        anchor.connection.close()


def close_sqlite_runtimes(path: Path | str | None = None) -> None:
    """Release idle anchors; leased anchors close after their operation closes."""
    _check_process()
    resolved = Path(path).resolve() if path is not None else None
    with _guard:
        for anchor in list(_anchors.values()):
            if resolved is None or anchor.identity[0] == resolved:
                _retire(anchor)
        for key in list(_initialized):
            if resolved is None or key[0] == resolved:
                _initialized.discard(key)


atexit.register(close_sqlite_runtimes)


def _acquire_anchor(path: Path, *, timeout_ms: int) -> _Anchor:
    _check_process()
    deadline = time.monotonic() + max(0, timeout_ms) / 1000
    lock = _path_lock(path)
    if not lock.acquire(timeout=max(0, deadline - time.monotonic())):
        raise SqliteOperationDeadlineExceeded("SQLite lifecycle lease is busy", lock_wait=True)
    try:
        try:
            identity = _identity(path)
        except FileNotFoundError as error:
            raise sqlite3.OperationalError("unable to open database file") from error
        with _guard:
            for old in list(_anchors.values()):
                if old.identity[0] == path and old.identity != identity:
                    _retire(old)
            anchor = _anchors.get(identity)
        if anchor is None:
            connection = sqlite3.connect(
                path.as_uri() + "?mode=ro",
                uri=True,
                timeout=max(0, deadline - time.monotonic()),
                check_same_thread=False,
            )
            try:
                connection.execute("PRAGMA query_only=ON")
                cursor = connection.execute("PRAGMA schema_version")
                cursor.fetchall()
                cursor.close()
                assert not connection.in_transaction
            except BaseException:
                connection.close()
                raise
            anchor = _Anchor(connection, identity)
        with _guard:
            _anchors[identity] = anchor
            anchor.leases += 1
            _anchors.move_to_end(identity)
            for candidate in list(_anchors.values()):
                if len(_anchors) <= _MAX_IDLE_DATABASES:
                    break
                if candidate.leases == 0:
                    _retire(candidate)
            return anchor
    finally:
        lock.release()


def _release_anchor(anchor: _Anchor) -> None:
    with _guard:
        anchor.leases -= 1
        if anchor.retired and not anchor.leases:
            anchor.connection.close()


class _ManagedCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        self.connection._prepare()
        started = time.monotonic()
        try:
            return super().execute(sql, parameters)
        except sqlite3.OperationalError as error:
            self.connection._translate(error)
        finally:
            elapsed = (time.monotonic() - started) * 1000
            self.connection._sql_ms += elapsed
            if sql.strip().upper().startswith("BEGIN IMMEDIATE"):
                self.connection._writer_acquire_ms += elapsed

    def executemany(self, sql, parameters):
        self.connection._prepare()
        started = time.monotonic()
        try:
            return super().executemany(sql, parameters)
        except sqlite3.OperationalError as error:
            self.connection._translate(error)
        finally:
            self.connection._sql_ms += (time.monotonic() - started) * 1000

    def fetchone(self):
        started = time.monotonic()
        try:
            return super().fetchone()
        except sqlite3.OperationalError as error:
            self.connection._translate(error)
        finally:
            self.connection._sql_ms += (time.monotonic() - started) * 1000

    def fetchall(self):
        started = time.monotonic()
        try:
            return super().fetchall()
        except sqlite3.OperationalError as error:
            self.connection._translate(error)
        finally:
            self.connection._sql_ms += (time.monotonic() - started) * 1000

    def fetchmany(self, size=None):
        started = time.monotonic()
        try:
            return super().fetchmany() if size is None else super().fetchmany(size)
        except sqlite3.OperationalError as error:
            self.connection._translate(error)
        finally:
            self.connection._sql_ms += (time.monotonic() - started) * 1000

    def __next__(self):
        try:
            return super().__next__()
        except sqlite3.OperationalError as error:
            self.connection._translate(error)


class ManagedSqliteConnection(sqlite3.Connection):
    _anchor: _Anchor | None = None
    _deadline: float | None = None
    _wait_deadline: float | None = None
    _operation: str = "database"
    _started: float = 0.0
    _closed: bool = False
    _busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS
    _connection_ms: float = 0.0
    _sql_ms: float = 0.0
    _writer_acquire_ms: float = 0.0
    _commit_ms: float = 0.0
    _outcome: str = "succeeded"
    _error_code: int | None = None

    def _prepare(self) -> None:
        now = time.monotonic()
        remaining_ms = self._busy_timeout_ms
        if self._wait_deadline is not None:
            remaining_ms = min(remaining_ms, max(0, int((self._wait_deadline - now) * 1000)))
        if self._deadline is not None:
            remaining = self._deadline - now
            if remaining <= 0:
                self._outcome = "deadline"
                self._error_code = sqlite3.SQLITE_BUSY
                raise SqliteOperationDeadlineExceeded("SQLite operation deadline exceeded")
            remaining_ms = min(remaining_ms, max(0, int(remaining * 1000)))
        super().execute(f"PRAGMA busy_timeout={remaining_ms}")

    def _translate(self, error: sqlite3.OperationalError) -> None:
        self._error_code = getattr(error, "sqlite_errorcode", None)
        self._outcome = "busy" if is_storage_busy(error) else "failed"
        if self._deadline is not None and time.monotonic() >= self._deadline:
            self._outcome = "deadline"
            raise SqliteOperationDeadlineExceeded(
                "SQLite operation deadline exceeded", lock_wait=is_storage_contention(error)
            ) from error
        raise error

    def execute(self, sql, parameters=()):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, parameters):
        return self.cursor().executemany(sql, parameters)

    def cursor(self, factory=_ManagedCursor):
        return super().cursor(factory=factory)

    def executescript(self, sql):
        self._prepare()
        try:
            return super().executescript(sql)
        except sqlite3.OperationalError as error:
            self._translate(error)

    def commit(self) -> None:
        if self.in_transaction:
            self._prepare()
        started = time.monotonic()
        try:
            super().commit()
        except sqlite3.OperationalError as error:
            self._translate(error)
        finally:
            self._commit_ms += (time.monotonic() - started) * 1000

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is None:
                try:
                    self.commit()
                except BaseException:
                    self.rollback()
                    raise
            else:
                if self._outcome == "succeeded":
                    self._outcome = "rolled_back"
                self.rollback()
        finally:
            self.close()
        return False

    def close(self) -> None:
        if self._closed:
            return
        started = time.monotonic()
        try:
            super().close()
        finally:
            self._closed = True
            if self._anchor is not None:
                _release_anchor(self._anchor)
                self._anchor = None
            elapsed = (time.monotonic() - self._started) * 1000
            close_ms = (time.monotonic() - started) * 1000
            level = (
                logging.WARNING if elapsed >= 100 or self._outcome != "succeeded" else logging.DEBUG
            )
            if logger.isEnabledFor(level):
                logger.log(
                    level,
                    "sqlite_operation operation=%s duration_ms=%.1f connection_ms=%.1f "
                    "sql_ms=%.1f writer_acquire_ms=%.1f commit_ms=%.1f close_ms=%.1f "
                    "outcome=%s sqlite_errorcode=%s",
                    self._operation,
                    elapsed,
                    self._connection_ms,
                    self._sql_ms,
                    self._writer_acquire_ms,
                    self._commit_ms,
                    close_ms,
                    self._outcome,
                    self._error_code,
                )


def open_sqlite_connection(
    path: Path | str,
    *,
    read_only: bool = False,
    timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    operation: str = "database",
    deadline_seconds: float | None = None,
    managed_checkpoints: bool = False,
) -> ManagedSqliteConnection:
    """Open an initialized DB with independent lock and execution limits.

    Only explicit execution deadlines interrupt SQL or expire between statements.
    Exhausting the lock-wait allowance still permits uncontended work. Filesystem fsync/close
    is measured separately: SQLite cannot promise a hard I/O time limit.
    """
    resolved = Path(path).resolve()
    started = time.monotonic()
    deadline = None if deadline_seconds is None else started + deadline_seconds
    foreground = _foreground_wait_budget.get()
    if foreground is not None:
        timeout_ms = min(timeout_ms, max(0, int(foreground * 1000)))
    wait_deadline = started + timeout_ms / 1000
    outer_wait_deadline = _storage_wait_deadline.get()
    if outer_wait_deadline is not None:
        wait_deadline = min(wait_deadline, outer_wait_deadline)
    if deadline is not None:
        wait_deadline = min(wait_deadline, deadline)
    timeout_ms = min(timeout_ms, max(0, int((wait_deadline - time.monotonic()) * 1000)))
    anchor = _acquire_anchor(resolved, timeout_ms=timeout_ms)
    connection = None
    try:
        remaining_ms = max(0, int((wait_deadline - time.monotonic()) * 1000))
        connection = sqlite3.connect(
            resolved.as_uri() + ("?mode=ro" if read_only else "?mode=rw"),
            uri=True,
            timeout=remaining_ms / 1000,
            factory=ManagedSqliteConnection,
        )
        connection._anchor = anchor
        connection._started = started
        connection._operation = operation
        connection._busy_timeout_ms = timeout_ms
        connection._deadline = deadline
        connection._wait_deadline = wait_deadline
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON" if read_only else "PRAGMA foreign_keys=ON")
        # Only databases covered by a background checkpoint owner may disable
        # SQLite's own WAL maintenance. Team and other standalone stores retain
        # automatic checkpoints even while an idle lifetime anchor is open.
        if not read_only and managed_checkpoints:
            connection.execute("PRAGMA wal_autocheckpoint=0")
        if deadline is not None:
            connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        connection._connection_ms = (time.monotonic() - started) * 1000
        return connection
    except BaseException:
        if connection is not None:
            connection.close()
        else:
            _release_anchor(anchor)
        raise


@contextmanager
def _initialization_lock(path: Path, *, deadline: float) -> Iterator[None]:
    lock_path = path.with_name(path.name + ".schema.lock")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise SqliteOperationDeadlineExceeded(
                        "SQLite schema initialization is busy", lock_wait=True
                    )
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def ensure_sqlite_initialized(
    path: Path | str,
    *,
    schema_key: str,
    is_current: Callable[[sqlite3.Connection], bool],
    initialize: Callable[[sqlite3.Connection], None],
    force: bool = False,
    timeout_ms: int = 30_000,
) -> None:
    resolved = Path(path).resolve()
    deadline = time.monotonic() + timeout_ms / 1000
    with _path_lock(resolved):
        identity = _identity(resolved) if resolved.exists() else None
        key = (*identity, schema_key) if identity is not None else None
        if not force and key in _initialized:
            return
        if not force and identity is not None:
            try:
                with open_sqlite_connection(
                    resolved, read_only=True, timeout_ms=timeout_ms, operation="schema_readiness"
                ) as connection:
                    ready = is_current(connection)
                if ready:
                    with _guard:
                        _initialized.add((*identity, schema_key))
                    return
            except sqlite3.OperationalError as error:
                if "no such table" not in str(error):
                    raise
        resolved.parent.mkdir(parents=True, exist_ok=True)
        with _initialization_lock(resolved, deadline=deadline):
            remaining = max(0, deadline - time.monotonic())
            connection = sqlite3.connect(resolved, timeout=remaining)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute(f"PRAGMA busy_timeout={int(remaining * 1000)}")
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA wal_autocheckpoint=0")
                connection.execute("PRAGMA foreign_keys=ON")
                try:
                    ready = not force and is_current(connection)
                except sqlite3.OperationalError as error:
                    if "no such table" not in str(error):
                        raise
                    ready = False
                if not ready:
                    initialize(connection)
                connection.commit()
                anchor = _acquire_anchor(resolved, timeout_ms=timeout_ms)
                _release_anchor(anchor)
                with _guard:
                    _initialized.add((*_identity(resolved), schema_key))
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()
