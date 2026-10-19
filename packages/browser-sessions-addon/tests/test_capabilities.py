from datetime import UTC, datetime, timedelta

from evander.admin.services.agent_capabilities import AgentCapabilityRestriction
from evander.models import SessionManifest


def test_support_agent_does_not_inherit_parent_authority():
    parent = SessionManifest(
        session_id="parent",
        allowed_tools=["get_session", "list_teams_messages"],
        directory_path="root/demo",
        expires_at=datetime.now(UTC) + timedelta(hours=4),
        connector_grants={"production": ["write"]},
        host_directory_grants={"private": ["import"]},
        allowed_contact_ids=["contact-a"],
        communication_target_ids=["teams-a"],
        teams_chat_allowlist=["chat-a"],
        allowed_script_names=["publish"],
        metadata={
            "http_targets": [{"base_url": "https://private.test"}],
            "agent_thread_id": "privileged-thread",
            "execution_backend": "container_worker_vm",
        },
        web_search_enabled=True,
        image_generation_enabled=True,
        email_send_enabled=True,
    )
    child = AgentCapabilityRestriction(
        ("browser_session_observe", "browser_session_reply"), {"browser_session_id": "browser-a"}
    ).apply(parent)
    assert set(child.allowed_tools) == {"browser_session_observe", "browser_session_reply"}
    assert child.connector_grants == {} and child.host_directory_grants == {}
    assert child.allowed_contact_ids == [] and child.communication_target_ids == []
    assert not child.allowed_script_names and not child.teams_chat_allowlist
    assert (
        not child.email_send_enabled
        and not child.web_search_enabled
        and not child.image_generation_enabled
    )
    assert child.metadata == {
        "execution_backend": "container_worker_vm",
        "capability_binding": {"browser_session_id": "browser-a"},
    }
