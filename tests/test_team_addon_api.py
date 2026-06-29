from __future__ import annotations

import asyncio
import json
import subprocess
from io import BytesIO
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from PIL import Image
from starlette.testclient import TestClient

from agent_sandbox.admin.composition import AdminRuntimeComponents, build_admin_runtime
from agent_sandbox.admin.web.app import create_app
from agent_sandbox.directory_config import DirectoryConfigStore
from agent_sandbox.events.internal import InternalEventBus, internal_event_store_path
from agent_sandbox.model_usage.models import ModelInferenceUsageEvent
from agent_sandbox.model_usage.store import ModelUsageStore
from agent_sandbox.models import SessionManifest
from agent_sandbox.repositories.session_manifests import JsonSessionManifestRepository
from evander_team_addon import api as team_addon_api
from evander_team_addon.api import (
    _bootstrap_payload_for_request,
    _conversation_snapshot_events,
    _state_snapshot_events,
    _team_state_payload_for_request,
)
from evander_team_addon.models import (
    AgentActionRecord,
    AgentInboxItem,
    AgentRuntime,
    AgentStatus,
    AgentVoiceProfile,
    EphemeralAssignment,
    EphemeralAssignmentState,
    GitPromotionRecord,
    ReleasePackage,
    TeamConversationMessage,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamRecordState,
    TeamTaskState,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.domain.chat import CHAT_MESSAGE_QUEUED_EVENT_TYPE
from evander_team_addon.services.agent_action_service import TeamAgentActionService
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.chat_service import TeamChatService
from evander_team_addon.services.runtime_service import TeamRuntimeService


def _team_admin_service(repo: TeamStateRepository) -> TeamAdminService:
    return TeamAdminService(repo)


def _team_chat_service(repo: TeamStateRepository) -> TeamChatService:
    return TeamChatService(repo)


def _team_runtime_service(repo: TeamStateRepository) -> TeamRuntimeService:
    return TeamRuntimeService(repo)


def _team_agent_action_service(repo: TeamStateRepository) -> TeamAgentActionService:
    return TeamAgentActionService(repo)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
                '    - "evander_team_addon"',
            ]
        )
    config_path.write_text("\n".join(lines), encoding="utf-8")
    return config_path


def _client(tmp_path: Path, *, enable_team_addon: bool = True) -> TestClient:
    return TestClient(
        create_app(build_admin_runtime(_write_config(tmp_path, enable_team_addon=enable_team_addon)))
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _git_dir(git_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", f"--git-dir={git_dir}", *args],
        check=True,
        capture_output=True,
        text=True,
    )


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
    _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
    )
    _team_admin_service(repo).create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
    )
    _team_admin_service(repo).create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
    )
    _team_admin_service(repo).register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
    )
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="blocked-task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Blocked task",
        description="This task is blocked.",
        created_by_agent_id="lead",
        state=TeamTaskState.BLOCKED,
    )
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="dependency-task",
        owner_agent_id="backend",
        project_id="project",
        priority=2,
        title="Dependency task",
        description="This task is still open.",
        created_by_agent_id="lead",
    )
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="waiting-task",
        owner_agent_id="backend",
        project_id="project",
        priority=3,
        title="Waiting task",
        description="This task waits on another task.",
        created_by_agent_id="lead",
        dependency_task_ids=["dependency-task"],
    )
    _team_admin_service(repo).record_deliverable(
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


def _seed_long_chat_history(
    runtime_dir: Path,
    *,
    message_count: int = 75,
) -> tuple[TeamStateRepository, str]:
    repo = _seed_runtime_operations_state(runtime_dir)
    _state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="admin",
        operator_display_name="Admin",
        created_by="operator:admin",
        expected_revision=repo.read().revision,
    )
    conversation_id = conversation.id
    base_time = datetime(2026, 1, 1, tzinfo=UTC)

    def _add_messages(state):
        for index in range(message_count):
            created_at = base_time + timedelta(minutes=index)
            state.conversation_messages[f"msg-{index:03d}"] = TeamConversationMessage(
                id=f"msg-{index:03d}",
                conversation_id=conversation_id,
                sender="operator" if index % 2 == 0 else "agent",
                body=f"Message {index:03d}",
                status="sent",
                created_at=created_at,
                updated_at=created_at,
            )
        state.conversations[
            conversation_id
        ].last_message_preview = f"Message {message_count - 1:03d}"
        state.conversations[conversation_id].last_interaction_at = base_time + timedelta(
            minutes=message_count - 1
        )
        return state

    repo.update(_add_messages)
    return repo, conversation_id


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
        "evander_team_addon.api.openai_avatar_generator_from_config",
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
        "evander_team_addon.api.openai_avatar_generator_from_config",
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
        "evander_team_addon.api.openai_avatar_generator_from_config",
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
    from evander_team_addon.avatars import OpenAIAvatarGenerator

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


