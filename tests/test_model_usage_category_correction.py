from datetime import UTC, datetime, timedelta

import pytest

from evander.model_usage.inference_report import InferenceCostReport
from evander.model_usage.models import ModelInferenceUsageEvent
from evander.model_usage.pricing import load_model_pricing_catalog_payload
from evander.model_usage.store import ModelUsageStore


START = datetime(2026, 9, 1, tzinfo=UTC)
END = START + timedelta(days=1)


def usage(event_id, **changes):
    return ModelInferenceUsageEvent.model_validate(
        {
            "event_id": event_id,
            "occurred_at": START,
            "provider": "openai",
            "provider_endpoint_identifier": "responses",
            "model": "example-model",
            "input_tokens": 1_000_000,
            "output_tokens": 0,
            "request_status": "success",
            "normalized_usage_status": "available",
            "api_key_name": "EXAMPLE_API_KEY",
            "billing_kind": "api",
            "cost_attribution_recorded": True,
            **changes,
        }
    )


def correct(store, categories, expected, **bounds):
    return store.set_event_cost_categories(
        categories,
        expected_categories=expected,
        occurred_after=bounds.get("start", START),
        occurred_before=bounds.get("end", END),
    )


def test_operator_correction_refreshes_reports_without_changing_usage_or_ingestion(tmp_path):
    store = ModelUsageStore(tmp_path / "usage.db")
    original = usage("recorded")
    store.upsert_event(original)
    catalog = load_model_pricing_catalog_payload(
        {
            "version": "example",
            "currency": "usd",
            "rules": [
                {
                    "id": "example",
                    "provider": "openai",
                    "models": ["example-model"],
                    "rates_per_1m_tokens": {"input": "2", "output": "5"},
                }
            ],
        }
    )
    report = InferenceCostReport(tmp_path, store)

    def read():
        return report.read(start=START, end=END, catalog=catalog)

    assert read()["groups"][0]["category"] is None
    assert correct(store, {"recorded": " Development "}, {"recorded": None}) == 1
    corrected = store.get_event("recorded")
    assert corrected.model_dump(exclude={"cost_category"}) == original.model_dump(
        exclude={"cost_category"}
    )
    result = read()
    assert result["total_cost"] == 2
    assert [(g["category"], g["cost"]) for g in result["groups"]] == [("Development", 2)]
    assert result["daily"][0]["categories"] == [{"category": "Development", "cost": 2}]
    # A delayed ingestion update must retain the explicitly corrected category.
    store.upsert_event(original)
    assert store.get_event("recorded").cost_category == "Development"
    assert correct(store, {"recorded": None}, {"recorded": "Development"}) == 1
    assert read()["groups"][0]["category"] is None
    assert read()["total_cost"] == 2


@pytest.mark.parametrize("conflict", ["changed", "missing", "outside"])
def test_conflicting_batch_does_not_partially_categorize_events(tmp_path, conflict):
    store = ModelUsageStore(tmp_path / "usage.db")
    store.upsert_event(usage("first"))
    if conflict != "missing":
        store.upsert_event(
            usage(
                "second",
                cost_category="Review" if conflict == "changed" else None,
                occurred_at=END if conflict == "outside" else START,
            )
        )
    with pytest.raises(ValueError):
        correct(
            store,
            {"first": "Development", "second": "Development"},
            {"first": None, "second": None},
        )
    assert store.get_event("first").cost_category is None
    if conflict == "changed":
        assert store.get_event("second").cost_category == "Review"


@pytest.mark.parametrize(
    "categories,expected,bounds",
    [
        ({"event": "Review"}, {}, {}),
        ({"event": "Review"}, {"event": None}, {"start": START.replace(tzinfo=None)}),
        ({"event": "Review"}, {"event": None}, {"end": START}),
        ({"event": 123}, {"event": None}, {}),
    ],
)
def test_invalid_corrections_are_rejected_before_mutation(tmp_path, categories, expected, bounds):
    store = ModelUsageStore(tmp_path / "usage.db")
    store.upsert_event(usage("event"))
    with pytest.raises(ValueError):
        correct(store, categories, expected, **bounds)
    assert store.get_event("event").cost_category is None


def test_correction_batches_are_bounded(tmp_path):
    store = ModelUsageStore(tmp_path / "usage.db")
    categories = {str(i): "Development" for i in range(1001)}
    with pytest.raises(ValueError, match="1000"):
        correct(store, categories, dict.fromkeys(categories))
