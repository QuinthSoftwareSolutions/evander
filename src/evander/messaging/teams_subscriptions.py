from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx

from evander.config import GatewayConfig, TeamsIngestWorkerGlobalConfig
from evander.messaging.teams_inbox_store import (
    TeamsInboxStore,
    TeamsNotificationQueueEntry,
    TeamsOwnedSubscription,
)
from evander.services import GraphTeamsService, parse_teams_target

logger = logging.getLogger(__name__)


def notification_resource_for_target(target: str) -> str:
    parsed = parse_teams_target(target)
    if parsed.kind == "chat":
        return f"/chats/{parsed.chat_id}/messages"
    if parsed.kind == "channel":
        return f"/teams/{parsed.team_id}/channels/{parsed.channel_id}/messages"
    raise ValueError("Teams user targets cannot be subscribed before chat discovery")


def tenant_id_from_graph_token(service: GraphTeamsService, provider_config: GatewayConfig) -> str:
    token = service._resolve_token()
    parts = token.split(".")
    if len(parts) >= 2:
        try:
            encoded = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            tenant_id = str(payload.get("tid") or "").strip()
            if tenant_id:
                return tenant_id
    fallback = str(provider_config.teams_delegated_tenant_id or "").strip()
    if fallback and fallback not in {"organizations", "common", "consumers"}:
        return fallback
    raise RuntimeError("Teams Graph access token does not identify a concrete tenant")


