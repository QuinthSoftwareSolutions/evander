from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from evander.events.models import WorkflowNodeEnvelope
from evander.queries.providers.base import QueryRuntimeContext
from evander.workflow.contracts import get_node_output_schema_registry
from evander_azure_devops_addon.queries import pr_build_context as context
from evander_azure_devops_addon.registration import register_workflow_providers
from evander_azure_devops_addon.pull_requests import AzureDevOpsPrQueryProvider


class Clock(datetime):
    current = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        return cls.current


def provider(monkeypatch, **config):
    monkeypatch.setattr(context, "datetime", Clock)
    monkeypatch.setattr(httpx, "Client", lambda **_: pytest.fail("context query must not perform I/O"))
    registration = register_workflow_providers()
    definition = registration.node_definition_resolvers[context.NODE_TYPE](WorkflowNodeEnvelope(
        id="context", display_name="PR build context", type=context.NODE_TYPE,
        config={"pull_request": {"labels": [], "reviewers": [], "lastMergeCommit": {"commitId": "merge-current"}}, "tested_commit": "merge-current", "intake_tags": ["agent", "agent-pending"], "reviewer_ids": ["bot-id"], **config},
    ))
    query = registration.query_providers[context.NODE_TYPE]()
    query.initialize(definition, QueryRuntimeContext())
    return query


@pytest.mark.parametrize("pr,expected", [
    ({"labels": [{"name": "AGENT"}]}, True),
    ({"labels": [{"name": "agent-pending"}]}, True),
    ({"reviewers": [{"id": "BOT-ID", "isRequired": False}]}, True),
    ({"reviewers": [{"id": "bot-id", "isRequired": True}]}, True),
    ({"labels": [{"name": "ordinary"}], "reviewers": [{"id": "human-id"}]}, False),
    ({}, False),
])
def test_configured_tag_or_reviewer_eligibility_without_network(monkeypatch, pr, expected):
    result = provider(monkeypatch, pull_request=pr).execute()
    assert result["intake_eligible"] is expected


def test_absolute_deadline_survives_delayed_retries_and_restart(monkeypatch):
    Clock.current = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    query = provider(monkeypatch)
    initial = query.execute()
    assert initial["preparation_deadline"] == "2026-01-01T12:15:00Z"
    assert initial["within_preparation_window"] is True
    assert initial["retry_delay_seconds"] == 60
    Clock.current = datetime(2026, 1, 1, 12, 14, 35, tzinfo=UTC)
    near_deadline = provider(monkeypatch, preparation_deadline=initial["preparation_deadline"]).execute()
    assert near_deadline["retry_delay_seconds"] == 25
    for delay in (timedelta(minutes=15), timedelta(hours=2), timedelta(days=2)):
        Clock.current = datetime(2026, 1, 1, 12, 0, tzinfo=UTC) + delay
        restarted = provider(monkeypatch, preparation_deadline=initial["preparation_deadline"]).execute()
        assert restarted["preparation_deadline"] == "2026-01-01T12:15:00Z"
        assert restarted["within_preparation_window"] is False
        assert restarted["retry_delay_seconds"] == 0


@pytest.mark.parametrize("current,tested,expected", [
    ("abcd", "ABCD", "current"), ("new", "old", "superseded"),
    (None, "old", "unknown"), ("new", None, "unknown"), ("", "old", "unknown"),
])
def test_revision_compares_the_tested_merge_not_the_source_commit(monkeypatch, current, tested, expected):
    result = provider(monkeypatch, pull_request={"lastMergeCommit": {"commitId": current}, "lastMergeSourceCommit": {"commitId": "source-head"}}, tested_commit=tested).execute()
    assert result["revision_context"] == expected


@pytest.mark.parametrize("overrides", [
    {"pull_request": []}, {"pull_request": {"labels": False}},
    {"pull_request": {"reviewers": ["bot-id"]}}, {"intake_tags": "agent"},
    {"reviewer_ids": [123]}, {"preparation_deadline": "2026-01-01T12:15:00"},
    {"preparation_deadline": "invalid"},
])
def test_context_rejects_malformed_inputs_without_extending_the_window(monkeypatch, overrides):
    with pytest.raises(ValueError):
        provider(monkeypatch, **overrides).execute()


def test_context_registration_publishes_bindable_inputs_and_output_contract():
    registration = register_workflow_providers()
    entry = next(e for e in registration.workflow_editor_catalog_entries if e.type == context.NODE_TYPE)
    fields = {f.key: f for f in entry.fields}
    assert fields["pull_request"].required and fields["preparation_deadline"].bindable
    outputs = get_node_output_schema_registry()[context.NODE_TYPE]
    assert outputs["intake_eligible"] == {"type": "boolean"}
    assert outputs["preparation_deadline"] == {"type": "string"}
    assert outputs["within_preparation_window"] == {"type": "boolean"}
    assert outputs["retry_delay_seconds"] == {"type": "number"}


@pytest.mark.parametrize("include_labels", [False, True])
def test_raw_pr_labels_are_fetched_only_when_requested(monkeypatch, include_labels):
    original = httpx.Client
    calls = []

    def respond(request):
        calls.append(request)
        assert request.headers["authorization"] == "Basic OnRlc3QtcGF0"
        if request.url.path.endswith("/labels"):
            return httpx.Response(200, json={"value": [{"id": 3, "name": "agent"}]})
        return httpx.Response(200, json={"pullRequestId": 17, "status": "active", "reviewers": [{"id": "bot-id"}]})

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond)))
    pr = AzureDevOpsPrQueryProvider(organization_url="https://dev.azure.com/example", project="Sample", pat="test-pat").get_pull_request(repo="app", pr_id="17", include_labels=include_labels)
    if include_labels:
        assert pr.raw["labels"] == [{"name": "agent"}]
        assert str(calls[1].url) == "https://dev.azure.com/example/Sample/_apis/git/repositories/app/pullRequests/17/labels?api-version=7.1"
        assert len(calls) == 2
    else:
        assert "labels" not in pr.raw and len(calls) == 1
