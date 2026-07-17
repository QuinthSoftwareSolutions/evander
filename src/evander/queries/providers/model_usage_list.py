from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
import logging
from typing import Any

from evander.model_usage.models import ModelInferenceUsageEvent
from evander.model_usage.pricing import (
    ModelPricingCatalog,
    default_model_pricing_catalog,
    estimate_model_usage_cost,
    extract_cache_write_input_tokens,
    extract_cached_input_tokens,
)

from .base import QueryProviderBase

logger = logging.getLogger(__name__)


class ModelUsageListQueryProvider(QueryProviderBase):
    NODE_TYPE = "query:model-usage:list"

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._query_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        admin_service = self._admin_service()
        events = admin_service.list_model_usage_events(
            session_id=self._normalize_optional_string(payload.get("session_id")),
            workflow_run_id=self._normalize_optional_string(payload.get("workflow_run_id")),
            effect_id=self._normalize_optional_string(payload.get("effect_id")),
            dispatch_id=self._normalize_optional_string(payload.get("dispatch_id")),
            directory_path=self._normalize_optional_string(payload.get("directory_path")),
            include_directory_descendants=self._coerce_optional_bool(
                payload.get("include_directory_descendants")
            )
            or False,
            provider=self._normalize_optional_string(payload.get("provider")),
            model=self._normalize_optional_string(payload.get("model")),
            agent_harness=self._normalize_optional_string(payload.get("agent_harness")),
            execution_backend=self._normalize_optional_string(payload.get("execution_backend")),
            normalized_usage_status=self._normalize_optional_string(
                payload.get("normalized_usage_status")
            ),
            request_status=self._normalize_optional_string(payload.get("request_status")),
            occurred_after=self._coerce_optional_datetime(payload.get("occurred_after")),
            occurred_before=self._coerce_optional_datetime(payload.get("occurred_before")),
            limit=self._coerce_optional_limit(payload.get("limit")),
        )

        if isinstance(events, Mapping):
            events = list(events.values())
        elif isinstance(events, Sequence) and not isinstance(events, (str, bytes, bytearray)):
            events = list(events)
        else:
            raise RuntimeError(
                f"{self.NODE_TYPE} admin_service.list_model_usage_events must return a mapping "
                "or sequence"
            )

        include_costs = self._coerce_optional_bool(payload.get("include_costs"))
        if include_costs is None:
            include_costs = True
        cost_currency = self._normalize_optional_string(payload.get("cost_currency")) or "usd"
        if cost_currency.lower() != "usd":
            raise ValueError("query:model-usage:list cost_currency only supports usd")

        catalog = self._pricing_catalog(admin_service) if include_costs else None
        items: list[dict[str, Any]] = []
        total_estimated_cost = Decimal("0")
        total_input_tokens = 0
        total_cached_input_tokens = 0
        total_cache_write_input_tokens = 0
        total_output_tokens = 0
        total_tokens = 0
        costs_estimated_count = 0
        costs_unavailable_count = 0

        for event_value in events:
            event = self._usage_event(event_value)
            item = self._event_payload(event_value)
            input_tokens = int(event.input_tokens or 0)
            output_tokens = int(event.output_tokens or 0)
            cached_input_tokens = extract_cached_input_tokens(event)
            cache_write_tokens = extract_cache_write_input_tokens(event)
            total_input_tokens += input_tokens
            total_cached_input_tokens += int(cached_input_tokens or 0)
            total_cache_write_input_tokens += cache_write_tokens.total
            total_output_tokens += output_tokens
            total_tokens += (
                int(event.total_tokens)
                if event.total_tokens is not None
                else input_tokens + output_tokens
            )

            if include_costs:
                estimate = estimate_model_usage_cost(event, catalog, currency=cost_currency)
                item.update(
                    {
                        "estimated_cost": _decimal_to_float(estimate.estimated_total),
                        "estimated_input_cost": _decimal_to_float(estimate.input_cost),
                        "estimated_cached_input_cost": _decimal_to_float(
                            estimate.cached_input_cost
                        ),
                        "estimated_cache_write_cost": _decimal_to_float(estimate.cache_write_cost),
                        "estimated_output_cost": _decimal_to_float(estimate.output_cost),
                        "cost_currency": estimate.currency,
                        "cost_status": estimate.status,
                        "pricing_rule_id": estimate.pricing_rule_id,
                        "pricing_version": estimate.pricing_version,
                        "pricing_explanation": estimate.explanation,
                        "cached_input_tokens": cached_input_tokens,
                        "cache_write_input_tokens": cache_write_tokens.total,
                    }
                )
                if estimate.estimated_total is not None:
                    total_estimated_cost += estimate.estimated_total
                    costs_estimated_count += 1
                else:
                    costs_unavailable_count += 1
            items.append(item)

        return {
            "items": items,
            "count": len(items),
            "total_estimated_cost": _decimal_to_float(total_estimated_cost)
            if include_costs
            else None,
            "total_input_tokens": total_input_tokens,
            "total_cached_input_tokens": total_cached_input_tokens,
            "total_cache_write_input_tokens": total_cache_write_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_tokens,
            "costs_estimated_count": costs_estimated_count,
            "costs_unavailable_count": costs_unavailable_count,
        }

    def _query_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        query = self.query_definition
        if query is None:
            return payload
        for field_name in (
            "session_id",
            "workflow_run_id",
            "effect_id",
            "dispatch_id",
            "directory_path",
            "include_directory_descendants",
            "provider",
            "model",
            "agent_harness",
            "execution_backend",
            "normalized_usage_status",
            "request_status",
            "occurred_after",
            "occurred_before",
            "limit",
            "include_costs",
            "cost_currency",
        ):
            if hasattr(query, field_name):
                payload[field_name] = getattr(query, field_name)
        return payload

    def _admin_service(self):
        context = self._context()
        admin_service = getattr(context, "admin_service", None)
        if admin_service is None:
            raise RuntimeError("query:model-usage:list requires admin_service")
        list_model_usage_events = getattr(admin_service, "list_model_usage_events", None)
        if not callable(list_model_usage_events):
            raise RuntimeError(
                "query:model-usage:list requires admin_service.list_model_usage_events"
            )
        return admin_service

    def _event_payload(self, event: Any) -> dict[str, Any]:
        model_dump = getattr(event, "model_dump", None)
        if callable(model_dump):
            return model_dump(mode="json")
        if isinstance(event, Mapping):
            return dict(event)
        raise RuntimeError(
            "query:model-usage:list returned event values must be mappings or models"
        )

    def _usage_event(self, event: Any) -> ModelInferenceUsageEvent:
        if isinstance(event, ModelInferenceUsageEvent):
            return event
        if isinstance(event, Mapping):
            return ModelInferenceUsageEvent.model_validate(event)
        model_dump = getattr(event, "model_dump", None)
        if callable(model_dump):
            return ModelInferenceUsageEvent.model_validate(model_dump())
        raise RuntimeError(
            "query:model-usage:list returned event values must be mappings or models"
        )

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("ModelUsageListQueryProvider has no runtime context")
        return self.runtime_context

    def _pricing_catalog(self, admin_service: Any) -> ModelPricingCatalog | None:
        try:
            catalog_loader = getattr(admin_service, "model_pricing_catalog", None)
            if callable(catalog_loader):
                return catalog_loader()
            return default_model_pricing_catalog()
        except Exception as exc:
            logger.warning(
                "model_usage_pricing_catalog_load_failed exception_class=%s exception_message=%s",
                exc.__class__.__name__,
                exc,
            )
            return None

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _coerce_optional_bool(value: Any) -> bool | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        raise ValueError(
            "query:model-usage:list include_directory_descendants must resolve to a boolean"
        )

    @staticmethod
    def _coerce_optional_limit(value: Any) -> int | None:
        if value is None:
            return None
        try:
            limit = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("query:model-usage:list limit must resolve to an integer") from exc
        if limit < 1:
            raise ValueError("query:model-usage:list limit must be at least 1")
        return limit

    @staticmethod
    def _coerce_optional_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        text = str(value).strip()
        if not text:
            return None
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)
