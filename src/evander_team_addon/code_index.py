"""Adapt immutable Team Git bindings to the generic repository index."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path


def protected_bindings(runtime_dir: Path) -> set[tuple[str, str]]:
    if not (Path(runtime_dir) / "addons/team-addon/state.db").exists():
        return set()
    from .repository import TeamStateRepository

    state = TeamStateRepository(Path(runtime_dir)).read_projection(
        collections=(
            "git_work_contexts",
            "tasks",
            "projects",
            "review_requests",
            "ephemeral_assignments",
        )
    )
    result = set()
    for context in state.git_work_contexts.values():
        if str(getattr(context.state, "value", context.state)) != "active":
            continue
        task = state.tasks.get(context.task_id)
        project = state.projects.get(task.project_id) if task else None
        if project and project.git:
            result.update(
                (project.git.managed_repo_id, sha)
                for sha in (context.base_commit, context.commit_sha)
                if sha
            )
    for review in state.review_requests.values():
        if review.repository_snapshot and str(getattr(review.state, "value", review.state)) in {
            "pending",
            "in_progress",
        }:
            result.add(
                (review.repository_snapshot.managed_repo_id, review.repository_snapshot.commit_sha)
            )
    for assignment in state.ephemeral_assignments.values():
        if str(getattr(assignment.state, "value", assignment.state)) in {
            "requested",
            "spawn_pending",
            "running",
        }:
            result.update(
                (b["managed_repo_id"], b["commit_sha"])
                for b in assignment.metadata.get("code_index_bindings", [])
            )
    return result


def session_bindings(runtime_dir: Path, session_id: str) -> set[tuple[str, str]] | None:
    if not (Path(runtime_dir) / "addons/team-addon/state.db").exists():
        return None
    from .repository import TeamStateRepository

    state = TeamStateRepository(Path(runtime_dir)).read_projection(
        collections=(
            "agents",
            "agent_runtimes",
            "dispatches",
            "ephemeral_assignments",
            "git_work_contexts",
            "tasks",
            "projects",
            "review_requests",
        )
    )
    agent = next((agent for agent in state.agents.values() if agent.session_id == session_id), None)
    assignments = [a for a in state.ephemeral_assignments.values() if a.session_id == session_id]
    if agent is None:
        if assignments:
            # Ephemeral sessions are Team-scoped too; never fall through to a broad Git grant.
            return {
                (str(b["managed_repo_id"]), str(b["commit_sha"]))
                for a in assignments
                if str(getattr(a.state, "value", a.state)) == "running"
                for b in a.metadata.get("code_index_bindings", [])
            }
        return None
    runtime = state.agent_runtimes.get(agent.id)
    dispatch = (
        state.dispatches.get(runtime.active_dispatch_id)
        if runtime and runtime.active_dispatch_id
        else None
    )
    if (
        not dispatch
        or dispatch.session_id != session_id
        or str(getattr(dispatch.status, "value", dispatch.status)) != "leased"
        or (dispatch.lease_expires_at and dispatch.lease_expires_at <= datetime.now(UTC))
    ):
        return set()
    bindings = set()
    for context in state.git_work_contexts.values():
        if (
            context.agent_id != agent.id
            or context.dispatch_id != dispatch.id
            or context.task_id not in dispatch.task_ids
            or str(getattr(context.state, "value", context.state)) != "active"
        ):
            continue
        task = state.tasks.get(context.task_id)
        project = state.projects.get(task.project_id) if task else None
        sha = context.commit_sha or context.base_commit
        if project and project.git and sha:
            bindings.add((project.git.managed_repo_id, sha))
    for review_id in dispatch.review_request_ids:
        review = state.review_requests.get(review_id)
        snapshot = review.repository_snapshot if review else None
        if snapshot:
            bindings.add((snapshot.managed_repo_id, snapshot.commit_sha))
    return bindings


def prepare_indexes(context, project_repo: dict) -> None:
    """Attach existing coverage to the wake; Team work never queues compiler builds."""
    service = getattr(context.admin_service, "repository_index_admin", None)
    if service is None:
        return
    repo = project_repo.get("managed_repo_id")
    directory = str(context.metadata.get("directory_path") or "root")
    sha_values = {
        row.get("commit_sha") or row.get("base_commit")
        for row in project_repo.get("git_work_contexts", [])
    }
    sha_values.update(
        row.get("commit_sha") for row in project_repo.get("review_repository_snapshots", [])
    )
    if not sha_values:
        sha_values.add(project_repo.get("commit_sha"))
    statuses = []
    for sha in sorted(value for value in sha_values if value):
        try:
            result = service.ensure(repo, directory, sha, for_task=True)
            if result["status"] != "disabled":
                statuses.append(
                    {
                        "commit_sha": sha,
                        "status": result["status"],
                        "indexed_commit_sha": result.get("commit_sha"),
                        "match": result.get("match"),
                        "snapshot_id": result.get("snapshot_id"),
                    }
                )
        except Exception:
            statuses.append({"commit_sha": sha, "status": "unavailable"})
    if statuses:
        project_repo["code_indexes"] = statuses
