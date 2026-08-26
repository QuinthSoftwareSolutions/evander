from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx

from evander.config import GatewayConfig, TeamsIngestWorkerGlobalConfig
from evander.messaging.teams_inbox_store import (
    TeamsInboxStore,
    TeamsNotificationQueueEntry,
    TeamsOwnedSubscription,
)
from evander.messaging.teams_subscriptions import TeamsSubscriptionReconciler


_NOTIFICATION_URL = "https://notifications.example.test/msgraph/teams/notifications"
_LIFECYCLE_NOTIFICATION_URL = "https://notifications.example.test/msgraph/teams/lifecycle"


class _FakeGraphService:
    api_base_url = "https://graph.example.test/v1.0"

    def __init__(self) -> None:
        self.token = "opaque-access-token"

    def _resolve_token(self) -> str:
        return self.token

    def _request_with_retries(self, client, method, url, *, params=None, json=None):
        response = client.request(method, url, params=params, json=json)
        response.raise_for_status()
        return response


def _worker_config() -> TeamsIngestWorkerGlobalConfig:
    return TeamsIngestWorkerGlobalConfig.model_validate(
        {
            "change_notifications_enabled": True,
            "change_notification_public_base_url": "https://notifications.example.test",
        }
    )


def _provider_config(tmp_path) -> GatewayConfig:
    return GatewayConfig.model_validate(
        {
            "runtime_dir": tmp_path / "runtime",
            "teams_backend": "graph_api",
            "teams_delegated_tenant_id": "tenant-a",
        }
    )


