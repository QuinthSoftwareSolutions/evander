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
