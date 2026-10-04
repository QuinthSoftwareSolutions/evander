from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from evander.config import GatewayConfig
from evander.directories import DirectoryService
from evander.directory_config import DirectoryConfigStore
from evander.gateway.app import create_app
from evander.models import SessionManifest
from evander.runtime import build_runtime


@pytest.fixture
def phone_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ROOT_PHONE_TOKEN", "root-token")
    monkeypatch.setenv("ALPHA_PHONE_TOKEN", "alpha-token")
    monkeypatch.setenv("BETA_PHONE_TOKEN", "beta-token")
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
        voice_media_controller_token_env="ROOT_PHONE_TOKEN",
    )
    runtime = build_runtime(config)
    directories = DirectoryService(runtime.directories)
    for name in ("phone", "alpha", "beta"):
        directories.create(parent="root", name=name)
    for session_id, directory in (
        ("default-agent", "phone"),
        ("alpha-agent", "alpha"),
        ("beta-agent", "beta"),
    ):
        runtime.session_repository.upsert_session(
            SessionManifest(
                session_id=session_id,
                directory_path=f"root/{directory}",
                allowed_tools=[],
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
    store = DirectoryConfigStore(runtime.directories, runtime_dir=tmp_path)
    for name, model in (("alpha", "gpt-realtime"), ("beta", "gpt-realtime-2")):
        store.write(
            f"root/{name}",
            domain="providers",
            payload={
                "voice_media_controller_token_env": f"{name.upper()}_PHONE_TOKEN",
                "openai_realtime_model": model,
            },
        )
    providers = {
        "inbound_phone_routes": [
            {
                "id": route_id,
                "did": number,
                "directory_path": "root/phone",
                "session_id": "default-agent",
                "route_prompt": "Delegate requests to the selected agent.",
                "startup_text": "Hello from the shared phone route.",
                "allowed_callers": ["+15550000201", "+15550000202"],
            }
            for route_id, number in (
                ("shared", "+15550000100"),
                ("second", "+15550000101"),
                ("legacy", "+15550000102"),
            )
        ],
        "inbound_phone_caller_maps": [
            {
                "did": "+15550000100",
                "route_id": "shared",
                "callers": [
                    {
                        "caller_number": "+15550000201",
                        "session_id": "alpha-agent",
                        "directory_path": "root/alpha",
                    },
                    {
                        "caller_number": "+15550000202",
                        "session_id": "beta-agent",
                        "directory_path": "root/beta",
                    },
                ],
            },
            {
                "did": "+15550000101",
                "route_id": "second",
                "callers": [
                    {
                        "caller_number": "+15550000201",
                        "session_id": "beta-agent",
                        "directory_path": "root/beta",
                    },
                ],
            },
        ],
    }
    store.write("root", domain="providers", payload=providers)
    # Reproduce a real process whose bootstrap already contains the resolved map.
    runtime = build_runtime(runtime.provider_config.resolve_for_directory("root"))
    return runtime, store, providers


def admit(
    client: TestClient,
    *,
    caller="+15550000201",
    did="+15550000100",
    channel="channel-1",
    token="root-token",
    **extra,
):
    return client.post(
        "/voice-sessions/inbound-phone-calls",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "did": did,
            "caller_number": caller,
            "asterisk_channel_id": channel,
            "bridge_call_id": f"bridge-{channel}",
            **extra,
        },
    )


@pytest.mark.parametrize(
    "caller,did,target,directory,token,model",
    [
        (
            " +1 (555) 000-0201 ",
            "15550000100",
            "alpha-agent",
            "root/alpha",
            "alpha-token",
            "gpt-realtime",
        ),
        (
            "15550000202",
            "+1 (555) 000-0100",
            "beta-agent",
            "root/beta",
            "beta-token",
            "gpt-realtime-2",
        ),
        ("+15550000201", "+15550000101", "beta-agent", "root/beta", "beta-token", "gpt-realtime-2"),
    ],
)
def test_caller_and_called_number_select_an_agent_session_and_its_provider_scope(
    phone_runtime, caller, did, target, directory, token, model
):
    runtime, _store, _providers = phone_runtime
    with TestClient(create_app(runtime)) as client:
        response = admit(client, caller=caller, did=did, token=token)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["route"]["session_id"] == target
        assert body["route"]["directory_path"] == directory
        assert body["realtime_session_config"]["model"] == model
        assert body["inbound_instructions"]["startup_text"] == "Hello from the shared phone route."
        record = runtime.voice_session_store.get_by_voice_session_id(body["voice_session_id"])
        assert (record.origin_session_id, record.directory_path) == (target, directory)
        query = client.post(
            f"/voice-sessions/{record.voice_session_id}/queries",
            json={
                "question": "Which task should I do next?",
                "request_id": "query-1",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert query.status_code == 201, query.text
        saved_query = runtime.voice_session_store.get_query(query.json()["query_id"])
        assert (saved_query.origin_session_id, saved_query.directory_path) == (target, directory)
        wake = runtime.wake_store.get_request(f"voice-query:{saved_query.query_id}")
        assert wake.session_id == target
        assert (
            admit(client, caller=caller, did=did, token=token).json()["voice_session_id"]
            == record.voice_session_id
        )
        ended = client.post(
            f"/voice-sessions/{record.voice_session_id}/bridge-result",
            json={
                "status": "ended",
                "transcript": "Caller: That task is complete.",
                "summary": "Task completed.",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert ended.status_code == 200, ended.text
        completed = runtime.voice_session_store.get_by_voice_session_id(record.voice_session_id)
        assert (completed.origin_session_id, completed.directory_path) == (target, directory)
        assert completed.transcript == "Caller: That task is complete."
    for path in (runtime.config.audit_log_path, runtime.voice_session_store.path):
        content = path.read_text()
        assert "15550000201" not in content
        assert "15550000202" not in content
        assert "15550000100" not in content


@pytest.mark.parametrize(
    "caller,extra,status",
    [
        ("+15550000299", {}, 403),
        ("+15550000299", {"route_id": "shared"}, 403),
        (None, {}, 400),
        ("anonymous", {}, 400),
        ("+15550000201", {"route_id": "legacy"}, 400),
    ],
)
def test_unassigned_withheld_and_conflicting_calls_cannot_use_the_default_session(
    phone_runtime, caller, extra, status
):
    runtime, _store, _providers = phone_runtime
    with TestClient(create_app(runtime)) as client:
        assert admit(client, caller=caller, **extra).status_code == status
    assert runtime.voice_session_store.get_by_request_id("inbound-phone:shared:channel-1") is None


@pytest.mark.parametrize(
    "failure,status",
    [
        ("missing", 404),
        ("expired", 403),
        ("wrong-directory", 403),
        ("blocked", 403),
        ("not-allowed", 403),
        ("wrong-token", 401),
    ],
)
def test_mapped_calls_still_enforce_session_scope_caller_policy_and_controller_auth(
    phone_runtime, failure, status
):
    runtime, store, providers = phone_runtime
    candidate = deepcopy(providers)
    if failure == "missing":
        candidate["inbound_phone_caller_maps"][0]["callers"][0]["session_id"] = "missing-agent"
    elif failure == "expired":
        session = runtime.session_repository.get_session("alpha-agent")
        runtime.session_repository.upsert_session(
            session.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
        )
    elif failure == "wrong-directory":
        candidate["inbound_phone_caller_maps"][0]["callers"][0]["directory_path"] = "root/beta"
    elif failure == "blocked":
        candidate["inbound_phone_routes"][0]["blocked_callers"] = ["+15550000201"]
    elif failure == "not-allowed":
        candidate["inbound_phone_routes"][0]["allowed_callers"] = ["+15550000202"]
    store.write("root", domain="providers", payload=candidate)
    with TestClient(create_app(runtime)) as client:
        response = admit(
            client, token="invalid-token" if failure == "wrong-token" else "root-token"
        )
        assert response.status_code == status, response.text
    assert runtime.voice_session_store.get_by_request_id("inbound-phone:shared:channel-1") is None


def test_live_map_edits_pin_existing_calls_and_keep_an_empty_did_closed(phone_runtime):
    runtime, store, providers = phone_runtime
    candidate = deepcopy(providers)
    with TestClient(create_app(runtime)) as client:
        original = admit(client).json()["voice_session_id"]
        candidate["inbound_phone_caller_maps"][0]["callers"][0].update(
            session_id="beta-agent", directory_path="root/beta"
        )
        store.write("root", domain="providers", payload=candidate)
        remapped = admit(client, channel="new-channel")
        assert remapped.status_code == 201
        assert remapped.json()["route"]["session_id"] == "beta-agent"
        assert (
            runtime.voice_session_store.get_by_voice_session_id(original).origin_session_id
            == "alpha-agent"
        )
        assert (
            admit(client).status_code == 400
        )  # A retry cannot change an already reserved call's origin.
        candidate["inbound_phone_caller_maps"][0]["callers"] = []
        store.write("root", domain="providers", payload=candidate)
        assert admit(client, channel="empty-map", route_id="shared").status_code == 403
        query = client.post(
            f"/voice-sessions/{original}/queries",
            json={"question": "Continue my original task."},
            headers={"Authorization": "Bearer alpha-token"},
        )
        assert query.status_code == 201
        assert (
            runtime.voice_session_store.get_query(query.json()["query_id"]).origin_session_id
            == "alpha-agent"
        )
        # Explicitly removing caller-routing enablement restores the legacy binding.
        candidate.pop("inbound_phone_caller_maps")
        store.write("root", domain="providers", payload=candidate)
        restored = admit(client, channel="restored")
        assert restored.status_code == 201
        assert restored.json()["route"]["session_id"] == "default-agent"


def test_mapped_mode_does_not_require_the_legacy_default_to_be_active_and_other_dids_are_unchanged(
    phone_runtime,
):
    runtime, _store, _providers = phone_runtime
    session = runtime.session_repository.get_session("default-agent")
    with TestClient(create_app(runtime)) as client:
        legacy = admit(client, did="+15550000102", channel="legacy")
        assert legacy.status_code == 201
        assert legacy.json()["route"]["session_id"] == "default-agent"
        runtime.session_repository.upsert_session(
            session.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
        )
        assert admit(client).status_code == 201


def test_caller_maps_are_root_only_and_duplicate_normalized_keys_are_rejected(phone_runtime):
    runtime, store, providers = phone_runtime
    with pytest.raises(ValueError, match="only in root"):
        store.write("root/alpha", domain="providers", payload={"inbound_phone_caller_maps": []})
    candidate = deepcopy(providers)
    candidate["inbound_phone_caller_maps"].append(
        {"did": "1 (555) 000-0100", "route_id": "shared", "callers": []}
    )
    with pytest.raises(ValueError, match="one map per DID"):
        GatewayConfig.model_validate(candidate)
    candidate = deepcopy(providers)
    candidate["inbound_phone_caller_maps"][0]["callers"].append(
        {
            "caller_number": "1 (555) 000-0201",
            "session_id": "beta-agent",
            "directory_path": "root/beta",
        }
    )
    with pytest.raises(ValueError, match="one target per caller"):
        GatewayConfig.model_validate(candidate)
    # Disk overlays cannot bypass the root-only store boundary either.
    path = runtime.config.runtime_dir / "directory-config/root/alpha/providers.json"
    path.write_text('{"inbound_phone_caller_maps": []}')
    with pytest.raises(ValueError, match="only in root"):
        runtime.provider_config.resolve_for_directory("root/alpha")


def test_removing_a_live_phone_route_does_not_resurrect_its_startup_binding(phone_runtime):
    runtime, store, providers = phone_runtime
    candidate = deepcopy(providers)
    candidate.pop("inbound_phone_routes")
    store.write("root", domain="providers", payload=candidate)
    with TestClient(create_app(runtime)) as client:
        assert admit(client).status_code == 404
    assert runtime.voice_session_store.get_by_request_id("inbound-phone:shared:channel-1") is None


def test_explicit_map_disambiguates_phone_routes_without_using_overlapping_allowlists(
    phone_runtime,
):
    runtime, store, providers = phone_runtime
    candidate = deepcopy(providers)
    alternate = deepcopy(candidate["inbound_phone_routes"][0])
    alternate.update(id="alternate", session_id="beta-agent", directory_path="root/beta")
    candidate["inbound_phone_routes"].append(alternate)
    store.write("root", domain="providers", payload=candidate)
    with TestClient(create_app(runtime)) as client:
        selected = admit(client)
        assert selected.status_code == 201
        assert selected.json()["route"]["route_id"] == "shared"
        assert selected.json()["route"]["session_id"] == "alpha-agent"
        assert admit(client, channel="bypass", route_id="alternate").status_code == 400


@pytest.mark.parametrize(
    "entry",
    [
        {"did": "", "route_id": "shared", "callers": []},
        {"did": "+15550000100", "route_id": "", "callers": []},
        {
            "did": "+15550000100",
            "route_id": "shared",
            "callers": [{"caller_number": "", "session_id": "alpha", "directory_path": "root"}],
        },
        {
            "did": "+15550000100",
            "route_id": "shared",
            "callers": [
                {"caller_number": "+15550000201", "session_id": "", "directory_path": "root"}
            ],
        },
        {
            "did": "+15550000100",
            "route_id": "shared",
            "callers": [
                {
                    "caller_number": "+15550000201",
                    "session_id": "alpha",
                    "directory_path": "root",
                    "extra": True,
                }
            ],
        },
    ],
)
def test_caller_map_schema_rejects_incomplete_and_unknown_fields(entry):
    with pytest.raises(ValueError):
        GatewayConfig.model_validate({"inbound_phone_caller_maps": [entry]})
