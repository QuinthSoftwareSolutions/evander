from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from starlette.testclient import TestClient

from evander.admin.services.teams_voice_service import TeamsVoiceService
from evander.config import GatewayConfig
from evander.directories import DirectoryService, DirectoryStore
from evander.directory_config import DirectoryConfigStore
from evander.gateway.app import create_app
from evander.messaging.wake_store import WakeRequestStore
from evander.models import SessionManifest, ToolName
from evander.voice_sessions import VoiceQueryDelegator, VoiceSessionReconciler, VoiceSessionStore


TENANT = "11111111-1111-4111-8111-111111111111"
CALLER = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"


def route(session="assistant-b", caller=CALLER):
    return {
        "tenant_id": TENANT,
        "caller_user_id": caller,
        "directory_path": "root/personal",
        "origin_session_id": session,
    }


@pytest.fixture
def runtime(tmp_path):
    sessions = {
        name: SessionManifest(
            session_id=name,
            directory_path="root/personal",
            model="gpt-6-luna",
            expires_at=datetime.now(UTC) + timedelta(days=1),
            teams_user_allowlist=[f"user:{CALLER}"],
            allowed_tools=[ToolName.SUBMIT_VOICE_QUERY_RESPONSE],
        )
        for name in ("assistant-a", "assistant-b")
    }
    config = GatewayConfig(
        teams_delegated_tenant_id=TENANT, teams_call_enabled=True, teams_call_routes=[route()]
    )
    store = VoiceSessionStore(tmp_path / "voice.json")
    wakes = WakeRequestStore(tmp_path / "wake.json")
    state = SimpleNamespace(
        config=config,
        sessions=sessions,
        voice_session_store=store,
        wake_store=wakes,
        provider_config=SimpleNamespace(resolve_for_directory=lambda _: state.config),
        policy=SimpleNamespace(
            get_session=lambda key: state.sessions[key],
            require_tool=lambda *_: None,
            approval_required_for_tool=lambda *_: False,
            allowed_tool_summary_for_session=lambda _: {},
        ),
        policy_config=SimpleNamespace(
            resolved_teams_user_targets_for_session=lambda session, **_: (
                session.teams_user_allowlist
            )
        ),
        teams_service_for_session=lambda _: SimpleNamespace(),
        voice_session_reconciler=VoiceSessionReconciler(store=store, wake_store=wakes),
    )
    state.teams_voice_service = TeamsVoiceService(state)
    return state


def admit(runtime, call_id="graph-call-1", **changes):
    return runtime.teams_voice_service.admit(
        {"tenant_id": TENANT, "caller_user_id": CALLER, "graph_call_id": call_id, **changes}
    )


def test_explicit_map_selects_one_of_two_assistants_with_same_allowlist(runtime):
    admitted = admit(runtime)
    record = runtime.voice_session_store.get_by_voice_session_id(admitted["voice_session_id"])
    assert record.origin_session_id == "assistant-b"
    assert record.caller_metadata["teams_user_target"] == f"user:{CALLER}"


def test_active_binding_survives_route_change_but_next_call_uses_new_target(runtime):
    first = admit(runtime)
    runtime.config = GatewayConfig(
        teams_delegated_tenant_id=TENANT,
        teams_call_enabled=True,
        teams_call_routes=[route("assistant-a")],
    )
    assert admit(runtime)["voice_session_id"] == first["voice_session_id"]
    second = admit(runtime, "graph-call-2")
    assert (
        runtime.voice_session_store.get_by_voice_session_id(
            second["voice_session_id"]
        ).origin_session_id
        == "assistant-a"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_id": OTHER},
        {"caller_user_id": OTHER},
        {"caller_user_id": "anonymous"},
        {"origin_session_id": "assistant-a"},
    ],
)
def test_unmapped_or_caller_selected_target_is_rejected(runtime, changes):
    with pytest.raises(ValueError):
        admit(runtime, **changes)
    assert runtime.voice_session_store.list_sessions(channel="teams_call") == []


