from __future__ import annotations

import asyncio
import json
from io import BytesIO
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from PIL import Image
from starlette.testclient import TestClient

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.app import create_app
from agent_sandbox.events.internal import InternalEventBus, internal_event_store_path
from agent_sandbox.model_usage.models import ModelInferenceUsageEvent
from agent_sandbox.model_usage.store import ModelUsageStore
from agent_sandbox.models import SessionManifest
from truman_team_addon.api import _conversation_snapshot_events
from truman_team_addon.models import (
    AgentActionRecord,
    AgentInboxItem,
    AgentRuntime,
    AgentStatus,
    AgentVoiceProfile,
    EphemeralAssignment,
    EphemeralAssignmentState,
    GitPromotionRecord,
    ReleasePackage,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamRecordState,
    TeamTaskState,
)
from truman_team_addon.repository import TeamStateRepository


def _png_avatar_bytes(color: tuple[int, int, int] = (23, 111, 122)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (32, 24), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _write_config(tmp_path: Path, *, enable_team_addon: bool) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    runtime_dir = tmp_path / "runtime"
    config_path = tmp_path / "gateway.yaml"
    lines = [
        f'runtime_dir: "{runtime_dir}"',
        f'audit_log_path: "{runtime_dir / "audit.jsonl"}"',
        f'session_store_path: "{runtime_dir / "sessions.json"}"',
        f'approval_store_path: "{runtime_dir / "approvals.json"}"',
        f'draft_store_path: "{runtime_dir / "drafts.json"}"',
        f'email_store_path: "{runtime_dir / "emails.json"}"',
        f'discord_command_store_path: "{runtime_dir / "discord_commands.json"}"',
    ]
    if enable_team_addon:
        lines.extend(
            [
                "addon_loader:",
                "  modules:",
                '    - "truman_team_addon"',
            ]
        )
    config_path.write_text("\n".join(lines), encoding="utf-8")
    return config_path


def _client(tmp_path: Path, *, enable_team_addon: bool = True) -> TestClient:
    return TestClient(create_app(AdminService(_write_config(tmp_path, enable_team_addon=enable_team_addon))))


def _post_backend_role(
    client: TestClient,
    *,
    expected_revision: int | None = None,
    role_id: str = "project__backend",
    project_id: str = "project",
) -> dict:
    payload: dict[str, object] = {
        "id": role_id,
        "team_id": "team",
        "project_id": project_id,
        "name": "Backend",
    }
    if expected_revision is not None:
        payload["expected_revision"] = expected_revision
    response = client.post("/api/addons/team-addon/roles", json=payload)
    assert response.status_code == 201
    return response.json()


def _seed_runtime_operations_state(runtime_dir: Path) -> TeamStateRepository:
    repo = TeamStateRepository(runtime_dir)
    repo.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
    )
    repo.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
    )
    repo.create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
    )
    repo.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
    )
    repo.create_or_reprioritize_task(
        task_id="blocked-task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Blocked task",
        description="This task is blocked.",
        created_by_agent_id="lead",
        state=TeamTaskState.BLOCKED,
    )
    repo.create_or_reprioritize_task(
        task_id="dependency-task",
        owner_agent_id="backend",
        project_id="project",
        priority=2,
        title="Dependency task",
        description="This task is still open.",
        created_by_agent_id="lead",
    )
    repo.create_or_reprioritize_task(
        task_id="waiting-task",
        owner_agent_id="backend",
        project_id="project",
        priority=3,
        title="Waiting task",
        description="This task waits on another task.",
        created_by_agent_id="lead",
        dependency_task_ids=["dependency-task"],
    )
    repo.record_deliverable(
        deliverable_id="deliverable",
        task_id="waiting-task",
        producer_agent_id="backend",
        summary="Waiting task deliverable.",
        artifact_refs=["artifacts/backend.tar.gz"],
    )

    now = datetime(2026, 1, 1, tzinfo=UTC)
    lease_until = datetime(2099, 1, 1, tzinfo=UTC)
    next_wake_at = datetime(2000, 1, 1, tzinfo=UTC)

    def _mutate(state):
        state.agent_runtimes["backend"] = AgentRuntime(
            agent_id="backend",
            wake_leased_until=lease_until,
            active_dispatch_id="dispatch-backend",
            wake_reason="inbox",
            last_wake_at=now - timedelta(minutes=5),
            next_wake_at=next_wake_at,
            no_progress_count=4,
            last_progress_at=now - timedelta(minutes=10),
            updated_at=now,
        )
        state.inbox["inbox-1"] = AgentInboxItem(
            id="inbox-1",
            recipient_agent_id="backend",
            sender_agent_id="lead",
            task_id="waiting-task",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            state=TeamInboxItemState.PENDING,
            payload={"question": "Status update?"},
            created_at=now,
            updated_at=now,
        )
        state.inbox["escalation-1"] = AgentInboxItem(
            id="escalation-1",
            recipient_agent_id="backend",
            sender_agent_id="lead",
            task_id="waiting-task",
            item_type=TeamInboxItemType.ESCALATION,
            state=TeamInboxItemState.PENDING,
            payload={"reason": "blocked too long"},
            created_at=now,
            updated_at=now,
        )
        state.action_records["action-1"] = AgentActionRecord(
            id="action-1",
            agent_id="backend",
            dispatch_id="dispatch-backend",
            action={"type": "build"},
            success=False,
            error="build failed",
            created_at=now,
        )
        state.release_packages["release-1"] = ReleasePackage(
            id="release-1",
            project_id="project",
            deliverable_ids=["deliverable"],
            artifact_refs=["artifacts/backend.tar.gz"],
            feature_branches=["team/team/features/waiting-task"],
            release_branch="team/team/releases/release-1",
            canonical_branch="team/releases/release-1",
            git_promotion_record_ids=["promotion-1"],
            state=TeamRecordState.BLOCKED,
            error="manual hold",
            created_at=now,
        )
        state.git_promotions["promotion-1"] = GitPromotionRecord(
            id="promotion-1",
            project_id="project",
            actor_agent_id="backend",
            source_branch="team/team/features/waiting-task",
            target_branch="team/team/releases/release-1",
            promotion_kind="release",
            task_id="waiting-task",
            deliverable_id="deliverable",
            release_package_id="release-1",
            state="failed",
            commit_sha="abc123",
            outputs={"reason": "merge conflict"},
            error="merge conflict",
            created_at=now,
            updated_at=now,
        )
        state.ephemeral_assignments["ephemeral-failed"] = EphemeralAssignment(
            id="ephemeral-failed",
            parent_agent_id="backend",
            project_id="project",
            task_id="waiting-task",
            session_id="session-backend-ephemeral",
            title="Failed ephemeral work",
            prompt="Run the failing ephemeral task.",
            state=EphemeralAssignmentState.FAILED,
            error="spawn failed",
            created_at=now,
            updated_at=now,
        )
        state.ephemeral_assignments["ephemeral-cleanup"] = EphemeralAssignment(
            id="ephemeral-cleanup",
            parent_agent_id="backend",
            project_id="project",
            task_id="waiting-task",
            session_id="session-backend-cleanup",
            title="Cleanup ephemeral work",
            prompt="Clean up the ephemeral task.",
            state=EphemeralAssignmentState.CLEANUP_PENDING,
            created_at=now,
            updated_at=now,
        )
        return state

    repo.update(_mutate)
    return repo


class _FakeAvatarGenerator:
    model = "gpt-image-2"

    async def generate(self, *, prompt: str) -> bytes:
        assert "Backend" in prompt
        return _png_avatar_bytes((140, 60, 170))


class _FailingAvatarGenerator:
    model = "gpt-image-2"

    async def generate(self, *, prompt: str) -> bytes:
        raise RuntimeError("OpenAI image generation request failed: timeout")


def test_agent_avatar_upload_normalizes_and_serves_image(tmp_path: Path) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/agents/backend/avatar/upload",
        data={"expected_revision": str(repo.read().revision)},
        files={"file": ("avatar.png", _png_avatar_bytes(), "image/png")},
    )

    assert response.status_code == 200
    payload = response.json()
    avatar = payload["agents"]["backend"]["avatar"]
    assert avatar["source"] == "uploaded"
    assert avatar["mime_type"] == "image/webp"
    assert avatar["asset_id"].startswith("av_")
    assert len(avatar["sha256"]) == 64

    image_response = client.get(f"/api/addons/team-addon/avatars/{avatar['asset_id']}")
    assert image_response.status_code == 200
    assert image_response.headers["content-type"].startswith("image/webp")
    assert image_response.content.startswith(b"RIFF")


def test_agent_avatar_upload_rejects_non_image(tmp_path: Path) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/agents/backend/avatar/upload",
        data={"expected_revision": str(repo.read().revision)},
        files={"file": ("avatar.txt", b"not an image", "text/plain")},
    )

    assert response.status_code == 400
    assert "valid PNG, JPEG, or WebP" in response.json()["error"]


def test_agent_avatar_upload_rejects_corrupt_recognized_image(tmp_path: Path) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    client = _client(tmp_path)
    corrupt_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24

    response = client.post(
        "/api/addons/team-addon/agents/backend/avatar/upload",
        data={"expected_revision": str(repo.read().revision)},
        files={"file": ("avatar.png", corrupt_png, "image/png")},
    )

    assert response.status_code == 400
    assert "valid PNG, JPEG, or WebP" in response.json()["error"]


def test_generated_agent_avatar_preview_wraps_transport_failures(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _seed_runtime_operations_state(tmp_path / "runtime")
    monkeypatch.setattr(
        "truman_team_addon.api.openai_avatar_generator_from_config",
        lambda _config: _FailingAvatarGenerator(),
    )
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/agents/backend/avatar/previews",
        json={},
    )

    assert response.status_code == 503
    assert "OpenAI image generation request failed" in response.json()["error"]