class TeamsSubscriptionReconciler:
    def __init__(
        self,
        *,
        store: TeamsInboxStore,
        worker_config: TeamsIngestWorkerGlobalConfig,
    ) -> None:
        self.store = store
        self.worker_config = worker_config
        self.owner_id = store.notification_owner_id()

    def reconcile_directory(
        self,
        client: httpx.Client,
        *,
        service: GraphTeamsService,
        provider_config: GatewayConfig,
        directory_path: str,
        targets: set[str],
        now: datetime,
    ) -> set[str]:
        tenant_id = tenant_id_from_graph_token(service, provider_config)
        desired = {target for target in targets if parse_teams_target(target).kind != "user"}
        for target in sorted(desired):
            reconciliation_state = self.store.get_subscription_reconciliation_state(
                directory_path=directory_path,
                target=target,
            )
            if (
                reconciliation_state is not None
                and _utc(reconciliation_state.next_attempt_at) > now
            ):
                continue
            succeeded, error = self._ensure_subscription(
                client,
                service=service,
                directory_path=directory_path,
                tenant_id=tenant_id,
                target=target,
                now=now,
            )
            if succeeded:
                self.store.clear_subscription_reconciliation_state(
                    directory_path=directory_path,
                    target=target,
                )
            else:
                self.store.record_subscription_reconciliation_failure(
                    directory_path=directory_path,
                    target=target,
                    failed_at=now,
                    error=error or "subscription reconciliation failed",
                    initial_backoff_seconds=self.worker_config.target_backoff_initial_seconds,
                    max_backoff_seconds=self.worker_config.target_backoff_max_seconds,
                )
        for subscription in self.store.list_owned_subscriptions(directory_path=directory_path):
            if subscription.owner_id != self.owner_id or subscription.tenant_id != tenant_id:
                continue
            if subscription.target not in desired:
                self._delete_owned(client, service=service, subscription=subscription, now=now)
        healthy = self.healthy_targets(
            directory_path=directory_path,
            tenant_id=tenant_id,
            now=now,
        )
        current_by_target = {
            item.target: item
            for item in self.store.list_owned_subscriptions(directory_path=directory_path)
            if item.owner_id == self.owner_id and item.tenant_id == tenant_id
        }
        reconciliation_by_target = {
            target: self.store.get_subscription_reconciliation_state(
                directory_path=directory_path,
                target=target,
            )
            for target in desired
        }
        self.store.replace_notification_target_health(
            directory_path=directory_path,
            desired_targets=desired,
            healthy_targets=healthy,
            errors_by_target={
                target: (
                    current_by_target[target].last_error
                    if target in current_by_target
                    else (
                        reconciliation_by_target[target].last_error
                        if reconciliation_by_target[target] is not None
                        else "subscription missing"
                    )
                )
                for target in desired
            },
            updated_at=now,
        )
        return healthy

    def cleanup_directory(
        self,
        client: httpx.Client,
        *,
        service: GraphTeamsService,
        directory_path: str,
        now: datetime,
    ) -> None:
        for subscription in self.store.list_owned_subscriptions(directory_path=directory_path):
            if subscription.owner_id == self.owner_id:
                self._delete_owned(client, service=service, subscription=subscription, now=now)

    def healthy_targets(
        self,
        *,
        directory_path: str,
        tenant_id: str,
        now: datetime,
    ) -> set[str]:
        return {
            item.target
            for item in self.store.list_owned_subscriptions(directory_path=directory_path)
            if item.owner_id == self.owner_id
            and item.tenant_id == tenant_id
            and item.health == "active"
            and _utc(item.expires_at) > now
        }

    def apply_lifecycle_event(
        self,
        entry: TeamsNotificationQueueEntry,
        *,
        now: datetime,
    ) -> None:
        subscription = self.store.get_owned_subscription(entry.subscription_id)
        if subscription is None or subscription.owner_id != self.owner_id:
            return
        health = {
            "missed": "missed",
            "reauthorizationRequired": "reauthorization_required",
            "subscriptionRemoved": "removed",
        }.get(entry.lifecycle_event or "", "degraded")
        self.store.upsert_owned_subscription(
            subscription.model_copy(
                update={
                    "health": health,
                    "updated_at": now,
                    "last_error": f"lifecycle event: {entry.lifecycle_event}",
                }
            )
        )
        self.store.clear_subscription_reconciliation_state(
            directory_path=subscription.directory_path,
            target=subscription.target,
        )

    def _ensure_subscription(
        self,
        client: httpx.Client,
        *,
        service: GraphTeamsService,
        directory_path: str,
        tenant_id: str,
        target: str,
        now: datetime,
    ) -> tuple[bool, str | None]:
        existing = self.store.find_owned_subscription(
            directory_path=directory_path,
            tenant_id=tenant_id,
            target=target,
        )
        if existing is not None and existing.owner_id != self.owner_id:
            return False, "subscription is owned by another deployment"
        if existing is not None and existing.health == "reauthorization_required":
            try:
                service._request_with_retries(
                    client,
                    "POST",
                    f"{service.api_base_url}/subscriptions/"
                    f"{quote(existing.subscription_id, safe='')}/reauthorize",
                )
            except Exception as exc:
                self._mark_degraded(existing, now=now, error=exc)
                return False, _safe_error(exc)
        renewal_deadline = now + timedelta(
            hours=self.worker_config.change_notification_renewal_margin_hours
        )
        if (
            existing is not None
            and existing.health == "active"
            and _utc(existing.expires_at) > renewal_deadline
        ):
            return True, None
        if existing is not None and existing.health != "removed":
            if self._renew(client, service=service, subscription=existing, now=now):
                return True, None
            if _utc(existing.expires_at) > now:
                refreshed = self.store.get_owned_subscription(existing.subscription_id)
                return False, refreshed.last_error if refreshed is not None else None
        return self._create(
            client,
            service=service,
            directory_path=directory_path,
            tenant_id=tenant_id,
            target=target,
            existing=existing,
            now=now,
        )

    def _create(
        self,
        client: httpx.Client,
        *,
        service: GraphTeamsService,
        directory_path: str,
        tenant_id: str,
        target: str,
        existing: TeamsOwnedSubscription | None,
        now: datetime,
    ) -> tuple[bool, str | None]:
        base_url = str(self.worker_config.change_notification_public_base_url or "").rstrip("/")
        if not base_url:
            return False, "change notification public base URL is not configured"
        client_state = secrets.token_urlsafe(48)[:96]
        requested_expiry = now + timedelta(
            hours=self.worker_config.change_notification_subscription_lifetime_hours
        )
        payload = {
            "changeType": "created",
            "notificationUrl": f"{base_url}/msgraph/teams/notifications",
            "lifecycleNotificationUrl": f"{base_url}/msgraph/teams/lifecycle",
            "resource": notification_resource_for_target(target),
            "expirationDateTime": _iso(requested_expiry),
            "clientState": client_state,
            "includeResourceData": False,
        }
        try:
            response = service._request_with_retries(
                client,
                "POST",
                f"{service.api_base_url}/subscriptions",
                json=payload,
            )
            response_payload = response.json()
            subscription_id = str(response_payload.get("id") or "").strip()
            if not subscription_id:
                raise RuntimeError("Graph subscription response omitted id")
            expires_at = _parse_datetime(
                response_payload.get("expirationDateTime"),
                fallback=requested_expiry,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409 and existing is not None:
                renewed = self._renew(client, service=service, subscription=existing, now=now)
                refreshed = self.store.get_owned_subscription(existing.subscription_id)
                return renewed, refreshed.last_error if refreshed is not None else None
            if existing is not None:
                self._mark_degraded(existing, now=now, error=exc)
            else:
                logger.warning(
                    "Failed to create Teams Graph subscription directory=%s target=%s error=%s",
                    directory_path,
                    target,
                    _safe_error(exc),
                )
            return False, _safe_error(exc)
        except Exception as exc:
            if existing is not None:
                self._mark_degraded(existing, now=now, error=exc)
            else:
                logger.warning(
                    "Failed to create Teams Graph subscription directory=%s target=%s error=%s",
                    directory_path,
                    target,
                    _safe_error(exc),
                )
            return False, _safe_error(exc)
        self.store.upsert_owned_subscription(
            TeamsOwnedSubscription(
                subscription_id=subscription_id,
                owner_id=self.owner_id,
                target=target,
                resource=payload["resource"],
                directory_path=directory_path,
                tenant_id=tenant_id,
                expires_at=expires_at,
                health="active",
                client_state_sha256=hashlib.sha256(client_state.encode("utf-8")).hexdigest(),
                created_at=now,
                updated_at=now,
                last_reconciled_at=now,
            )
        )
        if existing is not None and existing.subscription_id != subscription_id:
            self.store.upsert_owned_subscription(
                existing.model_copy(
                    update={"health": "expired", "updated_at": now, "last_error": None}
                )
            )
        return True, None

    def _renew(
        self,
        client: httpx.Client,
        *,
        service: GraphTeamsService,
        subscription: TeamsOwnedSubscription,
        now: datetime,
    ) -> bool:
        requested_expiry = now + timedelta(
            hours=self.worker_config.change_notification_subscription_lifetime_hours
        )
        try:
            response = service._request_with_retries(
                client,
                "PATCH",
                f"{service.api_base_url}/subscriptions/"
                f"{quote(subscription.subscription_id, safe='')}",
                json={"expirationDateTime": _iso(requested_expiry)},
            )
            payload = response.json()
            expires_at = _parse_datetime(
                payload.get("expirationDateTime"), fallback=requested_expiry
            )
        except Exception as exc:
            self._mark_degraded(subscription, now=now, error=exc)
            return False
        self.store.upsert_owned_subscription(
            subscription.model_copy(
                update={
                    "expires_at": expires_at,
                    "health": "active",
                    "updated_at": now,
                    "last_reconciled_at": now,
                    "last_error": None,
                }
            )
        )
        return True

    def _delete_owned(
        self,
        client: httpx.Client,
        *,
        service: GraphTeamsService,
        subscription: TeamsOwnedSubscription,
        now: datetime,
    ) -> None:
        if subscription.cleanup_attempted:
            return
        try:
            service._request_with_retries(
                client,
                "DELETE",
                f"{service.api_base_url}/subscriptions/"
                f"{quote(subscription.subscription_id, safe='')}",
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                self.store.upsert_owned_subscription(
                    subscription.model_copy(
                        update={
                            "health": "abandoned",
                            "cleanup_attempted": True,
                            "updated_at": now,
                            "last_error": _safe_error(exc),
                        }
                    )
                )
                return
        except Exception as exc:
            self.store.upsert_owned_subscription(
                subscription.model_copy(
                    update={
                        "health": "abandoned",
                        "cleanup_attempted": True,
                        "updated_at": now,
                        "last_error": _safe_error(exc),
                    }
                )
            )
            return
        self.store.upsert_owned_subscription(
            subscription.model_copy(
                update={
                    "health": "deleted",
                    "cleanup_attempted": True,
                    "updated_at": now,
                    "last_error": None,
                }
            )
        )

    def _mark_degraded(
        self,
        subscription: TeamsOwnedSubscription,
        *,
        now: datetime,
        error: Exception,
    ) -> None:
        self.store.upsert_owned_subscription(
            subscription.model_copy(
                update={
                    "health": "degraded",
                    "updated_at": now,
                    "last_reconciled_at": now,
                    "last_error": _safe_error(error),
                }
            )
        )


def _safe_error(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:500] or type(exc).__name__


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_datetime(value: object, *, fallback: datetime) -> datetime:
    text = str(value or "").strip()
    if not text:
        return fallback
    try:
        return _utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return fallback