def test_team_addon_admin_api_conversation_queues_without_inline_dispatch_and_reply(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(tmp_path, enable_team_addon=True)
    repo = _seed_runtime_operations_state(tmp_path / "runtime")

    def _clear_backend_runtime(state):
        state.agent_runtimes.pop("backend", None)
        return state

    repo.update(_clear_backend_runtime)
    service = build_admin_runtime(config_path)
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
                str(file.get("destination_path")) for file in kwargs.get("files") or []
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
    assert conversation["operator_user_id"] == "admin"
    assert conversation["read_only"] is False
    assert created.json()["current_operator"]["user_id"] == "admin"
    assert created.json()["selected_operator"]["user_id"] == "admin"
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
    assert sent_payload["dispatch"]["success"] is False
    assert sent_payload["dispatch"]["queued"] is True
    assert sent_payload["dispatch"]["dispatch_id"].startswith("team-chat:")
    assert "queued for asynchronous agent dispatch" in sent_payload["dispatch"]["message"]
    assert len(sent_payload["published_event_ids"]) == 1
    assert copy_calls == []
    message = sent_payload["message"]
    assert message["sender"] == "operator"
    assert message["status"] == "queued"
    assert message["dispatch_id"] == sent_payload["dispatch"]["dispatch_id"]
    assert message["metadata"]["sender_kind"] == "human_operator"
    assert message["metadata"]["operator_user_id"] == "admin"
    assert resume_calls == []
    events = InternalEventBus(internal_event_store_path(tmp_path / "runtime")).list_events()
    assert len(events) == 1
    assert events[0].event_type == CHAT_MESSAGE_QUEUED_EVENT_TYPE
    assert events[0].correlation_key == message["dispatch_id"]
    assert events[0].idempotency_key == f"team-chat-message-queued:{message['id']}"
    assert events[0].payload == {
        "team_id": "team",
        "agent_id": "backend",
        "session_id": "session-backend",
        "conversation_id": conversation["id"],
        "message_id": message["id"],
        "dispatch_id": message["dispatch_id"],
    }

    state, result = _team_agent_action_service(repo).apply_agent_action(
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


def test_team_addon_admin_api_queues_chat_when_agent_has_active_dispatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(tmp_path, enable_team_addon=True)
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    service = build_admin_runtime(config_path)
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

    def _fake_resume_agent(target, prompt, **kwargs):
        resume_calls.append({"target": target, "prompt": prompt, **kwargs})
        return {"success": True, "dispatch_id": kwargs.get("dispatch_id")}

    monkeypatch.setattr(service, "resume_agent", _fake_resume_agent)
    client = TestClient(create_app(service))

    created = client.post(
        "/api/addons/team-addon/conversations",
        json={"agent_id": "backend", "expected_revision": repo.read().revision},
    )
    assert created.status_code == 201
    conversation = created.json()["conversations"][0]

    sent = client.post(
        f"/api/addons/team-addon/conversations/{conversation['id']}/messages",
        json={
            "body": "Can you look at this once your current wake finishes?",
            "expected_revision": created.json()["revision"],
        },
    )

    assert sent.status_code == 202
    sent_payload = sent.json()
    assert sent_payload["dispatch"]["success"] is False
    assert sent_payload["dispatch"]["queued"] is True
    assert sent_payload["dispatch"]["dispatch_id"].startswith("team-chat:")
    assert "queued for asynchronous agent dispatch" in sent_payload["dispatch"]["message"]
    assert len(sent_payload["published_event_ids"]) == 1
    assert resume_calls == []
    message = sent_payload["message"]
    assert message["status"] == "queued"
    assert message["dispatch_id"] == sent_payload["dispatch"]["dispatch_id"]
    reread = TeamStateRepository(tmp_path / "runtime").read()
    assert reread.conversation_messages[message["id"]].status == "queued"
    assert reread.agent_runtimes["backend"].active_dispatch_id == "dispatch-backend"


def test_team_addon_conversation_messages_are_paginated(tmp_path: Path) -> None:
    _repo, conversation_id = _seed_long_chat_history(tmp_path / "runtime")
    client = _client(tmp_path)

    detail = client.get(f"/api/addons/team-addon/conversations/{conversation_id}")
    assert detail.status_code == 200
    detail_conversation = detail.json()["conversation"]
    assert detail_conversation["message_count"] == 75
    assert detail_conversation["has_older"] is True
    assert detail_conversation["oldest_message_id"] == "msg-025"
    assert detail_conversation["newest_message_id"] == "msg-074"
    assert [message["body"] for message in detail_conversation["messages"][:2]] == [
        "Message 025",
        "Message 026",
    ]
    assert detail_conversation["messages"][-1]["body"] == "Message 074"
    assert len(detail_conversation["messages"]) == 50

    latest_page = client.get(
        f"/api/addons/team-addon/conversations/{conversation_id}/messages?limit=10"
    )
    assert latest_page.status_code == 200
    latest_payload = latest_page.json()
    assert latest_payload["message_count"] == 75
    assert latest_payload["has_older"] is True
    assert latest_payload["oldest_message_id"] == "msg-065"
    assert latest_payload["newest_message_id"] == "msg-074"
    assert [message["id"] for message in latest_payload["messages"]] == [
        f"msg-{index:03d}" for index in range(65, 75)
    ]

    older_page = client.get(
        f"/api/addons/team-addon/conversations/{conversation_id}/messages?before=msg-065&limit=10"
    )
    assert older_page.status_code == 200
    older_payload = older_page.json()
    assert older_payload["has_older"] is True
    assert older_payload["oldest_message_id"] == "msg-055"
    assert older_payload["newest_message_id"] == "msg-064"
    assert [message["id"] for message in older_payload["messages"]] == [
        f"msg-{index:03d}" for index in range(55, 65)
    ]

    first_page = client.get(
        f"/api/addons/team-addon/conversations/{conversation_id}/messages?before=msg-005&limit=10"
    )
    assert first_page.status_code == 200
    first_payload = first_page.json()
    assert first_payload["has_older"] is False
    assert [message["id"] for message in first_payload["messages"]] == [
        f"msg-{index:03d}" for index in range(0, 5)
    ]


def test_team_addon_conversation_list_can_inline_selected_message_page(
    tmp_path: Path,
) -> None:
    _repo, conversation_id = _seed_long_chat_history(tmp_path / "runtime")
    client = _client(tmp_path)

    default_response = client.get("/api/addons/team-addon/conversations")
    assert default_response.status_code == 200
    default_conversation = default_response.json()["conversations"][0]
    assert default_conversation["message_count"] == 75
    assert "messages" not in default_conversation

    selected_response = client.get(
        "/api/addons/team-addon/conversations"
        f"?include_selected_messages=1&selected_conversation_id={conversation_id}"
    )
    assert selected_response.status_code == 200
    payload = selected_response.json()
    assert payload["selected_conversation_id"] == conversation_id
    conversation = payload["conversations"][0]
    assert conversation["id"] == conversation_id
    assert conversation["message_count"] == 75
    assert conversation["messages_limit"] == 50
    assert conversation["has_older"] is True
    assert conversation["oldest_message_id"] == "msg-025"
    assert conversation["newest_message_id"] == "msg-074"
    assert [message["id"] for message in conversation["messages"]] == [
        f"msg-{index:03d}" for index in range(25, 75)
    ]


def test_team_addon_bootstrap_and_chat_view_are_scoped(tmp_path: Path) -> None:
    _repo, conversation_id = _seed_long_chat_history(tmp_path / "runtime")
    client = _client(tmp_path)

    bootstrap = client.get("/api/addons/team-addon/bootstrap")
    assert bootstrap.status_code == 200
    bootstrap_payload = bootstrap.json()
    assert bootstrap_payload["revision"] > 0
    assert "chat" in bootstrap_payload["views"]
    assert "overview" not in bootstrap_payload["views"]
    assert bootstrap_payload["counts"]["conversations"] == 1
    assert "tasks" not in bootstrap_payload
    assert "conversation_messages" not in bootstrap_payload

    chat_view = client.get(
        f"/api/addons/team-addon/views/chat?selected_conversation_id={conversation_id}"
    )
    assert chat_view.status_code == 200
    payload = chat_view.json()
    assert payload["view"] == "chat"
    assert set(payload["data"]) == {"chat", "state"}
    assert payload["data"]["chat"]["selected_conversation_id"] == conversation_id
    conversation = payload["data"]["chat"]["conversations"][0]
    assert conversation["messages_limit"] == 50
    assert len(conversation["messages"]) == 50
    assert "tasks" not in payload["data"]["state"]
    assert "conversation_messages" not in payload["data"]["state"]
    assert set(payload["resource_tokens"]) == {"agents"}

    missing_view = client.get("/api/addons/team-addon/views/overview")
    assert missing_view.status_code == 404
    assert missing_view.json() == {"error": "view not found"}


def test_team_addon_views_filter_non_actionable_history(tmp_path: Path) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    now = datetime(2026, 1, 2, tzinfo=UTC)

    def _mutate(state):
        state.inbox["handled-inbox"] = AgentInboxItem(
            id="handled-inbox",
            recipient_agent_id="backend",
            sender_agent_id="lead",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            state=TeamInboxItemState.HANDLED,
            payload={"question": "Already handled"},
            created_at=now,
            updated_at=now,
        )
        state.action_records["dismissed-action"] = AgentActionRecord(
            id="dismissed-action",
            agent_id="backend",
            dispatch_id="dispatch-backend",
            action={"type": "dismissed"},
            success=False,
            error="dismissed failure",
            created_at=state.action_records["action-1"].created_at - timedelta(seconds=2),
            dismissed_at=now,
            dismissed_by="operator",
        )
        state.action_records["older-failed-action"] = AgentActionRecord(
            id="older-failed-action",
            agent_id="backend",
            dispatch_id="dispatch-backend-older",
            action={"type": "old-build"},
            success=False,
            error="older failed action",
            created_at=state.action_records["action-1"].created_at - timedelta(seconds=1),
        )
        state.action_records["successful-action"] = AgentActionRecord(
            id="successful-action",
            agent_id="lead",
            dispatch_id="dispatch-lead",
            action={"type": "ok"},
            success=True,
            created_at=now,
        )

    repo.update(_mutate)
    client = _client(tmp_path)

    people = client.get("/api/addons/team-addon/views/people")
    assert people.status_code == 200
    people_state = people.json()["data"]["state"]
    assert set(people_state["inbox"]) == {"inbox-1", "escalation-1"}
    assert "action_records" not in people_state

    operations = client.get("/api/addons/team-addon/views/operations")
    assert operations.status_code == 200
    operations_state = operations.json()["data"]["state"]
    assert set(operations_state["inbox"]) == {"inbox-1", "escalation-1"}
    assert set(operations_state["action_records"]) == {"action-1"}
    assert set(operations_state["resource_tokens"]["inbox"]) == {"inbox-1", "escalation-1"}
    assert set(operations_state["resource_tokens"]["action_records"]) == {"action-1"}


def test_team_addon_chat_defaults_to_current_operator_and_inspects_others_read_only(
    tmp_path: Path,
) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    _state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        operator_display_name="Alice",
        expected_revision=repo.read().revision,
    )
    client = _client(tmp_path)

    mine = client.get("/api/addons/team-addon/conversations")
    assert mine.status_code == 200
    assert mine.json()["current_operator"]["user_id"] == "admin"
    assert mine.json()["selected_operator"]["user_id"] == "admin"
    assert mine.json()["read_only"] is False
    assert mine.json()["conversations"] == []

    inspected = client.get(
        "/api/addons/team-addon/conversations?operator_user_id=alice@example.com"
    )
    assert inspected.status_code == 200
    inspected_payload = inspected.json()
    assert inspected_payload["selected_operator"]["user_id"] == "alice@example.com"
    assert inspected_payload["read_only"] is True
    assert inspected_payload["conversations"][0]["id"] == conversation.id
    assert inspected_payload["conversations"][0]["read_only"] is True

    blocked_send = client.post(
        f"/api/addons/team-addon/conversations/{conversation.id}/messages",
        json={"body": "I should not be able to send here."},
    )
    assert blocked_send.status_code == 403


def test_team_addon_state_payload_filters_chats_without_read_all(
    tmp_path: Path,
) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    _state, alice_conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        operator_display_name="Alice",
        expected_revision=repo.read().revision,
    )
    _team_chat_service(repo).record_operator_chat_message(
        conversation_id=alice_conversation.id,
        body="Alice private question",
        created_by="operator:alice@example.com",
        expected_revision=repo.read().revision,
    )
    _state, bob_conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="bob@example.com",
        operator_display_name="Bob",
        expected_revision=repo.read().revision,
    )
    _team_chat_service(repo).record_operator_chat_message(
        conversation_id=bob_conversation.id,
        body="Bob private question",
        created_by="operator:bob@example.com",
        expected_revision=repo.read().revision,
    )

    class AuthStub:
        def __init__(self, *, can_read_all: bool) -> None:
            self.can_read_all = can_read_all

        def read_session(self, _request) -> dict[str, str]:
            return {"login": "Alice@Example.com", "name": "Alice"}

        def can(self, _request, _permission: str) -> bool:
            return self.can_read_all

    scoped_payload = _team_state_payload_for_request(
        AuthStub(can_read_all=False),
        object(),  # type: ignore[arg-type]
        repo.read(),
    )
    assert set(scoped_payload["conversations"]) == {alice_conversation.id}
    assert bob_conversation.id not in scoped_payload["conversations"]
    assert {message["body"] for message in scoped_payload["conversation_messages"].values()} == {
        "Alice private question"
    }
    assert all(
        message["conversation_id"] == alice_conversation.id
        for message in scoped_payload["conversation_messages"].values()
    )

    read_all_payload = _team_state_payload_for_request(
        AuthStub(can_read_all=True),
        object(),  # type: ignore[arg-type]
        repo.read(),
    )
    assert {alice_conversation.id, bob_conversation.id}.issubset(
        set(read_all_payload["conversations"])
    )


