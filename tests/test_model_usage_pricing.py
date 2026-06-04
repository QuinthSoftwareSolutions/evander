from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from agent_sandbox.model_usage.models import ModelInferenceUsageEvent
from agent_sandbox.model_usage.pricing import (
    clear_model_pricing_catalog_cache,
    default_model_pricing_catalog,
    estimate_model_usage_cost,
    extract_cached_input_tokens,
    load_model_pricing_catalog_payload,
)


def _event(
    *,
    provider: str = "openai",
    model: str | None = "gpt-5.2-codex",
    input_tokens: int | None = 1000,
    output_tokens: int | None = 500,
    total_tokens: int | None = 1500,
    normalized_usage_status: str = "available",
    raw_usage_metadata: dict | None = None,
    occurred_at: datetime | None = None,
) -> ModelInferenceUsageEvent:
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
    return ModelInferenceUsageEvent(
        event_id="event-1",
        occurred_at=occurred_at or datetime(2026, 6, 4, 12, 0, tzinfo=UTC),
        provider=provider,
        provider_endpoint_identifier="responses",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        request_status="success",
        normalized_usage_status=normalized_usage_status,
        raw_usage_metadata=raw_usage_metadata
        if raw_usage_metadata is not None
        else {"usage": usage, "request_metadata": {"path": "/v1/responses"}},
    )


def _pricing_catalog_payload(*, version: str, model: str) -> dict:
    return {
        "version": version,
        "currency": "usd",
        "rules": [
            {
                "id": f"openai:{model}:text",
                "provider": "openai",
                "models": [model],
                "currency": "usd",
                "rates_per_1m_tokens": {
                    "input": "1.00",
                    "output": "2.00",
                },
            }
        ],
    }


def test_openai_pricing_estimates_cached_input_costs() -> None:
    catalog = default_model_pricing_catalog()
    event = _event(
        raw_usage_metadata={
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 500,
                "total_tokens": 1500,
                "input_tokens_details": {"cached_tokens": 200},
            },
            "request_metadata": {"path": "/v1/responses"},
        }
    )

    estimate = estimate_model_usage_cost(event, catalog)

    assert estimate.status == "estimated"
    assert estimate.pricing_rule_id == "openai:gpt-5.2-codex:text:2025-12-11"
    assert estimate.cached_input_tokens == 200
    assert estimate.input_cost == Decimal("0.0014")
    assert estimate.cached_input_cost == Decimal("0.000035")
    assert estimate.output_cost == Decimal("0.007")
    assert estimate.estimated_total == Decimal("0.008435")
    assert extract_cached_input_tokens(event) == 200


def test_openai_pricing_applies_long_context_and_batch_multipliers() -> None:
    catalog = default_model_pricing_catalog()
    event = _event(
        model="gpt-5.4",
        input_tokens=300000,
        output_tokens=1000,
        total_tokens=301000,
        raw_usage_metadata={
            "usage": {
                "input_tokens": 300000,
                "output_tokens": 1000,
                "total_tokens": 301000,
            },
            "request_metadata": {"path": "/v1/batch", "batch": True},
        },
    )

    estimate = estimate_model_usage_cost(event, catalog)

    assert estimate.status == "estimated"
    assert estimate.pricing_rule_id == "openai:gpt-5.4:text:2026-03-17"
    assert estimate.input_cost == Decimal("0.75")
    assert estimate.output_cost == Decimal("0.01125")
    assert estimate.estimated_total == Decimal("0.76125")


def test_openai_pricing_rejects_rule_currency_mismatches() -> None:
    with pytest.raises(ValueError, match="rule currency must match catalog currency"):
        load_model_pricing_catalog_payload(
            {
                "version": "test-pricing",
                "currency": "usd",
                "rules": [
                    {
                        "id": "openai:test-model:text",
                        "provider": "openai",
                        "models": ["test-model"],
                        "currency": "eur",
                        "rates_per_1m_tokens": {
                            "input": "1.00",
                            "output": "2.00",
                        },
                    }
                ],
            }
        )


def test_default_model_pricing_catalog_reloads_when_file_changes(
    tmp_path, monkeypatch
) -> None:
    catalog_path = tmp_path / "pricing.json"
    catalog_path.write_text(
        json.dumps(_pricing_catalog_payload(version="cache-initial", model="test-model")),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_SANDBOX_MODEL_PRICING_PATH", str(catalog_path))
    clear_model_pricing_catalog_cache()

    assert default_model_pricing_catalog().version == "cache-initial"

    catalog_path.write_text(
        json.dumps(
            _pricing_catalog_payload(
                version="cache-updated-with-longer-version",
                model="updated-model-with-longer-name",
            )
        ),
        encoding="utf-8",
    )

    assert default_model_pricing_catalog().version == "cache-updated-with-longer-version"
    clear_model_pricing_catalog_cache()


def test_openai_pricing_prefers_service_tier_specific_rules() -> None:
    catalog = load_model_pricing_catalog_payload(
        {
            "version": "tier-test-pricing",
            "currency": "usd",
            "rules": [
                {
                    "id": "openai:tier-model:zz-generic",
                    "provider": "openai",
                    "models": ["tier-model"],
                    "effective_from": "2026-01-01T00:00:00Z",
                    "currency": "usd",
                    "rates_per_1m_tokens": {
                        "input": "1.00",
                        "output": "1.00",
                    },
                },
                {
                    "id": "openai:tier-model:aa-priority",
                    "provider": "openai",
                    "models": ["tier-model"],
                    "effective_from": "2026-01-01T00:00:00Z",
                    "currency": "usd",
                    "rates_per_1m_tokens": {
                        "input": "3.00",
                        "output": "5.00",
                    },
                    "service_tier": "priority",
                },
            ],
        }
    )
    event = _event(
        model="tier-model",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        total_tokens=2_000_000,
        raw_usage_metadata={
            "usage": {
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
                "total_tokens": 2_000_000,
            },
            "request_metadata": {
                "path": "/v1/responses",
                "service_tier": "priority",
            },
        },
    )

    estimate = estimate_model_usage_cost(event, catalog)

    assert estimate.status == "estimated"
    assert estimate.pricing_rule_id == "openai:tier-model:aa-priority"
    assert estimate.estimated_total == Decimal("8.00")


@pytest.mark.parametrize(
    ("event", "expected_status"),
    [
        (_event(provider="anthropic", model="claude-3-7-sonnet"), "unsupported_provider"),
        (_event(model="unknown-model"), "pricing_unavailable"),
        (
            _event(input_tokens=None, output_tokens=10, total_tokens=10),
            "insufficient_token_breakdown",
        ),
        (_event(normalized_usage_status="usage_unavailable"), "usage_unavailable"),
    ],
)
def test_openai_pricing_reports_unavailable_statuses(
    event: ModelInferenceUsageEvent, expected_status: str
) -> None:
    estimate = estimate_model_usage_cost(event, default_model_pricing_catalog())

    assert estimate.status == expected_status
    assert estimate.estimated_total is None
    assert estimate.explanation
