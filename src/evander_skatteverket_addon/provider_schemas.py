from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

SKATTEVERKET_OUTPUT_SCHEMAS: dict[str, dict[str, dict[str, Any]]] = {
    "query:skatteverket:catalog": {
        "apis": {"type": "array"},
        "submission_api_names": {"type": "array"},
    },
    "query:skatteverket:connection-status": {
        "configured": {"type": "boolean"},
        "ready": {"type": "boolean"},
    },
    "query:skatteverket:skattekonto": {
        "status": {"type": "string"},
        "api_name": {"type": "string"},
    },
    "query:skatteverket:kundhandelser": {
        "status": {"type": "string"},
        "api_name": {"type": "string"},
    },
    "effect:skatteverket:create-draft": {
        "draft_id": {"type": "string"},
        "status": {"type": "string"},
        "revision": {"type": "integer"},
    },
    "effect:skatteverket:validate-draft": {
        "draft_id": {"type": "string"},
        "status": {"type": "string"},
        "revision": {"type": "integer"},
    },
    "effect:skatteverket:request-submission-approval": {
        "approval_request_id": {"type": ["string", "null"]},
        "status": {"type": "string"},
    },
    "effect:skatteverket:submit-approved-draft": {
        "submission_id": {"type": ["string", "null"]},
        "status": {"type": "string"},
    },
    "effect:skatteverket:record-submission-result": {
        "submission_id": {"type": ["string", "null"]},
        "status": {"type": "string"},
    },
}


class SkatteverketCatalogQueryDefinition(BaseModel):
    type: str = "query:skatteverket:catalog"


class SkatteverketConnectionStatusQueryDefinition(BaseModel):
    type: str = "query:skatteverket:connection-status"


class SkatteverketReadQueryDefinition(BaseModel):
    type: str
    subject_id: str | None = None
    period: str | None = None


class SkatteverketCreateDraftEffectDefinition(BaseModel):
    type: str = "effect:skatteverket:create-draft"
    api_name: str
    payload: dict[str, Any] = Field(default_factory=dict)
    period: str | None = None
    subject_id: str | None = None
    source_session_id: str | None = None


class SkatteverketValidateDraftEffectDefinition(BaseModel):
    type: str = "effect:skatteverket:validate-draft"
    draft_id: str


class SkatteverketRequestSubmissionApprovalEffectDefinition(BaseModel):
    type: str = "effect:skatteverket:request-submission-approval"
    draft_id: str
    session_id: str | None = None


class SkatteverketSubmitApprovedDraftEffectDefinition(BaseModel):
    type: str = "effect:skatteverket:submit-approved-draft"
    draft_id: str
    approval_request_id: str


class SkatteverketRecordSubmissionResultEffectDefinition(BaseModel):
    type: str = "effect:skatteverket:record-submission-result"
    draft_id: str
    approval_request_id: str
    response: dict[str, Any] = Field(default_factory=dict)
