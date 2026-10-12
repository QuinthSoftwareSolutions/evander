"""Capture a retained attempt through its recorded execution backend."""

from __future__ import annotations

import inspect
import json
import shlex
from pathlib import PurePosixPath

from evander.execution.git_salvage import capture_git_salvage
from evander.worker.models import WorkerGitSalvageRequest


def capture_retained_attempt(context, state, incident):
    attempt = state.git_preparation_attempts.get(incident.evidence.get("attempt_id", ""))
    if (
        attempt is None
        or attempt.id not in incident.preserved_attempt_ids
        or attempt.task_id != incident.task_id
    ):
        raise ValueError("salvage requires one authoritative retained preparation attempt")
    project = state.projects[incident.project_id]
    if not project.git or project.git.managed_repo_id != attempt.managed_repo_id:
        raise ValueError("retained attempt repository no longer matches this project")
    path = PurePosixPath(attempt.worktree_path)
    if path.parent.parent != PurePosixPath("/home/agent/worktrees"):
        raise ValueError("retained attempt is outside the managed worktree root")
    from evander.admin.composition import build_admin_runtime
    from evander.provider_runtime.adapters import AdminRuntimeProviderAdapter
    from evander.effects.providers.base import EffectRuntimeContext

    adapter = AdminRuntimeProviderAdapter(build_admin_runtime(context.runtime_dir))
    config = context.service.resolve_provider_config_for_session(attempt.session_id)
    runtime = EffectRuntimeContext(
        resolved_config=config,
        admin_service=adapter,
        session_lookup=adapter,
        vm_executor=adapter,
        vm_file_copier=adapter,
        metadata={"directory_path": attempt.directory_path},
    )
    session = adapter.read_all_sessions().get(attempt.session_id)
    metadata = getattr(session, "metadata", {}) if session else {}
    team_metadata = metadata.get("team_addon") or {}
    repositories = team_metadata.get("project_repos") or [team_metadata.get("project_repo") or {}]
    matches = [
        row
        for row in repositories
        if row.get("managed_repo_id") == attempt.managed_repo_id
        and row.get("guest_repo_name") == path.parent.name
        and row.get("target_dir")
        and row.get("remote_url")
    ]
    if len(matches) != 1:
        raise ValueError("retained repository has no unique host-owned checkout record")
    request = WorkerGitSalvageRequest(
        repo_name=path.parent.name,
        worktree_name=path.name,
        repo_source=matches[0]["remote_url"],
        expected_branch=attempt.branch,
        recovery_branch=f"agents/{attempt.session_id}/recovery-{incident.id}",
    )
    if attempt.execution_backend == "container_worker_vm":
        from evander.effects.providers.agent_session import container_backend

        result = container_backend(runtime, config).capture_git_salvage(attempt.session_id, request)
    else:
        from evander.effects.providers.agent_exec import AgentExecEffectProvider
        from evander.events.provider_models import AgentExecEffectDefinition

        kwargs = {
            "repository": matches[0]["target_dir"],
            "worktree": attempt.worktree_path,
            "expected_branch": request.expected_branch,
            "recovery_branch": request.recovery_branch,
            "destination": request.repo_source,
        }
        script = (
            inspect.getsource(capture_git_salvage)
            + "\nimport json\nprint(json.dumps(capture_git_salvage(**json.loads("
            + repr(json.dumps(kwargs))
            + "))))\n"
        )
        provider = AgentExecEffectProvider()
        provider.initialize(
            AgentExecEffectDefinition(
                id=f"salvage-{incident.id}",
                display_name="Preserve Git recovery evidence",
                type="effect:agent:exec",
                session_id=attempt.session_id,
                command="python3 -c " + shlex.quote(script),
                timeout_seconds=120,
                check=True,
            ),
            runtime,
        )
        output = provider.execute()
        result = json.loads(str(output.get("stdout") or "").strip())
    # Verify that code arrived in this repository before exposing any source to a
    # new task. A worker reply alone cannot establish durable Git transport.
    managed = adapter.managed_git_admin
    for ref_key, commit_key in (("work_ref", "commit_sha"), ("index_ref", "index_commit_sha")):
        if not str(result[ref_key]).startswith(f"refs/evander/{request.recovery_branch}/"):
            raise ValueError("salvage receipt escaped its authorized Git namespace")
        managed.resolve_snapshot(
            project.git.managed_repo_id,
            project.git.directory_path,
            source_ref=result[ref_key],
            commit_sha=result[commit_key],
        )
    return result