def test_map_does_not_grant_access_or_discover_an_alternate_assistant(runtime):
    runtime.sessions["assistant-b"].teams_user_allowlist = []
    with pytest.raises(ValueError, match="not authorized"):
        admit(runtime)


def test_email_permission_is_resolved_separately_from_routing(runtime):
    runtime.sessions["assistant-b"].teams_user_allowlist = ["user:person@example.test"]
    runtime.teams_service_for_session = lambda _: SimpleNamespace(
        resolve_user_id_for_reference=lambda _: CALLER
    )
    result = admit(runtime)
    record = runtime.voice_session_store.get_by_voice_session_id(result["voice_session_id"])
    assert record.origin_session_id == "assistant-b"
    assert record.caller_metadata["teams_user_target"] == "user:person@example.test"


def test_disabled_or_misbound_directory_declines(runtime):
    runtime.config = GatewayConfig(teams_call_routes=[route()])
    with pytest.raises(ValueError, match="disabled"):
        admit(runtime)
    runtime.config = GatewayConfig(
        teams_delegated_tenant_id=TENANT, teams_call_enabled=True, teams_call_routes=[route()]
    )
    runtime.sessions["assistant-b"].directory_path = "root/other"
    with pytest.raises(ValueError, match="directory"):
        admit(runtime)


def test_duplicate_normalized_mapping_keys_rejected():
    with pytest.raises(ValueError, match="one target"):
        GatewayConfig(teams_call_routes=[route(), route("assistant-a")])


def test_map_is_root_only_and_removed_routes_are_not_cached_grants(tmp_path):
    directories = DirectoryStore(tmp_path / "directories.json")
    DirectoryService(directories).create(parent="root", name="personal")
    config_store = DirectoryConfigStore(directories, runtime_dir=tmp_path)
    with pytest.raises(ValueError, match="root providers"):
        config_store.write(
            "root/personal", domain="providers", payload={"teams_call_routes": [route()]}
        )
    from evander.provider_config import ProviderConfigResolver
    from evander.storage import JsonStore

    resolver = ProviderConfigResolver(
        config=GatewayConfig(
            runtime_dir=tmp_path,
            teams_delegated_tenant_id=TENANT,
            teams_call_enabled=True,
            teams_call_routes=[route()],
        ),
        session_store=JsonStore(tmp_path / "sessions.json", SessionManifest),
        directory_store=directories,
    )
    assert resolver.resolve_for_directory("root").teams_call_routes == []
    assert resolver.resolve_for_directory("root").teams_call_enabled is False


def test_atomic_reservations_across_store_instances(tmp_path):
    def reserve(index):
        try:
            return (
                VoiceSessionStore(tmp_path / "voice.json")
                .prepare_session(
                    origin_session_id="assistant",
                    directory_path="root",
                    channel="teams_call",
                    request_id=f"call-{index}",
                    prompt_metadata={},
                    projection={},
                    max_active_sessions=4,
                )[0]
                .voice_session_id
            )
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(reserve, range(8)))
    assert len([value for value in ids if value]) == 1


def test_global_capacity_and_hangup_release(runtime):
    runtime.config = runtime.config.model_copy(update={"teams_call_max_concurrency": 1})
    first = admit(runtime)
    runtime.config = runtime.config.model_copy(
        update={
            "teams_call_routes": GatewayConfig(
                teams_call_routes=[route("assistant-a")]
            ).teams_call_routes
        }
    )
    with pytest.raises(ValueError, match="capacity"):
        admit(runtime, "graph-call-2")
    runtime.voice_session_store.update_from_bridge_payload(
        first["voice_session_id"], {"status": "ended"}
    )
    assert admit(runtime, "graph-call-2")["voice_session_id"] != first["voice_session_id"]


