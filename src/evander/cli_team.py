"""Evander team commands."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated

import typer

from . import cli_app, cli_common, cli_git, cli_runtime


@cli_app.team_app.command(
    "checkout-release-candidate",
    short_help="Materialize an exact Team release candidate for human testing.",
)
def team_checkout_release_candidate(
    release_package_id: str,
    target_dir: Annotated[
        Path,
        typer.Option(
            "--target",
            help="New local directory to create for the detached candidate checkout.",
        ),
    ],
    expected_commit_sha: Annotated[
        str,
        typer.Option(
            "--expected-commit-sha",
            help="Pinned candidate SHA shown by Review & Delivery; rejects changed metadata.",
        ),
    ],
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    from evander_team_addon.repository import TeamStateRepository

    runtime_dir = cli_runtime._runtime_dir_for_config_option(config)
    state = TeamStateRepository(runtime_dir).read()
    release = state.release_packages.get(str(release_package_id).strip())
    if release is None:
        raise typer.BadParameter(f"Unknown Team release package: {release_package_id}")
    project = state.projects.get(release.project_id)
    if project is None or project.git is None:
        raise typer.BadParameter(
            f"Release project {release.project_id} has no managed Git repository"
        )
    candidate_sha = str(release.candidate_commit_sha or "").strip().lower()
    candidate_tree = str(release.candidate_tree_hash or "").strip().lower()
    release_branch = str(release.release_branch or "").strip()
    if not candidate_sha or not candidate_tree or not release_branch:
        raise typer.BadParameter(
            "Release does not have a complete pinned candidate SHA, tree, and release ref"
        )
    if str(expected_commit_sha or "").strip().lower() != candidate_sha:
        raise typer.BadParameter(
            "Expected candidate SHA does not match the pinned release candidate"
        )
    source_ref = (
        release_branch if release_branch.startswith("refs/") else f"refs/heads/{release_branch}"
    )
    managed_git = cli_git._build_managed_git_admin_for_cli(config)
    result = cli_git._managed_git_call(
        managed_git.materialize_candidate_checkout,
        project.git.managed_repo_id,
        project.git.directory_path,
        source_ref=source_ref,
        commit_sha=candidate_sha,
        tree_hash=candidate_tree,
        target_dir=target_dir,
        purpose=f"operator-release-review:{release.id}",
    )
    assert isinstance(result, dict)
    cli_git._echo_managed_git_json(
        {
            **result,
            "release_package_id": release.id,
        }
    )


@cli_app.team_app.command(
    "migrate-historical-lifecycle",
    short_help="Dry-run or apply one audited Team historical-lifecycle migration.",
)
def team_migrate_historical_lifecycle(
    project_id: Annotated[
        str,
        typer.Argument(
            help="Team project whose invalid historical lifecycle records to reconcile."
        ),
    ],
    actor: Annotated[
        str,
        typer.Option("--actor", help="Operator identity recorded on every migration audit record."),
    ],
    reason: Annotated[
        str,
        typer.Option("--reason", help="Operator-authorized reason retained with every repair."),
    ],
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Apply the revision-checked migration; omit for a dry run."),
    ] = False,
    reference_ids: Annotated[
        list[str],
        typer.Option(
            "--reference-id", help="Repeatable directive, incident, or approval reference."
        ),
    ] = [],
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    """Repair only known lossless terminal lifecycle defects in one project."""

    from evander_team_addon.repository import TeamStateRepository
    from evander_team_addon.services.admin_service import TeamAdminService

    runtime_dir = cli_runtime._runtime_dir_for_config_option(config)
    service = TeamAdminService(TeamStateRepository(runtime_dir))
    state, plan = service.plan_historical_lifecycle_migration(project_id=project_id)
    payload: dict[str, object] = {
        "dry_run": not apply,
        "revision": state.revision,
        **plan.payload(),
    }
    if apply:
        state, result = service.migrate_historical_task_lifecycle(
            project_id=project_id,
            actor=actor,
            reason=reason,
            reference_ids=reference_ids,
            expected_revision=state.revision,
        )
        payload.update(
            {
                "dry_run": False,
                "revision": state.revision,
                "result": result,
            }
        )
    typer.echo(json.dumps(payload, indent=2, sort_keys=True, default=str))


@cli_app.team_app.command("dispatch-tail")
def team_dispatch_tail(
    dispatch_id: Annotated[
        str | None,
        typer.Option("--dispatch-id", help="Filter to one Team dispatch id."),
    ] = None,
    agent_id: Annotated[
        str | None,
        typer.Option("--agent-id", help="Filter to one persistent agent id."),
    ] = None,
    tool_name: Annotated[
        str | None,
        typer.Option("--tool-name", help="Filter to one Team tool name."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=500, help="Maximum initial events."),
    ] = 100,
    follow: Annotated[
        bool,
        typer.Option("--follow/--no-follow", help="Continue polling for new events."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one JSON object per line."),
    ] = False,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    """Tail redacted, structured Team dispatch and tool activity."""

    from evander_team_addon.repository import TeamStateRepository

    runtime_dir = cli_runtime._runtime_dir_for_config_option(config)
    repository = TeamStateRepository(runtime_dir)
    seen: set[str] = set()
    first = True
    try:
        while True:
            rows = repository.list_dispatch_activity(
                dispatch_id=dispatch_id,
                agent_id=agent_id,
                tool_name=tool_name,
                limit=limit,
                newest_first=True,
            )
            pending = [row for row in reversed(rows) if row.id not in seen]
            for row in pending:
                payload = row.model_dump(mode="json")
                if json_output:
                    typer.echo(json.dumps(payload, separators=(",", ":"), default=str))
                else:
                    occurred_at = payload.get("created_at") or ""
                    actor = payload.get("agent_id") or payload.get("caller_identity")
                    status = payload.get("status")
                    dispatch = payload.get("dispatch_id") or "-"
                    detail = payload.get("error_message") or payload.get(
                        "correction_context_summary"
                    )
                    line = f"{occurred_at} {status} {actor} {dispatch} {payload.get('tool_name')}"
                    typer.echo(f"{line} - {detail}" if detail else line)
                seen.add(row.id)
            if not follow:
                return
            if first:
                first = False
            time.sleep(1.0)
    except KeyboardInterrupt:
        return
