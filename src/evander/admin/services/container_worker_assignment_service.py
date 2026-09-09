from __future__ import annotations

from collections.abc import Mapping
import sqlite3
from typing import Any

from evander.config import GatewayConfig
from evander.execution.container_worker_repository import ContainerWorkerExecutionRepository
from evander.execution.worker_assignments import (
    ContainerWorkerAssignment,
    ContainerWorkerAssignmentRepository,
)
from evander.repositories.session_manifests import JsonSessionManifestRepository

# Store connection settings and secret *references*, never resolved credentials or
# unrelated directory policy. Model choices, tool grants and job limits stay live.
WORKER_CONNECTION_FIELDS = frozenset(
    {
        "gateway_url",
        "openai_relay_port",
        "nuget_relay_port",
        "http_target_relay_port",
        "http_target_browser_proxy_port",
        "container_worker_base_url",
        "container_worker_token_env",
        "container_worker_git_allowed_from",
        "container_worker_default_image",
        "container_worker_images",
        "container_worker_workspace_persistence",
        "container_worker_codex_auth_mode",
        "container_worker_guest_openai_base_url",
        "container_worker_guest_anthropic_base_url",
        "container_worker_guest_gateway_url",
        "container_worker_guest_http_target_relay_base_url",
        "container_worker_guest_http_target_browser_proxy_url",
        "container_worker_guest_nuget_relay_base_url",
        "container_worker_guest_pypi_index_url",
        "container_worker_guest_npm_registry_url",
        "container_worker_mssql_enabled",
        "container_worker_mssql_host",
        "container_worker_mssql_port",
    }
)


class ContainerWorkerAssignmentError(ValueError):
    pass


def normalize_worker_url(value: object) -> str:
    return str(value or "").strip().rstrip("/")


