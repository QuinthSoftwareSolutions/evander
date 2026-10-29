from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_RESTORE_TIMEOUT_SECONDS = 3600.0


class MssqlSeedSidecarClientError(RuntimeError):
    pass


class MssqlSeedSidecarClient:
    def __init__(
        self,
        *,
        base_url: str,
        token_env: str,
        timeout_seconds: float = DEFAULT_RESTORE_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.token_env = str(token_env or "").strip()
        self.timeout_seconds = float(timeout_seconds)
        self._client = client
        if not self.base_url:
            raise ValueError("base_url is required")
        if not self.token_env:
            raise ValueError("token_env is required")

    def restore_database(
        self,
        *,
        session_id: str,
        logical_name: str,
        database_name: str,
        login_name: str,
        backup_path: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/databases/restore",
            json={
                "session_id": session_id,
                "logical_name": logical_name,
                "database_name": database_name,
                "login_name": login_name,
                "backup_path": backup_path,
            },
        )

    def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        token = os.environ.get(self.token_env, "").strip()
        if not token:
            raise MssqlSeedSidecarClientError(
                f"Host environment variable {self.token_env} is not set"
            )
        client = self._client or httpx.Client(timeout=self.timeout_seconds)
        close_client = self._client is None
        try:
            try:
                response = client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers={"Authorization": f"Bearer {token}"},
                    json=json,
                )
            except httpx.HTTPError as exc:
                raise MssqlSeedSidecarClientError(str(exc)) from exc
            if response.status_code >= 400:
                message = _error_message(response)
                raise MssqlSeedSidecarClientError(
                    f"MSSQL seed sidecar returned HTTP {response.status_code}: {message}"
                )
            payload = response.json()
            if not isinstance(payload, dict):
                raise MssqlSeedSidecarClientError("MSSQL seed sidecar response was not an object")
            return payload
        finally:
            if close_client:
                client.close()


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip() or response.reason_phrase
    if isinstance(payload, dict):
        return str(payload.get("error") or payload)
    return str(payload)
