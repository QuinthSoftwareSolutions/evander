from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from .models import (
    TeamDispatchActivity,
    TeamIdempotencyRecord,
    TeamOutboxRecord,
    TeamOutboxStatus,
)
from .sqlite_store import (
    TeamStateSqliteStore,
    normalized_payload_digest as _normalized_payload_digest,
)


class TeamDispatchActivityRepository:
    def __init__(self, store: TeamStateSqliteStore) -> None:
        self._store = store

    def append_dispatch_activity(
        self,
        activity: TeamDispatchActivity | Mapping[str, Any],
    ) -> TeamDispatchActivity:
        return self._store.append_dispatch_activity(activity)

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
        return self._store.list_dispatch_activity(
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


class TeamIdempotencyRepository:
    def __init__(self, store: TeamStateSqliteStore) -> None:
        self._store = store

    def normalized_payload_digest(self, payload: Mapping[str, Any]) -> str:
        return _normalized_payload_digest(payload)

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
        return self._store.check_idempotency_replay(
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
        return self._store.store_idempotency_record(record)

    def get_idempotency_record(
        self,
        *,
        tool_name: str,
        caller_identity: str,
        scope_kind: str,
        scope_id: str,
        request_id: str,
    ) -> TeamIdempotencyRecord | None:
        return self._store.get_idempotency_record(
            tool_name=tool_name,
            caller_identity=caller_identity,
            scope_kind=scope_kind,
            scope_id=scope_id,
            request_id=request_id,
        )


class TeamOutboxRepository:
    def __init__(self, store: TeamStateSqliteStore) -> None:
        self._store = store

    def enqueue_outbox_record(
        self,
        record: TeamOutboxRecord | Mapping[str, Any],
    ) -> TeamOutboxRecord:
        return self._store.enqueue_outbox_record(record)

    def list_outbox_records(
        self,
        *,
        status: TeamOutboxStatus | str | None = None,
        intent_type: str | None = None,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        limit: int | None = None,
    ) -> list[TeamOutboxRecord]:
        return self._store.list_outbox_records(
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
        return self._store.claim_outbox_records(
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
        return self._store.complete_outbox_record(
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
        return self._store.fail_outbox_record(
            record_id=record_id,
            error_code=error_code,
            error_message=error_message,
            claim_token=claim_token,
            retry_after_seconds=retry_after_seconds,
            now=now,
        )
