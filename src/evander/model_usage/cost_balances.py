from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from evander.model_usage.models import ModelInferenceUsageEvent
from evander.model_usage.pricing import (
    ModelPricingCatalog,
    estimate_model_usage_cost,
    extract_cache_write_input_tokens,
    extract_cached_input_tokens,
)

TRANSCRIPT_USAGE_SOURCE = "claude_transcript_backfill"


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _raw_metadata_text(raw_usage_metadata: Mapping[str, Any], section: str, key: str) -> str | None:
    payload = raw_usage_metadata.get(section)
    if not isinstance(payload, Mapping):
        return None
    return _optional_text(payload.get(key))


def model_usage_source(event: ModelInferenceUsageEvent) -> str:
    request_metadata = event.raw_usage_metadata.get("request_metadata")
    if isinstance(request_metadata, Mapping):
        source = _optional_text(request_metadata.get("source"))
        if source == TRANSCRIPT_USAGE_SOURCE:
            return "transcript"
    return "relay"


def model_usage_request_key(event: ModelInferenceUsageEvent) -> tuple[str, str] | None:
    session_id = _optional_text(event.session_id)
    request_id = next(
        (
            value
            for value in (
                _optional_text(event.request_id),
                _raw_metadata_text(
                    event.raw_usage_metadata, "response_metadata", "provider_request_id"
                ),
                _raw_metadata_text(event.raw_usage_metadata, "response_metadata", "request_id"),
                _raw_metadata_text(
                    event.raw_usage_metadata, "request_metadata", "provider_request_id"
                ),
                _raw_metadata_text(event.raw_usage_metadata, "request_metadata", "request_id"),
            )
            if value is not None
        ),
        None,
    )
    if session_id is None or request_id is None:
        return None
    return session_id, request_id


def model_usage_event_has_billable_tokens(event: ModelInferenceUsageEvent) -> bool:
    for value in (
        event.input_tokens,
        event.output_tokens,
        event.total_tokens,
        extract_cached_input_tokens(event),
        extract_cache_write_input_tokens(event).total,
    ):
        if value is not None and int(value) > 0:
            return True
    return False


def dedupe_model_usage_cost_events(
    events: Iterable[ModelInferenceUsageEvent],
) -> list[ModelInferenceUsageEvent]:
    """Apply the same relay/transcript precedence used by Team cost reports."""
    materialized = [
        event
        for event in events
        if model_usage_source(event) != "transcript" or model_usage_event_has_billable_tokens(event)
    ]
    transcript_request_keys = {
        key
        for event in materialized
        if model_usage_source(event) == "transcript"
        and _optional_text(event.provider) == "anthropic"
        and (key := model_usage_request_key(event)) is not None
    }
    if not transcript_request_keys:
        return materialized
    return [
        event
        for event in materialized
        if not (
            model_usage_source(event) != "transcript"
            and _optional_text(event.provider) == "anthropic"
            and model_usage_request_key(event) in transcript_request_keys
        )
    ]


@dataclass(frozen=True)
class CostAttribution:
    team_id: str | None
    project_id: str | None = None
    agent_id: str | None = None
    source: str = "unattributed"


def metadata_cost_attribution(event: ModelInferenceUsageEvent) -> CostAttribution | None:
    request_metadata = event.raw_usage_metadata.get("request_metadata")
    if not isinstance(request_metadata, Mapping):
        return None
    if not any(key in request_metadata for key in ("team_id", "project_id", "agent_id")):
        return None
    return CostAttribution(
        team_id=_optional_text(request_metadata.get("team_id")),
        project_id=_optional_text(request_metadata.get("project_id")),
        agent_id=_optional_text(request_metadata.get("agent_id")),
        source="usage_metadata",
    )


def request_cost_attribution(
    events: Iterable[ModelInferenceUsageEvent],
    *,
    fallback: Callable[[ModelInferenceUsageEvent], CostAttribution | None],
) -> CostAttribution | None:
    selected: CostAttribution | None = None
    selected_rank: tuple[int, int, int] | None = None
    for event in events:
        attribution = metadata_cost_attribution(event) or fallback(event)
        if attribution is None:
            continue
        rank = _request_attribution_rank(event, attribution)
        if selected is None or selected_rank is None or rank > selected_rank:
            selected = attribution
            selected_rank = rank
    return selected


def _request_attribution_rank(
    event: ModelInferenceUsageEvent,
    attribution: CostAttribution,
) -> tuple[int, int, int]:
    relay_rank = 1 if model_usage_source(event) != "transcript" else 0
    context_rank = 1 if attribution.source == "dispatch_correlation_context" else 0
    score = 0
    if attribution.team_id:
        score += 4
    if attribution.project_id:
        score += 2
    if attribution.agent_id:
        score += 1
    if attribution.source == "usage_metadata":
        score += 1
    return relay_rank, context_rank, score


