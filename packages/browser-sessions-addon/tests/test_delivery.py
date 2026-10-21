from evander_browser_sessions_addon.models import MessageRequest


def test_status_updates_are_visible_after_message_cursor_and_replies_stay_bound(domain, local):
    service, bindings, _ = domain
    bindings.enqueue = lambda session, message: {"delivery_status": "queued"}
    message = service.enqueue_message(
        local.id, "guest:host", MessageRequest(request_id="one", body="Move it")
    )
    service.dispatch_messages()
    rows = service.store.messages(local.id)
    assert rows[0]["delivery_status"] == "queued"
    cursor = rows[0]["seq"]
    bindings.delivery = lambda session, message: {"delivery_status": "steered"}
    service.dispatch_messages()
    assert service.store.messages(local.id, after=cursor) == []
    assert service.store.message_updates(local.id)[0]["delivery_status"] == "steered"
    response = service.reply(local.agent_session_id, local.id, message["id"], "Moved it")
    assert response["reply_to"] == message["id"]
    assert service.store.message_updates(local.id)[0]["status"] == "answered"


def test_close_cancels_core_queued_message_but_keeps_delivered_work(domain, local):
    service, bindings, _ = domain
    cancelled = []
    bindings.cancel_message = lambda session, message: cancelled.append(message["id"])
    bindings.enqueue = lambda session, message: {"delivery_status": "queued"}
    queued = service.enqueue_message(
        local.id, "guest:host", MessageRequest(request_id="queued", body="Wait")
    )
    service.dispatch_messages()
    bindings.enqueue = lambda session, message: {"delivery_status": "steered"}
    active = service.enqueue_message(
        local.id, "guest:host", MessageRequest(request_id="active", body="Continue")
    )
    service.dispatch_messages()
    service.close(local.id)
    assert cancelled == [queued["id"]]
    states = {row["id"]: row for row in service.store.messages(local.id)}
    assert states[queued["id"]]["delivery_status"] == "cancelled"
    assert states[active["id"]]["delivery_status"] == "steered"


def test_failed_dispatch_is_retried_with_the_same_message_identity(domain, local):
    service, bindings, _ = domain
    identities = []

    def fail_once(session, message):
        identities.append(message["id"])
        if len(identities) == 1:
            raise TimeoutError()
        return {"delivery_status": "steered"}

    bindings.enqueue = fail_once
    message = service.enqueue_message(
        local.id, "guest:host", MessageRequest(request_id="same", body="Change it")
    )
    service.dispatch_messages()
    with service.store.connect(write=True) as db:
        db.execute("UPDATE dispatches SET lease_until=0")
    service.dispatch_messages()
    assert identities == [message["id"], message["id"]]
    assert service.store.message_updates(local.id)[0]["delivery_status"] == "steered"


def test_explicit_uncertain_retry_is_authorized_and_idempotent(domain, local):
    from evander_browser_sessions_addon.models import BrowserError
    import pytest

    service, bindings, _ = domain
    cancelled = []
    bindings.enqueue = lambda session, message: {"delivery_status": "delivery_unknown"}
    bindings.delivery = lambda session, message: {"delivery_status": "delivery_unknown"}
    bindings.cancel_message = lambda session, message: cancelled.append(message["id"])
    original = service.enqueue_message(
        local.id, "guest:host", MessageRequest(request_id="original", body="Move it")
    )
    service.dispatch_messages()
    with pytest.raises(BrowserError, match="unavailable"):
        service.retry_message(local.id, "guest:other", original["id"], "retry")
    new = service.retry_message(local.id, "guest:host", original["id"], "retry")
    assert new["id"] != original["id"] and new["body"] == "Move it"
    assert service.retry_message(local.id, "guest:host", original["id"], "retry")["id"] == new["id"]
    assert cancelled == [original["id"]]
    service.dispatch_messages()
    bindings.delivery = lambda session, message: {"delivery_status": "steered"}
    with pytest.raises(BrowserError, match="already delivered"):
        service.retry_message(local.id, "guest:host", new["id"], "another")