def test_generated_agent_avatar_preview_is_accepted_with_revision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    monkeypatch.setattr(
        "truman_team_addon.api.openai_avatar_generator_from_config",
        lambda _config: _FakeAvatarGenerator(),
    )
    client = _client(tmp_path)

    preview_response = client.post(
        "/api/addons/team-addon/agents/backend/avatar/previews",
        json={"style_preset": "minimal", "prompt_note": "teal accent"},
    )

    assert preview_response.status_code == 201
    preview = preview_response.json()["preview"]
    assert preview["preview_id"].startswith("av_")
    assert preview["style_preset"] == "minimal"
    assert repo.read().agents["backend"].avatar is None
    assert client.get(preview["url"]).status_code == 200

    accept_response = client.post(
        "/api/addons/team-addon/agents/backend/avatar/accept-preview",
        json={
            "expected_revision": repo.read().revision,
            "preview_id": preview["preview_id"],
        },
    )

    assert accept_response.status_code == 200
    avatar = accept_response.json()["agents"]["backend"]["avatar"]
    assert avatar["source"] == "generated"
    assert avatar["asset_id"] == preview["preview_id"]
    assert avatar["style_preset"] == "minimal"
    assert client.get(f"/api/addons/team-addon/avatars/{avatar['asset_id']}").status_code == 200


def test_agent_avatar_accept_preview_requires_current_revision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    monkeypatch.setattr(
        "truman_team_addon.api.openai_avatar_generator_from_config",
        lambda _config: _FakeAvatarGenerator(),
    )
    client = _client(tmp_path)
    preview = client.post(
        "/api/addons/team-addon/agents/backend/avatar/previews",
        json={},
    ).json()["preview"]

    response = client.post(
        "/api/addons/team-addon/agents/backend/avatar/accept-preview",
        json={"expected_revision": repo.read().revision - 1, "preview_id": preview["preview_id"]},
    )

    assert response.status_code == 409
    assert repo.read().agents["backend"].avatar is None
    assert client.get(preview["url"]).status_code == 200


def test_openai_avatar_generator_wraps_http_transport_errors() -> None:
    from truman_team_addon.avatars import OpenAIAvatarGenerator

    async def _handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failed")

    generator = OpenAIAvatarGenerator(
        api_key="token",
        api_key_source="OPENAI_API_KEY",
        transport=httpx.MockTransport(_handler),
    )

    try:
        asyncio.run(generator.generate(prompt="avatar"))
    except RuntimeError as exc:
        assert "OpenAI image generation request failed" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_agent_avatar_delete_clears_state_and_asset(tmp_path: Path) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    client = _client(tmp_path)
    upload = client.post(
        "/api/addons/team-addon/agents/backend/avatar/upload",
        data={"expected_revision": str(repo.read().revision)},
        files={"file": ("avatar.png", _png_avatar_bytes(), "image/png")},
    ).json()
    asset_id = upload["agents"]["backend"]["avatar"]["asset_id"]

    response = client.request(
        "DELETE",
        "/api/addons/team-addon/agents/backend/avatar",
        json={"expected_revision": upload["revision"]},
    )

    assert response.status_code == 200
    assert response.json()["agents"]["backend"]["avatar"] is None
    assert client.get(f"/api/addons/team-addon/avatars/{asset_id}").status_code == 404


def test_team_addon_admin_api_conversation_dispatch_and_reply(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(tmp_path, enable_team_addon=True)
    repo = _seed_runtime_operations_state(tmp_path / "runtime")

    def _clear_backend_runtime(state):
        state.agent_runtimes.pop("backend", None)
        return state

    repo.update(_clear_backend_runtime)
    service = AdminService(config_path)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-backend",
            vm_id="vm-backend",
            directory_path="root",
            model="gpt-5",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    resume_calls: list[dict[str, object]] = []
    copy_calls: list[dict[str, object]] = []

    def _fake_resume_agent(target, prompt, **kwargs):
        resume_calls.append(
            {
                "target": target,
                "prompt": prompt,
                **kwargs,
            }
        )
        return {
            "success": True,
            "action": "resume_issue_agent",
            "vm_name": "vm-backend",
            "message": "started",
            "dispatch_id": kwargs.get("dispatch_id"),
        }

    def _fake_vm_copy_files(**kwargs):
        copy_calls.append(kwargs)
        return {
            "success": True,
            "vm_name": kwargs.get("target") or "vm-backend",
            "copied_count": len(kwargs.get("files") or []),
            "copied_paths": [
                str(file.get("destination_path"))
                for file in kwargs.get("files") or []
            ],
        }

    monkeypatch.setattr(service, "resume_agent", _fake_resume_agent)
    monkeypatch.setattr(service, "vm_copy_files", _fake_vm_copy_files)
    client = TestClient(create_app(service))

    created = client.post(
        "/api/addons/team-addon/conversations",
        json={"agent_id": "backend", "expected_revision": repo.read().revision},
    )
    assert created.status_code == 201
    conversation = created.json()["conversations"][0]
    assert conversation["agent_id"] == "backend"
    assert {agent["id"] for agent in created.json()["eligible_agents"]} >= {
        "backend",
        "lead",
        "manager",
    }

    sent = client.post(
        f"/api/addons/team-addon/conversations/{conversation['id']}/messages",
        json={
            "body": "Can you summarize the blocker?",
            "expected_revision": created.json()["revision"],
        },
    )

    assert sent.status_code == 202
    sent_payload = sent.json()
    assert sent_payload["dispatch"]["success"] is True
    assert sent_payload["dispatch"]["dispatch_id"].startswith("team-chat:")
    assert sent_payload["dispatch"]["agent_setup"]["action_contract"]["schema_version"] == 10
    assert len(copy_calls) == 2
    message = sent_payload["message"]
    assert message["sender"] == "operator"
    assert message["status"] == "running"
    assert message["dispatch_id"] == sent_payload["dispatch"]["dispatch_id"]
    assert resume_calls[0]["target"] == "session-backend"
    assert resume_calls[0]["resume_mode"] == "same_context"
    assert resume_calls[0]["dispatch_id"] == sent_payload["dispatch"]["dispatch_id"]
    assert "chat_replies" in str(resume_calls[0]["prompt"])
    assert conversation["id"] in str(resume_calls[0]["prompt"])
    assert message["id"] in str(resume_calls[0]["prompt"])

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "dispatch_id": message["dispatch_id"],
            "chat_replies": [
                {
                    "conversation_id": conversation["id"],
                    "message_id": message["id"],
                    "body": "The dependency task is still open.",
                }
            ],
        },
        dispatch_id=message["dispatch_id"],
        expected_revision=repo.read().revision,
    )
    assert result["success"] is True
    assert state.agent_runtimes["backend"].active_dispatch_id is None

    detail = client.get(
        f"/api/addons/team-addon/conversations/{conversation['id']}",
    )
    assert detail.status_code == 200
    messages = detail.json()["conversation"]["messages"]
    assert [item["sender"] for item in messages] == ["operator", "agent"]
    assert messages[0]["status"] == "replied"
    assert messages[1]["body"] == "The dependency task is still open."


def test_team_addon_conversation_stream_emits_updated_snapshots(tmp_path: Path) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")

    def _clear_backend_runtime(state):
        state.agent_runtimes.pop("backend", None)
        return state

    repo.update(_clear_backend_runtime)
    _state, conversation, _created = repo.create_or_get_conversation(
        agent_id="backend",
        created_by="operator",
    )

    class RequestStub:
        async def is_disconnected(self) -> bool:
            return False

    def _payload_from_event(event: str) -> dict:
        line = next(line for line in event.splitlines() if line.startswith("data: "))
        return json.loads(line.removeprefix("data: "))

    async def read_two_events() -> tuple[str, str]:
        stream = _conversation_snapshot_events(
            repo,
            RequestStub(),  # type: ignore[arg-type]
            conversation.id,
            poll_interval_seconds=0,
        )
        try:
            first = await asyncio.wait_for(anext(stream), timeout=1.0)
            _, message = repo.record_operator_chat_message(
                conversation_id=conversation.id,
                body="Can you summarize the blocker?",
                expected_revision=repo.read().revision,
            )
            dispatch_id = f"team-chat:{message.id}"
            repo.begin_chat_message_dispatch(
                message_id=message.id,
                dispatch_id=dispatch_id,
            )
            repo.apply_agent_action(
                action={
                    "agent_id": "backend",
                    "dispatch_id": dispatch_id,
                    "chat_replies": [
                        {
                            "conversation_id": conversation.id,
                            "message_id": message.id,
                            "body": "The dependency task is still open.",
                        }
                    ],
                },
                dispatch_id=dispatch_id,
                expected_revision=repo.read().revision,
            )
            second = await asyncio.wait_for(anext(stream), timeout=1.0)
            return first, second
        finally:
            await stream.aclose()

    first_event, second_event = asyncio.run(read_two_events())
    first_payload = _payload_from_event(first_event)
    second_payload = _payload_from_event(second_event)

    assert first_event.startswith("event: snapshot\n")
    assert first_payload["conversation"]["messages"] == []
    assert [item["sender"] for item in second_payload["conversation"]["messages"]] == [
        "operator",
        "agent",
    ]
    assert second_payload["conversation"]["messages"][1]["body"] == (
        "The dependency task is still open."
    )


