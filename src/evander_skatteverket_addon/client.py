from __future__ import annotations

import os
from typing import Any

import httpx

from .catalog import CATALOG_BY_NAME, SUBMISSION_API_NAMES
from .repository import DraftSnapshot, SkatteverketStateError


class SkatteverketClient:
    """Small host-side adapter.

    The add-on owns bookkeeping workflow state. Real Skatteverket endpoint paths are supplied by
    the operator after API onboarding, because production partner APIs are granted per agreement.
    """

    def __init__(self, connection: dict[str, Any] | None = None) -> None:
        self.connection = connection or {}

    def connection_status(self) -> dict[str, Any]:
        if not self.connection:
            return {
                "configured": False,
                "ready": False,
                "reason": "No enabled Skatteverket connection is configured.",
            }
        missing_secret_refs = [
            str(self.connection[key])
            for key in (
                "client_id_env",
                "client_secret_env",
                "apigw_client_id_env",
                "apigw_client_secret_env",
                "api_key_env",
            )
            if self.connection.get(key) and not os.environ.get(str(self.connection[key]))
        ]
        return {
            "configured": True,
            "ready": not missing_secret_refs,
            "connection_id": self.connection.get("connection_id"),
            "environment": self.connection.get("environment"),
            "base_url_configured": bool(self.connection.get("base_url")),
            "submission_endpoint_configured": bool(self.connection.get("submission_endpoint")),
            "missing_secret_refs": missing_secret_refs,
            "enabled_api_names": list(self.connection.get("enabled_api_names") or []),
        }

    def query_placeholder(self, api_name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if api_name not in CATALOG_BY_NAME:
            raise SkatteverketStateError(f"unknown Skatteverket API: {api_name}")
        return {
            "status": "not_configured",
            "api_name": api_name,
            "request_payload": payload or {},
            "message": (
                "Skatteverket read adapter requires API-specific endpoint configuration after "
                "onboarding."
            ),
        }

    def submit_draft(self, draft: DraftSnapshot, *, idempotency_key: str) -> dict[str, Any]:
        if draft.api_name not in SUBMISSION_API_NAMES:
            return {
                "status": "not_implemented_for_v1",
                "api_name": draft.api_name,
                "message": f"Submission is not implemented for {draft.api_name}.",
            }
        endpoint = str(self.connection.get("submission_endpoint") or "").strip()
        if not endpoint:
            return {
                "status": "blocked",
                "api_name": draft.api_name,
                "message": (
                    "No Skatteverket submission endpoint is configured for this connection. "
                    "Complete API onboarding and configure the endpoint before production use."
                ),
            }
        headers = {
            "content-type": "application/json",
            "idempotency-key": idempotency_key,
        }
        api_key_env = str(self.connection.get("api_key_env") or "").strip()
        if api_key_env and os.environ.get(api_key_env):
            headers["x-api-key"] = os.environ[api_key_env]
        apigw_client_id_env = str(self.connection.get("apigw_client_id_env") or "").strip()
        if apigw_client_id_env and os.environ.get(apigw_client_id_env):
            headers["Client_Id"] = os.environ[apigw_client_id_env]
        apigw_client_secret_env = str(self.connection.get("apigw_client_secret_env") or "").strip()
        if apigw_client_secret_env and os.environ.get(apigw_client_secret_env):
            headers["Client_Secret"] = os.environ[apigw_client_secret_env]
        request_payload = {
            "api_name": draft.api_name,
            "period": draft.period,
            "subject_id": draft.subject_id,
            "payload": draft.payload,
        }
        try:
            with httpx.Client(timeout=30) as client:
                response = client.post(endpoint, json=request_payload, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return {
                "status": "error",
                "api_name": draft.api_name,
                "error": str(exc),
                "error_class": type(exc).__name__,
            }
        try:
            response_payload = response.json()
        except ValueError:
            response_payload = {"text": response.text}
        return {
            "status": "submitted",
            "api_name": draft.api_name,
            "http_status": response.status_code,
            "response": response_payload,
        }
