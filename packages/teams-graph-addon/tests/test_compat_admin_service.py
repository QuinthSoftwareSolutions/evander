from __future__ import annotations


import json


from pathlib import Path


import subprocess


from typing import Any


import evander.admin.services.teams_service as admin_teams_service


import pytest


from evander.admin.composition import (
    AdminRuntimeComponents,
    build_admin_runtime as _build_admin_runtime,
)


from evander.config import (
    TeamsIngestWorkerGlobalConfig,
)


from config_helpers import (
    canonicalize_workflow_files,
    write_runtime_config,
)


@pytest.fixture(autouse=True)
def _isolate_managed_git_authorized_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVANDER_GIT_USER", "evander-admin-tests-no-such-user")


def write_config(
    tmp_path: Path, *, enable_directory_scoped_config: bool = False, **config_overrides: Any
) -> Path:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "managed-git-repositories.json").write_text(
        json.dumps(
            {
                "schema": "evander.managed-git-repositories/v1",
                "schema_version": 1,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "repositories": {},
                "migrations": {"test-fixture": {"status": "applied"}},
            }
        ),
        encoding="utf-8",
    )
    return write_runtime_config(
        tmp_path,
        enable_directory_scoped_config=enable_directory_scoped_config,
        **config_overrides,
    )


def build_admin_runtime(runtime_dir: Path) -> AdminRuntimeComponents:
    canonicalize_workflow_files(runtime_dir.parent)
    return _build_admin_runtime(runtime_dir)


def test_teams_auth_list_channels_resolves_exact_channel_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.directory_admin.create_child(parent="root", name="example-industry")
    assert created.ok is True and created.data is not None
    directory_path = created.data.details.directory.path
    write_result = service.operations_admin.write_directory_domain_config(
        directory_path,
        "providers",
        {
            "teams_backend": "delegated_graph_api",
            "teams_delegated_identity_id": "example-teams",
            "teams_delegated_client_id": "client-123",
            "teams_delegated_tenant_id": "tenant-123",
        },
    )
    assert write_result.ok is True

    requested_team_ids: list[str] = []

    class FakeGraphClient:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def list_channels(self, *, team_id: str) -> list[dict[str, object]]:
            requested_team_ids.append(team_id)
            return [
                {
                    "id": "19:evander@thread.skype",
                    "displayName": "Evander",
                    "membershipType": "standard",
                    "webUrl": "https://teams.example/evander",
                },
                {
                    "id": "19:other@thread.skype",
                    "displayName": "General",
                    "membershipType": "standard",
                },
            ]

    monkeypatch.setattr(admin_teams_service, "TeamsGraphClient", FakeGraphClient)
    monkeypatch.setattr(
        admin_teams_service.TeamsDeviceCodeTokenProvider,
        "get_bearer_token",
        lambda self, force_refresh=False: "access-token",
    )

    result = service.operations_admin.teams_auth_list_channels(
        directory=directory_path,
        team_id="c85e4de5-e0d7-4240-8302-8bc944f2360d",
        channel_name="Evander",
    )

    assert result["directory_path"] == directory_path
    assert result["match_count"] == 1
    assert result["channels"] == [
        {
            "team_id": "c85e4de5-e0d7-4240-8302-8bc944f2360d",
            "channel_id": "19:evander@thread.skype",
            "display_name": "Evander",
            "membership_type": "standard",
            "web_url": "https://teams.example/evander",
            "allowlist_entry": "c85e4de5-e0d7-4240-8302-8bc944f2360d/19:evander@thread.skype",
        }
    ]
    assert requested_team_ids == ["c85e4de5-e0d7-4240-8302-8bc944f2360d"]


