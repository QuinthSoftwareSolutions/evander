import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from evander_browser_sessions_addon.models import (
    BrowserCommand,
    BrowserError,
    CreateRequest,
    MessageRequest,
    Settings,
)
from evander_browser_sessions_addon.state import Store
from evander.session_dependencies import SessionDependencies
import httpx


def test_create_is_idempotent_and_worker_placement_is_pinned(domain, hosted):
    service, _, peers = domain
    same = service.create(
        "agent-a",
        CreateRequest(
            mode="hosted",
            request_id="create",
            profile_id="web",
            target_aliases=["app"],
            entry_url="http://preview-app:4200",
        ),
    )
    assert same["id"] == hosted.id
    assert (
        sum(call[2] == "POST" and call[3].startswith("/v1/sessions/") for call in peers.calls) == 1
    )
    revision, settings = service.store.settings(hosted.directory)
    settings.workers["worker-a"].base_url = "https://replacement.test"
    service.store.save_settings(hosted.directory, revision, settings)
    service.close(hosted.id)
    assert (
        next(call[0] for call in peers.calls if call[2] == "DELETE")
        == "https://worker.test/browser"
    )


def test_quota_and_request_identity_prevent_duplicate_resources(domain, hosted):
    service, _, _ = domain
    with pytest.raises(BrowserError, match="quota"):
        service.create(
            "agent-b",
            CreateRequest(
                mode="hosted",
                request_id="next",
                profile_id="web",
                target_aliases=["app"],
                entry_url="http://preview-app:4200",
            ),
        )
    with pytest.raises(BrowserError, match="different inputs"):
        service.create(
            "agent-a",
            CreateRequest(
                mode="hosted",
                request_id="create",
                profile_id="web",
                target_aliases=["other"],
                entry_url="http://other-app:4200",
            ),
        )


def test_local_invitation_is_atomic_single_use_and_does_not_authorize_hosted_view(domain, local):
    service, _, _ = domain
    invitation = service.invite(local.id, "agent:agent-a")["invitation_url"].split("invite=")[1]

    def pair():
        try:
            return service.pair(invitation, "http://localhost:4200/stations/1")
        except BrowserError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: pair(), range(2)))
    result = next(item for item in results if item)
    assert sum(item is not None for item in results) == 1
    assert result["session"]["controller"] is None
    assert result["session"]["allowed_origins"] == ["http://localhost:4200"]
    assert result["session"]["viewer_url"] is None
    with pytest.raises(BrowserError):
        service.store.authenticate(result["host_token"], "hosted_endpoint")
    with sqlite3.connect(service.store.path) as db:
        stored = " ".join(str(row) for row in db.execute("SELECT * FROM capabilities"))
    assert invitation not in stored and result["host_token"] not in stored


@pytest.mark.parametrize("legacy_support", [None, "browser-support-legacy"])
def test_local_creator_observes_and_requires_explicit_revocable_host_control(
    domain, local, legacy_support
):
    service, _, peers = domain
    assert local.support_agent_session_id is None
    if legacy_support:
        service.store.mutate(
            local.id, "test", lambda s: setattr(s, "support_agent_session_id", legacy_support)
        )
    token = service.invite(local.id, "agent:agent-a")["invitation_url"].split("invite=")[1]
    paired = service.pair(token, "https://app.test/")
    principal = paired["session"]["host_principal"]
    generation = paired["session"]["control_generation"]
    assert service.agent_session("agent-a", local.id, "browser_session_observe").id == local.id
    service.command(
        local.id,
        "agent:agent-a",
        BrowserCommand(id="observe", generation=generation, action="observe"),
        agent=True,
    )
    with pytest.raises(BrowserError, match="different agent"):
        service.agent_session("agent-b", local.id, "browser_session_observe")
    if legacy_support:
        with pytest.raises(BrowserError, match="different agent"):
            service.agent_session(legacy_support, local.id, "browser_session_act")
    with pytest.raises(BrowserError, match="local host"):
        service.control(local.id, "agent:agent-a", "grant")
    with pytest.raises(BrowserError, match="local host"):
        service.control(local.id, "guest:other", "grant", host=True)
    before = len(peers.calls)
    with pytest.raises(BrowserError, match="not been granted"):
        service.command(
            local.id,
            "agent:agent-a",
            BrowserCommand(
                id="before-grant",
                generation=generation,
                action="click",
                observation_id="current",
                ref="node-1",
            ),
            agent=True,
        )
    assert len(peers.calls) == before
    granted = service.control(local.id, principal, "grant", host=True)
    assert granted["controller"] == "agent:agent-a"
    assert service.agent_session("agent-a", local.id, "browser_session_act").id == local.id
    service.command(
        local.id,
        "agent:agent-a",
        BrowserCommand(
            id="authorized",
            generation=granted["control_generation"],
            action="click",
            observation_id="current",
            ref="node-1",
        ),
        agent=True,
    )
    with pytest.raises(BrowserError, match="outside the approved"):
        service.command(
            local.id,
            "agent:agent-a",
            BrowserCommand(
                id="other-origin",
                generation=granted["control_generation"],
                action="navigate",
                url="https://other.test/",
            ),
            agent=True,
        )
    paused = service.control(local.id, principal, "pause", host=True)
    assert paused["controller"] is None
    assert paused["control_generation"] > granted["control_generation"]
    with pytest.raises(BrowserError, match="not been granted"):
        service.command(
            local.id,
            "agent:agent-a",
            BrowserCommand(
                id="after-pause",
                generation=paused["control_generation"],
                action="click",
                observation_id="current",
                ref="node-1",
            ),
            agent=True,
        )