def test_team_addon_bootstrap_counts_only_current_operator_chats_without_read_all(
    tmp_path: Path,
) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        operator_display_name="Alice",
        expected_revision=repo.read().revision,
    )
    _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="bob@example.com",
        operator_display_name="Bob",
        expected_revision=repo.read().revision,
    )

    class AuthStub:
        def read_session(self, _request) -> dict[str, str]:
            return {"login": "Alice@Example.com", "name": "Alice"}

        def can(self, _request, _permission: str) -> bool:
            return False

    payload = _bootstrap_payload_for_request(
        AuthStub(),
        object(),  # type: ignore[arg-type]
        repo.read(),
    )

    assert payload["selected_operator"]["user_id"] == "alice@example.com"
    assert payload["counts"]["conversations"] == 1


def test_team_addon_conversation_stream_snapshots_are_bounded(tmp_path: Path) -> None:
    repo, conversation_id = _seed_long_chat_history(tmp_path / "runtime")

    class RequestStub:
        async def is_disconnected(self) -> bool:
            return False

    async def read_first_event() -> str:
        stream = _conversation_snapshot_events(
            repo,
            RequestStub(),  # type: ignore[arg-type]
            conversation_id,
            poll_interval_seconds=0,
        )
        try:
            return await asyncio.wait_for(anext(stream), timeout=1.0)
        finally:
            await stream.aclose()

    event = asyncio.run(read_first_event())
    line = next(line for line in event.splitlines() if line.startswith("data: "))
    payload = json.loads(line.removeprefix("data: "))

    assert event.startswith("event: snapshot\n")
    assert payload["conversation"]["message_count"] == 75
    assert len(payload["conversation"]["messages"]) == 50
    assert payload["conversation"]["messages"][0]["id"] == "msg-025"
    assert payload["conversation"]["messages"][-1]["id"] == "msg-074"


