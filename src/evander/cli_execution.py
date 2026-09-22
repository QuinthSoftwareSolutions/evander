"""Evander execution commands."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Annotated

import typer

from evander.config import GatewayConfig
from evander.execution import (
    ContainerWorkerExecutionRepository,
    ContainerWorkerVmExecutionBackend,
    ExecutionBackendKind,
    ExecutionSpec,
    ExecutionStatusKind,
    build_execution_backend,
)
from evander.provider_runtime.adapters import AdminRuntimeProviderAdapter

from . import cli_app, cli_common, cli_runtime


def _build_execution_backend_from_option(config: Path):
    cfg = cli_runtime._load_runtime_config_from_option(config)
    return build_execution_backend(
        cfg,
        admin_service=AdminRuntimeProviderAdapter(cli_runtime.build_admin_runtime(config))
        if str(cfg.execution_backend) == "libvirt_vm"
        else None,
        project_dir=cli_runtime._project_dir_for_config_option(config),
    )


def _container_worker_repository(config: GatewayConfig) -> ContainerWorkerExecutionRepository:
    return ContainerWorkerExecutionRepository(config.runtime_dir / "container-worker-executions.db")


def _container_worker_target_exists(config: GatewayConfig, target: str) -> bool:
    try:
        _container_worker_repository(config).resolve(target)
    except ValueError as exc:
        return str(exc).startswith("Ambiguous container worker session target")
    return True


def _preflight_container_worker_config(config: GatewayConfig) -> None:
    if not config.container_worker_base_url:
        raise typer.BadParameter("container_worker_base_url is required for container_worker_vm.")
    if not os.environ.get(config.container_worker_token_env, ""):
        raise typer.BadParameter(
            f"container worker bearer token env var {config.container_worker_token_env} is not set."
        )


def _build_execution_backend_for_kind(
    config_path: Path,
    *,
    backend: ExecutionBackendKind | None = None,
    target: str | None = None,
    default_container: bool = False,
    require_container_token: bool = True,
    directory_path: str | None = None,
    session_id: str | None = None,
    worker_id: str | None = None,
):
    cfg = cli_runtime._load_runtime_config_from_option(config_path)
    if backend is not None:
        selected = backend
    elif default_container:
        selected = ExecutionBackendKind.CONTAINER_WORKER_VM
    elif target and _container_worker_target_exists(cfg, target):
        selected = ExecutionBackendKind.CONTAINER_WORKER_VM
    else:
        selected = ExecutionBackendKind(str(cfg.execution_backend))

    if selected == ExecutionBackendKind.CONTAINER_WORKER_VM:
        from evander.admin.services.container_worker_assignment_service import (
            ContainerWorkerAssignmentService,
        )
        from evander.provider_config import ProviderConfigResolver

        if target:
            record = _container_worker_repository(cfg).resolve(target)
            cfg = ProviderConfigResolver(cfg).resolve_for_directory(record.directory_path)
            cfg = ContainerWorkerAssignmentService(cfg).resolve(
                record.session_id,
                worker_url=record.worker_url,
                directory_path=record.directory_path,
            )
        else:
            cfg = ProviderConfigResolver(cfg).resolve_for_directory(directory_path or "root")
            if session_id:
                cfg = ContainerWorkerAssignmentService(cfg).resolve(
                    session_id, directory_path=directory_path, worker_id=worker_id
                )
        if require_container_token:
            _preflight_container_worker_config(cfg)
        elif not cfg.container_worker_base_url:
            raise typer.BadParameter(
                "container_worker_base_url is required for container_worker_vm."
            )
        container_cfg = cfg.model_copy(
            update={"execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value}
        )
        return ContainerWorkerVmExecutionBackend(config=container_cfg)

    libvirt_cfg = cfg.model_copy(
        update={"execution_backend": ExecutionBackendKind.LIBVIRT_VM.value}
    )
    return build_execution_backend(
        libvirt_cfg,
        admin_service=AdminRuntimeProviderAdapter(cli_runtime.build_admin_runtime(config_path)),
        project_dir=cli_runtime._project_dir_for_config_option(config_path),
    )


BackendOption = Annotated[
    ExecutionBackendKind | None,
    typer.Option(
        "--backend", help="Force an execution backend instead of auto-detecting container mappings."
    ),
]


def _parse_metadata_pairs(pairs: list[str] | None) -> dict[str, str]:
    metadata: dict[str, str] = {}
    forbidden = {"env", "environment", "environment_variables"}
    for raw in pairs or []:
        if "=" not in raw:
            raise typer.BadParameter("--metadata values must use key=value format.")
        key, value = raw.split("=", 1)
        normalized_key = key.strip()
        if not normalized_key:
            raise typer.BadParameter("--metadata keys must not be empty.")
        if normalized_key.casefold() in forbidden:
            raise typer.BadParameter(f"--metadata key {normalized_key} is not allowed.")
        metadata[normalized_key] = value
    return metadata


def _resource_limits(
    memory: str | None, cpus: float | None, pids_limit: int | None
) -> dict[str, object] | None:
    limits: dict[str, object] = {}
    if memory:
        limits["memory"] = memory
    if cpus is not None:
        limits["cpus"] = cpus
    if pids_limit is not None:
        limits["pids_limit"] = pids_limit
    return limits or None


def _container_mapping_payload(record) -> dict[str, object]:
    return {
        "execution_id": record.execution_id,
        "session_id": record.session_id,
        "worker_job_id": record.worker_job_id,
        "target_name": record.target_name,
        "worker_url": record.worker_url,
        "last_status": record.last_status,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
    }


@cli_app.execution_app.command("pin-worker-assignments")
def execution_pin_worker_assignments(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    apply: Annotated[
        bool, typer.Option("--apply", help="Persist assignments; defaults to preview.")
    ] = False,
    directory_path: Annotated[
        str | None,
        typer.Option("--directory-path", help="Restrict to this directory and descendants."),
    ] = None,
) -> None:
    """Pin legacy agents to their current worker before changing worker defaults."""
    from evander.admin.services.container_worker_assignment_service import (
        ContainerWorkerAssignmentService,
    )

    cfg = cli_runtime._load_runtime_config_from_option(config)
    try:
        rows = ContainerWorkerAssignmentService(cfg).pin_existing_sessions(
            directory_path=directory_path, persist=apply
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps({"applied": apply, "count": len(rows), "assignments": rows}, indent=2))


@cli_app.execution_app.command("start-container-job")
def execution_start_container_job(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    session_id: Annotated[
        str | None, typer.Option("--session-id", help="Session id for worker persistence.")
    ] = None,
    worker_id: Annotated[
        str | None,
        typer.Option(
            "--worker-id", help="Configured named worker; defaults to directory selection."
        ),
    ] = None,
    command: Annotated[
        str, typer.Option("--command", help="Command to run inside the worker container.")
    ] = "",
    target_name: Annotated[
        str | None, typer.Option("--target-name", help="Optional stable execution id/target name.")
    ] = None,
    directory_path: Annotated[
        str,
        typer.Option(
            "--directory-path", "--directory", help="Directory path owning the execution."
        ),
    ] = "root",
    image: Annotated[str | None, typer.Option("--image", help="Container image override.")] = None,
    repo_source: Annotated[
        str | None, typer.Option("--repo-source", help="Explicit repository source to clone.")
    ] = None,
    clone_branch: Annotated[
        str | None, typer.Option("--clone-branch", help="Explicit repository branch to clone.")
    ] = None,
    metadata: Annotated[
        list[str] | None,
        typer.Option("--metadata", help="Sanitized metadata as key=value. May be repeated."),
    ] = None,
    memory: Annotated[
        str | None, typer.Option("--memory", help="Container memory limit, e.g. 512m.")
    ] = None,
    cpus: Annotated[
        float | None, typer.Option("--cpus", min=0.01, help="Container CPU limit.")
    ] = None,
    pids_limit: Annotated[
        int | None, typer.Option("--pids-limit", min=1, help="Container PID limit.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    if not command.strip():
        raise typer.BadParameter("--command is required")
    if worker_id and not session_id:
        session_id = target_name or f"session-{uuid.uuid4().hex}"
    try:
        backend = _build_execution_backend_for_kind(
            config,
            default_container=True,
            directory_path=directory_path,
            session_id=session_id,
            worker_id=worker_id,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    prepared = backend.prepare(
        ExecutionSpec(
            backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
            target_name=target_name,
            session_id=session_id,
            directory_path=directory_path,
            command=command,
            image=image,
            repo_source=repo_source,
            clone_branch=clone_branch,
            resource_limits=_resource_limits(memory, cpus, pids_limit),
            metadata=_parse_metadata_pairs(metadata),
        )
    )
    handle = backend.start(prepared)
    if json_output:
        cli_common._echo_model_json(handle)
        return
    typer.echo(
        f"Started container worker execution {handle.execution_id} "
        f"for session {handle.session_id or '<none>'}."
    )


@cli_app.execution_app.command("status")
def execution_status(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    target: str = typer.Argument(...),
    backend: BackendOption = None,
) -> None:
    backend = _build_execution_backend_for_kind(config, backend=backend, target=target)
    cli_common._echo_model_json(backend.inspect(target))


@cli_app.execution_app.command("logs")
def execution_logs(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    target: str = typer.Argument(...),
    lines: Annotated[
        int, typer.Option("--lines", min=1, help="Number of trailing lines to print.")
    ] = 50,
    backend: BackendOption = None,
) -> None:
    backend = _build_execution_backend_for_kind(config, backend=backend, target=target)
    typer.echo(backend.get_log_snapshot(target, lines=lines), nl=False)


@cli_app.execution_app.command("stop")
def execution_stop(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    target: str = typer.Argument(...),
    timeout_seconds: Annotated[int, typer.Option("--timeout-seconds", min=1)] = 60,
    backend: BackendOption = None,
) -> None:
    backend = _build_execution_backend_for_kind(config, backend=backend, target=target)
    cli_common._echo_model_json(backend.stop(target, timeout_seconds=timeout_seconds))


@cli_app.execution_app.command("destroy")
def execution_destroy(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    target: str = typer.Argument(...),
    backend: BackendOption = None,
) -> None:
    backend = _build_execution_backend_for_kind(config, backend=backend, target=target)
    cli_common._echo_model_json(backend.destroy(target))


@cli_app.execution_app.command("list-container-worker-executions")
def execution_list_container_worker_executions(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    session_id: Annotated[
        str | None, typer.Option("--session-id", help="Filter by session id.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    cfg = cli_runtime._load_runtime_config_from_option(config)
    repository = _container_worker_repository(cfg)
    if session_id:
        list_by_session = getattr(repository, "list_by_session", None)
        if callable(list_by_session):
            records = list_by_session(session_id)
        else:
            list_all_ordered = getattr(repository, "list_all_ordered", None)
            if callable(list_all_ordered):
                records = [
                    record for record in list_all_ordered() if record.session_id == session_id
                ]
            else:
                records = [
                    record
                    for record in sorted(
                        repository.read_all().values(),
                        key=lambda record: (record.created_at, record.execution_id),
                    )
                    if record.session_id == session_id
                ]
    else:
        list_all_ordered = getattr(repository, "list_all_ordered", None)
        if callable(list_all_ordered):
            records = list_all_ordered()
        else:
            records = sorted(
                repository.read_all().values(),
                key=lambda record: (record.created_at, record.execution_id),
            )
    payload = [_container_mapping_payload(record) for record in records]
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    if not payload:
        typer.echo("No container worker executions found.")
        return
    rows = [
        (
            item["execution_id"],
            item["session_id"],
            item["worker_job_id"],
            item["target_name"],
            item["last_status"],
            item["worker_url"],
        )
        for item in payload
    ]
    header = ("EXECUTION", "SESSION", "WORKER_JOB", "TARGET", "STATUS", "WORKER_URL")
    widths = [max(len(str(row[index])) for row in [header, *rows]) for index in range(6)]
    typer.echo("  ".join(value.ljust(widths[index]) for index, value in enumerate(header)))
    for row in rows:
        typer.echo("  ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)))


def _poll_container_execution(
    backend, execution_id: str, *, timeout_seconds: float, poll_interval_seconds: float
):
    deadline = time.monotonic() + timeout_seconds
    status = backend.inspect(execution_id)
    terminal = {
        ExecutionStatusKind.STOPPED,
        ExecutionStatusKind.FAILED,
        ExecutionStatusKind.DESTROYED,
    }
    while status.status not in terminal and time.monotonic() < deadline:
        time.sleep(poll_interval_seconds)
        status = backend.inspect(execution_id)
    return status


@cli_app.execution_app.command("smoke-container-worker")
def execution_smoke_container_worker(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    session_id: Annotated[
        str, typer.Option("--session-id", help="Session id to reuse across both smoke jobs.")
    ] = "smoke-session",
    image: Annotated[
        str, typer.Option("--image", help="Container image to run.")
    ] = "evander-runner:latest",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
    timeout_seconds: Annotated[
        int, typer.Option("--timeout-seconds", min=1, help="Per-job timeout.")
    ] = 120,
    poll_interval_seconds: Annotated[
        float,
        typer.Option(
            "--poll-interval-seconds",
            min=0.1,
            help="Polling interval while waiting for smoke jobs.",
        ),
    ] = 2.0,
) -> None:
    backend = _build_execution_backend_for_kind(config, default_container=True)
    run_id = uuid.uuid4().hex[:12]
    agent_marker = f"agent-home-{run_id}"
    repo_root_marker = f"repo-root-{run_id}"
    run1_target = f"phase4-smoke-{run_id}-1"
    run2_target = f"phase4-smoke-{run_id}-2"
    run1_command = (
        "set -euo pipefail; "
        f"printf '%s\\n' {agent_marker!r} > /home/agent/phase4-agent-home-marker.txt; "
        "mkdir -p /home/agent/repos; "
        f"printf '%s\\n' {repo_root_marker!r} > /home/agent/repos/phase4-repo-root-marker.txt; "
        "cat /home/agent/phase4-agent-home-marker.txt; "
        "cat /home/agent/repos/phase4-repo-root-marker.txt"
    )
    run2_command = (
        "set -euo pipefail; "
        "agent_marker=$(cat /home/agent/phase4-agent-home-marker.txt); "
        "repo_root_marker=$(cat /home/agent/repos/phase4-repo-root-marker.txt); "
        f'test "$agent_marker" = {agent_marker!r}; '
        f'test "$repo_root_marker" = {repo_root_marker!r}; '
        'printf \'agent_marker=%s\\nrepo_root_marker=%s\\n\' "$agent_marker" "$repo_root_marker"'
    )

    run1 = backend.start(
        backend.prepare(
            ExecutionSpec(
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                target_name=run1_target,
                session_id=session_id,
                command=run1_command,
                image=image,
                metadata={"smoke": "phase4", "run": "1"},
            )
        )
    )
    run1_status = _poll_container_execution(
        backend,
        run1.execution_id,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    _run1_logs = backend.get_log_snapshot(run1.execution_id, lines=200)
    if run1_status.status != ExecutionStatusKind.STOPPED:
        raise typer.BadParameter(
            f"smoke run 1 did not complete successfully: {run1_status.status.value}"
        )

    run2 = backend.start(
        backend.prepare(
            ExecutionSpec(
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                target_name=run2_target,
                session_id=session_id,
                command=run2_command,
                image=image,
                metadata={"smoke": "phase4", "run": "2"},
            )
        )
    )
    run2_status = _poll_container_execution(
        backend,
        run2.execution_id,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    run2_logs = backend.get_log_snapshot(run2.execution_id, lines=200)
    if run2_status.status != ExecutionStatusKind.STOPPED:
        raise typer.BadParameter(
            f"smoke run 2 did not complete successfully: {run2_status.status.value}"
        )
    if agent_marker not in run2_logs or repo_root_marker not in run2_logs:
        raise typer.BadParameter("smoke run 2 logs did not prove both marker reads.")

    payload = {
        "success": True,
        "session_id": session_id,
        "run1": run1_status.model_dump(mode="json"),
        "run2": run2_status.model_dump(mode="json"),
        "run2_logs": run2_logs,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"Container worker smoke succeeded for session {session_id}.")
    typer.echo(run2_logs, nl=False)
