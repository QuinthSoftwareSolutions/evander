from __future__ import annotations

from agent_sandbox.admin.services.vm_agent.common import (
    Any,
    DirectoryPath,
    ExecutionBackendKind,
    Mapping,
    SessionManifest,
    UTC,
    datetime,
)


class VmAgentContainerBackendMixin:
    def _build_container_worker_backend(
        self,
        *,
        directory_path: str | None = None,
        worker_url: str | None = None,
    ):
        from agent_sandbox.execution.container_worker_backend import (
            ContainerWorkerVmExecutionBackend,
        )

        resolver = getattr(self._deps.runtime, "provider_config", None)
        resolve_for_directory = getattr(resolver, "resolve_for_directory", None)
        if callable(resolve_for_directory):
            resolved_config = resolve_for_directory(
                self._deps.directory_path.normalize_directory_path(directory_path)
            )
        else:
            resolved_config = self._deps.config
        update: dict[str, Any] = {
            "execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value
        }
        normalized_worker_url = str(worker_url or "").strip()
        if normalized_worker_url:
            update["container_worker_base_url"] = normalized_worker_url
        config = resolved_config.model_copy(update=update)
        return ContainerWorkerVmExecutionBackend(config=config)

    def container_worker_backend(
        self,
        *,
        directory_path: str | None = None,
        worker_url: str | None = None,
    ):
        port = self._deps.container_worker_backend_port
        if port is not None:
            return port.container_worker_backend(
                directory_path=directory_path, worker_url=worker_url
            )
        return self._build_container_worker_backend(
            directory_path=directory_path, worker_url=worker_url
        )

    def container_worker_execution_repository(self):
        from agent_sandbox.execution.container_worker_repository import (
            ContainerWorkerExecutionRepository,
        )

        return ContainerWorkerExecutionRepository(
            self._deps.config.runtime_dir / "container-worker-executions.json"
        )

    def container_execution_records_for_session(self, session_id: str) -> list[Any]:
        repository = self.container_worker_execution_repository()
        list_by_session = getattr(repository, "list_by_session", None)
        if callable(list_by_session):
            records = list_by_session(session_id)
        else:
            records = [
                record
                for record in repository.read_all().values()
                if record.session_id == session_id
            ]
        return sorted(records, key=self.container_execution_sort_key)

    def normalize_agent_record_directory_path(self, directory_path: str | None) -> str:
        try:
            return DirectoryPath(segments=str(directory_path or "root")).as_posix()
        except ValueError:
            return "root"

    @staticmethod
    def is_container_session(session: SessionManifest) -> bool:
        metadata = session.metadata if isinstance(session.metadata, dict) else {}
        return (
            str(session.vm_provider or "").strip() == "container_worker_vm"
            or str(metadata.get("execution_backend") or "").strip() == "container_worker_vm"
        )

    @staticmethod
    def container_worker_url_from_metadata(metadata: Any) -> str | None:
        if not isinstance(metadata, Mapping):
            return None
        for key in ("worker_url", "container_worker_base_url"):
            value = str(metadata.get(key) or "").strip()
            if value:
                return value
        return None

    def container_worker_url_for_target(
        self,
        *,
        session: SessionManifest | None = None,
        record: Any | None = None,
        records: list[Any] | None = None,
    ) -> str | None:
        candidates: list[Any] = []
        if record is not None:
            candidates.append(record)
        if records:
            candidates.extend(
                sorted(
                    records,
                    key=self.container_execution_sort_key,
                    reverse=True,
                )
            )
        for candidate in candidates:
            worker_url = str(getattr(candidate, "worker_url", "") or "").strip()
            if worker_url:
                return worker_url
            metadata_url = self.container_worker_url_from_metadata(
                getattr(candidate, "metadata", None)
            )
            if metadata_url:
                return metadata_url
        if session is not None:
            return self.container_worker_url_from_metadata(session.metadata)
        return None

    @staticmethod
    def container_execution_sort_key(record: Any) -> tuple[datetime, str]:
        timestamp = (
            getattr(record, "started_at", None)
            or getattr(record, "created_at", None)
            or getattr(record, "updated_at", None)
            or datetime.min.replace(tzinfo=UTC)
        )
        return timestamp, str(getattr(record, "execution_id", "") or "")