def test_agent_voice_session_create_query_and_end(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(tmp_path, enable_team_addon=True)
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    repo.update_agent(
        agent_id="backend",
        voice_profile=AgentVoiceProfile(
            voice="cedar",
            speaking_style="Calm, concise, and lightly upbeat.",
        ),
        set_voice_profile=True,
        expected_revision=repo.read().revision,
    )
    service = AdminService(config_path)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-backend",
            directory_path="root",
            model="gpt-5",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    client_secret_calls: list[dict[str, object]] = []

    class _FakeRealtimeClientSecretService:
        def __init__(self, *, config) -> None:
            self.config = config

        def create_client_secret(self, **kwargs):
            client_secret_calls.append(kwargs)
            return {"value": "ek_test", "expires_at": 123}

    monkeypatch.setattr(
        "truman_team_addon.api.OpenAIRealtimeClientSecretService",
        _FakeRealtimeClientSecretService,
    )
    client = TestClient(create_app(service))

    response = client.post("/api/addons/team-addon/agents/backend/voice-sessions", json={})

    assert response.status_code == 201
    payload = response.json()
    assert payload["channel"] == "browser_agent_call"
    assert payload["status"] == "connecting"
    assert payload["client_secret"]["value"] == "ek_test"
    assert payload["realtime"]["transport"] == "webrtc_browser"
    assert payload["realtime"]["voice"] == "cedar"
    assert payload["realtime"]["speaking_style"] == "Calm, concise, and lightly upbeat."
    assert payload["projection"]["metadata"]["agent_id"] == "backend"
    assert payload["projection"]["context"]["open_tasks"][0]["title"] == "Blocked task"
    prompt = client_secret_calls[0]["instructions"]
    assert "Selected agent: backend (backend)" in prompt
    assert "Blocked task" in prompt
    assert "Status update?" in prompt
    assert "Waiting task deliverable." in prompt
    assert "Voice delivery style for this agent:" in prompt
    assert "Calm, concise, and lightly upbeat." in prompt
    assert client_secret_calls[0]["safety_identifier"] == "team-addon:backend"
    assert client_secret_calls[0]["voice"] == "cedar"
    record = service.runtime.voice_session_store.get_by_voice_session_id(
        payload["voice_session_id"]
    )
    assert record is not None
    assert record.channel == "browser_agent_call"
    assert record.origin_session_id == "session-backend"
    assert record.prompt_metadata["voice_profile"]["voice"] == "cedar"
    assert record.caller_metadata["voice_profile"]["speaking_style"] == (
        "Calm, concise, and lightly upbeat."
    )

    event_response = client.post(
        f"/api/addons/team-addon/voice-sessions/{payload['voice_session_id']}/events",
        json={
            "status": "active",
            "text": "What is blocked?",
            "speaker": "operator",
            "event_type": "browser_call.transcript",
        },
    )
    assert event_response.status_code == 200
    assert event_response.json()["transcript_events"][0]["text"] == "What is blocked?"

    query_response = client.post(
        f"/api/addons/team-addon/voice-sessions/{payload['voice_session_id']}/queries",
        json={
            "question": "Which files changed?",
            "transcript_excerpt": "operator: Which files changed?",
            "deadline_seconds": 60,
        },
    )
    assert query_response.status_code == 201
    query = query_response.json()
    assert query["status"] == "pending"
    assert service.runtime.wake_store.get_request(
        f"voice-query:{query['query_id']}"
    ) is not None

    end_response = client.post(
        f"/api/addons/team-addon/voice-sessions/{payload['voice_session_id']}/end",
        json={"status": "ended", "summary": "Operator asked about blockers."},
    )
    assert end_response.status_code == 200
    ended = end_response.json()
    assert ended["status"] == "ended"
    assert ended["reconciliation_status"] == "scheduled"
    assert service.runtime.wake_store.get_request(
        f"voice-session-reconcile:{payload['voice_session_id']}"
    ) is not None


def test_agent_voice_session_records_chat_conversation_message(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(tmp_path, enable_team_addon=True)
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    state, conversation, _created = repo.create_or_get_conversation(
        agent_id="backend",
        expected_revision=repo.read().revision,
    )
    service = AdminService(config_path)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-backend",
            directory_path="root",
            model="gpt-5",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    class _FakeRealtimeClientSecretService:
        def __init__(self, *, config) -> None:
            self.config = config

        def create_client_secret(self, **_kwargs):
            return {"value": "ek_test", "expires_at": 123}

    monkeypatch.setattr(
        "truman_team_addon.api.OpenAIRealtimeClientSecretService",
        _FakeRealtimeClientSecretService,
    )
    client = TestClient(create_app(service))

    created = client.post(
        "/api/addons/team-addon/agents/backend/voice-sessions",
        json={"conversation_id": conversation.id, "request_id": "chat-call-request-1"},
    )

    assert created.status_code == 201
    payload = created.json()
    assert payload["created"] is True
    assert payload["revision"] > state.revision
    assert payload["conversation"]["id"] == conversation.id
    assert payload["conversation_message"]["message_type"] == "voice_call"
    assert payload["conversation_message"]["status"] == "running"
    assert payload["conversation_message"]["voice_session_id"] == payload["voice_session_id"]
    record = service.runtime.voice_session_store.get_by_voice_session_id(
        payload["voice_session_id"]
    )
    assert record is not None
    assert record.caller_metadata["call_surface"] == "team_chat_tab"
    assert record.caller_metadata["conversation_id"] == conversation.id

    retry = client.post(
        "/api/addons/team-addon/agents/backend/voice-sessions",
        json={"conversation_id": conversation.id, "request_id": "chat-call-request-1"},
    )

    assert retry.status_code == 200
    retry_payload = retry.json()
    assert retry_payload["created"] is False
    assert retry_payload["voice_session_id"] == payload["voice_session_id"]
    assert retry_payload["conversation_message"]["id"] == payload["conversation_message"]["id"]
    assert retry_payload["revision"] == payload["revision"]
    reread_before_end = TeamStateRepository(tmp_path / "runtime").read()
    assert [
        message.id
        for message in reread_before_end.conversation_messages.values()
        if message.voice_session_id == payload["voice_session_id"]
    ] == [payload["conversation_message"]["id"]]

    ended = client.post(
        f"/api/addons/team-addon/voice-sessions/{payload['voice_session_id']}/end",
        json={"status": "ended", "summary": "Operator asked about blockers."},
    )

    assert ended.status_code == 200
    reread = TeamStateRepository(tmp_path / "runtime").read()
    chat_message = reread.conversation_messages[payload["conversation_message"]["id"]]
    assert chat_message.status == "ended"
    assert chat_message.metadata["summary"] == "Operator asked about blockers."
    assert reread.conversations[conversation.id].last_message_preview == "Voice call ended"
    assert [
        message.id
        for message in reread.conversation_messages.values()
        if message.voice_session_id == payload["voice_session_id"]
    ] == [payload["conversation_message"]["id"]]


def test_agent_voice_session_can_end_by_request_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(tmp_path, enable_team_addon=True)
    _seed_runtime_operations_state(tmp_path / "runtime")
    service = AdminService(config_path)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-backend",
            directory_path="root",
            model="gpt-5",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    class _FakeRealtimeClientSecretService:
        def __init__(self, *, config) -> None:
            self.config = config

        def create_client_secret(self, **_kwargs):
            return {"value": "ek_test", "expires_at": 123}

    monkeypatch.setattr(
        "truman_team_addon.api.OpenAIRealtimeClientSecretService",
        _FakeRealtimeClientSecretService,
    )
    client = TestClient(create_app(service))
    request_id = "browser-call:test-request"
    created = client.post(
        "/api/addons/team-addon/agents/backend/voice-sessions",
        json={"request_id": request_id},
    )
    assert created.status_code == 201

    ended = client.post(
        f"/api/addons/team-addon/voice-session-requests/{request_id}/end",
        json={"status": "ended", "error": "Cancelled during startup."},
    )

    assert ended.status_code == 200
    payload = ended.json()
    assert payload["request_id"] == request_id
    assert payload["status"] == "ended"
    assert payload["reconciliation_status"] == "scheduled"


def _auto_dev_squad_materialization_payload(
    *,
    duplicate_reviewer_agent_id: str | None = None,
    bad_git: bool = False,
    expected_revision: int = 0,
) -> dict[str, object]:
    git_payload: dict[str, object] = {
        "host_repo_path": "/srv/repos/auto-dev-squad",
        "bare_repo_name": "auto-dev-squad.git",
    }
    if bad_git:
        git_payload["bare_repo_path"] = "/srv/git/auto-dev-squad.git"

    return {
        "expected_revision": expected_revision,
        "team": {
            "id": "team-auto-dev-squad",
            "name": "Auto-Dev Squad",
            "manager_agent_id": "manager-auto-dev-squad",
            "manager_session_id": "session-manager-auto-dev-squad",
            "operator_communication_target_id": "slack://ops-channel",
        },
        "project": {
            "id": "project-auto-dev-squad",
            "name": "Reviewed Auto-Dev Delivery",
            "lead_agent_id": "project-lead-auto-dev-squad",
            "lead_session_id": "session-project-lead-auto-dev-squad",
            "git": git_payload,
        },
        "agents": {
            "implementer": {
                "agent_id": "implementer-auto-dev-squad",
                "session_id": "session-implementer-auto-dev-squad",
            },
            "reviewer": {
                "agent_id": (
                    duplicate_reviewer_agent_id or "reviewer-auto-dev-squad"
                ),
                "session_id": "session-reviewer-auto-dev-squad",
            },
            "release_operator": {
                "agent_id": "release-operator-auto-dev-squad",
                "session_id": "session-release-operator-auto-dev-squad",
            },
        },
    }


def test_team_addon_routes_are_available_only_when_enabled(tmp_path: Path) -> None:
    disabled = _client(tmp_path / "disabled", enable_team_addon=False)
    enabled = _client(tmp_path / "enabled", enable_team_addon=True)

    assert disabled.get("/api/addons/team-addon/state").status_code == 404
    assert disabled.get("/api/addons/team-addon/timeline").status_code == 404
    assert disabled.get("/addons/team-addon/").status_code == 404

    admin_state = enabled.get("/api/admin/state")
    assert admin_state.status_code == 200
    assert admin_state.json()["addons"] == [
        {
            "addon_id": "team-addon",
            "display_name": "Team Add-on",
            "tab_key": "addon:team-addon",
            "label": "Teams",
            "short_label": "Teams",
            "description": "Hierarchical agent-team orchestration.",
            "api_base_path": "/api/addons/team-addon",
            "ui_path": "/addons/team-addon/",
        }
    ]

    response = enabled.get("/api/addons/team-addon/state")

    assert response.status_code == 200
    state = response.json()
    assert state["revision"] == 0
    assert state["runtime_operations"]["agents"] == []
    assert state["runtime_operations"]["attention"] == []

    catalog = enabled.get("/api/addons/team-addon/templates")
    assert catalog.status_code == 200
    assert [template["id"] for template in catalog.json()["templates"]] == [
        "auto-dev-squad"
    ]

    template = enabled.get("/api/addons/team-addon/templates/auto-dev-squad")
    assert template.status_code == 200
    assert template.json()["root_role_key"] == "manager"

    missing_template = enabled.get("/api/addons/team-addon/templates/missing")
    assert missing_template.status_code == 404

    timeline = enabled.get("/api/addons/team-addon/timeline")
    assert timeline.status_code == 200
    assert timeline.json() == {
        "revision": 0,
        "derived": True,
        "audit_log": False,
        "timestampless_count": 0,
        "entries": [],
        "source": "derived_from_current_team_state",
        "description": "Derived timeline from the current TeamState snapshot.",
        "filters": {},
    }

    ui = enabled.get("/addons/team-addon/")
    assert ui.status_code == 200
    assert "data-team-addon-ui" in ui.text
    assert 'name="manager_display_name"' in ui.text
    assert 'name="lead_display_name"' in ui.text
    assert 'name="display_name"' in ui.text
    assert "Review &amp; Delivery" in ui.text
    assert 'id="review-delivery-view"' in ui.text
    assert 'id="delivery-filter-blocked"' in ui.text
    assert 'name="parent_task_id"' in ui.text
    assert ui.text.count('name="lead_session_id"') == 1
    assert 'name="review_profile_id"' in ui.text
    assert 'id="create-review-profile-form"' in ui.text
    assert 'data-team-view="governance"' in ui.text
    assert 'id="create-governance-group-form"' in ui.text
    assert 'id="create-governance-agenda-form"' in ui.text
    assert 'data-team-view="chat"' in ui.text
    assert 'id="team-chat-page"' in ui.text
    assert "data-chat-conversations-view" in ui.text
    assert 'name="bare_repo"' in ui.text
    assert 'data-options="git-providers"' in ui.text
    assert "data-agent-source-picker" in ui.text
    assert "data-id-field=" not in ui.text
    assert "Team ID <input" not in ui.text
    assert "Project ID <input" not in ui.text
    assert "Agent ID <input" not in ui.text
    assert "Task ID <input" not in ui.text
    assert "Profile ID <input" not in ui.text
    assert "Deliverable ID <input" not in ui.text
    assert "Review ID <input" not in ui.text
    assert 'id="edit-modal"' in ui.text
    assert 'id="edit-record-form"' in ui.text

    script = enabled.get("/addons/team-addon/team-ui.js")
    assert script.status_code == 200
    assert 'const API_BASE = "/api/addons/team-addon";' in script.text
    assert "renderRuntimeOperations" in script.text
    assert "runtimeOperationsView" in script.text
    assert "data-runtime-filter" in script.text
    assert "needs_attention" in script.text
    assert "due_leased_agents" in script.text
    assert "blocked_waiting_work" in script.text
    assert "ephemeral_work" in script.text
    assert "dependency_task_ids" in script.text
    assert "review_profile_id" in script.text
    assert "parseCriterionResults" in script.text
    assert "renderReviewDeliveryWorkspace" in script.text
    assert "submitReviewDecision" in script.text
    assert "bindWorkspaceReviewDecisionForms" in script.text
    assert "bindExistingAgentPickers" in script.text
    assert "setAutogeneratedValue" not in script.text
    assert "submitEditForm" in script.text
    assert "voice_profile: agent?.voice_profile || null" in script.text
    assert "elements.editModal?.contains(event.target)" in script.text
    assert "firstEditableField?.focus()" in script.text
    assert "refreshSelects({ includeEditForm = false } = {})" in script.text
    assert "elements.editForm?.contains(select)" in script.text
    assert "gitToBareRepo" in script.text
    assert "created_by_agent_id: text(data.created_by_agent_id)" in script.text
    assert "fieldTooltips" in script.text
    assert "create-task-form:created_by_agent_id" in script.text
    assert "decorateFieldTooltips" in script.text
    assert "data-dismiss-action-id" in script.text
    assert 'fetch(`${API_BASE}/costs`' in script.text
    assert "renderTeamCostCounter" in script.text
    assert "renderTeamCostBlockedNotice" in script.text
    assert "Blocked: estimated cost cap reached" in script.text
    assert "renderProjectCostCounter" in script.text
    assert "renderAgentInboxHeaderIndicators" in script.text
    assert "renderAgentInboxIndicatorIcon" in script.text
    assert "data-agent-call-agent-id" in script.text
    assert "RTCPeerConnection" in script.text
    assert "oai-events" in script.text
    assert "query_origin_agent" in script.text
    assert "cancelRequested" in script.text
    assert "agentVoiceCallIsCurrent" in script.text
    assert "Retry end" in script.text
    assert "voice-session-requests" in script.text
    assert "agent-inbox-indicator" in script.text
    assert 'direction: "down"' in script.text
    assert 'direction: "up"' in script.text
    assert "agent-inbox-icon--${escapeAttr(direction)}" in script.text
    assert "agent-inbox-bubble" in script.text
    assert "inboxReplyIntentLabel" in script.text
    assert "no reply needed" in script.text
    assert "Status requests" in script.text
    assert "Progress reports" in script.text
    assert "max_cost_usd" in script.text
    assert "cost_limit_reached" in script.text
    assert "renderGovernance" in script.text
    assert "governance_group_costs" in script.text
    assert "data-governance-agenda-handle-id" in script.text
    assert "loadConversations" in script.text
    assert "renderChat" in script.text
    assert "data-chat-message-form" in script.text
    assert 'document.addEventListener("submit"' in script.text
    assert 'closest?.("[data-chat-message-form]")' in script.text
    assert "data-agent-call-conversation-id" in script.text
    assert "syncTeamStateFromChatPayload(payload)" in script.text
    assert "new EventSource" in script.text
    assert "ensureChatStream" in script.text
    assert "/stream" in script.text
    assert "revision: state.revision,\n    conversations" in script.text

    stylesheet = enabled.get("/addons/team-addon/team-ui.css")
    assert stylesheet.status_code == 200
    assert ".team-shell" in stylesheet.text
    assert ".review-delivery-panel" in stylesheet.text
    assert ".review-delivery-view" in stylesheet.text
    assert ".delivery-filter" in stylesheet.text
    assert ".state-pill.rejected" in stylesheet.text
    assert ".state-pill.blocked" in stylesheet.text
    assert ".edit-modal" in stylesheet.text
    assert ".record-actions" in stylesheet.text
    assert ".field-tooltip" in stylesheet.text
    assert ".field-tooltip-bubble" in stylesheet.text
    assert ".cost-counter" in stylesheet.text
    assert ".cost-counter--warning" in stylesheet.text
    assert ".team-cost-blocked-notice" in stylesheet.text
    assert ".state-pill.neutral" in stylesheet.text
    assert ".agent-inbox-indicator" in stylesheet.text
    assert ".agent-inbox-icon" in stylesheet.text
    assert ".agent-inbox-popover" in stylesheet.text
    assert ".agent-inbox-bubble" in stylesheet.text
    assert ".agent-voice-call-panel" in stylesheet.text
    assert ".agent-voice-transcript" in stylesheet.text
    assert ".chat-layout" in stylesheet.text
    assert ".chat-message-bubble" in stylesheet.text
    assert ".chat-composer" in stylesheet.text

    html = enabled.get("/addons/team-addon/")
    assert html.status_code == 200
    assert 'name="max_cost_usd"' in html.text


def test_team_addon_admin_api_governance_group_flow(tmp_path: Path) -> None:
    client = _client(tmp_path)

    team = client.post(
        "/api/addons/team-addon/teams",
        json={
            "expected_revision": 0,
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
        },
    )
    assert team.status_code == 201

    group = client.post(
        "/api/addons/team-addon/governance-groups",
        json={
            "expected_revision": team.json()["revision"],
            "id": "portfolio",
            "name": "Portfolio",
            "manager_agent_id": "manager",
            "member_team_ids": ["team"],
            "goals": [{"description": "Keep the team under budget"}],
            "max_cost_usd": "10.00",
        },
    )
    assert group.status_code == 201
    state = group.json()
    assert state["governance_groups"]["portfolio"]["member_team_ids"] == ["team"]
    assert state["governance_groups"]["portfolio"]["goals"][0]["id"].startswith(
        "governance-goal-portfolio"
    )

    agenda = client.post(
        "/api/addons/team-addon/governance-agenda",
        json={
            "expected_revision": state["revision"],
            "governance_group_id": "portfolio",
            "item_type": "operator_directive",
            "priority": "high",
            "team_id": "team",
            "title": "Review team status",
            "summary": "Check whether the team needs intervention.",
        },
    )
    assert agenda.status_code == 201
    agenda_state = agenda.json()
    agenda_id = next(iter(agenda_state["governance_agenda"]))
    assert agenda_state["governance_agenda"][agenda_id]["state"] == "pending"

    handled = client.post(
        f"/api/addons/team-addon/governance-agenda/{agenda_id}/handle",
        json={"expected_revision": agenda_state["revision"]},
    )
    assert handled.status_code == 200
    assert handled.json()["governance_agenda"][agenda_id]["state"] == "handled"


def test_team_addon_admin_api_generates_missing_operator_ids(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/teams",
        json={
            "name": "Platform Team",
            "manager_session_id": "session-manager",
            "manager_display_name": "Platform Manager",
            "expected_revision": 0,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert "team-platform-team" in body["teams"]
    assert "agent-team-platform-team-platform-manager" in body["agents"]

    response = client.post(
        "/api/addons/team-addon/teams",
        json={
            "name": "Platform Team",
            "manager_session_id": "session-manager-2",
            "expected_revision": 1,
        },
    )
    assert response.status_code == 201
    assert "team-platform-team-2" in response.json()["teams"]

    response = client.post(
        "/api/addons/team-addon/projects",
        json={
            "team_id": "team-platform-team",
            "name": "Runtime Upgrade",
            "lead_session_id": "session-lead",
            "lead_display_name": "Project Lead",
            "goals": [{"description": "Ship runtime upgrade"}],
            "expected_revision": 2,
        },
    )
    assert response.status_code == 201
    body = response.json()
    project_id = "project-team-platform-team-runtime-upgrade"
    assert project_id in body["projects"]
    assert body["projects"][project_id]["goals"][0]["id"] == (
        "goal-project-team-platform-team-runtime-upgrade-ship-runtime-upgrade"
    )

    response = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "explicit-team",
            "name": "Explicit Team",
            "manager_agent_id": "explicit-manager",
            "manager_session_id": "session-explicit-manager",
            "manager_role_id": "explicit-manager-role",
            "expected_revision": 3,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert "explicit-team" in body["teams"]
    assert "explicit-manager" in body["agents"]
    assert "explicit-manager-role" in body["roles"]


def test_team_addon_admin_api_runtime_operations_smoke(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    _seed_runtime_operations_state(runtime_dir)
    client = _client(tmp_path)

    response = client.get("/api/addons/team-addon/state")
    assert response.status_code == 200
    state = response.json()

    agents = {row["id"]: row for row in state["runtime_operations"]["agents"]}
    assert {row["id"] for row in state["runtime_operations"]["agents"]} == {
        "backend",
        "lead",
        "manager",
    }
    backend = agents["backend"]
    assert backend["wake_due"] is True
    assert backend["lease_active"] is True
    assert backend["active_dispatch_id"] == "dispatch-backend"
    assert backend["wake_reason"] == "inbox"

    attention = {row["kind"]: row for row in state["runtime_operations"]["attention"]}
    assert set(attention) == {
        "blocked_task",
        "waiting_dependency_task",
        "pending_inbox_item",
        "pending_escalation",
        "failed_action_record",
        "failed_git_promotion",
        "blocked_release_package",
        "failed_ephemeral_assignment",
        "cleanup_pending_ephemeral_assignment",
    }
    assert attention["blocked_task"]["id"] == "blocked-task"
    assert attention["blocked_task"]["state"] == "blocked"
    assert attention["waiting_dependency_task"]["id"] == "waiting-task"
    assert attention["waiting_dependency_task"]["dependency_status"] == "waiting"
    assert attention["pending_inbox_item"]["inbox_item_id"] == "inbox-1"
    assert attention["pending_escalation"]["inbox_item_id"] == "escalation-1"
    assert attention["failed_action_record"]["dispatch_id"] == "dispatch-backend"
    assert attention["failed_action_record"]["error"] == "build failed"
    assert attention["failed_git_promotion"]["promotion_id"] == "promotion-1"
    assert attention["failed_git_promotion"]["release_package_id"] == "release-1"
    assert attention["blocked_release_package"]["release_package_id"] == "release-1"
    assert attention["blocked_release_package"]["state"] == "blocked"
    assert attention["failed_ephemeral_assignment"]["assignment_id"] == "ephemeral-failed"
    assert attention["failed_ephemeral_assignment"]["state"] == "failed"
    assert attention["cleanup_pending_ephemeral_assignment"]["assignment_id"] == "ephemeral-cleanup"
    assert attention["cleanup_pending_ephemeral_assignment"]["state"] == "cleanup_pending"


def test_team_addon_admin_api_costs_smoke(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    _seed_runtime_operations_state(runtime_dir)
    ModelUsageStore(runtime_dir / "model-usage.db").upsert_event(
        ModelInferenceUsageEvent(
            event_id="usage-1",
            occurred_at=datetime(2026, 6, 16, 12, 0, tzinfo=UTC),
            provider="openai",
            provider_endpoint_identifier="responses",
            model="gpt-5.2-codex",
            input_tokens=1000,
            output_tokens=500,
            total_tokens=1500,
            request_status="success",
            normalized_usage_status="available",
            raw_usage_metadata={
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "total_tokens": 1500,
                }
            },
            session_id="session-backend",
        )
    )
    client = _client(tmp_path)

    response = client.get("/api/addons/team-addon/costs?backfill=false")

    assert response.status_code == 200
    payload = response.json()
    assert payload["team_costs"]["team"]["events_count"] == 1
    assert payload["project_costs"]["project"]["events_count"] == 1
    assert payload["project_costs"]["project"]["estimated_cost"] is not None


def test_team_addon_admin_api_dismisses_failed_action_attention(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    _seed_runtime_operations_state(runtime_dir)
    client = _client(tmp_path)
    revision = client.get("/api/addons/team-addon/state").json()["revision"]

    response = client.post(
        "/api/addons/team-addon/action-records/action-1/dismiss",
        json={
            "dismissed_by": "operator",
            "dismissed_reason": "Superseded by follow-up wake.",
            "expected_revision": revision,
        },
    )

    assert response.status_code == 200
    state = response.json()
    record = state["action_records"]["action-1"]
    assert record["success"] is False
    assert record["dismissed_by"] == "operator"
    assert record["dismissed_reason"] == "Superseded by follow-up wake."
    assert record["dismissed_at"]
    assert all(
        row["kind"] != "failed_action_record"
        for row in state["runtime_operations"]["attention"]
    )


def test_team_addon_timeline_route_filters_current_state(tmp_path: Path) -> None:
    client = _client(tmp_path)

    client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    )
    client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "expected_revision": 1,
        },
    )
    _post_backend_role(client, expected_revision=2)
    client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role_id": "project__backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "expected_revision": 3,
        },
    )
    response = client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "task",
            "owner_agent_id": "backend",
            "project_id": "project",
            "priority": 10,
            "title": "Build backend",
            "description": "Implement the backend task.",
            "created_by_agent_id": "lead",
            "state": "accepted",
            "expected_revision": 4,
        },
    )
    assert response.status_code == 201
    response = client.patch(
        "/api/addons/team-addon/tasks/task",
        json={
            "title": "Build backend v2",
            "expected_revision": 5,
        },
    )
    assert response.status_code == 200
    response = client.post(
        "/api/addons/team-addon/deliverables",
        json={
            "id": "deliverable",
            "task_id": "task",
            "producer_agent_id": "backend",
            "summary": "Backend task is ready.",
            "expected_revision": 6,
        },
    )
    assert response.status_code == 201
    response = client.post(
        "/api/addons/team-addon/review-requests",
        json={
            "id": "review",
            "deliverable_id": "deliverable",
            "reviewer_agent_id": "lead",
            "state": "accepted",
            "decision_at": "2026-01-01T12:00:00",
            "expected_revision": 7,
        },
    )
    assert response.status_code == 201

    timeline = client.get(
        "/api/addons/team-addon/timeline",
        params=[
            ("type", "task"),
            ("type", "review"),
            ("status", "accepted"),
            ("attention_needed", "false"),
            ("agent_id", "lead"),
        ],
    )

    assert timeline.status_code == 200
    payload = timeline.json()
    assert payload["derived"] is True
    assert payload["audit_log"] is False
    assert payload["source"] == "derived_from_current_team_state"
    assert payload["filters"] == {
        "agent_id": "lead",
        "type": ["task", "review"],
        "status": ["accepted"],
        "attention_needed": False,
    }
    assert payload["timestampless_count"] == 0
    assert {entry["id"] for entry in payload["entries"]} == {
        "task:task:created",
        "task:task:updated",
        "review:review:created",
        "review:review:decision",
    }
    assert all(entry["status"] == "accepted" for entry in payload["entries"])
    assert all(entry["attention_needed"] is False for entry in payload["entries"])


