from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from starlette.testclient import TestClient

from evander.admin.payloads import WorkerGlobalConfigPayload
from evander.admin.web.workers_config import _worker_detail
from evander.config import GatewayConfig
from evander.messaging.teams_inbox_store import TeamsInboxStore, TeamsOwnedSubscription
from evander.messaging.teams_notifications import create_app
from evander.models import WorkerSummary


def _configured_service(tmp_path, *, ingest_wake_notifier=None):
    runtime_dir = tmp_path / "runtime"
    config_dir = tmp_path / "config" / "teams-ingest"
    config_dir.mkdir(parents=True)
    config_dir.joinpath("config.json").write_text(
        json.dumps(
            {
                "runtime_dir": str(runtime_dir),
                "change_notifications_enabled": True,
                "change_notification_public_base_url": "https://notifications.example.test",
            }
        ),
        encoding="utf-8",
    )
    config = GatewayConfig.model_validate({"runtime_dir": runtime_dir})
    store = TeamsInboxStore(runtime_dir / "teams-inbox-store.db")
    client_state = "constant-client-state"
    now = datetime.now(UTC)
    store.upsert_owned_subscription(
        TeamsOwnedSubscription(
            subscription_id="subscription-1",
            owner_id=store.notification_owner_id(),
            target="team-a/channel-a",
            resource="/teams/team-a/channels/channel-a/messages",
            directory_path="root",
            tenant_id="tenant-a",
            expires_at=now + timedelta(hours=48),
            client_state_sha256=hashlib.sha256(client_state.encode()).hexdigest(),
            created_at=now,
            updated_at=now,
        )
    )
    app = create_app(
        config,
        project_dir=tmp_path,
        store=store,
        ingest_wake_notifier=ingest_wake_notifier,
    )
    return TestClient(app), store, client_state


def test_teams_notifications_returns_decoded_challenge_verbatim(tmp_path) -> None:
    client, _store, _client_state = _configured_service(tmp_path)

    response = client.post("/msgraph/teams/notifications?validationToken=opaque%2Btoken%2Fvalue")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "opaque+token/value"


def test_teams_notifications_validates_batch_and_durably_deduplicates(tmp_path) -> None:
    wake_calls: list[str] = []
    client, store, client_state = _configured_service(
        tmp_path,
        ingest_wake_notifier=lambda: wake_calls.append("wake") or True,
    )
    valid = {
        "subscriptionId": "subscription-1",
        "tenantId": "tenant-a",
        "clientState": client_state,
        "changeType": "created",
        "resource": "teams('team-a')/channels('channel-a')/messages('message-1')",
        "resourceData": {"id": "message-1"},
    }
    invalid = {**valid, "clientState": "wrong"}

    response = client.post(
        "/msgraph/teams/notifications",
        json={"value": [valid, invalid]},
    )
    duplicate = client.post("/msgraph/teams/notifications", json={"value": [valid]})

    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "accepted": 1}
    assert duplicate.status_code == 202
    assert duplicate.json()["accepted"] == 0
    assert wake_calls == ["wake"]
    leased = store.lease_notifications(now=datetime.now(UTC))
    assert len(leased) == 1
    assert leased[0].resource.endswith("messages('message-1')")


def test_teams_notifications_wakes_ingest_after_durable_enqueue(tmp_path) -> None:
    observed_queue_depths: list[int] = []
    store_holder: dict[str, TeamsInboxStore] = {}

    def observe_wake() -> bool:
        status = store_holder["store"].notification_status(
            enabled=True,
            renewal_margin=timedelta(hours=24),
        )
        observed_queue_depths.append(status["queue_depth"])
        return True

    client, store, client_state = _configured_service(
        tmp_path,
        ingest_wake_notifier=observe_wake,
    )
    store_holder["store"] = store

    response = client.post(
        "/msgraph/teams/notifications",
        json={
            "value": [
                {
                    "subscriptionId": "subscription-1",
                    "tenantId": "tenant-a",
                    "clientState": client_state,
                    "changeType": "created",
                    "resource": "chats('chat-a')/messages('message-a')",
                }
            ]
        },
    )

    assert response.status_code == 202
    assert response.json()["accepted"] == 1
    assert observed_queue_depths == [1]


