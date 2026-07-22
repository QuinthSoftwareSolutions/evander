# ruff: noqa: F403, F405
from __future__ import annotations

from evander.admin.services.vm_agent.common import *  # noqa: F403

from evander.admin.services.vm_agent.agent_actions import VmAgentActionMixin
from evander.admin.services.vm_agent.agent_create import VmAgentCreateMixin
from evander.admin.services.vm_agent.chat_assistant import VmAgentChatAssistantMixin
from evander.admin.services.vm_agent.container_backend import (
    VmAgentContainerBackendMixin,
)
from evander.admin.services.vm_agent.container_sessions import (
    VmAgentContainerSessionMixin,
)
from evander.admin.services.vm_agent.inventory import VmAgentInventoryMixin
from evander.admin.services.vm_agent.git_access import VmAgentGitAccessMixin
from evander.admin.services.vm_agent.vm_lifecycle import VmAgentLifecycleMixin


class VmAgentAdminService(
    VmAgentContainerSessionMixin,
    VmAgentInventoryMixin,
    VmAgentCreateMixin,
    VmAgentActionMixin,
    VmAgentChatAssistantMixin,
    VmAgentContainerBackendMixin,
    VmAgentGitAccessMixin,
    VmAgentLifecycleMixin,
):
    def __init__(self, dependencies: VmAgentAdminDependencies) -> None:
        self._deps = dependencies
        self._assistant_session_repo = JsonChatAssistantSessionRepository(
            Path(self._deps.config.runtime_dir) / "chat-assistant-session-mappings.json"
        )
        self._operations_admin: Any | None = None
        self._skill_admin: Any | None = None
        self._workflow_config_admin: Any | None = None
        self._directory_admin: Any | None = None
        self._skill_library: Any | None = None
        self._agent_create_operations: Any | None = None
        self._agent_create_operation_owner_id_value: str | None = None
        self._agent_create_operation_payloads_value: dict[str, dict[str, Any]] | None = None
        self._agent_create_operation_payloads_lock_value: Any | None = None
        self._agent_create_executor_value: Any | None = None
        self._execution_backend_instance: Any | None = None
        self._project_dir_provider: Any | None = None

    def _current_utc_now(self):
        return _utc_now()

    def configure_admin_collaborators(
        self,
        *,
        operations_admin: Any | None = None,
        skill_admin: Any | None = None,
        workflow_config_admin: Any | None = None,
        directory_admin: Any | None = None,
        skill_library: Any | None = None,
        agent_create_operations: Any | None = None,
        agent_create_operation_owner_id: str | None = None,
        agent_create_operation_payloads: dict[str, dict[str, Any]] | None = None,
        agent_create_operation_payloads_lock: Any | None = None,
        agent_create_executor: Any | None = None,
        project_dir_provider: Any | None = None,
    ) -> None:
        self._operations_admin = operations_admin
        self._skill_admin = skill_admin
        self._workflow_config_admin = workflow_config_admin
        self._directory_admin = directory_admin
        self._skill_library = skill_library
        self._agent_create_operations = agent_create_operations
        self._agent_create_operation_owner_id_value = agent_create_operation_owner_id
        self._agent_create_operation_payloads_value = agent_create_operation_payloads
        self._agent_create_operation_payloads_lock_value = agent_create_operation_payloads_lock
        self._agent_create_executor_value = agent_create_executor
        self._project_dir_provider = project_dir_provider

    @property
    def config(self) -> Any:
        return self._deps.config

    @property
    def runtime(self) -> Any:
        return self._deps.runtime

    @property
    def project_dir(self) -> Path:
        if self._project_dir_provider is not None:
            return Path(self._project_dir_provider())
        return self._deps.project_dir

    @property
    def directory_admin(self) -> Any:
        if self._directory_admin is None:
            raise RuntimeError("VmAgentAdminService directory_admin collaborator is not configured")
        return self._directory_admin

    @property
    def operations_admin(self) -> Any:
        if self._operations_admin is None:
            raise RuntimeError(
                "VmAgentAdminService operations_admin collaborator is not configured"
            )
        return self._operations_admin

    @property
    def skill_admin(self) -> Any:
        if self._skill_admin is None:
            raise RuntimeError("VmAgentAdminService skill_admin collaborator is not configured")
        return self._skill_admin

    @property
    def workflow_config_admin(self) -> Any:
        return self._workflow_config_admin or self._deps.workflow_config

    @property
    def skill_library(self) -> Any:
        if self._skill_library is not None:
            return self._skill_library
        return SkillLibraryRepository(self.config.runtime_dir)

    @property
    def agent_create_operations(self) -> Any:
        if self._agent_create_operations is None:
            raise RuntimeError(
                "VmAgentAdminService agent_create_operations store is not configured"
            )
        return self._agent_create_operations

    @property
    def _agent_create_operation_owner_id(self) -> str:
        return self._agent_create_operation_owner_id_value or "vm-agent-admin"

    @property
    def _agent_create_operation_payloads(self) -> dict[str, dict[str, Any]]:
        if self._agent_create_operation_payloads_value is None:
            self._agent_create_operation_payloads_value = {}
        return self._agent_create_operation_payloads_value

    @property
    def _agent_create_operation_payloads_lock(self) -> Any:
        if self._agent_create_operation_payloads_lock_value is None:
            raise RuntimeError("VmAgentAdminService agent create payload lock is not configured")
        return self._agent_create_operation_payloads_lock_value

    @property
    def _agent_create_executor(self) -> Any:
        if self._agent_create_executor_value is None:
            raise RuntimeError("VmAgentAdminService agent create executor is not configured")
        return self._agent_create_executor_value

    def _vm_lifecycle_port(self) -> VmLifecyclePort:
        return self._deps.vm_lifecycle or self

    def _agent_create_port(self) -> AgentCreatePort:
        return self._deps.agent_create or self

    def _agent_prompt_dispatch_port(self) -> AgentPromptDispatchPort:
        return self._deps.agent_prompt_dispatch or self

    def validate_managed_codex_skills(
        self, skill_names: list[str] | tuple[str, ...] | None
    ) -> list[str]:
        if self._skill_admin is not None:
            return self._skill_admin.validate_managed_codex_skills(skill_names)
        names = normalize_skill_names(skill_names)
        for name in names:
            if not self.skill_library.has_skill(name):
                raise ValueError(f"Unknown Codex skill: {name}")
        return names

    def resolve_container_codex_skill_files(
        self, skill_names: list[str] | tuple[str, ...] | None
    ) -> list[dict[str, Any]]:
        if self._skill_admin is not None:
            return self._skill_admin.resolve_container_codex_skill_files(skill_names)
        names = self.validate_managed_codex_skills(skill_names)
        return self.skill_library.container_file_specs(names)

    def effective_execution_backend(self, directory_path: str | None = None) -> str:
        resolved_config = self.runtime.provider_config.resolve_for_directory(directory_path)
        return str(resolved_config.execution_backend or "libvirt_vm")

    def execution_backend(self):
        backend = self._execution_backend_instance
        if backend is not None:
            return backend
        selected_backend = ExecutionBackendKind(str(self.config.execution_backend))
        if selected_backend == ExecutionBackendKind.LIBVIRT_VM:
            backend = LibvirtVmExecutionBackend(vm_admin_service=self)
        elif selected_backend == ExecutionBackendKind.CONTAINER_WORKER_VM:
            if not self.config.container_worker_base_url:
                raise ValueError(
                    "execution_backend 'container_worker_vm' requires container_worker_base_url."
                )
            backend = ContainerWorkerVmExecutionBackend(config=self.config)
        else:
            raise ValueError(f"Unsupported execution backend: {selected_backend.value}")
        self._execution_backend_instance = backend
        return backend

    def _stable_json_revision(self, payload: object) -> str:
        if hasattr(self.workflow_config_admin, "_stable_json_revision"):
            return self.workflow_config_admin._stable_json_revision(payload)
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
        return hashlib.sha256(data).hexdigest()

    def _communication_notification_target_options(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._communication_notification_target_options(
            *args, **kwargs
        )

    def _agent_communication_target_options(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._agent_communication_target_options(*args, **kwargs)

    def _workflow_run_store_paths(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_run_store_paths(*args, **kwargs)

    def _workflow_uses_sqlite_store(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_uses_sqlite_store(*args, **kwargs)

    # VM/agent/container domain methods moved from the admin runtime.
    def _session_ids_by_vm(self) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        for session_id, session in self.runtime.session_repository.read_all().items():
            if session.vm_id:
                mapping.setdefault(session.vm_id, []).append(session_id)
        for session_ids in mapping.values():
            session_ids.sort()
        return mapping

    def _directory_paths_by_vm(self) -> dict[str, list[str]]:
        mapping: dict[str, set[str]] = {}
        for session in self.runtime.session_repository.list_sessions():
            if session.vm_id:
                mapping.setdefault(session.vm_id, set()).add(session.directory_path)
        return {vm_id: sorted(paths) for vm_id, paths in mapping.items()}

    def _labels_by_vm(self) -> dict[str, dict[str, str]]:
        mapping: dict[str, dict[str, str] | None] = {}
        sessions = self.runtime.session_repository.read_all()
        for session_id in sorted(sessions):
            session = sessions[session_id]
            if not session.vm_id:
                continue
            labels = _normalize_session_labels(session.metadata.get("labels"))
            current = mapping.get(session.vm_id)
            if current is None:
                mapping[session.vm_id] = dict(labels)
                continue
            mapping[session.vm_id] = {
                key: value for key, value in current.items() if labels.get(key) == value
            }
        return {vm_id: labels for vm_id, labels in mapping.items() if labels}

    def _normalize_directory_path(self, directory_path: str | None) -> str:
        if not directory_path:
            return "root"
        result = self.directory_admin.get(directory_path)
        if not result.ok or result.data is None:
            return "root"
        return result.data.details.directory.path

    def _resolve_vm_defaults(self, directory_path: str) -> EffectiveVmDefaultsConfig:
        resolver = VmDefaultsConfigResolver(
            self.config,
            directory_store=self.runtime.directories,
            session_store=self.runtime.session_repository,
        )
        return resolver.resolve_for_directory(directory_path)

    def _resolve_issue_setup(
        self, directory_path: str, *, root_dir: Path
    ) -> EffectiveIssueSetupConfig:
        return resolve_effective_issue_setup_config(
            config=self.config,
            runtime=self.runtime,
            directory_path=directory_path,
            root_dir=root_dir,
        )

    def _resolve_agent_model_for_session(self, session_or_id: SessionManifest | str) -> str:
        if isinstance(session_or_id, SessionManifest):
            session = session_or_id
            session_id = session.session_id
        else:
            session_id = str(session_or_id).strip()
            if not session_id:
                raise ValueError(
                    "Cannot resolve AGENT_MODEL: missing session id. Provide a valid session id or SessionManifest."
                )
            session = self.runtime.session_repository.get_session(session_id)
            if session is None:
                raise ValueError(
                    f"Cannot resolve AGENT_MODEL for session '{session_id}': session not found. "
                    "Remediation: create the session first or pass a SessionManifest with session.model set."
                )
        issue_setup = self._resolve_issue_setup(session.directory_path, root_dir=self.project_dir)
        return resolve_agent_model_for_manifest(session, issue_setup=issue_setup)

    def resolve_agent_model_for_target(self, target: str) -> str:
        session = self.runtime.session_repository.resolve_session_target(target)
        if session is None:
            raise ValueError(f"Unknown session target: {target}")
        return self._resolve_agent_model_for_session(session.session_id)

    session_ids_by_vm = _session_ids_by_vm
    directory_paths_by_vm = _directory_paths_by_vm
    labels_by_vm = _labels_by_vm
    normalize_directory_path = _normalize_directory_path
    resolve_issue_setup = _resolve_issue_setup
    resolve_agent_model_for_session = _resolve_agent_model_for_session