def test_team_addon_admin_api_create_flow(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "manager_display_name": "  Team Manager  ",
            "expected_revision": 0,
        },
    )
    assert response.status_code == 201
    assert response.json()["revision"] == 1
    assert response.json()["agents"]["manager"]["display_name"] == "Team Manager"

    response = client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "lead_display_name": "  Project Lead  ",
            "goals": [{"id": "goal", "description": "Ship reviewed work"}],
            "expected_revision": 1,
        },
    )
    assert response.status_code == 201
    assert response.json()["revision"] == 2
    assert response.json()["agents"]["lead"]["display_name"] == "Project Lead"

    _post_backend_role(client, expected_revision=2)

    response = client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role_id": "project__backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "display_name": "  Backend Agent  ",
            "ephemeral_policy": {"max_concurrent": 2},
            "expected_revision": 3,
        },
    )
    assert response.status_code == 201
    assert response.json()["revision"] == 4
    assert response.json()["agents"]["backend"]["display_name"] == "Backend Agent"

    response = client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "task",
            "owner_agent_id": "backend",
            "project_id": "project",
            "priority": 10,
            "title": "Build backend",
            "description": "Implement the backend task.",
            "created_by_agent_id": "lead",
            "expected_revision": 4,
        },
    )
    assert response.status_code == 201
    assert response.json()["tasks"]["task"]["state"] == "pending"

    response = client.patch(
        "/api/addons/team-addon/tasks/task",
        json={
            "state": "waiting_for_review",
            "expected_revision": 5,
        },
    )
    assert response.status_code == 200
    assert response.json()["tasks"]["task"]["state"] == "waiting_for_review"

    response = client.post(
        "/api/addons/team-addon/deliverables",
        json={
            "id": "deliverable",
            "task_id": "task",
            "producer_agent_id": "backend",
            "summary": "Backend task is ready.",
            "artifact_refs": ["artifacts/backend.tar.gz"],
            "expected_revision": 6,
        },
    )
    assert response.status_code == 201
    assert response.json()["tasks"]["task"]["deliverable_ids"] == ["deliverable"]

    response = client.post(
        "/api/addons/team-addon/review-requests",
        json={
            "id": "review",
            "deliverable_id": "deliverable",
            "reviewer_agent_id": "lead",
            "expected_revision": 7,
        },
    )
    assert response.status_code == 201
    state = response.json()
    assert state["revision"] == 8
    assert state["deliverables"]["deliverable"]["review_request_ids"] == ["review"]
    assert "runtime_operations" in state
    assert {row["id"] for row in state["runtime_operations"]["agents"]} == {
        "backend",
        "lead",
        "manager",
    }


