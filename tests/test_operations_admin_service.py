from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_sandbox.admin.services.operations_service import OperationsAdminService


class RecordingFacade:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def login(self, *, directory: str = "root"):
        self.calls.append(("login", (), {"directory": directory}))
        return {"method": "login", "directory": directory}

    def list_sessions(
        self,
        *,
        include_expired: bool = False,
        expired_only: bool = False,
        directory_path: str | None = None,
        direct_only: bool = False,
    ):
        payload = {
            "include_expired": include_expired,
            "expired_only": expired_only,
            "directory_path": directory_path,
            "direct_only": direct_only,
        }
        self.calls.append(("list_sessions", (), payload))
        return payload

    def create_wake_schedule(
        self,
        *,
        session_id: str,
        prompt: str,
        resume_at=None,
        cron: str | None = None,
        resume_mode: str = "same_context",
        enabled: bool = True,
        request_id: str | None = None,
        source: str | None = "admin_cli",
        metadata: dict[str, object] | None = None,
    ):
        payload = {
            "session_id": session_id,
            "prompt": prompt,
            "resume_at": resume_at,
            "cron": cron,
            "resume_mode": resume_mode,
            "enabled": enabled,
            "request_id": request_id,
            "source": source,
            "metadata": metadata,
        }
        self.calls.append(("create_wake_schedule", (), payload))
        return payload

    def start_background_worker(self, worker_id: str):
        self.calls.append(("start_background_worker", (worker_id,), {}))
        return {"worker_id": worker_id, "action": "start"}

    def get(self, directory_path: str):
        directory = SimpleNamespace(path=directory_path)
        details = SimpleNamespace(directory=directory)
        return SimpleNamespace(ok=True, data=SimpleNamespace(details=details))

    def write_domain_config(self, directory_path: str, *, domain: str, payload: dict[str, object]):
        self.calls.append(
            ("write_domain_config", (directory_path,), {"domain": domain, "payload": payload})
        )
        return {"directory_path": directory_path, "domain": domain, "payload": payload}


class RecordingAdmin:
    def __init__(self) -> None:
        self._privileged_helper = None
        self.project_dir = Path("/tmp/project")

    def _normalize_directory_path(self, directory_path: str | None) -> str:
        return directory_path or "root"


def test_operations_admin_service_delegates_representative_methods() -> None:
    facade = RecordingFacade()
    admin = RecordingAdmin()
    service = OperationsAdminService(
        project_dir=admin.project_dir,
        get_privileged_helper=lambda: admin._privileged_helper,
        get_use_privileged_helper=lambda: False,
        set_privileged_helper=lambda helper: setattr(admin, "_privileged_helper", helper),
        teams_admin=facade,
        session_admin=facade,
        worker_admin=facade,
        directory_admin=facade,
        privileged_helper_factory=lambda **_: None,
    )

    assert service.teams_auth_login(directory="eng") == {
        "method": "login",
        "directory": "eng",
    }
    assert service.list_sessions(
        include_expired=True, directory_path="root/team", direct_only=True
    ) == {
        "include_expired": True,
        "expired_only": False,
        "directory_path": "root/team",
        "direct_only": True,
    }
    assert service.create_wake_schedule(
        session_id="session-1",
        prompt="Wake up",
        cron="0 * * * *",
        metadata={"source": "test"},
    ) == {
        "session_id": "session-1",
        "prompt": "Wake up",
        "resume_at": None,
        "cron": "0 * * * *",
        "resume_mode": "same_context",
        "enabled": True,
        "request_id": None,
        "source": "admin_cli",
        "metadata": {"source": "test"},
    }
    assert service.start_background_worker("teams-ingest") == {
        "worker_id": "teams-ingest",
        "action": "start",
    }
    assert service.write_directory_domain_config(
        "root/team", "providers", {"mode": "delegated"}
    ) == {
        "directory_path": "root/team",
        "domain": "providers",
        "payload": {"mode": "delegated"},
    }

    assert facade.calls == [
        ("login", (), {"directory": "eng"}),
        (
            "list_sessions",
            (),
            {
                "include_expired": True,
                "expired_only": False,
                "directory_path": "root/team",
                "direct_only": True,
            },
        ),
        (
            "create_wake_schedule",
            (),
            {
                "session_id": "session-1",
                "prompt": "Wake up",
                "resume_at": None,
                "cron": "0 * * * *",
                "resume_mode": "same_context",
                "enabled": True,
                "request_id": None,
                "source": "admin_cli",
                "metadata": {"source": "test"},
            },
        ),
        ("start_background_worker", ("teams-ingest",), {}),
        (
            "write_domain_config",
            ("root/team",),
            {"domain": "providers", "payload": {"mode": "delegated"}},
        ),
    ]
