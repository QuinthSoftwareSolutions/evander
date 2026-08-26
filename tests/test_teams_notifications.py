from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from starlette.testclient import TestClient

from evander.admin.payloads import WorkerGlobalConfigPayload
from evander.admin.web.workers_config import _worker_detail
from evander.config import GatewayConfig
from evander.messaging.teams_inbox_store import TeamsInboxStore, TeamsOwnedSubscription
from evander.messaging.teams_notifications import create_app
from evander.models import WorkerSummary


def _configured_service(tmp_path):
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
    app = create_app(config, project_dir=tmp_path, store=store)
    return TestClient(app), store, client_state


def test_teams_notifications_returns_decoded_challenge_verbatim(tmp_path) -> None:
    client, _store, _client_state = _configured_service(tmp_path)

    response = client.post("/msgraph/teams/notifications?validationToken=opaque%2Btoken%2Fvalue")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "opaque+token/value"


def test_teams_notifications_validates_batch_and_durably_deduplicates(tmp_path) -> None:
    client, store, client_state = _configured_service(tmp_path)
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
    leased = store.lease_notifications(now=datetime.now(UTC))
    assert len(leased) == 1
    assert leased[0].resource.endswith("messages('message-1')")


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
