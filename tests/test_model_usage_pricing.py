from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from evander.model_usage.models import ModelInferenceUsageEvent
from evander.model_usage.cost_balances import UsageCostSummary
from evander.model_usage.pricing import (
    clear_model_pricing_catalog_cache,
    default_model_pricing_catalog,
    default_model_pricing_catalog_path,
    default_model_pricing_catalog_seed_path,
    estimate_model_usage_cost,
    extract_cache_write_input_tokens,
    extract_cached_input_tokens,
    load_model_pricing_catalog,
    load_model_pricing_catalog_payload,
    model_pricing_catalog_payload,
    seed_default_model_pricing_catalog,
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


def _seed_catalog():
    return load_model_pricing_catalog(default_model_pricing_catalog_seed_path())


@pytest.mark.parametrize(
    "endpoint, expected_rule, expected_total",
    [
        ("decisions", "openai:gpt-6-luna:decisions:2026-10-06", Decimal("0.00085")),
        ("responses", "openai:gpt-6-luna:text:2026-09-22", Decimal("0.0009225")),
    ],
)
def test_decisions_pricing_is_endpoint_scoped_and_survives_catalog_edits(
    endpoint, expected_rule, expected_total
):
    event = _event(
        model="gpt-6-luna",
        input_tokens=10000,
        output_tokens=0,
        total_tokens=10000,
        occurred_at=datetime(2026, 10, 7, 12, 0, tzinfo=UTC),
        raw_usage_metadata={
            "usage": {
                "input_tokens_details": {
                    "cached_tokens": 1000,
                    "cache_write_tokens": 500,
                }
            }
        },
    ).model_copy(update={"provider_endpoint_identifier": endpoint})
    payload = model_pricing_catalog_payload(_seed_catalog())
    catalog = load_model_pricing_catalog_payload(payload)
    estimate = estimate_model_usage_cost(event, catalog)
    assert estimate.status == "estimated" and estimate.pricing_rule_id == expected_rule
    assert estimate.estimated_total == expected_total
    if endpoint == "decisions":
        assert estimate.cached_input_cost == estimate.cache_write_cost == estimate.output_cost == 0


def test_endpoint_pricing_takes_priority_over_newer_generic_model_rules():
    payload = _pricing_catalog_payload(version="endpoint-rules", model="gpt-6-luna")
    payload["rules"][0]["effective_from"] = "2026-10-07T00:00:00Z"
    payload["rules"].append(
        {
            "id": "decisions-only",
            "provider": "openai",
            "models": ["gpt-6-luna"],
            "provider_endpoint_identifier": "decisions",
            "effective_from": "2026-10-06T00:00:00Z",
            "rates_per_1m_tokens": {"input": "0.10", "output": "0"},
        }
    )
    event = _event(
        model="gpt-6-luna",
        input_tokens=1000,
        output_tokens=0,
        occurred_at=datetime(2026, 10, 7, 12, 0, tzinfo=UTC),
    )
    catalog = load_model_pricing_catalog_payload(payload)
    decisions = estimate_model_usage_cost(
        event.model_copy(update={"provider_endpoint_identifier": "decisions"}), catalog
    )
    assert decisions.pricing_rule_id == "decisions-only"
    assert decisions.estimated_total == Decimal("0.0001")
    assert estimate_model_usage_cost(event, catalog).estimated_total == Decimal("0.001")


@pytest.mark.parametrize(
    ("model", "rule_id", "expected_total"),
    [
        ("gpt-6-sol", "openai:gpt-6-sol:text:2026-09-22", Decimal("0.012")),
        ("gpt-6-luna", "openai:gpt-6-luna:text:2026-09-22", Decimal("0.0006")),
    ],
)
def test_seed_catalog_prices_current_gpt6_models(
    model: str, rule_id: str, expected_total: Decimal
) -> None:
    event = _event(
        model=model,
        input_tokens=1000,
        output_tokens=1000,
        total_tokens=2000,
        occurred_at=datetime(2026, 9, 22, 12, 0, tzinfo=UTC),
    )

    estimate = estimate_model_usage_cost(event, _seed_catalog())

    assert estimate.status == "estimated"
    assert estimate.pricing_rule_id == rule_id
    assert estimate.estimated_total == expected_total


@pytest.mark.parametrize(
    ("requested_tier", "returned_tier", "expected_rule_id", "expected_total"),
    [
        ("ultrafast", None, "openai:gpt-6-astra:ultrafast:2026-10-07", "0.36"),
        (None, "ultrafast", "openai:gpt-6-astra:ultrafast:2026-10-07", "0.36"),
        ("ultrafast", "default", "openai:gpt-6-astra:text:2026-09-09", "0.06"),
        ("ultrafast", "priority", "openai:gpt-6-astra:priority:2026-09-09", "0.12"),
    ],
)
def test_seed_catalog_prices_astra_ultrafast_using_recorded_tier(
    requested_tier: str | None,
    returned_tier: str | None,
    expected_rule_id: str,
    expected_total: str,
) -> None:
    event = _event(
        model="gpt-6-astra",
        input_tokens=1000,
        output_tokens=1000,
        total_tokens=2000,
        # Pricing verification must not exclude previously recorded tier usage.
        occurred_at=datetime(2026, 10, 6, tzinfo=UTC),
        raw_usage_metadata={
            "request_metadata": {"service_tier": requested_tier},
            "response_metadata": {"service_tier": returned_tier},
        },
    )

    estimate = estimate_model_usage_cost(event, _seed_catalog())

    assert estimate.status == "estimated"
    assert estimate.pricing_rule_id == expected_rule_id
    assert estimate.estimated_total == Decimal(expected_total)


@pytest.mark.parametrize(
    ("input_tokens", "expected_costs"),
    [
        (1000, ("0.042", "0.0012", "0.0075", "0.3", "0.3507")),
        (272000, ("16.302", "0.0012", "0.0075", "0.3", "16.6107")),
        (272001, ("32.60412", "0.0024", "0.015", "0.45", "33.07152")),
    ],
)
def test_seed_catalog_prices_astra_ultrafast_cache_and_long_context(
    input_tokens: int, expected_costs: tuple[str, ...]
) -> None:
    event = _event(
        model="gpt-6-astra",
        input_tokens=input_tokens,
        output_tokens=1000,
        total_tokens=input_tokens + 1000,
        occurred_at=datetime(2026, 10, 7, tzinfo=UTC),
        raw_usage_metadata={
            "usage": {
                "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 100},
            },
            "request_metadata": {"service_tier": "ultrafast"},
        },
    )

    estimate = estimate_model_usage_cost(event, _seed_catalog())

    assert estimate.status == "estimated"
    assert estimate.pricing_rule_id == "openai:gpt-6-astra:ultrafast:2026-10-07"
    assert (
        estimate.input_cost,
        estimate.cached_input_cost,
        estimate.cache_write_cost,
        estimate.output_cost,
        estimate.estimated_total,
    ) == tuple(Decimal(cost) for cost in expected_costs)


def test_openai_pricing_estimates_cached_input_costs() -> None:
    catalog = _seed_catalog()
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


def test_openrouter_provider_reported_cost_is_authoritative_and_distinct() -> None:
    event = _event(
        provider="openrouter",
        model="openai/gpt-5.4",
        raw_usage_metadata={
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 500,
                "total_tokens": 1500,
                "cost": "0.01234",
                "cost_details": {"upstream_inference_cost": 0.011},
            }
        },
    )

    estimate = estimate_model_usage_cost(event, _seed_catalog())

    assert estimate.status == "reported"
    assert estimate.reported_total == Decimal("0.01234")
    assert estimate.estimated_total is None
    assert estimate.total == Decimal("0.01234")
    assert estimate.model_dump()["cost_source"] == "provider_reported"

    summary = UsageCostSummary()
    summary.add_event(event, _seed_catalog())
    assert summary.reported_cost_usd == Decimal("0.01234")
    assert summary.estimated_cost_usd == Decimal("0")
    assert summary.total_cost_usd == Decimal("0.01234")
    assert summary.public_payload()["coverage_status"] == "complete"


