from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from evander.admin.web.reports import report_routes
from evander.model_usage.inference_report import InferenceCostReport
from evander.model_usage.models import ModelInferenceUsageEvent
from evander.model_usage.pricing import load_model_pricing_catalog_payload
from evander.model_usage.store import ModelUsageStore


def catalog(rate="1"):
    return load_model_pricing_catalog_payload(
        {
            "version": "test",
            "currency": "usd",
            "rules": [
                {
                    "id": provider,
                    "provider": provider,
                    "models": ["test-model"],
                    "rates_per_1m_tokens": {"input": rate, "output": "2"},
                }
                for provider in ("openai", "anthropic")
            ],
        }
    )


def event(identity, **changes):
    values = dict(
        event_id=identity,
        occurred_at=datetime(2026, 3, 29, 0, tzinfo=UTC),
        provider="openai",
        provider_endpoint_identifier="responses",
        model="test-model",
        session_id="session-1",
        directory_path="root/a",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        request_status="success",
        normalized_usage_status="available",
        cost_category="Development",
        api_key_name="PRIMARY_KEY",
        billing_kind="api",
        cost_attribution_recorded=True,
    )
    values.update(changes)
    return ModelInferenceUsageEvent(**values)


def read(report, **kwargs):
    return report.read(
        start=datetime(2026, 3, 28, 23, tzinfo=UTC),
        end=datetime(2026, 3, 31, 22, tzinfo=UTC),
        timezone="Europe/Stockholm",
        catalog=catalog(),
        **kwargs,
    )


def test_cost_totals_reconcile_with_coverage_and_dst_calendar_days(tmp_path):
    store = ModelUsageStore(tmp_path / "model-usage.db")
    for item in [
        event("one"),
        event(
            "two",
            occurred_at=datetime(2026, 3, 29, 22, tzinfo=UTC),
            input_tokens=4_000_000,
            cost_category="Review",
        ),
        event(
            "reported",
            provider="openrouter",
            api_key_name="ROUTER_KEY",
            cost_category=None,
            raw_usage_metadata={"usage": {"cost": 5}},
        ),
        event("unpriced", model="unknown"),
        event("subscription", billing_kind="subscription"),
        event("local", billing_kind="local"),
        event("unknown", billing_kind="unknown"),
        event("sibling", directory_path="root/ab"),
    ]:
        store.upsert_event(item)
    report = read(InferenceCostReport(tmp_path, store), directory_path="root/a")
    assert report["total_cost"] == 14
    assert report["estimated_cost"] == 9
    assert report["reported_cost"] == 5
    assert [day["date"] for day in report["daily"]] == ["2026-03-29", "2026-03-30", "2026-03-31"]
    assert [day["cost"] for day in report["daily"]] == [8, 6, 0]
    assert [day["cumulative_cost"] for day in report["daily"]] == [8, 14, 14]
    assert sum(group["cost"] or 0 for group in report["groups"]) == 14
    assert sum(part["cost"] for day in report["daily"] for part in day["categories"]) == 14
    assert sum(account["cumulative_cost"] for account in report["daily"][-1]["accounts"]) == 14
    assert report["coverage"] == {
        "priced_events": 3,
        "unpriced_events": 1,
        "excluded_subscription_events": 1,
        "excluded_local_events": 1,
        "unknown_billing_events": 1,
        "partial": True,
    }


def test_mtd_uses_viewer_month_and_exclusive_end(tmp_path):
    store = ModelUsageStore(tmp_path / "model-usage.db")
    # In Stockholm April starts at 22:00 UTC on March 31.
    store.upsert_event(event("before", occurred_at=datetime(2026, 3, 31, 21, 59, tzinfo=UTC)))
    store.upsert_event(event("at-start", occurred_at=datetime(2026, 3, 31, 22, tzinfo=UTC)))
    store.upsert_event(event("at-end", occurred_at=datetime(2026, 4, 1, 0, tzinfo=UTC)))
    report = InferenceCostReport(tmp_path, store).read(
        timezone="Europe/Stockholm", catalog=catalog(), now=datetime(2026, 4, 1, 0, tzinfo=UTC)
    )
    assert report["period_start"] == "2026-03-31T22:00:00+00:00"
    assert report["total_cost"] == 3
    assert [day["date"] for day in report["daily"]] == ["2026-04-01"]


def test_incremental_transcript_replacement_correction_deletion_and_repricing(
    tmp_path, monkeypatch
):
    store = ModelUsageStore(tmp_path / "model-usage.db")
    relay = event("relay", provider="anthropic", request_id="request-1")
    store.upsert_event(relay)
    report = InferenceCostReport(tmp_path, store)
    assert read(report)["total_cost"] == 3
    from evander.model_usage.report_snapshot import CostReportSnapshot

    original_events = CostReportSnapshot.events
    monkeypatch.setattr(
        CostReportSnapshot,
        "events",
        lambda self: pytest.fail("ordinary reads must not rescan history"),
    )
    transcript = event(
        "transcript",
        provider="anthropic",
        request_id="request-1",
        input_tokens=2_000_000,
        cost_category=None,
        api_key_name=None,
        billing_kind=None,
        cost_attribution_recorded=False,
        raw_usage_metadata={"request_metadata": {"source": "claude_transcript"}},
    )
    # The importer uses this stable source marker.
    from evander.model_usage.cost_balances import TRANSCRIPT_USAGE_SOURCE

    transcript.raw_usage_metadata["request_metadata"]["source"] = TRANSCRIPT_USAGE_SOURCE
    store.upsert_event(transcript)
    changed = read(report)
    assert changed["total_cost"] == 4
    assert changed["groups"][0]["category"] == "Development"
    assert changed["groups"][0]["api_key_name"] == "PRIMARY_KEY"
    store.upsert_event(transcript.model_copy(update={"output_tokens": 2_000_000}))
    assert read(report)["total_cost"] == 6
    with store._connect() as connection:
        connection.execute("DELETE FROM model_usage_events WHERE event_id='transcript'")
    assert read(report)["total_cost"] == 3
    monkeypatch.setattr(CostReportSnapshot, "events", original_events)
    repriced = report.read(
        start=datetime(2026, 3, 1, tzinfo=UTC),
        end=datetime(2026, 4, 1, tzinfo=UTC),
        catalog=catalog("5"),
    )
    assert repriced["total_cost"] == 7  # Same catalog version, different prices.


