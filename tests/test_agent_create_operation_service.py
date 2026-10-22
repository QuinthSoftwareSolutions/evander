from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import threading
from typing import Any

import pytest

from evander.admin.services.agent_create_operation_service import (
    AgentCreateOperationDependencies,
    AgentCreateOperationService,
)
from evander.admin.services.vm_agent.common_models import AgentCreateOperation
from evander.storage import JsonStore


class DeferredExecutor:
    def submit(self, function: Any, *args: Any) -> None:
        self.function = function
        self.args = args


def operation_service(
    tmp_path: Path, *, executor: Any, finish_copy: Any = None
) -> AgentCreateOperationService:
    def reject_unexpected_policy_read(session_id: str | None) -> dict[str, Any]:
        raise AssertionError("Policy must only be read after copy configuration succeeds")

    service = AgentCreateOperationService(
        AgentCreateOperationDependencies(
            operation_details=lambda payload: ("container_worker_vm", "target", "session", "root"),
            serialize_payload=lambda payload: json.loads(json.dumps(dict(payload))),
            normalize_directory=lambda directory: directory or "root",
            now=lambda: datetime.now(UTC),
            create_agent=lambda payload: {"success": True},
            finish_copy=finish_copy or (lambda *args: None),
            effective_tool_policy=reject_unexpected_policy_read,
            owner_is_alive=lambda pid: True,
        )
    )
    service.configure(
        store=JsonStore(tmp_path / "operations.json", AgentCreateOperation),
        owner_id="test-owner",
        payloads={},
        payloads_lock=threading.Lock(),
        executor=executor,
    )
    return service


def test_scheduler_rejection_releases_payload_without_persisting_secrets(tmp_path: Path) -> None:
    class RejectingExecutor:
        def submit(self, function: Any, *args: Any) -> None:
            raise RuntimeError("executor is shutting down")

    service = operation_service(tmp_path, executor=RejectingExecutor())

    with pytest.raises(RuntimeError, match="executor is shutting down"):
        service.submit({"provider_token": "transient-secret"})

    assert service.payloads == {}
    saved = service.list()
    assert len(saved) == 1
    assert saved[0].status == "queued"
    assert "transient-secret" not in (tmp_path / "operations.json").read_text()


def test_copy_failure_records_terminal_error_and_releases_payload(tmp_path: Path) -> None:
    def finish_copy(session_id: str | None, configuration: dict[str, Any]) -> None:
        raise ValueError("Copied session policy is invalid")

    executor = DeferredExecutor()
    service = operation_service(tmp_path, executor=executor, finish_copy=finish_copy)
    operation = service.submit_operation({}, copy_configuration={"allowed_tools": []})
    executor.function(*executor.args)

    saved = service.get(operation.operation_id)
    assert saved.status == "failed"
    assert saved.error == "Copied session policy is invalid"
    assert saved.started_at is not None
    assert saved.finished_at is not None
    assert saved.result is None
    assert service.payloads == {}
