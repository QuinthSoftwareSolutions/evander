from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin import service as admin_service_module
from agent_sandbox.admin.services import VmAgentAdminDependencies, VmAgentAdminService
from agent_sandbox.admin.services.local_issue_identity import LocalIssueIdentityResolver
from agent_sandbox.config import GatewayConfig
from agent_sandbox.models import SessionManifest, ToolName, VmActionResult


@dataclass
class CallRecord:
    name: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class FakeVmAgentService:
    def __init__(self) -> None:
        self.calls: list[CallRecord] = []

    def _record(self, name: str, *args: Any, **kwargs: Any) -> str:
        self.calls.append(CallRecord(name=name, args=args, kwargs=dict(kwargs)))
        return f"{name}-result"

    def list_vms(self, **kwargs: Any) -> str:
        return self._record("list_vms", **kwargs)

    def get_agent_log_snapshot(self, *args: Any, **kwargs: Any) -> str:
        return self._record("get_agent_log_snapshot", *args, **kwargs)

    def start_vm(self, target: str) -> str:
        return self._record("start_vm", target)

    def shutdown_vm(self, target: str, *, timeout_seconds: int = 60) -> str:
        return self._record("shutdown_vm", target, timeout_seconds=timeout_seconds)

    def enable_agent(self, target: str, *, vm_user: str = "agent") -> str:
        return self._record("enable_agent", target, vm_user=vm_user)

    def disable_agent(self, target: str, *, vm_user: str = "agent") -> str:
        return self._record("disable_agent", target, vm_user=vm_user)

    def kill_agent(self, target: str, pid: int, *, vm_user: str = "agent") -> str:
        return self._record("kill_agent", target, pid, vm_user=vm_user)

    def resume_agent(self, *args: Any, **kwargs: Any) -> str:
        return self._record("resume_agent", *args, **kwargs)

    def vm_exec(self, *args: Any, **kwargs: Any) -> str:
        return self._record("vm_exec", *args, **kwargs)

    def vm_copy_files(self, *args: Any, **kwargs: Any) -> str:
        return self._record("vm_copy_files", *args, **kwargs)

    def vm_delete_files(self, *args: Any, **kwargs: Any) -> str:
        return self._record("vm_delete_files", *args, **kwargs)

    def create_issue_vm(self, *args: Any, **kwargs: Any) -> str:
        return self._record("create_issue_vm", *args, **kwargs)

    def create_issue_vm_from_local_issue(self, *args: Any, **kwargs: Any) -> str:
        return self._record("create_issue_vm_from_local_issue", *args, **kwargs)

    def create_vm(self, *args: Any, **kwargs: Any) -> str:
        return self._record("create_vm", *args, **kwargs)

    def clone_repo_into_vm(self, *args: Any, **kwargs: Any) -> str:
        return self._record("clone_repo_into_vm", *args, **kwargs)

    def bootstrap_python_in_vm(self, *args: Any, **kwargs: Any) -> str:
        return self._record("bootstrap_python_in_vm", *args, **kwargs)

    def bootstrap_frontend_in_vm(self, *args: Any, **kwargs: Any) -> str:
        return self._record("bootstrap_frontend_in_vm", *args, **kwargs)

    def destroy_vm(self, target: str) -> str:
        return self._record("destroy_vm", target)

    def destroy_issue_vm(self, target: str) -> str:
        return self._record("destroy_issue_vm", target)


