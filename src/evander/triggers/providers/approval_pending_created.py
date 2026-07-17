from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from evander.events.models import TriggerEvent
from evander.events.provider_models import ApprovalPendingCreatedTriggerDefinition
from evander.models import ApprovalRequest, ApprovalStatus, DirectoryPath
from evander.storage import JsonStore
from evander.triggers.delivery import (
    TriggerDeliveryRepository,
    TriggerDeliverySourceItem,
    TriggerDeliveryStore,
    TriggerDeliverySubscription,
)
from evander.triggers.providers.base import TriggerProviderBase


class ApprovalPendingCreatedTriggerProvider(TriggerProviderBase):
    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        now = datetime.now(UTC)
        subscription = TriggerDeliverySubscription(
            trigger_type=trigger.type,
            trigger_id=trigger.id,
            directory_scope=trigger.directory_path,
        )
        requests = self._matching_requests(trigger)
        source_items = [
            TriggerDeliverySourceItem(
                source_item_id=_source_item_id(request),
                source_occurred_at=_created_at_utc(request.created_at),
            )
            for request in requests
        ]
        delivery_store = self._delivery_store()
        delivery_store.initialize_subscription_if_new(
            subscription,
            source_items,
            now=now,
        )
        undelivered_by_id = {
            item.source_item_id: item
            for item in delivery_store.list_undelivered(subscription, source_items)
        }

        events: list[TriggerEvent] = []
        for request in requests:
            source_item_id = _source_item_id(request)
            if source_item_id not in undelivered_by_id:
                continue
            request_payload = request.model_dump(mode="json")
            events.append(
                TriggerEvent(
                    event_id=f"{trigger.id}:{source_item_id}",
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=_created_at_utc(request.created_at),
                    payload=_build_event_payload(request_payload),
                )
            )
        return events

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        approval_request = _extract_approval_request(event.payload)
        if approval_request is None:
            return
        subscription = TriggerDeliverySubscription(
            trigger_type=trigger.type,
            trigger_id=trigger.id,
            directory_scope=trigger.directory_path,
        )
        self._delivery_store().mark_published(
            subscription,
            TriggerDeliverySourceItem(
                source_item_id=_source_item_id(approval_request),
                source_occurred_at=_created_at_utc(approval_request.created_at),
            ),
            event_id=event.event_id,
        )

    def _trigger(self) -> ApprovalPendingCreatedTriggerDefinition:
        if not isinstance(self.trigger_definition, ApprovalPendingCreatedTriggerDefinition):
            raise RuntimeError(
                "ApprovalPendingCreatedTriggerProvider initialized with wrong definition"
            )
        return self.trigger_definition

    def _store(self) -> JsonStore[ApprovalRequest]:
        approval_store_path = getattr(self._resolved_config(), "approval_store_path", None)
        if approval_store_path is None:
            approval_store_path = self._runtime_dir() / "approvals.json"
        return JsonStore(Path(approval_store_path), ApprovalRequest)

    def _delivery_store(self) -> TriggerDeliveryRepository:
        return TriggerDeliveryStore(self._runtime_dir() / "trigger-deliveries.json")

    def _runtime_dir(self) -> Path:
        if self.runtime_context is None:
            raise RuntimeError("ApprovalPendingCreatedTriggerProvider has no runtime context")
        runtime_dir = getattr(self._resolved_config(), "runtime_dir", None)
        if runtime_dir is None:
            raise RuntimeError(
                "ApprovalPendingCreatedTriggerProvider runtime config has no runtime_dir"
            )
        return Path(runtime_dir)

    def _resolved_config(self) -> object:
        if self.runtime_context is None:
            raise RuntimeError("ApprovalPendingCreatedTriggerProvider has no runtime context")
        return self.runtime_context.resolved_config

    def _matching_requests(
        self, trigger: ApprovalPendingCreatedTriggerDefinition
    ) -> list[ApprovalRequest]:
        requests = [
            request
            for request in self._store().read_all().values()
            if request.status == ApprovalStatus.PENDING
            and _request_in_scope(request.directory_path, trigger.directory_path)
            and _matches_filter(request.action_type, trigger.action_types)
            and _matches_filter(_request_tool_name(request), trigger.tool_names)
        ]
        requests.sort(key=lambda request: (_created_at_utc(request.created_at), request.request_id))
        return requests


def _build_event_payload(request_payload: dict[str, object]) -> dict[str, object]:
    payload = {
        "approval_request_id": request_payload.get("request_id"),
        "session_id": request_payload.get("session_id"),
        "directory_path": request_payload.get("directory_path"),
        "action_type": request_payload.get("action_type"),
        "tool_name": request_payload.get("tool_name"),
        "human_summary": request_payload.get("human_summary"),
        "payload_digest": request_payload.get("payload_digest"),
        "payload": request_payload.get("payload"),
        "vm_id": request_payload.get("vm_id"),
        "intended_effect": request_payload.get("intended_effect"),
        "approval_settings": request_payload.get("approval_settings"),
        "notification_status": request_payload.get("notification_status"),
        "notification_target_ids": request_payload.get("notification_target_ids"),
        "notification_attempted_at": request_payload.get("notification_attempted_at"),
        "notification_error": request_payload.get("notification_error"),
        "notification_deliveries": request_payload.get("notification_deliveries"),
        "created_at": request_payload.get("created_at"),
        "expires_at": request_payload.get("expires_at"),
    }
    payload["approval_request"] = request_payload
    return payload


def _extract_approval_request(payload: dict[str, object]) -> ApprovalRequest | None:
    approval_request = payload.get("approval_request")
    if isinstance(approval_request, dict):
        return ApprovalRequest.model_validate(approval_request)
    return None


def _matches_filter(value: str, filters: list[str]) -> bool:
    return not filters or value in filters


def _request_tool_name(request: ApprovalRequest) -> str:
    return request.tool_name or request.action_type


def _request_in_scope(request_directory_path: str, scope_directory_path: str) -> bool:
    normalized_request = DirectoryPath(segments=request_directory_path).as_posix()
    normalized_scope = DirectoryPath(segments=scope_directory_path).as_posix()
    if normalized_scope == "root":
        return True
    return normalized_request == normalized_scope or normalized_request.startswith(
        f"{normalized_scope}/"
    )


def _source_item_id(request: ApprovalRequest) -> str:
    return f"{request.request_id}:{_created_at_utc(request.created_at).isoformat()}"


def _created_at_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