def test_report_snapshot_does_not_block_or_mix_concurrent_source_writes(tmp_path, monkeypatch):
    store = ModelUsageStore(tmp_path / "model-usage.db")
    store.upsert_event(event("one"))
    report = InferenceCostReport(tmp_path, store)
    from evander.model_usage import inference_report

    original = inference_report.estimate_model_usage_cost
    written = False

    def price(*args, **kwargs):
        nonlocal written
        if not written:
            written = True
            store.upsert_event(event("two"))
        return original(*args, **kwargs)

    monkeypatch.setattr(inference_report, "estimate_model_usage_cost", price)
    assert read(report)["total_cost"] == 3
    assert read(report)["total_cost"] == 6


def test_legacy_uncategorized_and_unknown_key_are_not_guessed_from_current_sessions(tmp_path):
    store = ModelUsageStore(tmp_path / "model-usage.db")
    store.upsert_event(
        event(
            "old",
            cost_category=None,
            api_key_name=None,
            billing_kind=None,
            cost_attribution_recorded=False,
        )
    )
    result = read(InferenceCostReport(tmp_path, store))
    assert result["total_cost"] == 3
    assert result["groups"][0]["category"] is None
    assert result["groups"][0]["api_key_name"] is None


def test_usage_update_cannot_erase_or_relabel_recorded_attribution(tmp_path):
    store = ModelUsageStore(tmp_path / "model-usage.db")
    store.upsert_event(event("one", cost_category=None))
    store.upsert_event(
        event("one", cost_category="Changed", api_key_name="ROTATED", input_tokens=2_000_000)
    )
    stored = store.list_events()[0]
    assert stored.cost_category is None
    assert stored.api_key_name == "PRIMARY_KEY"
    assert stored.input_tokens == 2_000_000


def test_transcript_keeps_request_attribution_after_relay_retention(tmp_path):
    from evander.model_usage.cost_balances import TRANSCRIPT_USAGE_SOURCE

    store = ModelUsageStore(tmp_path / "model-usage.db")
    store.upsert_event(event("relay", provider="anthropic", request_id="request-1"))
    store.upsert_event(
        event(
            "transcript",
            provider="anthropic",
            request_id="request-1",
            cost_category=None,
            api_key_name=None,
            billing_kind=None,
            cost_attribution_recorded=False,
            raw_usage_metadata={"request_metadata": {"source": TRANSCRIPT_USAGE_SOURCE}},
        )
    )
    with store._connect() as connection:
        connection.execute("DELETE FROM model_usage_events WHERE event_id='relay'")
    result = read(InferenceCostReport(tmp_path, store))
    assert result["total_cost"] == 3
    assert result["groups"][0]["category"] == "Development"
    assert result["groups"][0]["api_key_name"] == "PRIMARY_KEY"


def test_cost_routes_authorize_scope_and_interpret_local_calendar_dates():
    calls = []

    class Auth:
        def require_permission(self, request, permission, *, directory_path):
            assert permission == "workflow_runs.read"
            if directory_path != "root/a":
                raise PermissionError("Directory is not readable")

    service = SimpleNamespace(
        model_usage_admin=SimpleNamespace(
            read_inference_cost_report=lambda **kwargs: calls.append(kwargs) or {"total_cost": 3},
            inference_cost_categories=lambda **kwargs: ["Review"],
        )
    )
    with TestClient(Starlette(routes=report_routes(service, Auth()))) as client:
        assert client.get("/api/admin/reports/inference-costs").status_code == 403
        assert (
            client.get("/api/admin/inference-cost-categories?directory_path=root/b").status_code
            == 403
        )
        response = client.get(
            "/api/admin/reports/inference-costs?directory_path=root/a&timezone=Europe/Stockholm&start=2026-03-29&end=2026-03-30"
        )
        assert response.status_code == 200
        assert calls[0]["start"] == datetime(2026, 3, 28, 23, tzinfo=UTC)
        assert calls[0]["end"] == datetime(2026, 3, 29, 22, tzinfo=UTC)
        assert (
            client.get(
                "/api/admin/reports/inference-costs?directory_path=root/a&timezone=Bad/Zone&start=2026-03-29"
            ).status_code
            == 400
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timezone": "Bad/Zone"},
        {"start": datetime(2026, 4, 2, tzinfo=UTC), "end": datetime(2026, 4, 1, tzinfo=UTC)},
    ],
)
def test_report_rejects_invalid_periods(tmp_path, kwargs):
    with pytest.raises(ValueError):
        InferenceCostReport(tmp_path, ModelUsageStore(tmp_path / "usage.db")).read(**kwargs)