class ContainerWorkerAssignmentService:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.repository = ContainerWorkerAssignmentRepository(config.runtime_dir)

    def resolve(
        self,
        session_id: str,
        *,
        session: Any = None,
        directory_path: str | None = None,
        worker_url: str | None = None,
        worker_id: str | None = None,
        persist: bool = True,
    ) -> GatewayConfig:
        """Pin before provisioning; subsequent calls never select the new default."""
        session_id = str(session_id or "").strip()
        if not session_id:
            raise ContainerWorkerAssignmentError("session_id is required for worker assignment")
        worker_id = str(worker_id or "").strip() or None
        try:
            assignment = self.repository.get(session_id)
        except (ValueError, OSError, sqlite3.DatabaseError) as exc:
            raise ContainerWorkerAssignmentError("Cannot read saved worker assignment") from exc
        if assignment is None:
            if session is None:
                session = JsonSessionManifestRepository(self.config.session_store_path).get_session(
                    session_id
                )
            directory_path = directory_path or getattr(session, "directory_path", None) or "root"
            known_url = normalize_worker_url(worker_url) or self._legacy_worker_url(
                session_id, session
            )
            if worker_id:
                # Existing sessions must first retain their original assignment. A
                # creation override must never turn an upsert into a migration.
                if session is not None or known_url:
                    raise ContainerWorkerAssignmentError(
                        "worker_id cannot change an existing session's worker; create a new agent"
                    )
                connection_config = self.config_for_worker_id(worker_id)
            else:
                connection_config = self.config_for_worker(known_url, directory_path=directory_path)
            assignment = ContainerWorkerAssignment(
                session_id=session_id,
                directory_path=directory_path,
                worker_url=normalize_worker_url(connection_config.container_worker_base_url),
                worker_id=worker_id,
                connection=connection_config.model_dump(
                    mode="json", include=WORKER_CONNECTION_FIELDS
                ),
            )
            if not assignment.worker_url:
                raise ContainerWorkerAssignmentError("container_worker_base_url is required")
            if persist:
                assignment = self.repository.pin(assignment)
        if worker_id and worker_id != assignment.worker_id:
            raise ContainerWorkerAssignmentError(
                f"Session {session_id!r} already has a different worker assignment; "
                "create a new agent to select another worker"
            )
        expected_url = normalize_worker_url(worker_url)
        if expected_url and expected_url != assignment.worker_url:
            raise ContainerWorkerAssignmentError(
                f"Execution for session {session_id!r} belongs to a different worker than its "
                "saved assignment; reconcile the assignment before operating on this execution."
            )
        return self._apply(assignment)

    def config_for_worker_id(self, worker_id: str) -> GatewayConfig:
        worker_id = str(worker_id).strip()
        profile = self.config.container_workers.get(worker_id)
        if profile is None:
            raise ContainerWorkerAssignmentError(f"Unknown configured worker_id: {worker_id!r}")
        payload = self.config.model_dump(mode="json")
        payload.update(profile.connection_overrides())
        payload["execution_backend"] = "container_worker_vm"
        return GatewayConfig.model_validate(payload)

    def config_for_worker(
        self, worker_url: str | None, *, directory_path: str | None = None
    ) -> GatewayConfig:
        requested = normalize_worker_url(worker_url)
        if not requested or requested == normalize_worker_url(
            self.config.container_worker_base_url
        ):
            return self.config
        candidates = [
            entry for entry in self.repository.list_assignments() if entry.worker_url == requested
        ]
        scoped = [entry for entry in candidates if entry.directory_path == directory_path]
        candidates = scoped or candidates
        if candidates:
            first = candidates[0]
            if all(entry.connection == first.connection for entry in candidates):
                return self._apply(first)
        raise ContainerWorkerAssignmentError(
            f"No unambiguous saved connection settings for worker {requested!r}. "
            "Restore the original worker configuration and run "
            "'evander-cli execution pin-worker-assignments --apply' before changing worker defaults."
        )

    def pin_existing_sessions(
        self, *, directory_path: str | None = None, persist: bool = False, directory_store=None
    ) -> list[dict[str, str]]:
        from evander.provider_config import ProviderConfigResolver

        resolver = ProviderConfigResolver(self.config, directory_store=directory_store)
        sessions = JsonSessionManifestRepository(self.config.session_store_path).read_all().values()
        prefix = str(directory_path or "").strip().rstrip("/")
        plans = []
        for session in sorted(sessions, key=lambda entry: entry.session_id):
            if (
                session.vm_provider != "container_worker_vm"
                and session.metadata.get("execution_backend") != "container_worker_vm"
            ):
                continue
            if (
                prefix
                and session.directory_path != prefix
                and not session.directory_path.startswith(prefix + "/")
            ):
                continue
            service = ContainerWorkerAssignmentService(
                resolver.resolve_for_directory(session.directory_path)
            )
            resolved = service.resolve(session.session_id, session=session, persist=False)
            plans.append((session, service, resolved))
        # Validate the entire selection before pinning; an unknown worker blocks
        # a default change rather than allowing a partial, silent reassignment.
        rows = []
        for session, service, resolved in plans:
            if persist:
                resolved = service.resolve(session.session_id, session=session)
            rows.append(
                {
                    "session_id": session.session_id,
                    "directory_path": session.directory_path,
                    "worker_url": str(resolved.container_worker_base_url),
                }
            )
        return rows

    def _apply(self, assignment: ContainerWorkerAssignment) -> GatewayConfig:
        if set(assignment.connection) != WORKER_CONNECTION_FIELDS:
            raise ContainerWorkerAssignmentError("Saved worker connection settings are incomplete")
        if (
            normalize_worker_url(assignment.connection.get("container_worker_base_url"))
            != assignment.worker_url
        ):
            raise ContainerWorkerAssignmentError(
                "Saved worker address does not match its connection settings"
            )
        payload = self.config.model_dump(mode="json")
        payload.update(assignment.connection)
        payload["execution_backend"] = "container_worker_vm"
        try:
            return GatewayConfig.model_validate(payload)
        except ValueError as exc:
            raise ContainerWorkerAssignmentError(
                "Saved worker connection settings are invalid"
            ) from exc

    def _legacy_worker_url(self, session_id: str, session: Any) -> str:
        metadata = getattr(session, "metadata", {})
        manifest_url = ""
        if isinstance(metadata, Mapping):
            manifest_url = normalize_worker_url(
                metadata.get("worker_url") or metadata.get("container_worker_base_url")
            )
        path = self.config.runtime_dir / "container-worker-executions.db"
        if path.exists():
            summary = ContainerWorkerExecutionRepository(path).get_session_summary(session_id)
            if summary.latest_execution is not None:
                return normalize_worker_url(summary.latest_execution.worker_url)
        return manifest_url
