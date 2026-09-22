"""Evander session migration commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.vm_to_container_migration import (
    build_session_migration_plan,
    copy_vm_session_state,
    create_migration_backup,
    cutover_sessions_to_container,
    default_local_worker_root,
    default_migration_backup_root,
    plan_payload,
    restore_selected_manifests_from_backup,
    select_sessions_for_migration,
    verify_worker_session_state,
)

from . import cli_app, cli_common, cli_runtime


@cli_app.app.command(
    "migrate-vm-sessions-to-container",
    rich_help_panel="Sessions and Feedback",
    short_help="Copy libvirt VM session state into the local container worker and cut over manifests.",
)
def migrate_vm_sessions_to_container(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    session_id: Annotated[
        list[str] | None,
        typer.Option("--session-id", help="Session id to migrate. May be repeated."),
    ] = None,
    all_sessions: Annotated[
        bool,
        typer.Option("--all", help="Select all libvirt-backed sessions."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", help="Preview selected sessions without copying or rewriting state."
        ),
    ] = False,
    copy_only: Annotated[
        bool,
        typer.Option("--copy-only", help="Copy VM state into worker session directories."),
    ] = False,
    cutover: Annotated[
        bool,
        typer.Option(
            "--cutover", help="Rewrite selected session manifests to container_worker_vm."
        ),
    ] = False,
    verify_only: Annotated[
        bool,
        typer.Option(
            "--verify-only", help="Verify copied worker state without copying or cutting over."
        ),
    ] = False,
    rollback: Annotated[
        Path | None,
        typer.Option(
            "--rollback", help="Restore selected manifests from a migration backup directory."
        ),
    ] = None,
    worker_root: Annotated[
        Path | None,
        typer.Option(
            "--worker-root", help="Container worker root. Defaults to local setup if present."
        ),
    ] = None,
    backup_root: Annotated[
        Path | None,
        typer.Option("--backup-root", help="Directory for manifest cutover backups."),
    ] = None,
    vm_user: Annotated[
        str,
        typer.Option("--vm-user", help="SSH user for copying /home/agent and /artifacts."),
    ] = "agent",
    log_vm_user: Annotated[
        str,
        typer.Option("--log-vm-user", help="SSH user for optional VM run-log copy."),
    ] = "agent-admin",
    include_logs: Annotated[
        bool,
        typer.Option("--include-logs/--skip-logs", help="Best-effort copy of VM agent run logs."),
    ] = True,
    final_delta: Annotated[
        bool,
        typer.Option("--final-delta", help="Run rsync with --delete during the state copy."),
    ] = False,
    skip_worker_session_check: Annotated[
        bool,
        typer.Option(
            "--skip-worker-session-check",
            help="Allow cutover without verifying copied worker session roots.",
        ),
    ] = False,
    ssh_timeout_seconds: Annotated[
        int,
        typer.Option("--ssh-timeout-seconds", min=1, help="Per-VM SSH discovery timeout."),
    ] = 180,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    if rollback is not None and (dry_run or copy_only or cutover or verify_only):
        raise typer.BadParameter("--rollback cannot be combined with migration phase options.")
    if final_delta and not copy_only:
        raise typer.BadParameter("--final-delta requires --copy-only.")
    if not any((dry_run, copy_only, cutover, verify_only, rollback is not None)):
        dry_run = True

    cfg = cli_runtime._load_runtime_config_from_option(config)
    repository = JsonSessionManifestRepository(cfg.session_store_path)
    selected_session_ids = [value for value in session_id or [] if str(value).strip()]

    if rollback is not None:
        restored = restore_selected_manifests_from_backup(
            repository,
            rollback.expanduser().resolve(),
            session_ids=selected_session_ids or None,
        )
        report = {
            "rollback": {
                "backup_path": str(rollback.expanduser().resolve()),
                "restored_session_ids": restored,
            }
        }
        if json_output:
            typer.echo(json.dumps(report, indent=2, sort_keys=True))
            return
        typer.echo(
            f"Restored {len(restored)} session manifest(s) from {rollback.expanduser().resolve()}."
        )
        for restored_session_id in restored:
            typer.echo(f"- {restored_session_id}")
        return

    sessions = {session.session_id: session for session in repository.list_sessions()}
    try:
        selected = select_sessions_for_migration(
            sessions,
            session_ids=selected_session_ids,
            all_sessions=all_sessions,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    resolved_worker_root = (
        worker_root.expanduser().resolve()
        if worker_root is not None
        else default_local_worker_root(cfg.runtime_dir)
    )
    resolved_backup_root = (
        backup_root.expanduser().resolve()
        if backup_root is not None
        else default_migration_backup_root(cfg.runtime_dir)
    )
    plans = [
        build_session_migration_plan(session, worker_root=resolved_worker_root)
        for session in selected
    ]
    report: dict[str, object] = {
        "worker_root": str(resolved_worker_root),
        "backup_root": str(resolved_backup_root),
        "plan": plan_payload(plans),
        "copy_results": [],
        "verifications": [],
        "cutover": None,
    }
    blocking_plans = [plan for plan in plans if not plan.eligible]
    if blocking_plans and (copy_only or cutover):
        if json_output:
            typer.echo(json.dumps(report, indent=2, sort_keys=True))
        blockers = "; ".join(
            f"{plan.session_id}: {', '.join(plan.blockers)}" for plan in blocking_plans
        )
        raise typer.BadParameter(f"selected sessions are not migratable: {blockers}")

    if dry_run:
        if json_output:
            typer.echo(json.dumps(report, indent=2, sort_keys=True))
            return
        _echo_vm_to_container_plan(plans, worker_root=resolved_worker_root)
        return

    if copy_only:
        copy_results = []

        def _progress(message: str) -> None:
            typer.echo(message, err=True)

        for session in selected:
            result = copy_vm_session_state(
                session,
                config=cfg,
                worker_root=resolved_worker_root,
                vm_user=vm_user,
                log_vm_user=log_vm_user,
                include_logs=include_logs,
                final_delta=final_delta,
                ssh_timeout_seconds=ssh_timeout_seconds,
                progress=_progress if not json_output else None,
            )
            copy_results.append(result.as_json())
        report["copy_results"] = copy_results
        failed_copies = [
            item for item in copy_results if isinstance(item, dict) and not item.get("ok")
        ]
        if failed_copies and cutover:
            if json_output:
                typer.echo(json.dumps(report, indent=2, sort_keys=True))
            raise typer.BadParameter(
                "state copy failed for at least one session; refusing cutover."
            )

    if verify_only or cutover or copy_only:
        verifications = [
            verify_worker_session_state(
                worker_root=resolved_worker_root,
                session_id=session.session_id,
            ).as_json()
            for session in selected
        ]
        report["verifications"] = verifications
        failed_verifications = [
            item for item in verifications if isinstance(item, dict) and not item.get("ok")
        ]
        if failed_verifications and cutover and not skip_worker_session_check:
            if json_output:
                typer.echo(json.dumps(report, indent=2, sort_keys=True))
            raise typer.BadParameter(
                "worker session verification failed; rerun copy or pass --skip-worker-session-check."
            )

    if cutover:
        backup = create_migration_backup(
            repository,
            selected,
            backup_root=resolved_backup_root,
        )
        migrated = cutover_sessions_to_container(
            repository,
            selected,
            backup_id=backup.backup_id,
        )
        report["cutover"] = {
            "backup": backup.as_json(),
            "migrated_session_ids": [session.session_id for session in migrated],
        }

    if json_output:
        typer.echo(json.dumps(report, indent=2, sort_keys=True))
        return
    _echo_vm_to_container_report(report)


def _echo_vm_to_container_plan(plans: list[object], *, worker_root: Path) -> None:
    typer.echo(f"Worker root: {worker_root}")
    if not plans:
        typer.echo("No sessions selected.")
        return
    for plan in plans:
        session_id = getattr(plan, "session_id", "")
        vm_name = getattr(plan, "source_vm_name", None) or "-"
        destination = getattr(plan, "destination", None)
        destination_text = str(getattr(destination, "agent_home", "-"))
        status = "eligible" if getattr(plan, "eligible", False) else "blocked"
        typer.echo(f"{session_id}\t{status}\tvm={vm_name}\tagent_home={destination_text}")
        blockers = getattr(plan, "blockers", []) or []
        warnings = getattr(plan, "warnings", []) or []
        for blocker in blockers:
            typer.echo(f"  blocker: {blocker}")
        for warning in warnings:
            typer.echo(f"  warning: {warning}")


def _echo_vm_to_container_report(report: dict[str, object]) -> None:
    copy_results = report.get("copy_results")
    if isinstance(copy_results, list) and copy_results:
        for item in copy_results:
            if not isinstance(item, dict):
                continue
            copied = ", ".join(str(label) for label in item.get("copied", []) or []) or "none"
            status = "ok" if item.get("ok") else "failed"
            typer.echo(f"copy {item.get('session_id')}: {status}; copied={copied}")
            for warning in item.get("warnings", []) or []:
                typer.echo(f"  warning: {warning}")
            for error in item.get("errors", []) or []:
                typer.echo(f"  error: {error}")
    verifications = report.get("verifications")
    if isinstance(verifications, list) and verifications:
        for item in verifications:
            if not isinstance(item, dict):
                continue
            status = "ok" if item.get("ok") else "failed"
            typer.echo(f"verify {item.get('session_id')}: {status}")
            for warning in item.get("warnings", []) or []:
                typer.echo(f"  warning: {warning}")
            for error in item.get("errors", []) or []:
                typer.echo(f"  error: {error}")
    cutover = report.get("cutover")
    if isinstance(cutover, dict):
        backup = cutover.get("backup")
        backup_path = backup.get("path") if isinstance(backup, dict) else None
        migrated = cutover.get("migrated_session_ids") or []
        typer.echo(f"Cut over {len(migrated)} session manifest(s). Backup: {backup_path}")
        for migrated_session_id in migrated:
            typer.echo(f"- {migrated_session_id}")
