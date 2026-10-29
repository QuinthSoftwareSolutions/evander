from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import os
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


AgentCreateOperationStatus = Literal["queued", "running", "succeeded", "failed"]


class AgentCreateOperation(BaseModel):
    operation_id: str
    status: AgentCreateOperationStatus
    backend: str
    target_name: str
    session_id: str | None = None
    directory_path: str = "root"
    owner_id: str | None = None
    owner_pid: int | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class AgentCreateOperationDependencies:
    operation_details: Callable[[Mapping[str, Any]], tuple[str, str, str | None, str]]
    serialize_payload: Callable[[Mapping[str, Any]], dict[str, Any]]
    normalize_directory: Callable[[str | None], str]
    now: Callable[[], datetime]
    create_agent: Callable[[Mapping[str, Any]], dict[str, Any]]
    finish_copy: Callable[[str | None, dict[str, Any]], None]
    effective_tool_policy: Callable[[str | None], dict[str, Any]]
    owner_is_alive: Callable[[int | None], bool]


class AgentCreateOperationService:
    """Owns asynchronous create-operation state, scheduling and crash recovery.

    Creation and copy-policy application are supplied domain operations; queue
    persistence never receives the raw request payload.
    """

    def __init__(self, dependencies: AgentCreateOperationDependencies) -> None:
        self._deps = dependencies
        self._store: Any | None = None
        self._owner_id: str | None = None
        self._payloads: dict[str, dict[str, Any]] | None = None
        self._payloads_lock: Any | None = None
        self._executor: Any | None = None

    def configure(
        self,
        *,
        store: Any | None,
        owner_id: str | None,
        payloads: dict[str, dict[str, Any]] | None,
        payloads_lock: Any | None,
        executor: Any | None,
    ) -> None:
        self._store = store
        self._owner_id = owner_id
        self._payloads = payloads
        self._payloads_lock = payloads_lock
        self._executor = executor

    @property
    def store(self) -> Any:
        if self._store is None:
            raise RuntimeError(
                "VmAgentAdminService agent_create_operations store is not configured"
            )
        return self._store

    @property
    def owner_id(self) -> str:
        return self._owner_id or "vm-agent-admin"

    @property
    def payloads(self) -> dict[str, dict[str, Any]]:
        if self._payloads is None:
            self._payloads = {}
        return self._payloads

    @property
    def payloads_lock(self) -> Any:
        if self._payloads_lock is None:
            raise RuntimeError("VmAgentAdminService agent create payload lock is not configured")
        return self._payloads_lock

    @property
    def executor(self) -> Any:
        if self._executor is None:
            raise RuntimeError("VmAgentAdminService agent create executor is not configured")
        return self._executor

    def submit(self, payload: Mapping[str, Any]) -> AgentCreateOperation:
        if not isinstance(payload, Mapping):
            raise ValueError("Request body must be a JSON object")
        if "_copy_configuration" in payload:
            raise ValueError("_copy_configuration is reserved for session copy operations")
        return self.submit_operation(payload)

    def submit_operation(
        self, payload: Mapping[str, Any], *, copy_configuration: dict[str, Any] | None = None
    ) -> AgentCreateOperation:
        if not isinstance(payload, Mapping):
            raise ValueError("Request body must be a JSON object")
        worker_payload = self._deps.serialize_payload(payload)
        if copy_configuration is not None:
            worker_payload["_copy_configuration"] = copy_configuration
        backend, target_name, session_id, directory_path = self._deps.operation_details(
            worker_payload
        )
        now = self._deps.now()
        operation = AgentCreateOperation(
            operation_id=f"agent-create-{uuid4().hex}",
            status="queued",
            backend=backend,
            target_name=target_name,
            session_id=session_id,
            directory_path=directory_path,
            owner_id=self.owner_id,
            owner_pid=os.getpid(),
            created_at=now,
            updated_at=now,
        )
        with self.payloads_lock:
            self.payloads[operation.operation_id] = worker_payload
        try:
            self.store.put(operation.operation_id, operation)
            self.executor.submit(
                self.run,
                operation.operation_id,
            )
        except Exception:
            with self.payloads_lock:
                self.payloads.pop(operation.operation_id, None)
            raise
        return self.get(operation.operation_id)

    def list(
        self,
        *,
        limit: int = 50,
        directory_path: str | None = None,
    ) -> list[AgentCreateOperation]:
        normalized_directory = (
            self._deps.normalize_directory(directory_path) if directory_path else None
        )
        operations = list(self.store.read_all().values())
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

    def get(self, operation_id: str) -> AgentCreateOperation:
        normalized_operation_id = str(operation_id or "").strip()
        operation = self.store.get(normalized_operation_id)
        if operation is None:
            raise LookupError(f"Agent create operation not found: {normalized_operation_id}")
        return operation

    def update(
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

        return self.store.update_all(_update)

    def run(self, operation_id: str) -> None:
        started_at = self._deps.now()
        operation = self.update(
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
            with self.payloads_lock:
                worker_payload = self.payloads.get(operation_id)
            if worker_payload is None:
                raise RuntimeError("Agent create request payload is no longer available.")
            create_payload = dict(worker_payload)
            copy_configuration = create_payload.pop("_copy_configuration", None)
            result = self._deps.create_agent(create_payload)
            if bool(result.get("success")) and copy_configuration is not None:
                self._deps.finish_copy(operation.session_id, copy_configuration)
                result["effective_tool_policy"] = self._deps.effective_tool_policy(
                    operation.session_id
                )
            finished_at = self._deps.now()
            if bool(result.get("success")):
                self.update(
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
            self.update(
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
            finished_at = self._deps.now()
            error_message = str(exc) or exc.__class__.__name__
            self.update(
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
            with self.payloads_lock:
                self.payloads.pop(operation_id, None)

    def mark_abandoned_failed(self) -> None:
        now = self._deps.now()

        def _mark(items: dict[str, AgentCreateOperation]) -> None:
            for operation_id, operation in list(items.items()):
                if operation.status not in {"queued", "running"}:
                    continue
                if operation.owner_pid is None:
                    continue
                if self._deps.owner_is_alive(operation.owner_pid):
                    continue
                items[operation_id] = operation.model_copy(
                    update={
                        "status": "failed",
                        "error": "Admin Web process exited before this create operation finished.",
                        "finished_at": now,
                        "updated_at": now,
                    }
                )

        self.store.update_all(_mark)