def test_team_addon_admin_api_create_task_defaults_creator_to_operator(tmp_path: Path) -> None:
    client = _client(tmp_path)

    client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    )
    client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "expected_revision": 1,
        },
    )
    _post_backend_role(client, expected_revision=2)
    client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role_id": "project__backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "expected_revision": 3,
        },
    )

    response = client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "task",
            "owner_agent_id": "backend",
            "project_id": "project",
            "priority": 10,
            "title": "Build backend",
            "description": "Implement the backend task.",
            "expected_revision": 4,
        },
    )

    assert response.status_code == 201
    task = response.json()["tasks"]["task"]
    assert task["created_by_agent_id"] is None
    assert task["created_by_actor"] == "operator:admin"

    timeline = client.get("/api/addons/team-addon/timeline?type=task").json()
    created = next(
        entry for entry in timeline["entries"] if entry["id"] == "task:task:created"
    )
    assert created["actor_agent_id"] is None
    assert created["actor_subject"] == "operator:admin"


def test_team_addon_admin_api_edits_core_records(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    )
    assert response.status_code == 201
    response = client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "expected_revision": 1,
        },
    )
    assert response.status_code == 201
    _post_backend_role(client, expected_revision=2)
    response = client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role_id": "project__backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "expected_revision": 3,
        },
    )
    assert response.status_code == 201

    response = client.patch(
        "/api/addons/team-addon/teams/team",
        json={
            "name": "Renamed Team",
            "operator_communication_target_id": None,
            "archived": True,
            "expected_revision": 4,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["revision"] == 5
    assert body["teams"]["team"]["name"] == "Renamed Team"
    assert body["teams"]["team"]["operator_communication_target_id"] is None
    assert body["teams"]["team"]["archived"] is True
    assert body["agents"]["manager"]["status"] == "archived"
    assert body["agents"]["lead"]["status"] == "archived"
    assert body["agents"]["backend"]["status"] == "archived"

    response = client.patch(
        "/api/addons/team-addon/projects/project",
        json={
            "name": "Renamed Project",
            "status": "blocked",
            "goals": [{"id": "goal", "description": "Updated goal"}],
            "git": {
                "host_repo_path": "/srv/repo",
                "bare_repo": "/srv/git/repo.git",
                "provider": "github",
            },
            "expected_revision": 5,
        },
    )
    assert response.status_code == 200
    body = response.json()
    project = body["projects"]["project"]
    assert body["revision"] == 6
    assert project["name"] == "Renamed Project"
    assert project["status"] == "blocked"
    assert project["goals"][0]["id"] == "goal"
    assert project["git"]["bare_repo_path"] == "/srv/git/repo.git"
    assert project["git"]["provider"] == "github"

    response = client.post(
        "/api/addons/team-addon/roles",
        json={
            "id": "project__implementer",
            "team_id": "team",
            "project_id": "project",
            "name": "Implementer",
            "expected_revision": 6,
        },
    )
    assert response.status_code == 201

    response = client.patch(
        "/api/addons/team-addon/agents/backend",
        json={
            "project_id": "project",
            "role_id": "project__implementer",
            "session_id": "session-backend-2",
            "reports_to_agent_id": "manager",
            "display_name": None,
            "voice_profile": {
                "voice": "sage",
                "speaking_style": "Measured and direct.",
            },
            "ephemeral_policy": {"max_concurrent": 4},
            "status": "paused",
            "expected_revision": 7,
        },
    )
    assert response.status_code == 200
    body = response.json()
    backend = body["agents"]["backend"]
    assert body["revision"] == 8
    assert backend["role_id"] == "project__implementer"
    assert backend["role_name"] == "Implementer"
    assert backend["session_id"] == "session-backend-2"
    assert backend["reports_to_agent_id"] == "manager"
    assert backend["display_name"] is None
    assert backend["voice_profile"] == {
        "voice": "sage",
        "speaking_style": "Measured and direct.",
    }
    assert backend["ephemeral_policy"]["max_concurrent"] == 4
    assert backend["status"] == "paused"
    assert body["agents"]["lead"]["subordinate_agent_ids"] == []
    assert body["agents"]["manager"]["subordinate_agent_ids"] == ["lead", "backend"]


def test_team_addon_admin_api_agent_context_clearing_toggle(tmp_path: Path) -> None:
    client = _client(tmp_path)
    runtime_dir = tmp_path / "runtime"
    repo = TeamStateRepository(runtime_dir)
    repo.create_team(team_id="team", name="Team", manager_agent_id="manager", manager_session_id="session-manager")
    repo.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
    )
    repo.create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
    )
    repo.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
    )

    response = client.patch(
        "/api/addons/team-addon/agents/backend",
        json={
            "role_id": "project__backend",
            "session_id": "session-backend",
            "auto_context_clearing_enabled": True,
            "expected_revision": 4,
        },
    )

    assert response.status_code == 200
    runtime = response.json()["agent_runtimes"]["backend"]
    assert runtime["auto_context_clearing_enabled"] is True
    assert runtime["context_rotation_pending"] is True
    assert runtime["context_rotation_reason"] == "idle"
    assert "Active tasks: none." in runtime["last_handoff_summary"]

    response = client.patch(
        "/api/addons/team-addon/agents/backend",
        json={
            "role_id": "project__backend",
            "session_id": "session-backend",
            "auto_context_clearing_enabled": False,
            "expected_revision": 5,
        },
    )

    assert response.status_code == 200
    runtime = response.json()["agent_runtimes"]["backend"]
    assert runtime["auto_context_clearing_enabled"] is False
    assert runtime["context_rotation_pending"] is False
    assert runtime["context_rotation_reason"] is None
    assert runtime["context_rotation_requested_at"] is None
    assert runtime["next_wake_at"] is None
    assert runtime["last_handoff_summary"] is None


