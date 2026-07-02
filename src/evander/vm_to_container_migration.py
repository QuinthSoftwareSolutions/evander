from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evander.admin.services.vm_helpers import (
    resolve_vm_ssh_access as _resolve_vm_ssh_access,
    ssh_options as _ssh_options,
)
from evander.config import GatewayConfig
from evander.effects.providers.agent_session import build_container_session_files
from evander.execution.models import ExecutionBackendKind
from evander.models import SessionManifest
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.session_auth import session_mcp_auth_token
from evander.worker.models import DEFAULT_WORKER_ROOT, SAFE_ID_PATTERN

CONTAINER_BACKEND = ExecutionBackendKind.CONTAINER_WORKER_VM.value
LIBVIRT_BACKEND = ExecutionBackendKind.LIBVIRT_VM.value
MIGRATION_BACKUP_DIRNAME = "vm-to-container"


@dataclass(slots=True)
class WorkerSessionPathPlan:
    session_id: str
    root: Path
    agent_home: Path
    repo_root: Path
    artifacts: Path
    logs: Path
    metadata_path: Path

    def as_json(self) -> dict[str, str]:
        return {
            "session_id": self.session_id,
            "root": str(self.root),
            "agent_home": str(self.agent_home),
            "repo_root": str(self.repo_root),
            "artifacts": str(self.artifacts),
            "logs": str(self.logs),
            "metadata_path": str(self.metadata_path),
        }


@dataclass(slots=True)
class SessionMigrationPlan:
    session_id: str
    source_vm_name: str | None
    current_backend: str
    destination: WorkerSessionPathPlan
    eligible: bool
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "source_vm_name": self.source_vm_name,
            "current_backend": self.current_backend,
            "destination": self.destination.as_json(),
            "eligible": self.eligible,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class WorkerSessionVerification:
    session_id: str
    worker_root: Path
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    codex_session_roots: list[Path] = field(default_factory=list)

    def as_json(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "worker_root": str(self.worker_root),
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "codex_session_roots": [str(path) for path in self.codex_session_roots],
        }


@dataclass(slots=True)
class MigrationBackup:
    backup_id: str
    path: Path
    selected_session_ids: list[str]

    def as_json(self) -> dict[str, object]:
        return {
            "backup_id": self.backup_id,
            "path": str(self.path),
            "selected_session_ids": list(self.selected_session_ids),
        }


@dataclass(slots=True)
class SessionCopyResult:
    session_id: str
    vm_name: str
    worker_root: Path
    copied: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_json(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "vm_name": self.vm_name,
            "worker_root": str(self.worker_root),
            "copied": list(self.copied),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "ok": self.ok,
        }


def default_local_worker_root(runtime_dir: Path) -> Path:
    local_root = runtime_dir / "local-container-worker" / "worker-root"
    if local_root.exists():
        return local_root
    return DEFAULT_WORKER_ROOT


def default_migration_backup_root(runtime_dir: Path) -> Path:
    return runtime_dir / "migration-backups" / MIGRATION_BACKUP_DIRNAME


def resolve_worker_session_paths(worker_root: Path, session_id: str) -> WorkerSessionPathPlan:
    if not SAFE_ID_PATTERN.fullmatch(session_id):
        raise ValueError("session_id must contain only letters, numbers, dots, underscores, or dashes")
    root = Path(worker_root) / "sessions" / session_id
    agent_home = root / "agent_home"
    return WorkerSessionPathPlan(
        session_id=session_id,
        root=root,
        agent_home=agent_home,
        repo_root=agent_home / "repos",
        artifacts=root / "artifacts",
        logs=root / "logs",
        metadata_path=root / "metadata.json",
    )


def ensure_worker_session_paths(worker_root: Path, session_id: str) -> WorkerSessionPathPlan:
    paths = resolve_worker_session_paths(worker_root, session_id)
    for path in (paths.agent_home, paths.repo_root, paths.artifacts, paths.logs):
        path.mkdir(parents=True, exist_ok=True)
    if not paths.metadata_path.exists():
        paths.metadata_path.write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "created_at": datetime.now(UTC).isoformat(),
                    "agent_home": str(paths.agent_home),
                    "repo_root": str(paths.repo_root),
                    "artifacts": str(paths.artifacts),
                    "logs": str(paths.logs),
                    "migration_created": True,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    return paths