@dataclass
class UsageCostSummary:
    estimated_cost_usd: Decimal = Decimal("0")
    reported_cost_usd: Decimal = Decimal("0")
    events_count: int = 0
    priced_events_count: int = 0
    unpriced_events_count: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_status_counts: dict[str, int] = field(default_factory=dict)

    @property
    def total_cost_usd(self) -> Decimal:
        return self.estimated_cost_usd + self.reported_cost_usd

    def add_event(
        self,
        event: ModelInferenceUsageEvent,
        catalog: ModelPricingCatalog | None,
    ) -> None:
        estimate = estimate_model_usage_cost(event, catalog, currency="usd")
        self.events_count += 1
        if estimate.total is None:
            self.unpriced_events_count += 1
        else:
            if estimate.reported_total is not None:
                self.reported_cost_usd += estimate.reported_total
            elif estimate.estimated_total is not None:
                self.estimated_cost_usd += estimate.estimated_total
            self.priced_events_count += 1
        self.input_tokens += int(event.input_tokens or 0)
        self.cached_input_tokens += int(extract_cached_input_tokens(event) or 0)
        self.cache_write_input_tokens += extract_cache_write_input_tokens(event).total
        self.output_tokens += int(event.output_tokens or 0)
        self.total_tokens += int(
            event.total_tokens
            if event.total_tokens is not None
            else int(event.input_tokens or 0) + int(event.output_tokens or 0)
        )
        self.cost_status_counts[estimate.status] = (
            int(self.cost_status_counts.get(estimate.status, 0)) + 1
        )

    def apply(self, other: "UsageCostSummary", *, sign: int = 1) -> None:
        self.estimated_cost_usd += Decimal(sign) * other.estimated_cost_usd
        self.reported_cost_usd += Decimal(sign) * other.reported_cost_usd
        for field_name in (
            "events_count",
            "priced_events_count",
            "unpriced_events_count",
            "input_tokens",
            "cached_input_tokens",
            "cache_write_input_tokens",
            "output_tokens",
            "total_tokens",
        ):
            setattr(
                self,
                field_name,
                int(getattr(self, field_name)) + sign * int(getattr(other, field_name)),
            )
        for status, count in other.cost_status_counts.items():
            updated = int(self.cost_status_counts.get(status, 0)) + sign * int(count)
            if updated:
                self.cost_status_counts[status] = updated
            else:
                self.cost_status_counts.pop(status, None)
        self._normalize_non_negative()

    def _normalize_non_negative(self) -> None:
        if abs(self.estimated_cost_usd) < Decimal("1e-24"):
            self.estimated_cost_usd = Decimal("0")
        if abs(self.reported_cost_usd) < Decimal("1e-24"):
            self.reported_cost_usd = Decimal("0")
        for field_name in (
            "events_count",
            "priced_events_count",
            "unpriced_events_count",
            "input_tokens",
            "cached_input_tokens",
            "cache_write_input_tokens",
            "output_tokens",
            "total_tokens",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"incremental cost balance {field_name} became negative")
        if self.estimated_cost_usd < 0:
            raise ValueError("incremental estimated cost balance became negative")
        if self.reported_cost_usd < 0:
            raise ValueError("incremental reported cost balance became negative")
        if any(count < 0 for count in self.cost_status_counts.values()):
            raise ValueError("incremental cost status count became negative")

    def storage_payload(self) -> dict[str, Any]:
        return {
            "estimated_cost_usd": str(self.estimated_cost_usd),
            "reported_cost_usd": str(self.reported_cost_usd),
            "events_count": self.events_count,
            "priced_events_count": self.priced_events_count,
            "unpriced_events_count": self.unpriced_events_count,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_input_tokens": self.cache_write_input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_status_counts": dict(sorted(self.cost_status_counts.items())),
        }

    def public_payload(self) -> dict[str, Any]:
        coverage_reasons = sorted(
            status
            for status, count in self.cost_status_counts.items()
            if status not in {"estimated", "reported"} and count
        )
        return {
            "currency": "usd",
            "estimated_cost": float(self.estimated_cost_usd),
            "reported_cost": float(self.reported_cost_usd),
            "total_cost": float(self.total_cost_usd),
            "events_count": self.events_count,
            "priced_events_count": self.priced_events_count,
            "unpriced_events_count": self.unpriced_events_count,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_input_tokens": self.cache_write_input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_status_counts": dict(sorted(self.cost_status_counts.items())),
            "coverage_status": "partial" if coverage_reasons else "complete",
            "coverage_reasons": coverage_reasons,
        }

    @classmethod
    def from_storage_payload(cls, payload: Mapping[str, Any] | None) -> "UsageCostSummary":
        raw = payload or {}
        return cls(
            estimated_cost_usd=Decimal(str(raw.get("estimated_cost_usd") or "0")),
            reported_cost_usd=Decimal(str(raw.get("reported_cost_usd") or "0")),
            events_count=int(raw.get("events_count") or 0),
            priced_events_count=int(raw.get("priced_events_count") or 0),
            unpriced_events_count=int(raw.get("unpriced_events_count") or 0),
            input_tokens=int(raw.get("input_tokens") or 0),
            cached_input_tokens=int(raw.get("cached_input_tokens") or 0),
            cache_write_input_tokens=int(raw.get("cache_write_input_tokens") or 0),
            output_tokens=int(raw.get("output_tokens") or 0),
            total_tokens=int(raw.get("total_tokens") or 0),
            cost_status_counts={
                str(key): int(value)
                for key, value in dict(raw.get("cost_status_counts") or {}).items()
            },
        )


__all__ = [
    "CostAttribution",
    "TRANSCRIPT_USAGE_SOURCE",
    "UsageCostSummary",
    "dedupe_model_usage_cost_events",
    "metadata_cost_attribution",
    "model_usage_event_has_billable_tokens",
    "model_usage_request_key",
    "model_usage_source",
    "request_cost_attribution",
]