@pytest.mark.parametrize("playwright", [False, True])
def test_takeover_fences_in_flight_actions_and_only_one_human_controls(domain, hosted, playwright):
    service, _, peers = domain
    if playwright:
        private = service.store.private(hosted.id)
        private["profile"]["playwright_enabled"] = True
        service.store.set_private(hosted.id, private)
    service.control(hosted.id, "user:alice", "claim", revision=hosted.revision)
    with pytest.raises(BrowserError, match="Another participant"):
        service.control(hosted.id, "user:bob", "claim")
    before = len(peers.calls)
    with pytest.raises(BrowserError):
        service.command(
            hosted.id,
            "agent:agent-a",
            BrowserCommand(
                id="old-action",
                generation=hosted.control_generation,
                action="navigate",
                url="http://preview-app:4200/",
            ),
            agent=True,
        )
    assert len(peers.calls) == before
    assert service.control(hosted.id, "user:alice", "handback")["controller"] == "agent:agent-a"


def test_action_replay_denied_and_audit_omits_form_values(domain, hosted):
    service, _, peers = domain
    command = BrowserCommand(
        id="fill-once",
        generation=hosted.control_generation,
        action="fill",
        observation_id="observation",
        ref="node-1",
        text="sensitive-value",
    )
    service.command(hosted.id, "agent:agent-a", command, agent=True)
    with pytest.raises(BrowserError, match="already submitted"):
        service.command(hosted.id, "agent:agent-a", command, agent=True)
    with sqlite3.connect(service.store.path) as db:
        rows = db.execute("SELECT payload FROM audit WHERE kind='browser_action'").fetchall()
    assert len(rows) == 1 and "sensitive-value" not in str(rows)


def test_viewer_messages_resume_same_task_without_merging_transcripts(domain, hosted):
    service, bindings, _ = domain
    message = service.enqueue_message(
        hosted.id,
        "user:alice",
        MessageRequest(
            request_id="msg",
            body="Move this button",
            annotation={"x": 120, "y": 80, "url": "http://preview-app:4200/"},
        ),
    )
    service.dispatch_messages()
    service.dispatch_messages()
    assert len(bindings.prompts) == 1
    assert bindings.prompts[0][1]["annotation"] is not None
    service.reply("agent-a", hosted.id, message["id"], "Moved.")
    service.reply("agent-a", hosted.id, message["id"], "Moved.")
    messages = service.store.messages(hosted.id)
    assert [item["body"] for item in messages] == ["Move this button", "Moved."]
    assert service.store.messages(hosted.id, "teams") == []
    assert service.store.messages(hosted.id, "discord") == []
    with pytest.raises(BrowserError):
        service.store.message(
            hosted.id,
            "teams",
            "agent:agent-a",
            "bad-reply",
            "Wrong channel",
            reply_to=message["id"],
        )