def session_current_backend(session: SessionManifest) -> str:
    provider = _optional_string(session.vm_provider)
    metadata_provider = _optional_string(
        session.metadata.get("execution_backend")
        or session.metadata.get("vm_provider")
        if isinstance(session.metadata, Mapping)
        else None
    )
    selected = provider or metadata_provider
    if selected == CONTAINER_BACKEND:
        return CONTAINER_BACKEND
    if selected in {LIBVIRT_BACKEND, "libvirt", "azure"}:
        return LIBVIRT_BACKEND
    if selected:
        return selected
    if _optional_string(session.vm_id):
        return LIBVIRT_BACKEND
    return "unknown"


def source_vm_name_for_session(session: SessionManifest) -> str | None:
    if _optional_string(session.vm_id):
        return _optional_string(session.vm_id)
    if session_current_backend(session) != CONTAINER_BACKEND and _optional_string(
        session.vm_provider_id
    ):
        return _optional_string(session.vm_provider_id)
    metadata = session.metadata if isinstance(session.metadata, Mapping) else {}
    for key in ("vm_name", "libvirt_vm_name", "target_name"):
        value = _optional_string(metadata.get(key))
        if value:
            return value
    return None


def is_libvirt_backed_session(session: SessionManifest) -> bool:
    if session_current_backend(session) == CONTAINER_BACKEND:
        return False
    return session_current_backend(session) == LIBVIRT_BACKEND or bool(
        source_vm_name_for_session(session)
    )


def build_session_migration_plan(
    session: SessionManifest, *, worker_root: Path
) -> SessionMigrationPlan:
    backend = session_current_backend(session)
    vm_name = source_vm_name_for_session(session)
    destination = resolve_worker_session_paths(worker_root, session.session_id)
    blockers: list[str] = []
    warnings: list[str] = []
    if backend == CONTAINER_BACKEND:
        blockers.append("session is already container_worker_vm-backed")
    elif not is_libvirt_backed_session(session):
        blockers.append(f"session backend is not recognized as libvirt-backed: {backend}")
    if vm_name is None:
        blockers.append("session has no source VM name")
    if not session.allowed_tools:
        warnings.append("session has no allowed tools")
    return SessionMigrationPlan(
        session_id=session.session_id,
        source_vm_name=vm_name,
        current_backend=backend,
        destination=destination,
        eligible=not blockers,
        blockers=blockers,
        warnings=warnings,
    )


def build_container_migrated_manifest(
    session: SessionManifest,
    *,
    migrated_at: datetime,
    backup_id: str,
) -> SessionManifest:
    metadata = dict(session.metadata or {})
    previous = {
        "vm_id": session.vm_id,
        "vm_provider": session.vm_provider,
        "vm_provider_id": session.vm_provider_id,
        "execution_backend": metadata.get("execution_backend"),
        "vm_provider_metadata": metadata.get("vm_provider"),
        "target_name": metadata.get("target_name"),
    }
    existing_previous = metadata.get("previous_libvirt")
    if isinstance(existing_previous, Mapping):
        previous = {**dict(existing_previous), **previous}
    metadata["previous_libvirt"] = previous
    metadata["execution_backend"] = CONTAINER_BACKEND
    metadata["target_name"] = session.session_id
    metadata["migrated_from_libvirt_at"] = migrated_at.isoformat()
    metadata["migration_backup_id"] = backup_id
    return session.model_copy(
        update={
            "vm_id": None,
            "vm_provider": CONTAINER_BACKEND,
            "vm_provider_id": session.session_id,
            "metadata": metadata,
        }
    )


