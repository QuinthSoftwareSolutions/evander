"""The extracted work-item query retains the legacy result projection contract."""

from types import SimpleNamespace

import pytest

from evander.events.provider_models import AdoWorkItemsQueryDefinition
from evander.queries.providers.ado_work_items import AdoWorkItemsQueryProvider
from evander.queries.providers.base import QueryRuntimeContext
from evander_azure_devops_addon.queries import ado_work_items


class _DumpedResult:
    def model_dump(self):
        return {"items": [17]}


class _NonMappingDump:
    def __init__(self):
        self.items = [18]

    def model_dump(self):
        return [17]


class _MappingResult(dict):
    def model_dump(self):
        pytest.fail("mapping results must take precedence over model_dump")


@pytest.mark.parametrize(
    "result, expected",
    [
        (None, {}),
        (_MappingResult(items=[16]), {"items": [16]}),
        (_DumpedResult(), {"items": [17]}),
        (_NonMappingDump(), {"items": [18]}),
        (SimpleNamespace(items=[19]), {"items": [19]}),
        (42, {"result": 42}),
    ],
)
def test_legacy_work_item_query_preserves_result_shape(result, expected, monkeypatch):
    reader = SimpleNamespace(query_remote_backlog_items=lambda **_: result)
    monkeypatch.setattr(ado_work_items, "IssueService", lambda _: reader)
    provider = AdoWorkItemsQueryProvider()
    provider.initialize(
        AdoWorkItemsQueryDefinition(
            id="query", display_name="ADO", type="query:ado:work-items",
        ),
        QueryRuntimeContext(provider_config_resolver=object()),
    )

    actual = provider.execute()

    assert actual == expected
    if isinstance(result, dict):
        assert actual is not result
