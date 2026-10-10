from __future__ import annotations

from evander.code_index.providers import (
    CodeIndexStatusDefinition,
    CodeIndexSearchDefinition,
    CodeIndexNavigateDefinition,
)

from collections.abc import Callable

from pydantic import BaseModel

from evander.queries.providers.schemas import (
    ClassificationEvaluateQueryDefinition,
    AdoBuildsQueryDefinition,
    TextRegexQueryDefinition,
    AdoWorkItemsQueryDefinition,
    AgentArtifactTextQueryDefinition,
    ArtifactSpaceLatestQueryDefinition,
    ArtifactSpaceListQueryDefinition,
    CommunicationMessagesQueryDefinition,
    GitMergeBaseQueryDefinition,
    GitPrNoteQueryDefinition,
    IssueGetQueryDefinition,
    IssueListQueryDefinition,
    ModelUsageListQueryDefinition,
    PullRequestFeedbackQueryDefinition,
    PullRequestGetQueryDefinition,
    PullRequestListQueryDefinition,
    PullRequestWorkItemsQueryDefinition,
    RemoteIssuesQueryDefinition,
    StateGetQueryDefinition,
    SemanticGetQueryDefinition,
    SemanticSearchQueryDefinition,
    SemanticStoreStatusQueryDefinition,
    SessionContextQueryDefinition,
    SessionListQueryDefinition,
    VmRunStateQueryDefinition,
)
from evander.schema_bootstrap import _QUERY_OUTPUT_SCHEMAS
from evander.workflow.contracts import (
    materialize_node_model as _materialize_with_model,
    WorkflowNodeEnvelope,
    register_node_output_schema,
)

_QUERY_MODEL_BY_TYPE = {
    "query:classification:evaluate": ClassificationEvaluateQueryDefinition,
    "query:ado:builds": AdoBuildsQueryDefinition,
    "query:text:regex": TextRegexQueryDefinition,
    "query:code-index:status": CodeIndexStatusDefinition,
    "query:code-index:search": CodeIndexSearchDefinition,
    "query:code-index:navigate": CodeIndexNavigateDefinition,
    "query:vm:run-state": VmRunStateQueryDefinition,
    "query:artifact-space:list": ArtifactSpaceListQueryDefinition,
    "query:artifact-space:latest": ArtifactSpaceLatestQueryDefinition,
    "query:git:merge-base": GitMergeBaseQueryDefinition,
    "query:git:pr-note": GitPrNoteQueryDefinition,
    "query:session-context": SessionContextQueryDefinition,
    "query:session:list": SessionListQueryDefinition,
    "query:model-usage:list": ModelUsageListQueryDefinition,
    "query:agent:artifact-text": AgentArtifactTextQueryDefinition,
    "query:issue:get": IssueGetQueryDefinition,
    "query:issue:list": IssueListQueryDefinition,
    "query:ado:work-items": AdoWorkItemsQueryDefinition,
    "query:remote-issues": RemoteIssuesQueryDefinition,
    "query:pull-request:list": PullRequestListQueryDefinition,
    "query:pull-request:feedback": PullRequestFeedbackQueryDefinition,
    "query:communication:messages": CommunicationMessagesQueryDefinition,
    "query:pull-request:get": PullRequestGetQueryDefinition,
    "query:pull-request:work-items": PullRequestWorkItemsQueryDefinition,
    "query:state:get": StateGetQueryDefinition,
    "query:semantic:store-status": SemanticStoreStatusQueryDefinition,
    "query:semantic:search": SemanticSearchQueryDefinition,
    "query:semantic:get": SemanticGetQueryDefinition,
}


def query_node_schema_resolvers() -> dict[str, Callable[[WorkflowNodeEnvelope], BaseModel]]:
    return {
        node_type: (lambda node, model=model: _materialize_with_model(model, node))
        for node_type, model in _QUERY_MODEL_BY_TYPE.items()
    }


def register_query_output_schemas() -> None:
    for node_type, outputs in _QUERY_OUTPUT_SCHEMAS.items():
        register_node_output_schema(node_type=node_type, outputs=outputs)
