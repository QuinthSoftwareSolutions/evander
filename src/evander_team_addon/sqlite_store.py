"""Team state transactions, revisions, and consistent snapshots."""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from types import TracebackType
from typing import Any, Callable

from pydantic import ConfigDict, PrivateAttr, TypeAdapter

from evander.addons import addon_storage_path
from evander.models import utc_now
from evander.orchestration.isolated_maintenance import sqlite_busy
from evander.repositories.orchestration_db import SQLITE_BUSY_TIMEOUT_MS

from .case_context_store import CaseContextStoreMixin
from .lifecycle_candidates import LifecycleCandidate, LifecycleCandidatePage
from .models import (
    SCHEMA_VERSION,
    TeamDispatchActivity,
    TeamIdempotencyRecord,
    TeamOutboxRecord,
    TeamState,
)
from .sqlite_chat import (
    ChatStoreMixin,
)
from .sqlite_codec import (
    T,
    _datetime_value,
    _idempotency_key_values,
    _json_values_equal,
    _utc_datetime_value,
    _replace_state_result,
    _state_content_changed,
    _state_content_payload,
    _state_result_candidate,
)
from .sqlite_codec import (
    TeamIdempotencyConflictError as TeamIdempotencyConflictError,
)
from .sqlite_codec import (
    _backfill_position_values as _backfill_position_values,
)
from .sqlite_codec import (
    _bool as _bool,
)
from .sqlite_codec import (
    _datetime_text as _datetime_text,
)
from .sqlite_codec import (
    _ensure_column as _ensure_column,
)
from .sqlite_codec import (
    _grouped_rows as _grouped_rows,
)
from .sqlite_codec import (
    _grouped_values as _grouped_values,
)
from .sqlite_codec import (
    _insert_position_values as _insert_position_values,
)
from .sqlite_codec import (
    _int_bool as _int_bool,
)
from .sqlite_codec import (
    _json_dumps as _json_dumps,
)
from .sqlite_codec import (
    _json_loads as _json_loads,
)
from .sqlite_codec import (
    _json_normalize as _json_normalize,
)
from .sqlite_codec import (
    _optional_bool_db as _optional_bool_db,
)
from .sqlite_codec import (
    _optional_db_bool as _optional_db_bool,
)
from .sqlite_codec import (
    _optional_text as _optional_text,
)
from .sqlite_codec import (
    _outbox_status_value as _outbox_status_value,
)
from .sqlite_codec import (
    _raise_idempotency_conflict as _raise_idempotency_conflict,
)
from .sqlite_codec import (
    _required_text as _required_text,
)
from .sqlite_codec import (
    normalized_payload_digest as normalized_payload_digest,
)
from .sqlite_deliverables import (
    DeliverableStoreMixin,
)
from .sqlite_git import (
    GitStoreMixin,
)
from .sqlite_inbox_memory import (
    InboxMemoryStoreMixin,
)
from .sqlite_journal import (
    CommandJournalStoreMixin,
)
from .sqlite_migrations_acceptance import (
    _REJECTED_ACCEPTANCE_MIGRATION_ACTOR as _REJECTED_ACCEPTANCE_MIGRATION_ACTOR,
)
from .sqlite_migrations_acceptance import (
    _REJECTED_ACCEPTANCE_MIGRATION_REASON as _REJECTED_ACCEPTANCE_MIGRATION_REASON,
)
from .sqlite_migrations_acceptance import (
    _acceptance_decision_is_rejected as _acceptance_decision_is_rejected,
)
from .sqlite_migrations_acceptance import (
    _close_invalid_rejection_override_inbox_items as _close_invalid_rejection_override_inbox_items,
)
from .sqlite_migrations_acceptance import (
    _close_stale_task_resolution_inbox_items as _close_stale_task_resolution_inbox_items,
)
from .sqlite_migrations_acceptance import (
    _normalize_rejected_acceptance_override_rows as _normalize_rejected_acceptance_override_rows,
)
from .sqlite_migrations_acceptance import (
    _open_rejected_acceptance_migration_blocker as _open_rejected_acceptance_migration_blocker,
)
from .sqlite_migrations_acceptance import (
    _reconcile_rejected_acceptance_overrides as _reconcile_rejected_acceptance_overrides,
)
from .sqlite_migrations_acceptance import (
    _release_ids_depending_on_deliverables as _release_ids_depending_on_deliverables,
)
from .sqlite_migrations_acceptance import (
    _terminal_ancestor_ids_for_reopened_tasks as _terminal_ancestor_ids_for_reopened_tasks,
)
from .sqlite_migrations_git import (
    _LEGACY_MIXED_GIT_BASE_ERROR as _LEGACY_MIXED_GIT_BASE_ERROR,
)
from .sqlite_migrations_git import (
    _block_legacy_release_git_base_conflict as _block_legacy_release_git_base_conflict,
)
from .sqlite_migrations_git import (
    _block_legacy_task_git_base_conflict as _block_legacy_task_git_base_conflict,
)
from .sqlite_migrations_git import (
    _has_dependency_composed_release_base_candidates as _has_dependency_composed_release_base_candidates,
)
from .sqlite_migrations_git import (
    _legacy_correction_release_ids_by_task as _legacy_correction_release_ids_by_task,
)
from .sqlite_migrations_git import (
    _legacy_deliverable_git_snapshots as _legacy_deliverable_git_snapshots,
)
from .sqlite_migrations_git import (
    _legacy_release_candidate_snapshot as _legacy_release_candidate_snapshot,
)
from .sqlite_migrations_git import (
    _legacy_task_has_authoritative_git_work as _legacy_task_has_authoritative_git_work,
)
from .sqlite_migrations_git import (
    _migrate_release_canonical_base_snapshots as _migrate_release_canonical_base_snapshots,
)
from .sqlite_migrations_git import (
    _migrate_release_lineage as _migrate_release_lineage,
)
from .sqlite_migrations_git import (
    _migrate_task_git_base_snapshots as _migrate_task_git_base_snapshots,
)
from .sqlite_migrations_git import (
    _reconcile_release_base_outbox_records as _reconcile_release_base_outbox_records,
)
from .sqlite_migrations_membership import (
    _ensure_project_assignments_from_legacy as _ensure_project_assignments_from_legacy,
)
from .sqlite_migrations_membership import (
    _ensure_team_agents_nullable_membership as _ensure_team_agents_nullable_membership,
)
from .sqlite_migrations_membership import (
    _ensure_team_conversations_operator_scope as _ensure_team_conversations_operator_scope,
)
from .sqlite_migrations_membership import (
    _migrate_human_review_relationships as _migrate_human_review_relationships,
)
from .sqlite_migrations_membership import (
    _migrate_role_task_queue as _migrate_role_task_queue,
)
from .sqlite_migrations_membership import (
    _migrate_role_task_queue_columns as _migrate_role_task_queue_columns,
)
from .sqlite_migrations_membership import (
    _team_conversations_has_agent_unique_index as _team_conversations_has_agent_unique_index,
)
from .sqlite_migrations_outcomes import (
    _KNOWN_OUTCOME_KIND_MIGRATIONS as _KNOWN_OUTCOME_KIND_MIGRATIONS,
)
from .sqlite_migrations_outcomes import (
    _migrate_legacy_outcome_kind_definitions as _migrate_legacy_outcome_kind_definitions,
)
from .sqlite_migrations_worktrees import (
    _legacy_cleanup_candidate_context_id as _legacy_cleanup_candidate_context_id,
)
from .sqlite_migrations_worktrees import (
    _migrate_worktree_cleanup_context_ids as _migrate_worktree_cleanup_context_ids,
)
from .sqlite_migrations_worktrees import (
    _migrate_worktree_cleanup_directory_scopes as _migrate_worktree_cleanup_directory_scopes,
)
from .sqlite_migrations_worktrees import (
    _migrate_worktree_cleanup_evidence as _migrate_worktree_cleanup_evidence,
)
from .sqlite_migrations_worktrees import (
    _migrate_worktree_cleanup_retry_dispositions as _migrate_worktree_cleanup_retry_dispositions,
)
from .sqlite_organization import (
    OrganizationStoreMixin,
)
from .sqlite_persistence_map import (
    _PERSISTENCE_GROUPS,
    _STATE_TABLES,
)
from .sqlite_releases import (
    ReleaseStoreMixin,
)
from .sqlite_reviews import (
    ReviewStoreMixin,
)
from .sqlite_schema import (
    migrate_schema,
)
from .sqlite_schema_sql import (
    _LIFECYCLE_INDEX_SQL as _LIFECYCLE_INDEX_SQL,
)
from .sqlite_schema_sql import (
    _SCHEMA_SQL as _SCHEMA_SQL,
)
from .sqlite_schema_sql import (
    SQLITE_SCHEMA_VERSION,
)
from .sqlite_tasks import (
    TaskStoreMixin,
)