def test_team_addon_admin_api_manual_context_rotation_request(tmp_path: Path) -> None:
    client = _client(tmp_path)
    runtime_dir = tmp_path / "runtime"
    repo = TeamStateRepository(runtime_dir)
    repo.create_team(team_id="team", name="Team", manager_agent_id="manager", manager_session_id="session-manager")
    repo.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
    )
    repo.create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
    )
    repo.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
    )
    repo.create_or_reprioritize_task(
        task_id="active-work",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Active work",
        description="Manual context clearing should still queue.",
        created_by_agent_id="lead",
    )

    response = client.post(
        "/api/addons/team-addon/agents/backend/context-rotation",
        json={
            "reason": "controlled smoke",
            "requested_by": "operator:test",
            "expected_revision": 5,
        },
    )

    assert response.status_code == 200
    runtime = response.json()["agent_runtimes"]["backend"]
    assert runtime["auto_context_clearing_enabled"] is False
    assert runtime["context_rotation_pending"] is True
    assert runtime["context_rotation_reason"] == "manual"
    assert runtime["context_rotation_requested_by"] == "operator:test"
    assert runtime["context_rotation_request_note"] == "controlled smoke"
    assert "active-work" in runtime["last_handoff_summary"]


def test_team_addon_admin_api_manual_context_rotation_rejects_archived_agent(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    runtime_dir = tmp_path / "runtime"
    repo = TeamStateRepository(runtime_dir)
    repo.create_team(team_id="team", name="Team", manager_agent_id="manager", manager_session_id="session-manager")
    repo.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
    )
    repo.create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
    )
    repo.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
    )
    repo.update_agent(agent_id="backend", status=AgentStatus.ARCHIVED)

    response = client.post(
        "/api/addons/team-addon/agents/backend/context-rotation",
        json={"expected_revision": 5},
    )

    assert response.status_code == 400
    assert "archived" in response.json()["error"]


def test_team_addon_admin_api_operator_stand_down_and_revoke(tmp_path: Path) -> None:
    client = _client(tmp_path)
    runtime_dir = tmp_path / "runtime"
    repo = TeamStateRepository(runtime_dir)
    repo.create_team(team_id="team", name="Team", manager_agent_id="manager", manager_session_id="session-manager")
    repo.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
    )
    repo.create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
    )
    repo.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
    )

    response = client.post(
        "/api/addons/team-addon/agents/lead/stand-down",
        json={"reason": "Await operator follow-up.", "expected_revision": 4},
    )

    assert response.status_code == 201
    body = response.json()
    assert len(body["stand_downs"]) == 1
    stand_down = next(iter(body["stand_downs"].values()))
    assert stand_down["agent_id"] == "lead"
    assert stand_down["source"] == "operator"
    assert body["agents"]["lead"]["stand_down_active"] is True
    assert body["agents"]["backend"]["stand_down_active"] is True
    assert body["agents"]["backend"]["effective_stand_downs"][0]["inherited"] is True

    response = client.post(
        "/api/addons/team-addon/agents/lead/stand-down/revoke",
        json={"expected_revision": 5},
    )

    assert response.status_code == 200
    body = response.json()
    stand_down = next(iter(body["stand_downs"].values()))
    assert stand_down["cleared_at"] is not None
    assert body["agents"]["lead"]["stand_down_active"] is False
    assert body["agents"]["backend"]["stand_down_active"] is False


