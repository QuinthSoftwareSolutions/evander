from __future__ import annotations

from agent_sandbox.events.models import (
    GitPrNoteQueryDefinition,
    IssueGetQueryDefinition,
    SessionContextQueryDefinition,
    VmRunStateQueryDefinition,
    register_node_output_schema,
    register_node_schema,
)

_QUERY_MODEL_BY_TYPE = {
    "query:vm:run-state": VmRunStateQueryDefinition,
    "query:git:pr-note": GitPrNoteQueryDefinition,
    "query:session-context": SessionContextQueryDefinition,
    "query:issue:get": IssueGetQueryDefinition,
}

_QUERY_OUTPUT_SCHEMAS = {
    "query:vm:run-state": {"vm_name": {"type": "string"}, "is_running": {"type": "boolean"}},
    "query:git:pr-note": {"body": {"type": "string"}, "has_note": {"type": "boolean"}},
    "query:session-context": {"session_id": {"type": "string"}, "metadata": {"type": "object"}},
    "query:issue:get": {"issue_id": {"type": "string"}, "title": {"type": "string"}, "state": {"type": "string"}},
}


def register_query_node_schemas() -> None:
    for node_type, model in _QUERY_MODEL_BY_TYPE.items():
        register_node_schema(node_type=node_type, model_class=model)
    for node_type, outputs in _QUERY_OUTPUT_SCHEMAS.items():
        register_node_output_schema(node_type=node_type, outputs=outputs)