def test_team_addon_conversation_stream_emits_updated_snapshots(tmp_path: Path) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")

    def _clear_backend_runtime(state):
        state.agent_runtimes.pop("backend", None)
        return state

    repo.update(_clear_backend_runtime)
    _state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
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
            _, message = _team_chat_service(repo).record_operator_chat_message(
                conversation_id=conversation.id,
                body="Can you summarize the blocker?",
                expected_revision=repo.read().revision,
            )
            dispatch_id = f"team-chat:{message.id}"
            _team_chat_service(repo).begin_chat_message_dispatch(
                message_id=message.id,
                dispatch_id=dispatch_id,
            )
            _team_agent_action_service(repo).apply_agent_action(
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


def test_team_addon_state_stream_emits_updated_snapshots(tmp_path: Path) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")

    class RequestStub:
        async def is_disconnected(self) -> bool:
            return False

    def _payload_from_event(event: str) -> dict:
        line = next(line for line in event.splitlines() if line.startswith("data: "))
        return json.loads(line.removeprefix("data: "))

    async def read_two_events() -> tuple[str, str]:
        stream = _state_snapshot_events(
            lambda: team_addon_api.team_state_payload(repo.read()),
            RequestStub(),  # type: ignore[arg-type]
            poll_interval_seconds=0,
        )
        try:
            first = await asyncio.wait_for(anext(stream), timeout=1.0)
            _team_admin_service(repo).create_or_reprioritize_task(
                task_id="live-task",
                owner_agent_id="backend",
                project_id="project",
                priority=4,
                title="Live task",
                description="Task created while the UI stream is connected.",
                created_by_agent_id="lead",
            )
            second = await asyncio.wait_for(anext(stream), timeout=1.0)
            return first, second
        finally:
            await stream.aclose()

    first_event, second_event = asyncio.run(read_two_events())
    first_payload = _payload_from_event(first_event)
    second_payload = _payload_from_event(second_event)

    assert first_event.startswith("event: snapshot\n")
    assert second_event.startswith("event: snapshot\n")
    assert second_payload["revision"] > first_payload["revision"]
    assert "live-task" in second_payload["tasks"]


def test_agent_voice_session_create_query_and_end(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(tmp_path, enable_team_addon=True)
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    _team_admin_service(repo).update_agent(
        agent_id="backend",
        voice_profile=AgentVoiceProfile(
            voice="cedar",
            speaking_style="Calm, concise, and lightly upbeat.",
        ),
        set_voice_profile=True,
        expected_revision=repo.read().revision,
    )
    service = build_admin_runtime(config_path)
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
        "evander_team_addon.api.OpenAIRealtimeClientSecretService",
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
    assert service.runtime.wake_store.get_request(f"voice-query:{query['query_id']}") is not None

    end_response = client.post(
        f"/api/addons/team-addon/voice-sessions/{payload['voice_session_id']}/end",
        json={"status": "ended", "summary": "Operator asked about blockers."},
    )
    assert end_response.status_code == 200
    ended = end_response.json()
    assert ended["status"] == "ended"
    assert ended["reconciliation_status"] == "scheduled"
    assert (
        service.runtime.wake_store.get_request(
            f"voice-session-reconcile:{payload['voice_session_id']}"
        )
        is not None
    )


def test_agent_voice_session_records_chat_conversation_message(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(tmp_path, enable_team_addon=True)
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        expected_revision=repo.read().revision,
    )
    service = build_admin_runtime(config_path)
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
        "evander_team_addon.api.OpenAIRealtimeClientSecretService",
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
    service = build_admin_runtime(config_path)
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
        "evander_team_addon.api.OpenAIRealtimeClientSecretService",
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
                "agent_id": (duplicate_reviewer_agent_id or "reviewer-auto-dev-squad"),
                "session_id": "session-reviewer-auto-dev-squad",
            },
            "release_operator": {
                "agent_id": "release-operator-auto-dev-squad",
                "session_id": "session-release-operator-auto-dev-squad",
            },
        },
    }