def test_team_addon_admin_api_materializes_auto_dev_squad(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/templates/auto-dev-squad/materialize",
        json=_auto_dev_squad_materialization_payload(),
    )
    assert response.status_code == 201
    body = response.json()

    assert body["materialized"]["success"] is True
    assert body["materialized"]["template_id"] == "auto-dev-squad"
    assert body["materialized"]["revision"] == 1
    assert body["revision"] == 1
    assert len(body["agents"]) == 5
    assert {
        role_key: body["agents"][agent_id]["ephemeral_policy"]["max_concurrent"]
        for role_key, agent_id in body["materialized"]["agent_ids"].items()
    } == {
        "manager": 0,
        "project_lead": 1,
        "implementer": 2,
        "reviewer": 1,
        "release_operator": 1,
    }
    assert len(body["projects"]) == 1
    assert len(body["review_profiles"]) == 1
    assert len(body["tasks"]) == 2
    assert body["teams"]["team-auto-dev-squad"]["project_ids"] == [
        "project-auto-dev-squad"
    ]
    assert body["projects"]["project-auto-dev-squad"]["goals"][0]["id"] == (
        "project-auto-dev-squad-auto-dev-reviewed-delivery"
    )
    assert body["tasks"]["project-auto-dev-squad-release-handoff"]["dependency_task_ids"] == [
        "project-auto-dev-squad-implement-scoped-slice"
    ]
    assert (
        body["review_profiles"]["project-auto-dev-squad-auto-dev-review"]["criteria"][0]["id"]
        == "scope"
    )


def test_team_addon_admin_api_materialize_template_rejects_duplicates_and_reruns(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    duplicate_response = client.post(
        "/api/addons/team-addon/templates/auto-dev-squad/materialize",
        json=_auto_dev_squad_materialization_payload(
            duplicate_reviewer_agent_id="implementer-auto-dev-squad"
        ),
    )
    assert duplicate_response.status_code == 400
    assert "duplicate ids" in duplicate_response.json()["error"]
    assert client.get("/api/addons/team-addon/state").json()["revision"] == 0

    success_response = client.post(
        "/api/addons/team-addon/templates/auto-dev-squad/materialize",
        json=_auto_dev_squad_materialization_payload(),
    )
    assert success_response.status_code == 201
    assert success_response.json()["revision"] == 1

    rerun_response = client.post(
        "/api/addons/team-addon/templates/auto-dev-squad/materialize",
        json=_auto_dev_squad_materialization_payload(expected_revision=1),
    )
    assert rerun_response.status_code == 400
    assert client.get("/api/addons/team-addon/state").json()["revision"] == 1


def test_team_addon_admin_api_materialize_template_rejects_stale_revision(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "existing-team",
            "name": "Existing Team",
            "manager_agent_id": "existing-manager",
            "manager_session_id": "existing-session",
            "expected_revision": 0,
        },
    )

    response = client.post(
        "/api/addons/team-addon/templates/auto-dev-squad/materialize",
        json=_auto_dev_squad_materialization_payload(),
    )
    assert response.status_code == 409
    assert response.json()["current_revision"] == 1


def test_team_addon_admin_api_operator_target_and_git_config(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "operator_communication_target_id": "ops-channel",
            "max_cost_usd": 10.5,
            "expected_revision": 0,
        },
    )
    assert response.status_code == 201
    assert (
        response.json()["teams"]["team"]["operator_communication_target_id"]
        == "ops-channel"
    )
    assert response.json()["teams"]["team"]["max_cost_usd"] == 10.5

    response = client.patch(
        "/api/addons/team-addon/teams/team",
        json={
            "operator_communication_target_id": "ops-channel-2",
            "max_cost_usd": 12.25,
            "expected_revision": 1,
        },
    )
    assert response.status_code == 200
    assert (
        response.json()["teams"]["team"]["operator_communication_target_id"]
        == "ops-channel-2"
    )
    assert response.json()["teams"]["team"]["max_cost_usd"] == 12.25

    response = client.patch(
        "/api/addons/team-addon/teams/team",
        json={"max_cost_usd": None, "expected_revision": 2},
    )
    assert response.status_code == 200
    assert response.json()["teams"]["team"]["max_cost_usd"] is None

    response = client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "git": {"host_repo_path": "/srv/repo", "bare_repo_name": "repo.git"},
            "expected_revision": 3,
        },
    )
    assert response.status_code == 201
    assert response.json()["projects"]["project"]["git"]["bare_repo_name"] == "repo.git"

    response = client.patch(
        "/api/addons/team-addon/projects/project",
        json={
            "git": {
                "host_repo_path": "/srv/repo",
                "bare_repo_path": "/srv/bare/repo.git",
                "base_branch": "develop",
            },
            "expected_revision": 4,
        },
    )
    assert response.status_code == 200
    git = response.json()["projects"]["project"]["git"]
    assert git["bare_repo_path"] == "/srv/bare/repo.git"
    assert git["base_branch"] == "develop"
    assert git["bare_repo_name"] is None

    response = client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project-2",
            "team_id": "team",
            "name": "Project 2",
            "lead_agent_id": "lead-2",
            "lead_session_id": "session-lead-2",
            "git": {
                "host_repo_path": "/srv/repo",
                "bare_repo_name": "a.git",
                "bare_repo_path": "/srv/b.git",
            },
            "expected_revision": 5,
        },
    )
    assert response.status_code == 400
    assert "exactly one" in response.json()["error"]


def test_team_addon_admin_api_creates_operator_directive(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    )
    client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "expected_revision": 1,
        },
    )

    response = client.post(
        "/api/addons/team-addon/operator-directives",
        json={
            "team_id": "team",
            "project_id": "project",
            "title": "Status report",
            "message": "Ask the lead for a status report.",
            "source": "discord",
            "source_message_id": "discord-msg-1",
            "expected_revision": 2,
        },
    )

    assert response.status_code == 201
    body = response.json()
    item = body["operator_directive"]
    assert item["recipient_agent_id"] == "manager"
    assert item["item_type"] == "operator_directive"
    assert item["payload"]["title"] == "Status report"
    assert item["payload"]["message"] == "Ask the lead for a status report."
    assert item["payload"]["project_id"] == "project"
    assert item["payload"]["source_message_id"] == "discord-msg-1"
    assert item["payload"]["created_by"] == "operator:admin"
    assert body["agent_runtimes"]["manager"]["next_wake_at"] is None


def test_team_addon_admin_api_task_graph_fields(tmp_path: Path) -> None:
    client = _client(tmp_path)

    client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    )
    client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "expected_revision": 1,
        },
    )
    client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "other-project",
            "team_id": "team",
            "name": "Other Project",
            "lead_agent_id": "other-lead",
            "lead_session_id": "session-other-lead",
            "expected_revision": 2,
        },
    )
    _post_backend_role(client, expected_revision=3)
    client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role_id": "project__backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "expected_revision": 4,
        },
    )

    # Parent task and a dependency task that the child will depend on.
    assert (
        client.post(
            "/api/addons/team-addon/tasks",
            json={
                "id": "parent",
                "owner_agent_id": "backend",
                "project_id": "project",
                "priority": 10,
                "title": "Parent task",
                "description": "Coordinating task.",
                "created_by_agent_id": "lead",
                "expected_revision": 5,
            },
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/addons/team-addon/tasks",
            json={
                "id": "dependency",
                "owner_agent_id": "backend",
                "project_id": "project",
                "priority": 10,
                "title": "Dependency task",
                "description": "Must finish first.",
                "created_by_agent_id": "lead",
                "expected_revision": 6,
            },
        ).status_code
        == 201
    )

    # Child task with parent + dependency graph fields.
    response = client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "child",
            "owner_agent_id": "backend",
            "project_id": "project",
            "priority": 10,
            "title": "Child task",
            "description": "Depends on the dependency task.",
            "created_by_agent_id": "lead",
            "parent_task_id": "parent",
            "dependency_task_ids": ["dependency"],
            "expected_revision": 7,
        },
    )
    assert response.status_code == 201
    state = response.json()
    child = state["tasks"]["child"]
    assert child["parent_task_id"] == "parent"
    assert child["dependency_task_ids"] == ["dependency"]
    assert child["dependency_status"] == "waiting"
    assert state["tasks"]["parent"]["child_task_ids"] == ["child"]

    # PATCH can update dependency links.
    response = client.patch(
        "/api/addons/team-addon/tasks/child",
        json={"dependency_task_ids": [], "expected_revision": 8},
    )
    assert response.status_code == 200
    child = response.json()["tasks"]["child"]
    assert child["dependency_task_ids"] == []
    assert child["dependency_status"] == "satisfied"

    # PATCH can clear a parent link with explicit null.
    response = client.patch(
        "/api/addons/team-addon/tasks/child",
        json={"parent_task_id": None, "expected_revision": 9},
    )
    assert response.status_code == 200
    state = response.json()
    assert state["tasks"]["child"]["parent_task_id"] is None
    assert state["tasks"]["parent"]["child_task_ids"] == []

    # Cross-project parent is rejected with a 400.
    client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "other-task",
            "owner_agent_id": "lead",
            "project_id": "other-project",
            "priority": 10,
            "title": "Other project task",
            "description": "Belongs to a different project.",
            "created_by_agent_id": "other-lead",
            "expected_revision": 10,
        },
    )
    response = client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "bad-parent",
            "owner_agent_id": "backend",
            "project_id": "project",
            "priority": 10,
            "title": "Bad parent",
            "description": "Parent in another project.",
            "created_by_agent_id": "lead",
            "parent_task_id": "other-task",
            "expected_revision": 11,
        },
    )
    assert response.status_code == 400

    # Payloads WITHOUT the new keys still work (regression).
    response = client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "plain",
            "owner_agent_id": "backend",
            "project_id": "project",
            "priority": 10,
            "title": "Plain task",
            "description": "No graph fields.",
            "created_by_agent_id": "lead",
            "expected_revision": 11,
        },
    )
    assert response.status_code == 201
    plain = response.json()["tasks"]["plain"]
    assert plain["parent_task_id"] is None
    assert plain["dependency_task_ids"] == []


