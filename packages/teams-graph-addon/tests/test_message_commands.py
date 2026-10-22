from datetime import UTC, datetime, timedelta
import base64
from types import SimpleNamespace

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from evander.effects.providers.base import EffectRuntimeContext
from evander.events.models import WorkflowNodeEnvelope
from evander.models import SessionManifest, ToolName
from evander.policy import PolicyError
from evander.communication_artifacts import CommunicationArtifactStore
from evander.workflow.contracts import effective_node_output_schema
from evander.workflow.plugins import load_provider_plugins
from evander.workflow.binding import resolve_workflow_bindings
from evander_teams_graph_addon.commands import TeamsMessageCommands
from evander_teams_graph_addon.message_editing import TeamsMessageEditError
from evander_teams_graph_addon.workflow import TeamsUpdateMessageEffect, materialize

from test_gateway_teams_tools import (
    make_context,
    make_request_context,
    call_tool_with_context,
    read_audit_events,
    write_root_communication_targets,
)


TARGET = "team-a/channel-1"


def context_with_session(tmp_path, **changes):
    runtime = make_context(tmp_path)
    session = SessionManifest(
        session_id="session-a",
        allowed_tools=[ToolName.TEAMS_POST_STATUS, ToolName.TEAMS_UPDATE_MESSAGE],
        teams_channel_allowlist=[TARGET, "chat:existing-chat"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    ).model_copy(update=changes)
    runtime.session_repository.upsert_session(session)
    return runtime, session


def test_mcp_ambient_session_post_edit_and_binding_conflict_are_audited(tmp_path):
    from evander.gateway import create_server

    runtime, _ = context_with_session(tmp_path)
    session = runtime.session_repository.get_session("session-a")
    runtime.session_repository.upsert_session(
        session.model_copy(
            update={"allowed_tools": [*session.allowed_tools, ToolName.TEAMS_GET_MESSAGES]}
        )
    )
    server = create_server(runtime)
    bound = make_request_context(session_id="session-a")
    posted = call_tool_with_context(
        server, "teams_post_status", {"channel_target": TARGET, "message": "Original"}, bound
    )
    updated = call_tool_with_context(
        server,
        "teams_update_message",
        {
            "channel_target": TARGET,
            "message_id": posted["message_id"],
            "message": "Next",
            "expected_revision": 1,
        },
        bound,
    )
    assert updated["status"] == "stubbed" and updated["message_revision"] == 2
    assert updated["session_id"] == "session-a"
    visible = call_tool_with_context(
        server, "teams_get_messages", {"channel_target": TARGET}, bound
    )
    assert visible["messages"][0]["content"] == "<p>Next</p>"
    with pytest.raises(ToolError, match="session_id is bound"):
        call_tool_with_context(
            server,
            "teams_update_message",
            {
                "session_id": "another-session",
                "channel_target": TARGET,
                "message_id": posted["message_id"],
                "message": "Intrusion",
            },
            bound,
        )
    events = read_audit_events(runtime.config.audit_log_path)
    edits = [e for e in events if e.get("tool_name") == "teams_update_message"]
    assert any(e["decision"] == "allowed" and e["result"]["message_revision"] == 2 for e in edits)
    assert edits[-1]["decision"] in {"denied", "error"}
    assert "session_id is bound" in edits[-1]["reason"]


@pytest.mark.parametrize(
    "denial", ["grant", "expired", "owner", "human", "conversation", "thread", "user-alias"]
)
def test_shared_command_rejects_unowned_or_unauthorized_edits(tmp_path, denial):
    runtime, session = context_with_session(tmp_path)
    posted = runtime.teams_service.post_status("Original", TARGET, "session-a")
    kwargs = dict(
        session_id="session-a",
        channel_target=TARGET,
        message_id=posted["message_id"],
        message="Next",
    )
    if denial == "grant":
        session = session.model_copy(update={"allowed_tools": [ToolName.TEAMS_POST_STATUS]})
    elif denial == "expired":
        session = session.model_copy(
            update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
        )
    elif denial == "owner":
        session = session.model_copy(update={"session_id": "session-b"})
        kwargs["session_id"] = "session-b"
    elif denial == "human":
        kwargs["message_id"] = "human-message"
    elif denial == "conversation":
        kwargs["channel_target"] = "chat:existing-chat"
    elif denial == "thread":
        kwargs["thread_id"] = "wrong-thread"
    else:
        kwargs["channel_target"] = "user:person@example.com"
    runtime.session_repository.upsert_session(session)
    with pytest.raises(PolicyError):
        TeamsMessageCommands(runtime).update_message(**kwargs)
    assert (
        runtime.teams_messages.get_outbound_edit_state(TARGET, posted["message_id"]).revision == 1
    )
    event = read_audit_events(runtime.config.audit_log_path)[-1]
    assert event["decision"] == "denied" and event["tool_name"] == "teams_update_message"


@pytest.mark.parametrize("reply", [False, True])
def test_edit_requires_capability_from_recorded_parent_even_if_thread_omitted(tmp_path, reply):
    runtime, session = context_with_session(tmp_path)
    write_root_communication_targets(
        runtime,
        [
            {
                "id": "teams-target",
                "provider": "teams",
                "kind": "teams",
                "address": TARGET,
                "policy": {"capabilities": ["reply" if reply else "write"]},
            }
        ],
    )
    session = session.model_copy(update={"teams_channel_allowlist": ["teams-target"]})
    runtime.session_repository.upsert_session(session)
    service = runtime.teams_service_for_session("session-a")
    root = service.post_status("Root", TARGET, "session-a")
    child = service.post_status("Child", TARGET, "session-a", thread_id="root-message")
    command = TeamsMessageCommands(runtime)
    allowed, denied = (child, root) if reply else (root, child)
    assert (
        command.update_message("session-a", "teams-target", allowed["message_id"], "Next")[
            "message_revision"
        ]
        == 2
    )
    with pytest.raises(PolicyError):
        command.update_message("session-a", "teams-target", denied["message_id"], "No")


def test_provider_errors_and_noops_use_shared_command_audit(tmp_path, monkeypatch):
    runtime, _ = context_with_session(tmp_path)
    posted = runtime.teams_service.post_status("Original", TARGET, "session-a")
    command = TeamsMessageCommands(runtime)
    assert (
        command.update_message("session-a", TARGET, posted["message_id"], "Original")["status"]
        == "unchanged"
    )

    def unavailable(**kwargs):
        raise TeamsMessageEditError("Teams message update failed (HTTP 403)")

    monkeypatch.setattr(runtime.teams_service, "update_message", unavailable)
    with pytest.raises(TeamsMessageEditError):
        command.update_message("session-a", TARGET, posted["message_id"], "Next")
    events = read_audit_events(runtime.config.audit_log_path)
    assert events[-2]["result"]["status"] == "unchanged"
    assert events[-1]["decision"] == "error" and "403" in events[-1]["reason"]


def test_chat_thread_rejected_and_stub_files_mentions_survive_edit(tmp_path, monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("Stub must not contact Graph")

    monkeypatch.setattr(httpx, "Client", forbidden)
    runtime, _ = context_with_session(tmp_path)
    post = runtime.teams_service.post_file(
        "Original", TARGET, "session-a", [{"filename": "report.pdf"}]
    )
    receipt = runtime.teams_messages.get_outbound_receipt(TARGET, post["message_id"])
    result = TeamsMessageCommands(runtime).update_message(
        "session-a", TARGET, post["message_id"], "Next"
    )
    assert result["message_revision"] == 2
    envelope = runtime.teams_messages.get(f"{TARGET}/{post['message_id']}")
    assert envelope.content.endswith(receipt.body_suffix)
    assert envelope.metadata["attachments"][0]["name"] == "report.pdf"
    mention = runtime.teams_service.post_status(
        "Original", TARGET, "session-a", mentions=["a@example.com", "b@example.com"]
    )
    TeamsMessageCommands(runtime).update_message("session-a", TARGET, mention["message_id"], "Next")
    assert (
        runtime.teams_messages.get(f"{TARGET}/{mention['message_id']}").content
        == '<p><at id="0">a@example.com</at> <at id="1">b@example.com</at></p><p>Next</p>'
    )
    chat = runtime.teams_service.post_status("Original", "chat:existing-chat", "session-a")
    with pytest.raises(PolicyError, match="thread_id"):
        TeamsMessageCommands(runtime).update_message(
            "session-a", "chat:existing-chat", chat["message_id"], "Next", thread_id="thread"
        )


def test_mcp_file_post_returns_revision_for_a_following_edit(tmp_path):
    from evander.gateway import create_server

    runtime, _ = context_with_session(
        tmp_path,
        allowed_tools=[ToolName.TEAMS_POST_FILE, ToolName.TEAMS_UPDATE_MESSAGE],
        communication_file_send_enabled=True,
        communication_file_require_approval_for_send=False,
    )
    artifact = CommunicationArtifactStore(tmp_path).stage(
        provider="workspace",
        session_id="session-a",
        original_filename="pixel.png",
        mime_type="image/png",
        content=base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/ZukAAAAASUVORK5CYII="
        ),
    )
    server = create_server(runtime)
    bound = make_request_context(session_id="session-a")
    posted = call_tool_with_context(
        server,
        "teams_post_file",
        {"channel_target": TARGET, "message": "Original", "artifact_ids": [artifact.artifact_id]},
        bound,
    )
    assert posted["message_revision"] == 1
    result = call_tool_with_context(
        server,
        "teams_update_message",
        {
            "channel_target": TARGET,
            "message_id": posted["message_id"],
            "message": "Next",
            "expected_revision": posted["message_revision"],
        },
        bound,
    )
    assert result["message_revision"] == 2


def effect_for(runtime, posted, scope="root"):
    node = WorkflowNodeEnvelope.model_validate(
        {
            "id": "update",
            "display_name": "Update progress",
            "type": "effect:teams:update-message",
            "config": {
                "session_id": "session-a",
                "channel_target": TARGET,
                "message_id": posted["message_id"],
                "message": "Next",
            },
        }
    )
    provider = TeamsUpdateMessageEffect()
    provider.initialize(
        node,
        EffectRuntimeContext(
            admin_service=SimpleNamespace(runtime=runtime),
            metadata={
                "directory_path": scope,
                "graph_id": "synthetic-graph",
                "workflow_run_id": "synthetic-run",
            },
        ),
    )
    return provider


def test_workflow_effect_shares_authorization_revision_and_audit_behavior(tmp_path):
    runtime, _ = context_with_session(tmp_path)
    posted = runtime.teams_service.post_status("Original", TARGET, "session-a")
    provider = effect_for(runtime, posted)
    bound = resolve_workflow_bindings(
        {"message": "${{progress.text}}", "expected_revision": "${{posted.message_revision}}"},
        {"progress": {"text": "Bound text"}, "posted": {"message_revision": 1}},
        {"progress": {"text"}, "posted": {"message_revision"}},
    )
    assert (
        provider.execute(
            resolved_config=bound.resolved_config,
            execution_context={"workflow_run_id": "bound-run"},
        )["message_revision"]
        == 2
    )
    assert (
        TeamsMessageCommands(runtime).update_message(
            "session-a", TARGET, posted["message_id"], "Bound text", expected_revision=1
        )["status"]
        == "unchanged"
    )
    event = read_audit_events(runtime.config.audit_log_path)[-2]
    assert event["workflow_run_id"] == "bound-run"
    assert event["args"]["workflow"]["node_id"] == "update"
    with pytest.raises(PolicyError, match="outside workflow directory scope"):
        effect_for(runtime, posted, "root/child").execute()
    with pytest.raises(PolicyError):
        provider.execute(resolved_config={"session_id": "workflow"})


def test_workflow_catalog_bindings_and_disabled_addon_without_provider_work(tmp_path, monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("Discovery must not contact Graph")

    monkeypatch.setattr(httpx, "Client", forbidden)
    catalog = load_provider_plugins(
        module_names=[],
        include_entry_points=False,
        addon_loader={
            "modules": ["evander_teams_graph_addon"],
            "package_dirs": [],
            "enabled_addon_ids": ["teams-graph"],
        },
    )
    assert catalog.effect_providers["effect:teams:update-message"] is TeamsUpdateMessageEffect
    entry = next(
        e
        for e in catalog.workflow_editor_catalog_entries
        if e.type == "effect:teams:update-message"
    )
    assert {f.key for f in entry.fields if f.required} == {
        "session_id",
        "channel_target",
        "message_id",
        "message",
    }
    assert all(f.bindable for f in entry.fields)
    node = WorkflowNodeEnvelope.model_validate(
        {
            "id": "edit",
            "display_name": "Edit",
            "type": entry.type,
            "config": {
                "session_id": {"$ref": "state.session_id"},
                "channel_target": TARGET,
                "message_id": {"$ref": "nodes.post.message_id"},
                "message": "Next",
            },
        }
    )
    definition = materialize(node)
    assert definition.session_id == {"$ref": "state.session_id"}
    schema = effective_node_output_schema(definition)
    assert schema["message_revision"] == {"type": "integer", "minimum": 1}
    assert schema["status"]["enum"] == ["updated", "unchanged", "stubbed"]
    disabled = load_provider_plugins(
        module_names=[],
        include_entry_points=False,
        addon_loader={
            "modules": ["evander_teams_graph_addon"],
            "package_dirs": [],
            "enabled_addon_ids": [],
        },
    )
    assert "effect:teams:update-message" not in disabled.effect_providers
    assert list(tmp_path.iterdir()) == []
