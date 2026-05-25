from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from agent_sandbox.queries.providers.schemas import (
    AdoWorkItemsQueryDefinition,
    AgentArtifactTextQueryDefinition,
    GitPrNoteQueryDefinition,
    IssueGetQueryDefinition,
    PullRequestFeedbackQueryDefinition,
    PullRequestGetQueryDefinition,
    PullRequestListQueryDefinition,
    SemanticGetQueryDefinition,
    SemanticSearchQueryDefinition,
    SemanticStoreStatusQueryDefinition,
    SessionContextQueryDefinition,
    SessionListQueryDefinition,
    VmRunStateQueryDefinition,
)
from agent_sandbox.schema_bootstrap import _QUERY_OUTPUT_SCHEMAS
from agent_sandbox.workflow.contracts import WorkflowNodeEnvelope, register_node_output_schema

_QUERY_MODEL_BY_TYPE = {
    "query:vm:run-state": VmRunStateQueryDefinition,
    "query:git:pr-note": GitPrNoteQueryDefinition,
    "query:session-context": SessionContextQueryDefinition,
    "query:session:list": SessionListQueryDefinition,
    "query:agent:artifact-text": AgentArtifactTextQueryDefinition,
    "query:issue:get": IssueGetQueryDefinition,
    "query:ado:work-items": AdoWorkItemsQueryDefinition,
    "query:pull-request:list": PullRequestListQueryDefinition,
    "query:pull-request:feedback": PullRequestFeedbackQueryDefinition,
    "query:pull-request:get": PullRequestGetQueryDefinition,
    "query:semantic:store-status": SemanticStoreStatusQueryDefinition,
    "query:semantic:search": SemanticSearchQueryDefinition,
    "query:semantic:get": SemanticGetQueryDefinition,
}


def _materialize_with_model(model_class: type[BaseModel], node: WorkflowNodeEnvelope) -> BaseModel:
    payload = {
        "id": node.id,
        "display_name": node.display_name,
        "type": node.type,
        "outputs": node.outputs,
        **dict(node.config),
    }
    return model_class.model_validate(payload)


def query_node_schema_resolvers() -> dict[str, Callable[[WorkflowNodeEnvelope], BaseModel]]:
    return {
        node_type: (lambda node, model=model: _materialize_with_model(model, node))
        for node_type, model in _QUERY_MODEL_BY_TYPE.items()
    }


def register_query_output_schemas() -> None:
    for node_type, outputs in _QUERY_OUTPUT_SCHEMAS.items():
        register_node_output_schema(node_type=node_type, outputs=outputs)