ADDON_ID = "team-addon"

STATE_DB_FILE = "state.db"

_SQLITE_WAL_ENABLE_TIMEOUT_SECONDS = 5.0
logger = logging.getLogger(__name__)

_STATE_METADATA_FIELDS = frozenset({"schema_version", "revision", "updated_at"})

_STATE_COLLECTION_FIELDS = frozenset(TeamState.model_fields) - _STATE_METADATA_FIELDS

_STATE_FIELD_ADAPTERS = {
    name: TypeAdapter(field.annotation) for name, field in TeamState.model_fields.items()
}

_STATE_GROUP_READERS = {
    "notes": "_read_case_context",
    "case_context_views": "_read_case_context",
    "case_context_snapshots": "_read_case_context",
    "human_review_requests": "_read_human_reviews",
    "human_review_decisions": "_read_human_reviews",
    "human_review_findings": "_read_human_reviews",
}


class TeamStateReadProjection(TeamState):
    """A validated read projection, never a candidate for a state update.

    Rows are validated individually from a committed SQLite snapshot; the
    cross-collection invariants were checked when that snapshot was written.
    Accessing an omitted collection fails instead of treating missing data as
    an empty collection, which protects derived values and authorization checks.
    """

    model_config = ConfigDict(frozen=True)
    _loaded_collections: frozenset[str] = PrivateAttr(default_factory=frozenset)

    def __getattribute__(self, name: str) -> Any:
        if name in _STATE_COLLECTION_FIELDS:
            loaded = object.__getattribute__(self, "__pydantic_private__")
            if name not in loaded["_loaded_collections"]:
                raise ValueError(f"Team read projection did not load collection {name!r}")
        return super().__getattribute__(name)


def _require_complete_state(state: TeamState) -> None:
    if isinstance(state, TeamStateReadProjection):
        raise ValueError("Team read projections cannot be persisted")


