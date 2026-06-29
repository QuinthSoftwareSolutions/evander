from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class DirectoryPathPort(Protocol):
    """Normalizes admin directory references into canonical directory paths."""

    def normalize_directory_path(self, directory_path: str | None) -> str: ...


class DirectoryOwnershipPort(DirectoryPathPort, Protocol):
    """Directory normalization plus direct ownership lookup for scoped resources."""

    def direct_owned_entity_ids(self, directory_path: str, *, kind: str) -> set[str]: ...


class PrivilegedHelperPort(Protocol):
    """Owns the mutable privileged-helper session used by admin operations."""

    def get_privileged_helper(self) -> Any | None: ...

    def get_use_privileged_helper(self) -> bool: ...

    def set_privileged_helper(self, helper: Any | None) -> None: ...


class PrivilegedHelperFactoryPort(Protocol):
    """Creates privileged-helper session objects."""

    def create_privileged_helper(self, **kwargs: Any) -> Any: ...


class WorkflowConfigPort(Protocol):
    """Host workflow configuration callbacks used by admin worker services."""

    def read_worker_global_config(self, worker_id: str) -> Any: ...

    def write_worker_global_config(
        self,
        worker_id: str,
        payload: dict[str, object],
        *,
        expected_revision: str | None = None,
    ) -> Any: ...

    def read_workflow_editor_document(self, workflow_pack: str | None = None) -> Any: ...

    def read_composed_workflow_editor_document(self) -> Any: ...

    def read_workflow_editor_catalog(self, directory_path: str | None = None) -> Any: ...

    def validate_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
        graphs: list[dict[str, object]] | None = None,
        workflow_pack: str | None = None,
    ) -> Any: ...

    def save_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
        graphs: list[dict[str, object]] | None = None,
        expected_revision: str | None = None,
        workflow_pack: str | None = None,
    ) -> Any: ...

    def read_workflow_run_snapshot(self) -> Any: ...

    def read_workflow_run_detail(self, **kwargs: Any) -> Any: ...

    def read_workflow_run_search(self, **kwargs: Any) -> Any: ...

    def clear_workflow_run(self, event_id: str) -> Any: ...

    def dismiss_workflow_node_failures(self, node_id: str) -> Any: ...

    def resume_workflow_run(self, event_id: str) -> Any: ...

    def workflow_event_journal(self) -> Any: ...

    def upsert_trigger_config(self, trigger_id: str, payload: dict[str, object]) -> Any: ...

    def delete_trigger_config(self, trigger_id: str) -> Any: ...

    def upsert_effect_config(self, effect_id: str, payload: dict[str, object]) -> Any: ...

    def delete_effect_config(self, effect_id: str) -> Any: ...


class VmLifecyclePort(Protocol):
    """Public VM lifecycle operations needed by agent orchestration."""

    def create_vm(self, **kwargs: Any) -> Any: ...

    def start_vm(self, target: str) -> Any: ...


class AgentCreatePort(Protocol):
    """Runs a normalized admin-agent create request."""

    def create_admin_agent(self, payload: Mapping[str, Any]) -> dict[str, Any]: ...


class AgentPromptDispatchPort(Protocol):
    """Dispatches a prompt to an existing admin agent target."""

    def prompt_admin_agent(
        self,
        agent_id: str,
        *,
        backend: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]: ...


class ContainerWorkerBackendPort(Protocol):
    """Builds a container-worker backend for a directory or worker URL."""

    def container_worker_backend(
        self,
        *,
        directory_path: str | None = None,
        worker_url: str | None = None,
    ) -> Any: ...
