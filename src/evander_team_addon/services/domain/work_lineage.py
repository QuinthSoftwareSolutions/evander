from __future__ import annotations

from collections.abc import Iterable, Mapping

from ...models import (
    AgentTask,
    ReleasePackage,
    TeamBlockerSourceKind,
    TeamState,
    TeamWorkLineage,
    WorkLineageChatSourceRef,
)
from .common import _generated_id, utc_now


def chat_source_refs_for_dispatch(
    state: TeamState,
    dispatch_id: str | None,
) -> list[WorkLineageChatSourceRef]:
    if dispatch_id is None:
        return []
    dispatch = state.dispatches.get(dispatch_id)
    if dispatch is None:
        return []
    message_ids = list(dispatch.chat_message_ids)
    message_ids.extend(
        message.id
        for message in state.conversation_messages.values()
        if message.seen_dispatch_id == dispatch.id
    )
    grouped: dict[str, list[str]] = {}
    for message_id in dict.fromkeys(message_ids):
        message = state.conversation_messages.get(message_id)
        if message is None:
            continue
        grouped.setdefault(message.conversation_id, []).append(message.id)
    return [
        WorkLineageChatSourceRef(conversation_id=conversation_id, message_ids=ids)
        for conversation_id, ids in sorted(grouped.items())
    ]


def ensure_task_work_lineage(
    state: TeamState,
    task: AgentTask,
    *,
    chat_source_refs: Iterable[WorkLineageChatSourceRef] = (),
) -> str:
    lineage_id = _inherited_task_lineage_id(state, task)
    now = utc_now()
    if lineage_id is None:
        lineage_id = _generated_id(
            prefix="work-lineage",
            labels=[task.id],
            existing_ids=state.work_lineages,
        )
        state.work_lineages[lineage_id] = TeamWorkLineage(
            id=lineage_id,
            created_at=now,
            updated_at=now,
        )
    task.work_lineage_id = lineage_id
    append_work_lineage_chat_sources(
        state,
        lineage_id=lineage_id,
        refs=chat_source_refs,
        now=now,
    )
    return lineage_id


def ensure_task_work_lineages(
    state: TeamState,
    tasks: Iterable[AgentTask],
    *,
    chat_source_refs: Iterable[WorkLineageChatSourceRef] = (),
) -> dict[str, str]:
    """Assign task lineages after all members of a creation batch exist.

    Task creation accepts forward parent references. Walking those references
    before assigning a task prevents a child that appears first in the input
    from receiving a provisional root lineage of its own.
    """

    refs = list(chat_source_refs)
    resolved: dict[str, str] = {}
    visiting: set[str] = set()

    def _ensure(task: AgentTask) -> str:
        if task.work_lineage_id is not None:
            append_work_lineage_chat_sources(
                state,
                lineage_id=task.work_lineage_id,
                refs=refs,
            )
            resolved[task.id] = task.work_lineage_id
            return task.work_lineage_id
        if task.id not in visiting:
            visiting.add(task.id)
            for related_task_id in (task.parent_task_id, task.originating_task_id):
                related_task = state.tasks.get(related_task_id or "")
                if related_task is not None and related_task.work_lineage_id is None:
                    _ensure(related_task)
            visiting.remove(task.id)
        lineage_id = ensure_task_work_lineage(
            state,
            task,
            chat_source_refs=refs,
        )
        resolved[task.id] = lineage_id
        return lineage_id

    for task in tasks:
        _ensure(task)
    return resolved


def append_work_lineage_chat_sources(
    state: TeamState,
    *,
    lineage_id: str,
    refs: Iterable[WorkLineageChatSourceRef],
    now=None,
) -> None:
    lineage = state.work_lineages[lineage_id]
    changed = False
    by_conversation = {ref.conversation_id: ref for ref in lineage.chat_source_refs}
    for ref in refs:
        existing = by_conversation.get(ref.conversation_id)
        if existing is None:
            copied = WorkLineageChatSourceRef.model_validate(ref.model_dump(mode="python"))
            lineage.chat_source_refs.append(copied)
            by_conversation[copied.conversation_id] = copied
            changed = True
            continue
        merged = list(dict.fromkeys([*existing.message_ids, *ref.message_ids]))
        if merged != existing.message_ids:
            existing.message_ids = merged
            changed = True
    if changed:
        lineage.updated_at = now or utc_now()