def _accounting_department_materialization_payload(
    *,
    expected_revision: int = 0,
) -> dict[str, object]:
    return {
        "expected_revision": expected_revision,
        "team": {
            "name": "Accounting Department",
            "operator_communication_target_id": "teams://accounting-ops",
        },
        "project": {
            "name": "Bookkeeping Operations",
        },
        "roles": {
            "manager": {"session_id": "session-manager-accounting"},
            "project_lead": {"session_id": "session-bookkeeping-lead-accounting"},
            "bookkeeping_clerk": {"session_id": "session-bookkeeping-clerk-accounting"},
            "invoice_reviewer": {"session_id": "session-invoice-reviewer-accounting"},
            "payment_preparer": {"session_id": "session-payment-preparer-accounting"},
            "tax_compliance_reviewer": {"session_id": "session-tax-compliance-reviewer-accounting"},
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
        "auto-dev-squad",
        "accounting-department",
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
    assert 'name="lead_agent_id"' in ui.text
    assert 'name="lead_display_name"' not in ui.text
    assert 'class="workspace-dashboard-page"' in ui.text
    assert "data-workspace-dashboard-metrics" in ui.text
    assert "data-workspace-attention-column" in ui.text
    assert "data-workspace-status-column" in ui.text
    assert "Review &amp; Delivery" in ui.text
    assert "data-review-delivery-view" in ui.text
    assert 'data-filter="blocked"' in ui.text
    assert 'name="lead_session_id"' not in ui.text
    assert 'data-team-view="projects"' in ui.text
    assert 'id="team-projects-page"' in ui.text
    assert 'id="create-project-from-projects-form"' in ui.text
    assert 'data-form-id="create-project-form"' in ui.text
    assert "data-projects-focus-list" in ui.text
    assert "data-projects-focus-detail" in ui.text
    assert 'data-team-view="cases"' in ui.text
    assert 'id="team-cases-page"' in ui.text
    assert 'data-team-view="governance"' in ui.text
    assert "data-governance-create-toggle" in ui.text
    assert 'id="governance-create-panel"' in ui.text
    assert 'id="create-governance-group-form"' in ui.text
    assert ui.text.count('id="create-governance-group-form"') == 1
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
    assert "renderWorkspaceDashboard" in script.text
    assert "data-team-link-view" in script.text
    assert "renderRuntimeOperations" in script.text
    assert "runtimeOperationsView" in script.text
    assert "data-runtime-filter" in script.text
    assert "needs_attention" in script.text
    assert "due_leased_agents" in script.text
    assert "blocked_waiting_work" in script.text
    assert "ephemeral_work" in script.text
    assert "renderTemplateRoleFields" in script.text
    assert "data-template-role-fields" in script.text
    assert "data-template-git-fields" in script.text
    assert "const roles = {};" in script.text
    assert "dependency_task_ids" in script.text
    assert "parent_task_id" in script.text
    assert "caseStatuses" in script.text
    assert "renderCases" in script.text
    assert "create-case-form" in script.text
    assert "case_id" in script.text
    assert "review_profile_id" in script.text
    assert "parseCriterionResults" in script.text
    assert "data-review-profile-criteria-editor" in script.text
    assert "criterion_label" in script.text
    assert "renderReviewDeliveryWorkspace" in script.text
    assert "submitReviewDecision" in script.text
    assert "bindWorkspaceReviewDecisionForms" in script.text
    assert "bindExistingAgentPickers" in script.text
    assert "data-team-inline-create-toggle" in script.text
    assert "data-team-inline-create-form" in script.text
    assert "projectLeadAgentOptionsForTeam" in script.text
    assert '"projects"' in script.text
    assert "renderProjectFocus" in script.text
    assert "data-project-focus-id" in script.text
    assert "projectReviewRoleOptionsForProject" in script.text
    assert "create-review-profile-for-project-form" in script.text
    assert "data-delegated-create-form" in script.text
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
    assert "teamFetch(`${API_BASE}/costs`" in script.text
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
    assert "ensureStateStream" in script.text
    assert "/state/stream" in script.text
    assert "applyStateSnapshot" in script.text
    assert "restoreFormDrafts(drafts)" in script.text
    assert "chatReadMarkers" in script.text
    assert "chatConversationHasUnread" in script.text
    assert "team-view-tab-badge" in script.text
    assert "syncTeamStateFromChatPayload(payload)" in script.text
    assert "new EventSource" in script.text
    assert 'document.addEventListener("visibilitychange"' in script.text
    assert 'activeTeamView === "chat"' in script.text
    assert "teamRequestTimeoutMs" in script.text
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
    assert ".team-inline-create-form" in stylesheet.text
    assert ".projects-workbench" in stylesheet.text
    assert ".projects-focus-row" in stylesheet.text
    assert ".project-review-profile-form" in stylesheet.text
    assert ".field-tooltip" in stylesheet.text
    assert ".chat-conversation-row.has-unread" in stylesheet.text
    assert ".chat-unread-badge" in stylesheet.text
    assert ".team-view-tab.has-unread" in stylesheet.text
    assert ".field-tooltip-bubble" in stylesheet.text
    assert ".cost-counter" in stylesheet.text
    assert ".cost-counter--warning" in stylesheet.text
    assert ".team-cost-blocked-notice" in stylesheet.text
    assert ".state-pill.neutral" in stylesheet.text

    org_flow_script = enabled.get("/addons/team-addon/team-org-flow.js")
    assert org_flow_script.status_code == 200
    assert "team-org-flow:edit-agent" in org_flow_script.text
    assert "data-team-org-flow-canvas" in org_flow_script.text

    org_flow_stylesheet = enabled.get("/addons/team-addon/team-org-flow.css")
    assert org_flow_stylesheet.status_code == 200
    assert ".team-org-flow-canvas" in org_flow_stylesheet.text
    assert ".org-flow-governance-node" in org_flow_stylesheet.text
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

    org_view = client.get("/api/addons/team-addon/views/org")
    assert org_view.status_code == 200
    org_state = org_view.json()["data"]["state"]
    assert "portfolio" in org_state["governance_groups"]
    assert agenda_id in org_state["governance_agenda"]
    assert "governance_runtimes" in org_state

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
    manager_id = "agent-team-platform-team-platform-manager"
    assert manager_id in body["agents"]

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
        "/api/addons/team-addon/projects",
        json={
            "team_id": "team-platform-team",
            "name": "Manager Led",
            "lead_agent_id": manager_id,
            "expected_revision": body["revision"],
        },
    )
    assert response.status_code == 201
    body = response.json()
    manager_project_id = "project-team-platform-team-manager-led"
    assert body["projects"][manager_project_id]["lead_agent_id"] == manager_id
    manager = body["agents"][manager_id]
    assert manager["project_ids"] == [manager_project_id]
    assert manager["lead_project_ids"] == [manager_project_id]
    assert manager["role_id"] == "team-platform-team__manager"
    assert manager["reports_to_agent_id"] is None
    assert manager_id not in manager["subordinate_agent_ids"]

    response = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "explicit-team",
            "name": "Explicit Team",
            "manager_agent_id": "explicit-manager",
            "manager_session_id": "session-explicit-manager",
            "manager_role_id": "explicit-manager-role",
            "expected_revision": 4,
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
    session_repo = JsonSessionManifestRepository(runtime_dir / "sessions.json")
    session_repo.upsert_session(
        SessionManifest(
            session_id="session-backend",
            vm_id="vm-backend",
            allowed_tools=[],
            expires_at=datetime(2025, 12, 31, tzinfo=UTC),
        )
    )
    session_repo.upsert_session(
        SessionManifest(
            session_id="session-lead",
            vm_id="vm-lead",
            allowed_tools=[],
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        )
    )
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
    assert backend["session_is_expired"] is True
    assert backend["session_expires_at"] == "2025-12-31T00:00:00+00:00"

    attention = {row["kind"]: row for row in state["runtime_operations"]["attention"]}
    assert set(attention) == {
        "expired_agent_session",
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
    assert attention["expired_agent_session"]["agent_id"] == "backend"
    assert attention["expired_agent_session"]["session_id"] == "session-backend"
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
        row["kind"] != "failed_action_record" for row in state["runtime_operations"]["attention"]
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


def test_team_addon_admin_api_case_flow(tmp_path: Path) -> None:
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

    response = client.post(
        "/api/addons/team-addon/cases",
        json={
            "id": "case",
            "team_id": "team",
            "project_id": "project",
            "title": "Customer ticket",
            "description": "External support ticket.",
            "status": "triage",
            "priority": "high",
            "owner_agent_id": "lead",
            "source": "github",
            "source_ref": "issue-42",
            "tags": ["support", "bug"],
            "expected_revision": 4,
        },
    )
    assert response.status_code == 201
    state = response.json()
    assert state["cases"]["case"]["status"] == "triage"
    assert state["cases"]["case"]["source_ref"] == "issue-42"

    response = client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "task",
            "owner_agent_id": "backend",
            "project_id": "project",
            "case_id": "case",
            "priority": 10,
            "title": "Investigate ticket",
            "description": "Reproduce and fix the issue.",
            "created_by_agent_id": "lead",
            "expected_revision": 5,
        },
    )
    assert response.status_code == 201
    state = response.json()
    assert state["tasks"]["task"]["case_id"] == "case"
    assert state["cases"]["case"]["task_ids"] == ["task"]

    response = client.patch(
        "/api/addons/team-addon/cases/case",
        json={
            "status": "in_progress",
            "description": "",
            "source_ref": None,
            "task_ids": [],
            "expected_revision": 6,
        },
    )
    assert response.status_code == 200
    state = response.json()
    assert state["cases"]["case"]["status"] == "in_progress"
    assert state["cases"]["case"]["description"] == ""
    assert state["cases"]["case"]["source_ref"] is None
    assert state["cases"]["case"]["task_ids"] == []
    assert state["tasks"]["task"]["case_id"] is None


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
    created = next(entry for entry in timeline["entries"] if entry["id"] == "task:task:created")
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
    _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
    )
    _team_admin_service(repo).create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
    )
    _team_admin_service(repo).create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
    )
    _team_admin_service(repo).register_agent(
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
    assert runtime["context_clearing_policy"] == "idle"
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
    assert runtime["context_clearing_policy"] == "off"
    assert runtime["auto_context_clearing_enabled"] is False
    assert runtime["context_rotation_pending"] is False
    assert runtime["context_rotation_reason"] is None
    assert runtime["context_rotation_requested_at"] is None
    assert runtime["next_wake_at"] is None
    assert runtime["last_handoff_summary"] is None

    response = client.patch(
        "/api/addons/team-addon/agents/backend",
        json={
            "context_clearing_policy": "every_wake",
            "expected_revision": 6,
        },
    )

    assert response.status_code == 200
    runtime = response.json()["agent_runtimes"]["backend"]
    assert runtime["context_clearing_policy"] == "every_wake"
    assert runtime["auto_context_clearing_enabled"] is True
    assert runtime["context_rotation_pending"] is False
    assert runtime["next_wake_at"] is None


def test_team_addon_admin_api_manual_context_rotation_request(tmp_path: Path) -> None:
    client = _client(tmp_path)
    runtime_dir = tmp_path / "runtime"
    repo = TeamStateRepository(runtime_dir)
    _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
    )
    _team_admin_service(repo).create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
    )
    _team_admin_service(repo).create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
    )
    _team_admin_service(repo).register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
    )
    _team_admin_service(repo).create_or_reprioritize_task(
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
    _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
    )
    _team_admin_service(repo).create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
    )
    _team_admin_service(repo).create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
    )
    _team_admin_service(repo).register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
    )
    _team_admin_service(repo).update_agent(agent_id="backend", status=AgentStatus.ARCHIVED)

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
    _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
    )
    _team_admin_service(repo).create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
    )
    _team_admin_service(repo).create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
    )
    _team_admin_service(repo).register_agent(
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
    assert body["teams"]["team-auto-dev-squad"]["project_ids"] == ["project-auto-dev-squad"]
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


def test_team_addon_admin_api_materializes_accounting_department(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/templates/accounting-department/materialize",
        json=_accounting_department_materialization_payload(),
    )
    assert response.status_code == 201
    body = response.json()
    materialized = body["materialized"]

    assert materialized["success"] is True
    assert materialized["template_id"] == "accounting-department"
    assert materialized["template_name"] == "Accounting Department"
    assert materialized["revision"] == 1
    assert body["revision"] == 1
    assert set(materialized["agent_ids"]) == {
        "manager",
        "project_lead",
        "bookkeeping_clerk",
        "invoice_reviewer",
        "payment_preparer",
        "tax_compliance_reviewer",
    }
    assert len(body["agents"]) == 6
    assert len(body["projects"]) == 1
    assert len(body["review_profiles"]) == 1
    assert len(body["tasks"]) == 5

    project = body["projects"][materialized["project_id"]]
    assert project["git"] is None
    assert project["lead_agent_id"] == materialized["agent_ids"]["project_lead"]
    assert project["goals"][0]["id"] == materialized["goal_ids"][0]

    roles = body["roles"]
    assert roles[materialized["role_ids"]["payment_preparer"]]["permissions"] == [
        "prepare_bookkeeping_action",
        "request_payment_preparation",
    ]
    assert roles[materialized["role_ids"]["tax_compliance_reviewer"]]["permissions"] == [
        "review_bookkeeping_action"
    ]

    assert body["teams"][materialized["team_id"]]["project_ids"] == [materialized["project_id"]]
    review_profile = body["review_profiles"][materialized["review_profile_ids"][0]]
    assert review_profile["applicable_role_ids"] == [
        materialized["role_ids"]["invoice_reviewer"],
        materialized["role_ids"]["tax_compliance_reviewer"],
    ]
    assert [criterion["id"] for criterion in review_profile["criteria"]] == [
        "supporting_documentation",
        "bokio_mapping",
        "tax_treatment",
        "approval_boundary",
    ]
    assert (
        body["tasks"][f"{materialized['project_id']}-prepare-bank-payments"]["owner_agent_id"]
        == materialized["agent_ids"]["payment_preparer"]
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
    assert response.json()["teams"]["team"]["operator_communication_target_id"] == "ops-channel"
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
    assert response.json()["teams"]["team"]["operator_communication_target_id"] == "ops-channel-2"
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


def test_team_addon_admin_api_imports_project_git_repo(tmp_path: Path) -> None:
    source_repo = tmp_path / "source-repo"
    source_repo.mkdir()
    _git(source_repo, "init", "-q", "-b", "main")
    _git(source_repo, "config", "user.email", "team@example.invalid")
    _git(source_repo, "config", "user.name", "Team Test")
    (source_repo / "README.md").write_text("# Source\n", encoding="utf-8")
    _git(source_repo, "add", "README.md")
    _git(source_repo, "commit", "-q", "-m", "initial")
    source_commit = _git(source_repo, "rev-parse", "refs/heads/main").stdout.strip()

    config_path = _write_config(tmp_path, enable_team_addon=True)
    service = build_admin_runtime(config_path)
    service.project_dir = PROJECT_ROOT
    service.vm_agent_support.project_dir = PROJECT_ROOT
    git_root = tmp_path / "git-remotes"
    DirectoryConfigStore(
        service.runtime.directories,
        runtime_dir=service.config.runtime_dir,
    ).write(
        "root",
        domain="issue_setup",
        payload={
            "git_root": str(git_root),
            "git_host": "192.168.250.1",
            "git_user": "agent",
            "git_ssh_alias": "agent-sandbox-git-host",
        },
    )
    client = TestClient(create_app(service))

    response = client.post(
        "/api/addons/team-addon/projects/git/import",
        json={
            "repo_source": str(source_repo),
            "bare_repo_name": "team-project.git",
        },
    )

    assert response.status_code == 201
    body = response.json()
    imported = body["imported"]
    bare_repo = git_root / "team-project.git"
    assert imported["host_repo_path"] == str(source_repo.resolve())
    assert imported["bare_repo_name"] == "team-project.git"
    assert imported["bare_repo_path"] == str(bare_repo)
    assert imported["base_branch"] == "main"
    assert body["git"] == {
        "host_repo_path": str(source_repo.resolve()),
        "bare_repo_name": "team-project.git",
        "base_branch": "main",
    }
    assert _git_dir(bare_repo, "rev-parse", "refs/heads/main").stdout.strip() == source_commit
    assert _git_dir(
        bare_repo, "config", "--get", "agent-sandbox.trustedRepoPath"
    ).stdout.strip() == str(source_repo.resolve())
    assert _git_dir(
        bare_repo, "config", "--get", "agent-sandbox.mirrorSource"
    ).stdout.strip() == str(source_repo)
    (source_repo / "README.md").write_text("# Source\n\nRefresh\n", encoding="utf-8")
    _git(source_repo, "add", "README.md")
    _git(source_repo, "commit", "-q", "-m", "refresh")
    refreshed_commit = _git(source_repo, "rev-parse", "refs/heads/main").stdout.strip()

    refresh_response = client.post(
        "/api/addons/team-addon/projects/git/import",
        json={
            "repo_source": str(source_repo),
            "bare_repo_name": "team-project.git",
        },
    )

    assert refresh_response.status_code == 201
    assert _git_dir(bare_repo, "rev-parse", "refs/heads/main").stdout.strip() == refreshed_commit

    _git_dir(bare_repo, "update-ref", "refs/heads/agents/session-1/work", refreshed_commit)

    blocked_response = client.post(
        "/api/addons/team-addon/projects/git/import",
        json={
            "repo_source": str(source_repo),
            "bare_repo_name": "team-project.git",
        },
    )

    assert blocked_response.status_code == 409
    assert "already has agent branches" in blocked_response.json()["error"]
    assert (
        _git_dir(bare_repo, "rev-parse", "refs/heads/agents/session-1/work").stdout.strip()
        == refreshed_commit
    )


def test_team_addon_admin_api_imports_project_git_repo_in_threadpool(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(tmp_path, enable_team_addon=True)
    service = build_admin_runtime(config_path)
    calls: dict[str, object] = {}

    def fake_import_git_repo(
        *,
        repo_source: str,
        bare_repo_name: str | None = None,
        directory_path: str = "root",
    ) -> dict[str, object]:
        calls["import"] = {
            "repo_source": repo_source,
            "bare_repo_name": bare_repo_name,
            "directory_path": directory_path,
        }
        return {
            "success": True,
            "git": {
                "host_repo_path": repo_source,
                "bare_repo_name": bare_repo_name,
            },
        }

    async def fake_run_in_threadpool(func, *args, **kwargs):
        calls["threadpool"] = True
        return func(*args, **kwargs)

    monkeypatch.setattr(service, "import_git_repo", fake_import_git_repo)
    monkeypatch.setattr(team_addon_api, "run_in_threadpool", fake_run_in_threadpool)
    client = TestClient(create_app(service))

    response = client.post(
        "/api/addons/team-addon/projects/git/import",
        json={
            "repo_source": "/srv/repos/project",
            "bare_repo_name": "project.git",
            "directory_path": "root",
        },
    )

    assert response.status_code == 201
    assert calls["threadpool"] is True
    assert calls["import"] == {
        "repo_source": "/srv/repos/project",
        "bare_repo_name": "project.git",
        "directory_path": "root",
    }


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


def test_team_addon_admin_api_reassigns_team_manager(tmp_path: Path) -> None:
    client = _client(tmp_path)
    create_team = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    )
    assert create_team.status_code == 201
    create_project = client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "expected_revision": create_team.json()["revision"],
        },
    )
    assert create_project.status_code == 201

    response = client.patch(
        "/api/addons/team-addon/teams/team",
        json={
            "manager_agent_id": "lead",
            "expected_revision": create_project.json()["revision"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["teams"]["team"]["manager_agent_id"] == "lead"
    assert body["agents"]["lead"]["reports_to_agent_id"] is None
    assert body["agents"]["manager"]["subordinate_agent_ids"] == []

    directive = client.post(
        "/api/addons/team-addon/operator-directives",
        json={
            "team_id": "team",
            "message": "Check project state.",
            "expected_revision": body["revision"],
        },
    )
    assert directive.status_code == 201
    assert directive.json()["operator_directive"]["recipient_agent_id"] == "lead"


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
        current.deliverables[
            "deliverable"
        ].git_refs.feature_branch = "team/team/features/task-backend"
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
    assert payload["release_packages"]["release"]["deliverable_ids"] == ["deliverable"]
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
                {
                    "id": "logging",
                    "label": "Logging",
                    "description": "No secrets",
                    "required": False,
                },
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


def test_team_addon_admin_api_exposes_resource_tokens(tmp_path: Path) -> None:
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

    state = client.get("/api/addons/team-addon/state").json()

    token = state["resource_tokens"]["teams"]["team"]
    assert token.startswith("sha256:")
    assert state["teams"]["team"]["resource_token"] == token


def test_team_addon_mutation_can_return_scoped_view_envelope(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/teams",
        headers={"X-Team-Addon-Response-Scope": "view"},
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["response_scope"] == "view"
    assert payload["revision"] == 1
    assert "people" in payload["invalidated_views"]
    assert "teams" not in payload
    assert "runtime_operations" not in payload


def _seed_rebase_backend_agent(client: TestClient) -> dict:
    team = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    )
    assert team.status_code == 201
    project = client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "expected_revision": team.json()["revision"],
        },
    )
    assert project.status_code == 201
    role = client.post(
        "/api/addons/team-addon/roles",
        json={
            "id": "team__backend",
            "team_id": "team",
            "name": "Backend",
            "expected_revision": project.json()["revision"],
        },
    )
    assert role.status_code == 201
    agent = client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role_id": "team__backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "expected_revision": role.json()["revision"],
        },
    )
    assert agent.status_code == 201
    return agent.json()