def test_query_retry_is_bound_to_origin_and_completed_work_not_repeated(runtime):
    voice_id = admit(runtime)["voice_session_id"]
    delegator = VoiceQueryDelegator(
        store=runtime.voice_session_store, wake_store=runtime.wake_store
    )
    first = delegator.create_query_and_wake_origin(
        voice_id, question="Prepare the report", request_id="function-1"
    )
    second = delegator.create_query_and_wake_origin(
        voice_id, question="Prepare the report", request_id="function-1"
    )
    assert first.query_id == second.query_id
    assert first.origin_session_id == "assistant-b"
    assert runtime.wake_store.get_request(first.wake_request_id).resume_mode == "same_context"
    with pytest.raises(ValueError):
        runtime.voice_session_store.complete_query(
            first.query_id, origin_session_id="assistant-a", answer="done"
        )
    runtime.voice_session_store.complete_query(
        first.query_id, origin_session_id="assistant-b", answer="prepared"
    )
    assert (
        delegator.create_query_and_wake_origin(
            voice_id, question="Prepare the report", request_id="function-1"
        ).status
        == "answered"
    )
    with pytest.raises(ValueError, match="different question"):
        delegator.create_query_and_wake_origin(
            voice_id, question="Other action", request_id="function-1"
        )


def test_terminal_events_are_deduplicated_and_reconciled_once(runtime):
    voice_id = admit(runtime)["voice_session_id"]
    payload = {
        "status": "ended",
        "transcript_events": [{"sequence": 1, "speaker": "caller", "text": "Prepare a report"}],
    }
    runtime.teams_meeting_bridge_client = SimpleNamespace(get_call=lambda _: payload.copy())
    runtime.teams_voice_service.supervise()
    record = runtime.voice_session_store.update_from_bridge_payload(
        voice_id, {**payload, "status": "active"}
    )
    assert record.status == "ended"
    assert len(record.transcript_events) == 1
    runtime.teams_voice_service.supervise()
    updated = runtime.voice_session_store.get_by_voice_session_id(voice_id)
    assert updated.reconciliation_request_id == record.reconciliation_request_id
    assert updated.reconciliation_status == "scheduled"
    assert f"Caller Teams target: user:{CALLER}" in updated.reconciliation_prompt
    with pytest.raises(ValueError, match="ended"):
        runtime.voice_session_store.create_query(voice_id, question="New action")


def test_bridge_restart_recovers_orphan_and_preserves_accepted_work(runtime):
    voice_id = admit(runtime)["voice_session_id"]
    query = VoiceQueryDelegator(
        store=runtime.voice_session_store, wake_store=runtime.wake_store
    ).create_query_and_wake_origin(voice_id, question="Prepare the report", request_id="function-1")
    runtime.voice_session_store.update_from_bridge_payload(
        voice_id, {"last_poll_at": (datetime.now(UTC) - timedelta(seconds=60)).isoformat()}
    )

    def missing(_):
        response = httpx.Response(
            404, request=httpx.Request("GET", "https://bridge.example.test/calls/missing")
        )
        response.raise_for_status()

    runtime.teams_meeting_bridge_client = SimpleNamespace(get_call=missing)
    assert runtime.teams_voice_service.supervise() == (1, 1)
    assert runtime.voice_session_store.get_by_voice_session_id(voice_id).status == "failed"
    assert runtime.wake_store.get_request(query.wake_request_id).enabled


def test_http_admission_auth_and_forbidden_session_override(tmp_path, monkeypatch):
    from evander.runtime import build_runtime

    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )
    monkeypatch.setenv("VOICE_MEDIA_CONTROLLER_TOKEN", "synthetic-controller-token")
    client = TestClient(create_app(context))
    data = {"tenant_id": TENANT, "caller_user_id": CALLER, "graph_call_id": "call"}
    assert client.post("/voice-sessions/inbound-teams-calls", json=data).status_code == 401
    response = client.post(
        "/voice-sessions/inbound-teams-calls",
        json={**data, "origin_session_id": "assistant"},
        headers={"Authorization": "Bearer synthetic-controller-token"},
    )
    assert response.status_code == 400
    assert context.voice_session_store.list_sessions(channel="teams_call") == []


