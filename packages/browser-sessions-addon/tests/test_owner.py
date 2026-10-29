import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from evander.models import SessionManifest
from evander_browser_sessions_addon.models import CreateRequest
from evander_browser_sessions_addon.service import Bindings, Service


def test_local_creation_keeps_owner_context_without_provisioning_or_new_tool_grants(domain):
    previous, _, peers = domain
    expiry = datetime.now(UTC) + timedelta(minutes=15)
    owner = SessionManifest(
        session_id="author",
        directory_path="root/demo",
        allowed_tools=["browser_session_create"],
        expires_at=expiry,
        metadata={"agent_thread_id": "existing-conversation"},
    )

    def require_tool(agent, tool):
        assert agent == owner.session_id
        if tool not in owner.allowed_tools:
            raise ValueError("Tool was not granted")

    runtime = SimpleNamespace(
        policy=SimpleNamespace(
            require_tool=require_tool,
            get_session=lambda agent: owner,
        )
    )
    admin = SimpleNamespace(
        runtime=runtime,
        create_admin_agent=lambda *args, **kwargs: pytest.fail("Must not create a support agent"),
        enqueue_agent_prompt=lambda *args, **kwargs: pytest.fail("No prompt on creation"),
    )
    service = Service(
        previous.runtime_dir,
        bindings=Bindings(previous.runtime_dir, admin),
        store=previous.store,
        peer_factory=peers,
    )
    request = CreateRequest(mode="user_hosted", request_id="local-owner")
    result = service.create("author", request)
    assert result["agent_session_id"] == "author"
    assert result["native_thread_id"] == "existing-conversation"
    assert result["support_agent_session_id"] is None
    assert result["expires_at"] <= expiry.timestamp()
    assert result["controller"] is None
    assert service.create("author", request)["id"] == result["id"]
    assert peers.calls == []
    with pytest.raises(ValueError, match="not granted"):
        service.agent_session("author", result["id"], "browser_session_observe")


def test_legacy_support_cleanup_retries_without_deleting_the_owner(domain, local):
    service, fake, _ = domain
    legacy = "browser-support-legacy"
    service.store.mutate(local.id, "test", lambda s: setattr(s, "support_agent_session_id", legacy))
    existing = {"agent-a", legacy}
    deletes = []
    fail = True

    def delete(agent, *, backend):
        assert agent == legacy and backend == "container_worker_vm"
        deletes.append(agent)
        if fail:
            raise RuntimeError("Worker unavailable")
        existing.remove(agent)
        return {"success": True}

    admin = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=SimpleNamespace(
                get_session=lambda agent: object() if agent in existing else None,
            )
        ),
        delete_admin_agent=delete,
        enqueue_agent_prompt=lambda *args, **kwargs: None,
    )
    fake.close_support = Bindings(service.runtime_dir, admin).close_support
    assert service.close(local.id)["state"] == "cleanup_failed"
    assert existing == {"agent-a", legacy}
    fail = False
    service.reconcile()
    assert service.store.get(local.id).state == "closed"
    assert existing == {"agent-a"}
    assert deletes == [legacy, legacy]


def test_saved_local_screenshot_belongs_to_creator_even_with_legacy_support(domain, local):
    service, _, peers = domain
    service.store.mutate(
        local.id, "test", lambda s: setattr(s, "support_agent_session_id", "legacy-support")
    )
    invitation = service.invite(local.id, "agent:agent-a")["invitation_url"].split("invite=")[1]
    paired = service.pair(invitation, "https://app.test/")
    generation = paired["session"]["control_generation"]
    original = peers.call

    def peer_call(base, token, method, path, payload):
        if path.endswith("/command"):
            return {"image": base64.b64encode(b"\xff\xd8fixture-jpeg").decode()}
        return original(base, token, method, path, payload)

    peers.call = peer_call
    result = service.save_screenshot(local.id, "agent:agent-a", generation)
    assert result["session_id"] == "agent-a"
    assert result["metadata"]["browser_session_id"] == local.id
