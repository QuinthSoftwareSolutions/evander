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
    LEGACY_STATE_FILE,
    STATE_DB_FILE,
    TeamStateSqliteCommandTransaction,
    TeamStateSqliteStore,
)

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
    def __init__(self, runtime_dir: str | Path) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.store = TeamStateSqliteStore(self.runtime_dir)
        self._dispatch_activity_repository = TeamDispatchActivityRepository(self.store)
        self._idempotency_repository = TeamIdempotencyRepository(self.store)
        self._outbox_repository = TeamOutboxRepository(self.store)

    def read(self) -> TeamState:
        return self.store.read_state()

    def read_revision(self) -> int:
        return self.store.read_revision()

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
            payload: dict[str, Any],
            tx: TeamStateSqliteCommandTransaction,
        ) -> TeamState:
            state = TeamState.model_validate(payload)
            if expected_revision is not None and state.revision != expected_revision:
                raise TeamStateConflictError(
                    expected_revision=expected_revision,
                    current_revision=state.revision,
                )
            next_state = mutator(state) or state
            next_state = next_state.model_copy(
                update={"updated_at": utc_now()},
                deep=True,
            )
            validated = TeamState.model_validate(next_state.model_dump(mode="json"))
            payload.clear()
            payload.update(validated.model_dump(mode="json"))
            for record in outbox_records():
                tx.enqueue_outbox_record(record)
            return validated

        return self.store.update_object_with_command_transaction(_update)

    def update_with_dispatch_activity(
        self,
        mutator: Callable[[TeamState], TeamState | None],
        *,
        activity: TeamDispatchActivity | Mapping[str, Any],
        expected_revision: int | None = None,
    ) -> TeamState:
        def _update(
            payload: dict[str, Any],
            tx: TeamStateSqliteCommandTransaction,
        ) -> TeamState:
            state = TeamState.model_validate(payload)
            if expected_revision is not None and state.revision != expected_revision:
                raise TeamStateConflictError(
                    expected_revision=expected_revision,
                    current_revision=state.revision,
                )
            next_state = mutator(state) or state
            next_state = next_state.model_copy(
                update={"updated_at": utc_now()},
                deep=True,
            )
            validated = TeamState.model_validate(next_state.model_dump(mode="json"))
            payload.clear()
            payload.update(validated.model_dump(mode="json"))
            tx.append_dispatch_activity(activity)
            return validated

        return self.store.update_object_with_command_transaction(_update)

    def update_with_command_transaction(
        self,
        mutator: Callable[[TeamState, TeamStateSqliteCommandTransaction], TeamState | None],
        *,
        expected_revision: int | None = None,
    ) -> TeamState:
        def _update(
            payload: dict[str, Any],
            tx: TeamStateSqliteCommandTransaction,
        ) -> TeamState:
            state = TeamState.model_validate(payload)
            if expected_revision is not None and state.revision != expected_revision:
                raise TeamStateConflictError(
                    expected_revision=expected_revision,
                    current_revision=state.revision,
                )
            next_state = mutator(state, tx) or state
            next_state = next_state.model_copy(
                update={"updated_at": utc_now()},
                deep=True,
            )
            validated = TeamState.model_validate(next_state.model_dump(mode="json"))
            payload.clear()
            payload.update(validated.model_dump(mode="json"))
            return validated

        return self.store.update_object_with_command_transaction(_update)

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
    "LEGACY_STATE_FILE",
    "STATE_DB_FILE",
    "STATE_FILE",
    "TeamStateConflictError",
    "TeamStateRepository",
]
