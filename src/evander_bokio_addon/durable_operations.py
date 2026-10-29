from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import re
import sqlite3


_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SQLiteDurableOperationLedger:
    """Shared SQLite identity and state-transition mechanics for durable operations.

    The owning stores retain their domain-specific schemas, statuses, validation, and
    recovery policy. This class only centralizes the transactional mechanics that must
    stay consistent across those ledgers.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        table: str,
        identity_columns: Sequence[str],
        foreign_keys: bool = False,
    ) -> None:
        if not identity_columns:
            raise ValueError("durable operation identity must contain at least one column")
        self.database_path = Path(database_path)
        self.table = _identifier(table)
        self.identity_columns = tuple(_identifier(column) for column in identity_columns)
        self.foreign_keys = foreign_keys

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        if self.foreign_keys:
            connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @staticmethod
    def begin_immediate(connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")

    def fetch_row(
        self,
        connection: sqlite3.Connection,
        identity: Sequence[object],
    ) -> sqlite3.Row | None:
        where, parameters = self._identity_clause(identity)
        return connection.execute(
            f"SELECT * FROM {self.table} WHERE {where}",  # noqa: S608
            parameters,
        ).fetchone()

    def insert(
        self,
        connection: sqlite3.Connection,
        values: Mapping[str, object],
    ) -> None:
        if not values:
            raise ValueError("durable operation insert values are required")
        columns = tuple(_identifier(column) for column in values)
        placeholders = ", ".join("?" for _column in columns)
        connection.execute(
            f"INSERT INTO {self.table} ({', '.join(columns)}) VALUES ({placeholders})",  # noqa: S608
            tuple(values[column] for column in values),
        )

    def transition(
        self,
        connection: sqlite3.Connection,
        identity: Sequence[object],
        *,
        expected_status: str,
        new_status: str,
        values: Mapping[str, object] | None = None,
        increment_columns: Sequence[str] = (),
    ) -> int:
        assignments: dict[str, object] = {"status": new_status}
        assignments.update(values or {})
        return self.update(
            connection,
            identity,
            values=assignments,
            expected_status=expected_status,
            increment_columns=increment_columns,
        )

    def update(
        self,
        connection: sqlite3.Connection,
        identity: Sequence[object],
        *,
        values: Mapping[str, object],
        expected_status: str | None = None,
        increment_columns: Sequence[str] = (),
    ) -> int:
        if not values and not increment_columns:
            raise ValueError("durable operation update values are required")
        set_clauses: list[str] = []
        parameters: list[object] = []
        for column, value in values.items():
            normalized_column = _identifier(column)
            set_clauses.append(f"{normalized_column} = ?")
            parameters.append(value)
        for column in increment_columns:
            normalized_column = _identifier(column)
            set_clauses.append(f"{normalized_column} = {normalized_column} + 1")
        where, identity_parameters = self._identity_clause(identity)
        parameters.extend(identity_parameters)
        if expected_status is not None:
            where = f"{where} AND status = ?"
            parameters.append(expected_status)
        return connection.execute(
            f"UPDATE {self.table} SET {', '.join(set_clauses)} WHERE {where}",  # noqa: S608
            tuple(parameters),
        ).rowcount

    def _identity_clause(self, identity: Sequence[object]) -> tuple[str, tuple[object, ...]]:
        values = tuple(identity)
        if len(values) != len(self.identity_columns):
            raise ValueError("durable operation identity has the wrong number of values")
        return (
            " AND ".join(f"{column} = ?" for column in self.identity_columns),
            values,
        )


def _identifier(value: str) -> str:
    if not _SQL_IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"invalid SQLite identifier: {value!r}")
    return value
