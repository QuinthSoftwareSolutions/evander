from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agent_sandbox.events.models import (
    EffectDefinitionDiscriminated,
    WorkflowNodeEnvelope,
    materialize_node_definition,
)
from agent_sandbox.provider_runtime.contracts import (
    AgentResumer,
    GitBranchPromoter,
    IssueUpdater,
    ProviderRuntimeBase,
    PullRequestCreator,
    SessionLookup,
    SessionMetadataUpdater,
    VmFrontendBootstrapper,
    VmPythonBootstrapper,
    VmCreator,
    VmRepoCloner,
    VmExecutor,
    VmFileCopier,
    VmFileDeleter,
    VmInventory,
    VmPowerController,
)
from agent_sandbox.workflow.validation import NodeCapability, WorkflowValidationDiagnostic


@dataclass(frozen=True)
class EffectRuntimeContext(ProviderRuntimeBase):
    """Host-side runtime inputs used by effect providers."""

    vm_creator: VmCreator | None = None
    vm_repo_cloner: VmRepoCloner | None = None
    vm_python_bootstrapper: VmPythonBootstrapper | None = None
    vm_frontend_bootstrapper: VmFrontendBootstrapper | None = None
    agent_resumer: AgentResumer | None = None
    vm_power_controller: VmPowerController | None = None
    vm_inventory: VmInventory | None = None
    vm_executor: VmExecutor | None = None
    vm_file_copier: VmFileCopier | None = None
    vm_file_deleter: VmFileDeleter | None = None
    session_lookup: SessionLookup | None = None
    issue_updater: IssueUpdater | None = None
    session_metadata_updater: SessionMetadataUpdater | None = None
    git_branch_promoter: GitBranchPromoter | None = None
    pull_request_creator: PullRequestCreator | None = None
    admin_service: Any | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.admin_service is None:
            return
        for field_name, method_name in (
            ("vm_creator", "create_vm"),
            ("vm_repo_cloner", "clone_repo_into_vm"),
            ("vm_python_bootstrapper", "bootstrap_python_in_vm"),
            ("vm_frontend_bootstrapper", "bootstrap_frontend_in_vm"),
            ("agent_resumer", "resume_agent"),
            ("vm_power_controller", "start_vm"),
            ("vm_inventory", "list_vms"),
            ("vm_executor", "vm_exec"),
            ("vm_file_copier", "vm_copy_files"),
            ("vm_file_deleter", "vm_delete_files"),
            ("issue_updater", "update_issue"),
            ("issue_updater", "update_issue_metadata"),
            ("session_metadata_updater", "session_update_metadata"),
            ("git_branch_promoter", "promote_agent_branch"),
            ("pull_request_creator", "pr_create"),
        ):
            if getattr(self, field_name) is None and hasattr(self.admin_service, method_name):
                object.__setattr__(self, field_name, self.admin_service)
        if self.session_lookup is None:
            session_lookup = _session_lookup_from_admin_service(self.admin_service)
            if session_lookup is not None:
                object.__setattr__(self, "session_lookup", session_lookup)


class EffectProvider(Protocol):
    @classmethod
    def validate_definition(
        cls, effect_definition: EffectDefinitionDiscriminated
    ) -> EffectDefinitionDiscriminated: ...

    def initialize(
        self,
        effect_definition: EffectDefinitionDiscriminated,
        runtime_context: EffectRuntimeContext,
    ) -> None: ...

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]: ...

    def normalize_resolved_config(
        self,
        *,
        resolved_config: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]: ...

    def load_state(self, **kwargs: Any) -> Any: ...

    def save_state(self, **kwargs: Any) -> None: ...

    def cleanup(self, **kwargs: Any) -> None: ...


class EffectProviderBase:
    """Convenience base class for effect provider implementations."""

    def __init__(self) -> None:
        self.effect_definition: EffectDefinitionDiscriminated | None = None
        self.runtime_context: EffectRuntimeContext | None = None

    def initialize(
        self,
        effect_definition: EffectDefinitionDiscriminated,
        runtime_context: EffectRuntimeContext,
    ) -> None:
        if type(effect_definition) is WorkflowNodeEnvelope:
            effect_definition = self.validate_definition(effect_definition)
        self.effect_definition = effect_definition
        self.runtime_context = runtime_context

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        del resolved_config
        return {}

    def normalize_resolved_config(
        self,
        *,
        resolved_config: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        return dict(resolved_config) if isinstance(resolved_config, Mapping) else {}

    def load_state(self, **kwargs: Any) -> Any:
        del kwargs
        return None

    def save_state(self, **kwargs: Any) -> None:
        del kwargs
        return None

    def cleanup(self, **kwargs: Any) -> None:
        del kwargs
        return None

    @classmethod
    def on_execution_started(cls, **kwargs: Any) -> bool:
        del kwargs
        return False

    @classmethod
    def on_execution_waiting(cls, **kwargs: Any) -> None:
        del kwargs
        return None

    @classmethod
    def on_completion_event(cls, **kwargs: Any) -> int:
        del kwargs
        return 0

    @classmethod
    def reconcile_waiting(cls, **kwargs: Any) -> int:
        del kwargs
        return 0

    @classmethod
    def lifecycle_hooks(cls) -> tuple[type["EffectLifecycleHook"], ...]:
        return ()

    @classmethod
    def validate_definition(
        cls, effect_definition: EffectDefinitionDiscriminated
    ) -> EffectDefinitionDiscriminated:
        if type(effect_definition) is not WorkflowNodeEnvelope:
            return effect_definition
        return materialize_node_definition(effect_definition)

    @classmethod
    def node_capability(cls) -> NodeCapability:
        return cls.capability

    @classmethod
    def validate_graph_node(
        cls,
        *,
        node: Any,
        **_: Any,
    ) -> list[WorkflowValidationDiagnostic]:
        del node
        return []

    capability = NodeCapability(min_inbound=1, max_inbound=1)


class EffectProviderExecutionError(RuntimeError):
    """Provider failure that still has terminal outputs worth recording."""

    def __init__(self, message: str, *, outputs: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.outputs = dict(outputs or {})


class EffectLifecycleHook(Protocol):
    def on_post_completion_dispatch(self, *, worker: Any) -> int: ...

    def on_waiting_reconciliation(self, *, worker: Any) -> int: ...

    def on_completion_fan_out(self, *, worker: Any, queue_record: Any, graph: Any) -> int: ...


class EffectLifecycleHookBase:
    def on_post_completion_dispatch(self, *, worker: Any) -> int:
        del worker
        return 0

    def on_waiting_reconciliation(self, *, worker: Any) -> int:
        del worker
        return 0

    def on_completion_fan_out(self, *, worker: Any, queue_record: Any, graph: Any) -> int:
        del worker, queue_record, graph
        return 0


def _session_lookup_from_admin_service(admin_service: Any) -> SessionLookup | None:
    if hasattr(admin_service, "read_all_sessions"):
        return admin_service
    session_repository = getattr(
        getattr(admin_service, "runtime", None), "session_repository", None
    )
    read_all = getattr(session_repository, "read_all", None)
    if callable(read_all):
        return _SessionLookupAdapter(session_repository)
    return None


class _SessionLookupAdapter:
    def __init__(self, sessions: Any) -> None:
        self._sessions = sessions

    def read_all_sessions(self) -> Mapping[str, Any]:
        return self._sessions.read_all()
