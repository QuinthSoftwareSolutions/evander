from __future__ import annotations

from collections.abc import Mapping
import json
import os
from typing import Any

from evander.admin.services.vm_agent.common_models import AgentCreateOperation


class VmAgentCreateOperationMixin:
    """Compatibility entry points for the composed create-operation service."""

    def _agent_create_operation_details(
        self, payload: Mapping[str, Any]
    ) -> tuple[str, str, str | None, str]:
        selected_backend = self._normalize_agent_backend(
            self._agent_optional_string(payload, "backend")
            or self._agent_optional_string(payload, "execution_backend")
        )
        directory_path = self._normalize_directory_path(
            self._agent_optional_string(payload, "directory_path")
        )
        if selected_backend == "libvirt_vm":
            vm_name = self._agent_optional_string(payload, "vm_name")
            if not vm_name:
                raise ValueError("vm_name is required for libvirt_vm agents")
            return (
                selected_backend,
                vm_name,
                self._agent_optional_string(payload, "session_id"),
                directory_path,
            )

        session_id = self._agent_optional_string(payload, "session_id")
        if not session_id:
            raise ValueError("session_id is required for container_worker_vm agents")
        target_name = self._agent_optional_string(payload, "target_name") or session_id
        return selected_backend, target_name, session_id, directory_path

    @staticmethod
    def _agent_create_operation_worker_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(dict(payload), default=str))

    def submit_admin_agent_create(self, payload: Mapping[str, Any]) -> AgentCreateOperation:
        return self._agent_creation_operations.submit(payload)

    def _submit_admin_agent_create(
        self, payload: Mapping[str, Any], *, copy_configuration: dict[str, Any] | None = None
    ) -> AgentCreateOperation:
        return self._agent_creation_operations.submit_operation(
            payload, copy_configuration=copy_configuration
        )

    def list_admin_agent_create_operations(
        self,
        *,
        limit: int = 50,
        directory_path: str | None = None,
    ) -> list[AgentCreateOperation]:
        return self._agent_creation_operations.list(limit=limit, directory_path=directory_path)

    def get_admin_agent_create_operation(self, operation_id: str) -> AgentCreateOperation:
        return self._agent_creation_operations.get(operation_id)

    def _update_agent_create_operation(
        self,
        operation_id: str,
        updater: Any,
    ) -> AgentCreateOperation | None:
        return self._agent_creation_operations.update(operation_id, updater)

    def _run_agent_create_operation(self, operation_id: str) -> None:
        return self._agent_creation_operations.run(operation_id)

    @staticmethod
    def _agent_create_operation_owner_is_alive(owner_pid: int | None) -> bool:
        if owner_pid is None or owner_pid <= 0:
            return False
        if owner_pid == os.getpid():
            return True
        try:
            os.kill(owner_pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def mark_abandoned_agent_create_operations_failed(self) -> None:
        return self._agent_creation_operations.mark_abandoned_failed()

    agent_create_operation_details = _agent_create_operation_details
    agent_create_operation_worker_payload = _agent_create_operation_worker_payload
    update_agent_create_operation = _update_agent_create_operation
    run_agent_create_operation = _run_agent_create_operation
    agent_create_operation_owner_is_alive = _agent_create_operation_owner_is_alive