def test_teams_notifications_background_processor_is_independent_of_ingest_poll(
    tmp_path,
) -> None:
    processed = threading.Event()
    runtime_dir = tmp_path / "runtime"
    config_dir = tmp_path / "config" / "teams-ingest"
    config_dir.mkdir(parents=True)
    config_dir.joinpath("config.json").write_text(
        json.dumps(
            {
                "runtime_dir": str(runtime_dir),
                "change_notifications_enabled": True,
                "change_notification_public_base_url": "https://notifications.example.test",
            }
        ),
        encoding="utf-8",
    )
    config = GatewayConfig.model_validate({"runtime_dir": runtime_dir})
    store = TeamsInboxStore(runtime_dir / "teams-inbox-store.db")
    client_state = "constant-client-state"
    now = datetime.now(UTC)
    store.upsert_owned_subscription(
        TeamsOwnedSubscription(
            subscription_id="subscription-1",
            owner_id=store.notification_owner_id(),
            target="team-a/channel-a",
            resource="/teams/team-a/channels/channel-a/messages",
            directory_path="root",
            tenant_id="tenant-a",
            expires_at=now + timedelta(hours=48),
            client_state_sha256=hashlib.sha256(client_state.encode()).hexdigest(),
            created_at=now,
            updated_at=now,
        )
    )

    def process_queue() -> int:
        entries = store.lease_notifications(now=datetime.now(UTC))
        for entry in entries:
            store.complete_notification(entry, completed_at=datetime.now(UTC))
        if entries:
            processed.set()
        return len(entries)

    app = create_app(
        config,
        project_dir=tmp_path,
        store=store,
        ingest_wake_notifier=lambda: False,
        notification_processor=process_queue,
    )
    with TestClient(app) as client:
        response = client.post(
            "/msgraph/teams/notifications",
            json={
                "value": [
                    {
                        "subscriptionId": "subscription-1",
                        "tenantId": "tenant-a",
                        "clientState": client_state,
                        "changeType": "created",
                        "resource": "teams('team-a')/channels('channel-a')/messages('message-1')",
                    }
                ]
            },
        )
        assert response.status_code == 202
        assert processed.wait(1)
        health = client.get("/healthz").json()
        assert health["processor"]["enabled"] is True
        assert health["processor"]["running"] is True
        assert health["notifications"]["queue_depth"] == 0


def test_teams_notifications_acknowledges_when_ingest_wake_fails(
    tmp_path,
    caplog,
) -> None:
    def fail_wake() -> bool:
        raise OSError("wake socket unavailable")

    client, store, client_state = _configured_service(
        tmp_path,
        ingest_wake_notifier=fail_wake,
    )

    with caplog.at_level("WARNING"):
        response = client.post(
            "/msgraph/teams/notifications",
            json={
                "value": [
                    {
                        "subscriptionId": "subscription-1",
                        "tenantId": "tenant-a",
                        "clientState": client_state,
                        "changeType": "created",
                        "resource": "chats('chat-a')/messages('message-a')",
                    }
                ]
            },
        )

    assert response.status_code == 202
    assert response.json()["accepted"] == 1
    assert len(store.lease_notifications(now=datetime.now(UTC))) == 1
    assert "periodic polling will process the queue" in caplog.text


def test_teams_notifications_resolves_worker_wake_socket_from_project_dir(
    tmp_path,
    monkeypatch,
) -> None:
    notified_paths = []

    class RecordingWakeNotifier:
        def __init__(self, path) -> None:
            notified_paths.append(path)

        def notify(self) -> bool:
            return True

    monkeypatch.setattr(
        "evander.messaging.teams_notifications.HostWakeNotifier",
        RecordingWakeNotifier,
    )
    client, _store, client_state = _configured_service(tmp_path)
    config_path = tmp_path / "config" / "teams-ingest" / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "runtime_dir": "runtime",
                "lock_path": "custom/teams-ingest.lock",
                "change_notifications_enabled": True,
                "change_notification_public_base_url": "https://notifications.example.test",
            }
        ),
        encoding="utf-8",
    )

    response = client.post(
        "/msgraph/teams/notifications",
        json={
            "value": [
                {
                    "subscriptionId": "subscription-1",
                    "tenantId": "tenant-a",
                    "clientState": client_state,
                    "changeType": "created",
                    "resource": "chats('chat-a')/messages('message-a')",
                }
            ]
        },
    )

    assert response.status_code == 202
    assert notified_paths == [tmp_path / "custom" / "teams-ingest.wake.sock"]


def test_teams_notifications_ignore_unknown_subscription_and_wrong_tenant(tmp_path) -> None:
    client, store, client_state = _configured_service(tmp_path)
    base = {
        "tenantId": "tenant-a",
        "clientState": client_state,
        "changeType": "created",
        "resource": "teams('team-a')/channels('channel-a')/messages('message-1')",
    }

    response = client.post(
        "/msgraph/teams/notifications",
        json={
            "value": [
                {**base, "subscriptionId": "unknown-subscription"},
                {
                    **base,
                    "subscriptionId": "subscription-1",
                    "tenantId": "wrong-tenant",
                },
            ]
        },
    )

    assert response.status_code == 202
    assert response.json()["accepted"] == 0
    assert store.lease_notifications(now=datetime.now(UTC)) == []


