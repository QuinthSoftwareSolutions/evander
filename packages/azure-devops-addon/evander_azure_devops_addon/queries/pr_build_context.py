"""Deterministic PR intake, revision and preparation-window checks; no I/O."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from evander.events.models import ConfigValue, WorkflowNodeEnvelope
from evander.queries.providers.base import QueryProviderBase
from evander.workflow.catalog import WorkflowEditorFieldDefinition, WorkflowEditorNodeCatalogEntry
from evander.workflow.contracts import register_node_output_schema
from evander.workflow.plugins import ProviderPluginRegistration

NODE_TYPE = "query:ado:pr-build-context"


class PrBuildContextDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    display_name: str
    type: Literal["query:ado:pr-build-context"]
    pull_request: ConfigValue
    tested_commit: ConfigValue | None = None
    intake_tags: ConfigValue = Field(default_factory=list)
    reviewer_ids: ConfigValue = Field(default_factory=list)
    preparation_deadline: ConfigValue | None = None
    outputs: dict[str, Any] | None = None


def materialize(node):
    return PrBuildContextDefinition.model_validate({
        "id": node.id, "display_name": node.display_name,
        "type": node.type, "outputs": node.outputs, **node.config,
    })


def _strings(value: Any, field: str) -> set[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a list of strings")
    return {item.strip().casefold() for item in value if item.strip()}


def _deadline(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("preparation_deadline must be an ISO timestamp with a timezone")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(UTC)
    except ValueError:
        raise ValueError("preparation_deadline must be an ISO timestamp with a timezone") from None


def evaluate_context(payload: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    pr = payload.get("pull_request")
    if not isinstance(pr, Mapping):
        raise ValueError("pull_request must be the raw PR object from query:pull-request:get")
    tags = _strings(payload.get("intake_tags", []), "intake_tags")
    reviewers = _strings(payload.get("reviewer_ids", []), "reviewer_ids")
    labels = pr.get("labels", [])
    assigned = pr.get("reviewers", [])
    labels = [] if labels is None else labels
    assigned = [] if assigned is None else assigned
    if any(not isinstance(items, list) or any(not isinstance(item, Mapping) for item in items)
           for items in (labels, assigned)):
        raise ValueError("PR labels and reviewers must be lists of objects")
    tagged = any(isinstance(item.get("name"), str) and item["name"].casefold() in tags for item in labels)
    requested = any(isinstance(item.get("id"), str) and item["id"].casefold() in reviewers for item in assigned)
    merge = pr.get("lastMergeCommit") or {}
    current = merge.get("commitId") if isinstance(merge, Mapping) else None
    tested = payload.get("tested_commit")
    current = current.strip() if isinstance(current, str) and current.strip() else None
    tested = tested.strip() if isinstance(tested, str) and tested.strip() else None
    revision = "unknown" if current is None or tested is None else (
        "current" if current.casefold() == tested.casefold() else "superseded"
    )
    # The first check creates one absolute deadline. Every scheduled retry must
    # carry this exact value; a worker delay/restart cannot renew the window.
    deadline = payload.get("preparation_deadline")
    expires = now + timedelta(minutes=15) if deadline is None else _deadline(deadline)
    remaining = max(0.0, (expires - now).total_seconds())
    return {
        "intake_eligible": tagged or requested,
        "revision_context": revision, "current_merge_sha": current,
        "preparation_deadline": expires.isoformat().replace("+00:00", "Z"),
        "within_preparation_window": remaining > 0,
        "retry_delay_seconds": min(60.0, remaining),
    }


class PrBuildContextQueryProvider(QueryProviderBase):
    @classmethod
    def validate_definition(cls, definition):
        return materialize(definition) if isinstance(definition, WorkflowNodeEnvelope) else definition

    def execute(self, *, resolved_config=None):
        payload = self.query_definition.model_dump()
        payload.update(resolved_config or {})
        return evaluate_context(payload, now=datetime.now(UTC))


def register_providers():
    register_node_output_schema(node_type=NODE_TYPE, outputs={
        "intake_eligible": {"type": "boolean"},
        "revision_context": {"type": "string"},
        "current_merge_sha": {"type": ["string", "null"]},
        "preparation_deadline": {"type": "string"},
        "within_preparation_window": {"type": "boolean"},
        "retry_delay_seconds": {"type": "number"},
    })
    return ProviderPluginRegistration(
        query_providers={NODE_TYPE: PrBuildContextQueryProvider},
        node_definition_resolvers={NODE_TYPE: materialize},
        workflow_editor_catalog_entries=(WorkflowEditorNodeCatalogEntry(
            kind="query", type=NODE_TYPE, label="Check PR build context",
            description="Check configured intake tags/reviewers, tested merge revision and an absolute 15-minute preparation deadline without network or state changes.",
            fields=[WorkflowEditorFieldDefinition(
                key=key, label=key.replace("_", " ").title(),
                kind="json" if key in {"pull_request", "intake_tags", "reviewer_ids"} else "text",
                required=key == "pull_request", bindable=True,
            ) for key in ("pull_request", "tested_commit", "intake_tags", "reviewer_ids", "preparation_deadline")],
        ),),
    )
