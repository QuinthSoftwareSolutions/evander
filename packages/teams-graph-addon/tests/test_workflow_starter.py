from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

import pytest

from config_helpers import write_runtime_config
from evander.admin.composition import build_admin_runtime
from evander.models import SessionManifest, ToolName


@pytest.fixture
def admin(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_STARTER_GRAPH_TOKEN", "synthetic-token")
    service = build_admin_runtime(
        write_runtime_config(
            tmp_path,
            teams_backend="graph_api",
            teams_graph_access_token_env="TEST_STARTER_GRAPH_TOKEN",
            addon_loader={
                "modules": ["evander_teams_graph_addon"],
                "enabled_addon_ids": ["teams-graph"],
            },
        )
    )
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="assistant",
            vm_id="vm-assistant",
            allowed_tools=[ToolName.TEAMS_POST_STATUS],
            teams_chat_allowlist=["chat:example"],
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    return service


def values(**kwargs):
    return dict(
        name="teams-assistant",
        session_id="assistant",
        target="chat:example",
        sender="person@example.invalid",
        **kwargs,
    )


def test_teams_starter_creates_scoped_sender_filter_and_preserves_permissions(admin):
    service = admin.workflow_starter_admin
    original = admin.runtime.session_repository.get_session("assistant")
    preview = service.preview("teams-graph/teams-assistant", "root", values())
    assert preview["ready"]
    assert "synthetic-token" not in json.dumps(preview)
    receipt = service.activate(
        "teams-graph/teams-assistant",
        "root",
        values(),
        preview_revision=preview["preview_revision"],
    )
    document = admin.workflow_config_admin.read_workflow_editor_document(
        workflow_pack=receipt["workflow_pack"]
    )
    trigger = document.config["triggers"][0]["config"]
    assert [target["target"] for target in trigger["targets"]] == ["chat:example"]
    assert trigger["sender_user_ids"] == ["person@example.invalid"]
    assert admin.runtime.session_repository.get_session("assistant") == original


def test_teams_starter_refuses_a_target_outside_agent_grants(admin):
    candidate = values()
    candidate["target"] = "chat:forbidden"
    with pytest.raises(ValueError, match="write access"):
        admin.workflow_starter_admin.preview("teams-graph/teams-assistant", "root", candidate)


def test_teams_verification_requires_a_correlated_successful_reply_to_selected_chat(admin):
    service = admin.workflow_starter_admin
    preview = service.preview("teams-graph/teams-assistant", "root", values())
    receipt = service.activate(
        "teams-graph/teams-assistant",
        "root",
        values(),
        preview_revision=preview["preview_revision"],
    )
    completion = SimpleNamespace(
        node_id="starter-teams-assistant-completed", status="succeeded", tool_call_links=[]
    )
    run = SimpleNamespace(
        source_node_id="starter-teams-assistant-message",
        event_id="run",
        trace_id="trace",
        occurred_at=datetime.now(UTC),
        run_state="completed",
        failed_node_id=None,
        nodes=[completion],
        tool_call_links=[],
    )
    service.reports = SimpleNamespace(read_workflow_run_detail=lambda **_: run)
    assert service.verification(receipt["id"], "root", event_id="run")["state"] == "needs_attention"
    reply = SimpleNamespace(
        tool_name="teams_post_status",
        status="success",
        workflow_run_id="other-run",
        trace_id=None,
        args={"channel_target": "chat:example"},
    )
    completion.tool_call_links = [reply]
    assert service.verification(receipt["id"], "root", event_id="run")["state"] == "needs_attention"
    reply.workflow_run_id = "run"
    reply.args = {"channel_target": "chat:other"}
    assert service.verification(receipt["id"], "root", event_id="run")["state"] == "needs_attention"
    reply.args = {"channel_target": "chat:example"}
    assert service.verification(receipt["id"], "root", event_id="run")["state"] == "verified"