class TeamStateSqliteStore(
    CommandJournalStoreMixin,
    OrganizationStoreMixin,
    TaskStoreMixin,
    DeliverableStoreMixin,
    ReviewStoreMixin,
    InboxMemoryStoreMixin,
    ChatStoreMixin,
    ReleaseStoreMixin,
    GitStoreMixin,
    CaseContextStoreMixin,
):
    def __init__(self, runtime_dir: str | Path, *, maintenance: bool = False) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.path = addon_storage_path(self.runtime_dir, ADDON_ID, STATE_DB_FILE)
        self._lock = RLock()
        self._busy_timeout_ms = SQLITE_BUSY_TIMEOUT_MS
        self._maintenance = False
        self.ensure_schema()
        self._initialize_if_empty()
        self._maintenance = maintenance
        if maintenance:
            self._busy_timeout_ms = 100

    def read_object(self) -> dict[str, Any]:
        with self._lock:
            with self._connect() as connection:
                # _read_payload spans normalized tables. Pin one SQLite read
                # transaction so every row belongs to the same revision.
                connection.execute("BEGIN")
                return self._read_payload(connection)

    def read_state(self) -> TeamState:
        """Read and validate Team state once without a JSON model round-trip."""

        with self._lock:
            with self._connect() as connection:
                # Without an explicit transaction, each completed SELECT may
                # observe a later writer commit and assemble an impossible
                # cross-revision TeamState (for example, half a review cohort).
                connection.execute("BEGIN")
                return self._read_state(connection)

    def read_projection(self, *, collections: Sequence[str]) -> TeamStateReadProjection:
        """Read only the collections needed by a read-only API projection."""

        selected = frozenset(collections)
        unknown = selected - _STATE_COLLECTION_FIELDS
        if unknown:
            raise ValueError(f"Unknown Team projection collections: {sorted(unknown)!r}")
        # WAL readers can take a consistent snapshot without waiting for the
        # process-local writer lock or resetting the journal mode.
        with closing(sqlite3.connect(self.path, timeout=30.0)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            payload = self._read_state_payload(connection, collections=selected)
        validated = {
            name: _STATE_FIELD_ADAPTERS[name].validate_python(value)
            for name, value in payload.items()
            if name in _STATE_METADATA_FIELDS or name in selected
        }
        projection = TeamStateReadProjection.model_construct(**validated)
        projection._loaded_collections = selected
        return projection

    def read_revision(self) -> int:
        """Read the current state revision without reconstructing the Team state."""

        # Do not take the process-local state lock or reapply WAL mode here. This
        # read is used by one-second UI polls and WAL readers can safely proceed
        # while a state update holds its write transaction.
        with closing(sqlite3.connect(self.path, timeout=30.0)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA query_only=ON")
            row = connection.execute(
                "SELECT value FROM team_state_metadata WHERE key = 'revision'"
            ).fetchone()
        return int(row["value"]) if row is not None else 0

    def lifecycle_candidates(
        self,
        *,
        kind: str = "completion",
        after: str = "",
        limit: int = 50,
        due_at: datetime | None = None,
        dispatch_ids: Sequence[str] | None = None,
    ) -> LifecycleCandidatePage:
        """Pin revision and narrowly projected rows in one read transaction.

        Keyset cursors are hints only: mutations always revalidate in the runtime
        service. The historical receipt lane is separately paginated so old
        terminal dispatches cannot delay leases or active completions.
        """
        limit = max(1, min(50, limit))
        params: list[Any] = []
        if dispatch_ids is not None:
            ids = sorted(set(dispatch_ids))
            if len(ids) > 50:
                raise ValueError("lifecycle dispatch lookup is limited to 50 IDs")
            source = (
                "SELECT id FROM team_dispatches WHERE id IN (" + ",".join("?" for _ in ids) + ")"
            )
            params.extend(ids)
        elif kind == "leases":
            source = """
                SELECT d.id FROM team_agent_runtimes r
                JOIN team_dispatches d ON d.id = r.active_dispatch_id AND d.agent_id = r.agent_id
                WHERE d.status = 'leased' AND d.lease_expires_at IS NOT NULL
                  AND r.wake_leased_until IS NOT NULL
                  AND (julianday(d.lease_expires_at) <= julianday(?)
                    OR julianday(r.wake_leased_until) <= julianday(?))
            """
            params.extend([(due_at or utc_now()).isoformat()] * 2)
        elif kind == "history":
            source = "SELECT id FROM team_dispatches WHERE activation_completed_at IS NOT NULL"
        elif kind == "completion":
            source = """
                SELECT active_dispatch_id AS id, 0 AS priority FROM team_agent_runtimes WHERE active_dispatch_id IS NOT NULL
                UNION SELECT id, 0 FROM team_dispatches
                  WHERE status IN ('preparing', 'leased') AND activation_completed_at IS NULL
                UNION SELECT j.value AS id, 1 FROM team_agent_runtimes r,
                  json_each(r.work_lineage_thread_dispatch_ids_json) j
            """
        else:
            raise ValueError(f"unknown lifecycle candidate kind: {kind}")

        # Include governance reservations in the same snapshot, with their
        # manager's current session. They have no team_dispatches row.
        governance = ""
        if kind == "completion":
            governance = """
                UNION ALL SELECT g.active_dispatch_id, a.session_id, a.id, NULL, 1,
                  NULL, NULL, NULL, NULL, NULL, a.session_id, g.queued_at,
                  g.governance_group_id, 0
                FROM team_governance_runtimes g
                JOIN team_governance_groups gg ON gg.id = g.governance_group_id
                JOIN team_agents a ON a.id = gg.manager_agent_id
                WHERE g.active_dispatch_id IS NOT NULL AND g.wake_leased_until IS NULL
            """
            if dispatch_ids is not None:
                governance += " AND g.active_dispatch_id IN (" + ",".join("?" for _ in ids) + ")"
                params.extend(ids)
        prioritized = kind == "completion" and dispatch_ids is None
        id_source = (
            f"SELECT id, MIN(priority) AS priority FROM ({source}) GROUP BY id"
            if prioritized
            else source
        )
        priority = "ids.priority" if prioritized else "0"
        sql = f"""
            WITH ids AS ({id_source}), candidates AS (
              SELECT d.id AS dispatch_id, d.session_id, d.agent_id, d.status,
                COALESCE(r.active_dispatch_id = d.id, 0) AS active,
                d.activation_completed_at, d.native_thread_id, d.native_thread_observed_at,
                (SELECT value FROM json_each(r.work_lineage_thread_ids_json) WHERE key = d.work_lineage_id) AS lineage_thread_id,
                (SELECT value FROM json_each(r.work_lineage_thread_dispatch_ids_json) WHERE key = d.work_lineage_id) AS lineage_owner_id,
                a.session_id AS current_session_id,
                d.queued_at, NULL AS governance_group_id, {priority} AS recovery_priority
              FROM team_dispatches d JOIN ids ON ids.id = d.id
              LEFT JOIN team_agent_runtimes r ON r.agent_id = d.agent_id
              LEFT JOIN team_agents a ON a.id = d.agent_id
              {governance}
            )
            SELECT * FROM candidates
            WHERE (recovery_priority > ? OR (recovery_priority = ? AND dispatch_id > ?))
            ORDER BY recovery_priority, dispatch_id LIMIT ?
        """
        after_priority = 0
        if kind == "completion" and after:
            priority_text, after = after.split(":", 1)
            after_priority = int(priority_text)
        with self._read_connection() as connection:
            connection.execute("BEGIN")
            revision_row = connection.execute(
                "SELECT value FROM team_state_metadata WHERE key = 'revision'"
            ).fetchone()
            rows = connection.execute(
                sql, [*params, after_priority, after_priority, after, limit + 1]
            ).fetchall()
        candidates = []
        for row in rows[:limit]:
            data = dict(row)
            for name in ("activation_completed_at", "native_thread_observed_at", "queued_at"):
                data[name] = datetime.fromisoformat(data[name]) if data[name] else None
                if data[name] is not None:
                    data[name] = data[name].replace(tzinfo=data[name].tzinfo or UTC).astimezone(UTC)
            data["active"] = bool(data["active"])
            candidates.append(LifecycleCandidate(**data))
        return LifecycleCandidatePage(
            revision=int(revision_row["value"]) if revision_row else 0,
            candidates=tuple(candidates),
            next_cursor=(
                candidates[-1].cursor if kind == "completion" else candidates[-1].dispatch_id
            )
            if len(rows) > limit
            else None,
        )

    def write_object(self, payload: dict[str, Any]) -> None:
        state = TeamState.model_validate(payload)
        with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._write_state(connection, state)

    def renew_dispatch_lease(
        self,
        *,
        agent_id: str,
        dispatch_id: str,
        renewal_due_at: datetime,
        renewed_until: datetime,
        now: datetime,
        expected_revision: int | None = None,
    ) -> tuple[bool, int, datetime, bool]:
        """Renew only the current leased dispatch/runtime pair atomically.

        These timestamp-only changes preserve all previously validated Team
        relationships. Matching identity, live status, both lease timestamps,
        and the active runtime reservation are checked under the writer lock;
        a replaced or terminal dispatch can never be revived by this path.
        """
        with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                metadata = dict(connection.execute(
                    "SELECT key, value FROM team_state_metadata "
                    "WHERE key IN ('revision', 'updated_at')"
                ))
                revision = int(metadata["revision"])
                updated_at = _datetime_value(metadata["updated_at"])
                if expected_revision is not None and revision != expected_revision:
                    return False, revision, updated_at, False
                row = connection.execute(
                    """SELECT d.lease_expires_at, r.wake_leased_until, r.updated_at
                       FROM team_dispatches AS d
                       JOIN team_agent_runtimes AS r ON r.agent_id = d.agent_id
                       WHERE d.id = ? AND d.agent_id = ? AND d.status = 'leased'
                         AND r.active_dispatch_id = d.id
                         AND d.lease_expires_at IS NOT NULL
                         AND r.wake_leased_until IS NOT NULL""",
                    (dispatch_id, agent_id),
                ).fetchone()
                if row is None:
                    return True, revision, updated_at, False
                lease_expires_at = _utc_datetime_value(row["lease_expires_at"])
                wake_leased_until = _utc_datetime_value(row["wake_leased_until"])
                if lease_expires_at > renewal_due_at and wake_leased_until > renewal_due_at:
                    return True, revision, updated_at, False
                if (
                    lease_expires_at == renewed_until
                    and wake_leased_until == renewed_until
                    and _utc_datetime_value(row["updated_at"]) == now
                ):
                    return True, revision, updated_at, True
                connection.execute(
                    "UPDATE team_dispatches SET lease_expires_at = ? WHERE id = ?",
                    (renewed_until.isoformat(), dispatch_id),
                )
                connection.execute(
                    "UPDATE team_agent_runtimes SET wake_leased_until = ?, updated_at = ? "
                    "WHERE agent_id = ?",
                    (renewed_until.isoformat(), now.isoformat(), agent_id),
                )
                revision += 1
                updated_at = utc_now()
                connection.executemany(
                    "UPDATE team_state_metadata SET value = ? WHERE key = ?",
                    (
                        (str(revision), "revision"),
                        (updated_at.isoformat(), "updated_at"),
                        (str(SCHEMA_VERSION), "state_schema_version"),
                    ),
                )
                return True, revision, updated_at, True


    @contextmanager
    def _state_update_transaction(
        self,
    ) -> Iterator[tuple[sqlite3.Connection, TeamState, dict[str, Any]]]:
        """Prepare a coherent baseline before reserving the SQLite writer.

        Only snapshot reads may repeat. Mutators and their command-side writes
        run exactly once, after verifying the baseline revision under the writer
        lock. Under sustained contention the final attempt reads under that lock
        to guarantee progress with the same callback semantics as before.
        """
        for _attempt in range(3):
            with self._connect() as reader:
                reader.execute("BEGIN")
                state = self._read_state(reader)
                baseline = state.model_dump(mode="json")
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if self._read_revision_from_connection(connection) != state.revision:
                    continue
                yield connection, state, baseline
                return
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._read_state(connection)
            yield connection, state, state.model_dump(mode="json")

    def update_object(
        self,
        updater: Callable[[dict[str, Any]], T | None],
    ) -> T | None:
        with self._lock:
            with self._state_update_transaction() as (connection, previous_state, payload):
                result = updater(payload)
                state = self._persist_updated_payload(
                    connection,
                    previous_state=previous_state,
                    payload=payload,
                )
                return _replace_state_result(result, state)

    def update_state(
        self,
        updater: Callable[[TeamState], TeamState | None],
    ) -> TeamState:
        """Atomically update typed state with a revision-checked read baseline."""

        with self._lock:
            with self._state_update_transaction() as (connection, state, previous_payload):
                previous_schema_version = state.schema_version
                previous_revision = state.revision
                previous_updated_at = state.updated_at
                candidate = updater(state) or state
                return self._persist_updated_state(
                    connection,
                    previous_payload=previous_payload,
                    previous_schema_version=previous_schema_version,
                    previous_revision=previous_revision,
                    previous_updated_at=previous_updated_at,
                    candidate=candidate,
                )

    def update_object_with_command_transaction(
        self,
        updater: Callable[[dict[str, Any], TeamStateSqliteCommandTransaction], T],
    ) -> T:
        with self._lock:
            with self._state_update_transaction() as (connection, previous_state, payload):
                transaction = TeamStateSqliteCommandTransaction(self, connection)
                result = updater(payload, transaction)
                state = self._persist_updated_payload(
                    connection,
                    previous_state=previous_state,
                    payload=payload,
                )
                return _replace_state_result(result, state)

    def update_state_with_command_transaction(
        self,
        updater: Callable[[TeamState, TeamStateSqliteCommandTransaction], T],
    ) -> T:
        """Commit state, activity, idempotency and outbox in one transaction.

        Baseline reconstruction happens before the writer reservation. The
        callback and full candidate validation remain inside the transaction so
        callback failures and invalid mutations also roll back command records.
        """
        with self._lock:
            with self._state_update_transaction() as (connection, state, previous_payload):
                previous_schema_version = state.schema_version
                previous_revision = state.revision
                previous_updated_at = state.updated_at
                transaction = TeamStateSqliteCommandTransaction(self, connection)
                result = updater(state, transaction)
                candidate = _state_result_candidate(result, fallback=state)
                persisted_state = self._persist_updated_state(
                    connection,
                    previous_payload=previous_payload,
                    previous_schema_version=previous_schema_version,
                    previous_revision=previous_revision,
                    previous_updated_at=previous_updated_at,
                    candidate=candidate,
                )
                return _replace_state_result(result, persisted_state)

    def commit_prepared_state_with_command_transaction(
        self,
        *,
        previous_payload: Mapping[str, Any],
        candidate: TeamState,
        command_writer: Callable[[TeamStateSqliteCommandTransaction], None],
    ) -> tuple[bool, int, TeamState | None]:
        """Commit a precomputed Team-state delta under a short revision CAS.

        Full candidate validation and record-delta discovery intentionally happen
        before ``BEGIN IMMEDIATE``. The write transaction only verifies that the
        planning snapshot is still current, applies the prepared row changes, and
        persists command-side records atomically. Callers must replan when the
        returned committed flag is false.
        """

        baseline = dict(previous_payload)
        expected_revision = int(baseline.get("revision", 0))
        previous_schema_version = int(baseline.get("schema_version", SCHEMA_VERSION))
        previous_updated_at = _datetime_value(baseline.get("updated_at"))
        prepared = self._prepare_updated_state(
            previous_payload=baseline,
            previous_schema_version=previous_schema_version,
            previous_revision=expected_revision,
            previous_updated_at=previous_updated_at,
            candidate=candidate,
        )

        with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current_revision = self._read_revision_from_connection(connection)
                if current_revision != expected_revision:
                    return False, current_revision, None
                transaction = TeamStateSqliteCommandTransaction(self, connection)
                command_writer(transaction)
                if prepared.state_changed:
                    self._write_state_delta_payloads(
                        connection,
                        previous=baseline,
                        current=prepared.current_payload,
                        state=prepared.state,
                        changed_by_field=prepared.changed_by_field,
                    )
                return True, current_revision, prepared.state

    def _persist_updated_payload(
        self,
        connection: sqlite3.Connection,
        *,
        previous_state: TeamState,
        payload: dict[str, Any],
    ) -> TeamState:
        candidate = TeamState.model_validate(payload)
        previous_content = _state_content_payload(previous_state)
        candidate_content = _state_content_payload(candidate)
        state_changed = previous_content != candidate_content
        if not state_changed:
            payload.clear()
            payload.update(previous_state.model_dump(mode="json"))
            return previous_state
        updated_at = (
            candidate.updated_at if candidate.updated_at != previous_state.updated_at else utc_now()
        )
        state = TeamState.model_validate(
            candidate.model_copy(
                update={
                    "schema_version": SCHEMA_VERSION,
                    "revision": previous_state.revision + 1,
                    "updated_at": updated_at,
                },
                deep=True,
            ).model_dump(mode="json")
        )
        payload.clear()
        payload.update(state.model_dump(mode="json"))
        self._write_state_delta(
            connection,
            previous_state=previous_state,
            state=state,
        )
        return state

    def _persist_updated_state(
        self,
        connection: sqlite3.Connection,
        *,
        previous_payload: dict[str, Any],
        previous_schema_version: int,
        previous_revision: int,
        previous_updated_at: datetime,
        candidate: TeamState,
    ) -> TeamState:
        prepared = self._prepare_updated_state(
            previous_payload=previous_payload,
            previous_schema_version=previous_schema_version,
            previous_revision=previous_revision,
            previous_updated_at=previous_updated_at,
            candidate=candidate,
        )
        if prepared.state_changed:
            self._write_state_delta_payloads(
                connection,
                previous=previous_payload,
                current=prepared.current_payload,
                state=prepared.state,
                changed_by_field=prepared.changed_by_field,
            )
        return prepared.state

    def _prepare_updated_state(
        self,
        *,
        previous_payload: Mapping[str, Any],
        previous_schema_version: int,
        previous_revision: int,
        previous_updated_at: datetime,
        candidate: TeamState,
    ) -> _PreparedTeamStateUpdate:
        _require_complete_state(candidate)
        candidate_payload = candidate.model_dump(mode="json")
        candidate_updated_at = (
            candidate.updated_at if candidate.updated_at != previous_updated_at else utc_now()
        )
        candidate_payload["updated_at"] = candidate_updated_at.isoformat()
        validated = TeamState.model_validate(candidate_payload)
        current_payload = validated.model_dump(mode="json")
        state_changed = _state_content_changed(previous_payload, current_payload)
        if not state_changed:
            return _PreparedTeamStateUpdate(
                state=validated.model_copy(
                    update={
                        "schema_version": previous_schema_version,
                        "revision": previous_revision,
                        "updated_at": previous_updated_at,
                    }
                ),
                current_payload=current_payload,
                changed_by_field={},
                state_changed=False,
            )
        state = validated.model_copy(
            update={
                "schema_version": SCHEMA_VERSION,
                "revision": previous_revision + 1,
                "updated_at": validated.updated_at,
            }
        )
        current_payload.update(
            {
                "schema_version": SCHEMA_VERSION,
                "revision": state.revision,
                "updated_at": state.updated_at.isoformat(),
            }
        )
        return _PreparedTeamStateUpdate(
            state=state,
            current_payload=current_payload,
            changed_by_field=self._changed_state_record_ids(
                previous=previous_payload,
                current=current_payload,
            ),
            state_changed=True,
        )

    @staticmethod
    def _read_revision_from_connection(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT value FROM team_state_metadata WHERE key = 'revision'"
        ).fetchone()
        return int(row["value"]) if row is not None else 0

    @staticmethod
    def _changed_state_record_ids(
        *,
        previous: Mapping[str, Any],
        current: Mapping[str, Any],
    ) -> dict[str, set[str]]:
        changed_by_field: dict[str, set[str]] = {}
        for fields, _delete_specs, _writer_name in _PERSISTENCE_GROUPS:
            for field_name in fields:
                before = previous[field_name]
                after = current[field_name]
                changed_by_field[field_name] = {
                    record_id
                    for record_id in set(before) | set(after)
                    if not _json_values_equal(before.get(record_id), after.get(record_id))
                }
        return changed_by_field

    def ensure_schema(self) -> None:
        with self._lock:
            with self._connect() as connection:
                migrate_schema(
                    connection,
                    read_payload=self._read_payload,
                    write_state=self._write_state,
                )

    def _read_connection(self) -> sqlite3.Connection:
        """Read a committed snapshot without negotiating WAL or a writer lock."""
        started = time.perf_counter()
        connection = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=self._busy_timeout_ms / 1000,
            factory=_ClosingSqliteConnection,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
        except BaseException:
            connection.close()
            raise
        logger.debug(
            "Team lifecycle phase=database_connection duration_ms=%.1f mode=read outcome=succeeded",
            (time.perf_counter() - started) * 1000,
        )
        return connection

    def _connect(self) -> sqlite3.Connection:
        started = time.perf_counter()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path,
            timeout=self._busy_timeout_ms / 1000,
            factory=_ClosingSqliteConnection,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            if not self._maintenance:
                self._enable_wal(connection)
            connection.execute("PRAGMA foreign_keys=ON")
        except BaseException as exc:
            connection.close()
            if self._maintenance:
                logger.warning(
                    "Team lifecycle phase=database_connection duration_ms=%.1f "
                    "mode=maintenance outcome=%s",
                    (time.perf_counter() - started) * 1000,
                    "busy" if sqlite_busy(exc) else "failed",
                )
            raise
        if self._maintenance:
            logger.debug(
                "Team lifecycle phase=database_connection duration_ms=%.1f "
                "mode=maintenance outcome=succeeded",
                (time.perf_counter() - started) * 1000,
            )
        return connection

    @staticmethod
    def _enable_wal(connection: sqlite3.Connection) -> None:
        deadline = time.monotonic() + _SQLITE_WAL_ENABLE_TIMEOUT_SECONDS
        while True:
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).casefold() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def _initialize_if_empty(self) -> None:
        with self._lock:
            with self._connect() as connection:
                if self._has_state(connection):
                    return
                connection.execute("BEGIN IMMEDIATE")
                self._write_state(connection, TeamState.empty())

    @staticmethod
    def _has_state(connection: sqlite3.Connection) -> bool:
        row = connection.execute(
            "SELECT 1 FROM team_state_metadata WHERE key = 'revision'"
        ).fetchone()
        return row is not None

    def _read_payload(self, connection: sqlite3.Connection) -> dict[str, Any]:
        return self._read_state(connection).model_dump(mode="json")

    def _read_state(self, connection: sqlite3.Connection) -> TeamState:
        return TeamState.model_validate(self._read_state_payload(connection))

    def _read_state_payload(
        self,
        connection: sqlite3.Connection,
        *,
        collections: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        metadata = {
            str(row["key"]): str(row["value"])
            for row in connection.execute("SELECT key, value FROM team_state_metadata")
        }
        if "revision" not in metadata:
            return TeamState.empty().model_dump(mode="python")

        payload: dict[str, Any] = {
            "schema_version": int(metadata.get("state_schema_version", SCHEMA_VERSION)),
            "revision": int(metadata.get("revision", "0")),
            "updated_at": metadata.get("updated_at") or utc_now().isoformat(),
            "teams": {},
            "governance_groups": {},
            "governance_agenda": {},
            "governance_runtimes": {},
            "projects": {},
            "roles": {},
            "agents": {},
            "project_assignments": {},
            "agent_runtimes": {},
            "work_lineages": {},
            "external_source_evidence_snapshots": {},
            "cases": {},
            "tasks": {},
            "deliverables": {},
            "review_requests": {},
            "outcome_kind_definitions": {},
            "review_profiles": {},
            "inbox": {},
            "blockers": {},
            "dependency_composition_recoveries": {},
            "blocker_events": {},
            "action_records": {},
            "memories": {},
            "conversations": {},
            "conversation_messages": {},
            "external_chat_ingress": {},
            "external_chat_deliveries": {},
            "chat_attachments": {},
            "chat_attachment_policies": {},
            "release_packages": {},
            "release_verification_runs": {},
            "human_review_requests": {},
            "human_review_decisions": {},
            "human_review_findings": {},
            "dispatches": {},
            "git_preparation_attempts": {},
            "worktree_cleanup_candidates": {},
            "ephemeral_assignments": {},
            "git_promotions": {},
            "git_work_contexts": {},
            "stand_downs": {},
        }
        readers = dict.fromkeys(
            _STATE_GROUP_READERS.get(name, f"_read_{name}")
            for name in TeamState.model_fields
            if name in _STATE_COLLECTION_FIELDS and (collections is None or name in collections)
        )
        for reader in readers:
            getattr(self, reader)(connection, payload)
        return payload

    def _write_state(self, connection: sqlite3.Connection, state: TeamState) -> None:
        _require_complete_state(state)
        payload = state.model_dump(mode="json")
        payload["schema_version"] = max(
            int(payload.get("schema_version", SCHEMA_VERSION)),
            SCHEMA_VERSION,
        )
        self._clear_state_tables(connection)
        self._write_teams(connection, payload["teams"])
        self._write_governance_groups(connection, payload["governance_groups"])
        self._write_governance_agenda(connection, payload["governance_agenda"])
        self._write_governance_runtimes(connection, payload["governance_runtimes"])
        self._write_roles(connection, payload["roles"])
        self._write_projects(connection, payload["projects"])
        self._write_agents(connection, payload["agents"])
        self._write_project_assignments(connection, payload["project_assignments"])
        self._write_stand_downs(connection, payload["stand_downs"])
        self._write_agent_runtimes(connection, payload["agent_runtimes"])
        self._write_work_lineages(connection, payload["work_lineages"])
        self._write_external_source_evidence_snapshots(
            connection,
            payload["external_source_evidence_snapshots"],
        )
        self._write_cases(connection, payload["cases"])
        self._write_tasks(connection, payload["tasks"])
        self._write_deliverables(connection, payload["deliverables"])
        self._write_outcome_kind_definitions(
            connection,
            payload["outcome_kind_definitions"],
        )
        self._write_review_profiles(connection, payload["review_profiles"])
        self._write_review_requests(connection, payload["review_requests"])
        self._write_inbox(connection, payload["inbox"])
        self._write_blockers(connection, payload["blockers"])
        self._write_dependency_composition_recoveries(
            connection,
            payload["dependency_composition_recoveries"],
        )
        self._write_blocker_events(connection, payload["blocker_events"])
        self._write_action_records(connection, payload["action_records"])
        self._write_notes(connection, payload["notes"])
        self._write_case_context_views(connection, payload["case_context_views"])
        self._write_case_context_snapshots(connection, payload["case_context_snapshots"])
        self._write_memories(connection, payload["memories"])
        self._write_conversations(connection, payload["conversations"])
        self._write_conversation_messages(connection, payload["conversation_messages"])
        self._write_external_chat_ingress(connection, payload["external_chat_ingress"])
        self._write_external_chat_deliveries(connection, payload["external_chat_deliveries"])
        self._write_chat_attachments(connection, payload["chat_attachments"])
        self._write_chat_attachment_policies(connection, payload["chat_attachment_policies"])
        self._write_release_packages(connection, payload["release_packages"])
        self._write_release_verification_runs(connection, payload["release_verification_runs"])
        self._write_human_reviews(
            connection,
            requests=payload["human_review_requests"],
            decisions=payload["human_review_decisions"],
            findings=payload["human_review_findings"],
        )
        self._write_dispatches(connection, payload["dispatches"])
        self._write_git_preparation_attempts(
            connection,
            payload["git_preparation_attempts"],
        )
        self._write_worktree_cleanup_candidates(
            connection,
            payload["worktree_cleanup_candidates"],
        )
        self._write_ephemeral_assignments(connection, payload["ephemeral_assignments"])
        self._write_git_promotions(connection, payload["git_promotions"])
        self._write_git_work_contexts(connection, payload["git_work_contexts"])
        connection.executemany(
            """
            INSERT INTO team_state_metadata(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (
                ("sqlite_schema_version", str(SQLITE_SCHEMA_VERSION)),
                ("state_schema_version", str(payload["schema_version"])),
                ("revision", str(payload["revision"])),
                ("updated_at", str(payload["updated_at"])),
            ),
        )

    def _write_state_delta(
        self,
        connection: sqlite3.Connection,
        *,
        previous_state: TeamState,
        state: TeamState,
    ) -> None:
        previous = previous_state.model_dump(mode="json")
        current = state.model_dump(mode="json")
        self._write_state_delta_payloads(
            connection,
            previous=previous,
            current=current,
            state=state,
        )

    def _write_state_delta_payloads(
        self,
        connection: sqlite3.Connection,
        *,
        previous: Mapping[str, Any],
        current: Mapping[str, Any],
        state: TeamState,
        changed_by_field: Mapping[str, set[str]] | None = None,
    ) -> None:
        if changed_by_field is None:
            changed_by_field = self._changed_state_record_ids(
                previous=previous,
                current=current,
            )

        for fields, delete_specs, writer_name in _PERSISTENCE_GROUPS:
            if not any(changed_by_field[field_name] for field_name in fields):
                continue
            for table_name, owner_column, field_name in delete_specs:
                record_ids = sorted(changed_by_field[field_name])
                if not record_ids:
                    continue
                placeholders = ", ".join("?" for _ in record_ids)
                connection.execute(
                    f"DELETE FROM {table_name} WHERE {owner_column} IN ({placeholders})",
                    record_ids,
                )
            if writer_name == "_write_human_reviews":
                self._write_human_reviews(
                    connection,
                    requests={
                        record_id: current["human_review_requests"][record_id]
                        for record_id in changed_by_field["human_review_requests"]
                        if record_id in current["human_review_requests"]
                    },
                    decisions={
                        record_id: current["human_review_decisions"][record_id]
                        for record_id in changed_by_field["human_review_decisions"]
                        if record_id in current["human_review_decisions"]
                    },
                    findings={
                        record_id: current["human_review_findings"][record_id]
                        for record_id in changed_by_field["human_review_findings"]
                        if record_id in current["human_review_findings"]
                    },
                )
                continue
            field_name = fields[0]
            writer = getattr(self, writer_name)
            writer(
                connection,
                {
                    record_id: current[field_name][record_id]
                    for record_id in changed_by_field[field_name]
                    if record_id in current[field_name]
                },
            )

        connection.executemany(
            """
            INSERT INTO team_state_metadata(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (
                ("sqlite_schema_version", str(SQLITE_SCHEMA_VERSION)),
                ("state_schema_version", str(SCHEMA_VERSION)),
                ("revision", str(state.revision)),
                ("updated_at", state.updated_at.isoformat()),
            ),
        )

    @staticmethod
    def _clear_state_tables(connection: sqlite3.Connection) -> None:
        for table in _STATE_TABLES:
            connection.execute(f"DELETE FROM {table}")


class _ClosingSqliteConnection(sqlite3.Connection):
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


@dataclass(frozen=True)
class _PreparedTeamStateUpdate:
    state: TeamState
    current_payload: dict[str, Any]
    changed_by_field: dict[str, set[str]]
    state_changed: bool


class TeamStateSqliteCommandTransaction:
    def __init__(
        self,
        store: TeamStateSqliteStore,
        connection: sqlite3.Connection,
    ) -> None:
        self._store = store
        self._connection = connection

    def get_idempotency_record(
        self,
        *,
        tool_name: str,
        caller_identity: str,
        scope_kind: str,
        scope_id: str,
        request_id: str,
    ) -> TeamIdempotencyRecord | None:
        key = _idempotency_key_values(
            tool_name=tool_name,
            caller_identity=caller_identity,
            scope_kind=scope_kind,
            scope_id=scope_id,
            request_id=request_id,
        )
        return self._store._lookup_idempotency_record(self._connection, **key)

    def store_idempotency_record(
        self,
        record: TeamIdempotencyRecord | Mapping[str, Any],
    ) -> TeamIdempotencyRecord:
        return self._store._insert_idempotency_record(self._connection, record)

    def append_dispatch_activity(
        self,
        activity: TeamDispatchActivity | Mapping[str, Any],
    ) -> TeamDispatchActivity:
        return self._store._insert_dispatch_activity(self._connection, activity)

    def list_dispatch_activity(
        self,
        *,
        dispatch_id: str | None = None,
        caller_session_id: str | None = None,
        caller_identity: str | None = None,
        agent_id: str | None = None,
        governance_group_id: str | None = None,
        assignment_id: str | None = None,
        tool_name: str | None = None,
        request_id: str | None = None,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> list[TeamDispatchActivity]:
        return self._store._list_dispatch_activity(
            self._connection,
            dispatch_id=dispatch_id,
            caller_session_id=caller_session_id,
            caller_identity=caller_identity,
            agent_id=agent_id,
            governance_group_id=governance_group_id,
            assignment_id=assignment_id,
            tool_name=tool_name,
            request_id=request_id,
            limit=limit,
            newest_first=newest_first,
        )

    def enqueue_outbox_record(
        self,
        record: TeamOutboxRecord | Mapping[str, Any],
    ) -> TeamOutboxRecord:
        return self._store._insert_outbox_record(self._connection, record)


__all__ = [
    "STATE_DB_FILE",
    "TeamIdempotencyConflictError",
    "TeamStateSqliteStore",
    "normalized_payload_digest",
]
