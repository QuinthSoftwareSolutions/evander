from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from evander.effects.providers.base import EffectProviderBase
from evander.events.models import WorkflowNodeEnvelope
from evander.models import digest_payload
from evander.queries.providers.base import QueryProviderBase
from evander.workflow.catalog import (
    WorkflowEditorFieldDefinition,
    WorkflowEditorNodeCatalogEntry,
)
from evander.workflow.contracts import register_node_output_schema
from evander.workflow.plugins import ProviderPluginRegistration

from .catalog import catalog_payload
from .client import SkatteverketClient
from .mcp import SUBMIT_APPROVED_DRAFT_TOOL
from .provider_schemas import (
    SKATTEVERKET_OUTPUT_SCHEMAS,
    SkatteverketCatalogQueryDefinition,
    SkatteverketConnectionStatusQueryDefinition,
    SkatteverketCreateDraftEffectDefinition,
    SkatteverketReadQueryDefinition,
    SkatteverketRecordSubmissionResultEffectDefinition,
    SkatteverketRequestSubmissionApprovalEffectDefinition,
    SkatteverketSubmitApprovedDraftEffectDefinition,
    SkatteverketValidateDraftEffectDefinition,
)
from .repository import SkatteverketRepository, SkatteverketStateError


def _field(
    key: str,
    label: str,
    kind: str,
    *,
    required: bool = False,
    description: str,
    multiline: bool = False,
    json_value_type: str | None = None,
) -> WorkflowEditorFieldDefinition:
    return WorkflowEditorFieldDefinition(
        key=key,
        label=label,
        kind=kind,
        required=required,
        bindable=True,
        multiline=multiline,
        json_value_type=json_value_type,
        description=description,
    )


def _entry(
    kind: str,
    node_type: str,
    label: str,
    description: str,
    fields: list[WorkflowEditorFieldDefinition],
) -> WorkflowEditorNodeCatalogEntry:
    return WorkflowEditorNodeCatalogEntry(
        kind=kind,
        type=node_type,
        label=label,
        description=description,
        fields=fields,
    )


_CATALOG_ENTRIES = (
    _entry(
        "query",
        "query:skatteverket:catalog",
        "Skatteverket catalog",
        "List known Skatteverket APIs exposed by the add-on.",
        [],
    ),
    _entry(
        "query",
        "query:skatteverket:connection-status",
        "Skatteverket connection status",
        "Report host-side Skatteverket connection and secret readiness.",
        [],
    ),
    _entry(
        "query",
        "query:skatteverket:skattekonto",
        "Read Skattekonto",
        "Prepare a host-side tax account read through the configured Skatteverket adapter.",
        [
            _field(
                "subject_id",
                "Subject",
                "text",
                description="Optional organisation or principal identifier.",
            ),
            _field("period", "Period", "text", description="Optional reporting period."),
        ],
    ),
    _entry(
        "query",
        "query:skatteverket:kundhandelser",
        "Read Skatteverket events",
        "Prepare a host-side customer event read through the configured Skatteverket adapter.",
        [
            _field(
                "subject_id",
                "Subject",
                "text",
                description="Optional organisation or principal identifier.",
            ),
            _field("period", "Period", "text", description="Optional reporting period."),
        ],
    ),
    _entry(
        "effect",
        "effect:skatteverket:create-draft",
        "Create Skatteverket draft",
        "Create a draft filing payload in add-on-owned state.",
        [
            _field(
                "api_name",
                "API name",
                "text",
                required=True,
                description="Skatteverket API name such as momsdeklaration.",
            ),
            _field("period", "Period", "text", description="Reporting period for the draft."),
            _field(
                "subject_id", "Subject", "text", description="Organisation or principal identifier."
            ),
            _field(
                "payload",
                "Payload",
                "json",
                required=True,
                multiline=True,
                json_value_type="object",
                description="Draft JSON payload.",
            ),
            _field(
                "source_session_id",
                "Source session",
                "text",
                description="Optional session that prepared the draft.",
            ),
        ],
    ),
    _entry(
        "effect",
        "effect:skatteverket:validate-draft",
        "Validate Skatteverket draft",
        "Validate a stored draft before approval and submission.",
        [
            _field(
                "draft_id",
                "Draft ID",
                "text",
                required=True,
                description="Stored Skatteverket draft id.",
            )
        ],
    ),
    _entry(
        "effect",
        "effect:skatteverket:request-submission-approval",
        "Request Skatteverket submission approval",
        "Create an operator approval request for a validated draft revision when policy context is available.",
        [
            _field(
                "draft_id", "Draft ID", "text", required=True, description="Validated draft id."
            ),
            _field(
                "session_id",
                "Session ID",
                "text",
                description="Session that should own the approval request.",
            ),
        ],
    ),
    _entry(
        "effect",
        "effect:skatteverket:submit-approved-draft",
        "Submit approved Skatteverket draft",
        "Submit a draft after exact approval has been granted.",
        [
            _field(
                "draft_id", "Draft ID", "text", required=True, description="Validated draft id."
            ),
            _field(
                "approval_request_id",
                "Approval request",
                "text",
                required=True,
                description="Approved exact submission request id.",
            ),
        ],
    ),
    _entry(
        "effect",
        "effect:skatteverket:record-submission-result",
        "Record Skatteverket submission result",
        "Record an externally submitted Skatteverket response against a draft.",
        [
            _field(
                "draft_id", "Draft ID", "text", required=True, description="Validated draft id."
            ),
            _field(
                "approval_request_id",
                "Approval request",
                "text",
                required=True,
                description="Approval request used for the submission.",
            ),
            _field(
                "response",
                "Response",
                "json",
                multiline=True,
                json_value_type="object",
                description="Sanitized Skatteverket response payload.",
            ),
        ],
    ),
)


