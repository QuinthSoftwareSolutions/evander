from __future__ import annotations

from collections.abc import Iterable

from ...models import AdoWorkItemAttribution, ProjectGitIdentity, TeamState


def task_author_identity(value: object) -> ProjectGitIdentity | None:
    """Validate an optional task author using the project Git identity contract."""
    return ProjectGitIdentity.model_validate(value) if value is not None else None


def inherited_task_author(
    state: TeamState,
    *,
    project_id: str,
    authored_by: object = None,
    originating_task_id: str | None = None,
    parent_task_id: str | None = None,
) -> ProjectGitIdentity | None:
    identity = task_author_identity(authored_by)
    if identity is not None:
        return identity.model_copy(deep=True)
    # A correction's source is more specific than its coordinating parent.
    for source_id in (originating_task_id, parent_task_id):
        source = state.tasks.get(source_id or "")
        if source is not None and source.project_id == project_id:
            return source.authored_by.model_copy(deep=True) if source.authored_by else None
    return None


def common_task_author(state: TeamState, task_ids: Iterable[str]) -> ProjectGitIdentity | None:
    """Keep a shared source author; mixed-author aggregate work uses the project default."""
    identities = [
        state.tasks[task_id].authored_by if task_id in state.tasks else None for task_id in task_ids
    ]
    if (
        identities
        and identities[0] is not None
        and all(identity == identities[0] for identity in identities)
    ):
        return identities[0].model_copy(deep=True)
    return None


def common_deliverable_author(
    state: TeamState, deliverable_ids: Iterable[str]
) -> ProjectGitIdentity | None:
    return common_task_author(
        state,
        (
            state.deliverables[deliverable_id].task_id
            if deliverable_id in state.deliverables
            else ""
            for deliverable_id in deliverable_ids
        ),
    )


def inherited_work_item_attributions(
    state: TeamState,
    *,
    project_id: str,
    originating_task_id: str | None = None,
    parent_task_id: str | None = None,
) -> list[AdoWorkItemAttribution] | None:
    for source_id in (originating_task_id, parent_task_id):
        source = state.tasks.get(source_id or "")
        if source is not None and source.project_id == project_id:
            return (
                [item.model_copy(deep=True) for item in source.work_item_attributions]
                if source.work_item_attributions
                else None
            )
    return None


def combined_deliverable_attributions(
    state: TeamState, deliverable_ids: Iterable[str]
) -> list[AdoWorkItemAttribution] | None:
    results: list[AdoWorkItemAttribution] = []
    visited: set[str] = set()
    pending = list(deliverable_ids)
    while pending:
        deliverable_id = pending.pop()
        if deliverable_id in visited:
            continue
        visited.add(deliverable_id)
        deliverable = state.deliverables.get(deliverable_id)
        task = state.tasks.get(deliverable.task_id) if deliverable else None
        if task is None or not task.work_item_attributions:
            return None
        for item in task.work_item_attributions:
            if item not in results:
                results.append(item.model_copy(deep=True))
        if deliverable is not None:
            pending.extend(deliverable.source_deliverable_ids)
            if deliverable.dependency_composition_manifest:
                pending.extend(
                    source.deliverable_id
                    for source in deliverable.dependency_composition_manifest.dependency_git_sources
                )
    return results or None
