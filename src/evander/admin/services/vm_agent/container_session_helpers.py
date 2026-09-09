from __future__ import annotations

from evander.agent_threads import usable_agent_thread_id

from evander.admin.services.vm_agent.common import (
    Any,
    DirectoryPath,
    Mapping,
    SessionManifest,
    UTC,
    _CONTAINER_SESSION_ID_LOG_PATTERN,
    datetime,
)
from evander.admin.services.vm_agent.container_backend import VmAgentContainerBackendMixin


class VmAgentContainerSessionHelperMixin:
    def _container_worker_execution_repository(self):
        return self.container_worker_execution_repository()

    @staticmethod
    def _container_worker_backend_url(backend: Any) -> str | None:
        client = getattr(backend, "client", None)
        client_url = str(getattr(client, "base_url", "") or "").strip()
        if client_url:
            return client_url
        config = getattr(backend, "config", None)
        config_url = str(getattr(config, "container_worker_base_url", "") or "").strip()
        return config_url or None

    @staticmethod
    def _container_worker_url_from_metadata(metadata: Any) -> str | None:
        return VmAgentContainerBackendMixin.container_worker_url_from_metadata(metadata)

    def _container_worker_url_for_target(
        self,
        *,
        session: SessionManifest | None = None,
        record: Any | None = None,
        records: list[Any] | None = None,
    ) -> str | None:
        return self.container_worker_url_for_target(
            session=session,
            record=record,
            records=records,
        )

    @staticmethod
    def _agent_record_directory_path(directory_path: str | None) -> str:
        try:
            return DirectoryPath(segments=str(directory_path or "root")).as_posix()
        except ValueError:
            return "root"

    @classmethod
    def _agent_record_matches_directory(
        cls,
        record_directory_path: str | None,
        selected_directory_path: str,
        *,
        direct_only: bool,
    ) -> bool:
        record_path = cls._agent_record_directory_path(record_directory_path)
        selected_path = cls._agent_record_directory_path(selected_directory_path)
        if direct_only:
            return record_path == selected_path
        return record_path == selected_path or record_path.startswith(f"{selected_path}/")

    @staticmethod
    def _container_agent_status(last_status: str | None) -> str:
        normalized = str(last_status or "").strip().casefold()
        if normalized in {"created", "starting"}:
            return "starting"
        if normalized == "running":
            return "running"
        if normalized in {"stopped", "exited"}:
            return "stopped"
        if normalized == "failed":
            return "failed"
        if normalized == "removed":
            return "destroyed"
        return "unknown"

    @staticmethod
    def _container_codex_outcome(record: Any) -> Mapping[str, Any] | None:
        metadata = getattr(record, "metadata", None)
        if not isinstance(metadata, Mapping):
            return None
        outcome = metadata.get("codex_outcome")
        if not isinstance(outcome, Mapping):
            return None
        return outcome

    @classmethod
    def _container_execution_status(cls, record: Any) -> str:
        outcome = cls._container_codex_outcome(record)
        if outcome is not None:
            status = str(outcome.get("status") or "").strip().casefold()
            if status == "failed":
                return "failed"
        status = cls._container_agent_status(getattr(record, "last_status", None))
        metadata = getattr(record, "metadata", None)
        if status in {"destroyed", "stopped"} and isinstance(metadata, Mapping):
            terminal_kind = str(metadata.get("worker_terminal_kind") or "").strip().casefold()
            if terminal_kind == "infrastructure":
                return "failed"
            raw_exit_code = metadata.get("exit_code")
            if raw_exit_code is not None:
                try:
                    if int(raw_exit_code) != 0:
                        return "failed"
                except (TypeError, ValueError):
                    pass
        return status

    @staticmethod
    def _agent_status_is_active(status: str | None) -> bool:
        return str(status or "").strip().casefold() in {
            "preparing",
            "starting",
            "running",
        }

    @staticmethod
    def _container_execution_sort_key(record: Any) -> tuple[datetime, str]:
        timestamp = (
            getattr(record, "started_at", None)
            or getattr(record, "created_at", None)
            or getattr(record, "updated_at", None)
            or datetime.min.replace(tzinfo=UTC)
        )
        return timestamp, str(getattr(record, "execution_id", "") or "")

    @classmethod
    def _container_session_status(cls, records: list[Any]) -> str:
        if not records:
            return "prepared"
        statuses = [cls._container_execution_status(record) for record in records]
        if "running" in statuses:
            return "running"
        if "starting" in statuses:
            return "starting"
        latest = max(records, key=cls._container_execution_sort_key)
        latest_status = cls._container_execution_status(latest)
        if latest_status == "failed":
            return "failed"
        return "idle"

    @staticmethod
    def _normalize_agent_backend(backend: str | None) -> str:
        selected_backend = str(backend or "libvirt_vm").strip()
        if selected_backend in {"libvirt", "libvirt_vm"}:
            return "libvirt_vm"
        if selected_backend == "container_worker_vm":
            return selected_backend
        raise ValueError(f"Unknown agent backend: {selected_backend}")

    @classmethod
    def _normalize_agent_log_view(cls, view: str | None) -> str:
        selected_view = str(view or "executions").strip()
        if selected_view in {"", "executions"}:
            return "executions"
        if selected_view == "threads":
            return "threads"
        raise ValueError(f"Unknown agent log view: {selected_view}")

    @staticmethod
    def _container_log_thread_id(text: str) -> str | None:
        match = _CONTAINER_SESSION_ID_LOG_PATTERN.search(text)
        if match is None:
            return None
        thread_id = str(match.group("thread_id") or "").strip()
        return thread_id or None

    @staticmethod
    def _container_log_without_session_id_lines(text: str) -> str:
        lines = [
            line
            for line in str(text or "").splitlines()
            if _CONTAINER_SESSION_ID_LOG_PATTERN.match(line) is None
        ]
        if not lines:
            return ""
        return "\n".join(lines) + "\n"

    @staticmethod
    def _container_execution_stored_thread_id(execution: Any) -> str | None:
        metadata = getattr(execution, "metadata", None)
        if not isinstance(metadata, Mapping):
            return None

        candidates = [
            metadata.get("native_thread_id"),
            metadata.get("codex_subscription_thread_id"),
            metadata.get("thread_id"),
            metadata.get("agent_thread_id"),
        ]
        codex_outcome = metadata.get("codex_outcome")
        if isinstance(codex_outcome, Mapping):
            candidates.append(codex_outcome.get("thread_id"))
            outcome_thread_ids = codex_outcome.get("thread_ids")
            if isinstance(outcome_thread_ids, list | tuple):
                candidates.extend(outcome_thread_ids)
        for nested_key in ("outputs", "result", "container_worker"):
            nested = metadata.get(nested_key)
            if isinstance(nested, Mapping):
                candidates.extend(
                    [
                        nested.get("thread_id"),
                        nested.get("agent_thread_id"),
                        nested.get("native_thread_id"),
                    ]
                )

        # Historical UI grouping may have only the requested resume ID. Keep
        # that display fallback after all observed identities, never before it.
        candidates.append(metadata.get("resume_thread_id"))
        for nested_key in ("outputs", "result", "container_worker"):
            nested = metadata.get(nested_key)
            if isinstance(nested, Mapping):
                candidates.append(nested.get("resume_thread_id"))
        for candidate in candidates:
            thread_id = usable_agent_thread_id(
                candidate, metadata.get("dispatch_id"), getattr(execution, "execution_id", None)
            )
            if thread_id:
                return thread_id
        return None

    @staticmethod
    def _container_execution_is_agent_run(execution: Any) -> bool:
        metadata = getattr(execution, "metadata", None)
        if isinstance(metadata, Mapping):
            effect_type = str(metadata.get("effect_type") or "").strip()
            if effect_type in {
                "effect:agent:prompt",
                "effect:agent:enqueue-prompt",
            }:
                return True
            if any(
                metadata.get(key)
                for key in (
                    "agent_provider",
                    "codex_auth_mode",
                    "codex_subscription_thread_id",
                )
            ):
                return True
        for candidate in (
            getattr(execution, "execution_id", None),
            getattr(execution, "target_name", None),
        ):
            if str(candidate or "").strip().startswith("agent-"):
                return True
        return False

    @staticmethod
    def _container_execution_artifact_metadata(execution: Any) -> Mapping[str, Any] | None:
        metadata = getattr(execution, "metadata", None)
        if not isinstance(metadata, Mapping):
            return None
        operation_kind = str(metadata.get("operation_kind") or "").strip()
        if not operation_kind.startswith("artifact_"):
            return None
        return metadata

    @classmethod
    def _normalize_agent_backend_filter(cls, backend: str | None) -> str:
        selected_backend = str(backend or "all").strip()
        if selected_backend in {"", "all"}:
            return "all"
        return cls._normalize_agent_backend(selected_backend)

    @staticmethod
    def _agent_optional_string(payload: Mapping[str, Any], key: str) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _agent_string_list(payload: Mapping[str, Any], key: str) -> list[str] | None:
        if key not in payload or payload.get(key) is None:
            return None
        value = payload.get(key)
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"{key} must be a list of strings")
        normalized = [str(item).strip() for item in value if str(item).strip()]
        return normalized

    @staticmethod
    def _agent_int_or_none(payload: Mapping[str, Any], key: str) -> int | None:
        if key not in payload:
            return None
        value = payload.get(key)
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be an integer") from exc

    @staticmethod
    def _container_session_target_name(session: SessionManifest) -> str:
        metadata = session.metadata if isinstance(session.metadata, dict) else {}
        return (
            str(metadata.get("target_name") or session.vm_provider_id or session.session_id).strip()
            or session.session_id
        )

    @staticmethod
    def _is_container_session(session: SessionManifest) -> bool:
        metadata = getattr(session, "metadata", {})
        metadata = metadata if isinstance(metadata, dict) else {}
        return (
            str(getattr(session, "vm_provider", "") or "").strip() == "container_worker_vm"
            or str(metadata.get("execution_backend") or "").strip() == "container_worker_vm"
        )

    container_worker_backend_url = _container_worker_backend_url
    container_worker_url_from_metadata_compat = _container_worker_url_from_metadata
    container_worker_url_for_target_compat = _container_worker_url_for_target
    agent_record_directory_path = _agent_record_directory_path
    agent_record_matches_directory = _agent_record_matches_directory
    container_agent_status = _container_agent_status
    container_codex_outcome = _container_codex_outcome
    container_execution_status = _container_execution_status
    agent_status_is_active = _agent_status_is_active
    container_execution_sort_key = _container_execution_sort_key
    container_session_status = _container_session_status
    normalize_agent_backend = _normalize_agent_backend
    normalize_agent_log_view = _normalize_agent_log_view
    container_log_thread_id = _container_log_thread_id
    container_log_without_session_id_lines = _container_log_without_session_id_lines
    container_execution_stored_thread_id = _container_execution_stored_thread_id
    container_execution_is_agent_run = _container_execution_is_agent_run
    container_execution_artifact_metadata = _container_execution_artifact_metadata
    normalize_agent_backend_filter = _normalize_agent_backend_filter
    agent_optional_string = _agent_optional_string
    agent_string_list = _agent_string_list
    agent_int_or_none = _agent_int_or_none
    container_session_target_name = _container_session_target_name
    is_container_session_compat = _is_container_session
