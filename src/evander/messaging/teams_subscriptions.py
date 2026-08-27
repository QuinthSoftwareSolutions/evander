from __future__ import annotations

import hashlib
import json
import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import UUID

import httpx

from evander.config import GatewayConfig, TeamsIngestWorkerGlobalConfig
from evander.messaging.teams_inbox_store import (
    TeamsInboxStore,
    TeamsNotificationQueueEntry,
    TeamsOwnedSubscription,
)
from evander.services import GraphTeamsService, parse_teams_target

logger = logging.getLogger(__name__)

_NON_CONCRETE_TENANT_IDS = {"organizations", "common", "consumers"}


def teams_subscription_provider_identity_key(config: GatewayConfig) -> str:
    """Return a stable, secret-free identity for Graph subscription ownership."""

    payload: dict[str, str] = {
        "backend": config.teams_backend,
        "graph_api_base_url": config.teams_graph_api_base_url.rstrip("/").casefold(),
    }
    if config.teams_backend == "delegated_graph_api":
        payload.update(
            {
                "delegated_identity_id": str(config.teams_delegated_identity_id or "").strip(),
                "delegated_client_id": str(config.teams_delegated_client_id or "").strip(),
                "delegated_authority_base_url": (
                    config.teams_delegated_authority_base_url.rstrip("/").casefold()
                ),
                "delegated_credential_store_path": str(
                    config.teams_delegated_credential_store_path
                ),
            }
        )
    else:
        payload["graph_access_token_env"] = config.teams_graph_access_token_env.strip()
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _tenant_uuid(value: str) -> str | None:
    try:
        return str(UUID(value))
    except ValueError:
        return None


def notification_resource_for_target(target: str) -> str:
    parsed = parse_teams_target(target)
    if parsed.kind == "chat":
        return f"/chats/{parsed.chat_id}/messages"
    if parsed.kind == "channel":
        return f"/teams/{parsed.team_id}/channels/{parsed.channel_id}/messages"
    raise ValueError("Teams user targets cannot be subscribed before chat discovery")


def resolve_graph_tenant_id(
    _client: httpx.Client,
    *,
    service: GraphTeamsService,
    provider_config: GatewayConfig,
) -> str:
    configured = str(provider_config.teams_delegated_tenant_id or "").strip()
    tenant_resolver = getattr(service, "_resolve_tenant_id", None)
    credential_tenant = str(tenant_resolver() or "").strip() if callable(tenant_resolver) else ""
    if configured and configured.casefold() not in _NON_CONCRETE_TENANT_IDS:
        configured_uuid = _tenant_uuid(configured)
        credential_uuid = _tenant_uuid(credential_tenant) if credential_tenant else None
        if configured_uuid and credential_uuid and credential_uuid != configured_uuid:
            raise PermissionError(
                "Configured Teams tenant does not match the authenticated credential tenant"
            )
        return credential_tenant or configured

    if credential_tenant:
        return credential_tenant
    raise RuntimeError(
        "Teams delegated credential does not identify a concrete tenant; configure "
        "teams_delegated_tenant_id or repeat device-code login"
    )