@pytest.mark.parametrize("cost", [0, "0.1234"])
@pytest.mark.parametrize("usage_status", ["available", "usage_unavailable"])
def test_openrouter_reported_cost_needs_no_catalog(cost, usage_status):
    event = _event(
        provider="openrouter",
        raw_usage_metadata={"usage": {"cost": cost}},
        normalized_usage_status=usage_status,
    )
    estimate = estimate_model_usage_cost(event, None)
    assert estimate.reported_total == Decimal(str(cost))
    assert estimate.estimated_total is None
    summary = UsageCostSummary()
    summary.add_event(event, None)
    restored = UsageCostSummary.from_storage_payload(summary.storage_payload())
    assert restored.total_cost_usd == Decimal(str(cost))
    restored.apply(summary, sign=-1)
    assert restored.total_cost_usd == 0
    assert restored.events_count == 0


@pytest.mark.parametrize("cost", [None, -1, "NaN", "Infinity", True, [], "invalid"])
def test_invalid_openrouter_cost_is_unavailable(cost):
    event = _event(provider="openrouter", raw_usage_metadata={"usage": {"cost": cost}})
    estimate = estimate_model_usage_cost(event, _seed_catalog())
    assert estimate.total is None
    assert estimate.status == "pricing_unavailable"


def test_openai_pricing_applies_long_context_and_batch_multipliers() -> None:
    catalog = _seed_catalog()
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


