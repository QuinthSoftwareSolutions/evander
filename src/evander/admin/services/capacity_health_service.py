from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from evander.capacity_health import (
    DirectorySizeTelemetry,
    HealthState,
    SessionStorageSample,
    TelemetryError,
    TelemetryStatus,
    collect_disk_usage,
    collect_recursive_directory_size,
    top_session_storage,
)
from evander.execution.container_worker_client import (
    ContainerWorkerClient,
    ContainerWorkerClientError,
)
from evander.models import SessionManifest

from evander.admin.common import _coerce_optional_datetime, _utc_now
from evander.admin.payloads import (
    CapacityControlPlanePayload,
    CapacityControlPlaneStoragePayload,
    CapacityHealthPayload,
    CapacityPromptTelemetryPayload,
    CapacityQueueTelemetryPayload,
    CapacitySessionStoragePayload,
    CapacitySessionSummaryPayload,
    CapacityWorkerTelemetryEntryPayload,
    CapacityWorkerTelemetrySummaryPayload,
)


@dataclass(frozen=True)
class CapacityHealthAdminDependencies:
    config: Any
    runtime: Any
    local_issue_yaml_dir: Path
    list_agents: Callable[..., list[Any]]
    normalize_agent_record_directory_path: Callable[[Any], str]
    is_container_session: Callable[[SessionManifest], bool]
    container_worker_execution_repository: Callable[[], Any]
    container_worker_url_from_metadata: Callable[[object], str | None]
    communication_artifact_store: Callable[[], Any]
    published_artifact_store: Callable[[], Any]
    workflow_config_admin: Any
    workflow_run_store_paths: Callable[[], dict[str, Any]]
    workflow_run_store_bundle: Callable[[dict[str, Any]], tuple[Any, Any, Any]]


