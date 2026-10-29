"""Revision-pinned working records for trusted Team lifecycle commands.

This is deliberately not a TeamState. Enumeration is an operational working
set; keyed references are fetched from the same revision on demand. Only a
validated delta may be committed, never the working set as aggregate state.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import time
from contextlib import contextmanager
from types import MethodType
from typing import get_args

from pydantic import TypeAdapter

from evander.models import utc_now
from evander.repositories.sqlite_runtime import storage_lock

from .models import TeamDispatchActivity, TeamOutboxRecord, TeamState
from .lifecycle_sql import (
    ACTION_CREATED_ORDER,
    DISPATCH_ATTEMPT_KEY,
    DISPATCH_ATTEMPT_ORDER,
    DISPATCH_COMPLETED_KEY,
    timestamp_microseconds,
    timestamp_microseconds_sql,
)
from .repository import TeamStateConflictError
from .sqlite_persistence_map import _PERSISTENCE_GROUPS
from .sqlite_store import (
    TeamStateSqliteCommandTransaction,
    _STATE_GROUP_READERS,
)

logger = logging.getLogger(__name__)
_TABLES = {
    field: tuple((table, key) for table, key, owner in specs if owner == field)
    for fields, specs, _writer in _PERSISTENCE_GROUPS
    for field in fields
}
_MODELS = {
    field: TypeAdapter(get_args(TeamState.model_fields[field].annotation)[1]) for field in _TABLES
}
_VALIDATORS = {
    "review_requests": "reviews",
    "release_packages": "releases",
    "human_review_requests": "human_reviews",
    "human_review_decisions": "human_reviews",
    "human_review_findings": "human_reviews",
    "conversation_messages": "conversations",
    "blocker_events": "blockers",
    "git_work_contexts": "deliverables",
}


class _SelectedConnection:
    """Apply primary-key read sets to the existing lossless row decoders."""

    def __init__(self, connection, selections):
        self.connection, self.selections = connection, selections

    def execute(self, sql, parameters=()):
        names = set(re.findall(r"\b(?:FROM|JOIN)\s+(team_\w+)", sql, re.IGNORECASE))
        ctes, values = [], []
        for table in sorted(names & self.selections.keys()):
            key, ids = self.selections[table]
            placeholders = ",".join("?" for _ in ids)
            ctes.append(f"{table} AS (SELECT * FROM main.{table} WHERE {key} IN ({placeholders}))")
            values.extend(ids)
        if ctes:
            sql = "WITH " + ",".join(ctes) + " " + sql
        return self.connection.execute(sql, [*values, *parameters])


class _Records(dict):
    def __init__(self, scope, field):
        super().__init__()
        self.scope, self.field, self.enumerated = scope, field, False
        self.missing = set()
        self.sealed = False

    def _load(self, ids=None):
        if ids is None and self.enumerated:
            return
        if ids is not None:
            ids = [
                key
                for key in ids
                if not dict.__contains__(self, key)
                and key not in self.missing
                and key not in self.scope.baseline[self.field]
            ]
            if not ids:
                return
        rows = self.scope._load(self.field, ids)
        for key, payload in rows.items():
            if not dict.__contains__(self, key) and key not in self.scope.baseline[self.field]:
                value = _MODELS[self.field].validate_python(payload)
                dict.__setitem__(self, key, value)
                self.scope.baseline[self.field][key] = value.model_dump(mode="json")
        if ids is None:
            self.enumerated = True
        else:
            self.missing.update(set(ids) - rows.keys())

    def __getitem__(self, key):
        if not dict.__contains__(self, key) and key not in self.missing and not self.sealed:
            self._load([key])
        return dict.__getitem__(self, key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        return self.get(key) is not None

    def values(self):
        self._load()
        return tuple(dict.values(self))

    def items(self):
        self._load()
        return tuple(dict.items(self))

    def __iter__(self):
        self._load()
        return iter(tuple(dict.keys(self)))

    def __len__(self):
        self._load()
        return dict.__len__(self)

    def keys(self):
        self._load()
        return dict(dict.items(self)).keys()

    def __setitem__(self, key, value):
        self.get(key)
        dict.__setitem__(self, key, value)

    def setdefault(self, key, default=None):
        if self.get(key) is None:
            dict.__setitem__(self, key, default)
        return self[key]

    def pop(self, key, *default):
        self.get(key)
        return dict.pop(self, key, *default)


class _AffectedRecords(dict):
    """Validate changed rows while resolving references against the full scope."""

    def __init__(self, source, ids):
        super().__init__((key, source[key]) for key in ids if dict.__contains__(source, key))
        self.source = source

    def __getitem__(self, key):
        return self.source[key]

    def get(self, key, default=None):
        return self.source.get(key, default)

    def __contains__(self, key):
        return key in self.source


class _PreparedWrites:
    """Prepare the two journal operations used by lifecycle maintenance."""

    def __init__(self, scope):
        self.scope, self.writes = scope, []
        self.outbox_by_key = {}

    def enqueue_outbox_record(self, record):
        stored = TeamOutboxRecord.model_validate(record)
        if stored.idempotency_key is not None:
            existing = self.outbox_by_key.get(stored.idempotency_key)
            if existing is None:
                with self.scope._read_connection() as connection:
                    existing = self.scope.repository.base.store._lookup_outbox_by_idempotency_key(
                        connection,
                        stored.idempotency_key,
                    )
            stored = existing or stored
            self.outbox_by_key[stored.idempotency_key] = stored
        self.writes.append(("enqueue_outbox_record", stored))
        return stored

    def append_dispatch_activity(self, activity):
        stored = TeamDispatchActivity.model_validate(activity)
        self.writes.append(("append_dispatch_activity", stored))
        return stored

    def commit(self, transaction):
        for method, record in self.writes:
            actual = getattr(transaction, method)(record)
            if actual.id != record.id:
                # Outbox writers need not change Team revision. Replan rather
                # than return an ID different from the atomically stored one.
                raise TeamStateConflictError(
                    expected_revision=self.scope.original_revision,
                    current_revision=self.scope.original_revision,
                )


class TeamLifecycleScope:
    """Operational enumeration plus exact references, pinned by global revision."""

    def __init__(self, repository, metadata):
        self.repository = repository
        self.schema_version = int(metadata["state_schema_version"])
        self.revision = int(metadata["revision"])
        self.original_revision = self.revision
        from datetime import datetime

        self.updated_at = datetime.fromisoformat(metadata["updated_at"])
        self.baseline = {field: {} for field in _TABLES}
        self._case_context_snapshot_writes = {}
        self._snapshot_token = {"connection": None}
        for field in _TABLES:
            setattr(self, field, _Records(self, field))

    @property
    def _snapshot_connection(self):
        return self._snapshot_token["connection"]

    @_snapshot_connection.setter
    def _snapshot_connection(self, connection):
        self._snapshot_token["connection"] = connection

    def __getattr__(self, name):
        # Share pure graph queries and relationship validators, without
        # inheriting the aggregate model or its full-state persistence authority.
        if name in {
            "ordered_tasks_for_agent",
            "child_task_ids",
            "dependency_status",
            "blocked_by_task_ids",
            "subtask_counts",
            "_release_descends_from",
        } or (name.startswith("_validate_") and name != "_validate_state"):
            return MethodType(getattr(TeamState, name), self)
        raise AttributeError(name)

    def _load(self, field, ids):
        started = time.perf_counter()
        store = self.repository.base.store
        with self._read_connection() as connection:
            revision = store._read_revision_from_connection(connection)
            if revision != self.original_revision:
                raise TeamStateConflictError(
                    expected_revision=self.original_revision, current_revision=revision
                )
            if ids is None:
                ids = self.repository.select_ids(connection, field, self)
                records = getattr(self, field)
                ids = [
                    key
                    for key in ids
                    if not dict.__contains__(records, key) and key not in self.baseline[field]
                ]
            if not ids:
                return {}
            selections = {table: (key, tuple(ids)) for table, key in _TABLES[field]}
            # A grouped decoder must never hydrate its unselected siblings.
            reader = _STATE_GROUP_READERS.get(field, f"_read_{field}")
            for other in _TABLES:
                if other != field and _STATE_GROUP_READERS.get(other, f"_read_{other}") == reader:
                    selections.update({table: (key, ()) for table, key in _TABLES[other]})
            payload = {name: {} for name in _TABLES}
            getattr(store, reader)(_SelectedConnection(connection, selections), payload)
            elapsed = (time.perf_counter() - started) * 1000
            log = logger.info if elapsed >= 100 else logger.debug
            log(
                "Team lifecycle phase=scoped_read collection=%s rows=%s duration_ms=%.1f",
                field,
                len(payload[field]),
                elapsed,
            )
            return payload[field]

    @contextmanager
    def _read_connection(self):
        if self._snapshot_connection is not None:
            yield self._snapshot_connection
        else:
            with self.repository.base.store._read_connection() as connection:
                connection.execute("BEGIN")
                yield connection

    def query_records(self, field, where, parameters=(), *, order=None, limit=None):
        """Read an explicit domain fact, rather than enumerate terminal history."""
        store = self.repository.base.store
        table, key = _TABLES[field][-1]
        with self._read_connection() as connection:
            actual = store._read_revision_from_connection(connection)
            if actual != self.original_revision:
                raise TeamStateConflictError(
                    expected_revision=self.original_revision, current_revision=actual
                )
            sql = f"SELECT {key} FROM {table} WHERE {where}"
            if order:
                sql += " ORDER BY " + order
            if limit is not None:
                sql += " LIMIT ?"
                parameters = (*parameters, limit)
            ids = [row[0] for row in connection.execute(sql, parameters)]
        records = getattr(self, field)
        records._load(ids)
        # Include uncommitted working records too. The domain predicate still
        # runs in Python and must observe changes already made by its command.
        return list(dict.values(records))

    def child_task_ids(self, task_id):
        records = self.query_records("tasks", "parent_task_id=?", (task_id,))
        return sorted(task.id for task in records if task.parent_task_id == task_id)

    def latest_wake_work_records(self, agent_id):
        terminal = (
            "agent_id=? AND cause_kind!='manager_fallback' AND completed_at IS NOT NULL "
            "AND status IN ('completed','expired','failed') AND (leased_at IS NOT NULL OR activation_completed_at IS NOT NULL)"
        )
        modern = self.query_records(
            "dispatches",
            terminal
            + " AND (wake_work_record_fingerprints_json!='{}' OR wake_work_fingerprint IS NOT NULL)",
            (agent_id,),
            order=DISPATCH_ATTEMPT_ORDER,
            limit=1,
        )
        modern = [
            row
            for row in modern
            if row.agent_id == agent_id
            and row.cause_kind != "manager_fallback"
            and row.status in ("completed", "expired", "failed")
            and row.completed_at is not None
            and (row.leased_at is not None or row.activation_completed_at is not None)
            and (row.wake_work_record_fingerprints or row.wake_work_fingerprint is not None)
        ]
        latest = max(
            (
                (timestamp_microseconds(row.leased_at or row.activation_completed_at), row.id)
                for row in modern
            ),
            default=None,
        )
        legacy = (
            terminal
            + " AND cause_kind NOT IN ('blocker','context_handoff') AND wake_work_record_fingerprints_json='{}' AND wake_work_fingerprint IS NULL"
        )
        parameters = [agent_id]
        if latest:
            # SQLite 3.45 does not range-seek a tuple whose first term is an
            # expression index. Supply the scalar bound explicitly, then use
            # the ID to preserve exact ordering within timestamp ties.
            legacy += (
                " AND "
                + DISPATCH_ATTEMPT_KEY
                + ">=? AND ("
                + DISPATCH_ATTEMPT_KEY
                + ">? OR team_dispatches.id>?)"
            )
            parameters.extend((latest[0], latest[0], latest[1]))
        # Probe at most one page of indexed headers before considering the
        # exact legacy compatibility fallback. Modern attempts fence off all
        # older legacy history, and a short page proves absence directly.
        with self._read_connection() as connection:
            headers = connection.execute(
                "SELECT id FROM team_dispatches INDEXED BY idx_team_dispatches_legacy_attempt WHERE "
                + legacy
                + " ORDER BY "
                + DISPATCH_ATTEMPT_ORDER
                + " LIMIT 50",
                parameters,
            ).fetchall()
            for header in headers:
                if connection.execute(
                    "SELECT EXISTS(SELECT 1 FROM team_dispatch_inbox WHERE dispatch_id=?) OR EXISTS(SELECT 1 FROM team_dispatch_reviews WHERE dispatch_id=?)",
                    (header[0], header[0]),
                ).fetchone()[0]:
                    self.dispatches._load([header[0]])
                    return list(dict.values(self.dispatches))
            if len(headers) < 50:
                return list(dict.values(self.dispatches))
            # Only legacy databases with a full page of empty newer headers
            # require this relation-first fallback. Cross-table existence
            # cannot be encoded in a SQLite expression/partial index.
            ids = [
                row[0]
                for row in connection.execute(
                    "WITH scoped(id) AS MATERIALIZED (SELECT dispatch_id FROM team_dispatch_inbox "
                    "UNION SELECT dispatch_id FROM team_dispatch_reviews) "
                    "SELECT team_dispatches.id FROM scoped s CROSS JOIN team_dispatches ON team_dispatches.id=s.id WHERE "
                    + legacy
                    + " ORDER BY "
                    + DISPATCH_ATTEMPT_KEY
                    + " DESC,team_dispatches.id DESC LIMIT 1",
                    parameters,
                )
            ]
        self.dispatches._load(ids)
        return list(dict.values(self.dispatches))

    def cleanup_work_context_records(self):
        """Discover outstanding physical cleanup, excluding completed history."""
        records = self.query_records(
            "git_work_contexts",
            "worktree_path IS NOT NULL AND NOT EXISTS(SELECT 1 FROM team_worktree_cleanup_candidates c "
            "WHERE c.git_work_context_id=team_git_work_contexts.id AND c.state IN ('succeeded','superseded'))",
        )
        # A cleaned newer generation still suppresses its older siblings. Read
        # those exact lineages before choosing a generation for physical work.
        lineages = {
            (row.agent_id, row.agent_branch, row.worktree_path)
            for row in records
            if row.worktree_path is not None
        }
        for agent_id, branch, path in lineages:
            self.query_records(
                "git_work_contexts",
                "agent_id=? AND agent_branch=? AND worktree_path=?",
                (agent_id, branch, path),
            )
        return list(dict.values(self.git_work_contexts))

    def prunable_work_context_records(self, before):
        # Filter headers before decoding models. Resolved recovery incidents
        # still hold retained work, so the retention predicate has no state filter.
        return self.query_records(
            "git_work_contexts",
            "state='abandoned' AND " + timestamp_microseconds_sql("updated_at") + "<=? "
            "AND NOT EXISTS(SELECT 1 FROM team_deliverables d WHERE d.branch_context_id=team_git_work_contexts.id) "
            "AND (worktree_path IS NULL OR EXISTS(SELECT 1 FROM team_worktree_cleanup_candidates c "
            "WHERE c.state='succeeded' AND c.task_id=team_git_work_contexts.task_id "
            "AND c.agent_id=team_git_work_contexts.agent_id AND c.worktree_path=team_git_work_contexts.worktree_path)) "
            "AND NOT EXISTS(SELECT 1 FROM team_git_recovery_incidents i, json_each(i.payload_json,'$.preserved_context_ids') j "
            "WHERE json_array_length(i.payload_json,'$.preserved_context_ids')>0 AND j.value=team_git_work_contexts.id)",
            (timestamp_microseconds(before),),
        )

    def prepare_task_assessment(self):
        # Include historical ancestors, dependencies and children only when
        # they are connected to operational or explicitly selected tasks.
        self.tasks._load()
        with self._read_connection() as connection:
            ids = self.repository.task_scope_ids(connection, self)
        self.tasks._load(ids)
        marks = ",".join("?" for _ in ids)
        self.query_records("deliverables", "task_id IN (" + marks + ")", ids)
        self.query_records(
            "review_requests",
            "deliverable_id IN (SELECT id FROM team_deliverables WHERE task_id IN (" + marks + "))",
            ids,
        )

    def work_context_pruning_facts(self, contexts):
        """Read physical cleanup and retention evidence without history models."""
        cleaned, retained = set(), set()
        with self._read_connection() as connection:
            for context in contexts:
                identity = (context.task_id, context.agent_id, context.worktree_path)
                if connection.execute(
                    "SELECT 1 FROM team_worktree_cleanup_candidates WHERE state='succeeded' AND task_id=? AND agent_id=? AND worktree_path=? LIMIT 1",
                    identity,
                ).fetchone():
                    cleaned.add(identity)
            ids = [context.id for context in contexts]
            retained.update(
                row[0]
                for row in connection.execute(
                    "SELECT j.value FROM team_git_recovery_incidents i, json_each(i.payload_json,'$.preserved_context_ids') j "
                    "WHERE json_array_length(i.payload_json,'$.preserved_context_ids')>0 AND j.value IN ("
                    + ",".join("?" for _ in ids)
                    + ")",
                    ids,
                )
            )
        for candidate in dict.values(self.worktree_cleanup_candidates):
            if candidate.state == "succeeded":
                cleaned.add((candidate.task_id, candidate.agent_id, candidate.worktree_path))
        for incident in dict.values(self.git_recovery_incidents):
            retained.update(incident.preserved_context_ids)
        return cleaned, retained

    def has_recent_lifecycle_activity(self, agent_id, rotated_at):
        cutoff = timestamp_microseconds(rotated_at)
        with self._read_connection() as connection:
            actual = self.repository.base.store._read_revision_from_connection(connection)
            if actual != self.original_revision:
                raise TeamStateConflictError(
                    expected_revision=self.original_revision, current_revision=actual
                )
            for field, owner, updated in (
                ("tasks", "owner_agent_id", "updated_at"),
                ("inbox", "recipient_agent_id", "updated_at"),
                ("review_requests", "reviewer_agent_id", "decision_at"),
                ("ephemeral_assignments", "parent_agent_id", "updated_at"),
            ):
                records = getattr(self, field)
                for record in dict.values(records):
                    if (
                        getattr(record, owner) == agent_id
                        and max(
                            (
                                timestamp_microseconds(value)
                                for value in (record.created_at, getattr(record, updated))
                                if value is not None
                            ),
                            default=0,
                        )
                        > cutoff
                    ):
                        return True
                excluded = sorted(set(self.baseline[field]) | set(dict.keys(records)))
                created_key = timestamp_microseconds_sql("created_at")
                updated_key = timestamp_microseconds_sql(updated)
                if field == "review_requests":
                    created_key, updated_key = (
                        f"COALESCE({created_key},0)",
                        f"COALESCE({updated_key},0)",
                    )
                key = f"max({created_key},{updated_key})"
                sql = f"SELECT 1 FROM team_{field} INDEXED BY idx_team_{field}_activity WHERE {owner}=? AND {key}>?"
                if excluded:
                    sql += " AND id NOT IN (" + ",".join("?" for _ in excluded) + ")"
                if connection.execute(sql + " LIMIT 1", [agent_id, cutoff, *excluded]).fetchone():
                    return True
        return False

    def has_prior_successful_ordinary_wake(
        self, agent, *, exclude=None, mutation_allowed=True, exclude_governance=True
    ):
        records = self.query_records(
            "dispatches",
            "agent_id=? AND session_id=? AND status='completed' AND id!=? "
            "AND (?=0 OR cause_kind!='governance') AND (?=0 OR cause_kind!='team_chat' OR EXISTS("
            "SELECT 1 FROM team_conversation_messages m WHERE m.id=team_dispatches.cause_id "
            "AND json_extract(m.metadata_json,'$.ordinary_context_seeded')=1))",
            (
                agent.id,
                agent.session_id,
                exclude or "",
                int(exclude_governance),
                int(mutation_allowed),
            ),
            limit=1,
        )
        return any(
            row.id != exclude
            and row.agent_id == agent.id
            and row.session_id == agent.session_id
            and row.status == "completed"
            and (not exclude_governance or row.cause_kind != "governance")
            and (
                not mutation_allowed
                or row.cause_kind != "team_chat"
                or bool(
                    (message := self.conversation_messages.get(row.cause_id or ""))
                    and message.metadata.get("ordinary_context_seeded") is True
                )
            )
            for row in records
        )

    def previous_context_snapshot_id(self, dispatch, thread_id):
        """Select retained native-thread context from indexed headers, not models."""
        for snapshot in self._case_context_snapshot_writes.values():
            if snapshot.dispatch_id:
                self.dispatches.get(snapshot.dispatch_id)
        cached = list(dict.values(self.dispatches))
        excluded = tuple(row.id for row in cached)
        cutoff = timestamp_microseconds(dispatch.created_at)
        where = (
            "agent_id=? AND session_id=? AND native_thread_id=? AND status='completed' "
            "AND completed_at IS NOT NULL AND team_dispatches.id!=? AND "
            + DISPATCH_COMPLETED_KEY
            + "<=? AND team_dispatches.id NOT IN ("
            + ",".join("?" for _ in excluded)
            + ")"
        )
        parameters = [
            dispatch.agent_id,
            dispatch.session_id,
            thread_id,
            dispatch.id,
            cutoff,
            *excluded,
        ]
        best = None
        with self._read_connection() as connection:
            actual = self.repository.base.store._read_revision_from_connection(connection)
            if actual != self.original_revision:
                raise TeamStateConflictError(
                    expected_revision=self.original_revision, current_revision=actual
                )
            row = connection.execute(
                "SELECT " + DISPATCH_COMPLETED_KEY + " AS instant FROM team_dispatches "
                "INDEXED BY idx_team_dispatches_context_previous WHERE "
                + where
                + " AND EXISTS(SELECT 1 FROM team_case_context_snapshot_index s WHERE s.dispatch_id=team_dispatches.id) "
                "ORDER BY " + DISPATCH_COMPLETED_KEY + " DESC LIMIT 1",
                parameters,
            ).fetchone()
            if row is not None:
                # Completion ties use snapshot ID, matching previous_id exactly.
                snapshot_id = connection.execute(
                    "SELECT MAX(s.id) FROM team_dispatches INDEXED BY idx_team_dispatches_context_previous "
                    "JOIN team_case_context_snapshot_index s ON s.dispatch_id=team_dispatches.id WHERE "
                    + where
                    + " AND "
                    + DISPATCH_COMPLETED_KEY
                    + "=?",
                    [*parameters, row["instant"]],
                ).fetchone()[0]
                best = (row["instant"], snapshot_id)
            for previous in cached:
                if (
                    previous.id == dispatch.id
                    or previous.agent_id != dispatch.agent_id
                    or previous.session_id != dispatch.session_id
                    or previous.status != "completed"
                    or previous.native_thread_id != thread_id
                    or previous.completed_at is None
                ):
                    continue
                instant = timestamp_microseconds(previous.completed_at)
                if instant > cutoff:
                    continue
                snapshot_id = connection.execute(
                    "SELECT MAX(id) FROM team_case_context_snapshot_index WHERE dispatch_id=?",
                    (previous.id,),
                ).fetchone()[0]
                pending = [
                    snapshot.id
                    for snapshot in self._case_context_snapshot_writes.values()
                    if snapshot.dispatch_id == previous.id
                ]
                ids = [value for value in [snapshot_id, *pending] if value is not None]
                if ids:
                    candidate = (instant, max(ids))
                    best = max(best, candidate) if best else candidate
        return best[1] if best else None

    def model_dump(self, *, mode="python", **_kwargs):
        # This representation is a delta preparation aid, never a TeamState
        # payload. Do not enumerate omitted history just to take a baseline.
        return {
            "revision": self.revision,
            "schema_version": self.schema_version,
            "updated_at": self.updated_at.isoformat(),
            **{
                field: {
                    key: row.model_dump(mode=mode) for key, row in dict.items(getattr(self, field))
                }
                for field in _TABLES
            },
        }

    def model_copy(self, *, deep=False, update=None):
        clone = TeamLifecycleScope.__new__(TeamLifecycleScope)
        clone.__dict__ = self.__dict__.copy()
        clone.baseline = copy.deepcopy(self.baseline)
        if deep:
            clone._case_context_snapshot_writes = copy.deepcopy(self._case_context_snapshot_writes)
        for field in _TABLES:
            original = getattr(self, field)
            rows = _Records(clone, field)
            rows.enumerated = original.enumerated if isinstance(original, _Records) else True
            rows.missing = set(original.missing) if isinstance(original, _Records) else set()
            rows.sealed = original.sealed if isinstance(original, _Records) else True
            for key, value in dict.items(original):
                dict.__setitem__(rows, key, value.model_copy(deep=True) if deep else value)
            setattr(clone, field, rows)
        for field, value in (update or {}).items():
            if field in _TABLES:
                rows = _Records(clone, field)
                rows.enumerated = True
                rows.sealed = True
                for key, row in value.items():
                    dict.__setitem__(rows, key, row)
                setattr(clone, field, rows)
            else:
                setattr(clone, field, value)
        return clone


class TeamLifecycleRepository:
    """Internal command adapter; public aggregate repository APIs stay unchanged.

    Lifecycle rules can use their existing mutators, but this adapter only
    persists changed, individually and relationally validated records. It does
    not offer aggregate replacement or a full-state fallback on contention.
    """

    maintenance = False  # Commands below already prepare outside the writer.

    def __init__(self, base, *, dispatch_ids=(), project_ids=(), purpose="lifecycle"):
        if isinstance(base, TeamLifecycleRepository) and not project_ids:
            project_ids = base.project_ids
        if isinstance(base, TeamLifecycleRepository) and not dispatch_ids:
            dispatch_ids = base.dispatch_ids
        self.base = base.base if isinstance(base, TeamLifecycleRepository) else base
        self.runtime_dir = self.base.runtime_dir
        self.dispatch_ids = tuple(sorted(set(dispatch_ids)))
        self.project_ids = tuple(sorted(set(project_ids)))
        self.purpose = purpose
        self.store = _LifecycleStore(self)

    def __getattr__(self, name):
        if name.startswith(("update", "commit", "write", "replace", "read")):
            raise AttributeError(f"unsupported scoped lifecycle repository operation: {name}")
        return getattr(self.base, name)

    def read_revision(self):
        return self.base.read_revision()

    def read(self):
        with self.command_snapshot() as scope:
            return scope

    @contextmanager
    def command_snapshot(self):
        """Pin all preparation reads; WAL permits writers until the revision CAS."""
        with self.base.store._read_connection() as connection:
            connection.execute("BEGIN")
            metadata = dict(connection.execute("SELECT key,value FROM team_state_metadata"))
            scope = TeamLifecycleScope(self, metadata)
            scope.agent_ids, scope.project_ids = (), self.project_ids
            if self.dispatch_ids:
                placeholders = ",".join("?" for _ in self.dispatch_ids)
                scope.agent_ids = tuple(
                    row[0]
                    for row in connection.execute(
                        f"SELECT DISTINCT agent_id FROM team_dispatches WHERE id IN ({placeholders})",
                        self.dispatch_ids,
                    )
                )
                scope.project_ids = tuple(
                    sorted(
                        set(self.project_ids)
                        | {
                            row[0]
                            for row in connection.execute(
                                f"SELECT project_id FROM team_dispatch_projects WHERE dispatch_id IN ({placeholders}) "
                                f"UNION SELECT p.id FROM team_projects p JOIN team_agents a ON a.team_id=p.team_id "
                                f"JOIN team_dispatches d ON d.agent_id=a.id WHERE d.id IN ({placeholders})",
                                [*self.dispatch_ids, *self.dispatch_ids],
                            )
                        }
                    )
                )
            scope._snapshot_connection = connection
            try:
                yield scope
            finally:
                scope._snapshot_connection = None

    @contextmanager
    def dispatch_finalization_snapshot(self, *, dispatch_id):
        with self.command_snapshot() as scope:
            activities = self.base.store._list_dispatch_activity(
                scope._snapshot_connection,
                dispatch_id=dispatch_id,
            )
            yield scope, activities

    def read_projection(self, *, collections):
        with self.command_snapshot() as scope:
            for field in collections:
                getattr(scope, field)._load()
            return scope

    def select_ids(self, connection, field, scope):
        table, key = _TABLES[field][-1]
        parameters = []
        if field == "dispatches":
            # Terminal history is available through explicit indexed facts or
            # exact references, never through operational enumeration.
            ids = set(self.dispatch_ids)
            sql = "SELECT id FROM team_dispatches WHERE status IN ('preparing','leased')"
            ids.update(row[0] for row in connection.execute(sql))
            return sorted(ids)
        if field == "tasks":
            return self.task_scope_ids(connection, scope)
        if field in {
            "human_review_requests",
            "human_review_findings",
            "human_review_decisions",
            "release_verification_runs",
        }:
            releases = sorted(
                set(
                    self.release_scope_ids(
                        connection, scope, self.task_scope_ids(connection, scope)
                    )
                )
                | set(dict.keys(scope.release_packages))
            )
            marks = ",".join("?" for _ in releases)
            requests = (
                "SELECT id FROM team_human_review_requests WHERE release_package_id IN ("
                + marks
                + ")"
            )
            if field == "human_review_requests":
                sql = (
                    "SELECT id FROM team_human_review_requests WHERE state IN ('preparing','pending','blocked') UNION "
                    + requests
                )
            elif field == "human_review_findings":
                sql = (
                    "SELECT id FROM team_human_review_findings WHERE state='open' UNION SELECT id FROM team_human_review_findings WHERE source_human_review_request_id IN ("
                    + requests
                    + ")"
                )
            elif field == "human_review_decisions":
                sql = (
                    "SELECT id FROM team_human_review_decisions WHERE human_review_request_id IN ("
                    + requests
                    + ")"
                )
            else:
                sql = (
                    "SELECT id FROM team_release_verification_runs WHERE json_extract(payload_json,'$.release_package_id') IN ("
                    + marks
                    + ")"
                )
            return [row[0] for row in connection.execute(sql, releases)]
        if field in {
            "deliverables",
            "review_requests",
            "cases",
            "release_packages",
            "git_work_contexts",
            "git_preparation_attempts",
            "git_recovery_incidents",
            "ephemeral_assignments",
        } and not (field == "cases" and self.purpose == "human_correction"):
            tasks = self.task_scope_ids(connection, scope)
            marks = ",".join("?" for _ in tasks)
            if field == "deliverables":
                sql = f"SELECT id FROM team_deliverables WHERE task_id IN ({marks})"
            elif field == "review_requests":
                sql = (
                    "SELECT id FROM team_review_requests INDEXED BY idx_team_reviews_operational "
                    "WHERE state IN ('pending','in_progress','blocked') AND retired_at IS NULL AND superseded_by_review_request_id IS NULL "
                    f"UNION SELECT r.id FROM team_review_requests r JOIN team_deliverables d ON d.id=r.deliverable_id WHERE d.task_id IN ({marks})"
                )
                related = sorted(set(dict.keys(scope.deliverables)))
                if related:
                    sql += (
                        " UNION SELECT id FROM team_review_requests WHERE deliverable_id IN ("
                        + ",".join("?" for _ in related)
                        + ")"
                    )
                    tasks.extend(related)
                if self.dispatch_ids:
                    sql += (
                        " UNION SELECT review_request_id FROM team_dispatch_reviews WHERE dispatch_id IN ("
                        + ",".join("?" for _ in self.dispatch_ids)
                        + ")"
                    )
                    tasks.extend(self.dispatch_ids)
                releases = sorted(dict.keys(scope.release_packages))
                if releases:
                    sql += (
                        " UNION SELECT id FROM team_review_requests WHERE release_package_id IN ("
                        + ",".join("?" for _ in releases)
                        + ")"
                    )
                    tasks.extend(releases)
            elif field == "cases":
                sql = (
                    "SELECT id FROM team_cases INDEXED BY idx_team_cases_operational WHERE status IN ('open','triage','in_progress','waiting') "
                    f"UNION SELECT case_id FROM team_tasks WHERE id IN ({marks}) AND case_id IS NOT NULL"
                )
            elif field == "release_packages":
                return self.release_scope_ids(connection, scope, tasks)
            elif field == "git_work_contexts":
                sql = (
                    "SELECT id FROM team_git_work_contexts INDEXED BY idx_team_contexts_active WHERE state='active' "
                    f"UNION SELECT id FROM team_git_work_contexts WHERE task_id IN ({marks})"
                )
            elif field == "git_preparation_attempts":
                sql = (
                    "SELECT id FROM team_git_preparation_attempts INDEXED BY idx_team_preparations_operational "
                    "WHERE state IN ('pending','prepared','cleanup_pending') "
                    f"UNION SELECT id FROM team_git_preparation_attempts WHERE task_id IN ({marks})"
                )
            elif field == "git_recovery_incidents":
                sql = (
                    "SELECT id FROM team_git_recovery_incidents INDEXED BY idx_team_recovery_operational "
                    "WHERE state IN ('diagnosed','preparing','awaiting_review','waiting') "
                    f"UNION SELECT id FROM team_git_recovery_incidents WHERE task_id IN ({marks})"
                )
            else:
                sql = (
                    "SELECT id FROM team_ephemeral_assignments INDEXED BY idx_team_ephemeral_operational "
                    "WHERE state IN ('requested','spawn_pending','running','completed','cleanup_pending') "
                    f"UNION SELECT id FROM team_ephemeral_assignments WHERE task_id IN ({marks})"
                )
            return [row[0] for row in connection.execute(sql, tasks)]
        if field == "cases" and self.purpose == "human_correction":
            where = "source='human_release_review' AND source_ref IN (SELECT h.id FROM team_human_review_requests h "
            where += "JOIN team_release_packages r ON r.id=h.release_package_id WHERE h.state='changes_requested' AND r.state='rejected')"
        elif field == "inbox":
            where = "state='pending'"
        elif field == "blockers":
            where = "state='open'"
        elif field == "worktree_cleanup_candidates":
            where = (
                "state IN ('pending','processing','operator_action_required','recovery_required')"
            )
        elif field == "conversation_messages":
            where = "status IN ('queued','dispatching','running','delivered_in_dispatch')"
        elif field == "action_records":
            # Feedback belongs to the latest complete action batch, including
            # successful/dismissed rows that suppress failures in that batch.
            agents = scope.agent_ids or tuple(
                row[0] for row in connection.execute("SELECT id FROM team_agents")
            )
            ids = set()
            for agent in agents:
                latest = connection.execute(
                    "SELECT dispatch_id FROM team_action_records WHERE agent_id=? ORDER BY "
                    + ACTION_CREATED_ORDER
                    + " LIMIT 1",
                    (agent,),
                ).fetchone()
                if latest:
                    ids.update(
                        row[0]
                        for row in connection.execute(
                            "SELECT id FROM team_action_records WHERE agent_id=? AND dispatch_id IS ?",
                            (agent, latest[0]),
                        )
                    )
            if self.dispatch_ids:
                marks = ",".join("?" for _ in self.dispatch_ids)
                for dispatch in connection.execute(
                    f"SELECT agent_id,action_feedback_dispatch_ids_json FROM team_dispatches WHERE id IN ({marks})",
                    self.dispatch_ids,
                ):
                    sources = json.loads(dispatch[1])
                    source_marks = ",".join("?" for _ in sources)
                    ids.update(
                        row[0]
                        for row in connection.execute(
                            f"SELECT id FROM team_action_records WHERE agent_id=? AND dispatch_id IN ({source_marks})",
                            [dispatch[0], *sources],
                        )
                    )
            return sorted(ids)
        elif field == "blocker_events":
            where = (
                "(blocker_id IN (SELECT id FROM team_blockers WHERE state='open') OR dispatch_id IN ("
                + ",".join("?" for _ in self.dispatch_ids)
                + "))"
            )
            parameters.extend(self.dispatch_ids)
        else:
            where = "1"
        if scope.agent_ids:
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            owner = {"inbox": "recipient_agent_id", "conversation_messages": None}.get(
                field, "agent_id"
            )
            if field in {"action_records", "conversations"} and owner in columns:
                where += f" AND {owner} IN (" + ",".join("?" for _ in scope.agent_ids) + ")"
                parameters.extend(scope.agent_ids)
        if scope.project_ids:
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if "project_id" in columns:
                where += (
                    " AND (project_id IS NULL OR project_id IN ("
                    + ",".join("?" for _ in scope.project_ids)
                    + "))"
                )
                parameters.extend(scope.project_ids)
        return [
            row[0]
            for row in connection.execute(f"SELECT {key} FROM {table} WHERE {where}", parameters)
        ]

    def task_scope_ids(self, connection, scope):
        active = (
            "state IN ('pending','in_progress','blocked','waiting_for_input','waiting_for_review')"
        )
        roots = "SELECT id FROM team_tasks INDEXED BY idx_team_tasks_operational WHERE " + active
        parameters = []
        if scope.project_ids:
            roots += " AND project_id IN (" + ",".join("?" for _ in scope.project_ids) + ")"
            parameters.extend(scope.project_ids)
        roots += " UNION SELECT task_id FROM team_deliverables INDEXED BY idx_team_deliverables_operational WHERE state IN ('pending','in_progress','blocked') AND superseded_by_deliverable_id IS NULL"
        roots += " UNION SELECT d.task_id FROM team_review_requests r INDEXED BY idx_team_reviews_operational JOIN team_deliverables d ON d.id=r.deliverable_id WHERE r.state IN ('pending','in_progress','blocked') AND r.retired_at IS NULL AND r.superseded_by_review_request_id IS NULL"
        roots += " UNION SELECT task_id FROM team_deliverables INDEXED BY idx_team_deliverables_initial_release WHERE state='accepted' AND superseded_by_deliverable_id IS NULL AND json_extract(initial_release_disposition_json,'$.state')='pending' AND NOT EXISTS(SELECT 1 FROM team_release_deliverables r WHERE r.deliverable_id=team_deliverables.id)"
        roots += " UNION SELECT task_id FROM team_inbox INDEXED BY idx_team_inbox_pending WHERE state='pending' AND task_id IS NOT NULL"
        roots += " UNION SELECT task_id FROM team_blockers INDEXED BY idx_team_blockers_open WHERE state='open' AND task_id IS NOT NULL"
        roots += " UNION SELECT t.task_id FROM team_blockers b INDEXED BY idx_team_blockers_open JOIN team_blocker_affected_tasks t ON t.blocker_id=b.id WHERE b.state='open'"
        if self.dispatch_ids:
            roots += (
                " UNION SELECT task_id FROM team_dispatch_tasks WHERE dispatch_id IN ("
                + ",".join("?" for _ in self.dispatch_ids)
                + ")"
            )
            parameters.extend(self.dispatch_ids)
        cached = set(dict.keys(scope.tasks))
        for task in dict.values(scope.tasks):
            cached.update(task.dependency_task_ids)
            if task.parent_task_id:
                cached.add(task.parent_task_id)
            if task.deliverable_contract:
                cached.update(task.deliverable_contract.required_contribution_task_ids)
        cached = sorted(cached)
        if cached:
            roots += (
                " UNION SELECT id FROM team_tasks WHERE id IN ("
                + ",".join("?" for _ in cached)
                + ")"
            )
            parameters.extend(cached)
        return [
            row[0]
            for row in connection.execute(
                "WITH RECURSIVE related(id) AS ("
                + roots
                + " UNION SELECT t.parent_task_id FROM team_tasks t JOIN related r ON r.id=t.id WHERE t.parent_task_id IS NOT NULL"
                + " UNION SELECT t.id FROM team_tasks t JOIN related r ON t.parent_task_id=r.id"
                + " UNION SELECT t.id FROM team_tasks t JOIN related r ON t.originating_task_id=r.id"
                + " UNION SELECT d.dependency_task_id FROM team_task_dependencies d JOIN related r ON d.task_id=r.id"
                + " UNION SELECT j.value FROM team_tasks t JOIN related r ON r.id=t.id JOIN json_each(t.deliverable_contract_json,'$.required_contribution_task_ids') j) SELECT id FROM related",
                parameters,
            )
        ]

    def release_scope_ids(self, connection, scope, task_ids):
        marks = ",".join("?" for _ in task_ids)
        sql = (
            "SELECT id FROM team_release_packages INDEXED BY idx_team_releases_operational WHERE state IN ('pending','in_progress','blocked') "
            f"UNION SELECT release_base_package_id FROM team_tasks WHERE id IN ({marks}) AND release_base_package_id IS NOT NULL "
            f"UNION SELECT r.package_id FROM team_deliverables d JOIN team_release_deliverables r ON r.deliverable_id=d.id WHERE d.task_id IN ({marks}) "
            "UNION SELECT source_id FROM team_blockers INDEXED BY idx_team_blockers_open WHERE state='open' AND source_kind='release_package' "
            "UNION SELECT r.release_package_id FROM team_blockers b INDEXED BY idx_team_blockers_open JOIN team_review_requests r ON r.id=b.source_id WHERE b.state='open' AND b.source_kind='review_request' AND r.release_package_id IS NOT NULL "
            "UNION SELECT h.release_package_id FROM team_blockers b INDEXED BY idx_team_blockers_open JOIN team_human_review_requests h ON h.id=b.source_id WHERE b.state='open' AND b.source_kind='human_review_request' "
            "UNION SELECT h.release_package_id FROM team_human_review_findings f JOIN team_human_review_requests h ON h.id=f.source_human_review_request_id WHERE f.state='open' "
            "UNION SELECT release_package_id FROM team_human_review_requests WHERE state IN ('preparing','pending','blocked') "
            "UNION SELECT release_package_id FROM team_review_requests INDEXED BY idx_team_reviews_operational WHERE state IN ('pending','in_progress','blocked') AND retired_at IS NULL AND superseded_by_review_request_id IS NULL AND release_package_id IS NOT NULL"
        )
        return [row[0] for row in connection.execute(sql, [*task_ids, *task_ids])]

    def update(self, mutator, *, expected_revision=None):
        with self.command_snapshot() as state:
            if expected_revision is not None and state.revision != expected_revision:
                raise TeamStateConflictError(
                    expected_revision=expected_revision, current_revision=state.revision
                )
            candidate = mutator(state) or state
            return self.commit_prepared_state_with_command_transaction(
                previous_payload={"revision": state.original_revision},
                candidate=candidate,
                command_writer=lambda _tx: None,
            )

    update_prepared = update

    def update_with_command_transaction(self, mutator, *, expected_revision=None):
        with self.command_snapshot() as state:
            if expected_revision is not None and state.revision != expected_revision:
                raise TeamStateConflictError(
                    expected_revision=expected_revision, current_revision=state.revision
                )
            commands = _PreparedWrites(state)
            candidate = mutator(state, commands) or state
            candidate.updated_at = utc_now()
            return self.commit_prepared_state_with_command_transaction(
                previous_payload={"revision": state.original_revision},
                candidate=candidate,
                command_writer=commands.commit,
            )

    def update_with_dispatch_activity(self, mutator, *, activity, expected_revision=None):
        def prepare(state, commands):
            candidate = mutator(state) or state
            commands.append_dispatch_activity(activity)
            return candidate

        return self.update_with_command_transaction(prepare, expected_revision=expected_revision)

    def update_with_outbox(self, mutator, *, outbox_records, expected_revision=None):
        with self.command_snapshot() as state:
            if expected_revision is not None and state.revision != expected_revision:
                raise TeamStateConflictError(
                    expected_revision=expected_revision, current_revision=state.revision
                )
            candidate = mutator(state) or state
            records = outbox_records() if callable(outbox_records) else outbox_records
            return self.commit_prepared_state_with_command_transaction(
                previous_payload={"revision": state.original_revision},
                candidate=candidate,
                command_writer=lambda tx: [tx.enqueue_outbox_record(row) for row in records],
            )

    def commit_prepared_state_with_command_transaction(
        self, *, previous_payload, candidate, command_writer
    ):
        if not isinstance(candidate, TeamLifecycleScope) or candidate.repository is not self:
            raise ValueError("lifecycle commands require their own scoped snapshot")
        started = time.perf_counter()
        changed = {field: set() for field in _TABLES}
        current = {}
        # Exact references loaded during validation join the baseline as reads,
        # never as candidate inserts or deletes.
        for field in _TABLES:
            rows = getattr(candidate, field)
            for record_id, row in list(dict.items(rows)):
                validated = _MODELS[field].validate_python(row.model_dump(mode="json"))
                key_field = _TABLES[field][-1][1]
                if validated.model_dump(mode="json").get(key_field) != record_id:
                    raise ValueError(f"lifecycle {field} key does not match {key_field}")
                dict.__setitem__(rows, record_id, validated)
                if candidate.baseline[field].get(record_id) != validated.model_dump(mode="json"):
                    changed[field].add(record_id)
            changed[field].update(candidate.baseline[field].keys() - dict.keys(rows))
        errors = []
        view = copy.copy(candidate)
        for field, ids in changed.items():
            if not ids:
                continue
            validator = getattr(TeamState, "_validate_" + _VALIDATORS.get(field, field), None)
            if validator is None:
                continue
            # Validators enumerate only affected records, and use the original
            # revision-pinned maps for all related lookups.
            setattr(view, field, _AffectedRecords(getattr(candidate, field), ids))
            validator(view, errors)
            setattr(view, field, getattr(candidate, field))
        if changed["tasks"]:
            TeamState._validate_task_graph(candidate, errors)
        lineage_fields = (
            "work_lineages",
            "agent_runtimes",
            "tasks",
            "release_packages",
            "dispatches",
        )
        if any(changed[field] for field in lineage_fields):
            for field in lineage_fields:
                setattr(view, field, _AffectedRecords(getattr(candidate, field), changed[field]))
            TeamState._validate_work_lineages(view, errors)
            for field in lineage_fields:
                setattr(view, field, getattr(candidate, field))
        chat_fields = ("conversations", "conversation_messages", "chat_attachments", "dispatches")
        if any(changed[field] for field in chat_fields):
            for field in chat_fields:
                setattr(view, field, _AffectedRecords(getattr(candidate, field), changed[field]))
            TeamState._validate_conversations(view, errors)
        if errors:
            raise ValueError("; ".join(errors))
        current = candidate.model_dump(mode="json")
        baseline = candidate.baseline
        store = self.base.store
        if any(changed.values()) and candidate.revision <= candidate.original_revision:
            candidate.revision = candidate.original_revision + 1
            candidate.updated_at = utc_now()
        prepared_ms = (time.perf_counter() - started) * 1000
        writer_started = time.perf_counter()
        with storage_lock(store._lock), store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            actual = store._read_revision_from_connection(connection)
            if actual != candidate.original_revision:
                raise TeamStateConflictError(
                    expected_revision=candidate.original_revision, current_revision=actual
                )
            command_writer(TeamStateSqliteCommandTransaction(store, connection))
            store._write_state_delta_payloads(
                connection,
                previous=baseline,
                current=current,
                state=candidate,
                changed_by_field=changed,
            )
            if candidate._case_context_snapshot_writes:
                from .case_context_history import write_snapshots

                write_snapshots(connection, list(candidate._case_context_snapshot_writes.values()))
        candidate._case_context_snapshot_writes.clear()
        writer_ms = (time.perf_counter() - writer_started) * 1000
        log = logger.info if prepared_ms + writer_ms >= 100 else logger.debug
        log(
            "Team lifecycle phase=scoped_commit prepare_ms=%.1f writer_ms=%.1f rows=%s",
            prepared_ms,
            writer_ms,
            {field: len(ids) for field, ids in changed.items() if ids},
        )
        candidate.original_revision = candidate.revision
        candidate.baseline = {
            field: {
                key: row.model_dump(mode="json")
                for key, row in dict.items(getattr(candidate, field))
            }
            for field in _TABLES
        }
        return candidate

    def update_dispatch_bookkeeping(self, mutator, *, dispatch_ids, snapshot=None):
        # The existing narrow writer keeps its field allowlist and lease/thread
        # ownership guards. Never ask it to reconstruct aggregate state.
        self.base.store.update_dispatch_bookkeeping(
            mutator, dispatch_ids=dispatch_ids, include_state=False
        )
        return self.read()


class _LifecycleStore:
    def __init__(self, repository):
        self.repository = repository

    def __getattr__(self, name):
        if name.startswith(
            ("update_state", "commit_prepared", "_read_state", "_write_state")
        ) or name in {"read_object", "write_object", "_read_payload", "_state_update_transaction"}:
            raise AttributeError(f"unsupported scoped lifecycle store operation: {name}")
        return getattr(self.repository.base.store, name)

    def read_state(self):
        return self.repository.read()

    def update_state(self, updater):
        return self.repository.update(updater)

    def read_state_with_dispatch_activity(self, *, dispatch_id):
        state = self.repository.read()
        with self.repository.base.store._read_connection() as connection:
            connection.execute("BEGIN")
            actual = self.repository.base.store._read_revision_from_connection(connection)
            if actual != state.revision:
                raise TeamStateConflictError(
                    expected_revision=state.revision, current_revision=actual
                )
            activities = self.repository.base.store._list_dispatch_activity(
                connection, dispatch_id=dispatch_id
            )
        return state, activities

    def update_state_with_command_transaction(self, mutator):
        # Reuse the independent dispatch-activity guard in the existing prepared
        # finalizer, with a scoped state and delta commit instead of full state.
        from .services.prepared_finalization import commit_prepared_dispatch_finalization

        dispatch_ids = self.repository.dispatch_ids
        if len(dispatch_ids) != 1:
            raise ValueError("activity finalization requires exactly one dispatch")
        return commit_prepared_dispatch_finalization(
            self.repository,
            dispatch_id=dispatch_ids[0],
            mutator=mutator,
        )
