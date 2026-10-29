from __future__ import annotations

from types import SimpleNamespace

import pytest

from evander.events.provider_models import (
    PullRequestFeedbackQueryDefinition,
    PullRequestGetQueryDefinition,
    PullRequestWorkItemsQueryDefinition,
)
from evander.queries.providers.base import QueryRuntimeContext
from evander.queries.providers.pull_request_feedback import PullRequestFeedbackQueryProvider
from evander.queries.providers.pull_request_get import PullRequestGetQueryProvider
from evander.queries.providers.pull_request_work_items import PullRequestWorkItemsQueryProvider


@pytest.mark.parametrize(
    "provider_class,definition_class,node_type,expected_error",
    [
        (
            PullRequestGetQueryProvider,
            PullRequestGetQueryDefinition,
            "query:pull-request:get",
            "query:pull-request:get requires repo",
        ),
        (
            PullRequestFeedbackQueryProvider,
            PullRequestFeedbackQueryDefinition,
            "query:pull-request:feedback",
            "query:pull-request:feedback requires repo",
        ),
        (
            PullRequestWorkItemsQueryProvider,
            PullRequestWorkItemsQueryDefinition,
            "query:pull-request:work-items",
            "query:pull-request:work-items provider 'ADO' is not supported",
        ),
    ],
)
def test_pr_query_resolved_inputs_preserve_provider_case_and_validation_order(
    provider_class, definition_class, node_type, expected_error
) -> None:
    def unexpected_resolution(directory_path):
        pytest.fail("Invalid inputs must fail before directory configuration is resolved")

    provider = provider_class()
    provider.initialize(
        definition_class(
            id="pr-inputs",
            display_name="PR inputs",
            type=node_type,
            provider="ado",
            repo="repository",
            pr_id="17",
        ),
        QueryRuntimeContext(
            metadata={"directory_path": "root/team"},
            provider_config_resolver=SimpleNamespace(resolve_for_directory=unexpected_resolution),
        ),
    )

    with pytest.raises(ValueError) as error:
        provider.execute(resolved_config={"provider": " ADO ", "repo": " "})

    assert str(error.value) == expected_error


@pytest.mark.parametrize(
    "provider_class,definition_class,node_type",
    [
        (PullRequestGetQueryProvider, PullRequestGetQueryDefinition, "query:pull-request:get"),
        (
            PullRequestFeedbackQueryProvider,
            PullRequestFeedbackQueryDefinition,
            "query:pull-request:feedback",
        ),
        (
            PullRequestWorkItemsQueryProvider,
            PullRequestWorkItemsQueryDefinition,
            "query:pull-request:work-items",
        ),
    ],
)
def test_pr_query_execution_preserves_missing_context_error(
    provider_class, definition_class, node_type
) -> None:
    provider = provider_class()
    provider.query_definition = definition_class(
        id="pr-inputs",
        display_name="PR inputs",
        type=node_type,
        provider="azure-devops",
        repo="repository",
        pr_id="17",
    )

    with pytest.raises(RuntimeError) as error:
        provider.execute()

    assert str(error.value) == f"{provider_class.__name__} has no runtime context"
