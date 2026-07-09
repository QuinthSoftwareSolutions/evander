from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

from .models import STATE_DB_FILENAME


class AdoActivityRepository:
    def __init__(self, runtime_dir: str | Path) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.db_path = self.runtime_dir / STATE_DB_FILENAME

    def ensure_schema(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS activity_records (
                    activity_id TEXT PRIMARY KEY,
                    activity_kind TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    actor_display_name TEXT,
                    actor_unique_name TEXT,
                    actor_email TEXT,
                    directory_path TEXT,
                    organization_url TEXT,
                    project TEXT,
                    repo TEXT,
                    work_item_id TEXT,
                    pr_id TEXT,
                    title TEXT,
                    state TEXT,
                    work_item_type TEXT,
                    url TEXT,
                    summary TEXT,
                    changed_fields_json TEXT NOT NULL DEFAULT '{}',
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    ingested_at TEXT NOT NULL,
                    source_revision TEXT,
                    source_updated_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_ado_activity_occurred
                    ON activity_records (occurred_at DESC);
                CREATE INDEX IF NOT EXISTS idx_ado_activity_kind
                    ON activity_records (activity_kind);
                CREATE INDEX IF NOT EXISTS idx_ado_activity_actor_unique
                    ON activity_records (actor_unique_name);
                CREATE INDEX IF NOT EXISTS idx_ado_activity_actor_email
                    ON activity_records (actor_email);
                CREATE INDEX IF NOT EXISTS idx_ado_activity_directory
                    ON activity_records (directory_path);
                CREATE INDEX IF NOT EXISTS idx_ado_activity_project
                    ON activity_records (project);
                CREATE INDEX IF NOT EXISTS idx_ado_activity_repo
                    ON activity_records (repo);
                CREATE INDEX IF NOT EXISTS idx_ado_activity_work_item
                    ON activity_records (work_item_id);
                CREATE INDEX IF NOT EXISTS idx_ado_activity_pr
                    ON activity_records (pr_id);

                CREATE TABLE IF NOT EXISTS sync_state (
                    sync_key TEXT PRIMARY KEY,
                    complete_through TEXT,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                """
            )

    def upsert_records(self, records: list[Mapping[str, Any]]) -> dict[str, int]:
        if not records:
            return {"inserted": 0, "updated": 0}
        self.ensure_schema()
        now = _utc_now_iso()
        inserted = 0
        updated = 0
        with self._connect() as conn:
            for record in records:
                activity_id = _required(record, "activity_id")
                existed = conn.execute(
                    "SELECT 1 FROM activity_records WHERE activity_id = ?",
                    (activity_id,),
                ).fetchone()
                if existed:
                    updated += 1
                else:
                    inserted += 1
                conn.execute(
                    """
                    INSERT INTO activity_records (
                        activity_id,
                        activity_kind,
                        occurred_at,
                        actor_display_name,
                        actor_unique_name,
                        actor_email,
                        directory_path,
                        organization_url,
                        project,
                        repo,
                        work_item_id,
                        pr_id,
                        title,
                        state,
                        work_item_type,
                        url,
                        summary,
                        changed_fields_json,
                        raw_json,
                        ingested_at,
                        source_revision,
                        source_updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(activity_id) DO UPDATE SET
                        activity_kind = excluded.activity_kind,
                        occurred_at = excluded.occurred_at,
                        actor_display_name = excluded.actor_display_name,
                        actor_unique_name = excluded.actor_unique_name,
                        actor_email = excluded.actor_email,
                        directory_path = excluded.directory_path,
                        organization_url = excluded.organization_url,
                        project = excluded.project,
                        repo = excluded.repo,
                        work_item_id = excluded.work_item_id,
                        pr_id = excluded.pr_id,
                        title = excluded.title,
                        state = excluded.state,
                        work_item_type = excluded.work_item_type,
                        url = excluded.url,
                        summary = excluded.summary,
                        changed_fields_json = excluded.changed_fields_json,
                        raw_json = excluded.raw_json,
                        ingested_at = excluded.ingested_at,
                        source_revision = excluded.source_revision,
                        source_updated_at = excluded.source_updated_at
                    """,
                    (
                        activity_id,
                        _required(record, "activity_kind"),
                        _to_iso(record.get("occurred_at")),
                        _text(record.get("actor_display_name")),
                        _text(record.get("actor_unique_name")),
                        _text(record.get("actor_email")),
                        _text(record.get("directory_path")),
                        _text(record.get("organization_url")),
                        _text(record.get("project")),
                        _text(record.get("repo")),
                        _text(record.get("work_item_id")),
                        _text(record.get("pr_id")),
                        _text(record.get("title")),
                        _text(record.get("state")),
                        _text(record.get("work_item_type")),
                        _text(record.get("url")),
                        _text(record.get("summary")),
                        _json_dumps(record.get("changed_fields") or {}),
                        _json_dumps(record.get("raw") or {}),
                        now,
                        _text(record.get("source_revision")),
                        _to_iso_or_none(record.get("source_updated_at")),
                    ),
                )
        return {"inserted": inserted, "updated": updated}

    def query(
        self,
        *,
        since: datetime,
        until: datetime | None = None,
        actor: str | None = None,
        activity_kinds: list[str] | None = None,
        directory_path: str | None = None,
        project: str | None = None,
        repo: str | None = None,
        work_item_id: str | None = None,
        pr_id: str | None = None,
        top: int = 100,
        cursor: str | None = None,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        self.ensure_schema()
        offset = _parse_cursor(cursor)
        limit = max(1, min(200, int(top)))
        where = ["occurred_at >= ?"]
        params: list[Any] = [_to_iso(since)]
        if until is not None:
            where.append("occurred_at <= ?")
            params.append(_to_iso(until))
        if actor:
            where.append(
                """
                (
                    actor_unique_name LIKE ? COLLATE NOCASE
                    OR actor_email LIKE ? COLLATE NOCASE
                    OR actor_display_name LIKE ? COLLATE NOCASE
                )
                """
            )
            actor_param = f"%{actor.strip()}%"
            params.extend([actor_param, actor_param, actor_param])
        if activity_kinds:
            placeholders = ", ".join("?" for _ in activity_kinds)
            where.append(f"activity_kind IN ({placeholders})")
            params.extend(activity_kinds)
        for column, value in (
            ("directory_path", directory_path),
            ("project", project),
            ("repo", repo),
            ("work_item_id", work_item_id),
            ("pr_id", pr_id),
        ):
            normalized = _text(value)
            if normalized is None:
                continue
            where.append(f"{column} = ?")
            params.append(normalized)

        where_sql = " AND ".join(where)
        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM activity_records WHERE {where_sql}",
                params,
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT *
                FROM activity_records
                WHERE {where_sql}
                ORDER BY occurred_at DESC, activity_id ASC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
            states = conn.execute(
                """
                SELECT sync_key, complete_through, updated_at, metadata_json
                FROM sync_state
                ORDER BY sync_key
                """
            ).fetchall()

        items = [_row_to_item(row, include_raw=include_raw) for row in rows]
        next_offset = offset + len(items)
        has_more = next_offset < int(total)
        return {
            "items": items,
            "count": len(items),
            "paging": {
                "top": limit,
                "cursor": str(offset) if cursor is not None else None,
                "next_cursor": str(next_offset) if has_more else None,
                "has_more": has_more,
                "returned_count": len(items),
                "total_matching_count": int(total),
            },
            "index": {"sync_state": [_sync_state_row_to_item(row) for row in states]},
        }

    def update_sync_state(
        self,
        *,
        sync_key: str,
        complete_through: datetime | None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.ensure_schema()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_state (sync_key, complete_through, updated_at, metadata_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(sync_key) DO UPDATE SET
                    complete_through = excluded.complete_through,
                    updated_at = excluded.updated_at,
                    metadata_json = excluded.metadata_json
                """,
                (
                    sync_key,
                    _to_iso_or_none(complete_through),
                    _utc_now_iso(),
                    _json_dumps(metadata or {}),
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


def _row_to_item(row: sqlite3.Row, *, include_raw: bool) -> dict[str, Any]:
    item = {
        "activity_id": row["activity_id"],
        "activity_kind": row["activity_kind"],
        "occurred_at": row["occurred_at"],
        "actor": {
            "display_name": row["actor_display_name"],
            "unique_name": row["actor_unique_name"],
            "email": row["actor_email"],
        },
        "directory_path": row["directory_path"],
        "organization_url": row["organization_url"],
        "project": row["project"],
        "repo": row["repo"],
        "work_item_id": row["work_item_id"],
        "pr_id": row["pr_id"],
        "title": row["title"],
        "state": row["state"],
        "work_item_type": row["work_item_type"],
        "url": row["url"],
        "summary": row["summary"],
        "changed_fields": _json_loads(row["changed_fields_json"], {}),
        "source_revision": row["source_revision"],
        "source_updated_at": row["source_updated_at"],
        "ingested_at": row["ingested_at"],
    }
    if include_raw:
        item["raw"] = _json_loads(row["raw_json"], {})
    return item


def _sync_state_row_to_item(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "sync_key": row["sync_key"],
        "complete_through": row["complete_through"],
        "updated_at": row["updated_at"],
        "metadata": _json_loads(row["metadata_json"], {}),
    }


def _parse_cursor(value: str | None) -> int:
    if value is None or str(value).strip() == "":
        return 0
    try:
        offset = int(str(value).strip())
    except ValueError as exc:
        raise ValueError("cursor must be a non-negative integer offset") from exc
    if offset < 0:
        raise ValueError("cursor must be a non-negative integer offset")
    return offset


def _required(record: Mapping[str, Any], key: str) -> str:
    value = _text(record.get(key))
    if value is None:
        raise ValueError(f"{key} is required")
    return value


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_iso_or_none(value: object) -> str | None:
    if value is None:
        return None
    return _to_iso(value)


def _to_iso(value: object) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    else:
        raise ValueError("datetime value is required")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=_json_default)


def _json_loads(value: str, default: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _json_default(value: object) -> object:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
