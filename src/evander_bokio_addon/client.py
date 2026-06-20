from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import mimetypes
import os

import httpx


DEFAULT_BOKIO_API_BASE_URL = "https://api.bokio.se/v1"


class BokioConfigError(RuntimeError):
    """Raised when the Bokio add-on is missing host-side configuration."""


class BokioApiError(RuntimeError):
    """Raised for structured Bokio API failures."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Any | None = None,
        plan_warning: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.payload = payload
        self.plan_warning = plan_warning
        super().__init__(message)

    def to_payload(self) -> dict[str, Any]:
        return {
            "error": str(self),
            "status_code": self.status_code,
            "payload": self.payload,
            "plan_warning": self.plan_warning,
        }


@dataclass(frozen=True)
class BokioConnectionSettings:
    company_id: str
    integration_token: str
    api_base_url: str = DEFAULT_BOKIO_API_BASE_URL
    token_env: str = "BOKIO_INTEGRATION_TOKEN"
    company_id_env: str = "BOKIO_COMPANY_ID"
    timeout_seconds: float = 30.0

    def redacted_payload(self) -> dict[str, Any]:
        return {
            "company_id": self.company_id,
            "api_base_url": self.api_base_url,
            "token_env": self.token_env,
            "configured": bool(self.company_id and self.integration_token),
        }


def settings_from_context(context: Any | None = None) -> BokioConnectionSettings:
    config = getattr(context, "config", None)
    provider_state = getattr(config, "provider_state", {}) if config is not None else {}
    bokio_config = provider_state.get("bokio") if isinstance(provider_state, dict) else None
    if not isinstance(bokio_config, dict):
        bokio_config = {}
    token_env = str(bokio_config.get("integration_token_env") or "BOKIO_INTEGRATION_TOKEN").strip()
    company_id_env = str(bokio_config.get("company_id_env") or "BOKIO_COMPANY_ID").strip()
    company_id = str(
        os.environ.get(company_id_env)
        or bokio_config.get("company_id")
        or os.environ.get("BOKIO_COMPANY_ID")
        or ""
    ).strip()
    integration_token = str(os.environ.get(token_env) or "").strip()
    api_base_url = str(
        os.environ.get("BOKIO_API_BASE_URL")
        or bokio_config.get("api_base_url")
        or DEFAULT_BOKIO_API_BASE_URL
    ).strip().rstrip("/")
    timeout_seconds = float(bokio_config.get("timeout_seconds") or 30.0)
    return BokioConnectionSettings(
        company_id=company_id,
        integration_token=integration_token,
        api_base_url=api_base_url,
        token_env=token_env,
        company_id_env=company_id_env,
        timeout_seconds=timeout_seconds,
    )


class BokioClient:
    def __init__(self, settings: BokioConnectionSettings) -> None:
        self.settings = settings
        if not settings.company_id:
            raise BokioConfigError(
                f"Bokio company id is required; set {settings.company_id_env} or provider_state.bokio.company_id"
            )
        if not settings.integration_token:
            raise BokioConfigError(
                f"Bokio integration token is required in env var {settings.token_env}"
            )

    @classmethod
    def from_context(cls, context: Any | None = None) -> "BokioClient":
        return cls(settings_from_context(context))

    def get_company(self) -> dict[str, Any]:
        return self.get("/company-information")

    def list_accounts(self, *, query: str | None = None) -> Any:
        return self.get("/chart-of-accounts", params=_query_params(query=query))

    def list_journal_entries(
        self, *, page: int = 1, page_size: int = 25, query: str | None = None
    ) -> Any:
        return self.get("/journal-entries", params=_page_params(page, page_size, query))

    def create_journal_entry(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post("/journal-entries", json=payload)

    def list_invoices(
        self, *, page: int = 1, page_size: int = 25, query: str | None = None
    ) -> Any:
        return self.get("/invoices", params=_page_params(page, page_size, query))

    def create_invoice(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post("/invoices", json=payload)

    def list_supplier_invoices(
        self, *, page: int = 1, page_size: int = 25, query: str | None = None
    ) -> Any:
        return self.get("/supplier-invoices", params=_page_params(page, page_size, query))

    def create_supplier_invoice(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post("/supplier-invoices", json=payload)

    def list_uploads(
        self, *, page: int = 1, page_size: int = 25, query: str | None = None
    ) -> Any:
        return self.get("/uploads", params=_page_params(page, page_size, query))

    def upload_document(
        self,
        *,
        file_path: str | Path,
        description: str | None = None,
        journal_entry_id: str | None = None,
    ) -> dict[str, Any]:
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Bokio upload file not found: {path}")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data: dict[str, str] = {}
        if description:
            data["description"] = description
        if journal_entry_id:
            data["journalEntryId"] = journal_entry_id
        with path.open("rb") as handle:
            files = {"file": (path.name, handle, content_type)}
            return self.post("/uploads", data=data, files=files)

    def list_bank_payments(
        self, *, page: int = 1, page_size: int = 25, query: str | None = None
    ) -> Any:
        return self.get("/bank-payments", params=_page_params(page, page_size, query))

    def create_bank_payment(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.post("/bank-payments", json=payload)
        result.setdefault("bankid_signing_required", True)
        result.setdefault(
            "message",
            "Payment was created in Bokio; a signatory must still log in and sign with BankID.",
        )
        return result

    def download_sie(self, fiscal_year_id: str) -> bytes:
        return self.request("GET", f"/sie/{_required(fiscal_year_id, 'fiscal_year_id')}/download").content

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return self._json_response(self.request("GET", path, params=params))

    def post(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        files: dict[str, Any] | None = None,
    ) -> Any:
        return self._json_response(
            self.request("POST", path, json=json, data=data, files=files)
        )

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.settings.api_base_url}/companies/{self.settings.company_id}{path}"
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {self.settings.integration_token}"
        headers.setdefault("Accept", "application/json")
        with httpx.Client(timeout=self.settings.timeout_seconds) as client:
            response = client.request(method, url, headers=headers, **kwargs)
        if response.status_code >= 400:
            raise self._api_error(response)
        return response

    def _json_response(self, response: httpx.Response) -> Any:
        if response.status_code == 204 or not response.content:
            return {"status": "ok"}
        try:
            payload = response.json()
        except ValueError as exc:
            raise BokioApiError(
                "Bokio returned a non-JSON response",
                status_code=response.status_code,
                payload=response.text[:1000],
                plan_warning=response.headers.get("Bokio-Plan-Warning"),
            ) from exc
        if isinstance(payload, dict):
            warning = response.headers.get("Bokio-Plan-Warning")
            if warning:
                payload.setdefault("plan_warning", warning)
        return payload

    def _api_error(self, response: httpx.Response) -> BokioApiError:
        payload: Any
        try:
            payload = response.json()
        except ValueError:
            payload = response.text[:2000]
        message = "Bokio API request failed"
        if isinstance(payload, dict):
            message = str(payload.get("message") or payload.get("error") or message)
        return BokioApiError(
            message,
            status_code=response.status_code,
            payload=payload,
            plan_warning=response.headers.get("Bokio-Plan-Warning"),
        )


def _required(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _query_params(*, query: str | None = None) -> dict[str, Any]:
    return {"query": query} if query else {}


def _page_params(page: int, page_size: int, query: str | None = None) -> dict[str, Any]:
    normalized_page = max(1, int(page or 1))
    normalized_page_size = min(100, max(1, int(page_size or 25)))
    params: dict[str, Any] = {"page": normalized_page, "pageSize": normalized_page_size}
    if query:
        params["query"] = query
    return params
