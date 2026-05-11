from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Literal, Protocol

from pydantic import BaseModel, Field, field_validator

from agent_sandbox.models import DirectoryPath

TriggerDeliveryStatus = Literal[
    "suppressed_initial_history",
    "published",
    "skipped_duplicate",
]


@dataclass(frozen=True)
class TriggerDeliverySubscription:
    trigger_type: str
    trigger_id: str
    directory_scope: str = "root"

    @property
    def key(self) -> str:
        payload = json.dumps(
            {
                "directory_scope": _normalize_directory_scope(self.directory_scope),
                "trigger_id": _normalize_required(self.trigger_id, "trigger_id"),
                "trigger_type": _normalize_required(self.trigger_type, "trigger_type"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TriggerDeliverySourceItem:
    source_item_id: str
    source_occurred_at: datetime


class TriggerDeliverySubscriptionRecord(BaseModel):
    subscription_key: str = Field(min_length=1)
    trigger_type: str = Field(min_length=1)
    trigger_id: str = Field(min_length=1)
    directory_scope: str = "root"
    started_at: datetime
    initialized_at: datetime
    updated_at: datetime

    @field_validator("trigger_type", "trigger_id", mode="before")
    @classmethod
    def normalize_required_strings(cls, value: object) -> str:
        return _normalize_required(value, "value")

    @field_validator("directory_scope", mode="before")
    @classmethod
    def normalize_directory_scope(cls, value: object) -> str:
        return _normalize_directory_scope(value)


class TriggerDeliveryRecord(BaseModel):
    delivery_key: str = Field(min_length=1)
    subscription_key: str = Field(min_length=1)
    trigger_type: str = Field(min_length=1)
    trigger_id: str = Field(min_length=1)
    directory_scope: str = "root"
    source_item_id: str = Field(min_length=1)
    source_occurred_at: datetime
    event_id: str | None = None
    status: TriggerDeliveryStatus
    created_at: datetime
    updated_at: datetime

    @field_validator("trigger_type", "trigger_id", "source_item_id", mode="before")
    @classmethod
    def normalize_required_strings(cls, value: object) -> str:
        return _normalize_required(value, "value")

    @field_validator("event_id", mode="before")
    @classmethod
    def normalize_optional_event_id(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("directory_scope", mode="before")
    @classmethod
    def normalize_directory_scope(cls, value: object) -> str:
        return _normalize_directory_scope(value)


class TriggerDeliveryRepository(Protocol):
    def initialize_subscription_if_new(
        self,
        subscription: TriggerDeliverySubscription,
        existing_items: list[TriggerDeliverySourceItem],
        *,
        now: datetime | None = None,
    ) -> bool: ...

    def list_undelivered(
        self,
        subscription: TriggerDeliverySubscription,
        candidate_items: list[TriggerDeliverySourceItem],
    ) -> list[TriggerDeliverySourceItem]: ...

    def mark_published(
        self,
        subscription: TriggerDeliverySubscription,
        source_item: TriggerDeliverySourceItem,
        *,
        event_id: str,
        now: datetime | None = None,
    ) -> TriggerDeliveryRecord: ...

    def read_raw(self) -> dict[str, object]: ...


class TriggerDeliveryStore:
    """Per-trigger delivery state for inbox-backed trigger providers."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write({"subscriptions": {}, "deliveries": {}})

    def initialize_subscription_if_new(
        self,
        subscription: TriggerDeliverySubscription,
        existing_items: list[TriggerDeliverySourceItem],
        *,
        now: datetime | None = None,
    ) -> bool:
        initialized_at = _normalize_datetime(now)
        normalized_subscription = _normalize_subscription(subscription)

        with self._lock:
            payload = self._read_raw()
            subscriptions = payload.setdefault("subscriptions", {})
            deliveries = payload.setdefault("deliveries", {})
            if normalized_subscription.key in subscriptions:
                return False

            subscriptions[normalized_subscription.key] = TriggerDeliverySubscriptionRecord(
                subscription_key=normalized_subscription.key,
                trigger_type=normalized_subscription.trigger_type,
                trigger_id=normalized_subscription.trigger_id,
                directory_scope=normalized_subscription.directory_scope,
                started_at=initialized_at,
                initialized_at=initialized_at,
                updated_at=initialized_at,
            ).model_dump(mode="json")

            for item in existing_items:
                normalized_item = _normalize_source_item(item)
                delivery_key = _delivery_key(normalized_subscription, normalized_item.source_item_id)
                deliveries[delivery_key] = TriggerDeliveryRecord(
                    delivery_key=delivery_key,
                    subscription_key=normalized_subscription.key,
                    trigger_type=normalized_subscription.trigger_type,
                    trigger_id=normalized_subscription.trigger_id,
                    directory_scope=normalized_subscription.directory_scope,
                    source_item_id=normalized_item.source_item_id,
                    source_occurred_at=normalized_item.source_occurred_at,
                    event_id=None,
                    status="suppressed_initial_history",
                    created_at=initialized_at,
                    updated_at=initialized_at,
                ).model_dump(mode="json")

            self._atomic_write(payload)
            return True

    def list_undelivered(
        self,
        subscription: TriggerDeliverySubscription,
        candidate_items: list[TriggerDeliverySourceItem],
    ) -> list[TriggerDeliverySourceItem]:
        normalized_subscription = _normalize_subscription(subscription)

        with self._lock:
            payload = self._read_raw()
            subscription_record = self._subscription_record(payload, normalized_subscription)
            if subscription_record is None:
                return []
            deliveries = payload.get("deliveries", {})
            undelivered: list[TriggerDeliverySourceItem] = []
            for item in candidate_items:
                normalized_item = _normalize_source_item(item)
                if normalized_item.source_occurred_at < subscription_record.started_at:
                    continue
                delivery_key = _delivery_key(
                    normalized_subscription,
                    normalized_item.source_item_id,
                )
                if delivery_key in deliveries:
                    continue
                undelivered.append(normalized_item)

        undelivered.sort(key=lambda item: (item.source_occurred_at, item.source_item_id))
        return undelivered

    def mark_published(
        self,
        subscription: TriggerDeliverySubscription,
        source_item: TriggerDeliverySourceItem,
        *,
        event_id: str,
        now: datetime | None = None,
    ) -> TriggerDeliveryRecord:
        published_at = _normalize_datetime(now)
        normalized_subscription = _normalize_subscription(subscription)
        normalized_item = _normalize_source_item(source_item)
        normalized_event_id = _normalize_required(event_id, "event_id")
        delivery_key = _delivery_key(normalized_subscription, normalized_item.source_item_id)

        with self._lock:
            payload = self._read_raw()
            subscriptions = payload.setdefault("subscriptions", {})
            if normalized_subscription.key not in subscriptions:
                subscriptions[normalized_subscription.key] = TriggerDeliverySubscriptionRecord(
                    subscription_key=normalized_subscription.key,
                    trigger_type=normalized_subscription.trigger_type,
                    trigger_id=normalized_subscription.trigger_id,
                    directory_scope=normalized_subscription.directory_scope,
                    started_at=published_at,
                    initialized_at=published_at,
                    updated_at=published_at,
                ).model_dump(mode="json")

            deliveries = payload.setdefault("deliveries", {})
            existing = self._delivery_record(payload, delivery_key)
            if existing is not None:
                return existing

            record = TriggerDeliveryRecord(
                delivery_key=delivery_key,
                subscription_key=normalized_subscription.key,
                trigger_type=normalized_subscription.trigger_type,
                trigger_id=normalized_subscription.trigger_id,
                directory_scope=normalized_subscription.directory_scope,
                source_item_id=normalized_item.source_item_id,
                source_occurred_at=normalized_item.source_occurred_at,
                event_id=normalized_event_id,
                status="published",
                created_at=published_at,
                updated_at=published_at,
            )
            deliveries[delivery_key] = record.model_dump(mode="json")
            self._atomic_write(payload)
            return record

    def read_raw(self) -> dict[str, object]:
        with self._lock:
            return self._read_raw()

    def _subscription_record(
        self,
        payload: dict[str, object],
        subscription: TriggerDeliverySubscription,
    ) -> TriggerDeliverySubscriptionRecord | None:
        subscriptions = payload.get("subscriptions")
        if not isinstance(subscriptions, dict):
            return None
        raw = subscriptions.get(subscription.key)
        if not isinstance(raw, dict):
            return None
        return TriggerDeliverySubscriptionRecord.model_validate(raw)

    def _delivery_record(
        self,
        payload: dict[str, object],
        delivery_key: str,
    ) -> TriggerDeliveryRecord | None:
        deliveries = payload.get("deliveries")
        if not isinstance(deliveries, dict):
            return None
        raw = deliveries.get(delivery_key)
        if not isinstance(raw, dict):
            return None
        return TriggerDeliveryRecord.model_validate(raw)

    def _read_raw(self) -> dict[str, object]:
        text = self.path.read_text(encoding="utf-8")
        if not text.strip():
            return {"subscriptions": {}, "deliveries": {}}
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError(f"Trigger delivery store at {self.path} must contain an object")
        payload.setdefault("subscriptions", {})
        payload.setdefault("deliveries", {})
        return payload

    def _atomic_write(self, payload: dict[str, object]) -> None:
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)


def _normalize_subscription(
    subscription: TriggerDeliverySubscription,
) -> TriggerDeliverySubscription:
    return TriggerDeliverySubscription(
        trigger_type=_normalize_required(subscription.trigger_type, "trigger_type"),
        trigger_id=_normalize_required(subscription.trigger_id, "trigger_id"),
        directory_scope=_normalize_directory_scope(subscription.directory_scope),
    )


def _normalize_source_item(item: TriggerDeliverySourceItem) -> TriggerDeliverySourceItem:
    return TriggerDeliverySourceItem(
        source_item_id=_normalize_required(item.source_item_id, "source_item_id"),
        source_occurred_at=_normalize_datetime(item.source_occurred_at),
    )


def _delivery_key(subscription: TriggerDeliverySubscription, source_item_id: str) -> str:
    payload = json.dumps(
        {
            "source_item_id": _normalize_required(source_item_id, "source_item_id"),
            "subscription_key": subscription.key,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _normalize_directory_scope(value: object) -> str:
    text = str(value).strip() if value is not None else "root"
    return DirectoryPath(segments=text or "root").as_posix()


def _normalize_required(value: object, field_name: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string")
    return text


def _normalize_datetime(value: datetime | None) -> datetime:
    candidate = value or datetime.now(UTC)
    if candidate.tzinfo is None:
        return candidate.replace(tzinfo=UTC)
    return candidate.astimezone(UTC)
