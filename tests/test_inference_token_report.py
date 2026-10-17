from datetime import UTC, datetime

import pytest

from evander.model_usage.cost_balances import TRANSCRIPT_USAGE_SOURCE
from evander.model_usage.models import ModelInferenceUsageEvent
from evander.model_usage.store import ModelUsageStore
from evander.model_usage.token_report import InferenceTokenReport


def usage(identity, **changes):
    values = dict(
        event_id=identity,
        occurred_at=datetime(2026, 3, 29, tzinfo=UTC),
        provider="openai",
        provider_endpoint_identifier="responses",
        directory_path="root/a",
        session_id="session",
        cost_category="Development",
        billing_kind="api",
        cost_attribution_recorded=True,
        input_tokens=40,
        output_tokens=10,
        normalized_usage_status="available",
        request_status="success",
    )
    values.update(changes)
    return ModelInferenceUsageEvent(**values)


def report(store, **changes):
    return InferenceTokenReport(store).read(
        start=datetime(2026, 3, 28, 23, tzinfo=UTC),
        end=datetime(2026, 3, 31, 22, tzinfo=UTC),
        timezone="Europe/Stockholm",
        directory_path="root/a",
        **changes,
    )


def test_tokens_include_unpriced_api_and_subscription_but_not_local_unknown_or_siblings(tmp_path):
    store = ModelUsageStore(tmp_path / "usage.db")
    for event in [
        usage("api", model="no-price"),
        usage(
            "subscription",
            billing_kind="subscription",
            cost_category="Research",
            total_tokens=200,
            occurred_at=datetime(2026, 3, 29, 22, tzinfo=UTC),
            directory_path="root/a/child",
        ),
        usage("local", billing_kind="local", total_tokens=900),
        usage("unknown", billing_kind="unknown", total_tokens=900),
        usage("sibling", directory_path="root/ab", total_tokens=900),
        usage("end", occurred_at=datetime(2026, 3, 31, 22, tzinfo=UTC), total_tokens=900),
        usage("missing", input_tokens=None, output_tokens=None),
        usage("incomplete", output_tokens=None),
        usage(
            "zero",
            provider="anthropic",
            input_tokens=None,
            output_tokens=None,
            total_tokens=0,
            raw_usage_metadata={"request_metadata": {"source": TRANSCRIPT_USAGE_SOURCE}},
        ),
    ]:
        store.upsert_event(event)
    result = report(store)
    assert result["total_tokens"] == 250
    assert result["coverage"] == {"available_events": 3, "unavailable_events": 2, "partial": True}
    assert [day["date"] for day in result["daily"]] == ["2026-03-29", "2026-03-30", "2026-03-31"]
    assert [day["total_tokens"] for day in result["daily"]] == [50, 200, 0]
    assert result["daily"][1]["categories"] == [{"category": "Research", "total_tokens": 200}]


def test_tokens_deduplicate_transcripts_preserve_attribution_and_observe_updates(
    tmp_path, monkeypatch
):
    store = ModelUsageStore(tmp_path / "usage.db")
    store.upsert_event(usage("relay", provider="anthropic", request_id="request-1"))
    transcript = usage(
        "transcript",
        provider="anthropic",
        request_id="request-1",
        input_tokens=80,
        cost_category=None,
        billing_kind=None,
        cost_attribution_recorded=False,
        raw_usage_metadata={"request_metadata": {"source": TRANSCRIPT_USAGE_SOURCE}},
    )
    store.upsert_event(transcript)
    from evander.model_usage.report_snapshot import CostReportSnapshot

    monkeypatch.setattr(
        CostReportSnapshot, "events", lambda self: pytest.fail("must use period index")
    )
    reader = InferenceTokenReport(store)
    args = dict(start=datetime(2026, 3, 1, tzinfo=UTC), end=datetime(2026, 4, 1, tzinfo=UTC))
    first = reader.read(**args)
    assert first["total_tokens"] == 90
    assert first["daily"][28]["categories"] == [{"category": "Development", "total_tokens": 90}]
    store.upsert_event(transcript.model_copy(update={"output_tokens": 20}))
    assert reader.read(**args)["total_tokens"] == 100
    with store._connect() as connection:
        connection.execute("DELETE FROM model_usage_events WHERE event_id='transcript'")
    assert reader.read(**args)["total_tokens"] == 50


def test_transcript_precedence_applies_across_report_boundaries(tmp_path):
    store = ModelUsageStore(tmp_path / "usage.db")
    store.upsert_event(
        usage(
            "relay",
            provider="anthropic",
            request_id="request-1",
            occurred_at=datetime(2026, 3, 30, tzinfo=UTC),
        )
    )
    store.upsert_event(
        usage(
            "transcript",
            provider="anthropic",
            request_id="request-1",
            input_tokens=80,
            raw_usage_metadata={"request_metadata": {"source": TRANSCRIPT_USAGE_SOURCE}},
        )
    )
    reader = InferenceTokenReport(store)
    assert (
        reader.read(
            start=datetime(2026, 3, 30, tzinfo=UTC),
            end=datetime(2026, 3, 31, tzinfo=UTC),
        )["total_tokens"]
        == 0
    )
    assert (
        reader.read(
            start=datetime(2026, 3, 29, tzinfo=UTC),
            end=datetime(2026, 3, 30, tzinfo=UTC),
        )["total_tokens"]
        == 90
    )


def test_mtd_uses_local_month_and_counts_cache_and_reasoning_subsets_once(tmp_path):
    store = ModelUsageStore(tmp_path / "usage.db")
    store.upsert_event(usage("before", occurred_at=datetime(2026, 3, 31, 21, 59, tzinfo=UTC)))
    store.upsert_event(
        usage(
            "now",
            occurred_at=datetime(2026, 3, 31, 22, tzinfo=UTC),
            total_tokens=50,
            cost_category=None,
            raw_usage_metadata={
                "usage": {
                    "input_tokens_details": {"cached_tokens": 30},
                    "output_tokens_details": {"reasoning_tokens": 5},
                }
            },
        )
    )
    result = InferenceTokenReport(store).read(
        timezone="Europe/Stockholm",
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    assert result["period_start"] == "2026-03-31T22:00:00+00:00"
    assert result["total_tokens"] == 50
    assert result["daily"] == [
        {
            "date": "2026-04-01",
            "total_tokens": 50,
            "categories": [{"category": None, "total_tokens": 50}],
        }
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"timezone": "Bad/Zone"},
        {"start": datetime(2026, 4, 2, tzinfo=UTC), "end": datetime(2026, 4, 1, tzinfo=UTC)},
        {"start": datetime(2026, 4, 1)},
    ],
)
def test_tokens_reject_invalid_periods(tmp_path, changes):
    with pytest.raises(ValueError):
        InferenceTokenReport(ModelUsageStore(tmp_path / "usage.db")).read(**changes)