def test_subscription_reconciler_creates_and_renews_owned_subscription(tmp_path) -> None:
    store = TeamsInboxStore(tmp_path / "runtime" / "teams-inbox-store.db")
    reconciler = TeamsSubscriptionReconciler(store=store, worker_config=_worker_config())
    requests: list[tuple[str, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content) if request.content else None
        requests.append((request.method, payload))
        if request.method == "POST":
            return httpx.Response(
                201,
                request=request,
                json={
                    "id": "owned-subscription",
                    "expirationDateTime": (datetime.now(UTC) + timedelta(hours=48)).isoformat(),
                },
            )
        if request.method == "PATCH":
            return httpx.Response(
                200,
                request=request,
                json={
                    "expirationDateTime": (datetime.now(UTC) + timedelta(hours=48)).isoformat(),
                },
            )
        return httpx.Response(204, request=request)

    now = datetime.now(UTC)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        healthy = reconciler.reconcile_directory(
            client,
            service=_FakeGraphService(),
            provider_config=_provider_config(tmp_path),
            directory_path="root",
            targets={"team-a/channel-a"},
            now=now,
        )
        assert healthy == {"team-a/channel-a"}
        created = store.get_owned_subscription("owned-subscription")
        assert created is not None
        store.upsert_owned_subscription(
            created.model_copy(update={"expires_at": now + timedelta(hours=1)})
        )
        reconciler.reconcile_directory(
            client,
            service=_FakeGraphService(),
            provider_config=_provider_config(tmp_path),
            directory_path="root",
            targets={"team-a/channel-a"},
            now=now,
        )

    create_payload = requests[0][1]
    assert create_payload is not None
    assert create_payload["changeType"] == "created"
    assert create_payload["includeResourceData"] is False
    assert create_payload["resource"] == "/teams/team-a/channels/channel-a/messages"
    assert create_payload["lifecycleNotificationUrl"].endswith("/msgraph/teams/lifecycle")
    assert [method for method, _payload in requests] == ["POST", "PATCH"]


def test_subscription_reconciler_resolves_opaque_token_tenant_through_graph(tmp_path) -> None:
    store = TeamsInboxStore(tmp_path / "runtime" / "teams-inbox-store.db")
    reconciler = TeamsSubscriptionReconciler(store=store, worker_config=_worker_config())
    requests: list[httpx.Request] = []
    now = datetime.now(UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1.0/organization":
            assert request.url.params["$select"] == "id"
            return httpx.Response(
                200,
                request=request,
                json={"value": [{"id": "tenant-from-graph"}]},
            )
        return httpx.Response(
            201,
            request=request,
            json={
                "id": "opaque-token-subscription",
                "expirationDateTime": (now + timedelta(hours=48)).isoformat(),
            },
        )

    provider_config = GatewayConfig.model_validate(
        {
            "runtime_dir": tmp_path / "runtime",
            "teams_backend": "graph_api",
        }
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        healthy = reconciler.reconcile_directory(
            client,
            service=_FakeGraphService(),
            provider_config=provider_config,
            directory_path="root",
            targets={"team-a/channel-a"},
            now=now,
        )

    assert healthy == {"team-a/channel-a"}
    assert [request.method for request in requests] == ["GET", "POST"]
    subscription = store.get_owned_subscription("opaque-token-subscription")
    assert subscription is not None
    assert subscription.tenant_id == "tenant-from-graph"


def test_subscription_reconciler_never_adopts_foreign_subscription(tmp_path) -> None:
    store = TeamsInboxStore(tmp_path / "runtime" / "teams-inbox-store.db")
    now = datetime.now(UTC)
    store.upsert_owned_subscription(
        TeamsOwnedSubscription(
            subscription_id="foreign-subscription",
            owner_id="another-deployment",
            target="team-a/channel-a",
            resource="/teams/team-a/channels/channel-a/messages",
            directory_path="root",
            tenant_id="tenant-a",
            expires_at=now + timedelta(hours=48),
            client_state_sha256="0" * 64,
            created_at=now,
            updated_at=now,
        )
    )
    reconciler = TeamsSubscriptionReconciler(store=store, worker_config=_worker_config())

    def unexpected(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected Graph request: {request.method} {request.url}")

    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        healthy = reconciler.reconcile_directory(
            client,
            service=_FakeGraphService(),
            provider_config=_provider_config(tmp_path),
            directory_path="root",
            targets={"team-a/channel-a"},
            now=now,
        )

    assert healthy == set()
    assert store.get_owned_subscription("foreign-subscription") is not None


def test_subscription_permission_failure_keeps_target_on_fast_fallback(tmp_path) -> None:
    store = TeamsInboxStore(tmp_path / "runtime" / "teams-inbox-store.db")
    reconciler = TeamsSubscriptionReconciler(store=store, worker_config=_worker_config())

    def forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request, json={"error": {"code": "Forbidden"}})

    now = datetime.now(UTC)
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        healthy = reconciler.reconcile_directory(
            client,
            service=_FakeGraphService(),
            provider_config=_provider_config(tmp_path),
            directory_path="root",
            targets={"team-a/channel-a"},
            now=now,
        )

    assert healthy == set()
    status = store.notification_status(
        enabled=True,
        renewal_margin=timedelta(hours=24),
        now=now,
    )
    assert status["targets_on_fast_fallback"] == ["team-a/channel-a"]
    assert len(status["degraded_reasons"]) == 1
    assert "403" in status["degraded_reasons"][0]


def test_subscription_reconciliation_failure_uses_persistent_target_backoff(tmp_path) -> None:
    database_path = tmp_path / "runtime" / "teams-inbox-store.db"
    worker_config = _worker_config()
    request_count = 0
    now = datetime.now(UTC)

    def fail_once_then_create(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(
                403,
                request=request,
                json={"error": {"code": "Forbidden"}},
            )
        return httpx.Response(
            201,
            request=request,
            json={
                "id": "owned-subscription",
                "expirationDateTime": (now + timedelta(hours=48)).isoformat(),
            },
        )

    first_store = TeamsInboxStore(database_path)
    first_reconciler = TeamsSubscriptionReconciler(
        store=first_store,
        worker_config=worker_config,
    )
    with httpx.Client(transport=httpx.MockTransport(fail_once_then_create)) as client:
        assert (
            first_reconciler.reconcile_directory(
                client,
                service=_FakeGraphService(),
                provider_config=_provider_config(tmp_path),
                directory_path="root",
                targets={"team-a/channel-a"},
                now=now,
            )
            == set()
        )
        state = first_store.get_subscription_reconciliation_state(
            directory_path="root",
            target="team-a/channel-a",
        )
        assert state is not None
        assert state.consecutive_failures == 1
        assert state.next_attempt_at == now + timedelta(
            seconds=worker_config.target_backoff_initial_seconds
        )

        restarted_store = TeamsInboxStore(database_path)
        restarted_reconciler = TeamsSubscriptionReconciler(
            store=restarted_store,
            worker_config=worker_config,
        )
        assert (
            restarted_reconciler.reconcile_directory(
                client,
                service=_FakeGraphService(),
                provider_config=_provider_config(tmp_path),
                directory_path="root",
                targets={"team-a/channel-a"},
                now=now + timedelta(seconds=2),
            )
            == set()
        )
        assert request_count == 1

        assert restarted_reconciler.reconcile_directory(
            client,
            service=_FakeGraphService(),
            provider_config=_provider_config(tmp_path),
            directory_path="root",
            targets={"team-a/channel-a"},
            now=state.next_attempt_at,
        ) == {"team-a/channel-a"}

    assert request_count == 2
    assert (
        restarted_store.get_subscription_reconciliation_state(
            directory_path="root",
            target="team-a/channel-a",
        )
        is None
    )


def test_reauthorization_lifecycle_event_reauthorizes_and_renews(tmp_path) -> None:
    store = TeamsInboxStore(tmp_path / "runtime" / "teams-inbox-store.db")
    reconciler = TeamsSubscriptionReconciler(store=store, worker_config=_worker_config())
    now = datetime.now(UTC)
    store.upsert_owned_subscription(
        TeamsOwnedSubscription(
            subscription_id="owned-subscription",
            owner_id=store.notification_owner_id(),
            target="chat:chat-a",
            resource="/chats/chat-a/messages",
            directory_path="root",
            tenant_id="tenant-a",
            expires_at=now + timedelta(hours=48),
            health="reauthorization_required",
            notification_url=_NOTIFICATION_URL,
            lifecycle_notification_url=_LIFECYCLE_NOTIFICATION_URL,
            client_state_sha256="0" * 64,
            created_at=now,
            updated_at=now,
        )
    )
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "PATCH":
            return httpx.Response(
                200,
                request=request,
                json={"expirationDateTime": (now + timedelta(hours=48)).isoformat()},
            )
        return httpx.Response(204, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        healthy = reconciler.reconcile_directory(
            client,
            service=_FakeGraphService(),
            provider_config=_provider_config(tmp_path),
            directory_path="root",
            targets={"chat:chat-a"},
            now=now,
        )

    assert healthy == {"chat:chat-a"}
    assert requests == [
        ("POST", "/v1.0/subscriptions/owned-subscription/reauthorize"),
        ("PATCH", "/v1.0/subscriptions/owned-subscription"),
    ]


def test_duplicate_creation_recovers_only_recorded_owned_subscription(tmp_path) -> None:
    store = TeamsInboxStore(tmp_path / "runtime" / "teams-inbox-store.db")
    reconciler = TeamsSubscriptionReconciler(store=store, worker_config=_worker_config())
    now = datetime.now(UTC)
    store.upsert_owned_subscription(
        TeamsOwnedSubscription(
            subscription_id="owned-subscription",
            owner_id=store.notification_owner_id(),
            target="team-a/channel-a",
            resource="/teams/team-a/channels/channel-a/messages",
            directory_path="root",
            tenant_id="tenant-a",
            expires_at=now - timedelta(minutes=1),
            notification_url=_NOTIFICATION_URL,
            lifecycle_notification_url=_LIFECYCLE_NOTIFICATION_URL,
            client_state_sha256="0" * 64,
            created_at=now - timedelta(days=2),
            updated_at=now - timedelta(days=2),
        )
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if calls == ["PATCH"]:
            return httpx.Response(404, request=request)
        if request.method == "POST":
            return httpx.Response(409, request=request)
        return httpx.Response(
            200,
            request=request,
            json={"expirationDateTime": (now + timedelta(hours=48)).isoformat()},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        healthy = reconciler.reconcile_directory(
            client,
            service=_FakeGraphService(),
            provider_config=_provider_config(tmp_path),
            directory_path="root",
            targets={"team-a/channel-a"},
            now=now,
        )

    assert calls == ["PATCH", "POST", "PATCH"]
    assert healthy == {"team-a/channel-a"}


def test_subscription_reconciler_recreates_subscription_after_callback_url_change(
    tmp_path,
) -> None:
    store = TeamsInboxStore(tmp_path / "runtime" / "teams-inbox-store.db")
    now = datetime.now(UTC)
    store.upsert_owned_subscription(
        TeamsOwnedSubscription(
            subscription_id="old-callback-subscription",
            owner_id=store.notification_owner_id(),
            target="team-a/channel-a",
            resource="/teams/team-a/channels/channel-a/messages",
            directory_path="root",
            tenant_id="tenant-a",
            expires_at=now + timedelta(hours=48),
            notification_url="https://old.example.test/msgraph/teams/notifications",
            lifecycle_notification_url="https://old.example.test/msgraph/teams/lifecycle",
            client_state_sha256="0" * 64,
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
    )
    reconciler = TeamsSubscriptionReconciler(store=store, worker_config=_worker_config())
    assert reconciler.healthy_targets(
        directory_path="root",
        tenant_id="tenant-a",
        now=now,
    ) == set()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "DELETE":
            return httpx.Response(204, request=request)
        payload = json.loads(request.content)
        assert payload["notificationUrl"] == _NOTIFICATION_URL
        assert payload["lifecycleNotificationUrl"] == _LIFECYCLE_NOTIFICATION_URL
        return httpx.Response(
            201,
            request=request,
            json={
                "id": "new-callback-subscription",
                "expirationDateTime": (now + timedelta(hours=48)).isoformat(),
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        healthy = reconciler.reconcile_directory(
            client,
            service=_FakeGraphService(),
            provider_config=_provider_config(tmp_path),
            directory_path="root",
            targets={"team-a/channel-a"},
            now=now,
        )

    assert healthy == {"team-a/channel-a"}
    assert [request.method for request in requests] == ["DELETE", "POST"]
    previous = store.get_owned_subscription("old-callback-subscription")
    replacement = store.get_owned_subscription("new-callback-subscription")
    assert previous is not None and previous.health == "deleted"
    assert replacement is not None
    assert replacement.notification_url == _NOTIFICATION_URL
    assert replacement.lifecycle_notification_url == _LIFECYCLE_NOTIFICATION_URL


def test_failed_owned_subscription_cleanup_is_attempted_once_then_abandoned(tmp_path) -> None:
    store = TeamsInboxStore(tmp_path / "runtime" / "teams-inbox-store.db")
    reconciler = TeamsSubscriptionReconciler(store=store, worker_config=_worker_config())
    now = datetime.now(UTC)
    store.upsert_owned_subscription(
        TeamsOwnedSubscription(
            subscription_id="owned-subscription",
            owner_id=store.notification_owner_id(),
            target="chat:chat-a",
            resource="/chats/chat-a/messages",
            directory_path="root",
            tenant_id="tenant-a",
            expires_at=now + timedelta(hours=48),
            client_state_sha256="0" * 64,
            created_at=now,
            updated_at=now,
        )
    )
    delete_calls = 0

    def unavailable(request: httpx.Request) -> httpx.Response:
        nonlocal delete_calls
        delete_calls += 1
        return httpx.Response(503, request=request)

    with httpx.Client(transport=httpx.MockTransport(unavailable)) as client:
        reconciler.cleanup_directory(
            client,
            service=_FakeGraphService(),
            directory_path="root",
            now=now,
        )
        reconciler.cleanup_directory(
            client,
            service=_FakeGraphService(),
            directory_path="root",
            now=now,
        )

    subscription = store.get_owned_subscription("owned-subscription")
    assert subscription is not None
    assert subscription.health == "abandoned"
    assert subscription.cleanup_attempted is True
    assert delete_calls == 1


def test_lifecycle_event_marks_subscription_unhealthy_for_polling_fallback(tmp_path) -> None:
    store = TeamsInboxStore(tmp_path / "runtime" / "teams-inbox-store.db")
    reconciler = TeamsSubscriptionReconciler(store=store, worker_config=_worker_config())
    now = datetime.now(UTC)
    store.upsert_owned_subscription(
        TeamsOwnedSubscription(
            subscription_id="owned-subscription",
            owner_id=store.notification_owner_id(),
            target="chat:chat-a",
            resource="/chats/chat-a/messages",
            directory_path="root",
            tenant_id="tenant-a",
            expires_at=now + timedelta(hours=48),
            client_state_sha256="0" * 64,
            created_at=now,
            updated_at=now,
        )
    )
    entry = TeamsNotificationQueueEntry(
        idempotency_key="lifecycle-1",
        notification_kind="lifecycle",
        subscription_id="owned-subscription",
        tenant_id="tenant-a",
        resource="/chats/chat-a/messages",
        lifecycle_event="missed",
        received_at=now,
        next_attempt_at=now,
    )

    reconciler.apply_lifecycle_event(entry, now=now)

    subscription = store.get_owned_subscription("owned-subscription")
    assert subscription is not None
    assert subscription.health == "missed"
    assert reconciler.healthy_targets(directory_path="root", tenant_id="tenant-a", now=now) == set()
