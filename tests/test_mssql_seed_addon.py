from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from evander.addons import AddonContext
from evander.config import GatewayConfig
from evander.models import SessionManifest
from evander.worker.models import WorkerMssqlDatabaseRecord
from evander_mssql_seed_addon.mcp import (
    PROVISION_DATABASE_TOOL,
    RESET_DATABASE_TOOL,
    register_mcp_tools,
)
from evander_mssql_seed_addon.state import MssqlSeedStore


class FakeServer:
    def __init__(self) -> None:
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


class FakePolicy:
    def __init__(self, session: SessionManifest) -> None:
        self.session = session
        self.required: list[tuple[str, str]] = []

    def require_tool(self, session_id: str, tool_name: str) -> SessionManifest:
        self.required.append((session_id, tool_name))
        return self.session

    def get_session(self, session_id: str) -> SessionManifest:
        assert session_id == self.session.session_id
        return self.session


class FakeProviderConfig:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config

    def resolve_for_session_manifest(self, session: SessionManifest) -> GatewayConfig:
        assert session.session_id == "session-1"
        return self.config


class FakeAudit:
    def __init__(self) -> None:
        self.events = []

    def log(self, event) -> None:
        self.events.append(event)


class FakeSessionRepository:
    def __init__(self, session: SessionManifest) -> None:
        self.session = session

    def get_session(self, session_id: str) -> SessionManifest | None:
        return self.session if session_id == self.session.session_id else None


class FakeBackend:
    calls: list[tuple[str, str]] = []

    def __init__(self, *, config: GatewayConfig) -> None:
        assert config.execution_backend == "container_worker_vm"

    def provision_mssql_database(self, *, session_id: str, logical_name: str):
        self.calls.append((session_id, logical_name))
        return WorkerMssqlDatabaseRecord(
            session_id=session_id,
            logical_name=logical_name,
            database_name="asb_session_app_hash",
            server="172.17.0.1",
            port=1433,
            username="asb_login_session",
            password="Secret!",
            connection_string="Server=tcp:172.17.0.1,1433;Password=Secret!",
        )


class FakeSidecarClient:
    calls: list[dict[str, object]] = []

    def __init__(self, *, base_url: str, token_env: str) -> None:
        assert base_url == "http://worker:11990"
        assert token_env == "SEED_TOKEN"

    def restore_database(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {"status": "restored", "file_count": 2}


def test_store_replaces_settings_and_tracks_seeded_databases(tmp_path: Path) -> None:
    store = MssqlSeedStore(tmp_path)

    store.replace_settings(
        sidecars={
            "worker-a": {
                "base_url": "http://worker:11990",
                "token_env": "SEED_TOKEN",
            }
        },
        seeds={
            "app-demo": {
                "sidecar_id": "worker-a",
                "backup_path": "/var/lib/evander/mssql-seeds/app-demo.bak",
            }
        },
    )

    state = store.read()
    assert sorted(state.sidecars) == ["worker-a"]
    assert sorted(state.seeds) == ["app-demo"]
    assert state.seeds["app-demo"].backup_path.endswith("app-demo.bak")


def test_mcp_seed_provision_uses_core_mssql_provisioning_before_restore(
    monkeypatch,
    tmp_path: Path,
) -> None:
    store = MssqlSeedStore(tmp_path)
    store.replace_settings(
        sidecars={"worker-a": {"base_url": "http://worker:11990", "token_env": "SEED_TOKEN"}},
        seeds={"app-demo": {"sidecar_id": "worker-a", "backup_path": "/seed/app-demo.bak"}},
    )
    session = SessionManifest(
        session_id="session-1",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        directory_path="root",
    )
    runtime = SimpleNamespace(
        policy=FakePolicy(session),
        provider_config=FakeProviderConfig(
            GatewayConfig(
                runtime_dir=tmp_path,
                execution_backend="container_worker_vm",
                container_worker_base_url="http://worker:11888",
                container_worker_mssql_enabled=True,
            )
        ),
        audit=FakeAudit(),
        session_repository=FakeSessionRepository(session),
    )
    server = FakeServer()

    FakeBackend.calls = []
    FakeSidecarClient.calls = []
    monkeypatch.setattr("evander_mssql_seed_addon.mcp.ContainerWorkerVmExecutionBackend", FakeBackend)
    monkeypatch.setattr("evander_mssql_seed_addon.mcp.MssqlSeedSidecarClient", FakeSidecarClient)

    register_mcp_tools(
        server,
        AddonContext(
            addon_id="mssql-seed-addon",
            runtime_dir=tmp_path,
            addon_runtime_dir=tmp_path / "addons" / "mssql-seed-addon",
            config=runtime.provider_config.config,
            service=runtime,
        ),
    )

    result = server.tools["mssql_seed_provision_database"](
        logical_name="app",
        seed_alias="app-demo",
        session_id="session-1",
    )

    assert runtime.policy.required == [("session-1", PROVISION_DATABASE_TOOL)]
    assert FakeBackend.calls == [("session-1", "app")]
    assert FakeSidecarClient.calls == [
        {
            "session_id": "session-1",
            "logical_name": "app",
            "database_name": "asb_session_app_hash",
            "login_name": "asb_login_session",
            "backup_path": "/seed/app-demo.bak",
        }
    ]
    assert result["database_name"] == "asb_session_app_hash"
    assert result["seed_alias"] == "app-demo"
    assert result["seed_restore"]["file_count"] == 2
    assert store.get_database("session-1", "app").database_name == "asb_session_app_hash"
    assert runtime.audit.events[-1].result["password"] == "[redacted]"

    reset = server.tools["mssql_seed_reset_database"](
        logical_name="app",
        session_id="session-1",
    )

    assert runtime.policy.required[-1] == ("session-1", RESET_DATABASE_TOOL)
    assert FakeBackend.calls[-1] == ("session-1", "app")
    assert FakeSidecarClient.calls[-1]["backup_path"] == "/seed/app-demo.bak"
    assert reset["seed_restore"]["reset_count"] == 1
    assert store.get_database("session-1", "app").reset_count == 1