class SkatteverketCatalogQueryProvider(QueryProviderBase):
    @classmethod
    def validate_definition(cls, query_definition):
        if isinstance(query_definition, WorkflowNodeEnvelope):
            return _materialize(SkatteverketCatalogQueryDefinition, query_definition)
        return query_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        del resolved_config
        return catalog_payload()


class SkatteverketConnectionStatusQueryProvider(QueryProviderBase):
    @classmethod
    def validate_definition(cls, query_definition):
        if isinstance(query_definition, WorkflowNodeEnvelope):
            return _materialize(SkatteverketConnectionStatusQueryDefinition, query_definition)
        return query_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        del resolved_config
        repo = SkatteverketRepository(_runtime_dir(self.runtime_context))
        return SkatteverketClient(repo.default_connection()).connection_status()


class SkatteverketReadQueryProvider(QueryProviderBase):
    api_name = "skattekonto"

    @classmethod
    def validate_definition(cls, query_definition):
        if isinstance(query_definition, WorkflowNodeEnvelope):
            return _materialize(SkatteverketReadQueryDefinition, query_definition)
        return query_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(self.query_definition, "subject_id", "period")
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = SkatteverketRepository(_runtime_dir(self.runtime_context))
        return SkatteverketClient(repo.default_connection()).query_placeholder(
            self.api_name,
            {"subject_id": payload.get("subject_id"), "period": payload.get("period")},
        )


class SkatteverketCustomerEventsQueryProvider(SkatteverketReadQueryProvider):
    api_name = "kundhandelser"


class SkatteverketCreateDraftEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(SkatteverketCreateDraftEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "api_name",
            "payload",
            "period",
            "subject_id",
            "source_session_id",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        draft = SkatteverketRepository(_runtime_dir(self.runtime_context)).create_draft(
            api_name=str(payload.get("api_name") or ""),
            payload=_mapping(payload.get("payload")),
            period=_optional_text(payload.get("period")),
            subject_id=_optional_text(payload.get("subject_id")),
            source_session_id=_optional_text(payload.get("source_session_id")),
        )
        return draft


class SkatteverketValidateDraftEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(SkatteverketValidateDraftEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(self.effect_definition, "draft_id")
        if resolved_config:
            payload.update(dict(resolved_config))
        return SkatteverketRepository(_runtime_dir(self.runtime_context)).validate_draft(
            str(payload.get("draft_id") or "")
        )


class SkatteverketRequestSubmissionApprovalEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(
                SkatteverketRequestSubmissionApprovalEffectDefinition,
                effect_definition,
            )
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(self.effect_definition, "draft_id", "session_id")
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = SkatteverketRepository(_runtime_dir(self.runtime_context))
        draft = repo.get_draft(str(payload.get("draft_id") or ""))
        session_id = _optional_text(payload.get("session_id"))
        runtime_policy = _runtime_policy(self.runtime_context)
        if runtime_policy is None:
            return {
                "status": "policy_context_unavailable",
                "approval_request_id": None,
                "payload_digest": digest_payload(draft.approval_payload()),
                "draft": repo.get_draft_payload(draft.draft_id),
            }
        if not session_id:
            raise ValueError("session_id is required to create a Skatteverket approval request")
        approval = runtime_policy.create_approval(
            session_id,
            "skatteverket_submission",
            draft.approval_payload(),
            f"Submit Skatteverket draft {draft.draft_id} for {draft.api_name}",
            tool_name=SUBMIT_APPROVED_DRAFT_TOOL,
            intended_effect=(
                f"Submit Skatteverket {draft.api_name} draft {draft.draft_id} "
                f"at revision {draft.revision}."
            ),
        )
        return {
            "status": "pending_approval",
            "approval_request_id": approval.request_id,
            "payload_digest": digest_payload(draft.approval_payload()),
            "draft": repo.get_draft_payload(draft.draft_id),
        }


class SkatteverketSubmitApprovedDraftEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(SkatteverketSubmitApprovedDraftEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(self.effect_definition, "draft_id", "approval_request_id")
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = SkatteverketRepository(_runtime_dir(self.runtime_context))
        draft = repo.get_draft(str(payload.get("draft_id") or ""))
        approval_request_id = str(payload.get("approval_request_id") or "").strip()
        runtime_policy = _runtime_policy(self.runtime_context)
        if runtime_policy is None:
            return {
                "status": "policy_context_unavailable",
                "submission_id": None,
                "draft": repo.get_draft_payload(draft.draft_id),
            }
        approval = runtime_policy.require_approved(
            approval_request_id,
            draft.approval_payload(),
            consume=False,
        )
        if str(approval.tool_name or "").strip() != SUBMIT_APPROVED_DRAFT_TOOL:
            raise ValueError(
                f"approval_request_id {approval_request_id} is not for {SUBMIT_APPROVED_DRAFT_TOOL}"
            )
        approval = runtime_policy.require_approved(
            approval_request_id,
            draft.approval_payload(),
            consume=True,
        )
        idempotency_key = f"{draft.draft_id}:{draft.revision}:{approval_request_id}"
        response = SkatteverketClient(repo.default_connection()).submit_draft(
            draft,
            idempotency_key=idempotency_key,
        )
        submission = repo.record_submission(
            draft_id=draft.draft_id,
            approval_request_id=approval_request_id,
            response=response,
            idempotency_key=idempotency_key,
        )
        return {
            "status": submission["status"],
            "submission_id": submission["submission_id"],
            "submission": submission,
        }


class SkatteverketRecordSubmissionResultEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(
                SkatteverketRecordSubmissionResultEffectDefinition,
                effect_definition,
            )
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "draft_id",
            "approval_request_id",
            "response",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = SkatteverketRepository(_runtime_dir(self.runtime_context))
        try:
            submission = repo.record_submission(
                draft_id=str(payload.get("draft_id") or ""),
                approval_request_id=str(payload.get("approval_request_id") or ""),
                response=_mapping(payload.get("response")),
            )
        except SkatteverketStateError as exc:
            return {"status": "error", "submission_id": None, "error": str(exc)}
        return {
            "status": submission["status"],
            "submission_id": submission["submission_id"],
            "submission": submission,
        }


def register_providers() -> ProviderPluginRegistration:
    for node_type, outputs in SKATTEVERKET_OUTPUT_SCHEMAS.items():
        register_node_output_schema(node_type=node_type, outputs=outputs)
    return ProviderPluginRegistration(
        query_providers={
            "query:skatteverket:catalog": SkatteverketCatalogQueryProvider,
            "query:skatteverket:connection-status": SkatteverketConnectionStatusQueryProvider,
            "query:skatteverket:skattekonto": SkatteverketReadQueryProvider,
            "query:skatteverket:kundhandelser": SkatteverketCustomerEventsQueryProvider,
        },
        effect_providers={
            "effect:skatteverket:create-draft": SkatteverketCreateDraftEffectProvider,
            "effect:skatteverket:validate-draft": SkatteverketValidateDraftEffectProvider,
            "effect:skatteverket:request-submission-approval": (
                SkatteverketRequestSubmissionApprovalEffectProvider
            ),
            "effect:skatteverket:submit-approved-draft": (
                SkatteverketSubmitApprovedDraftEffectProvider
            ),
            "effect:skatteverket:record-submission-result": (
                SkatteverketRecordSubmissionResultEffectProvider
            ),
        },
        node_definition_resolvers={
            "query:skatteverket:catalog": (
                lambda node: _materialize(SkatteverketCatalogQueryDefinition, node)
            ),
            "query:skatteverket:connection-status": (
                lambda node: _materialize(SkatteverketConnectionStatusQueryDefinition, node)
            ),
            "query:skatteverket:skattekonto": (
                lambda node: _materialize(SkatteverketReadQueryDefinition, node)
            ),
            "query:skatteverket:kundhandelser": (
                lambda node: _materialize(SkatteverketReadQueryDefinition, node)
            ),
            "effect:skatteverket:create-draft": (
                lambda node: _materialize(SkatteverketCreateDraftEffectDefinition, node)
            ),
            "effect:skatteverket:validate-draft": (
                lambda node: _materialize(SkatteverketValidateDraftEffectDefinition, node)
            ),
            "effect:skatteverket:request-submission-approval": (
                lambda node: _materialize(
                    SkatteverketRequestSubmissionApprovalEffectDefinition, node
                )
            ),
            "effect:skatteverket:submit-approved-draft": (
                lambda node: _materialize(SkatteverketSubmitApprovedDraftEffectDefinition, node)
            ),
            "effect:skatteverket:record-submission-result": (
                lambda node: _materialize(SkatteverketRecordSubmissionResultEffectDefinition, node)
            ),
        },
        workflow_editor_catalog_entries=_CATALOG_ENTRIES,
    )


def _materialize(model_class: type[Any], node: WorkflowNodeEnvelope) -> Any:
    return model_class.model_validate({"type": node.type, **dict(node.config)})


def _definition_payload(definition: Any, *field_names: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field_name in field_names:
        if definition is not None and hasattr(definition, field_name):
            payload[field_name] = getattr(definition, field_name)
    return payload


def _runtime_dir(context: Any) -> Path:
    resolved = getattr(context, "resolved_config", None)
    runtime_dir = getattr(resolved, "runtime_dir", None)
    if runtime_dir is None:
        raise RuntimeError("Skatteverket add-on provider requires resolved_config.runtime_dir")
    return Path(runtime_dir)


def _runtime_policy(context: Any):
    policy = getattr(context, "policy", None)
    if policy is not None:
        return policy
    admin_service = getattr(context, "admin_service", None)
    admin_runtime = getattr(admin_service, "runtime", None)
    if admin_runtime is not None:
        policy = getattr(admin_runtime, "policy", None)
        if policy is not None:
            return policy
    return getattr(admin_service, "policy", None)


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("payload must be a JSON object")
    return dict(value)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
