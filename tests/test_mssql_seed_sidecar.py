from __future__ import annotations

import httpx
from starlette.testclient import TestClient

from evander_mssql_seed_addon.client import (
    DEFAULT_RESTORE_TIMEOUT_SECONDS,
    MssqlSeedSidecarClient,
)
from evander_mssql_seed_addon.sidecar import (
    MssqlSeedRestoreRequest,
    MssqlSeedRestoreService,
    create_app,
)


class CapturingRestoreService(MssqlSeedRestoreService):
    def __init__(self) -> None:
        super().__init__(
            admin_user="sa",
            admin_password="SecretPassw0rd!",
            sqlcmd_path="/bin/false",
        )
        self.scripts: list[str] = []

    def _run_sql_output(self, script: str) -> str:
        self.scripts.append(script)
        return (
            "AppData\t/var/opt/mssql/data/app.mdf\tD\n"
            "AppLog\t/var/opt/mssql/data/app_log.ldf\tL\n"
        )

    def _run_sql(self, script: str) -> None:
        self.scripts.append(script)


def test_sidecar_restore_generates_replace_script_and_reattaches_session_login() -> None:
    service = CapturingRestoreService()

    result = service.restore_database(
        MssqlSeedRestoreRequest(
            session_id="session-1",
            logical_name="app",
            database_name="asb_session_app_hash",
            login_name="asb_login_session",
            backup_path="/var/lib/evander/mssql-seeds/app.bak",
        )
    )

    assert result["status"] == "restored"
    restore_script = service.scripts[-1]
    assert "ALTER DATABASE [asb_session_app_hash] SET SINGLE_USER WITH ROLLBACK IMMEDIATE" in restore_script
    assert "RESTORE DATABASE [asb_session_app_hash]" in restore_script
    assert "FROM DISK = N'/var/lib/evander/mssql-seeds/app.bak'" in restore_script
    assert "MOVE N'AppData' TO N'/var/opt/mssql/data/asb_session_app_hash.mdf'" in restore_script
    assert "MOVE N'AppLog' TO N'/var/opt/mssql/data/asb_session_app_hash_log.ldf'" in restore_script
    assert "CREATE USER [asb_login_session] FOR LOGIN [asb_login_session]" in restore_script
    assert "ALTER ROLE [db_owner] ADD MEMBER [asb_login_session]" in restore_script


def test_sidecar_requires_bearer_token_for_restore_endpoint() -> None:
    service = CapturingRestoreService()
    client = TestClient(create_app(service=service, token="test-token"))
    payload = {
        "session_id": "session-1",
        "logical_name": "app",
        "database_name": "asb_session_app_hash",
        "login_name": "asb_login_session",
        "backup_path": "/var/lib/evander/mssql-seeds/app.bak",
    }

    assert client.get("/healthz").status_code == 200
    assert client.post("/v1/databases/restore", json=payload).status_code == 401

    response = client.post(
        "/v1/databases/restore",
        headers={"Authorization": "Bearer test-token"},
        json=payload,
    )

    assert response.status_code == 201
    assert response.json()["database_name"] == "asb_session_app_hash"


def test_sidecar_client_sends_configured_bearer_token(monkeypatch) -> None:
    requests: list[dict[str, object]] = []

    def handler(request):
        requests.append(
            {
                "path": request.url.path,
                "authorization": request.headers.get("authorization"),
                "payload": request.read(),
            }
        )
        return httpx.Response(201, json={"status": "restored", "file_count": 2})

    monkeypatch.setenv("SEED_TOKEN", "abc123")
    transport = httpx.MockTransport(handler)
    client = MssqlSeedSidecarClient(
        base_url="http://worker:11990",
        token_env="SEED_TOKEN",
        client=httpx.Client(transport=transport),
    )

    result = client.restore_database(
        session_id="session-1",
        logical_name="app",
        database_name="asb_session_app_hash",
        login_name="asb_login_session",
        backup_path="/seed/app.bak",
    )

    assert result == {"status": "restored", "file_count": 2}
    assert requests[0]["path"] == "/v1/databases/restore"
    assert requests[0]["authorization"] == "Bearer abc123"


def test_sidecar_client_uses_long_restore_timeout_by_default() -> None:
    client = MssqlSeedSidecarClient(
        base_url="http://worker:11990",
        token_env="SEED_TOKEN",
    )

    assert client.timeout_seconds == DEFAULT_RESTORE_TIMEOUT_SECONDS
    assert client.timeout_seconds >= 3600.0
