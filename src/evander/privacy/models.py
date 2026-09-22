from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evander.privacy.providers import ProviderPolicy


class PrivacyError(ValueError):
    """Safe operator-facing error; never contains a stored record or secret."""


class PrivacyConflict(PrivacyError):
    pass


class PrivacyUnsupported(PrivacyError):
    pass


def now_utc() -> datetime:
    return datetime.now(UTC)


def revision(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetentionRule(StrictModel):
    days: int | None = Field(default=None, ge=1, le=36500)
    approved: bool = False
    approval_reference: str = Field(default="", max_length=500)
    # Existing configurable lifetimes remain authoritative at their owning service.
    trigger: Literal["source_expiry", "terminal", "created", "explicit"] = "source_expiry"
    scheduled: bool = False

    @model_validator(mode="after")
    def require_approval(self):
        if self.approved and not self.approval_reference.strip():
            raise ValueError("approved retention requires an approval reference")
        if self.scheduled and not self.approved:
            raise ValueError("scheduled retention requires approval")
        return self


class PrivacySettings(StrictModel):
    mode: Literal["report_only", "enforce"] = "report_only"
    enabled_categories: list[str] = Field(default_factory=lambda: ["unknown"], max_length=30)
    provider_policy: ProviderPolicy = Field(default_factory=ProviderPolicy)
    retention: dict[str, RetentionRule] = Field(default_factory=dict)
    export_ttl_days: int = Field(default=7, ge=1, le=30)

    @field_validator("enabled_categories")
    @classmethod
    def categories(cls, values):
        if not values or any(not value.strip() or len(value) > 80 for value in values):
            raise ValueError("enabled categories must be nonempty short identifiers")
        return sorted(set(values))


class DatasetDescriptor(StrictModel):
    dataset_id: str
    label: str
    storage: str
    retention_source: str
    purpose: str
    owner: str = "installation operator / customer controller"
    capabilities: dict[str, bool] = Field(default_factory=dict)
    related_datasets: list[str] = Field(default_factory=list)
    limitation: str = "Local lifecycle is not implemented; use a reviewed manual procedure."


class RecordReference(StrictModel):
    dataset_id: str
    record_id: str
    fingerprint: str
    directory_path: str | None = None
    eligible_at: datetime | None = None


class HoldCreate(StrictModel):
    dataset: str
    record_ids: list[str] = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=1000)
    review_at: datetime

    @field_validator("review_at")
    @classmethod
    def aware_review(cls, value):
        if value.tzinfo is None:
            raise ValueError("review_at requires a timezone")
        return value.astimezone(UTC)

    @field_validator("record_ids")
    @classmethod
    def explicit_ids(cls, values):
        if any(not value or value == "*" or len(value) > 500 for value in values):
            raise ValueError("holds require explicit nonempty record identifiers")
        return sorted(set(values))


class CaseCreate(StrictModel):
    request_type: Literal[
        "access", "portability", "rectification", "erasure", "restriction", "objection"
    ]
    controller_ref: str = Field(min_length=1, max_length=500)
    instruction_ref: str = Field(default="", max_length=500)
    subject_keys: dict[str, str] = Field(min_length=1, max_length=20)
    deadline_at: datetime

    @field_validator("deadline_at")
    @classmethod
    def aware_deadline(cls, value):
        if value.tzinfo is None:
            raise ValueError("deadline_at requires a timezone")
        return value.astimezone(UTC)

    @field_validator("subject_keys")
    @classmethod
    def bounded_keys(cls, value):
        allowed = {
            "session_id",
            "origin_session_id",
            "contact_id",
            "meeting_id",
            "user_id",
            "actor",
            "login",
            "email",
            "provider_user_id",
            "source_message_id",
            "source_mailbox",
            "call_id",
            "voice_session_id",
        }
        if set(value) - allowed or any(not v.strip() or len(v) > 500 for v in value.values()):
            raise ValueError("subject keys must be supported, nonempty identifiers")
        return value


class FollowupCreate(StrictModel):
    case_id: str
    kind: Literal["provider", "backup", "manual_store", "rectification", "restriction"]
    owner: str = Field(min_length=1, max_length=200)
    reference: str = Field(min_length=1, max_length=500)
    deadline_at: datetime

    @field_validator("deadline_at")
    @classmethod
    def aware_deadline(cls, value):
        if value.tzinfo is None:
            raise ValueError("deadline_at requires a timezone")
        return value.astimezone(UTC)


class IncidentCreate(StrictModel):
    discovered_at: datetime
    controller_ref: str = Field(min_length=1, max_length=500)
    evidence_reference: str = Field(min_length=1, max_length=500)
    containment_reference: str = Field(default="", max_length=500)
    notification_reference: str = Field(default="", max_length=500)

    @field_validator("discovered_at")
    @classmethod
    def aware_discovery(cls, value):
        if value.tzinfo is None:
            raise ValueError("discovered_at requires a timezone")
        return value.astimezone(UTC)
