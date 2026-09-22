from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from evander.models import utc_now

from .models import (
    TeamDispatchActivity,
    TeamIdempotencyRecord,
    TeamOutboxRecord,
    TeamOutboxStatus,
    TeamState,
)
from .persistence import (
    TeamDispatchActivityRepository,
    TeamIdempotencyRepository,
    TeamOutboxRepository,
)
from .sqlite_store import (
    STATE_DB_FILE,
    TeamStateSqliteCommandTransaction,
    TeamStateSqliteStore,
    TeamStateReadProjection,
)
from .lifecycle_candidates import LifecycleCandidatePage

ADDON_ID = "team-addon"
STATE_FILE = STATE_DB_FILE


class TeamStateConflictError(RuntimeError):
    def __init__(self, *, expected_revision: int, current_revision: int) -> None:
        super().__init__(
            f"stale team add-on state revision: expected {expected_revision}, "
            f"current {current_revision}"
        )
        self.expected_revision = expected_revision
        self.current_revision = current_revision


class TeamStateRepository:
    def __init__(self, runtime_dir: str | Path, *, maintenance: bool = False) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.store = TeamStateSqliteStore(self.runtime_dir, maintenance=maintenance)
        self._dispatch_activity_repository = TeamDispatchActivityRepository(self.store)
        self._idempotency_repository = TeamIdempotencyRepository(self.store)
        self._outbox_repository = TeamOutboxRepository(self.store)

    def read(self) -> TeamState:
        return self.store.read_state()

    def read_projection(self, *, collections: Sequence[str]) -> TeamStateReadProjection:
        """Return a read-only subset; mutations must always use full state."""

        return self.store.read_projection(collections=collections)

    def read_revision(self) -> int:
        return self.store.read_revision()

    def lifecycle_candidates(
        self,
        *,
        kind: str = "completion",
        after: str = "",
        limit: int = 50,
        due_at: datetime | None = None,
        dispatch_ids: Sequence[str] | None = None,
    ) -> LifecycleCandidatePage:
        return self.store.lifecycle_candidates(
            kind=kind,
            after=after,
            limit=limit,
            due_at=due_at,
            dispatch_ids=dispatch_ids,
        )

    def update(
        self,
        mutator: Callable[[TeamState], TeamState | None],
        *,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _update(state: TeamState) -> TeamState:
            if expected_revision is not None and state.revision != expected_revision:
                raise TeamStateConflictError(
                    expected_revision=expected_revision,
                    current_revision=state.revision,
                )
            return mutator(state) or state

        return self.store.update_state(_update)

    def update_with_outbox(
        self,
        mutator: Callable[[TeamState], TeamState | None],
        *,
        outbox_records: Callable[[], Sequence[TeamOutboxRecord | Mapping[str, Any]]],
        expected_revision: int | None = None,
    ) -> TeamState:
        def _update(
            state: TeamState,
            tx: TeamStateSqliteCommandTransaction,
        ) -> TeamState:
            if expected_revision is not None and state.revision != expected_revision:
                raise TeamStateConflictError(
                    expected_revision=expected_revision,
                    current_revision=state.revision,
                )
            next_state = mutator(state) or state
            next_state.updated_at = utc_now()
            for record in outbox_records():
                tx.enqueue_outbox_record(record)
            return next_state

        return self.store.update_state_with_command_transaction(_update)

    def update_with_dispatch_activity(
        self,
        mutator: Callable[[TeamState], TeamState | None],
        *,
        activity: TeamDispatchActivity | Mapping[str, Any],
        expected_revision: int | None = None,
    ) -> TeamState:
        def _update(
            state: TeamState,
            tx: TeamStateSqliteCommandTransaction,
        ) -> TeamState:
            if expected_revision is not None and state.revision != expected_revision:
                raise TeamStateConflictError(
                    expected_revision=expected_revision,
                    current_revision=state.revision,
                )
            next_state = mutator(state) or state
            next_state.updated_at = utc_now()
            tx.append_dispatch_activity(activity)
            return next_state

        return self.store.update_state_with_command_transaction(_update)

    def update_with_command_transaction(
        self,
        mutator: Callable[[TeamState, TeamStateSqliteCommandTransaction], TeamState | None],
        *,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _update(
            state: TeamState,
            tx: TeamStateSqliteCommandTransaction,
        ) -> TeamState:
            if expected_revision is not None and state.revision != expected_revision:
                raise TeamStateConflictError(
                    expected_revision=expected_revision,
                    current_revision=state.revision,
                )
            next_state = mutator(state, tx) or state
            next_state.updated_at = utc_now()
            return next_state

        return self.store.update_state_with_command_transaction(_update)

    def commit_prepared_state_with_command_transaction(
        self,
        *,
        previous_payload: Mapping[str, Any],
        candidate: TeamState,
        command_writer: Callable[[TeamStateSqliteCommandTransaction], None],
    ) -> TeamState:
        expected_revision = int(previous_payload.get("revision", 0))
        committed, current_revision, state = (
            self.store.commit_prepared_state_with_command_transaction(
                previous_payload=previous_payload,
                candidate=candidate,
                command_writer=command_writer,
            )
        )
        if not committed or state is None:
            raise TeamStateConflictError(
                expected_revision=expected_revision,
                current_revision=current_revision,
            )
        return state

    def append_dispatch_activity(
        self,
        activity: TeamDispatchActivity | Mapping[str, Any],
    ) -> TeamDispatchActivity:
        return self._dispatch_activity_repository.append_dispatch_activity(activity)

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
        return self._dispatch_activity_repository.list_dispatch_activity(
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

    def normalized_payload_digest(self, payload: Mapping[str, Any]) -> str:
        return self._idempotency_repository.normalized_payload_digest(payload)

    def check_idempotency_replay(
        self,
        *,
        tool_name: str,
        caller_identity: str,
        scope_kind: str,
        scope_id: str,
        request_id: str,
        normalized_payload_digest: str,
    ) -> TeamIdempotencyRecord | None:
        return self._idempotency_repository.check_idempotency_replay(
            tool_name=tool_name,
            caller_identity=caller_identity,
            scope_kind=scope_kind,
            scope_id=scope_id,
            request_id=request_id,
            normalized_payload_digest=normalized_payload_digest,
        )

    def store_idempotency_record(
        self,
        record: TeamIdempotencyRecord | Mapping[str, Any],
    ) -> TeamIdempotencyRecord:
        return self._idempotency_repository.store_idempotency_record(record)

    def get_idempotency_record(
        self,
        *,
        tool_name: str,
        caller_identity: str,
        scope_kind: str,
        scope_id: str,
        request_id: str,
    ) -> TeamIdempotencyRecord | None:
        return self._idempotency_repository.get_idempotency_record(
            tool_name=tool_name,
            caller_identity=caller_identity,
            scope_kind=scope_kind,
            scope_id=scope_id,
            request_id=request_id,
        )

    def enqueue_outbox_record(
        self,
        record: TeamOutboxRecord | Mapping[str, Any],
    ) -> TeamOutboxRecord:
        return self._outbox_repository.enqueue_outbox_record(record)

    def list_outbox_records(
        self,
        *,
        status: TeamOutboxStatus | str | None = None,
        intent_type: str | None = None,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        limit: int | None = None,
    ) -> list[TeamOutboxRecord]:
        return self._outbox_repository.list_outbox_records(
            status=status,
            intent_type=intent_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            limit=limit,
        )

    def claim_outbox_records(
        self,
        *,
        worker_id: str,
        limit: int = 1,
        intent_types: Sequence[str] | None = None,
        record_ids: Sequence[str] | None = None,
        claim_token: str | None = None,
        claim_ttl_seconds: int = 300,
        now: datetime | None = None,
    ) -> list[TeamOutboxRecord]:
        return self._outbox_repository.claim_outbox_records(
            worker_id=worker_id,
            limit=limit,
            intent_types=intent_types,
            record_ids=record_ids,
            claim_token=claim_token,
            claim_ttl_seconds=claim_ttl_seconds,
            now=now,
        )

    def complete_outbox_record(
        self,
        *,
        record_id: str,
        claim_token: str | None = None,
        now: datetime | None = None,
    ) -> TeamOutboxRecord:
        return self._outbox_repository.complete_outbox_record(
            record_id=record_id,
            claim_token=claim_token,
            now=now,
        )

    def fail_outbox_record(
        self,
        *,
        record_id: str,
        error_code: str | None = None,
        error_message: str | None = None,
        claim_token: str | None = None,
        retry_after_seconds: int | None = None,
        now: datetime | None = None,
    ) -> TeamOutboxRecord:
        return self._outbox_repository.fail_outbox_record(
            record_id=record_id,
            error_code=error_code,
            error_message=error_message,
            claim_token=claim_token,
            retry_after_seconds=retry_after_seconds,
            now=now,
        )


__all__ = [
    "ADDON_ID",
    "STATE_DB_FILE",
    "STATE_FILE",
    "TeamStateConflictError",
    "TeamStateRepository",
]