def test_teams_lifecycle_notifications_are_durably_queued(tmp_path) -> None:
    client, store, client_state = _configured_service(tmp_path)

    response = client.post(
        "/msgraph/teams/lifecycle",
        json={
            "value": [
                {
                    "id": "lifecycle-delivery-1",
                    "subscriptionId": "subscription-1",
                    "tenantId": "tenant-a",
                    "clientState": client_state,
                    "lifecycleEvent": "reauthorizationRequired",
                }
            ]
        },
    )

    assert response.status_code == 202
    assert response.json()["accepted"] == 1
    leased = store.lease_notifications(now=datetime.now(UTC))
    assert leased[0].notification_kind == "lifecycle"
    assert leased[0].lifecycle_event == "reauthorizationRequired"


def test_disabled_notifications_acknowledge_without_queuing_new_deliveries(tmp_path) -> None:
    client, store, client_state = _configured_service(tmp_path)
    (tmp_path / "config" / "teams-ingest" / "config.json").write_text(
        json.dumps(
            {
                "runtime_dir": str(tmp_path / "runtime"),
                "change_notifications_enabled": False,
                "change_notification_public_base_url": ("https://notifications.example.test"),
            }
        ),
        encoding="utf-8",
    )

    response = client.post(
        "/msgraph/teams/notifications",
        json={
            "value": [
                {
                    "subscriptionId": "subscription-1",
                    "tenantId": "tenant-a",
                    "clientState": client_state,
                    "changeType": "created",
                    "resource": "chats('chat-a')/messages('message-a')",
                }
            ]
        },
    )

    assert response.status_code == 202
    assert response.json()["accepted"] == 0
    assert store.lease_notifications(now=datetime.now(UTC)) == []


def test_teams_notifications_rejects_malformed_and_oversized_requests(tmp_path) -> None:
    runtime_dir = tmp_path / "runtime"
    config_dir = tmp_path / "config" / "teams-ingest"
    config_dir.mkdir(parents=True)
    config_dir.joinpath("config.json").write_text("{}", encoding="utf-8")
    config = GatewayConfig.model_validate({"runtime_dir": runtime_dir})
    app = create_app(config, project_dir=tmp_path, max_request_bytes=32)
    client = TestClient(app)

    assert client.post("/msgraph/teams/notifications", content=b"not-json").status_code == 400
    assert client.post("/msgraph/teams/notifications", json={"wrong": []}).status_code == 400
    oversized = client.post(
        "/msgraph/teams/notifications",
        content=b"x" * 33,
        headers={"content-type": "application/json"},
    )
    assert oversized.status_code == 413


def test_teams_notifications_rejects_oversized_batch_before_subscription_lookup(
    tmp_path,
    monkeypatch,
) -> None:
    client, store, _client_state = _configured_service(tmp_path)
    lookup_calls = 0

    def unexpected_lookup(_subscription_ids):
        nonlocal lookup_calls
        lookup_calls += 1
        raise AssertionError("oversized batch must be rejected before SQLite lookup")

    monkeypatch.setattr(store, "get_owned_subscriptions", unexpected_lookup)
    response = client.post(
        "/msgraph/teams/notifications",
        json={"value": [{"subscriptionId": f"unknown-{index}"} for index in range(101)]},
    )

    assert response.status_code == 413
    assert "at most 100 items" in response.json()["error"]
    assert lookup_calls == 0


def test_teams_notifications_bulk_loads_subscriptions_once_for_invalid_batch(
    tmp_path,
    monkeypatch,
) -> None:
    client, store, _client_state = _configured_service(tmp_path)
    original_lookup = store.get_owned_subscriptions
    lookup_calls = 0

    def recording_lookup(subscription_ids):
        nonlocal lookup_calls
        lookup_calls += 1
        return original_lookup(subscription_ids)

    monkeypatch.setattr(store, "get_owned_subscriptions", recording_lookup)
    response = client.post(
        "/msgraph/teams/notifications",
        json={
            "value": [
                {
                    "subscriptionId": f"unknown-{index}",
                    "tenantId": "tenant-a",
                    "clientState": "invalid",
                    "changeType": "created",
                    "resource": "chats('chat-a')/messages('message-a')",
                }
                for index in range(100)
            ]
        },
    )

    assert response.status_code == 202
    assert response.json()["accepted"] == 0
    assert lookup_calls == 1