def test_teams_auth_list_channels_can_list_all_channels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(write_config(tmp_path, enable_directory_scoped_config=True))
    created = service.directory_admin.create_child(parent="root", name="example-industry")
    assert created.ok is True and created.data is not None
    directory_path = created.data.details.directory.path
    write_result = service.operations_admin.write_directory_domain_config(
        directory_path,
        "providers",
        {
            "teams_backend": "delegated_graph_api",
            "teams_delegated_identity_id": "example-teams",
            "teams_delegated_client_id": "client-123",
            "teams_delegated_tenant_id": "tenant-123",
        },
    )
    assert write_result.ok is True

    class FakeGraphClient:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def list_channels(self, *, team_id: str) -> list[dict[str, object]]:
            assert team_id == "team-123"
            return [
                {"id": "19:a@thread.skype", "displayName": "General"},
                {"id": "19:b@thread.skype", "displayName": "Evander"},
            ]

    monkeypatch.setattr(admin_teams_service, "TeamsGraphClient", FakeGraphClient)
    monkeypatch.setattr(
        admin_teams_service.TeamsDeviceCodeTokenProvider,
        "get_bearer_token",
        lambda self, force_refresh=False: "access-token",
    )

    result = service.operations_admin.teams_auth_list_channels(
        directory=directory_path,
        team_id="team-123",
        include_all=True,
    )

    assert result["channel_name"] is None
    assert result["match_count"] == 2
    assert [item["display_name"] for item in result["channels"]] == ["General", "Evander"]


def test_admin_service_reads_and_writes_teams_ingest_worker_global_config(tmp_path: Path) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    read_result = service.workflow_config_admin.read_worker_global_config("teams-ingest")
    assert read_result.exists is False
    assert read_result.path == str(tmp_path / "config" / "teams-ingest" / "config.json")
    assert (
        read_result.config["poll_interval_seconds"]
        == TeamsIngestWorkerGlobalConfig().poll_interval_seconds
    )
    assert read_result.config["directories"] == []
    assert read_result.config["backoff_seconds"] == 60.0
    assert read_result.config["max_concurrent_target_polls"] == 4
    assert read_result.config["publish_trigger_events_directly"] is True
    assert read_result.config["target_transient_backoff_initial_seconds"] == 5.0
    assert read_result.config["target_transient_backoff_max_seconds"] == 30.0

    write_result = service.workflow_config_admin.write_worker_global_config(
        "teams-ingest",
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "directories": ["root/team-a", "root/team-b"],
            "poll_interval_seconds": 15.0,
            "jitter_seconds": 2.0,
            "backoff_seconds": 90.0,
            "max_concurrent_target_polls": 2,
            "publish_trigger_events_directly": False,
            "target_transient_backoff_initial_seconds": 4.0,
            "target_transient_backoff_max_seconds": 24.0,
            "lock_path": None,
        },
    )
    assert write_result.config["directories"] == ["root/team-a", "root/team-b"]
    assert write_result.config["poll_interval_seconds"] == 15.0

    config_path = tmp_path / "config" / "teams-ingest" / "config.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["directories"] == ["root/team-a", "root/team-b"]
    assert payload["poll_interval_seconds"] == 15.0
    assert payload["backoff_seconds"] == 90.0
    assert payload["max_concurrent_target_polls"] == 2
    assert payload["publish_trigger_events_directly"] is False
    assert payload["target_transient_backoff_initial_seconds"] == 4.0
    assert payload["target_transient_backoff_max_seconds"] == 24.0

    reread = service.workflow_config_admin.read_worker_global_config("teams-ingest")
    assert reread.config["directories"] == ["root/team-a", "root/team-b"]
    assert reread.config["backoff_seconds"] == 90.0
    assert reread.config["max_concurrent_target_polls"] == 2
    assert reread.config["publish_trigger_events_directly"] is False
    assert reread.config["target_transient_backoff_initial_seconds"] == 4.0
    assert reread.config["target_transient_backoff_max_seconds"] == 24.0


def test_create_vm_sets_teams_chat_allowlist_on_session_manifest(
    tmp_path: Path, monkeypatch: Any
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.dominfo_field", lambda vm_name, field_name: None
    )
    monkeypatch.setattr(
        "evander.admin.services.vm_helpers.resolve_vm_ip", lambda vm_name: "192.168.250.10"
    )
    monkeypatch.setattr(
        "evander.admin.services.process_helpers.subprocess.run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="ok", stderr=""
        ),
    )

    result = service.vm_agent_admin.create_vm(
        vm_name="lab-vm",
        session_id="lab-session",
        teams_channel_allowlist=["team-a/channel-1"],
        teams_chat_allowlist=[" chat:chat-1 ", "chat:chat-1", "chat:chat-2"],
    )

    assert result.success is True
    manifest = service.runtime.session_repository.get_session("lab-session")
    assert manifest is not None
    assert manifest.teams_channel_allowlist == ["team-a/channel-1"]
    assert manifest.teams_chat_allowlist == ["chat:chat-1", "chat:chat-2"]