def test_idle_deadline_ignores_polling_and_cleanup_failure_blocks_workspace_deletion(
    domain, hosted, tmp_path, monkeypatch
):
    service, bindings, peers = domain
    peers.fail_delete = True
    failed = service.close(hosted.id)
    assert failed["state"] == "cleanup_failed"
    monkeypatch.setenv("EVANDER_BROWSER_RELAY_TOKEN", "private")
    registry = SessionDependencies(tmp_path)
    with pytest.raises(ValueError, match="preserved"):
        registry.release(
            "agent-a",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"released": False})
            ),
        )
    assert registry.path.exists()
    peers.fail_delete = False
    service.close(hosted.id)
    registry.release(
        "agent-a",
        transport=httpx.MockTransport(lambda request: pytest.fail("Callback should be removed")),
    )
    assert service.store.get(hosted.id).state == "closed"


def test_expiry_and_deleted_owner_release_resources(domain, hosted):
    service, bindings, _ = domain
    bindings.denied.add("agent-a")
    service.reconcile()
    assert service.store.get(hosted.id).state == "expired"


def test_settings_inheritance_revision_and_retention_prune(tmp_path):
    store = Store(tmp_path)
    assert store.save_settings("root", 0, Settings(enabled=True)) == 1
    assert store.settings("root/child")[0] == 1
    assert store.save_settings("root/child", 1, Settings(enabled=False)) == 2
    with pytest.raises(BrowserError):
        store.save_settings("root/child", 1, Settings())
    assert store.settings("root/other")[1].enabled


def test_pause_cannot_be_blocked_by_concurrent_chat_or_action_revisions(domain, hosted):
    service, _, _ = domain
    service.control(hosted.id, "user:alice", "claim")
    paused = service.control(
        hosted.id, "user:alice", "pause", revision=hosted.revision, generation=0
    )
    assert paused["controller"] is None
    with pytest.raises(BrowserError, match="control changed"):
        service.control(hosted.id, "user:alice", "handback", generation=0)


def test_viewer_queue_is_bounded_without_counting_an_idempotent_retry_twice(domain, hosted):
    service, _, _ = domain
    for index in range(20):
        request = MessageRequest(request_id="queued-" + str(index), body="Question " + str(index))
        service.enqueue_message(hosted.id, "user:alice", request)
    service.enqueue_message(hosted.id, "user:alice", request)
    with pytest.raises(BrowserError) as failure:
        service.enqueue_message(
            hosted.id, "user:alice", MessageRequest(request_id="overflow", body="Another question")
        )
    assert failure.value.status == 429
    assert len(service.store.messages(hosted.id)) == 20


def test_remote_typing_preserves_spaces_and_line_breaks():
    for text in (" ", "  indented value  ", "\n"):
        command = BrowserCommand(
            id="typing", generation=0, action="type", observation_id="current", text=text
        )
        assert command.text == text


@pytest.mark.parametrize("blocked", ["closed", "expired", "leased", "retry"])
def test_old_requests_cannot_starve_another_browser_session(domain, hosted, blocked):
    service, bindings, _ = domain
    for index in range(20):
        service.enqueue_message(
            hosted.id,
            "user:alice",
            MessageRequest(request_id=f"old-{index}", body="Earlier question"),
        )
    if blocked == "closed":
        service.close(hosted.id)
    elif blocked == "expired":
        service.store.mutate(
            hosted.id, "test", lambda value: setattr(value, "expires_at", time.time() - 1)
        )
    elif blocked == "leased":
        with service.store.connect(write=True) as db:
            db.executemany(
                "INSERT INTO dispatches VALUES(?,?,?)",
                [
                    (f"browser-msg-old-{index}", "dispatching", time.time() + 60)
                    for index in range(20)
                ],
            )
    else:
        original = bindings.enqueue

        def enqueue(session, message):
            if session.id == hosted.id:
                raise BrowserError("Agent temporarily unavailable")
            return original(session, message)

        bindings.enqueue = enqueue

    fresh = service.create("agent-b", CreateRequest(mode="user_hosted", request_id="fresh"))
    message = service.enqueue_message(
        fresh["id"], "guest:bob", MessageRequest(request_id="fresh-question", body="New question")
    )
    for _ in range(3):
        service.dispatch_messages()
    assert [(session_id, request["id"]) for session_id, request in bindings.prompts] == [
        (fresh["id"], message["id"])
    ]
    if blocked in {"closed", "expired"}:
        assert {row["status"] for row in service.store.messages(hosted.id)} == {"cancelled"}


