"""Typed publication races retain their approved candidate and destination lease."""

import re

from .git_recovery import record_incident, recovery_inputs


def publication_inputs(state, release, task_id):
    inputs = recovery_inputs(state, task_id)
    source_ids = (
        sorted(row.deliverable_id for row in release.manifest.deliverables)
        if release.manifest
        else sorted(release.deliverable_ids)
    )
    inputs["release"] = {
        "id": release.id,
        "state": release.state.value,
        "source_ids": source_ids,
        "candidate_commit_sha": release.candidate_commit_sha,
        "approved_commit_sha": release.approved_commit_sha,
        "release_branch": release.release_branch,
        "canonical_branch": release.canonical_branch,
    }
    return inputs


def record_publication_incident(state, *, release, blocker, outputs):
    # A missing destination or opaque provider error is not evidence of advance.
    destination = outputs.get("destination_commit")
    if (
        outputs.get("reason") != "destination_base_mismatch"
        or not isinstance(destination, str)
        or not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", destination)
        or destination == outputs.get("expected_destination_commit_sha")
    ):
        return None
    sources = [
        state.deliverables[key] for key in release.deliverable_ids if key in state.deliverables
    ]
    if not sources:
        return None
    task_id = sorted(source.task_id for source in sources)[0]
    return record_incident(
        state,
        blocker=blocker,
        task_id=task_id,
        stage="publication",
        cause="destination_advanced",
        inputs=publication_inputs(state, release, task_id),
        evidence={
            "release_package_id": release.id,
            "destination_branch": outputs.get("destination_branch"),
            "destination_commit_sha": destination,
            "expected_destination_commit_sha": outputs.get("expected_destination_commit_sha"),
        },
    )


def current_incident_inputs(state, incident):
    if incident.stage == "publication":
        release = state.release_packages[incident.evidence["release_package_id"]]
        return publication_inputs(state, release, incident.task_id)
    return recovery_inputs(state, incident.task_id)
