from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agent_sandbox.events.models import ReviewNewRequestTriggerDefinition, TriggerEvent
from agent_sandbox.review_request_store import ReviewRequestStore
from agent_sandbox.triggers.providers.base import TriggerProviderBase


class ReviewNewRequestTriggerProvider(TriggerProviderBase):
    _LEASE_SECONDS = 300

    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        store = self._store()
        directory_path = self._directory_path()
        lease_owner = f"{trigger.id}:{directory_path or 'root'}"
        claimed = store.claim_ready_requests(
            lease_owner=lease_owner,
            lease_seconds=self._LEASE_SECONDS,
            directory_path=directory_path,
        )

        events: list[TriggerEvent] = []
        for request in claimed:
            events.append(
                TriggerEvent(
                    event_id=f"{trigger.id}:{request.request_id}",
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=request.requested_at.astimezone(UTC),
                    payload={"review_request": request.model_dump(mode="json")},
                )
            )
        return events

    def _trigger(self) -> ReviewNewRequestTriggerDefinition:
        if not isinstance(self.trigger_definition, ReviewNewRequestTriggerDefinition):
            raise RuntimeError("ReviewNewRequestTriggerProvider initialized with wrong definition")
        return self.trigger_definition

    def _store(self) -> ReviewRequestStore:
        return ReviewRequestStore(self._runtime_dir() / "review-request-store.json")

    def _runtime_dir(self) -> Path:
        if self.runtime_context is None:
            raise RuntimeError("ReviewNewRequestTriggerProvider has no runtime context")
        return self.runtime_context.resolved_config.runtime_dir

    def _directory_path(self) -> str | None:
        if self.runtime_context is None:
            return None
        directory_path = str(self.runtime_context.metadata.get("directory_path") or "").strip()
        return directory_path or None