@pytest.mark.parametrize("state", ["starting", "ready", "paused", "disconnected"])
def test_worker_process_failure_reaches_the_viewer_after_startup(domain, hosted, state):
    service, _, peers = domain
    service.store.mutate(hosted.id, "test", lambda value: setattr(value, "state", state))
    original = peers.call

    def peer_call(base, token, method, path, payload):
        if method == "GET" and path.startswith("/v1/sessions/"):
            return {"state": "failed", "error": "private diagnostic"}
        return original(base, token, method, path, payload)

    peers.call = peer_call
    service.reconcile()
    failed = service.store.get(hosted.id)
    assert failed.state == "failed" and failed.controller is None
    assert failed.control_generation > hosted.control_generation
    assert "private diagnostic" not in failed.error
    assert service.close(hosted.id)["state"] == "closed"


def test_playwright_requires_a_new_recent_observation_for_each_connection(domain, hosted):
    service, _, peers = domain
    revision, settings = service.store.settings("root")
    settings.profiles["web"].playwright_enabled = True
    service.store.save_settings("root", revision, settings)
    original = peers.call

    def call(base, token, method, path, payload):
        if path.endswith("/command"):
            return {"observation_id": payload["command"]["id"]}
        if path.endswith("/connect"):
            return {"config_path": "/run/evander-resources/browser-sessions-addon/connection.json"}
        return original(base, token, method, path, payload)

    peers.call = call
    with pytest.raises(BrowserError, match="Observe"):
        service.connect("agent-a", hosted.id, "missing", hosted.control_generation)

    def observe(id):
        service.command(
            hosted.id,
            "agent:agent-a",
            BrowserCommand(id=id, generation=hosted.control_generation, action="observe"),
            agent=True,
        )

    observe("observed-one")
    assert (
        service.connect("agent-a", hosted.id, "observed-one", hosted.control_generation)["protocol"]
        == "cdp"
    )
    with pytest.raises(BrowserError, match="Observe"):
        service.connect("agent-a", hosted.id, "observed-one", hosted.control_generation)
    observe("observed-two")
    private = service.store.private(hosted.id)
    private["playwright_observation"]["observed_at"] -= 31
    service.store.set_private(hosted.id, private)
    with pytest.raises(BrowserError, match="Observe"):
        service.connect("agent-a", hosted.id, "observed-two", hosted.control_generation)


def test_worker_endpoint_withdrawal_pauses_browser_without_closing_it(domain, hosted):
    service, _, peers = domain
    original = peers.call

    def call(base, token, method, path, payload):
        if method == "GET" and path.startswith("/v1/sessions/"):
            return {
                "state": "running",
                "allowed_origins": [],
                "control_generation": hosted.control_generation + 1,
            }
        return original(base, token, method, path, payload)

    peers.call = call
    service.reconcile()
    session = service.store.get(hosted.id)
    assert session.state == "paused" and session.controller is None
    assert session.allowed_origins == [] and session.control_generation > hosted.control_generation
    assert not any(method == "DELETE" for _, _, method, _, _ in peers.calls)


def test_typed_actions_cannot_bypass_the_managed_login_tool_grant(domain, hosted):
    service, bindings, peers = domain
    original = bindings.parent
    checked = []

    def parent(agent, tool=None):
        checked.append(tool)
        if tool == "browser_session_authenticate":
            raise BrowserError("Tool not granted", "forbidden", 403)
        return original(agent, tool)

    bindings.parent = parent
    before = len(peers.calls)
    with pytest.raises(BrowserError, match="not granted"):
        service.command(
            hosted.id,
            "agent:agent-a",
            BrowserCommand(
                id="managed-login",
                generation=hosted.control_generation,
                action="authenticate",
                target_alias="app",
            ),
            agent=True,
        )
    assert checked == ["browser_session_authenticate"] and len(peers.calls) == before