def create_migration_backup(
    repository: JsonSessionManifestRepository,
    sessions: Sequence[SessionManifest],
    *,
    backup_root: Path,
    created_at: datetime | None = None,
) -> MigrationBackup:
    timestamp = (created_at or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    backup_id = f"{timestamp}-vm-to-container"
    backup_path = backup_root / backup_id
    backup_path.mkdir(parents=True, exist_ok=False)
    all_sessions = repository.read_all_safe()
    selected = {session.session_id: all_sessions[session.session_id] for session in sessions}
    _write_json(
        backup_path / "sessions.json",
        {session_id: manifest.model_dump(mode="json") for session_id, manifest in all_sessions.items()},
    )
    _write_json(
        backup_path / "selected-sessions.json",
        {session_id: manifest.model_dump(mode="json") for session_id, manifest in selected.items()},
    )
    _write_json(
        backup_path / "summary.json",
        {
            "backup_id": backup_id,
            "created_at": (created_at or datetime.now(UTC)).isoformat(),
            "selected_session_ids": sorted(selected),
            "session_store_path": str(repository.path),
        },
    )
    return MigrationBackup(
        backup_id=backup_id,
        path=backup_path,
        selected_session_ids=sorted(selected),
    )


def restore_selected_manifests_from_backup(
    repository: JsonSessionManifestRepository,
    backup_path: Path,
    *,
    session_ids: Sequence[str] | None = None,
) -> list[str]:
    selected_path = backup_path / "selected-sessions.json"
    payload = json.loads(selected_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{selected_path} must contain an object")
    requested = set(session_ids or payload.keys())
    restored: list[str] = []
    for session_id in sorted(requested):
        raw = payload.get(session_id)
        if raw is None:
            raise ValueError(f"backup does not contain selected session {session_id!r}")
        manifest = SessionManifest.model_validate(raw)
        repository.upsert_session(manifest)
        restored.append(session_id)
    return restored


def verify_worker_session_state(
    *, worker_root: Path, session_id: str
) -> WorkerSessionVerification:
    paths = resolve_worker_session_paths(worker_root, session_id)
    errors: list[str] = []
    warnings: list[str] = []
    if not paths.root.exists():
        errors.append(f"worker session root is missing: {paths.root}")
    if not paths.agent_home.is_dir():
        errors.append(f"worker agent_home is missing: {paths.agent_home}")
    if not paths.metadata_path.is_file():
        errors.append(f"worker metadata is missing: {paths.metadata_path}")
    if not paths.artifacts.is_dir():
        warnings.append(f"worker artifacts directory is missing: {paths.artifacts}")
    codex_roots = [
        paths.agent_home / ".config" / "evander" / "codex" / "sessions",
        paths.agent_home / ".codex" / "sessions",
    ]
    present_codex_roots = [path for path in codex_roots if path.is_dir()]
    if not present_codex_roots:
        warnings.append("no Codex session directory found under .config/evander/codex or .codex")
    elif not any(_has_any_file(path) for path in present_codex_roots):
        warnings.append("Codex session directories exist but contain no files")
    return WorkerSessionVerification(
        session_id=session_id,
        worker_root=worker_root,
        ok=not errors,
        errors=errors,
        warnings=warnings,
        codex_session_roots=present_codex_roots,
    )


def copy_vm_session_state(
    session: SessionManifest,
    *,
    config: GatewayConfig,
    worker_root: Path,
    vm_user: str = "agent",
    log_vm_user: str = "agent-admin",
    include_logs: bool = True,
    final_delta: bool = False,
    ssh_timeout_seconds: int = 180,
    chown_agent_uid_gid: tuple[int, int] | None = (1000, 1000),
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    progress: Callable[[str], None] | None = None,
) -> SessionCopyResult:
    vm_name = source_vm_name_for_session(session)
    if vm_name is None:
        return SessionCopyResult(
            session_id=session.session_id,
            vm_name="",
            worker_root=worker_root,
            errors=["session has no source VM name"],
        )
    paths = ensure_worker_session_paths(worker_root, session.session_id)
    result = SessionCopyResult(
        session_id=session.session_id,
        vm_name=vm_name,
        worker_root=worker_root,
    )
    try:
        vm_ip, key_path = _resolve_vm_ssh_access(vm_name, vm_user, timeout_seconds=ssh_timeout_seconds)
    except Exception as exc:
        result.errors.append(f"could not resolve SSH access for {vm_name}: {exc}")
        return result
    ssh_options = _ssh_options(vm_name, vm_ip, key_path)
    ssh_command = "ssh " + shlex.join(ssh_options)

    _copy_remote_tree(
        label="agent_home",
        user=vm_user,
        vm_ip=vm_ip,
        remote_path="/home/agent",
        local_path=paths.agent_home,
        ssh_command=ssh_command,
        delete=final_delta,
        result=result,
        run=run,
        progress=progress,
        required=True,
    )
    _copy_remote_tree(
        label="artifacts",
        user=vm_user,
        vm_ip=vm_ip,
        remote_path="/artifacts",
        local_path=paths.artifacts,
        ssh_command=ssh_command,
        delete=final_delta,
        result=result,
        run=run,
        progress=progress,
        required=False,
        probe_first=True,
    )
    if include_logs:
        try:
            log_ip, log_key_path = _resolve_vm_ssh_access(
                vm_name, log_vm_user, timeout_seconds=ssh_timeout_seconds
            )
            log_ssh_options = _ssh_options(vm_name, log_ip, log_key_path)
            log_ssh_command = "ssh " + shlex.join(log_ssh_options)
            _copy_remote_tree(
                label="agent_run_logs",
                user=log_vm_user,
                vm_ip=log_ip,
                remote_path="/var/log/evander/agent-runs",
                local_path=paths.logs / "migrated-vm-agent-runs",
                ssh_command=log_ssh_command,
                delete=final_delta,
                result=result,
                run=run,
                progress=progress,
                required=False,
                probe_first=True,
            )
        except Exception as exc:
            result.warnings.append(f"could not copy VM run logs for {vm_name}: {exc}")

    if result.ok:
        result.warnings.extend(reconcile_codex_state(paths.agent_home))
        result.warnings.extend(
            refresh_container_session_files(
                config=config,
                session=session,
                agent_home=paths.agent_home,
            )
        )
        if chown_agent_uid_gid is not None:
            uid, gid = chown_agent_uid_gid
            result.warnings.extend(_best_effort_chown(paths.root, uid=uid, gid=gid, run=run))
    return result


def reconcile_codex_state(agent_home: Path) -> list[str]:
    warnings: list[str] = []
    legacy_sessions = agent_home / ".codex" / "sessions"
    active_sessions = agent_home / ".config" / "evander" / "codex" / "sessions"
    if legacy_sessions.is_dir():
        warnings.extend(_merge_tree_without_overwrite(legacy_sessions, active_sessions))
    if active_sessions.is_dir():
        warnings.extend(_merge_tree_without_overwrite(active_sessions, legacy_sessions))
    return warnings


def refresh_container_session_files(
    *,
    config: GatewayConfig,
    session: SessionManifest,
    agent_home: Path,
    dotnet: bool = True,
    nuget: bool = True,
    playwright: bool = True,
) -> list[str]:
    warnings: list[str] = []
    files = build_container_session_files(
        config=config,
        session_id=session.session_id,
        model=session.model,
        agent_provider=_optional_string(session.metadata.get("agent_provider")),
        web_search_enabled=session.web_search_enabled,
        dotnet=dotnet,
        nuget=nuget,
        playwright=playwright,
        mcp_session_token=session_mcp_auth_token(session),
    )
    for record in files:
        destination = _container_destination_to_agent_home(
            str(record["destination_path"]), agent_home=agent_home
        )
        if destination is None:
            warnings.append(f"skipped unsupported container file destination {record['destination_path']}")
            continue
        content = str(record.get("content") or "")
        mode = int(str(record.get("mode") or "0644"), 8)
        _preserve_existing_file(destination, agent_home=agent_home, warnings=warnings)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        destination.chmod(mode)
    return warnings


def cutover_sessions_to_container(
    repository: JsonSessionManifestRepository,
    sessions: Sequence[SessionManifest],
    *,
    backup_id: str,
    migrated_at: datetime | None = None,
) -> list[SessionManifest]:
    now = migrated_at or datetime.now(UTC)
    migrated: list[SessionManifest] = []
    for session in sessions:
        manifest = build_container_migrated_manifest(
            session,
            migrated_at=now,
            backup_id=backup_id,
        )
        repository.upsert_session(manifest)
        migrated.append(manifest)
    return migrated


def select_sessions_for_migration(
    sessions: Mapping[str, SessionManifest],
    *,
    session_ids: Sequence[str] | None = None,
    all_sessions: bool = False,
) -> list[SessionManifest]:
    if all_sessions:
        selected = [
            session
            for session in sessions.values()
            if is_libvirt_backed_session(session)
        ]
        return sorted(selected, key=lambda session: session.session_id)
    requested = [str(session_id).strip() for session_id in (session_ids or []) if str(session_id).strip()]
    if not requested:
        raise ValueError("specify --all or at least one --session-id")
    selected: list[SessionManifest] = []
    for session_id in requested:
        session = sessions.get(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        selected.append(session)
    return selected


def plan_payload(plans: Sequence[SessionMigrationPlan]) -> list[dict[str, object]]:
    return [plan.as_json() for plan in plans]


def _copy_remote_tree(
    *,
    label: str,
    user: str,
    vm_ip: str,
    remote_path: str,
    local_path: Path,
    ssh_command: str,
    delete: bool,
    result: SessionCopyResult,
    run: Callable[..., subprocess.CompletedProcess[str]],
    progress: Callable[[str], None] | None,
    required: bool,
    probe_first: bool = False,
) -> None:
    local_path.mkdir(parents=True, exist_ok=True)
    remote = f"{user}@{vm_ip}:{remote_path.rstrip('/')}/"
    if probe_first and not _remote_dir_exists(
        user=user,
        vm_ip=vm_ip,
        remote_path=remote_path,
        ssh_command=ssh_command,
        run=run,
    ):
        result.warnings.append(f"{label} source directory does not exist on VM: {remote_path}")
        return
    if progress is not None:
        progress(f"copying {label} for {result.session_id} from {remote_path}")
    command = ["rsync", "-aH", "--no-owner", "--no-group"]
    if delete:
        command.append("--delete")
    command.extend(["-e", ssh_command, remote, f"{local_path}/"])
    completed = run(command, check=False, capture_output=True, text=True)
    if completed.returncode == 0:
        result.copied.append(label)
        return
    message = _subprocess_message(completed)
    if required:
        result.errors.append(f"{label} copy failed: {message}")
    else:
        result.warnings.append(f"{label} copy skipped or failed: {message}")


def _remote_dir_exists(
    *,
    user: str,
    vm_ip: str,
    remote_path: str,
    ssh_command: str,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> bool:
    command = [*shlex.split(ssh_command), f"{user}@{vm_ip}", "test", "-d", remote_path]
    completed = run(command, check=False, capture_output=True, text=True)
    return completed.returncode == 0


def _merge_tree_without_overwrite(source: Path, destination: Path) -> list[str]:
    warnings: list[str] = []
    for root, _, filenames in os.walk(source):
        root_path = Path(root)
        for filename in filenames:
            source_file = root_path / filename
            relative = source_file.relative_to(source)
            destination_file = destination / relative
            if destination_file.exists():
                if source_file.stat().st_size != destination_file.stat().st_size:
                    warnings.append(
                        f"kept existing Codex state file with different size: {destination_file}"
                    )
                continue
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, destination_file)
    return warnings


def _preserve_existing_file(destination: Path, *, agent_home: Path, warnings: list[str]) -> None:
    if not destination.exists() or not destination.is_file():
        return
    try:
        relative = destination.relative_to(agent_home)
    except ValueError:
        return
    backup_path = agent_home / ".evander" / "migration-originals" / relative
    if backup_path.exists():
        return
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(destination, backup_path)
    warnings.append(f"preserved original {destination} at {backup_path}")


def _container_destination_to_agent_home(destination_path: str, *, agent_home: Path) -> Path | None:
    prefix = "/home/agent/"
    if destination_path == "/home/agent":
        return agent_home
    if not destination_path.startswith(prefix):
        return None
    return agent_home / destination_path[len(prefix) :]


def _best_effort_chown(
    path: Path,
    *,
    uid: int,
    gid: int,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> list[str]:
    completed = run(
        ["chown", "-R", f"{uid}:{gid}", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return []
    return [f"could not chown {path} to {uid}:{gid}: {_subprocess_message(completed)}"]


def _subprocess_message(completed: subprocess.CompletedProcess[str]) -> str:
    stderr = str(completed.stderr or "").strip()
    stdout = str(completed.stdout or "").strip()
    if stderr:
        return stderr
    if stdout:
        return stdout
    return f"exit code {completed.returncode}"


def _has_any_file(path: Path) -> bool:
    return any(child.is_file() for child in path.rglob("*"))


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def dataclass_json(value: object) -> object:
    if hasattr(value, "as_json"):
        return value.as_json()
    return asdict(value) if hasattr(value, "__dataclass_fields__") else value