def test_team_addon_admin_api_review_decision_and_release_flow(tmp_path: Path) -> None:
    client = _client(tmp_path)

    client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    )
    client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "goals": [{"id": "goal", "description": "Ship reviewed work"}],
            "git": {"host_repo_path": "/srv/repo", "bare_repo_name": "repo.git"},
            "expected_revision": 1,
        },
    )
    _post_backend_role(client, expected_revision=2)
    client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role_id": "project__backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "expected_revision": 3,
        },
    )
    client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "task",
            "owner_agent_id": "backend",
            "project_id": "project",
            "priority": 10,
            "title": "Build backend",
            "description": "Implement the backend task.",
            "created_by_agent_id": "lead",
            "expected_revision": 4,
        },
    )
    client.post(
        "/api/addons/team-addon/deliverables",
        json={
            "id": "deliverable",
            "task_id": "task",
            "producer_agent_id": "backend",
            "summary": "Backend task is ready.",
            "artifact_refs": ["artifacts/backend.tar.gz"],
            "expected_revision": 5,
        },
    )
    client.post(
        "/api/addons/team-addon/review-requests",
        json={
            "id": "review",
            "deliverable_id": "deliverable",
            "reviewer_agent_id": "lead",
            "expected_revision": 6,
        },
    )

    response = client.post(
        "/api/addons/team-addon/review-requests/review/decision",
        json={"state": "accepted", "findings": ["looks good"], "expected_revision": 7},
    )
    assert response.status_code == 200
    state = response.json()
    assert state["review_requests"]["review"]["state"] == "accepted"
    assert state["review_requests"]["review"]["findings"] == ["looks good"]
    assert state["review_requests"]["review"]["decision_at"] is not None
    assert state["deliverables"]["deliverable"]["state"] == "accepted"
    assert state["tasks"]["task"]["state"] == "accepted"

    repo = TeamStateRepository(tmp_path / "runtime")

    def _attach_feature_branch(current):
        current.deliverables["deliverable"].git_refs.feature_branch = (
            "team/team/features/task-backend"
        )
        return current

    repo.update(_attach_feature_branch)

    response = client.post(
        "/api/addons/team-addon/release-packages",
        json={
            "id": "release",
            "project_id": "project",
            "deliverable_ids": ["deliverable"],
            "expected_revision": 9,
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["release_packages"]["release"]["deliverable_ids"] == [
        "deliverable"
    ]
    assert len(payload["published_event_ids"]) == 1
    events = InternalEventBus(internal_event_store_path(tmp_path / "runtime")).list_events()
    assert len(events) == 1
    assert events[0].event_type == "team.git.promotion_requested"
    assert events[0].payload["promotion_kind"] == "release"
    assert events[0].payload["target_branch"] == "team/team/releases/release"

    response = client.get("/api/addons/team-addon/release-packages")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["release_packages"]] == ["release"]

    response = client.get("/api/addons/team-addon/release-packages/release")
    assert response.status_code == 200
    assert response.json()["project_id"] == "project"

    response = client.get("/api/addons/team-addon/release-packages/missing")
    assert response.status_code == 404

    response = client.get("/api/addons/team-addon/git-promotions")
    assert response.status_code == 200
    assert isinstance(response.json()["git_promotions"], list)

    response = client.get("/api/addons/team-addon/git-promotions/missing")
    assert response.status_code == 404


def test_team_addon_admin_api_review_profile_flow(tmp_path: Path) -> None:
    client = _client(tmp_path)

    client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    )
    client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "expected_revision": 1,
        },
    )
    _post_backend_role(client, expected_revision=2)
    client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role_id": "project__backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "expected_revision": 3,
        },
    )
    client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "task",
            "owner_agent_id": "backend",
            "project_id": "project",
            "priority": 10,
            "title": "Build backend",
            "description": "Implement the backend task.",
            "created_by_agent_id": "lead",
            "expected_revision": 4,
        },
    )
    client.post(
        "/api/addons/team-addon/deliverables",
        json={
            "id": "deliverable",
            "task_id": "task",
            "producer_agent_id": "backend",
            "summary": "Backend task is ready.",
            "expected_revision": 5,
        },
    )

    # Create a review profile.
    response = client.post(
        "/api/addons/team-addon/review-profiles",
        json={
            "id": "profile",
            "team_id": "team",
            "project_id": "project",
            "name": "Security review",
            "description": "Checklist for security-sensitive work.",
            "applicable_role_ids": ["project__project-lead", "project__backend"],
            "criteria": [
                {"id": "authz", "label": "Authorization", "required": True},
                {"id": "logging", "label": "Logging", "description": "No secrets", "required": False},
            ],
            "expected_revision": 6,
        },
    )
    assert response.status_code == 201

    # List shows it.
    response = client.get("/api/addons/team-addon/review-profiles")
    assert response.status_code == 200
    listing = response.json()
    assert [item["id"] for item in listing["review_profiles"]] == ["profile"]

    # Get by id.
    response = client.get("/api/addons/team-addon/review-profiles/profile")
    assert response.status_code == 200
    profile = response.json()
    assert profile["name"] == "Security review"
    assert [criterion["id"] for criterion in profile["criteria"]] == ["authz", "logging"]

    # Missing profile returns 404.
    assert client.get("/api/addons/team-addon/review-profiles/missing").status_code == 404

    # Patch name and archived flag.
    response = client.patch(
        "/api/addons/team-addon/review-profiles/profile",
        json={"name": "Security review v2", "archived": True, "expected_revision": 7},
    )
    assert response.status_code == 200
    response = client.get("/api/addons/team-addon/review-profiles/profile")
    profile = response.json()
    assert profile["name"] == "Security review v2"
    assert profile["archived"] is True

    # Empty strings are meaningful on edit: they clear the optional description.
    response = client.patch(
        "/api/addons/team-addon/review-profiles/profile",
        json={"description": "", "expected_revision": 8},
    )
    assert response.status_code == 200
    response = client.get("/api/addons/team-addon/review-profiles/profile")
    profile = response.json()
    assert profile["description"] == ""

    # Archived profiles cannot be assigned through the Admin API.
    response = client.post(
        "/api/addons/team-addon/review-requests",
        json={
            "id": "review-archived",
            "deliverable_id": "deliverable",
            "reviewer_agent_id": "lead",
            "review_profile_id": "profile",
            "expected_revision": 9,
        },
    )
    assert response.status_code == 400
    assert "archived" in response.json()["error"]

    # Un-archive so the profile can be attached to a review request.
    client.patch(
        "/api/addons/team-addon/review-profiles/profile",
        json={"archived": False, "expected_revision": 9},
    )

    # Create a review request with the profile + instructions.
    response = client.post(
        "/api/addons/team-addon/review-requests",
        json={
            "id": "review",
            "deliverable_id": "deliverable",
            "reviewer_agent_id": "lead",
            "review_profile_id": "profile",
            "instructions": "Pay attention to authz.",
            "expected_revision": 10,
        },
    )
    assert response.status_code == 201
    review = response.json()["review_requests"]["review"]
    assert review["review_profile_id"] == "profile"
    assert review["instructions"] == "Pay attention to authz."

    # GET /state shows the profile on the review.
    state = client.get("/api/addons/team-addon/state").json()
    assert state["review_requests"]["review"]["review_profile_id"] == "profile"

    # Decision with criterion results persists them.
    response = client.post(
        "/api/addons/team-addon/review-requests/review/decision",
        json={
            "state": "accepted",
            "findings": ["looks good"],
            "criteria": [
                {"criterion_id": "authz", "state": "passed", "finding": "scoped"},
                {"criterion_id": "logging", "state": "skipped"},
            ],
            "expected_revision": 11,
        },
    )
    assert response.status_code == 200
    review = response.json()["review_requests"]["review"]
    assert review["state"] == "accepted"
    assert [result["criterion_id"] for result in review["criteria"]] == ["authz", "logging"]
    assert review["criteria"][0]["state"] == "passed"
    assert review["criteria"][0]["finding"] == "scoped"


def test_team_addon_admin_api_review_request_without_profile_still_works(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    )
    client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "expected_revision": 1,
        },
    )
    _post_backend_role(client, expected_revision=2)
    client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role_id": "project__backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "expected_revision": 3,
        },
    )
    client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "task",
            "owner_agent_id": "backend",
            "project_id": "project",
            "priority": 10,
            "title": "Build backend",
            "description": "Implement the backend task.",
            "created_by_agent_id": "lead",
            "expected_revision": 4,
        },
    )
    client.post(
        "/api/addons/team-addon/deliverables",
        json={
            "id": "deliverable",
            "task_id": "task",
            "producer_agent_id": "backend",
            "summary": "Backend task is ready.",
            "expected_revision": 5,
        },
    )

    # Regression: review request without a profile still succeeds.
    response = client.post(
        "/api/addons/team-addon/review-requests",
        json={
            "id": "review",
            "deliverable_id": "deliverable",
            "reviewer_agent_id": "lead",
            "expected_revision": 6,
        },
    )
    assert response.status_code == 201
    assert response.json()["review_requests"]["review"]["review_profile_id"] is None

    # Unknown profile id on a review request returns 400.
    response = client.post(
        "/api/addons/team-addon/review-requests",
        json={
            "id": "review-2",
            "deliverable_id": "deliverable",
            "reviewer_agent_id": "lead",
            "review_profile_id": "missing-profile",
            "expected_revision": 7,
        },
    )
    assert response.status_code == 400


def test_team_addon_admin_api_patch_team_rejects_stale_revision(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    )

    response = client.patch(
        "/api/addons/team-addon/teams/team",
        json={"operator_communication_target_id": "ops", "expected_revision": 0},
    )

    assert response.status_code == 409
    assert response.json()["current_revision"] == 1


def test_team_addon_admin_api_requires_expected_revision(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "expected_revision is required"

    response = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": None,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "expected_revision is required"
    assert client.get("/api/addons/team-addon/state").json()["revision"] == 0


def test_team_addon_admin_api_rejects_stale_revision(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    )

    response = client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "expected_revision": 0,
        },
    )

    assert response.status_code == 409
    assert response.json()["current_revision"] == 1
