from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Any
import json
import mimetypes
import os
import re

import httpx


DEFAULT_BOKIO_API_BASE_URL = "https://api.bokio.se/v1"
MAX_BOKIO_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_BOKIO_ERROR_BODY_BYTES = 2 * 1024
_UPLOAD_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


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
    api_base_url = (
        str(
            os.environ.get("BOKIO_API_BASE_URL")
            or bokio_config.get("api_base_url")
            or DEFAULT_BOKIO_API_BASE_URL
        )
        .strip()
        .rstrip("/")
    )
    timeout_seconds = float(bokio_config.get("timeout_seconds") or 30.0)
    return BokioConnectionSettings(
        company_id=company_id,
        integration_token=integration_token,
        api_base_url=api_base_url,
        token_env=token_env,
        company_id_env=company_id_env,
        timeout_seconds=timeout_seconds,
    )


@dataclass(frozen=True)
class BokioDownloadedUpload:
    content: bytes
    filename: str
    mime_type: str | None


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

    def list_invoices(self, *, page: int = 1, page_size: int = 25, query: str | None = None) -> Any:
        return self.get("/invoices", params=_page_params(page, page_size, query))

    def create_invoice(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post("/invoices", json=payload)

    def list_supplier_invoices(
        self, *, page: int = 1, page_size: int = 25, query: str | None = None
    ) -> Any:
        return self.get("/supplier-invoices", params=_page_params(page, page_size, query))

    def create_supplier_invoice(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post("/supplier-invoices", json=payload)

    def list_uploads(self, *, page: int = 1, page_size: int = 25, query: str | None = None) -> Any:
        return self.get("/uploads", params=_page_params(page, page_size, query))

    def get_upload(self, upload_id: str) -> Any:
        return self.get(f"/uploads/{_opaque_upload_id(upload_id)}")

    def download_upload(
        self,
        upload_id: str,
        *,
        max_bytes: int | None = None,
    ) -> BokioDownloadedUpload:
        normalized_upload_id = _opaque_upload_id(upload_id)
        bounded_max_bytes = MAX_BOKIO_DOWNLOAD_BYTES if max_bytes is None else int(max_bytes)
        if bounded_max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        path = f"/uploads/{normalized_upload_id}/download"
        with httpx.Client(timeout=self.settings.timeout_seconds) as client:
            with client.stream(
                "GET",
                self._request_url(path),
                headers=self._request_headers(accept="application/octet-stream"),
            ) as response:
                if response.status_code != 200:
                    raise _download_response_error(response)
                content_length = _optional_content_length(response.headers.get("content-length"))
                if content_length is not None and content_length > bounded_max_bytes:
                    raise _download_too_large_error(
                        response,
                        byte_count=content_length,
                        max_bytes=bounded_max_bytes,
                    )
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > bounded_max_bytes:
                        raise _download_too_large_error(
                            response,
                            byte_count=len(content),
                            max_bytes=bounded_max_bytes,
                        )
                mime_type = _response_mime_type(response)
                filename = _response_filename(
                    response,
                    fallback=_download_fallback_filename(normalized_upload_id, mime_type),
                )
        return BokioDownloadedUpload(
            content=bytes(content),
            filename=filename,
            mime_type=mime_type,
        )

    def upload_document(
        self,
        *,
        file_path: str | Path,
        allowed_roots: Sequence[str | Path] | None,
        description: str | None = None,
        journal_entry_id: str | None = None,
    ) -> dict[str, Any]:
        path = _resolve_upload_path(file_path, allowed_roots=allowed_roots)
        content_type = _validated_upload_content_type(path)
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
        return self.request(
            "GET", f"/sie/{_required(fiscal_year_id, 'fiscal_year_id')}/download"
        ).content

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
        return self._json_response(self.request("POST", path, json=json, data=data, files=files))

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {self.settings.integration_token}"
        headers.setdefault("Accept", "application/json")
        with httpx.Client(timeout=self.settings.timeout_seconds) as client:
            response = client.request(
                method,
                self._request_url(path),
                headers=headers,
                **kwargs,
            )
        if response.status_code >= 400:
            raise self._api_error(response)
        return response

    def _request_url(self, path: str) -> str:
        return f"{self.settings.api_base_url}/companies/{self.settings.company_id}{path}"

    def _request_headers(self, *, accept: str = "application/json") -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.integration_token}",
            "Accept": accept,
        }

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


def _opaque_upload_id(value: str) -> str:
    normalized = _required(value, "upload_id")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", normalized):
        raise ValueError("upload_id must be an opaque Bokio upload ID")
    return normalized