def test_stale_status_refresh_cannot_make_a_cancelled_message_retryable(domain, local):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import pytest
    from evander_browser_sessions_addon.models import BrowserError

    service, bindings, _ = domain
    bindings.enqueue = lambda session, message: {"delivery_status": "delivery_unknown"}
    bindings.cancel_message = lambda session, message: None
    original = service.enqueue_message(
        local.id, "guest:host", MessageRequest(request_id="original", body="Move it")
    )
    service.dispatch_messages()
    refresh_started, release_refresh = threading.Event(), threading.Event()

    def stale_status(session, message):
        refresh_started.set()
        assert release_refresh.wait(5)
        return {"delivery_status": "delivery_unknown"}

    bindings.delivery = stale_status
    with ThreadPoolExecutor(max_workers=1) as pool:
        refresh = pool.submit(service.dispatch_messages)
        try:
            assert refresh_started.wait(5)
            bindings.delivery = lambda session, message: {"delivery_status": "delivery_unknown"}
            retried = service.retry_message(local.id, "guest:host", original["id"], "retry-1")
        finally:
            release_refresh.set()
        refresh.result(timeout=10)
    with pytest.raises(BrowserError) as error:
        service.retry_message(local.id, "guest:host", original["id"], "retry-2")
    assert error.value.code == "message_conflict"
    assert service.retry_message(local.id, "guest:host", original["id"], "retry-1")[
        "id"
    ] == retried["id"]
    rows = {row["id"]: row for row in service.store.messages(local.id)}
    assert rows[original["id"]]["delivery_status"] == "cancelled"
    assert len(rows) == 2


def test_retry_does_not_duplicate_a_delivery_that_wins_cancellation(domain, local):
    import pytest
    from evander_browser_sessions_addon.models import BrowserError

    service, bindings, _ = domain
    bindings.enqueue = lambda session, message: {"delivery_status": "delivery_unknown"}
    bindings.delivery = lambda session, message: {"delivery_status": "delivery_unknown"}
    bindings.cancel_message = lambda session, message: {"delivery_status": "steered"}
    original = service.enqueue_message(
        local.id, "guest:host", MessageRequest(request_id="original", body="Move it")
    )
    service.dispatch_messages()
    with pytest.raises(BrowserError) as error:
        service.retry_message(local.id, "guest:host", original["id"], "retry")
    assert error.value.code == "message_conflict"
    assert len(service.store.messages(local.id)) == 1


def test_close_preserves_delivery_that_wins_cancellation(domain, local):
    service, bindings, _ = domain
    bindings.enqueue = lambda session, message: {"delivery_status": "pending"}
    bindings.cancel_message = lambda session, message: {"delivery_status": "steered"}
    original = service.enqueue_message(
        local.id, "guest:host", MessageRequest(request_id="original", body="Move it")
    )
    service.dispatch_messages()
    service.close(local.id)
    row = service.store.messages(local.id)[0]
    assert row["id"] == original["id"] and row["delivery_status"] == "steered"


def test_status_polling_rotates_across_sessions_and_service_restarts(domain):
    from evander_browser_sessions_addon.models import CreateRequest
    from evander_browser_sessions_addon.service import Service

    service, bindings, peers = domain
    bindings.enqueue = lambda session, message: {"delivery_status": "queued"}
    messages = []
    for number in range(3):
        session = service.create(
            "agent-a", CreateRequest(mode="user_hosted", request_id=f"local-{number}")
        )
        for index in range(17):
            messages.append(service.enqueue_message(
                session["id"], "guest:host",
                MessageRequest(request_id=f"message-{number}-{index}", body="Check this"),
            ))
    for _ in range(3):
        service.dispatch_messages()
    seen = set()

    def status(session, message):
        seen.add(message["id"])
        return {"delivery_status": "steered" if message["id"] == messages[-1]["id"] else "queued"}

    bindings.delivery = status
    for _ in range(2):
        restarted = Service(service.runtime_dir, bindings=bindings, peer_factory=peers)
        restarted.dispatch_messages()
    assert seen == {message["id"] for message in messages}
    assert service.store.messages(messages[-1]["session_id"])[-1]["delivery_status"] == "steered"