class CapacityHealthAdminService:
    """Capacity and health telemetry operations."""

    def __init__(self, dependencies: CapacityHealthAdminDependencies) -> None:
        self._deps = dependencies

    def read_capacity_health(self) -> CapacityHealthPayload:
        now = _utc_now()
        sessions = sorted(
            self._deps.runtime.session_repository.list_sessions(),
            key=lambda session: (session.created_at, session.session_id),
        )
        agent_statuses = self._capacity_session_statuses()
        worker_entries = self._capacity_worker_telemetry_entries(sessions)
        session_storage = self._capacity_session_storage_payload(sessions, worker_entries)
        control_plane_storage_areas = self._capacity_control_plane_storage_areas()
        control_plane_storage = self._capacity_storage_aggregate(
            control_plane_storage_areas,
            scope="control_plane_known_storage",
        )
        control_plane_disk = collect_disk_usage(
            self._deps.config.runtime_dir,
            scope="control_plane_runtime",
        )

        backend_counts: Counter[str] = Counter()
        directory_counts: Counter[str] = Counter()
        lifecycle_counts: Counter[str] = Counter()
        status_counts: Counter[str] = Counter()
        age_buckets: Counter[str] = Counter()
        directory_breakdowns: dict[str, dict[str, Any]] = {}
        for session in sessions:
            backend = self._capacity_session_backend(session)
            directory_path = self._deps.normalize_agent_record_directory_path(
                session.directory_path
            )
            backend_counts[backend] += 1
            directory_counts[directory_path] += 1
            agent_status = str(agent_statuses.get(session.session_id) or "").strip() or "unknown"
            status_counts[agent_status] += 1
            lifecycle_bucket = self._capacity_session_lifecycle_bucket(
                session,
                agent_status=agent_status,
                now=now,
            )
            age_bucket = self._capacity_age_bucket(now - session.created_at)
            lifecycle_counts[lifecycle_bucket] += 1
            age_buckets[age_bucket] += 1
            directory_summary = directory_breakdowns.setdefault(
                directory_path,
                {
                    "total_sessions": 0,
                    "lifecycle_counts": Counter(),
                    "status_counts": Counter(),
                    "backend_counts": Counter(),
                    "age_buckets": Counter(),
                },
            )
            directory_summary["total_sessions"] += 1
            directory_summary["lifecycle_counts"][lifecycle_bucket] += 1
            directory_summary["status_counts"][agent_status] += 1
            directory_summary["backend_counts"][backend] += 1
            directory_summary["age_buckets"][age_bucket] += 1

        queue_payload = self._capacity_queue_telemetry(now=now)
        prompt_payload = self._capacity_prompt_telemetry(now=now)

        return CapacityHealthPayload(
            generated_at=now,
            control_plane=CapacityControlPlanePayload(
                disk=control_plane_disk,
                storage=CapacityControlPlaneStoragePayload(
                    total=control_plane_storage,
                    areas=control_plane_storage_areas,
                ),
            ),
            sessions=CapacitySessionSummaryPayload(
                total_sessions=len(sessions),
                total_agents=len(agent_statuses),
                lifecycle_counts=dict(lifecycle_counts),
                status_counts=dict(status_counts),
                backend_counts=dict(backend_counts),
                directory_counts=dict(directory_counts),
                directory_breakdowns={
                    directory_path: {
                        "total_sessions": int(summary["total_sessions"]),
                        "lifecycle_counts": dict(summary["lifecycle_counts"]),
                        "status_counts": dict(summary["status_counts"]),
                        "backend_counts": dict(summary["backend_counts"]),
                        "age_buckets": dict(summary["age_buckets"]),
                    }
                    for directory_path, summary in sorted(directory_breakdowns.items())
                },
                age_buckets=dict(age_buckets),
                storage=session_storage,
            ),
            workers=CapacityWorkerTelemetrySummaryPayload(
                total_workers=len(worker_entries),
                reachable_workers=sum(1 for entry in worker_entries if entry.reachable),
                telemetry_available_workers=sum(
                    1 for entry in worker_entries if entry.telemetry is not None
                ),
                entries=worker_entries,
            ),
            queue=queue_payload,
            prompts=prompt_payload,
        )

    def _capacity_session_statuses(self) -> dict[str, str]:
        try:
            records = self._deps.list_agents(
                include_inactive=True,
                reconcile_containers=False,
                all_directories=True,
                omit_vm_state=True,
                summary_only=True,
            )
        except Exception:
            return {}
        statuses: dict[str, tuple[int, datetime | None, str]] = {}
        kind_rank = {
            "container_worker_session": 0,
            "vm_session": 0,
            "vm": 1,
        }
        for record in records:
            session_id = str(record.session_id or "").strip()
            if not session_id:
                continue
            kind = str((record.host or {}).get("kind") or "").strip()
            if kind not in kind_rank:
                continue
            updated_at = record.updated_at or record.started_at
            rank = kind_rank[kind]
            current = statuses.get(session_id)
            if (
                current is None
                or rank < current[0]
                or (
                    rank == current[0]
                    and updated_at is not None
                    and (current[1] is None or updated_at > current[1])
                )
            ):
                statuses[session_id] = (rank, updated_at, str(record.status or "").strip())
        return {session_id: value[2] for session_id, value in statuses.items()}

    def _capacity_session_backend(self, session: SessionManifest) -> str:
        metadata = session.metadata if isinstance(session.metadata, dict) else {}
        backend = str(metadata.get("execution_backend") or session.vm_provider or "").strip()
        normalized = backend.casefold()
        if normalized in {"libvirt", "libvirt_vm"}:
            return "libvirt_vm"
        if normalized == "container_worker_vm":
            return "container_worker_vm"
        if self._deps.is_container_session(session):
            return "container_worker_vm"
        return backend or "libvirt_vm"

    @staticmethod
    def _capacity_session_lifecycle_bucket(
        session: SessionManifest,
        *,
        agent_status: str,
        now: datetime,
    ) -> str:
        expired = session.expires_at <= now
        normalized = agent_status.strip().casefold()
        if not expired and normalized in {"preparing", "starting", "running"}:
            return "active"
        if normalized in {"failed", "stale"}:
            return "failed_stale"
        if expired:
            return "retained"
        return "waiting"

    @staticmethod
    def _capacity_age_bucket(age: timedelta) -> str:
        seconds = max(0, int(age.total_seconds()))
        if seconds < 60 * 60:
            return "lt_1h"
        if seconds < 24 * 60 * 60:
            return "lt_1d"
        if seconds < 7 * 24 * 60 * 60:
            return "lt_7d"
        return "gte_7d"

    def _capacity_control_plane_storage_areas(self) -> list[DirectorySizeTelemetry]:
        areas: list[DirectorySizeTelemetry] = []
        paths: list[tuple[str, Path]] = [
            ("control_plane_session_store", self._deps.config.session_store_path),
            ("control_plane_agent_prompt_store", self._deps.runtime.agent_prompt_store.path),
            ("control_plane_local_issues", self._deps.local_issue_yaml_dir),
            (
                "control_plane_container_worker_executions",
                self._deps.container_worker_execution_repository().path,
            ),
            (
                "control_plane_communication_artifacts",
                self._deps.communication_artifact_store().root,
            ),
            ("control_plane_published_artifacts", self._deps.published_artifact_store().root),
        ]
        try:
            workflow_stores = self._deps.workflow_run_store_paths()
        except Exception:
            workflow_stores = {}
        if workflow_stores:
            paths.extend(
                [
                    ("workflow_event_queue", Path(workflow_stores["event_queue_store_path"])),
                    (
                        "workflow_orchestration_store",
                        Path(workflow_stores["orchestration_store_path"]),
                    ),
                    (
                        "workflow_effect_executions",
                        Path(workflow_stores["effect_execution_store_path"]),
                    ),
                    (
                        "workflow_gate_executions",
                        Path(workflow_stores["gate_execution_store_path"]),
                    ),
                    (
                        "workflow_config_snapshots",
                        Path(workflow_stores["workflow_config_snapshot_store_path"]),
                    ),
                    (
                        "workflow_await_agent_store",
                        Path(workflow_stores["await_agent_store_path"]),
                    ),
                    (
                        "workflow_legacy_await_agent_store",
                        Path(workflow_stores["legacy_await_agent_store_path"]),
                    ),
                ]
            )
        for scope, path in paths:
            areas.append(collect_recursive_directory_size(path, scope=scope))
        return areas

    def _capacity_storage_aggregate(
        self,
        areas: list[DirectorySizeTelemetry],
        *,
        scope: str,
    ) -> DirectorySizeTelemetry:
        available = [area for area in areas if area.status is TelemetryStatus.AVAILABLE]
        errors = [error for area in areas for error in area.errors]
        if available:
            status = TelemetryStatus.AVAILABLE
            health = HealthState.OK if not errors else HealthState.WARNING
        elif any(area.status is TelemetryStatus.PERMISSION_DENIED for area in areas):
            status = TelemetryStatus.PERMISSION_DENIED
            health = HealthState.UNKNOWN
        elif any(area.status is TelemetryStatus.MISSING for area in areas):
            status = TelemetryStatus.MISSING
            health = HealthState.UNKNOWN
        else:
            status = TelemetryStatus.UNSUPPORTED
            health = HealthState.UNKNOWN
        return DirectorySizeTelemetry(
            scope=scope,
            status=status,
            health=health,
            size_bytes=sum(area.size_bytes for area in available),
            file_count=sum(area.file_count for area in available),
            directory_count=sum(area.directory_count for area in available),
            symlink_count=sum(area.symlink_count for area in available),
            error_count=len(errors),
            errors=errors,
        )

    def _capacity_session_storage_payload(
        self,
        sessions: list[SessionManifest],
        worker_entries: list[CapacityWorkerTelemetryEntryPayload],
    ) -> CapacitySessionStoragePayload:
        samples: dict[str, SessionStorageSample] = {}
        measured_total_bytes = 0
        measured_session_count = 0
        missing_telemetry: list[DirectorySizeTelemetry] = []
        missing_telemetry_seen: set[str] = set()

        for entry in worker_entries:
            telemetry = entry.telemetry
            if telemetry is None:
                if entry.worker_url not in missing_telemetry_seen:
                    missing_telemetry.append(
                        self._capacity_missing_storage_entry(
                            scope=f"session_storage:worker:{entry.worker_url}",
                            status=entry.telemetry_status,
                            message=entry.errors[0].message
                            if entry.errors
                            else "worker session storage telemetry is unavailable",
                        )
                    )
                    missing_telemetry_seen.add(entry.worker_url)
                continue
            storage_total = telemetry.session_storage.total
            if storage_total.status is not TelemetryStatus.AVAILABLE:
                if entry.worker_url not in missing_telemetry_seen:
                    missing_telemetry.append(
                        self._capacity_missing_storage_entry(
                            scope=f"session_storage:worker:{entry.worker_url}",
                            status=storage_total.status,
                            message=storage_total.errors[0].message
                            if storage_total.errors
                            else "worker session storage telemetry is unavailable",
                        )
                    )
                    missing_telemetry_seen.add(entry.worker_url)
                continue
            measured_total_bytes += storage_total.size_bytes
            measured_session_count += telemetry.session_storage.session_count
            for sample in (
                *telemetry.session_storage.largest_sessions,
                *telemetry.session_storage.oldest_sessions,
            ):
                current = samples.get(sample.session_id)
                if current is None:
                    samples[sample.session_id] = sample
                    continue
                samples[sample.session_id] = SessionStorageSample(
                    session_id=sample.session_id,
                    size_bytes=max(current.size_bytes, sample.size_bytes),
                    backend=current.backend or sample.backend,
                    directory_path=current.directory_path or sample.directory_path,
                    created_at=(
                        current.created_at
                        if current.created_at is not None
                        and (sample.created_at is None or current.created_at <= sample.created_at)
                        else sample.created_at
                    ),
                    updated_at=(
                        current.updated_at
                        if current.updated_at is not None
                        and (sample.updated_at is None or current.updated_at >= sample.updated_at)
                        else sample.updated_at
                    ),
                )

        libvirt_session_count = sum(
            1 for session in sessions if self._capacity_session_backend(session) == "libvirt_vm"
        )
        if libvirt_session_count > 0:
            missing_telemetry.append(
                self._capacity_missing_storage_entry(
                    scope="session_storage:libvirt_vm",
                    status=TelemetryStatus.UNSUPPORTED,
                    message="VM-local session storage is not host-readable",
                )
            )

        available_samples = list(samples.values())
        return CapacitySessionStoragePayload(
            measured_total=DirectorySizeTelemetry(
                scope="session_storage:container_worker_vm",
                status=TelemetryStatus.AVAILABLE
                if measured_session_count > 0
                else TelemetryStatus.MISSING,
                health=HealthState.OK if measured_session_count > 0 else HealthState.UNKNOWN,
                size_bytes=measured_total_bytes,
                file_count=0,
                directory_count=0,
                symlink_count=0,
            ),
            measured_session_count=measured_session_count,
            largest_sessions=top_session_storage(available_samples, limit=10, order="largest"),
            oldest_sessions=top_session_storage(available_samples, limit=10, order="oldest"),
            missing_telemetry=missing_telemetry,
        )

    def _capacity_missing_storage_entry(
        self,
        *,
        scope: str,
        status: TelemetryStatus,
        message: str,
    ) -> DirectorySizeTelemetry:
        return DirectorySizeTelemetry(
            scope=scope,
            status=status,
            health=HealthState.UNKNOWN,
            error_count=1,
            errors=[TelemetryError(status=status, message=message)],
        )

    def _capacity_worker_telemetry_entries(
        self,
        sessions: list[SessionManifest],
    ) -> list[CapacityWorkerTelemetryEntryPayload]:
        descriptors = self._capacity_worker_descriptors(sessions)
        entries: list[CapacityWorkerTelemetryEntryPayload] = []
        for descriptor in descriptors:
            entries.append(self._capacity_worker_telemetry_entry(descriptor))
        return entries

    def _capacity_worker_descriptors(
        self,
        sessions: list[SessionManifest],
    ) -> list[dict[str, Any]]:
        descriptors: dict[str, dict[str, Any]] = {}

        def register(
            worker_url: str | None,
            *,
            directory_path: str | None,
            token_env: str | None,
            timeout_seconds: float | None,
            discovered_from: str,
        ) -> None:
            normalized_url = str(worker_url or "").strip().rstrip("/")
            if not normalized_url:
                return
            descriptor = descriptors.setdefault(
                normalized_url,
                {
                    "worker_url": normalized_url,
                    "directory_paths": set(),
                    "discovered_from": set(),
                    "token_env": token_env,
                    "timeout_seconds": timeout_seconds,
                },
            )
            if directory_path:
                descriptor["directory_paths"].add(
                    self._deps.normalize_agent_record_directory_path(directory_path)
                )
            descriptor["discovered_from"].add(discovered_from)
            if descriptor.get("token_env") is None and token_env:
                descriptor["token_env"] = token_env
            if descriptor.get("timeout_seconds") is None and timeout_seconds is not None:
                descriptor["timeout_seconds"] = timeout_seconds

        try:
            root_config = self._deps.runtime.provider_config.resolve_for_directory("root")
        except Exception:
            root_config = None
        if root_config is not None:
            register(
                root_config.container_worker_base_url,
                directory_path="root",
                token_env=root_config.container_worker_token_env,
                timeout_seconds=root_config.container_worker_request_timeout_seconds,
                discovered_from="configured:root",
            )

        now = _utc_now()
        for session in sessions:
            directory_path = self._deps.normalize_agent_record_directory_path(
                session.directory_path
            )
            if self._deps.is_container_session(session) and session.expires_at > now:
                try:
                    resolved_config = self._deps.runtime.provider_config.resolve_for_directory(
                        session.directory_path
                    )
                except Exception:
                    resolved_config = None
                if resolved_config is not None:
                    register(
                        resolved_config.container_worker_base_url,
                        directory_path=directory_path,
                        token_env=resolved_config.container_worker_token_env,
                        timeout_seconds=resolved_config.container_worker_request_timeout_seconds,
                        discovered_from=f"configured:{directory_path}",
                    )
                register(
                    self._deps.container_worker_url_from_metadata(session.metadata),
                    directory_path=directory_path,
                    token_env=(
                        resolved_config.container_worker_token_env
                        if resolved_config is not None
                        else None
                    ),
                    timeout_seconds=(
                        resolved_config.container_worker_request_timeout_seconds
                        if resolved_config is not None
                        else None
                    ),
                    discovered_from=f"session:{session.session_id}",
                )

        ordered = sorted(
            descriptors.values(),
            key=lambda item: (
                str(item["worker_url"]),
                sorted(str(path) for path in item["directory_paths"]),
            ),
        )
        for descriptor in ordered:
            descriptor["directory_paths"] = sorted(
                str(path) for path in descriptor["directory_paths"]
            )
            descriptor["discovered_from"] = sorted(
                str(item) for item in descriptor["discovered_from"]
            )
            if descriptor.get("token_env") is None:
                descriptor["token_env"] = "EVANDER_WORKER_TOKEN"
            if descriptor.get("timeout_seconds") is None:
                descriptor["timeout_seconds"] = 30.0
        return ordered

    def _capacity_worker_telemetry_entry(
        self,
        descriptor: dict[str, Any],
    ) -> CapacityWorkerTelemetryEntryPayload:
        worker_url = str(descriptor.get("worker_url") or "").strip().rstrip("/")
        token_env = str(descriptor.get("token_env") or "EVANDER_WORKER_TOKEN").strip()
        timeout_seconds = float(descriptor.get("timeout_seconds") or 30.0)
        checked_at = _utc_now()
        entry = CapacityWorkerTelemetryEntryPayload(
            worker_url=worker_url,
            directory_paths=[str(path) for path in descriptor.get("directory_paths", [])],
            discovered_from=[str(item) for item in descriptor.get("discovered_from", [])],
            last_checked_at=checked_at,
        )
        worker_client = ContainerWorkerClient(
            base_url=worker_url,
            token_env=token_env,
            timeout_seconds=timeout_seconds,
        )
        try:
            healthz = worker_client.health()
            entry = entry.model_copy(
                update={
                    "healthz_status": TelemetryStatus.AVAILABLE,
                    "healthz": healthz,
                    "reachable": True,
                    "last_heartbeat_at": checked_at,
                    "last_heartbeat_source": "healthz_live_probe",
                }
            )
        except ContainerWorkerClientError as exc:
            status = self._capacity_telemetry_status_from_error(exc)
            entry.errors.append(TelemetryError(status=status, message=str(exc)))
            entry = entry.model_copy(update={"healthz_status": status})

        try:
            telemetry = worker_client.telemetry()
            entry = entry.model_copy(
                update={
                    "telemetry_status": TelemetryStatus.AVAILABLE,
                    "telemetry": telemetry,
                    "reachable": True,
                    "last_heartbeat_at": telemetry.generated_at,
                    "last_heartbeat_source": "worker_telemetry_live_probe",
                }
            )
        except ContainerWorkerClientError as exc:
            status = self._capacity_telemetry_status_from_error(exc)
            entry.errors.append(TelemetryError(status=status, message=str(exc)))
            entry = entry.model_copy(
                update={
                    "telemetry_status": status,
                }
            )
        return entry

    @staticmethod
    def _capacity_telemetry_status_from_error(exc: Exception) -> TelemetryStatus:
        message = str(exc).casefold()
        if any(token in message for token in ("not configured", "unauthorized", "forbidden")):
            return TelemetryStatus.PERMISSION_DENIED
        if any(token in message for token in ("connect", "timeout", "timed out")):
            return TelemetryStatus.MISSING
        if any(token in message for token in ("non-json", "non-object", "json", "unsupported")):
            return TelemetryStatus.UNSUPPORTED
        if any(token in message for token in ("404", "405")):
            return TelemetryStatus.UNSUPPORTED
        if any(token in message for token in ("401", "403")):
            return TelemetryStatus.PERMISSION_DENIED
        return TelemetryStatus.UNSUPPORTED

    def _capacity_queue_telemetry(self, *, now: datetime) -> CapacityQueueTelemetryPayload:
        try:
            stores = self._deps.workflow_run_store_paths()
        except Exception:
            return CapacityQueueTelemetryPayload(
                status=TelemetryStatus.MISSING,
                health=HealthState.UNKNOWN,
                errors=[
                    TelemetryError(
                        status=TelemetryStatus.MISSING,
                        message="workflow queue telemetry is unavailable",
                    )
                ],
            )
        backend = str(stores.get("orchestration_store_backend") or "").strip().casefold() or None
        try:
            queue_store, _, _ = self._deps.workflow_run_store_bundle(stores)
            active_records = list(queue_store.list_active_records())
        except FileNotFoundError:
            return CapacityQueueTelemetryPayload(
                status=TelemetryStatus.MISSING,
                health=HealthState.UNKNOWN,
                backend=backend,
                errors=[
                    TelemetryError(
                        status=TelemetryStatus.MISSING, message="trigger queue is missing"
                    )
                ],
            )
        except PermissionError:
            return CapacityQueueTelemetryPayload(
                status=TelemetryStatus.PERMISSION_DENIED,
                health=HealthState.UNKNOWN,
                backend=backend,
                errors=[
                    TelemetryError(
                        status=TelemetryStatus.PERMISSION_DENIED,
                        message="permission denied while reading trigger queue",
                    )
                ],
            )
        except (OSError, ValueError):
            return CapacityQueueTelemetryPayload(
                status=TelemetryStatus.UNSUPPORTED,
                health=HealthState.UNKNOWN,
                backend=backend,
                errors=[
                    TelemetryError(
                        status=TelemetryStatus.UNSUPPORTED,
                        message="trigger queue telemetry is unavailable",
                    )
                ],
            )

        pending = [record for record in active_records if record.delivery_state == "pending"]
        inflight = [record for record in active_records if record.delivery_state == "inflight"]
        oldest = min(
            active_records,
            key=lambda record: (record.occurred_at, record.event_id),
            default=None,
        )
        if oldest is None:
            health = HealthState.OK
        elif pending or inflight:
            health = HealthState.WARNING
        else:
            health = HealthState.OK
        oldest_age_seconds = None
        if oldest is not None:
            oldest_age_seconds = max(0.0, (now - oldest.occurred_at).total_seconds())
        return CapacityQueueTelemetryPayload(
            status=TelemetryStatus.AVAILABLE,
            health=health,
            backend=backend,
            active_record_count=len(active_records),
            pending_count=len(pending),
            inflight_count=len(inflight),
            oldest_event_id=oldest.event_id if oldest is not None else None,
            oldest_occurred_at=oldest.occurred_at if oldest is not None else None,
            oldest_age_seconds=oldest_age_seconds,
        )

    def _capacity_prompt_telemetry(self, *, now: datetime) -> CapacityPromptTelemetryPayload:
        try:
            raw = self._deps.runtime.agent_prompt_store.read_raw()
        except FileNotFoundError:
            return CapacityPromptTelemetryPayload(
                status=TelemetryStatus.MISSING,
                health=HealthState.UNKNOWN,
                errors=[
                    TelemetryError(
                        status=TelemetryStatus.MISSING,
                        message="agent prompt store is missing",
                    )
                ],
            )
        except PermissionError:
            return CapacityPromptTelemetryPayload(
                status=TelemetryStatus.PERMISSION_DENIED,
                health=HealthState.UNKNOWN,
                errors=[
                    TelemetryError(
                        status=TelemetryStatus.PERMISSION_DENIED,
                        message="permission denied while reading agent prompts",
                    )
                ],
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return CapacityPromptTelemetryPayload(
                status=TelemetryStatus.UNSUPPORTED,
                health=HealthState.UNKNOWN,
                errors=[
                    TelemetryError(
                        status=TelemetryStatus.UNSUPPORTED,
                        message="agent prompt telemetry is unavailable",
                    )
                ],
            )

        messages = raw.get("messages", {})
        unread_entries: list[tuple[str, datetime]] = []
        if isinstance(messages, dict):
            for message_id, value in messages.items():
                if not isinstance(value, dict):
                    continue
                status = str(value.get("status") or "").strip().casefold()
                if status != "unread":
                    continue
                created_at = _coerce_optional_datetime(value.get("created_at"))
                if created_at is None:
                    continue
                unread_entries.append((str(message_id), created_at))
        unread_entries.sort(key=lambda item: (item[1], item[0]))
        oldest = unread_entries[0] if unread_entries else None
        return CapacityPromptTelemetryPayload(
            status=TelemetryStatus.AVAILABLE,
            health=HealthState.OK if not unread_entries else HealthState.WARNING,
            unread_count=len(unread_entries),
            oldest_message_id=oldest[0] if oldest is not None else None,
            oldest_created_at=oldest[1] if oldest is not None else None,
            oldest_age_seconds=(
                max(0.0, (now - oldest[1]).total_seconds()) if oldest is not None else None
            ),
        )