def test_team_addon_admin_api_rebases_stale_create_when_target_is_new(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    team = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    ).json()
    stale_revision = team["revision"]
    bump = client.post(
        "/api/addons/team-addon/roles",
        json={
            "id": "team__reviewer",
            "team_id": "team",
            "name": "Reviewer",
            "expected_revision": stale_revision,
        },
    )
    assert bump.status_code == 201

    response = client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "expected_revision": stale_revision,
            "conflict_policy": "target_unchanged",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["rebased_from_revision"] == stale_revision
    assert payload["projects"]["project"]["name"] == "Project"


def test_team_addon_admin_api_rejects_stale_no_id_create(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    team = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    ).json()
    stale_revision = team["revision"]
    bump = client.post(
        "/api/addons/team-addon/roles",
        json={
            "id": "team__reviewer",
            "team_id": "team",
            "name": "Reviewer",
            "expected_revision": stale_revision,
        },
    )
    assert bump.status_code == 201

    response = client.post(
        "/api/addons/team-addon/projects",
        json={
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "expected_revision": stale_revision,
            "conflict_policy": "target_unchanged",
        },
    )

    assert response.status_code == 409
    assert response.json()["current_revision"] == bump.json()["revision"]


def test_team_addon_admin_api_rebases_stale_patch_when_target_token_matches(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    team = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    ).json()
    stale_revision = team["revision"]
    base_token = team["resource_tokens"]["teams"]["team"]
    bump = client.post(
        "/api/addons/team-addon/roles",
        json={
            "id": "team__reviewer",
            "team_id": "team",
            "name": "Reviewer",
            "expected_revision": stale_revision,
        },
    )
    assert bump.status_code == 201

    response = client.patch(
        "/api/addons/team-addon/teams/team",
        json={
            "operator_communication_target_id": "ops",
            "expected_revision": stale_revision,
            "conflict_policy": "target_unchanged",
            "base_resource_token": base_token,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["rebased_from_revision"] == stale_revision
    assert payload["teams"]["team"]["operator_communication_target_id"] == "ops"


def test_team_addon_admin_api_rejects_stale_patch_when_target_changed(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    team = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    ).json()
    stale_revision = team["revision"]
    base_token = team["resource_tokens"]["teams"]["team"]
    changed = client.patch(
        "/api/addons/team-addon/teams/team",
        json={"name": "Changed", "expected_revision": stale_revision},
    )
    assert changed.status_code == 200

    response = client.patch(
        "/api/addons/team-addon/teams/team",
        json={
            "operator_communication_target_id": "ops",
            "expected_revision": stale_revision,
            "conflict_policy": "target_unchanged",
            "base_resource_token": base_token,
        },
    )

    assert response.status_code == 409
    assert response.json()["current_revision"] == changed.json()["revision"]


def test_team_addon_admin_api_rejects_stale_agent_patch_when_assignment_changed(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    state = _seed_rebase_backend_agent(client)
    stale_revision = state["revision"]
    base_token = state["resource_tokens"]["agents"]["backend"]
    other_project = client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project-2",
            "team_id": "team",
            "name": "Project 2",
            "lead_agent_id": "lead-2",
            "lead_session_id": "session-lead-2",
            "expected_revision": stale_revision,
        },
    )
    assert other_project.status_code == 201
    changed = client.patch(
        "/api/addons/team-addon/agents/backend",
        json={
            "project_id": "project-2",
            "expected_revision": other_project.json()["revision"],
        },
    )
    assert changed.status_code == 200
    assert changed.json()["resource_tokens"]["agents"]["backend"] != base_token

    response = client.patch(
        "/api/addons/team-addon/agents/backend",
        json={
            "display_name": "Backend 2",
            "expected_revision": stale_revision,
            "conflict_policy": "target_unchanged",
            "base_resource_token": base_token,
        },
    )

    assert response.status_code == 409
    assert response.json()["current_revision"] == changed.json()["revision"]


def test_team_addon_admin_api_rejects_stale_agent_patch_when_runtime_changed(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    state = _seed_rebase_backend_agent(client)
    stale_revision = state["revision"]
    base_token = state["resource_tokens"]["agents"]["backend"]
    changed = client.patch(
        "/api/addons/team-addon/agents/backend",
        json={
            "context_clearing_policy": "idle",
            "expected_revision": stale_revision,
        },
    )
    assert changed.status_code == 200
    assert changed.json()["resource_tokens"]["agents"]["backend"] != base_token

    response = client.patch(
        "/api/addons/team-addon/agents/backend",
        json={
            "display_name": "Backend 2",
            "expected_revision": stale_revision,
            "conflict_policy": "target_unchanged",
            "base_resource_token": base_token,
        },
    )

    assert response.status_code == 409
    assert response.json()["current_revision"] == changed.json()["revision"]


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
