"""Prepare the background dispatch finalizer without holding the Team writer."""

from __future__ import annotations

from typing import Any

from ..models import TeamDispatchActivity, TeamOutboxRecord
from ..repository import TeamStateRepository


class DispatchActivityConflictError(RuntimeError):
    """New command evidence requires a fresh finalization plan."""


class _FinalizationCommands:
    """Only the three command operations used by dispatch finalization."""

    def __init__(self, dispatch_id: str, activities: list[TeamDispatchActivity]) -> None:
        self.dispatch_id = dispatch_id
        self.activities = activities
        self.outbox: list[TeamOutboxRecord] = []
        self.appended: list[TeamDispatchActivity] = []

    def list_dispatch_activity(self, *, dispatch_id: str) -> list[TeamDispatchActivity]:
        if dispatch_id != self.dispatch_id:
            raise ValueError("Finalization evidence belongs to another dispatch")
        return list(self.activities)

    def enqueue_outbox_record(self, record: Any) -> TeamOutboxRecord:
        stored = TeamOutboxRecord.model_validate(record)
        self.outbox.append(stored)
        return stored

    def append_dispatch_activity(self, activity: Any) -> TeamDispatchActivity:
        stored = TeamDispatchActivity.model_validate(activity)
        self.appended.append(stored)
        return stored


def commit_prepared_dispatch_finalization(
    repository: TeamStateRepository, *, dispatch_id: str, mutator
):
    state, activities = repository.store.read_state_with_dispatch_activity(dispatch_id=dispatch_id)
    baseline = state.model_dump(mode="json")
    commands = _FinalizationCommands(dispatch_id, activities)
    candidate, result = mutator(state, commands)

    def write_commands(transaction):
        # Activity append can occur without a Team state revision change. Guard
        # the evidence independently under the same writer as the revision CAS.
        if transaction.list_dispatch_activity(dispatch_id=dispatch_id) != activities:
            raise DispatchActivityConflictError(
                "Dispatch activity changed during finalization planning"
            )
        stored_ids = [transaction.enqueue_outbox_record(item).id for item in commands.outbox]
        if stored_ids:
            result["outbox_record_ids"] = stored_ids
        for activity in commands.appended:
            transaction.append_dispatch_activity(activity)

    persisted = repository.commit_prepared_state_with_command_transaction(
        previous_payload=baseline,
        candidate=candidate,
        command_writer=write_commands,
    )
    return persisted, result