@pytest.mark.parametrize(
    ("model", "expected_rule_id", "expected_total"),
    [
        (
            "gpt-5.6-sol",
            "openai:gpt-5.6-sol:text:2026-07-10",
            Decimal("7.475"),
        ),
        (
            "gpt-5.6-terra",
            "openai:gpt-5.6-terra:text:2026-07-10",
            Decimal("3.7375"),
        ),
        (
            "gpt-5.6-luna",
            "openai:gpt-5.6-luna:text:2026-07-10",
            Decimal("1.495"),
        ),
        (
            "gpt-5.6",
            "openai:gpt-5.6-sol:text:2026-07-10",
            Decimal("7.475"),
        ),
    ],
)
def test_openai_pricing_estimates_gpt_5_6_cache_writes_from_release_day(
    model: str,
    expected_rule_id: str,
    expected_total: Decimal,
) -> None:
    event = _event(
        model=model,
        input_tokens=1_000_000,
        output_tokens=100_000,
        total_tokens=1_100_000,
        occurred_at=datetime(2026, 7, 10, 7, 13, tzinfo=UTC),
        raw_usage_metadata={
            "usage": {
                "input_tokens": 1_000_000,
                "input_tokens_details": {
                    "cached_tokens": 200_000,
                    "cache_write_tokens": 300_000,
                },
                "output_tokens": 100_000,
                "total_tokens": 1_100_000,
            },
            "request_metadata": {"path": "/v1/responses"},
        },
    )

    estimate = estimate_model_usage_cost(event, _seed_catalog())

    assert estimate.status == "estimated"
    assert estimate.pricing_rule_id == expected_rule_id
    assert estimate.input_cost is not None
    assert estimate.cached_input_cost is not None
    assert estimate.cache_write_cost is not None
    assert estimate.output_cost is not None
    assert estimate.estimated_total == expected_total
    assert estimate.cached_input_tokens == 200_000
    assert estimate.cache_write_input_tokens == 300_000
    assert extract_cache_write_input_tokens(event).total == 300_000