def test_teams_notifications_uses_worker_configured_runtime_database(tmp_path) -> None:
    bootstrap_runtime = tmp_path / "bootstrap-runtime"
    worker_runtime = tmp_path / "worker-runtime"
    config_dir = tmp_path / "config" / "teams-ingest"
    config_dir.mkdir(parents=True)
    config_dir.joinpath("config.json").write_text(
        json.dumps(
            {
                "runtime_dir": "worker-runtime",
                "change_notifications_enabled": True,
                "change_notification_public_base_url": "https://notifications.example.test",
            }
        ),
        encoding="utf-8",
    )
    store = TeamsInboxStore(worker_runtime / "teams-inbox-store.db")
    client_state = "worker-runtime-client-state"
    now = datetime.now(UTC)
    store.upsert_owned_subscription(
        TeamsOwnedSubscription(
            subscription_id="worker-runtime-subscription",
            owner_id=store.notification_owner_id(),
            target="chat:chat-a",
            resource="/chats/chat-a/messages",
            directory_path="root",
            tenant_id="tenant-a",
            expires_at=now + timedelta(hours=48),
            client_state_sha256=hashlib.sha256(client_state.encode()).hexdigest(),
            created_at=now,
            updated_at=now,
        )
    )
    app = create_app(
        GatewayConfig.model_validate({"runtime_dir": bootstrap_runtime}),
        project_dir=tmp_path,
    )

    response = TestClient(app).post(
        "/msgraph/teams/notifications",
        json={
            "value": [
                {
                    "subscriptionId": "worker-runtime-subscription",
                    "tenantId": "tenant-a",
                    "clientState": client_state,
                    "changeType": "created",
                    "resource": "chats('chat-a')/messages('message-a')",
                }
            ]
        },
    )

    assert response.status_code == 202
    assert response.json()["accepted"] == 1
    assert len(store.lease_notifications(now=datetime.now(UTC))) == 1
    assert not (bootstrap_runtime / "teams-inbox-store.db").exists()


def test_teams_notifications_health_is_secret_free(tmp_path) -> None:
    client, _store, client_state = _configured_service(tmp_path)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["notifications"]["active_subscriptions"] == 1
    assert client_state not in response.text
    assert hashlib.sha256(client_state.encode()).hexdigest() not in response.text


def test_teams_notifications_health_ignores_terminal_and_foreign_subscriptions(tmp_path) -> None:
    client, store, _client_state = _configured_service(tmp_path)
    now = datetime.now(UTC)
    for subscription_id, owner_id, health in (
        ("expired-owned", store.notification_owner_id(), "expired"),
        ("foreign-degraded", "another-deployment", "degraded"),
    ):
        store.upsert_owned_subscription(
            TeamsOwnedSubscription(
                subscription_id=subscription_id,
                owner_id=owner_id,
                target="team-a/channel-a",
                resource="/teams/team-a/channels/channel-a/messages",
                directory_path="root",
                tenant_id="tenant-a",
                expires_at=now - timedelta(minutes=1),
                health=health,
                client_state_sha256="0" * 64,
                created_at=now - timedelta(days=2),
                updated_at=now,
            )
        )

    status = client.get("/healthz").json()["notifications"]

    assert status["active_subscriptions"] == 1
    assert status["targets_on_fast_fallback"] == []
    assert status["degraded_reasons"] == []


def test_expired_notification_lease_is_recoverable(tmp_path) -> None:
    client, store, client_state = _configured_service(tmp_path)
    client.post(
        "/msgraph/teams/notifications",
        json={
            "value": [
                {
                    "subscriptionId": "subscription-1",
                    "tenantId": "tenant-a",
                    "clientState": client_state,
                    "changeType": "created",
                    "resource": "chats('chat-a')/messages('message-a')",
                }
            ]
        },
    )
    now = datetime.now(UTC)

    first = store.lease_notifications(now=now, lease_seconds=1)
    assert len(first) == 1
    assert store.lease_notifications(now=now + timedelta(milliseconds=500)) == []
    recovered = store.lease_notifications(now=now + timedelta(seconds=1))

    assert len(recovered) == 1
    assert recovered[0].attempt_count == 2


def test_admin_worker_status_uses_the_teams_worker_runtime_directory(tmp_path) -> None:
    worker_config = {
        "runtime_dir": "alternate-runtime",
        "change_notifications_enabled": False,
    }
    service = SimpleNamespace(
        project_dir=tmp_path,
        runtime_dir=tmp_path / "admin-runtime",
        operations_admin=SimpleNamespace(
            list_background_workers=lambda: [
                WorkerSummary(
                    worker_id="teams-ingest",
                    name="Teams ingest",
                    unit="evander-teams-ingest.service",
                    active_state="active",
                    running=True,
                )
            ]
        ),
        workflow_config_admin=SimpleNamespace(
            read_worker_global_config=lambda _worker_id: WorkerGlobalConfigPayload(
                worker_id="teams-ingest",
                path=str(tmp_path / "config" / "teams-ingest" / "config.json"),
                exists=True,
                config=worker_config,
                revision="revision-1",
            )
        ),
    )

    detail = _worker_detail(service, "teams-ingest")

    assert detail["runtime_status"]["enabled"] is False
    assert (tmp_path / "alternate-runtime" / "teams-inbox-store.db").exists()
    assert not (tmp_path / "admin-runtime" / "teams-inbox-store.db").exists()
