"""Bounded writes for existing dispatch receipts and runtime bookkeeping."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from evander.models import utc_now

from .models import SCHEMA_VERSION, AgentRuntime, TeamDispatch, TeamState
from .sqlite_codec import _datetime_value, _json_dumps


@dataclass(frozen=True)
class TeamAgentSession:
    id: str
    session_id: str


@dataclass
class TeamDispatchBookkeepingScope:
    """Existing rows only, with no authority to change their domain relationships."""

    schema_version: int
    revision: int
    updated_at: datetime
    dispatches: dict[str, TeamDispatch]
    agent_runtimes: dict[str, AgentRuntime]
    agents: dict[str, TeamAgentSession]


class TeamDispatchBookkeepingConflictError(RuntimeError):
    def __init__(self, *, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__("dispatch bookkeeping snapshot is stale")


_DISPATCH_FIELDS = frozenset(
    {
        "queued_at",
        "preparation_expires_at",
        "native_thread_id",
        "native_thread_observed_at",
        "mcp_capability_receipt",
    }
)
_RUNTIME_FIELDS = frozenset(
    {
        "last_thread_id",
        "work_lineage_thread_ids",
        "work_lineage_thread_dispatch_ids",
        "last_delivered_role_description_fingerprint",
        "updated_at",
    }
)


def _validate_changes(
    *,
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    allowed: frozenset[str],
) -> dict[str, dict[str, Any]]:
    if before.keys() != after.keys():
        raise ValueError("dispatch bookkeeping cannot add or remove domain records")
    changed = {}
    for record_id, record in after.items():
        previous = before[record_id]
        fields = {
            field
            for field in previous.keys() | record.keys()
            if previous.get(field) != record.get(field)
        }
        if fields - allowed:
            raise ValueError("dispatch bookkeeping cannot change domain relationships or lifecycle")
        if fields:
            changed[record_id] = record
    return changed


class DispatchBookkeepingStoreMixin:
    def update_dispatch_bookkeeping(
        self,
        updater: Callable[[TeamDispatchBookkeepingScope], object],
        *,
        dispatch_ids: Sequence[str],
        expected_revision: int | None = None,
        include_state: bool = True,
        state_collections: Sequence[str] | None = None,
    ) -> tuple[TeamDispatchBookkeepingScope, TeamState | None]:
        """Update at most 50 existing dispatches without loading unrelated history.

        This path cannot change identities, status, authorization scope, leases,
        or other domain links. The only nested receipt change permitted is grant
        confirmation. Runtime lineage keys must still refer to existing lineages.
        Together with row validation, these restrictions preserve the full-state
        invariants without reconstructing every collection. General commands and
        their activity/idempotency/outbox writes retain the full-state transaction.

        The global revision check, current-row read, mutation and metadata write
        share one SQLite writer transaction, including across processes. A caller
        with a snapshot can patch its result only when that revision still matches.
        A fresh result may retain the caller's read projection collection set.
        """
        ids = tuple(sorted(set(dispatch_ids)))
        if not ids or len(ids) > 50:
            raise ValueError("dispatch bookkeeping requires between 1 and 50 dispatch IDs")
        with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                metadata = dict(
                    connection.execute(
                        "SELECT key, value FROM team_state_metadata "
                        "WHERE key IN ('revision', 'updated_at', 'state_schema_version')"
                    )
                )
                revision = int(metadata["revision"])
                if expected_revision is not None and revision != expected_revision:
                    raise TeamDispatchBookkeepingConflictError(
                        expected_revision=expected_revision,
                        current_revision=revision,
                    )
                payload = {"dispatches": {}, "agent_runtimes": {}}
                self._read_dispatches(connection, payload, dispatch_ids=ids)
                agent_ids = tuple(sorted({d["agent_id"] for d in payload["dispatches"].values()}))
                self._read_agent_runtimes(connection, payload, agent_ids=agent_ids)
                placeholders = ",".join("?" for _ in agent_ids)
                agents = {
                    row["id"]: TeamAgentSession(id=row["id"], session_id=row["session_id"])
                    for row in connection.execute(
                        f"SELECT id, session_id FROM team_agents WHERE id IN ({placeholders})",
                        agent_ids,
                    )
                }
                scope = TeamDispatchBookkeepingScope(
                    schema_version=int(metadata["state_schema_version"]),
                    revision=revision,
                    updated_at=_datetime_value(metadata["updated_at"]),
                    dispatches={
                        key: TeamDispatch.model_validate(value)
                        for key, value in payload["dispatches"].items()
                    },
                    agent_runtimes={
                        key: AgentRuntime.model_validate(value)
                        for key, value in payload["agent_runtimes"].items()
                    },
                    agents=agents,
                )
                baseline_dispatches = {
                    key: row.model_dump(mode="json") for key, row in scope.dispatches.items()
                }
                baseline_runtimes = {
                    key: row.model_dump(mode="json") for key, row in scope.agent_runtimes.items()
                }
                baseline_agents = dict(agents)
                updater(scope)
                if (
                    scope.agents != baseline_agents
                    or scope.revision != revision
                    or scope.schema_version != int(metadata["state_schema_version"])
                    or scope.updated_at != _datetime_value(metadata["updated_at"])
                ):
                    raise ValueError("dispatch bookkeeping cannot change agents or state metadata")
                # Assignment validation is not enabled on these mutable models.
                # Revalidate bounded rows before any writes, then compare every
                # field so callbacks cannot bypass the relationship restrictions.
                scope.dispatches = {
                    key: TeamDispatch.model_validate(row.model_dump(mode="json"))
                    for key, row in scope.dispatches.items()
                }
                scope.agent_runtimes = {
                    key: AgentRuntime.model_validate(row.model_dump(mode="json"))
                    for key, row in scope.agent_runtimes.items()
                }
                dispatches = _validate_changes(
                    before=baseline_dispatches,
                    after={
                        key: row.model_dump(mode="json") for key, row in scope.dispatches.items()
                    },
                    allowed=_DISPATCH_FIELDS,
                )
                runtimes = _validate_changes(
                    before=baseline_runtimes,
                    after={
                        key: row.model_dump(mode="json")
                        for key, row in scope.agent_runtimes.items()
                    },
                    allowed=_RUNTIME_FIELDS,
                )
                for key, dispatch in dispatches.items():
                    previous_receipt = baseline_dispatches[key]["mcp_capability_receipt"]
                    receipt = dispatch["mcp_capability_receipt"]
                    if previous_receipt != receipt:
                        if (
                            previous_receipt is None
                            or receipt is None
                            or {
                                name: value
                                for name, value in previous_receipt.items()
                                if name != "grant_confirmed_at"
                            }
                            != {
                                name: value
                                for name, value in receipt.items()
                                if name != "grant_confirmed_at"
                            }
                        ):
                            raise ValueError(
                                "dispatch bookkeeping may only confirm an existing MCP grant"
                            )
                lineage_ids = {
                    lineage_id
                    for runtime in runtimes.values()
                    for field in ("work_lineage_thread_ids", "work_lineage_thread_dispatch_ids")
                    for lineage_id in runtime[field]
                }
                if lineage_ids:
                    placeholders = ",".join("?" for _ in lineage_ids)
                    existing = {
                        row["id"]
                        for row in connection.execute(
                            f"SELECT id FROM team_work_lineages WHERE id IN ({placeholders})",
                            tuple(lineage_ids),
                        )
                    }
                    if existing != lineage_ids:
                        raise ValueError("agent runtime references missing work lineage")
                for key, dispatch in dispatches.items():
                    connection.execute(
                        """UPDATE team_dispatches SET queued_at = ?, preparation_expires_at = ?,
                           native_thread_id = ?, native_thread_observed_at = ?,
                           mcp_capability_receipt_json = ? WHERE id = ?""",
                        (
                            dispatch["queued_at"],
                            dispatch["preparation_expires_at"],
                            dispatch["native_thread_id"],
                            dispatch["native_thread_observed_at"],
                            _json_dumps(dispatch["mcp_capability_receipt"]),
                            key,
                        ),
                    )
                for key, runtime in runtimes.items():
                    connection.execute(
                        """UPDATE team_agent_runtimes SET last_thread_id = ?,
                           work_lineage_thread_ids_json = ?, work_lineage_thread_dispatch_ids_json = ?,
                           last_delivered_role_description_fingerprint = ?, updated_at = ?
                           WHERE agent_id = ?""",
                        (
                            runtime["last_thread_id"],
                            _json_dumps(runtime["work_lineage_thread_ids"]),
                            _json_dumps(runtime["work_lineage_thread_dispatch_ids"]),
                            runtime["last_delivered_role_description_fingerprint"],
                            runtime["updated_at"],
                            key,
                        ),
                    )
                if dispatches or runtimes:
                    scope.revision += 1
                    scope.schema_version = SCHEMA_VERSION
                    scope.updated_at = utc_now()
                    connection.executemany(
                        "UPDATE team_state_metadata SET value = ? WHERE key = ?",
                        (
                            (str(scope.revision), "revision"),
                            (scope.updated_at.isoformat(), "updated_at"),
                            (str(SCHEMA_VERSION), "state_schema_version"),
                        ),
                    )
                state = None
                if include_state:
                    state = (
                        self._read_projection(connection, collections=state_collections)
                        if state_collections is not None
                        else self._read_state(connection)
                    )
                return scope, state
