from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "AdoWorkItemsQueryProvider",
    "IssueGetQueryProvider",
    "PullRequestFeedbackQueryProvider",
    "PullRequestListQueryProvider",
    "QueryProviderBase",
    "QueryRuntimeContext",
    "SemanticGetQueryProvider",
    "SemanticSearchQueryProvider",
    "SemanticStoreStatusQueryProvider",
    "StateGetQueryProvider",
    "VmRunStateQueryProvider",
    "SessionListQueryProvider",
]

_EXPORT_MODULES = {
    "AdoWorkItemsQueryProvider": ".ado_work_items",
    "IssueGetQueryProvider": ".issue_get",
    "PullRequestFeedbackQueryProvider": ".pull_request_feedback",
    "PullRequestListQueryProvider": ".pull_request_list",
    "QueryProviderBase": ".base",
    "QueryRuntimeContext": ".base",
    "SemanticGetQueryProvider": ".semantic_get",
    "SemanticSearchQueryProvider": ".semantic_search",
    "SemanticStoreStatusQueryProvider": ".semantic_store_status",
    "StateGetQueryProvider": ".state_get",
    "VmRunStateQueryProvider": ".vm_run_state",
    "SessionListQueryProvider": ".session_list",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
