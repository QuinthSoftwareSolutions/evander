from __future__ import annotations

from evander.admin.services.vm_agent.common import (
    AgentCreateOperation,
    Any,
    Mapping,
    json,
    os,
    uuid4,
)


class VmAgentCreateOperationMixin:
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
        if not isinstance(payload, Mapping):
            raise ValueError("Request body must be a JSON object")
        worker_payload = self._agent_create_operation_worker_payload(payload)
        backend, target_name, session_id, directory_path = self._agent_create_operation_details(
            worker_payload
        )
        now = self._current_utc_now()
        operation = AgentCreateOperation(
            operation_id=f"agent-create-{uuid4().hex}",
            status="queued",
            backend=backend,
            target_name=target_name,
            session_id=session_id,
            directory_path=directory_path,
            owner_id=self._agent_create_operation_owner_id,
            owner_pid=os.getpid(),
            created_at=now,
            updated_at=now,
        )
        with self._agent_create_operation_payloads_lock:
            self._agent_create_operation_payloads[operation.operation_id] = worker_payload
        try:
            self.agent_create_operations.put(operation.operation_id, operation)
            self._agent_create_executor.submit(
                self._run_agent_create_operation,
                operation.operation_id,
            )
        except Exception:
            with self._agent_create_operation_payloads_lock:
                self._agent_create_operation_payloads.pop(operation.operation_id, None)
            raise
        return self.get_admin_agent_create_operation(operation.operation_id)

    def list_admin_agent_create_operations(
        self,
        *,
        limit: int = 50,
        directory_path: str | None = None,
    ) -> list[AgentCreateOperation]:
        normalized_directory = (
            self._normalize_directory_path(directory_path) if directory_path else None
        )
        operations = list(self.agent_create_operations.read_all().values())
        if normalized_directory is not None:
            operations = [
                operation
                for operation in operations
                if operation.directory_path == normalized_directory
            ]
        operations.sort(
            key=lambda operation: (
                operation.updated_at,
                operation.created_at,
                operation.operation_id,
            ),
            reverse=True,
        )
        bounded_limit = max(1, min(int(limit), 200))
        return operations[:bounded_limit]

    def get_admin_agent_create_operation(self, operation_id: str) -> AgentCreateOperation:
        normalized_operation_id = str(operation_id or "").strip()
        operation = self.agent_create_operations.get(normalized_operation_id)
        if operation is None:
            raise LookupError(f"Agent create operation not found: {normalized_operation_id}")
        return operation

    def _update_agent_create_operation(
        self,
        operation_id: str,
        updater: Any,
    ) -> AgentCreateOperation | None:
        def _update(items: dict[str, AgentCreateOperation]) -> AgentCreateOperation | None:
            current = items.get(operation_id)
            if current is None:
                return None
            updated = updater(current)
            items[operation_id] = updated
            return updated

        return self.agent_create_operations.update_all(_update)

    def _run_agent_create_operation(self, operation_id: str) -> None:
        started_at = self._current_utc_now()
        operation = self._update_agent_create_operation(
            operation_id,
            lambda current: (
                current.model_copy(
                    update={
                        "status": "running",
                        "started_at": current.started_at or started_at,
                        "updated_at": started_at,
                        "error": None,
                    }
                )
                if current.status in {"queued", "running"}
                else current
            ),
        )
        if operation is None or operation.status != "running":
            return
        try:
            with self._agent_create_operation_payloads_lock:
                worker_payload = self._agent_create_operation_payloads.get(operation_id)
            if worker_payload is None:
                raise RuntimeError("Agent create request payload is no longer available.")
            result = self._agent_create_port().create_admin_agent(worker_payload)
            finished_at = self._current_utc_now()
            if bool(result.get("success")):
                self._update_agent_create_operation(
                    operation_id,
                    lambda current: current.model_copy(
                        update={
                            "status": "succeeded",
                            "result": result,
                            "error": None,
                            "finished_at": finished_at,
                            "updated_at": finished_at,
                        }
                    ),
                )
                return
            self._update_agent_create_operation(
                operation_id,
                lambda current: current.model_copy(
                    update={
                        "status": "failed",
                        "result": result,
                        "error": str(result.get("message") or "Agent create failed."),
                        "finished_at": finished_at,
                        "updated_at": finished_at,
                    }
                ),
            )
        except Exception as exc:
            finished_at = self._current_utc_now()
            error_message = str(exc) or exc.__class__.__name__
            self._update_agent_create_operation(
                operation_id,
                lambda current: current.model_copy(
                    update={
                        "status": "failed",
                        "error": error_message,
                        "finished_at": finished_at,
                        "updated_at": finished_at,
                    }
                ),
            )
        finally:
            with self._agent_create_operation_payloads_lock:
                self._agent_create_operation_payloads.pop(operation_id, None)

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
        now = self._current_utc_now()

        def _mark(items: dict[str, AgentCreateOperation]) -> None:
            for operation_id, operation in list(items.items()):
                if operation.status not in {"queued", "running"}:
                    continue
                if operation.owner_pid is None:
                    continue
                if self._agent_create_operation_owner_is_alive(operation.owner_pid):
                    continue
                items[operation_id] = operation.model_copy(
                    update={
                        "status": "failed",
                        "error": "Admin Web process exited before this create operation finished.",
                        "finished_at": now,
                        "updated_at": now,
                    }
                )

        self.agent_create_operations.update_all(_mark)

    agent_create_operation_details = _agent_create_operation_details
    agent_create_operation_worker_payload = _agent_create_operation_worker_payload
    update_agent_create_operation = _update_agent_create_operation
    run_agent_create_operation = _run_agent_create_operation
    agent_create_operation_owner_is_alive = _agent_create_operation_owner_is_alive
