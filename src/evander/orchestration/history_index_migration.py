"""Explicit conversion of an offline history database to a separate output."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import Any

from evander.repositories.history_index_storage import convert_history_index_storage
from evander.repositories.orchestration_db import ensure_orchestration_schema
from evander.repositories.sqlite_runtime import close_sqlite_runtimes


def copy_history_index_storage(
    source: Path,
    destination: Path,
    *,
    interned: bool = True,
    cache_mib: int = 1024,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Publish only a fully converted, checked copy; never write to source.

    Large conversions require space for a backup, replacement tables and output.
    Keep every process off the destination until the operation finishes. An old
    binary can use a separately converted legacy output for rollback.
    """
    source, destination = source.resolve(), destination.absolute()
    if not source.is_file():
        raise ValueError("Source database does not exist")
    if destination.exists() or source == destination.resolve():
        raise ValueError("Destination must be a new file distinct from the source")
    if cache_mib < 1:
        raise ValueError("Cache size must be at least 1 MiB")
    started = phase_started = monotonic()
    active_phase: str | None = None
    phase_seconds: dict[str, float] = {}

    def report(phase: str) -> None:
        nonlocal active_phase, phase_started
        now = monotonic()
        if active_phase is not None:
            phase_seconds[active_phase] = round(now - phase_started, 3)
        active_phase, phase_started = phase, now
        if progress is not None:
            progress(phase)

    with tempfile.TemporaryDirectory(prefix=".history-index-", dir=destination.parent) as temporary:
        work = Path(temporary) / "work.db"
        output = Path(temporary) / "output.db"
        report("backup")
        origin = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        replica = sqlite3.connect(work)
        try:
            origin.backup(replica)
        finally:
            replica.close()
            origin.close()
        report("initialize_copy")
        ensure_orchestration_schema(work)
        close_sqlite_runtimes(work)
        connection = sqlite3.connect(work)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute("PRAGMA secure_delete=OFF")
            # Defer redundant per-row FK lookups during the bulk copy. Only
            # this unpublished, disposable copy disables enforcement. A full
            # foreign_key_check gates publication after conversion.
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("PRAGMA temp_store=MEMORY")
            connection.execute(f"PRAGMA cache_size=-{cache_mib * 1024}")
            result = convert_history_index_storage(connection, interned=interned, progress=report)
            report("commit_conversion")
            connection.commit()
            connection.execute("PRAGMA foreign_keys=ON")
            report("analyze")
            connection.execute("ANALYZE")
            connection.commit()
            connection.execute("PRAGMA synchronous=FULL")
            if interned:
                report("vacuum_output")
                connection.execute("VACUUM INTO ?", (str(output),))
                connection.close()
                connection = sqlite3.connect(output)
            else:
                # VACUUM may renumber implicit legacy rowids used by cursors.
                output = work
            connection.execute(f"PRAGMA cache_size=-{cache_mib * 1024}")
            connection.execute("PRAGMA journal_mode=DELETE")
            report("integrity_check")
            integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
            report("foreign_key_check")
            foreign = connection.execute("PRAGMA foreign_key_check").fetchall()
            if integrity != ["ok"] or foreign:
                raise ValueError("Converted database failed integrity or foreign-key checks")
        finally:
            connection.close()
        report("publish")
        output.chmod(0o600)
        with output.open("rb") as stream:
            os.fsync(stream.fileno())
        # Same-filesystem hard linking is atomic and refuses an existing target.
        os.link(output, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    report("complete")
    return {
        **result,
        "source": str(source),
        "destination": str(destination),
        "source_bytes": source.stat().st_size,
        "output_bytes": destination.stat().st_size,
        "integrity_check": "ok",
        "foreign_key_errors": 0,
        "cache_mib": cache_mib,
        "elapsed_seconds": round(monotonic() - started, 3),
        "phase_seconds": phase_seconds,
    }