def ensure_release_work_lineage(state: TeamState, release: ReleasePackage) -> str:
    now = utc_now()
    lineage_id: str | None = None
    source_lineage_ids: list[str] = []
    predecessor = (
        state.release_packages.get(release.supersedes_release_package_id)
        if release.supersedes_release_package_id is not None
        else None
    )
    if predecessor is not None and predecessor.work_lineage_id is not None:
        lineage_id = predecessor.work_lineage_id
    else:
        input_lineages: list[str | None] = []
        for deliverable_id in release.deliverable_ids:
            deliverable = state.deliverables.get(deliverable_id)
            task = state.tasks.get(deliverable.task_id) if deliverable is not None else None
            input_lineages.append(task.work_lineage_id if task is not None else None)
        source_lineage_ids = list(dict.fromkeys(row for row in input_lineages if row is not None))
        if input_lineages and None not in input_lineages and len(source_lineage_ids) == 1:
            lineage_id = source_lineage_ids[0]
    if lineage_id is None:
        lineage_id = _generated_id(
            prefix="work-lineage-release",
            labels=[release.id],
            existing_ids=state.work_lineages,
        )
        state.work_lineages[lineage_id] = TeamWorkLineage(
            id=lineage_id,
            created_at=now,
            updated_at=now,
        )
        for source_lineage_id in source_lineage_ids:
            source = state.work_lineages.get(source_lineage_id)
            if source is not None:
                append_work_lineage_chat_sources(
                    state,
                    lineage_id=lineage_id,
                    refs=source.chat_source_refs,
                    now=now,
                )
    release.work_lineage_id = lineage_id
    return lineage_id


def _inherited_task_lineage_id(state: TeamState, task: AgentTask) -> str | None:
    if task.release_base_package_id is not None:
        release = state.release_packages.get(task.release_base_package_id)
        if release is not None and release.work_lineage_id is not None:
            return release.work_lineage_id
    if task.parent_task_id is not None:
        parent = state.tasks.get(task.parent_task_id)
        if parent is not None and parent.work_lineage_id is not None:
            return parent.work_lineage_id
    if task.originating_task_id is not None:
        origin = state.tasks.get(task.originating_task_id)
        if origin is not None and origin.work_lineage_id is not None:
            return origin.work_lineage_id
    return None


def work_lineage_id_for_task_related_record(state: TeamState, record: object) -> str | None:
    task_id = getattr(record, "task_id", None)
    if task_id is not None:
        task = state.tasks.get(task_id)
        return task.work_lineage_id if task is not None else None
    release_id = getattr(record, "release_package_id", None)
    if release_id is not None:
        release = state.release_packages.get(release_id)
        return release.work_lineage_id if release is not None else None
    return None


def work_lineage_id_for_record(state: TeamState, record: object) -> str | None:
    if isinstance(record, Mapping):
        dispatch_id = record.get("dispatch_id")
        if isinstance(dispatch_id, str):
            dispatch = state.dispatches.get(dispatch_id)
            return dispatch.work_lineage_id if dispatch is not None else None
        return None
    if isinstance(record, AgentTask):
        return record.work_lineage_id
    if isinstance(record, ReleasePackage):
        return record.work_lineage_id
    direct = work_lineage_id_for_task_related_record(state, record)
    if direct is not None:
        return direct
    deliverable_id = getattr(record, "deliverable_id", None)
    if deliverable_id is not None:
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is not None:
            task = state.tasks.get(deliverable.task_id)
            if task is not None:
                return task.work_lineage_id
    payload = getattr(record, "payload", None)
    if isinstance(payload, dict):
        for key, collection in (
            ("task_id", state.tasks),
            ("deliverable_id", state.deliverables),
            ("review_request_id", state.review_requests),
            ("release_package_id", state.release_packages),
            ("blocker_id", state.blockers),
        ):
            record_id = payload.get(key)
            if isinstance(record_id, str) and record_id in collection:
                nested = collection[record_id]
                nested_lineage = work_lineage_id_for_record(state, nested)
                if nested_lineage is not None:
                    return nested_lineage
    dispatch_id = getattr(record, "dispatch_id", None)
    if dispatch_id is not None:
        dispatch = state.dispatches.get(dispatch_id)
        if dispatch is not None:
            return dispatch.work_lineage_id
    source_id = getattr(record, "source_id", None)
    if isinstance(source_id, str):
        raw_source_kind = getattr(record, "source_kind", None)
        try:
            source_kind = (
                raw_source_kind
                if isinstance(raw_source_kind, TeamBlockerSourceKind)
                else TeamBlockerSourceKind(str(raw_source_kind))
            )
        except ValueError:
            source_kind = None
        declared_collection = {
            TeamBlockerSourceKind.TASK: state.tasks,
            TeamBlockerSourceKind.DELIVERABLE: state.deliverables,
            TeamBlockerSourceKind.REVIEW_REQUEST: state.review_requests,
            TeamBlockerSourceKind.RELEASE_PACKAGE: state.release_packages,
            TeamBlockerSourceKind.HUMAN_REVIEW_REQUEST: state.human_review_requests,
        }.get(source_kind)
        if declared_collection is not None:
            nested = declared_collection.get(source_id)
            return work_lineage_id_for_record(state, nested) if nested is not None else None
        if source_kind is not None and source_kind != TeamBlockerSourceKind.EXPLICIT:
            return None
        for collection in (
            state.tasks,
            state.deliverables,
            state.review_requests,
            state.release_packages,
            state.human_review_requests,
        ):
            nested = collection.get(source_id)
            if nested is not None:
                return work_lineage_id_for_record(state, nested)
    return None


__all__ = [
    "append_work_lineage_chat_sources",
    "chat_source_refs_for_dispatch",
    "ensure_task_work_lineage",
    "ensure_task_work_lineages",
    "ensure_release_work_lineage",
    "work_lineage_id_for_task_related_record",
    "work_lineage_id_for_record",
]