def test_http_call_and_query_remain_bound_to_explicit_target(tmp_path, monkeypatch):
    from evander.runtime import build_runtime

    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )
    DirectoryService(context.directories).create(parent="root", name="personal")
    context.provider_config.config_store.write(
        "root",
        domain="providers",
        payload={
            "teams_call_enabled": True,
            "teams_call_routes": [route()],
            "teams_delegated_tenant_id": TENANT,
        },
    )
    for name in ("assistant-a", "assistant-b"):
        context.session_repository.upsert_session(
            SessionManifest(
                session_id=name,
                directory_path="root/personal",
                model="gpt-6-luna",
                expires_at=datetime.now(UTC) + timedelta(days=1),
                teams_user_allowlist=[f"user:{CALLER}"],
                allowed_tools=[ToolName.SUBMIT_VOICE_QUERY_RESPONSE],
            )
        )
    monkeypatch.setenv("VOICE_MEDIA_CONTROLLER_TOKEN", "synthetic-controller-token")
    client = TestClient(create_app(context))
    headers = {"Authorization": "Bearer synthetic-controller-token"}
    response = client.post(
        "/voice-sessions/inbound-teams-calls",
        headers=headers,
        json={
            "tenant_id": TENANT,
            "caller_user_id": CALLER,
            "graph_call_id": "graph-call-1",
        },
    )
    assert response.status_code == 200
    voice_id = response.json()["voice_session_id"]
    assert (
        context.voice_session_store.get_by_voice_session_id(voice_id).origin_session_id
        == "assistant-b"
    )
    query = client.post(
        f"/voice-sessions/{voice_id}/queries",
        headers=headers,
        json={"question": "Prepare report", "request_id": "function-1"},
    )
    replay = client.post(
        f"/voice-sessions/{voice_id}/queries",
        headers=headers,
        json={"question": "Prepare report", "request_id": "function-1"},
    )
    assert query.status_code == 201 and replay.status_code == 201
    assert query.json()["query_id"] == replay.json()["query_id"]
    assert len(context.wake_store.list_requests(session_id="assistant-b")) == 1
    assert context.wake_store.list_requests(session_id="assistant-a") == []
    ended = client.post(
        f"/voice-sessions/{voice_id}/bridge-result", headers=headers, json={"status": "ended"}
    )
    assert ended.status_code == 200
    assert ended.json()["reconciliation_status"] == "scheduled"


def test_identity_and_supervision_metadata_survive_redaction():
    from evander.voice_sessions import sanitize_voice_metadata

    timestamp = "2026-10-01T12:34:56+00:00"
    result = sanitize_voice_metadata(
        {
            "caller_user_id": CALLER,
            "last_poll_at": timestamp,
            "api_key": CALLER,
            "phone": "+15551234567",
        }
    )
    assert result["caller_user_id"] == CALLER
    assert result["last_poll_at"] == timestamp
    assert result["api_key"] == "<redacted>"
    assert result["phone"] == "<redacted>"


def test_configuration_schema_offers_named_assistants_and_root_only_routing(runtime):
    from evander.admin.web.workers_config import _directory_tab_metadata

    runtime.session_repository = SimpleNamespace(
        list_active_sessions=lambda: list(runtime.sessions.values())
    )
    runtime.sessions["assistant-b"].metadata["display_name"] = "Personal Assistant"
    service = SimpleNamespace(runtime=runtime)
    root = _directory_tab_metadata(service, "root", "people_communications")
    fields = {
        field["field_name"]: field for section in root["sections"] for field in section["fields"]
    }
    routes = fields["teams_call_routes"]
    assert routes["kind"] == "json"
    assert fields["teams_call_visual_context_enabled"]["kind"] == "bool"
    choice = next(
        item for item in routes["assistant_options"] if item["session_id"] == "assistant-b"
    )
    assert choice["name"] == "Personal Assistant"
    assert choice["directory_path"] == "root/personal"
    child = _directory_tab_metadata(service, "root/personal", "people_communications")
    child_fields = {
        field["field_name"] for section in child["sections"] for field in section["fields"]
    }
    assert "teams_call_routes" not in child_fields
    assert "teams_call_enabled" in child_fields
    assert "teams_call_visual_context_enabled" in child_fields
