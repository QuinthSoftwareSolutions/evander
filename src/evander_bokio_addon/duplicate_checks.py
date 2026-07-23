from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
from uuid import uuid4

from evander.addons import addon_storage_path

from .client import BokioClient
from .state import ADDON_ID


DEFAULT_DUPLICATE_CHECK_MAX_AGE_SECONDS = 15 * 60
MAX_DUPLICATE_CHECK_MAX_AGE_SECONDS = 24 * 60 * 60
_PAGE_SIZE = 100
_MAX_PAGES = 10_000
_KEY_RE = re.compile(r"[^a-z0-9]+")
_DECIMAL_TEXT_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_CRITERION_ALIASES = {
    "date": ("date", "accountingdate", "journaldate", "transactiondate"),
    "account": ("account", "accountid", "accountnumber", "accountno"),
    "amount": ("amount", "debit", "credit", "total", "totalamount"),
    "title": ("title", "description", "text"),
    "invoice_number": ("invoicenumber", "invoiceno", "invoice"),
    "receipt_number": ("receiptnumber", "receiptno", "receipt"),
}


class BokioDuplicateCheckError(RuntimeError):
    """Raised when a duplicate-check request or Bokio page is malformed."""


class BokioDuplicateCheckStore:
    """Durable evidence for bounded-freshness journal duplicate checks."""

    def __init__(self, runtime_dir: str | Path) -> None:
        self.root = addon_storage_path(runtime_dir, ADDON_ID, "duplicate-checks")
        self.database_path = self.root / "duplicate-checks.db"
        self.root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def latest_fresh(
        self,
        *,
        criteria_digest: str,
        max_age_seconds: int,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        cutoff = _as_utc(now or datetime.now(UTC)) - timedelta(seconds=max_age_seconds)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM duplicate_checks
                WHERE criteria_digest = ? AND created_at >= ?
                ORDER BY created_at DESC, check_id DESC
                LIMIT 1
                """,
                (criteria_digest, _storage_datetime(cutoff)),
            ).fetchone()
        return _record_from_row(row) if row is not None else None

    def record(
        self,
        *,
        criteria: Mapping[str, Any],
        criteria_digest: str,
        source_fingerprint: str,
        total_scanned: int,
        matching_entry_ids: Sequence[str],
        created_at: datetime | None = None,
    ) -> dict[str, Any]:
        check_id = f"bokio_duplicate_check_{uuid4().hex}"
        now = _as_utc(created_at or datetime.now(UTC))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO duplicate_checks (
                    check_id, criteria_digest, criteria_json, source_fingerprint,
                    total_scanned, matching_entry_ids_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    check_id,
                    criteria_digest,
                    _canonical_json(criteria),
                    source_fingerprint,
                    int(total_scanned),
                    _canonical_json(list(matching_entry_ids)),
                    _storage_datetime(now),
                ),
            )
            row = connection.execute(
                "SELECT * FROM duplicate_checks WHERE check_id = ?",
                (check_id,),
            ).fetchone()
        if row is None:  # pragma: no cover - SQLite insert/read invariant
            raise BokioDuplicateCheckError("duplicate-check evidence could not be read after insert")
        return _record_from_row(row)

    def get(self, check_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM duplicate_checks WHERE check_id = ?",
                (_required_text(check_id, "check_id"),),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown Bokio duplicate check: {check_id}")
        return _record_from_row(row)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS duplicate_checks (
                    check_id TEXT PRIMARY KEY,
                    criteria_digest TEXT NOT NULL,
                    criteria_json TEXT NOT NULL,
                    source_fingerprint TEXT NOT NULL,
                    total_scanned INTEGER NOT NULL,
                    matching_entry_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_duplicate_checks_criteria_created
                ON duplicate_checks(criteria_digest, created_at DESC);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection


def check_journal_duplicates(
    client: BokioClient,
    store: BokioDuplicateCheckStore,
    *,
    criteria: Mapping[str, Any],
    max_age_seconds: int = DEFAULT_DUPLICATE_CHECK_MAX_AGE_SECONDS,
    refresh: bool = False,
) -> dict[str, Any]:
    normalized_criteria = normalize_duplicate_criteria(criteria)
    criteria_digest = sha256(_canonical_json(normalized_criteria).encode("utf-8")).hexdigest()
    bounded_max_age = min(
        MAX_DUPLICATE_CHECK_MAX_AGE_SECONDS,
        max(0, int(max_age_seconds)),
    )
    if not refresh and bounded_max_age:
        cached = store.latest_fresh(
            criteria_digest=criteria_digest,
            max_age_seconds=bounded_max_age,
        )
        if cached is not None:
            return {
                **cached,
                "cache_hit": True,
                "max_age_seconds": bounded_max_age,
            }

    entries = _list_all_journal_entries(client)
    matches = [entry for entry in entries if _entry_matches(entry, normalized_criteria)]
    matching_ids = [_entry_identifier(entry) for entry in matches]
    record = store.record(
        criteria=normalized_criteria,
        criteria_digest=criteria_digest,
        source_fingerprint=sha256(_canonical_json(entries).encode("utf-8")).hexdigest(),
        total_scanned=len(entries),
        matching_entry_ids=matching_ids,
    )
    return {
        **record,
        "cache_hit": False,
        "max_age_seconds": bounded_max_age,
    }


def normalize_duplicate_criteria(criteria: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(criteria, Mapping) or not criteria:
        raise BokioDuplicateCheckError("criteria must be a non-empty object")
    normalized: dict[str, Any] = {}
    for raw_key, value in criteria.items():
        key = str(raw_key or "").strip().lower().replace("-", "_").replace(" ", "_")
        if key not in _CRITERION_ALIASES:
            supported = ", ".join(sorted(_CRITERION_ALIASES))
            raise BokioDuplicateCheckError(
                f"unsupported duplicate criterion {raw_key!r}; supported criteria: {supported}"
            )
        if value is None or (isinstance(value, str) and not value.strip()):
            raise BokioDuplicateCheckError(f"duplicate criterion {key!r} must not be empty")
        normalized[key] = _normalized_match_value(value)
    return dict(sorted(normalized.items()))


def _list_all_journal_entries(client: BokioClient) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for page in range(1, _MAX_PAGES + 1):
        response = client.list_journal_entries(page=page, page_size=_PAGE_SIZE)
        if not isinstance(response, Mapping):
            raise BokioDuplicateCheckError("Bokio journal-entry page must be an object")
        raw_items = response.get("items")
        if not isinstance(raw_items, list):
            raise BokioDuplicateCheckError("Bokio journal-entry page is missing an items list")
        for item in raw_items:
            if not isinstance(item, Mapping):
                raise BokioDuplicateCheckError("Bokio journal-entry items must be objects")
            entries.append(dict(item))
        total_items = _optional_nonnegative_int(response.get("totalItems"))
        if not raw_items or len(raw_items) < _PAGE_SIZE:
            break
        if total_items is not None and len(entries) >= total_items:
            break
    else:  # pragma: no cover - defensive API runaway guard
        raise BokioDuplicateCheckError("Bokio journal-entry pagination exceeded 10000 pages")
    return entries


def _entry_matches(entry: Mapping[str, Any], criteria: Mapping[str, Any]) -> bool:
    values_by_key: dict[str, list[str]] = {}

    def _collect(value: Any, key: str | None = None) -> None:
        if isinstance(value, Mapping):
            for child_key, child_value in value.items():
                _collect(child_value, _normalized_key(str(child_key)))
            return
        if isinstance(value, list):
            for child_value in value:
                _collect(child_value, key)
            return
        if key:
            values_by_key.setdefault(key, []).append(_normalized_match_value(value))

    _collect(entry)
    for criterion, expected in criteria.items():
        aliases = _CRITERION_ALIASES[criterion]
        actual_values = [
            actual
            for alias in aliases
            for actual in values_by_key.get(alias, [])
        ]
        if expected not in actual_values:
            return False
    return True


def _entry_identifier(entry: Mapping[str, Any]) -> str:
    for key in ("id", "journalEntryId", "journal_entry_id"):
        value = str(entry.get(key) or "").strip()
        if value:
            return value
    return "sha256:" + sha256(_canonical_json(entry).encode("utf-8")).hexdigest()


def _record_from_row(row: sqlite3.Row) -> dict[str, Any]:
    check_id = str(row["check_id"])
    return {
        "check_id": check_id,
        "criteria": json.loads(str(row["criteria_json"])),
        "criteria_digest": str(row["criteria_digest"]),
        "source_fingerprint": str(row["source_fingerprint"]),
        "total_scanned": int(row["total_scanned"]),
        "matching_entry_ids": json.loads(str(row["matching_entry_ids_json"])),
        "evidence_ref": f"bokio-duplicate-check:{check_id}",
        "created_at": str(row["created_at"]),
    }


def _normalized_key(value: str) -> str:
    return _KEY_RE.sub("", value.strip().lower())


def _normalized_match_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if (
        isinstance(value, (int, float, Decimal))
        and not isinstance(value, bool)
        or isinstance(value, str)
        and _DECIMAL_TEXT_RE.fullmatch(value.strip())
    ):
        try:
            decimal_value = Decimal(str(value))
            return format(decimal_value.normalize(), "f")
        except InvalidOperation:
            pass
    return str(value).strip().casefold()


def _optional_nonnegative_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _required_text(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _storage_datetime(value: datetime) -> str:
    return _as_utc(value).isoformat()