def _optional_content_length(value: str | None) -> int | None:
    try:
        parsed = int(str(value or "").strip())
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _response_mime_type(response: httpx.Response) -> str | None:
    value = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    return value or None


def _response_filename(response: httpx.Response, *, fallback: str) -> str:
    disposition = str(response.headers.get("content-disposition") or "").strip()
    if disposition:
        message = Message()
        message["content-disposition"] = disposition
        filename = str(message.get_filename() or "").strip()
        if filename:
            return filename
    return fallback


def _download_fallback_filename(upload_id: str, mime_type: str | None) -> str:
    suffix = mimetypes.guess_extension(str(mime_type or "").strip()) or ".bin"
    return f"bokio-upload-{upload_id}{suffix}"


def _download_too_large_error(
    response: httpx.Response,
    *,
    byte_count: int,
    max_bytes: int,
) -> BokioApiError:
    return BokioApiError(
        f"Bokio upload exceeds the {max_bytes}-byte download limit",
        status_code=response.status_code,
        payload={"byte_count": byte_count, "max_bytes": max_bytes},
        plan_warning=response.headers.get("Bokio-Plan-Warning"),
    )


def _download_response_error(response: httpx.Response) -> BokioApiError:
    prefix = bytearray()
    for chunk in response.iter_bytes(chunk_size=MAX_BOKIO_ERROR_BODY_BYTES):
        remaining = MAX_BOKIO_ERROR_BODY_BYTES - len(prefix)
        prefix.extend(chunk[:remaining])
        if len(prefix) >= MAX_BOKIO_ERROR_BODY_BYTES:
            break
    text = bytes(prefix).decode("utf-8", errors="replace")
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError:
        payload = text
    message = (
        "Bokio API request failed"
        if response.status_code >= 400
        else f"Bokio upload download returned unexpected HTTP {response.status_code}"
    )
    if isinstance(payload, dict):
        message = str(payload.get("message") or payload.get("error") or message)
    return BokioApiError(
        message,
        status_code=response.status_code,
        payload=payload,
        plan_warning=response.headers.get("Bokio-Plan-Warning"),
    )


def _resolve_upload_path(
    file_path: str | Path,
    *,
    allowed_roots: Sequence[str | Path] | None,
) -> Path:
    if not allowed_roots:
        raise BokioConfigError("Bokio uploads require an explicit allowed upload directory")
    raw = str(file_path or "").strip()
    if not raw or "\x00" in raw:
        raise ValueError("file_path is required and must not contain NUL")
    input_path = Path(raw)
    checked_roots: list[str] = []
    for raw_root in allowed_roots:
        root = Path(raw_root).resolve(strict=True)
        checked_roots.append(str(root))
        if not root.is_dir():
            continue
        candidate = input_path if input_path.is_absolute() else root / input_path
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            continue
        if any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError("file_path must stay inside the Bokio upload staging directory")
        if _contains_symlink(root, relative):
            raise PermissionError("Bokio uploads refuse symlinks in the upload path")
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        if not resolved.is_file():
            raise FileNotFoundError(f"Bokio upload file not found: {input_path}")
        return resolved
    raise PermissionError(
        "Bokio upload file must be under an allowed upload staging directory: "
        + ", ".join(checked_roots)
    )


def _contains_symlink(root: Path, relative: Path) -> bool:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _validated_upload_content_type(path: Path) -> str:
    expected = _UPLOAD_CONTENT_TYPES.get(path.suffix.lower())
    if expected is None:
        raise ValueError("Bokio uploads support only PDF, JPEG, or PNG files")
    with path.open("rb") as handle:
        header = handle.read(16)
    if expected == "application/pdf" and not header.startswith(b"%PDF-"):
        raise ValueError("Bokio PDF upload content does not match the .pdf extension")
    if expected == "image/jpeg" and not header.startswith(b"\xff\xd8\xff"):
        raise ValueError("Bokio JPEG upload content does not match the .jpg/.jpeg extension")
    if expected == "image/png" and not header.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Bokio PNG upload content does not match the .png extension")
    guessed = mimetypes.guess_type(path.name)[0] or expected
    if guessed != expected:
        raise ValueError("Bokio upload content type is not allowed")
    return expected


def _query_params(*, query: str | None = None) -> dict[str, Any]:
    return {"query": query} if query else {}


def _page_params(page: int, page_size: int, query: str | None = None) -> dict[str, Any]:
    normalized_page = max(1, int(page or 1))
    normalized_page_size = min(100, max(1, int(page_size or 25)))
    params: dict[str, Any] = {"page": normalized_page, "pageSize": normalized_page_size}
    if query:
        params["query"] = query
    return params
