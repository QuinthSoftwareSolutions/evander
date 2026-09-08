from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from evander.effects.binding import resolve_effect_config_bindings
from evander.events.provider_models import AdoWorkItemMatchingTriggerDefinition
from evander.issues.service import AzureDevOpsIssueAdapter, IssueService
from evander.triggers.providers.ado_work_item_matching import (
    AdoWorkItemMatchingTriggerProvider,
)
from evander.triggers.providers.base import TriggerRuntimeContext
from evander_team_addon.provider_schemas import TeamCreateOperatorDirectiveEffectDefinition


def _mock_ado_work_item(
    monkeypatch: pytest.MonkeyPatch, content_fields: dict[str, object]
) -> tuple[dict[str, object], list[httpx.Request]]:
    fields = {
        "System.Title": "Display alarms",
        "System.State": "Committed",
        "System.ChangedDate": "2026-09-07T14:48:00Z",
        **content_fields,
    }
    raw_item = {
        "id": 101,
        "rev": 7,
        "url": "https://dev.azure.com/example/_apis/wit/workitems/101",
        "fields": fields,
    }
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path.endswith("/wiql"):
            return httpx.Response(200, json={"workItems": [{"id": 101}]})
        if request.method == "POST" and request.url.path.endswith("/workitemsbatch"):
            body = json.loads(request.content)
            assert body["ids"] == [101]
            selected = body["fields"]
            # Match ADO's field selection; fixtures must not supply unrequested fields.
            item = {**raw_item, "fields": {key: fields[key] for key in selected if key in fields}}
            return httpx.Response(200, json={"value": [item]})
        if request.method == "GET" and request.url.path.endswith("/workitems/101"):
            return httpx.Response(200, json=raw_item)
        raise AssertionError(f"Unexpected ADO request: {request.method} {request.url}")

    client_type = httpx.Client
    monkeypatch.setattr(
        "evander.issues.service.httpx.Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs),
    )
    monkeypatch.setattr(
        AzureDevOpsIssueAdapter,
        "_resolve_connection",
        lambda self, directory_path: ("https://dev.azure.com/example", "Project", "test-pat"),
    )
    return fields, requests


@pytest.mark.parametrize(
    ("content_fields", "description", "acceptance_criteria"),
    [
        pytest.param(
            {
                "System.Description": "<p>Display <strong>all</strong> alarms &amp; warnings.</p>",
                "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>Keep alarm ordering.</li></ul>",
            },
            "<p>Display <strong>all</strong> alarms &amp; warnings.</p>",
            "<ul><li>Keep alarm ordering.</li></ul>",
            id="html",
        ),
        pytest.param(
            {
                "System.Description": "## Alarms\n\nShow **all** alarms.",
                "Microsoft.VSTS.Common.AcceptanceCriteria": "- Keep order\n- Show timestamps",
            },
            "## Alarms\n\nShow **all** alarms.",
            "- Keep order\n- Show timestamps",
            id="markdown",
        ),
        pytest.param(
            {"System.Description": "Description only"}, "Description only", "", id="no-criteria"
        ),
        pytest.param(
            {"Microsoft.VSTS.Common.AcceptanceCriteria": "Criteria only"},
            "",
            "Criteria only",
            id="no-description",
        ),
        pytest.param({}, "", "", id="missing"),
        pytest.param(
            {"System.Description": None, "Microsoft.VSTS.Common.AcceptanceCriteria": None},
            "",
            "",
            id="null",
        ),
        pytest.param(
            {"System.Description": "", "Microsoft.VSTS.Common.AcceptanceCriteria": ""},
            "",
            "",
            id="empty",
        ),
    ],
)
def test_ado_query_detail_and_batch_reads_expose_content_fields(
    monkeypatch: pytest.MonkeyPatch,
    content_fields: dict[str, object],
    description: str,
    acceptance_criteria: str,
) -> None:
    fields, requests = _mock_ado_work_item(monkeypatch, content_fields)
    service = IssueService(config_resolver=object())
    scope = {"provider": "azure_devops", "directory_path": "root"}

    query = service.query_remote_backlog_items(**scope)["items"][0]
    detail = service.get_remote_backlog_item_by_id(
        **scope, backlog_item_id="101", include_comments=False
    )
    issue = service.read_remote_issue(**scope, remote_issue_id="101", include_comments=False)
    summary = service.read_remote_issues_batch(**scope, remote_issue_ids=["101"])["items"]["101"]

    for item in (query, detail, issue, summary):
        assert item["description"] == description
        assert item["acceptance_criteria"] == acceptance_criteria
        assert item["fields"] == fields
        assert item["id"] == "101"
        assert item["title"] == "Display alarms"
        assert item["state"] == "Committed"
    assert query["rev"] == detail["rev"] == issue["rev"] == 7
    for request in requests:
        if request.url.path.endswith("/workitemsbatch"):
            selected = json.loads(request.content)["fields"]
            assert "System.Description" in selected
            assert "Microsoft.VSTS.Common.AcceptanceCriteria" in selected


@pytest.mark.parametrize("has_content", [True, False])
def test_matching_trigger_query_content_resolves_operator_directive_bindings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, has_content: bool
) -> None:
    description = "<p>Display all alarms.</p>" if has_content else ""
    criteria = "<p>Keep alarm ordering.</p>" if has_content else ""
    content_fields = (
        {
            "System.Description": description,
            "Microsoft.VSTS.Common.AcceptanceCriteria": criteria,
        }
        if has_content
        else {}
    )
    fields, requests = _mock_ado_work_item(monkeypatch, content_fields)
    provider = AdoWorkItemMatchingTriggerProvider()
    provider.initialize(
        AdoWorkItemMatchingTriggerDefinition(
            id="ado-intake",
            display_name="ADO intake",
            type="trigger:ado:work-item-matching",
            states=["Committed"],
            tags_any=["automation"],
        ),
        TriggerRuntimeContext(
            provider_config={"runtime_dir": str(tmp_path)},
            provider_config_resolver=object(),
        ),
    )

    [event] = provider.poll()
    directive = TeamCreateOperatorDirectiveEffectDefinition(
        id="directive",
        display_name="Create operator directive",
        type="effect:team:create-operator-directive",
        team_id="team",
        title="Work item ${{ado-intake.work_item_id}}: ${{ado-intake.work_item.title}}",
        message=(
            "Description:\n${{ado-intake.work_item.description}}\n"
            "Acceptance criteria:\n${{ado-intake.work_item.acceptance_criteria}}"
        ),
    )
    resolved = resolve_effect_config_bindings(
        directive.model_dump(),
        {"ado-intake": event.payload},
        {"ado-intake": {"work_item_id", "work_item"}},
    ).resolved_config

    assert resolved["title"] == "Work item 101: Display alarms"
    assert resolved["message"] == f"Description:\n{description}\nAcceptance criteria:\n{criteria}"
    assert event.payload["work_item"]["fields"] == fields
    assert event.payload["changed_at"] == "2026-09-07T14:48:00Z"
    assert [request.url.path.rsplit("/", 1)[-1] for request in requests] == [
        "wiql",
        "workitemsbatch",
    ]
