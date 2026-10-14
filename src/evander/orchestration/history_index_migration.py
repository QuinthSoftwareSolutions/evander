"""Explicit conversion of an offline history database to a separate output."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from evander.repositories.history_index_storage import convert_history_index_storage
from evander.repositories.orchestration_db import ensure_orchestration_schema
from evander.repositories.sqlite_runtime import close_sqlite_runtimes


def copy_history_index_storage(
    source: Path, destination: Path, *, interned: bool = True
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
    with tempfile.TemporaryDirectory(prefix=".history-index-", dir=destination.parent) as temporary:
        work = Path(temporary) / "work.db"
        output = Path(temporary) / "output.db"
        origin = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        replica = sqlite3.connect(work)
        try:
            origin.backup(replica)
        finally:
            replica.close()
            origin.close()
        ensure_orchestration_schema(work)
        close_sqlite_runtimes(work)
        connection = sqlite3.connect(work)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute("PRAGMA secure_delete=OFF")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA temp_store=MEMORY")
            connection.execute("PRAGMA cache_size=-131072")
            result = convert_history_index_storage(connection, interned=interned)
            connection.commit()
            connection.execute("ANALYZE")
            connection.commit()
            connection.execute("PRAGMA synchronous=FULL")
            if interned:
                connection.execute("VACUUM INTO ?", (str(output),))
                connection.close()
                connection = sqlite3.connect(output)
            else:
                # VACUUM may renumber implicit legacy rowids used by cursors.
                output = work
            connection.execute("PRAGMA journal_mode=DELETE")
            integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
            foreign = connection.execute("PRAGMA foreign_key_check").fetchall()
            if integrity != ["ok"] or foreign:
                raise ValueError("Converted database failed integrity or foreign-key checks")
        finally:
            connection.close()
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
    return {
        **result,
        "source": str(source),
        "destination": str(destination),
        "source_bytes": source.stat().st_size,
        "output_bytes": destination.stat().st_size,
        "integrity_check": "ok",
        "foreign_key_errors": 0,
    }