class TeamsSubscriptionReconciler:
    def __init__(
        self,
        *,
        store: TeamsInboxStore,
        worker_config: TeamsIngestWorkerGlobalConfig,
        provider_identity_key_for_directory: Callable[[str], str | None] | None = None,
    ) -> None:
        self.store = store
        self.worker_config = worker_config
        self.owner_id = store.notification_owner_id()
        self.provider_identity_key_for_directory = provider_identity_key_for_directory

    def reconcile_directory(
        self,
        client: httpx.Client,
        *,
        service: GraphTeamsService,
        provider_config: GatewayConfig,
        directory_path: str,
        targets: set[str],
        now: datetime,
        tenant_id: str | None = None,
    ) -> set[str]:
        tenant_id = tenant_id or resolve_graph_tenant_id(
            client,
            service=service,
            provider_config=provider_config,
        )
        provider_identity_key = teams_subscription_provider_identity_key(provider_config)
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
                provider_identity_key=provider_identity_key,
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
        for subscription in self.store.list_owned_subscriptions():
            if (
                subscription.owner_id != self.owner_id
                or subscription.tenant_id != tenant_id
                or not self._matches_provider_identity(
                    subscription,
                    provider_identity_key=provider_identity_key,
                )
                or directory_path not in self._consumer_directories(subscription)
            ):
                continue
            if subscription.target not in desired:
                self._remove_directory_consumer(
                    client,
                    service=service,
                    subscription=subscription,
                    directory_path=directory_path,
                    now=now,
                )
        healthy = self.healthy_targets(
            directory_path=directory_path,
            provider_identity_key=provider_identity_key,
            tenant_id=tenant_id,
            now=now,
        )
        current_by_target = {
            item.target: item
            for item in self.store.list_owned_subscriptions()
            if item.owner_id == self.owner_id
            and item.tenant_id == tenant_id
            and self._matches_provider_identity(
                item,
                provider_identity_key=provider_identity_key,
            )
            and directory_path in self._consumer_directories(item)
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
        for subscription in self.store.list_owned_subscriptions():
            if (
                subscription.owner_id == self.owner_id
                and directory_path in self._consumer_directories(subscription)
            ):
                self._remove_directory_consumer(
                    client,
                    service=service,
                    subscription=subscription,
                    directory_path=directory_path,
                    now=now,
                )

    def cleanup_owned_subscriptions(
        self,
        client: httpx.Client,
        *,
        service: GraphTeamsService,
        subscriptions: list[TeamsOwnedSubscription],
        now: datetime,
    ) -> None:
        for subscription in subscriptions:
            if subscription.owner_id == self.owner_id:
                self._delete_owned(
                    client,
                    service=service,
                    subscription=subscription,
                    now=now,
                )

    def prune_inactive_consumers(
        self,
        *,
        active_directories: set[str],
        now: datetime,
    ) -> list[TeamsOwnedSubscription]:
        """Detach inactive directory consumers and return subscriptions with none left."""

        orphaned: list[TeamsOwnedSubscription] = []
        for subscription in self.store.list_owned_subscriptions():
            if subscription.owner_id != self.owner_id:
                continue
            consumers = self._consumer_directories(subscription)
            remaining = consumers & active_directories
            if remaining == consumers:
                continue
            if not remaining:
                orphaned.append(subscription)
                continue
            self.store.upsert_owned_subscription(
                subscription.model_copy(
                    update={
                        "directory_path": self._representative_directory(remaining),
                        "consumer_directory_paths": sorted(remaining),
                        "updated_at": now,
                    }
                )
            )
        return orphaned

    def healthy_targets(
        self,
        *,
        directory_path: str,
        provider_identity_key: str | None = None,
        tenant_id: str,
        now: datetime,
    ) -> set[str]:
        return {
            item.target
            for item in self.store.list_owned_subscriptions()
            if item.owner_id == self.owner_id
            and item.tenant_id == tenant_id
            and (
                provider_identity_key is None
                or self._matches_provider_identity(
                    item,
                    provider_identity_key=provider_identity_key,
                )
            )
            and directory_path in self._consumer_directories(item)
            and item.health == "active"
            and _utc(item.expires_at) > now
            and self._uses_current_callback_urls(item)
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
        for directory_path in self._consumer_directories(subscription):
            self.store.clear_subscription_reconciliation_state(
                directory_path=directory_path,
                target=subscription.target,
            )

    @staticmethod
    def _consumer_directories(subscription: TeamsOwnedSubscription) -> set[str]:
        return set(subscription.consumer_directory_paths or [subscription.directory_path])

    @staticmethod
    def _representative_directory(directory_paths: set[str]) -> str:
        if not directory_paths:
            raise ValueError("at least one subscription consumer directory is required")
        return min(directory_paths, key=lambda item: (len(item.split("/")), item))

    def _matches_provider_identity(
        self,
        subscription: TeamsOwnedSubscription,
        *,
        provider_identity_key: str,
    ) -> bool:
        if subscription.provider_identity_key:
            return subscription.provider_identity_key == provider_identity_key
        resolver = self.provider_identity_key_for_directory
        if resolver is None:
            return True
        try:
            return resolver(subscription.directory_path) == provider_identity_key
        except Exception:
            return False

    def _matching_subscriptions(
        self,
        *,
        provider_identity_key: str,
        tenant_id: str,
        target: str,
    ) -> list[TeamsOwnedSubscription]:
        resource = notification_resource_for_target(target)
        return [
            item
            for item in self.store.list_owned_subscriptions()
            if item.owner_id == self.owner_id
            and item.tenant_id == tenant_id
            and item.target == target
            and item.resource == resource
            and self._matches_provider_identity(
                item,
                provider_identity_key=provider_identity_key,
            )
        ]

    def _remove_directory_consumer(
        self,
        client: httpx.Client,
        *,
        service: GraphTeamsService,
        subscription: TeamsOwnedSubscription,
        directory_path: str,
        now: datetime,
    ) -> bool:
        consumers = self._consumer_directories(subscription)
        consumers.discard(directory_path)
        if not consumers:
            return self._delete_owned(
                client,
                service=service,
                subscription=subscription,
                now=now,
            )
        self.store.upsert_owned_subscription(
            subscription.model_copy(
                update={
                    "directory_path": self._representative_directory(consumers),
                    "consumer_directory_paths": sorted(consumers),
                    "updated_at": now,
                }
            )
        )
        return True

    def _ensure_subscription(
        self,
        client: httpx.Client,
        *,
        service: GraphTeamsService,
        directory_path: str,
        provider_identity_key: str,
        tenant_id: str,
        target: str,
        now: datetime,
    ) -> tuple[bool, str | None]:
        directory_record = self.store.find_owned_subscription(
            directory_path=directory_path,
            tenant_id=tenant_id,
            target=target,
        )
        if directory_record is not None and directory_record.owner_id != self.owner_id:
            return False, "subscription is owned by another deployment"
        candidates = self._matching_subscriptions(
            provider_identity_key=provider_identity_key,
            tenant_id=tenant_id,
            target=target,
        )
        stale_callbacks = [
            item for item in candidates if not self._uses_current_callback_urls(item)
        ]
        for stale in stale_callbacks:
            if not self._delete_owned(
                client,
                service=service,
                subscription=stale,
                now=now,
            ):
                refreshed = self.store.get_owned_subscription(stale.subscription_id)
                return False, refreshed.last_error if refreshed is not None else None
        candidates = [item for item in candidates if item not in stale_callbacks]
        existing = self._preferred_subscription(candidates)
        if existing is not None:
            consumers = {directory_path}
            for candidate in candidates:
                consumers.update(self._consumer_directories(candidate))
            existing = existing.model_copy(
                update={
                    "provider_identity_key": provider_identity_key,
                    "directory_path": self._representative_directory(consumers),
                    "consumer_directory_paths": sorted(consumers),
                    "updated_at": now,
                }
            )
            self.store.upsert_owned_subscription(existing)
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
            self._cleanup_duplicate_subscriptions(
                client,
                service=service,
                provider_identity_key=provider_identity_key,
                tenant_id=tenant_id,
                target=target,
                keeper=existing,
                now=now,
            )
            return True, None
        if existing is not None and existing.health != "removed":
            if self._renew(client, service=service, subscription=existing, now=now):
                refreshed_existing = self.store.get_owned_subscription(existing.subscription_id)
                self._cleanup_duplicate_subscriptions(
                    client,
                    service=service,
                    provider_identity_key=provider_identity_key,
                    tenant_id=tenant_id,
                    target=target,
                    keeper=refreshed_existing or existing,
                    now=now,
                )
                return True, None
            if _utc(existing.expires_at) > now:
                refreshed = self.store.get_owned_subscription(existing.subscription_id)
                return False, refreshed.last_error if refreshed is not None else None
        created, error = self._create(
            client,
            service=service,
            directory_path=directory_path,
            provider_identity_key=provider_identity_key,
            tenant_id=tenant_id,
            target=target,
            existing=existing,
            now=now,
        )
        if created:
            current = self._preferred_subscription(
                self._matching_subscriptions(
                    provider_identity_key=provider_identity_key,
                    tenant_id=tenant_id,
                    target=target,
                )
            )
            if current is not None:
                self._cleanup_duplicate_subscriptions(
                    client,
                    service=service,
                    provider_identity_key=provider_identity_key,
                    tenant_id=tenant_id,
                    target=target,
                    keeper=current,
                    now=now,
                )
        return created, error

    @staticmethod
    def _preferred_subscription(
        candidates: list[TeamsOwnedSubscription],
    ) -> TeamsOwnedSubscription | None:
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda item: (
                item.health == "active",
                _utc(item.expires_at),
                item.subscription_id,
            ),
        )

    def _cleanup_duplicate_subscriptions(
        self,
        client: httpx.Client,
        *,
        service: GraphTeamsService,
        provider_identity_key: str,
        tenant_id: str,
        target: str,
        keeper: TeamsOwnedSubscription,
        now: datetime,
    ) -> None:
        candidates = self._matching_subscriptions(
            provider_identity_key=provider_identity_key,
            tenant_id=tenant_id,
            target=target,
        )
        consumers = set()
        for candidate in candidates:
            consumers.update(self._consumer_directories(candidate))
        keeper = keeper.model_copy(
            update={
                "provider_identity_key": provider_identity_key,
                "directory_path": self._representative_directory(consumers),
                "consumer_directory_paths": sorted(consumers),
                "updated_at": now,
            }
        )
        self.store.upsert_owned_subscription(keeper)
        for candidate in candidates:
            if candidate.subscription_id == keeper.subscription_id:
                continue
            self._delete_owned(
                client,
                service=service,
                subscription=candidate,
                now=now,
            )

    def _create(
        self,
        client: httpx.Client,
        *,
        service: GraphTeamsService,
        directory_path: str,
        provider_identity_key: str,
        tenant_id: str,
        target: str,
        existing: TeamsOwnedSubscription | None,
        now: datetime,
    ) -> tuple[bool, str | None]:
        notification_url, lifecycle_notification_url = self._callback_urls()
        if not notification_url or not lifecycle_notification_url:
            return False, "change notification public base URL is not configured"
        client_state = secrets.token_urlsafe(48)[:96]
        requested_expiry = now + timedelta(
            hours=self.worker_config.change_notification_subscription_lifetime_hours
        )
        payload = {
            "changeType": "created",
            "notificationUrl": notification_url,
            "lifecycleNotificationUrl": lifecycle_notification_url,
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
                directory_path=self._representative_directory(
                    self._consumer_directories(existing) | {directory_path}
                    if existing is not None
                    else {directory_path}
                ),
                provider_identity_key=provider_identity_key,
                consumer_directory_paths=sorted(
                    self._consumer_directories(existing) | {directory_path}
                    if existing is not None
                    else {directory_path}
                ),
                tenant_id=tenant_id,
                expires_at=expires_at,
                health="active",
                notification_url=notification_url,
                lifecycle_notification_url=lifecycle_notification_url,
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
    ) -> bool:
        if subscription.health == "deleted":
            return True
        if (
            subscription.cleanup_next_attempt_at is not None
            and _utc(subscription.cleanup_next_attempt_at) > now
        ):
            return False
        attempt_count = subscription.cleanup_attempt_count + 1
        retry_delay = min(
            self.worker_config.target_backoff_initial_seconds * (2 ** (attempt_count - 1)),
            self.worker_config.target_backoff_max_seconds,
        )

        def record_failure(error: BaseException) -> None:
            self.store.upsert_owned_subscription(
                subscription.model_copy(
                    update={
                        "health": "degraded",
                        "cleanup_attempted": False,
                        "cleanup_attempt_count": attempt_count,
                        "cleanup_next_attempt_at": now + timedelta(seconds=retry_delay),
                        "updated_at": now,
                        "last_error": _safe_error(error),
                    }
                )
            )

        try:
            service._request_with_retries(
                client,
                "DELETE",
                f"{service.api_base_url}/subscriptions/"
                f"{quote(subscription.subscription_id, safe='')}",
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                record_failure(exc)
                return False
        except Exception as exc:
            record_failure(exc)
            return False
        self.store.upsert_owned_subscription(
            subscription.model_copy(
                update={
                    "health": "deleted",
                    "cleanup_attempted": True,
                    "cleanup_attempt_count": attempt_count,
                    "cleanup_next_attempt_at": None,
                    "updated_at": now,
                    "last_error": None,
                }
            )
        )
        return True

    def _callback_urls(self) -> tuple[str, str]:
        base_url = str(self.worker_config.change_notification_public_base_url or "").rstrip("/")
        if not base_url:
            return "", ""
        return (
            f"{base_url}/msgraph/teams/notifications",
            f"{base_url}/msgraph/teams/lifecycle",
        )

    def _uses_current_callback_urls(self, subscription: TeamsOwnedSubscription) -> bool:
        notification_url, lifecycle_notification_url = self._callback_urls()
        return (
            bool(notification_url)
            and subscription.notification_url == notification_url
            and subscription.lifecycle_notification_url == lifecycle_notification_url
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
