from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.web.rbac import WORKFLOWS_WRITE_PERMISSION
from evander.admin.web.shared import AdminWebAuth, json_error
from evander.pr_merge_preflight import MergePreflightMode

_PR_ID_SPLIT_RE = re.compile(r"[\s,]+")
_SUPPORTED_PROVIDERS = {"ado", "azure-devops", "azure_devops"}


class PrIntegrationPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "azure_devops"
    repo: str
    pr_ids: list[str] = Field(default_factory=list)
    mode: MergePreflightMode
    directory_path: str = "root"
    repo_source: str | None = None
    organization_url: str | None = None
    project: str | None = None

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, value: object) -> str:
        text = cls._normalize_optional_text(value) or "azure_devops"
        normalized = text.lower()
        if normalized not in _SUPPORTED_PROVIDERS:
            raise ValueError(f"provider {text!r} is not supported")
        return "azure_devops"

    @field_validator("repo", mode="before")
    @classmethod
    def _normalize_repo(cls, value: object) -> str:
        text = cls._normalize_optional_text(value)
        if not text:
            raise ValueError("repo is required")
        return text

    @field_validator("directory_path", mode="before")
    @classmethod
    def _normalize_directory_path(cls, value: object) -> str:
        return cls._normalize_optional_text(value) or "root"

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: object) -> MergePreflightMode:
        text = cls._normalize_optional_text(value)
        if not text:
            raise ValueError("mode is required")
        normalized = text.lower()
        if normalized not in {"single", "pairwise", "ordered"}:
            raise ValueError("mode must be single, pairwise, or ordered")
        return normalized  # type: ignore[return-value]

    @field_validator("repo_source", "organization_url", "project", mode="before")
    @classmethod
    def _normalize_optional_text_field(cls, value: object) -> str | None:
        return cls._normalize_optional_text(value)

    @field_validator("pr_ids", mode="before")
    @classmethod
    def _normalize_pr_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            items = _PR_ID_SPLIT_RE.split(value.strip())
        elif isinstance(value, (list, tuple, set)):
            items = [str(item).strip() for item in value]
        else:
            items = [str(value).strip()]
        return [item for item in items if item]

    @model_validator(mode="after")
    def _validate_pr_count(self) -> "PrIntegrationPreflightRequest":
        count = len(self.pr_ids)
        if count == 0:
            raise ValueError("pr_ids must include at least one PR id")
        if self.mode == "single" and count != 1:
            raise ValueError("single mode requires exactly one PR id")
        if self.mode in {"pairwise", "ordered"} and count < 2:
            raise ValueError(f"{self.mode} mode requires at least two PR ids")
        return self

    @staticmethod
    def _normalize_optional_text(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


def _validation_error_text(exc: ValidationError) -> str:
    messages: list[str] = []
    for error in exc.errors():
        message = str(error.get("msg", "")).strip()
        if message:
            messages.append(message)
    return "; ".join(messages) or "Invalid PR integration preflight request"


def _response_payload(
    *,
    request_payload: PrIntegrationPreflightRequest,
    report: Any,
) -> dict[str, Any]:
    payload = report.model_dump(mode="json")
    payload.update(
        {
            "provider": request_payload.provider,
            "repo": request_payload.repo,
            "directory_path": request_payload.directory_path,
        }
    )
    return payload


def pr_integration_routes(service: AdminRuntimeComponents, auth: AdminWebAuth) -> list[Route]:
    async def run_pr_integration_preflight(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON")
        if not isinstance(body, dict):
            return json_error("Request body must be a JSON object")

        try:
            payload = PrIntegrationPreflightRequest.model_validate(body)
        except ValidationError as exc:
            return json_error(_validation_error_text(exc))

        try:
            auth.require(request)
            auth.require_permission(
                request,
                WORKFLOWS_WRITE_PERMISSION,
                directory_path=payload.directory_path,
            )
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)

        try:
            report = await run_in_threadpool(
                service.pr_integration_preflight_admin.run_pr_integration_preflight,
                provider=payload.provider,
                repo=payload.repo,
                pr_ids=payload.pr_ids,
                mode=payload.mode,
                directory_path=payload.directory_path,
                organization_url=payload.organization_url,
                project=payload.project,
                repo_source=payload.repo_source,
            )
        except ValueError as exc:
            return json_error(str(exc))

        return JSONResponse(_response_payload(request_payload=payload, report=report))

    return [
        Route("/api/admin/pr-integration-preflight", run_pr_integration_preflight, methods=["POST"]),
    ]


__all__ = ["pr_integration_routes"]