class FakeExecutionBackend:
    def __init__(self) -> None:
        self.calls: list[CallRecord] = []

    def get_log_snapshot(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(CallRecord("get_log_snapshot", args, dict(kwargs)))
        return "log-result"

    def start(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(CallRecord("start", args, dict(kwargs)))
        return "start-result"

    def stop(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(CallRecord("stop", args, dict(kwargs)))
        return "stop-result"

    def destroy(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(CallRecord("destroy", args, dict(kwargs)))
        return "destroy-result"


class FakeWorkerAdmin:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def list_vms(self, **kwargs: Any) -> list[str]:
        self.calls.append(dict(kwargs))
        return ["vm-summary"]


@dataclass
class FakeManifest:
    directory_path: str
    issue_id: str = "local-1"
    issue_number: str = "1"
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(days=30)
    )
    metadata: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    yaml_path: str = "/tmp/local-issue.yaml"
    repo_source: str = "/tmp/repo"

    def model_copy(self, update: dict[str, Any] | None = None) -> "FakeManifest":
        payload = {
            "directory_path": self.directory_path,
            "issue_id": self.issue_id,
            "issue_number": self.issue_number,
            "expires_at": self.expires_at,
            "metadata": dict(self.metadata),
            "model": self.model,
            "yaml_path": self.yaml_path,
            "repo_source": self.repo_source,
        }
        if update:
            payload.update(update)
        return FakeManifest(**payload)


class FakeSessionsStore:
    def __init__(self, initial: dict[str, FakeManifest] | None = None) -> None:
        self._data = dict(initial or {})
        self.put_records: list[tuple[str, FakeManifest]] = []
        self.deleted: list[str] = []
        self.write_all_records: list[dict[str, FakeManifest]] = []

    def read_all(self) -> dict[str, FakeManifest]:
        return dict(self._data)

    def get(self, key: str) -> FakeManifest | None:
        return self._data.get(key)

    def get_session(self, key: str) -> FakeManifest | None:
        return self._data.get(key)

    def resolve_session_target(self, target: str) -> FakeManifest | None:
        return self._data.get(target)

    def resolve_vm_name_from_target(self, target: str) -> str | None:
        session = self._data.get(target)
        vm_id = str(getattr(session, "vm_id", "") or "").strip() if session is not None else ""
        return vm_id or target

    def list_sessions(self) -> list[FakeManifest]:
        return list(self._data.values())

    def list_sessions_for_vm(self, vm_name: str) -> list[FakeManifest]:
        return [
            session
            for session in self._data.values()
            if str(getattr(session, "vm_id", "") or "").strip() == vm_name
        ]

    def upsert_session(self, value: FakeManifest) -> None:
        session_id = str(getattr(value, "session_id", "") or "").strip()
        if not session_id:
            session_id = next(
                (key for key, existing in self._data.items() if existing is value),
                f"session-{len(self._data) + 1}",
            )
        self._data[session_id] = value

    def put(self, key: str, value: FakeManifest) -> None:
        self.put_records.append((key, value))
        self._data[key] = value

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self._data.pop(key, None)

    def write_all(self, data: dict[str, FakeManifest]) -> None:
        self.write_all_records.append(dict(data))
        self._data = dict(data)


class FakeAdmin:
    def __init__(self, *, sessions: FakeSessionsStore | None = None) -> None:
        session_repository = sessions or FakeSessionsStore()
        self.worker_admin = FakeWorkerAdmin()
        self.runtime = SimpleNamespace(
            sessions=session_repository,
            session_repository=session_repository,
            policy_config=SimpleNamespace(
                resolve_for_directory=lambda directory_path: SimpleNamespace(
                    directory_path=directory_path
                )
            ),
        )
        self.config = SimpleNamespace(
            host_agent_control_state_path=Path("/tmp/host-agent-state"),
            runtime_dir=Path("/tmp/runtime"),
            pr_map_path=Path("/tmp/pr-map.json"),
        )
        self.project_dir = Path("/tmp/project")
        self.use_privileged_helper = False
        self._privileged_helper = None
        self.local_issue_store = SimpleNamespace(get=lambda key: None)

    def _resolve_agent_model_for_session(self, session_or_id: Any) -> str:
        return "gpt-5"

    def _read_worker_global_config(self, worker_id: str) -> SimpleNamespace:
        return SimpleNamespace(config={})

    def _normalize_directory_path(self, directory_path: str | None) -> str:
        return directory_path or "root"

    def _resolve_vm_defaults(self, directory_path: str) -> SimpleNamespace:
        return SimpleNamespace(guest_environment=lambda: {})

    def _resolve_issue_setup(self, directory_path: str, *, root_dir: Path) -> SimpleNamespace:
        return SimpleNamespace(
            git_ssh_alias="agent-sandbox-git-host",
            git_root="/srv/git",
            git_host="127.0.0.1",
            git_user="git",
            post_exec_verification_prompt=None,
            enable_guest_heartbeat=False,
            guest_heartbeat_prompt="",
            issue_setup_azure_devops_pr_pat_env="AZURE_DEVOPS_PR_PAT",
        )

    def _resolve_local_issue_record(self, issue_identifier: str) -> FakeManifest:
        raise ValueError(f"Unknown local issue identifier: {issue_identifier}")

    def _resolve_local_issue_key(self, identifier: str) -> str | None:
        return identifier

    def _resolve_repo_source(self, repo_source: str) -> tuple[str, str]:
        return ("/tmp/repo", "repo")

    def _sync_repo_into_bare_remote(self, *args: Any, **kwargs: Any) -> None:
        self.synced_repo = (args, kwargs)

    def _clone_repo_into_vm(self, **kwargs: Any) -> str:
        self.last_clone_kwargs = kwargs
        return "/home/agent/repo"


def _vm_agent_dependencies(admin: FakeAdmin) -> VmAgentAdminDependencies:
    return VmAgentAdminDependencies(
        config=admin.config,
        runtime=admin.runtime,
        project_dir=admin.project_dir,
        local_issue_store=admin.local_issue_store,
        worker_admin=admin.worker_admin,
        get_privileged_helper=lambda: admin._privileged_helper,
        get_use_privileged_helper=lambda: admin.use_privileged_helper,
        normalize_directory_path=lambda directory_path: admin._normalize_directory_path(
            directory_path
        ),
        read_worker_global_config=lambda worker_id: admin._read_worker_global_config(worker_id),
        resolve_agent_model_for_session=lambda session_or_id: (
            admin._resolve_agent_model_for_session(session_or_id)
        ),
        resolve_vm_defaults=lambda directory_path: admin._resolve_vm_defaults(directory_path),
        resolve_issue_setup=lambda directory_path, **kwargs: admin._resolve_issue_setup(
            directory_path, **kwargs
        ),
        resolve_local_issue_record=lambda issue_identifier: admin._resolve_local_issue_record(
            issue_identifier
        ),
        resolve_local_issue_key=lambda identifier: admin._resolve_local_issue_key(identifier),
        resolve_repo_source=lambda repo_source: admin._resolve_repo_source(repo_source),
        sync_repo_into_bare_remote=lambda *args, **kwargs: admin._sync_repo_into_bare_remote(
            *args, **kwargs
        ),
        clone_repo_into_vm=lambda **kwargs: admin._clone_repo_into_vm(**kwargs),
    )


def test_chat_assistant_session_resolution_is_backend_aware(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = FakeAdmin()
    admin.config.runtime_dir = tmp_path
    service = VmAgentAdminService(_vm_agent_dependencies(admin))
    settings = {
        "enabled": True,
        "read_only": False,
        "default_model": "gpt-test",
        "default_profile": "operator-limited",
    }
    created_vm_names: set[str] = set()
    create_vm_calls: list[dict[str, Any]] = []
    vm_copy_calls: list[tuple[str, str, list[dict[str, Any]]]] = []
    runtime_checks: list[tuple[str, str]] = []

    def fake_dominfo_field(vm_name: str, field_name: str) -> str | None:
        if field_name == "State" and vm_name in created_vm_names:
            return "running"
        return None

    def fake_create_vm(self: VmAgentAdminService, **kwargs: Any) -> VmActionResult:
        create_vm_calls.append(dict(kwargs))
        created_vm_names.add(str(kwargs.get("vm_name") or ""))
        return VmActionResult(
            success=True,
            vm_name=str(kwargs.get("vm_name") or ""),
            action="create_vm",
            message="created",
        )

    def fake_vm_copy_files(
        self: VmAgentAdminService,
        target: str,
        *,
        files: list[dict[str, Any]],
        vm_user: str = "agent",
    ) -> dict[str, Any]:
        vm_copy_calls.append((target, vm_user, list(files)))
        return {
            "success": True,
            "action": "vm_copy_files",
            "copied_paths": [str(file.get("destination_path") or "") for file in files],
        }

    class FakeContainerBackend:
        config = GatewayConfig(runtime_dir=tmp_path, container_worker_base_url="http://worker.test")

        def __init__(self) -> None:
            self.prepared: list[tuple[str, dict[str, Any]]] = []
            self.written: list[tuple[str, list[dict[str, Any]]]] = []

        def prepare_session(self, *, session_id: str, metadata: dict[str, Any]):
            self.prepared.append((session_id, dict(metadata)))
            return SimpleNamespace(session_id=session_id)

        def write_files(self, *, session_id: str, files: list[dict[str, Any]]):
            self.written.append((session_id, list(files)))
            return SimpleNamespace(session_id=session_id, copied_count=len(files))

    fake_container_backend = FakeContainerBackend()
    monkeypatch.setattr(admin_service_module, "_dominfo_field", fake_dominfo_field)
    monkeypatch.setattr(VmAgentAdminService, "create_vm", fake_create_vm)
    monkeypatch.setattr(VmAgentAdminService, "vm_copy_files", fake_vm_copy_files)
    monkeypatch.setattr(
        VmAgentAdminService,
        "_ensure_libvirt_chat_assistant_runtime",
        lambda self, *, session_id, vm_name, settings: runtime_checks.append(
            (session_id, vm_name)
        ),
    )
    monkeypatch.setattr(
        VmAgentAdminService,
        "_container_worker_backend",
        lambda self, *, directory_path=None: fake_container_backend,
    )

    alice_libvirt = service.resolve_chat_assistant_session(
        user_id="alice@example.com",
        backend="libvirt_vm",
        assistant_scope="admin_chat",
        settings=settings,
        thread_id="thread-a",
        user_message_id="msg-a",
    )
    bob_libvirt = service.resolve_chat_assistant_session(
        user_id="bob@example.com",
        backend="libvirt_vm",
        assistant_scope="admin_chat",
        settings=settings,
        thread_id="thread-b",
        user_message_id="msg-b",
    )

    assert alice_libvirt["session_id"] == "assistant-shared"
    assert bob_libvirt["session_id"] == "assistant-shared"
    assert alice_libvirt["agent_thread_id"] == ""
    assert bob_libvirt["agent_thread_id"] == ""
    assert [call["vm_name"] for call in create_vm_calls] == ["assistant-shared"]
    assert create_vm_calls[0]["provider"] == "libvirt"
    assert create_vm_calls[0]["allow_existing_session"] is True
    assert [call[0] for call in vm_copy_calls] == ["assistant-shared", "assistant-shared"]
    assert runtime_checks == [
        ("assistant-shared", "assistant-shared"),
        ("assistant-shared", "assistant-shared"),
    ]
    assert all(call[1] == "agent" for call in vm_copy_calls)
    assert all(
        call[2][0]["destination_path"] == "/home/agent/AGENTS.md"
        and "evander Control Plane" in str(call[2][0]["content"])
        for call in vm_copy_calls
    )
    shared_session = admin.runtime.session_repository.get_session("assistant-shared")
    assert shared_session is not None
    assert shared_session.vm_provider == "libvirt"
    assert shared_session.metadata["execution_backend"] == "libvirt_vm"
    assert ToolName.ASSISTANT_POST_CHAT_REPLY in shared_session.allowed_tools
    assert {
        value["user_message_id"] for value in shared_session.metadata["admin_chat_threads"].values()
    } == {"msg-a", "msg-b"}
    assert {
        value["thread_id"] for value in shared_session.metadata["admin_chat_threads"].values()
    } == {"thread-a", "thread-b"}

    service.persist_chat_assistant_agent_thread_id(
        session_id="assistant-shared",
        agent_thread_id="thread-real-alice-1",
        backend="libvirt_vm",
        assistant_scope="admin_chat",
        thread_id="thread-a",
    )

    created_vm_names.clear()
    create_vm_calls.clear()
    reused_libvirt = service.resolve_chat_assistant_session(
        user_id="alice@example.com",
        backend="libvirt_vm",
        assistant_scope="admin_chat",
        settings=settings,
        thread_id="thread-a",
        user_message_id="msg-a2",
    )

    assert reused_libvirt["session_id"] == "assistant-shared"
    assert reused_libvirt["agent_thread_id"] == "thread-real-alice-1"
    updated_shared_session = admin.runtime.session_repository.get_session("assistant-shared")
    assert updated_shared_session is not None
    assert {
        value["user_message_id"]
        for value in updated_shared_session.metadata["admin_chat_threads"].values()
    } == {"msg-a2", "msg-b"}
    assert [call["vm_name"] for call in create_vm_calls] == ["assistant-shared"]
    assert create_vm_calls[0]["allow_existing_session"] is True
    assert [call[0] for call in vm_copy_calls] == [
        "assistant-shared",
        "assistant-shared",
        "assistant-shared",
    ]
    assert runtime_checks == [
        ("assistant-shared", "assistant-shared"),
        ("assistant-shared", "assistant-shared"),
        ("assistant-shared", "assistant-shared"),
    ]

    reset_libvirt = service.reset_chat_assistant_thread_context(
        user_id="alice@example.com",
        backend="libvirt_vm",
        assistant_scope="admin_chat",
        thread_id="thread-a-new",
    )
    assert reset_libvirt == {
        "session_id": "assistant-shared",
        "agent_thread_id": "",
        "thread_id": "thread-a-new",
    }
    stale_updates = service.persist_chat_assistant_agent_thread_id(
        session_id="assistant-shared",
        agent_thread_id="thread-real-alice-stale",
        backend="libvirt_vm",
        assistant_scope="admin_chat",
        thread_id="thread-a",
    )
    assert stale_updates == 0
    fresh_libvirt = service.resolve_chat_assistant_session(
        user_id="alice@example.com",
        backend="libvirt_vm",
        assistant_scope="admin_chat",
        settings=settings,
        thread_id="thread-a-new",
        user_message_id="msg-a-new",
    )
    assert fresh_libvirt["agent_thread_id"] == ""
    service.persist_chat_assistant_agent_thread_id(
        session_id="assistant-shared",
        agent_thread_id="thread-real-alice-2",
        backend="libvirt_vm",
        assistant_scope="admin_chat",
        thread_id="thread-a-new",
    )
    continued_libvirt = service.resolve_chat_assistant_session(
        user_id="alice@example.com",
        backend="libvirt_vm",
        assistant_scope="admin_chat",
        settings=settings,
        thread_id="thread-a-new",
        user_message_id="msg-a-new-2",
    )
    assert continued_libvirt["agent_thread_id"] == "thread-real-alice-2"

    alice_container = service.resolve_chat_assistant_session(
        user_id="alice@example.com",
        backend="container_worker_vm",
        assistant_scope="admin_chat",
        settings={**settings, "read_only": True},
        thread_id="thread-c",
        user_message_id="msg-c",
    )
    bob_container = service.resolve_chat_assistant_session(
        user_id="bob@example.com",
        backend="container_worker_vm",
        assistant_scope="admin_chat",
        settings={**settings, "read_only": True},
        thread_id="thread-d",
        user_message_id="msg-d",
    )

    assert alice_container["session_id"] != bob_container["session_id"]
    assert alice_container["agent_thread_id"] == ""
    alice_session = admin.runtime.session_repository.get_session(alice_container["session_id"])
    assert alice_session is not None
    assert alice_session.vm_provider == "container_worker_vm"
    assert alice_session.metadata["capability_profile"] == "read-only"
    assert {
        value["thread_id"] for value in alice_session.metadata["admin_chat_threads"].values()
    } == {"thread-c"}

    admin.runtime.session_repository.upsert_session(
        alice_session.model_copy(
            update={
                "metadata": {
                    **alice_session.metadata,
                    "agent_thread_id": "thread-real-alice-1",
                }
            }
        )
    )
    alice_container_reused = service.resolve_chat_assistant_session(
        user_id="alice@example.com",
        backend="container_worker_vm",
        assistant_scope="admin_chat",
        settings={**settings, "read_only": True},
        thread_id="thread-c",
        user_message_id="msg-c2",
    )

    assert alice_container_reused["session_id"] == alice_container["session_id"]
    assert alice_container_reused["agent_thread_id"] == "thread-real-alice-1"
    alice_session_reused = admin.runtime.session_repository.get_session(
        alice_container["session_id"]
    )
    assert alice_session_reused is not None
    assert {
        value["user_message_id"] for value in alice_session_reused.metadata["admin_chat_threads"].values()
    } == {"msg-c", "msg-c2"}
    assert {
        value["thread_id"] for value in alice_session_reused.metadata["admin_chat_threads"].values()
    } == {"thread-c"}
    alice_container_new_thread = service.resolve_chat_assistant_session(
        user_id="alice@example.com",
        backend="container_worker_vm",
        assistant_scope="admin_chat",
        settings={**settings, "read_only": True},
        thread_id="thread-c-new",
        user_message_id="msg-c-new",
    )
    assert alice_container_new_thread["session_id"] == alice_container["session_id"]
    assert alice_container_new_thread["agent_thread_id"] == ""
    alice_session_new_thread = admin.runtime.session_repository.get_session(
        alice_container["session_id"]
    )
    assert alice_session_new_thread is not None
    assert "agent_thread_id" not in alice_session_new_thread.metadata
    assert service._assistant_session_repo.get_mapping(
        user_id="alice@example.com",
        backend="container_worker_vm",
        assistant_scope="admin_chat",
    ) == {
        "session_id": alice_container["session_id"],
        "agent_thread_id": "",
        "thread_id": "thread-c-new",
    }
    stale_container_updates = service.persist_chat_assistant_agent_thread_id(
        session_id=alice_container["session_id"],
        agent_thread_id="thread-real-alice-stale",
        backend="container_worker_vm",
        assistant_scope="admin_chat",
        thread_id="thread-c",
    )
    assert stale_container_updates == 0
    reset_container = service.reset_chat_assistant_thread_context(
        user_id="alice@example.com",
        backend="container_worker_vm",
        assistant_scope="admin_chat",
        thread_id="thread-c-explicit-reset",
    )
    assert reset_container == {
        "session_id": alice_container["session_id"],
        "agent_thread_id": "",
        "thread_id": "thread-c-explicit-reset",
    }
    alice_session_reset = admin.runtime.session_repository.get_session(alice_container["session_id"])
    assert alice_session_reset is not None
    assert "agent_thread_id" not in alice_session_reset.metadata
    assert [item[0] for item in fake_container_backend.prepared] == [
        alice_container["session_id"],
        bob_container["session_id"],
        alice_container["session_id"],
        alice_container["session_id"],
    ]
    assert all(
        "/home/agent/.codex/config.toml"
        in [str(file.get("destination_path") or "") for file in files]
        for _session_id, files in fake_container_backend.written
    )
    assert all(
        any(
            file.get("destination_path") == "/home/agent/AGENTS.md"
            and "evander Control Plane" in str(file.get("content") or "")
            for file in files
        )
        for _session_id, files in fake_container_backend.written
    )


def test_chat_assistant_libvirt_bootstraps_codex_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = FakeAdmin()
    admin.config.runtime_dir = tmp_path
    service = VmAgentAdminService(_vm_agent_dependencies(admin))
    copied: list[tuple[str, str, list[dict[str, Any]]]] = []
    exec_calls: list[dict[str, Any]] = []
    runs: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_vm_copy_files(
        self: VmAgentAdminService,
        target: str,
        *,
        files: list[dict[str, Any]],
        vm_user: str = "agent",
    ) -> dict[str, Any]:
        copied.append((target, vm_user, list(files)))
        return {"success": True, "copied_paths": []}

    def fake_vm_exec(self: VmAgentAdminService, target: str, **kwargs: Any) -> dict[str, Any]:
        exec_calls.append({"target": target, **kwargs})
        return {"success": False, "exit_code": 127, "stdout": "", "stderr": "missing"}

    def fake_run_subprocess(cmd: list[str], **kwargs: object):
        runs.append((cmd, kwargs.get("env")))  # type: ignore[arg-type]
        return type("Completed", (), {"returncode": 0, "stdout": "bootstrapped", "stderr": ""})()

    monkeypatch.setattr(admin_service_module, "_dominfo_field", lambda vm_name, field_name: "running")
    monkeypatch.setattr(VmAgentAdminService, "vm_copy_files", fake_vm_copy_files)
    monkeypatch.setattr(VmAgentAdminService, "vm_exec", fake_vm_exec)
    monkeypatch.setattr(admin_service_module, "_run_subprocess", fake_run_subprocess)

    result = service.resolve_chat_assistant_session(
        user_id="alice@example.com",
        backend="libvirt_vm",
        assistant_scope="admin_chat",
        settings={"default_model": "gpt-test"},
        thread_id="thread-a",
        user_message_id="msg-a",
    )

    assert result["session_id"] == "assistant-shared"
    assert copied[0][0] == "assistant-shared"
    assert exec_calls[0]["target"] == "assistant-shared"
    assert runs
    bootstrap_cmd, bootstrap_env = runs[0]
    assert bootstrap_cmd[0].endswith("scripts/bootstrap_codex_vm.sh")
    assert bootstrap_cmd[1:] == ["assistant-shared", "agent"]
    assert bootstrap_env is not None
    assert bootstrap_env["AGENT_MODEL"] == "gpt-test"


def test_vm_agent_admin_service_create_vm_can_provision_existing_assistant_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = SessionManifest(
        session_id="assistant-shared",
        allowed_tools=[ToolName.ASSISTANT_POST_CHAT_REPLY],
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        metadata={"assistant_scope": "admin_chat"},
    )
    sessions = FakeSessionsStore({"assistant-shared": existing})
    admin = FakeAdmin(sessions=sessions)
    admin.config.runtime_dir = tmp_path
    service = VmAgentAdminService(_vm_agent_dependencies(admin))

    class Completed:
        returncode = 0
        stdout = "setup ok"
        stderr = ""

    monkeypatch.setattr(admin_service_module, "_dominfo_field", lambda vm_name, field_name: None)
    monkeypatch.setattr(
        admin_service_module, "_run_subprocess", lambda *args, **kwargs: Completed()
    )

    result = service.create_vm(
        provider="libvirt",
        vm_name="assistant-shared",
        session_id="assistant-shared",
        labels={"system": "chat_assistant"},
        allow_existing_session=True,
    )

    assert result.success is True
    assert result.vm_name == "assistant-shared"
    updated = sessions.get_session("assistant-shared")
    assert updated is not None
    assert updated.vm_id == "assistant-shared"
    assert updated.vm_provider == "libvirt"
    assert updated.metadata["assistant_scope"] == "admin_chat"


def test_admin_service_delegates_vm_methods_to_vm_agent_service() -> None:
    admin = object.__new__(AdminService)
    admin.vm_agent_admin = FakeVmAgentService()

    assert (
        admin.list_vms(
            include_inactive=False,
            omit_state=True,
            vm_user="root",
            directory_path="root/team",
            direct_only=True,
        )
        == "list_vms-result"
    )
    assert (
        admin.get_agent_log_snapshot("vm-1", lines=12, repo_name="repo-a", vm_user="agent")
        == "get_agent_log_snapshot-result"
    )
    assert admin.start_vm("vm-2") == "start_vm-result"
    assert admin.shutdown_vm("vm-3", timeout_seconds=10) == "shutdown_vm-result"
    assert admin.enable_agent("vm-4", vm_user="service") == "enable_agent-result"
    assert admin.disable_agent("vm-5", vm_user="service") == "disable_agent-result"
    assert admin.kill_agent("vm-6", 1234, vm_user="service") == "kill_agent-result"
    assert (
        admin.resume_agent(
            "vm-7",
            "prompt text",
            model="gpt-5",
            vm_user="service",
            dispatch_id="dispatch-1",
            resume_thread_id="thread-1",
            resume_mode="same_context",
        )
        == "resume_agent-result"
    )
    assert (
        admin.vm_exec(
            "vm-8",
            command=["bash", "-lc", "echo hi"],
            vm_user="service",
            working_directory="/work",
            environment={"X": "1"},
            stdin="input",
            timeout_seconds=4.5,
            check=False,
        )
        == "vm_exec-result"
    )
    assert (
        admin.vm_copy_files(
            "vm-9", files=[{"destination_path": "/tmp/a", "content": "x"}], vm_user="service"
        )
        == "vm_copy_files-result"
    )
    assert (
        admin.vm_delete_files(
            "vm-10", paths=["/tmp/a"], vm_user="service", recursive=True, missing_ok=False
        )
        == "vm_delete_files-result"
    )
    assert (
        admin.create_issue_vm(
            issue="1",
            session_id="session-1",
            model="gpt-5",
            vm_user="service",
            ttl_hours=24,
            discord_channels=["chan"],
            email_mailboxes=["mailbox"],
            email_folders=["folder"],
            dotnet=True,
            nuget="cache",
            mssql=True,
            mssql_init_root="/mssql",
            playwright=True,
            codex_skills=["skill"],
            directory_path="root/team",
        )
        == "create_issue_vm-result"
    )
    assert (
        admin.create_issue_vm_from_local_issue(
            "local-1",
            model="gpt-5",
            vm_user="service",
            dotnet=True,
            nuget="cache",
            mssql=True,
            mssql_init_root="/mssql",
            playwright=True,
            codex_skills=["skill"],
        )
        == "create_issue_vm_from_local_issue-result"
    )
    assert (
        admin.create_vm(
            vm_name="vm-11",
            session_id="session-2",
            labels={"team": "infra"},
            model="gpt-5",
            vm_user="service",
            ttl_hours=48,
            allowed_tools=["tool-a"],
            allowed_script_names=["script-a"],
            discord_channels=["chan"],
            email_mailboxes=["mailbox"],
            email_folders=["folder"],
            dotnet=True,
            nuget=True,
            mssql=True,
            mssql_init_root="/mssql",
            playwright=True,
            codex_skills=["skill"],
            directory_path="root/team",
        )
        == "create_vm-result"
    )
    assert (
        admin.clone_repo_into_vm(
            vm_name="vm-11",
            session_id="session-2",
            repo_source="/repo",
            clone_branch="release/1.2",
            vm_user="service",
            directory_path="root/team",
        )
        == "clone_repo_into_vm-result"
    )
    assert (
        admin.bootstrap_python_in_vm(
            vm_name="vm-11",
            session_id="session-2",
            host_checkout="/repo",
            target_dir="/home/agent/repo",
            vm_user="service",
        )
        == "bootstrap_python_in_vm-result"
    )
    assert (
        admin.bootstrap_frontend_in_vm(
            vm_name="vm-11",
            session_id="session-2",
            host_checkout="/checkout",
            install_roots=["/checkout/app"],
            vm_user="service",
        )
        == "bootstrap_frontend_in_vm-result"
    )
    assert admin.destroy_vm("vm-12") == "destroy_vm-result"
    assert admin.destroy_issue_vm("vm-13") == "destroy_issue_vm-result"

    assert admin.vm_agent_admin.calls == [
        CallRecord(
            "list_vms",
            (),
            {
                "include_inactive": False,
                "omit_state": True,
                "vm_user": "root",
                "directory_path": "root/team",
                "direct_only": True,
            },
        ),
        CallRecord(
            "get_agent_log_snapshot",
            ("vm-1",),
            {"lines": 12, "repo_name": "repo-a", "vm_user": "agent"},
        ),
        CallRecord("start_vm", ("vm-2",), {}),
        CallRecord("shutdown_vm", ("vm-3",), {"timeout_seconds": 10}),
        CallRecord("enable_agent", ("vm-4",), {"vm_user": "service"}),
        CallRecord("disable_agent", ("vm-5",), {"vm_user": "service"}),
        CallRecord("kill_agent", ("vm-6", 1234), {"vm_user": "service"}),
        CallRecord(
            "resume_agent",
            ("vm-7", "prompt text"),
            {
                "model": "gpt-5",
                "vm_user": "service",
                "dispatch_id": "dispatch-1",
                "resume_thread_id": "thread-1",
                "resume_mode": "same_context",
                "persist_thread_id": True,
                "mcp_gateway_url": None,
            },
        ),
        CallRecord(
            "vm_exec",
            ("vm-8",),
            {
                "command": ["bash", "-lc", "echo hi"],
                "vm_user": "service",
                "working_directory": "/work",
                "environment": {"X": "1"},
                "stdin": "input",
                "timeout_seconds": 4.5,
                "check": False,
            },
        ),
        CallRecord(
            "vm_copy_files",
            ("vm-9",),
            {"files": [{"destination_path": "/tmp/a", "content": "x"}], "vm_user": "service"},
        ),
        CallRecord(
            "vm_delete_files",
            ("vm-10",),
            {"paths": ["/tmp/a"], "vm_user": "service", "recursive": True, "missing_ok": False},
        ),
        CallRecord(
            "create_issue_vm",
            (),
            {
                "issue": "1",
                "session_id": "session-1",
                "model": "gpt-5",
                "vm_user": "service",
                "ttl_hours": 24,
                "discord_channels": ["chan"],
                "email_mailboxes": ["mailbox"],
                "email_folders": ["folder"],
                "dotnet": True,
                "nuget": "cache",
                "mssql": True,
                "mssql_init_root": "/mssql",
                "playwright": True,
                "codex_skills": ["skill"],
                "directory_path": "root/team",
            },
        ),
        CallRecord(
            "create_issue_vm_from_local_issue",
            ("local-1",),
            {
                "model": "gpt-5",
                "vm_user": "service",
                "dotnet": True,
                "nuget": "cache",
                "mssql": True,
                "mssql_init_root": "/mssql",
                "playwright": True,
                "codex_skills": ["skill"],
            },
        ),
        CallRecord(
            "create_vm",
            (),
            {
                "vm_name": "vm-11",
                "session_id": "session-2",
                "labels": {"team": "infra"},
                "model": "gpt-5",
                "vm_user": "service",
                "ttl_hours": 48,
                "allowed_tools": ["tool-a"],
                "tool_argument_policies": None,
                "allowed_script_names": ["script-a"],
                "discord_channels": ["chan"],
                "email_mailboxes": ["mailbox"],
                "email_folders": ["folder"],
                "dotnet": True,
                "nuget": True,
                "mssql": True,
                "mssql_init_root": "/mssql",
                "playwright": True,
                "codex_skills": ["skill"],
                "directory_path": "root/team",
                "allow_existing_session": False,
            },
        ),
        CallRecord(
            "clone_repo_into_vm",
            (),
            {
                "vm_name": "vm-11",
                "session_id": "session-2",
                "repo_source": "/repo",
                "clone_branch": "release/1.2",
                "vm_user": "service",
                "directory_path": "root/team",
                "git_author_name": None,
                "git_author_email": None,
                "git_committer_name": None,
                "git_committer_email": None,
            },
        ),
        CallRecord(
            "bootstrap_python_in_vm",
            (),
            {
                "vm_name": "vm-11",
                "session_id": "session-2",
                "host_checkout": "/repo",
                "target_dir": "/home/agent/repo",
                "vm_user": "service",
            },
        ),
        CallRecord(
            "bootstrap_frontend_in_vm",
            (),
            {
                "vm_name": "vm-11",
                "session_id": "session-2",
                "host_checkout": "/checkout",
                "install_roots": ["/checkout/app"],
                "vm_user": "service",
            },
        ),
        CallRecord("destroy_vm", ("vm-12",), {}),
        CallRecord("destroy_issue_vm", ("vm-13",), {}),
    ]


def test_admin_service_routes_phase_1_vm_operations_through_execution_backend() -> None:
    admin = object.__new__(AdminService)
    backend = FakeExecutionBackend()
    admin._execution_backend_instance = backend

    assert (
        admin.get_agent_log_snapshot("vm-1", lines=12, repo_name="repo-a", vm_user="agent")
        == "log-result"
    )
    assert admin.start_vm("vm-2") == "start-result"
    assert admin.shutdown_vm("vm-3", timeout_seconds=10) == "stop-result"
    assert admin.destroy_vm("vm-4") == "destroy-result"

    assert backend.calls == [
        CallRecord(
            "get_log_snapshot", ("vm-1",), {"lines": 12, "repo_name": "repo-a", "vm_user": "agent"}
        ),
        CallRecord("start", ("vm-2",), {}),
        CallRecord("stop", ("vm-3",), {"timeout_seconds": 10}),
        CallRecord("destroy", ("vm-4",), {}),
    ]


def test_vm_agent_admin_service_list_vms_uses_worker_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = FakeAdmin()
    service = VmAgentAdminService(_vm_agent_dependencies(admin))

    class EmptyProviderRegistry:
        def list_vms(self, **kwargs: Any) -> list[Any]:
            del kwargs
            return []

    monkeypatch.setattr(service, "_provider_registry", lambda: EmptyProviderRegistry())

    summaries = service.list_vms(
        include_inactive=False,
        omit_state=True,
        vm_user="root",
        directory_path="root/team",
        direct_only=True,
    )

    assert summaries == ["vm-summary"]
    assert admin.worker_admin.calls == [
        {
            "include_inactive": False,
            "omit_state": True,
            "vm_user": "root",
            "directory_path": "root/team",
            "direct_only": True,
        }
    ]


def test_vm_agent_admin_service_list_vms_falls_back_when_provider_listing_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = FakeAdmin()
    service = VmAgentAdminService(_vm_agent_dependencies(admin))

    class FailingProviderRegistry:
        def list_vms(self, **kwargs: Any) -> list[Any]:
            del kwargs
            raise RuntimeError("provider inventory unavailable")

    monkeypatch.setattr(service, "_provider_registry", lambda: FailingProviderRegistry())

    summaries = service.list_vms(
        include_inactive=False,
        omit_state=True,
        vm_user="root",
        directory_path="root/team",
        direct_only=True,
    )

    assert summaries == ["vm-summary"]
    assert admin.worker_admin.calls == [
        {
            "include_inactive": False,
            "omit_state": True,
            "vm_user": "root",
            "directory_path": "root/team",
            "direct_only": True,
        }
    ]


def test_libvirt_provider_skips_agent_state_probe_when_omit_state_is_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_sandbox.vm.provider import LibvirtVmProvider, VmProviderContext

    session_store = FakeSessionsStore()
    session_store.upsert_session(
        SessionManifest(
            session_id="vm-libvirt-1",
            vm_id="vm-libvirt-1",
            allowed_tools=[],
            directory_path="root",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    provider = LibvirtVmProvider(
        VmProviderContext(
            dependencies=SimpleNamespace(
                runtime=SimpleNamespace(session_repository=session_store),
                normalize_directory_path=lambda directory_path: directory_path or "root",
                direct_owned_entity_ids=lambda directory_path, kind="vm": {"vm-libvirt-1"},
                agent_control_store=SimpleNamespace(
                    status_for_vm=lambda vm_name: "enabled"
                ),
            )
        )
    )

    monkeypatch.setattr(admin_service_module, "_virsh_lines", lambda args: ["vm-libvirt-1"])
    monkeypatch.setattr(
        admin_service_module,
        "_dominfo_field",
        lambda vm_name, field_name: "running",
    )
    monkeypatch.setattr(
        admin_service_module, "_resolve_vm_ip", lambda vm_name: "10.0.0.5"
    )

    def fail_probe(*args: Any, **kwargs: Any) -> tuple[Any, ...]:
        del args, kwargs
        raise AssertionError("_probe_agent_state should not be called when omit_state is true")

    monkeypatch.setattr(admin_service_module, "_probe_agent_state", fail_probe)

    summaries = provider.list_vms(directory_path="root", direct_only=True, omit_state=True)

    assert len(summaries) == 1
    assert summaries[0].vm_name == "vm-libvirt-1"
    assert summaries[0].agent_state == "omitted"
    assert summaries[0].agent_pid is None
    assert summaries[0].dispatch_id is None

    probe_calls: list[tuple[str, str, str]] = []

    def record_probe(
        vm_name: str, ip_address: str, vm_user: str
    ) -> tuple[str, list[str], object, str, int]:
        probe_calls.append((vm_name, ip_address, vm_user))
        return (
            "running",
            ["repo-a"],
            datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc),
            "dispatch-1",
            4321,
        )

    monkeypatch.setattr(admin_service_module, "_probe_agent_state", record_probe)

    deep_summaries = provider.list_vms(directory_path="root", direct_only=True, omit_state=False)

    assert probe_calls == [("vm-libvirt-1", "10.0.0.5", "agent")]
    assert len(deep_summaries) == 1
    assert deep_summaries[0].agent_state == "running"
    assert deep_summaries[0].agent_pid == 4321
    assert deep_summaries[0].dispatch_id == "dispatch-1"


def test_vm_agent_admin_service_start_vm_uses_libvirt_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = FakeAdmin()
    service = VmAgentAdminService(_vm_agent_dependencies(admin))
    virsh_calls: list[list[str]] = []

    monkeypatch.setattr(admin_service_module, "_resolve_vm_name", lambda target: "vm-1")
    monkeypatch.setattr(
        admin_service_module, "_dominfo_field", lambda vm_name, field_name: "shut off"
    )

    class Completed:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(
        admin_service_module,
        "_run_virsh",
        lambda args: virsh_calls.append(list(args)) or Completed(),
    )

    result = service.start_vm("vm-1")

    assert result.success is True
    assert result.vm_name == "vm-1"
    assert result.action == "start"
    assert result.message == "Started VM vm-1"
    assert virsh_calls == [["start", "vm-1"]]


def test_vm_agent_admin_service_resume_agent_updates_session_and_spawns_watcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = FakeManifest(
        directory_path="root/team", metadata={"agent_thread_id": "previous-thread"}
    )
    sessions = FakeSessionsStore({"session-1": manifest})
    admin = FakeAdmin(sessions=sessions)
    service = VmAgentAdminService(_vm_agent_dependencies(admin))
    watcher_calls: list[dict[str, Any]] = []

    service._spawn_agent_completion_watcher = lambda **kwargs: watcher_calls.append(dict(kwargs))  # type: ignore[method-assign]

    class Completed:
        returncode = 0
        stdout = "thread_id=thread-123\nresume ok\n"
        stderr = ""

    monkeypatch.setattr(
        admin_service_module, "_resolve_vm_name_from_target", lambda target, sessions: "vm-1"
    )
    monkeypatch.setattr(
        admin_service_module,
        "_resolve_session_target",
        lambda target, sessions: ("session-1", manifest),
    )
    monkeypatch.setattr(
        admin_service_module, "_run_subprocess", lambda *args, **kwargs: Completed()
    )

    result = service.resume_agent("target", "Resume now", dispatch_id="dispatch-1")

    assert result.success is True
    assert result.vm_name == "vm-1"
    assert result.action == "resume_issue_agent"
    assert result.thread_id == "thread-123"
    assert result.message == "resume ok"
    assert sessions.put_records[0][0] == "session-1"
    assert sessions.put_records[0][1].metadata["agent_thread_id"] == "thread-123"
    assert watcher_calls == [
        {
            "vm_name": "vm-1",
            "dispatch_id": "dispatch-1",
            "vm_user": "agent",
            "session_id": "session-1",
            "directory_path": "root/team",
        }
    ]


def test_vm_agent_admin_service_resume_agent_can_skip_thread_id_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = FakeManifest(
        directory_path="root/team", metadata={"agent_thread_id": "previous-thread"}
    )
    sessions = FakeSessionsStore({"session-1": manifest})
    admin = FakeAdmin(sessions=sessions)
    service = VmAgentAdminService(_vm_agent_dependencies(admin))
    service._spawn_agent_completion_watcher = lambda **kwargs: None  # type: ignore[method-assign]

    class Completed:
        returncode = 0
        stdout = "thread_id=thread-123\nresume ok\n"
        stderr = ""

    monkeypatch.setattr(
        admin_service_module, "_resolve_vm_name_from_target", lambda target, sessions: "vm-1"
    )
    monkeypatch.setattr(
        admin_service_module,
        "_resolve_session_target",
        lambda target, sessions: ("session-1", manifest),
    )
    monkeypatch.setattr(
        admin_service_module, "_run_subprocess", lambda *args, **kwargs: Completed()
    )

    result = service.resume_agent(
        "target", "Resume now", dispatch_id="dispatch-1", persist_thread_id=False
    )

    assert result.success is True
    assert result.thread_id == "thread-123"
    assert sessions.put_records == []


def test_vm_agent_admin_service_resume_agent_rejects_expired_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = FakeAdmin()
    service = VmAgentAdminService(_vm_agent_dependencies(admin))
    expired_session = SimpleNamespace(
        session_id="session-1",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        directory_path="root/team",
    )

    monkeypatch.setattr(
        admin_service_module, "_resolve_vm_name_from_target", lambda target, sessions: "vm-1"
    )
    monkeypatch.setattr(
        admin_service_module,
        "_resolve_session_target",
        lambda target, sessions: ("session-1", expired_session),
    )
    monkeypatch.setattr(
        admin_service_module,
        "_run_subprocess",
        lambda *args, **kwargs: pytest.fail("resume subprocess must not run for expired sessions"),
    )

    result = service.resume_agent("target", "Resume now", dispatch_id="dispatch-1")

    assert result.success is False
    assert result.action == "resume_issue_agent"
    assert result.vm_name == "vm-1"
    assert result.message == (
        "session expired: session_id=session-1 expires_at="
        + (expired_session.expires_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"))
    )


def test_vm_agent_admin_service_create_issue_vm_uses_admin_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = FakeSessionsStore()
    admin = FakeAdmin(sessions=sessions)
    service = VmAgentAdminService(_vm_agent_dependencies(admin))

    monkeypatch.setattr(
        admin_service_module,
        "_build_issue_session_manifest",
        lambda **kwargs: FakeManifest(directory_path=kwargs["directory_path"]),
    )

    class Completed:
        returncode = 0
        stdout = "setup ok"
        stderr = ""

    monkeypatch.setattr(
        admin_service_module, "_run_subprocess", lambda *args, **kwargs: Completed()
    )
    monkeypatch.setattr(admin_service_module, "_dominfo_field", lambda vm_name, field_name: None)

    result = service.create_issue_vm(issue="7", session_id="session-1")

    assert result.success is True
    assert result.vm_name == "vm-7"
    assert result.action == "create_issue_vm"
    assert result.message == "setup ok"
    assert sessions.put_records[0][0] == "session-1"
    assert sessions.put_records[0][1].model == "gpt-5"


def test_vm_agent_admin_service_create_issue_vm_from_local_issue_uses_record_resolution_for_proxy_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = FakeSessionsStore()
    admin = FakeAdmin(sessions=sessions)
    service = VmAgentAdminService(_vm_agent_dependencies(admin))
    resolved = FakeManifest(
        directory_path="root/team",
        issue_id="root/team::azure_devops::77",
        issue_number="77",
        yaml_path="/tmp/proxy-issue-77.yaml",
    )
    resolve_calls: list[str] = []

    def fake_resolve_local_issue_record(issue_identifier: str) -> FakeManifest:
        resolve_calls.append(issue_identifier)
        if issue_identifier != "proxy:ado/77":
            raise AssertionError(f"unexpected issue identifier: {issue_identifier}")
        return resolved

    def fake_resolve_local_issue_key(identifier: str) -> str | None:
        raise AssertionError("resolve_local_issue_key should not be used for proxy identifiers")

    captured: dict[str, Any] = {}

    def fake_create_issue_vm(self: VmAgentAdminService, **kwargs: Any) -> VmActionResult:
        captured.update(kwargs)
        return VmActionResult(
            success=True,
            vm_name="vm-77",
            action="create_issue_vm",
            message="setup ok",
        )

    monkeypatch.setattr(admin, "_resolve_local_issue_record", fake_resolve_local_issue_record)
    monkeypatch.setattr(admin, "_resolve_local_issue_key", fake_resolve_local_issue_key)
    monkeypatch.setattr(VmAgentAdminService, "create_issue_vm", fake_create_issue_vm)

    result = service.create_issue_vm_from_local_issue("proxy:ado/77", model="gpt-5")

    assert result.success is True
    assert resolve_calls == ["proxy:ado/77"]
    assert captured["issue"] == resolved.issue_id
    assert captured["directory_path"] == resolved.directory_path
    assert captured["model"] == "gpt-5"


def test_vm_agent_admin_service_create_issue_vm_from_local_issue_rejects_ambiguous_proxy_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = FakeSessionsStore()
    admin = FakeAdmin(sessions=sessions)
    service = VmAgentAdminService(_vm_agent_dependencies(admin))

    def fake_resolve_local_issue_record(issue_identifier: str) -> FakeManifest:
        raise ValueError(
            "Ambiguous proxy issue identifier: proxy:ado/42. "
            "Matches: root::azure_devops::42, team::azure_devops::42. "
            "Use a canonical proxy issue identifier."
        )

    def fail_create_issue_vm(self: VmAgentAdminService, **kwargs: Any) -> VmActionResult:
        raise AssertionError("create_issue_vm should not be called for ambiguous identifiers")

    monkeypatch.setattr(admin, "_resolve_local_issue_record", fake_resolve_local_issue_record)
    monkeypatch.setattr(VmAgentAdminService, "create_issue_vm", fail_create_issue_vm)

    result = service.create_issue_vm_from_local_issue("proxy:ado/42")

    assert result.success is False
    assert result.message.startswith("Ambiguous proxy issue identifier: proxy:ado/42.")


def test_vm_agent_admin_service_create_issue_vm_from_local_issue_rejects_unknown_proxy_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = FakeSessionsStore()
    admin = FakeAdmin(sessions=sessions)
    admin.local_issue_store = SimpleNamespace(read_all=lambda: {}, get=lambda key: None)
    admin._resolve_local_issue_record = LocalIssueIdentityResolver(
        local_issue_store=admin.local_issue_store,
        read_issue_number_from_yaml=admin_service_module._read_issue_number_from_yaml,
    ).resolve_record
    service = VmAgentAdminService(_vm_agent_dependencies(admin))

    def fail_create_issue_vm(self: VmAgentAdminService, **kwargs: Any) -> VmActionResult:
        raise AssertionError("create_issue_vm should not be called for unknown identifiers")

    monkeypatch.setattr(VmAgentAdminService, "create_issue_vm", fail_create_issue_vm)

    result = service.create_issue_vm_from_local_issue("proxy:ado/999")

    assert result.success is False
    assert result.message == "Unknown local issue identifier: proxy:ado/999"
