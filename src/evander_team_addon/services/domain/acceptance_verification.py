from __future__ import annotations

from hashlib import sha256
from typing import Any

from evander.models import utc_now

from ...models import (
    AgentTask,
    Deliverable,
    DeliverableAcceptanceVerificationCommandResult,
    DeliverableAcceptanceVerificationWaiver,
    DeliverableVerification,
    TeamState,
)
from .common import _required_text
from .correction_git_source import is_rejected_candidate_correction_task


DELIVERABLE_ACCEPTANCE_VERIFICATION_REQUESTED_EVENT_TYPE = (
    "team.deliverable.acceptance_verification_requested"
)


def deliverable_requires_server_acceptance_verification(
    state: TeamState,
    *,
    task: AgentTask,
    deliverable: Deliverable,
) -> bool:
    """Return whether prospective policy v2 requires a server-owned preflight."""

    return bool(
        deliverable.acceptance_policy_version >= 2
        and (
            task.integration_boundary
            or is_rejected_candidate_correction_task(state, task_id=task.id)
        )
    )


def initialize_deliverable_acceptance_verification(
    state: TeamState,
    *,
    deliverable: Deliverable,
    result: dict[str, Any],
) -> DeliverableVerification | None:
    """Freeze release-equivalent checks for a newly submitted reviewed deliverable."""

    task = state.tasks.get(deliverable.task_id)
    if task is None or not deliverable_requires_server_acceptance_verification(
        state,
        task=task,
        deliverable=deliverable,
    ):
        return None
    project = state.projects.get(task.project_id)
    if project is None or project.git is None:
        raise ValueError(
            f"deliverable {deliverable.id} requires server acceptance verification "
            "but its project has no managed Git configuration"
        )
    candidate_commit_sha = (
        deliverable.git_refs.reviewed_commit_sha
        or deliverable.git_refs.submitted_commit_sha
        or deliverable.git_refs.commit_sha
    )
    if candidate_commit_sha is None:
        raise ValueError(
            f"deliverable {deliverable.id} requires server acceptance verification "
            "but has no immutable candidate commit"
        )
    policy = project.release_policy
    commands = (
        [command.model_copy(deep=True) for command in policy.verification_commands]
        if policy is not None
        else []
    )
    material = "\0".join(
        (
            deliverable.id,
            candidate_commit_sha.casefold(),
            *(
                "\0".join((command.name, command.cwd, *command.argv))
                for command in commands
            ),
        )
    )
    run_id = f"acceptance-verification-{sha256(material.encode('utf-8')).hexdigest()[:24]}"
    verification = DeliverableVerification(
        status=("pending" if commands else "not_run"),
        summary=(
            "Release-equivalent acceptance verification is queued."
            if commands
            else "No release-equivalent verification commands are configured."
        ),
        provenance="server_release_equivalent",
        run_id=run_id,
        candidate_commit_sha=candidate_commit_sha,
        verification_commands=commands,
        command_results=[
            DeliverableAcceptanceVerificationCommandResult(name=command.name)
            for command in commands
        ],
        container_image=(policy.container_image if policy is not None else None),
        resource_limits=(
            policy.resource_limits.model_copy(deep=True)
            if policy is not None and policy.resource_limits is not None
            else None
        ),
    )
    deliverable.verification = verification
    if commands:
        result.setdefault("events", []).append(
            {
                "event_type": DELIVERABLE_ACCEPTANCE_VERIFICATION_REQUESTED_EVENT_TYPE,
                "correlation_key": run_id,
                "idempotency_key": (
                    "team-addon:"
                    f"{DELIVERABLE_ACCEPTANCE_VERIFICATION_REQUESTED_EVENT_TYPE}:{run_id}"
                ),
                "payload": {
                    "deliverable_id": deliverable.id,
                    "verification_run_id": run_id,
                    "candidate_commit_sha": candidate_commit_sha,
                },
            }
        )
    return verification


def deliverable_acceptance_verification_error(
    state: TeamState,
    *,
    deliverable: Deliverable,
) -> str | None:
    task = state.tasks.get(deliverable.task_id)
    if task is None or not deliverable_requires_server_acceptance_verification(
        state,
        task=task,
        deliverable=deliverable,
    ):
        return None
    verification = deliverable.verification
    if verification is None or verification.provenance != "server_release_equivalent":
        return (
            f"deliverable {deliverable.id} has no server-owned release-equivalent "
            "acceptance verification"
        )
    if verification.status == "passed":
        if not verification.evidence_refs:
            return f"deliverable {deliverable.id} passed verification without durable evidence"
        return None
    if verification.status in {"partial", "not_run"}:
        if verification.waiver is not None:
            return None
        return (
            f"deliverable {deliverable.id} has {verification.status} acceptance verification; "
            "an authorized operator waiver with a reason is required"
        )
    if verification.status in {"pending", "running"}:
        return (
            f"deliverable {deliverable.id} acceptance verification is "
            f"{verification.status}; wait for the server-owned run to finish"
        )
    return (
        f"deliverable {deliverable.id} acceptance verification is {verification.status}; "
        "submit corrected reviewed content"
    )


def authorize_deliverable_acceptance_verification_waiver(
    state: TeamState,
    *,
    deliverable_id: str,
    operator_actor_id: str,
    reason: str,
) -> Deliverable:
    deliverable = state.deliverables.get(_required_text(deliverable_id, "deliverable_id"))
    if deliverable is None:
        raise ValueError(f"deliverable {deliverable_id} does not exist")
    verification = deliverable.verification
    if (
        verification is None
        or verification.provenance != "server_release_equivalent"
        or verification.status not in {"partial", "not_run"}
    ):
        raise ValueError(
            "acceptance verification waiver requires a server-owned partial or not_run result"
        )
    verification.waiver = DeliverableAcceptanceVerificationWaiver(
        actor_id=_required_text(operator_actor_id, "operator_actor_id"),
        reason=_required_text(reason, "reason"),
        created_at=utc_now(),
    )
    return deliverable


__all__ = [
    "DELIVERABLE_ACCEPTANCE_VERIFICATION_REQUESTED_EVENT_TYPE",
    "authorize_deliverable_acceptance_verification_waiver",
    "deliverable_acceptance_verification_error",
    "deliverable_requires_server_acceptance_verification",
    "initialize_deliverable_acceptance_verification",
]