@pytest.mark.parametrize(
    ("model", "service_tier", "expected_rule_id", "expected_total"),
    [
        (
            "gpt-5.6",
            None,
            "openai:gpt-5.6-sol:text:2026-07-30",
            Decimal("13.45"),
        ),
        (
            "gpt-5.6-terra",
            None,
            "openai:gpt-5.6-terra:text:2026-07-30",
            Decimal("5.38"),
        ),
        (
            "gpt-5.6-luna",
            None,
            "openai:gpt-5.6-luna:text:2026-07-30",
            Decimal("0.538"),
        ),
        (
            "gpt-5.6-sol",
            "fast",
            "openai:gpt-5.6-sol:fast:2026-07-30",
            Decimal("14.95"),
        ),
        (
            "gpt-5.6-terra",
            "fast",
            "openai:gpt-5.6-terra:fast:2026-07-30",
            Decimal("5.98"),
        ),
        (
            "gpt-5.6-luna",
            "fast",
            "openai:gpt-5.6-luna:fast:2026-07-30",
            Decimal("0.598"),
        ),
        (
            "gpt-5.6-sol",
            "priority",
            "openai:gpt-5.6-sol:priority:2026-07-30",
            Decimal("14.95"),
        ),
        (
            "gpt-5.6-terra",
            "priority",
            "openai:gpt-5.6-terra:priority:2026-07-30",
            Decimal("5.98"),
        ),
        (
            "gpt-5.6-luna",
            "priority",
            "openai:gpt-5.6-luna:priority:2026-07-30",
            Decimal("0.598"),
        ),
    ],
)
def test_openai_pricing_estimates_gpt_5_6_july_price_changes(
    model: str,
    service_tier: str | None,
    expected_rule_id: str,
    expected_total: Decimal,
) -> None:
    request_metadata = {"path": "/v1/responses"}
    if service_tier is not None:
        request_metadata["service_tier"] = service_tier
    event = _event(
        model=model,
        input_tokens=1_000_000,
        output_tokens=100_000,
        total_tokens=1_100_000,
        occurred_at=datetime(2026, 7, 30, tzinfo=UTC),
        raw_usage_metadata={
            "usage": {
                "input_tokens": 1_000_000,
                "input_tokens_details": {
                    "cached_tokens": 200_000,
                    "cache_write_tokens": 300_000,
                },
                "output_tokens": 100_000,
                "total_tokens": 1_100_000,
            },
            "request_metadata": request_metadata,
        },
    )

    estimate = estimate_model_usage_cost(event, _seed_catalog())

    assert estimate.status == "estimated"
    assert estimate.pricing_rule_id == expected_rule_id
    assert estimate.estimated_total == expected_total


def test_openai_pricing_applies_gpt_5_6_standard_long_context_rates() -> None:
    event = _event(
        model="gpt-5.6-luna",
        input_tokens=300_000,
        output_tokens=100_000,
        total_tokens=400_000,
        occurred_at=datetime(2026, 7, 30, tzinfo=UTC),
    )

    estimate = estimate_model_usage_cost(event, _seed_catalog())

    assert estimate.status == "estimated"
    assert estimate.pricing_rule_id == "openai:gpt-5.6-luna:text:2026-07-30"
    assert estimate.input_cost == Decimal("0.12")
    assert estimate.output_cost == Decimal("0.18")
    assert estimate.estimated_total == Decimal("0.30")


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


def test_default_model_pricing_catalog_reloads_when_file_changes(tmp_path, monkeypatch) -> None:
    catalog_path = tmp_path / "pricing.json"
    catalog_path.write_text(
        json.dumps(_pricing_catalog_payload(version="cache-initial", model="test-model")),
        encoding="utf-8",
    )
    monkeypatch.setenv("EVANDER_MODEL_PRICING_PATH", str(catalog_path))
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


def test_default_model_pricing_catalog_path_uses_runtime_state(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("EVANDER_MODEL_PRICING_PATH", raising=False)

    assert default_model_pricing_catalog_path(runtime_dir=tmp_path) == (
        tmp_path / "model-pricing.openai.json"
    )


def test_seed_default_model_pricing_catalog_preserves_operator_edits(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("EVANDER_MODEL_PRICING_PATH", raising=False)
    catalog_path = seed_default_model_pricing_catalog(tmp_path)
    assert catalog_path.read_bytes() == default_model_pricing_catalog_seed_path().read_bytes()

    operator_payload = _pricing_catalog_payload(
        version="operator-pricing",
        model="operator-model",
    )
    catalog_path.write_text(json.dumps(operator_payload), encoding="utf-8")

    assert seed_default_model_pricing_catalog(tmp_path) == catalog_path
    assert json.loads(catalog_path.read_text(encoding="utf-8")) == operator_payload


def test_seed_default_model_pricing_catalog_migrates_legacy_operator_edits(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("EVANDER_MODEL_PRICING_PATH", raising=False)
    runtime_dir = tmp_path / "runtime"
    legacy_catalog_path = tmp_path / "config" / "model-pricing.openai.json"
    legacy_catalog_path.parent.mkdir(parents=True)
    operator_payload = _pricing_catalog_payload(
        version="legacy-operator-pricing",
        model="legacy-operator-model",
    )
    legacy_catalog_path.write_text(json.dumps(operator_payload), encoding="utf-8")

    catalog_path = seed_default_model_pricing_catalog(
        runtime_dir,
        legacy_catalog_path=legacy_catalog_path,
    )

    assert json.loads(catalog_path.read_text(encoding="utf-8")) == operator_payload


def test_seed_default_model_pricing_catalog_ignores_pristine_legacy_seed(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("EVANDER_MODEL_PRICING_PATH", raising=False)
    runtime_dir = tmp_path / "runtime"
    legacy_catalog_path = tmp_path / "config" / "model-pricing.openai.json"
    legacy_catalog_path.parent.mkdir(parents=True)
    repo_legacy_catalog = (
        default_model_pricing_catalog_seed_path().parent / "model-pricing.openai.json"
    )
    legacy_catalog_path.write_bytes(repo_legacy_catalog.read_bytes())

    catalog_path = seed_default_model_pricing_catalog(
        runtime_dir,
        legacy_catalog_path=legacy_catalog_path,
    )

    assert catalog_path.read_bytes() == default_model_pricing_catalog_seed_path().read_bytes()


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
        (_event(provider="anthropic", model="claude-3-7-sonnet"), "pricing_unavailable"),
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
    estimate = estimate_model_usage_cost(event, _seed_catalog())

    assert estimate.status == expected_status
    assert estimate.estimated_total is None
    assert estimate.explanation


def test_model_pricing_estimates_anthropic_catalog_rules() -> None:
    catalog = load_model_pricing_catalog_payload(
        {
            "version": "test-anthropic",
            "currency": "usd",
            "rules": [
                {
                    "id": "anthropic:claude-sonnet-4-6",
                    "provider": "anthropic",
                    "models": ["claude-sonnet-4-6"],
                    "rates_per_1m_tokens": {
                        "input": "3.00",
                        "output": "15.00",
                    },
                }
            ],
        }
    )
    event = _event(
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=100,
        total_tokens=1100,
    )

    estimate = estimate_model_usage_cost(event, catalog)

    assert estimate.status == "estimated"
    assert estimate.pricing_rule_id == "anthropic:claude-sonnet-4-6"
    assert estimate.estimated_total == Decimal("0.0045")


def test_anthropic_pricing_estimates_cache_reads_and_writes() -> None:
    catalog = load_model_pricing_catalog_payload(
        {
            "version": "test-anthropic-cache",
            "currency": "usd",
            "rules": [
                {
                    "id": "anthropic:claude-sonnet-4-6",
                    "provider": "anthropic",
                    "models": ["claude-sonnet-4-6"],
                    "rates_per_1m_tokens": {
                        "input": "3.00",
                        "cached_input": "0.30",
                        "cache_write_5m": "3.75",
                        "cache_write_1h": "6.00",
                        "output": "15.00",
                    },
                }
            ],
        }
    )
    event = _event(
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=100,
        total_tokens=None,
        raw_usage_metadata={
            "usage": {
                "input_tokens": 1000,
                "cache_read_input_tokens": 200000,
                "cache_creation_input_tokens": 7000,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 3000,
                    "ephemeral_1h_input_tokens": 4000,
                },
                "output_tokens": 100,
            },
            "request_metadata": {"path": "/v1/messages"},
        },
    )

    estimate = estimate_model_usage_cost(event, catalog)

    assert estimate.status == "estimated"
    assert estimate.input_cost == Decimal("0.003")
    assert estimate.cached_input_cost == Decimal("0.060")
    assert estimate.cache_write_cost == Decimal("0.03525")
    assert estimate.output_cost == Decimal("0.0015")
    assert estimate.estimated_total == Decimal("0.09975")
    assert estimate.cached_input_tokens == 200000
    assert estimate.cache_write_input_tokens == 7000
    assert extract_cached_input_tokens(event) == 200000
    assert extract_cache_write_input_tokens(event).total == 7000
