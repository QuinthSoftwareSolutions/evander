from __future__ import annotations

import logging
import os
import random
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from evander.worker.models import (
    WorkerFileWriteRequest,
    WorkerFileWriteRecord,
    WorkerGitWriteKeyRecord,
    WorkerGitWriteKeyRequest,
    WorkerGitKeyDeleteRecord,
    WorkerJobRecord,
    WorkerJobRequest,
    WorkerMssqlDatabaseDeleteRecord,
    WorkerMssqlDatabaseListRecord,
    WorkerMssqlDatabaseRecord,
    WorkerMssqlDatabaseRequest,
    WorkerRepoCloneRecord,
    WorkerRepoCloneRequest,
    WorkerSessionPrepareRequest,
    WorkerSessionRecord,
    WorkerTaskWorktreeCleanupRecord,
    WorkerTaskWorktreeCleanupRequest,
    WorkerTaskWorktreeRecord,
    WorkerTaskWorktreeRequest,
    WorkerTelemetryPayload,
)
from evander.worker.security import sanitize_metadata, sanitize_text

logger = logging.getLogger(__name__)


class ContainerWorkerClientError(RuntimeError):
    def __init__(self, message: str, *, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = dict(payload) if payload is not None else None


class ContainerWorkerClient:
    def __init__(
        self,
        *,
        base_url: str,
        token_env: str = "EVANDER_WORKER_TOKEN",
        timeout_seconds: float = 30.0,
        max_connect_attempts: int = 5,
        base_connect_backoff_seconds: float = 1.0,
        max_connect_backoff_seconds: float = 8.0,
        sleep: Callable[[float], None] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        normalized_url = str(base_url or "").strip().rstrip("/")
        if not normalized_url:
            raise ValueError("container_worker_base_url is required for container_worker_vm.")
        if max_connect_attempts < 1:
            raise ValueError("max_connect_attempts must be at least 1.")
        if base_connect_backoff_seconds <= 0:
            raise ValueError("base_connect_backoff_seconds must be greater than 0.")
        if max_connect_backoff_seconds <= 0:
            raise ValueError("max_connect_backoff_seconds must be greater than 0.")
        self.base_url = normalized_url
        self.token_env = token_env
        self.timeout_seconds = timeout_seconds
        self.max_connect_attempts = max_connect_attempts
        self.base_connect_backoff_seconds = base_connect_backoff_seconds
        self.max_connect_backoff_seconds = max_connect_backoff_seconds
        self._sleep = sleep or time.sleep
        self._client = client

    def health(self) -> dict[str, Any]:
        response = self._request("GET", "/healthz", auth=False)
        return self._json_dict(response, operation="health")

    def codex_auth_status(self) -> dict[str, Any]:
        response = self._request("GET", "/v1/codex-auth")
        return self._json_dict(response, operation="read Codex auth status")

    def start_codex_device_login(self) -> dict[str, Any]:
        response = self._request("POST", "/v1/codex-auth/device-login")
        return self._json_dict(response, operation="start Codex device login")

    def codex_device_login_status(self, login_id: str) -> dict[str, Any]:
        response = self._request(
            "GET", f"/v1/codex-auth/device-login/{quote(str(login_id), safe='')}"
        )
        return self._json_dict(response, operation="read Codex device login")

    def cancel_codex_device_login(self, login_id: str) -> dict[str, Any]:
        response = self._request(
            "DELETE", f"/v1/codex-auth/device-login/{quote(str(login_id), safe='')}"
        )
        return self._json_dict(response, operation="cancel Codex device login")

    def logout_codex_auth(self) -> dict[str, Any]:
        response = self._request("DELETE", "/v1/codex-auth")
        return self._json_dict(response, operation="log out Codex account")

    def create(self, request: WorkerJobRequest) -> WorkerJobRecord:
        response = self._request(
            "POST",
            "/v1/jobs",
            json=request.model_dump(mode="json", exclude_none=True),
        )
        return WorkerJobRecord.model_validate(self._json_dict(response, operation="create job"))

    def prepare_session(self, request: WorkerSessionPrepareRequest) -> WorkerSessionRecord:
        response = self._request(
            "POST",
            "/v1/sessions",
            json=request.model_dump(mode="json", exclude_none=True),
        )
        return WorkerSessionRecord.model_validate(
            self._json_dict(response, operation="prepare session")
        )

    def clone_repo(
        self,
        session_id: str,
        request: WorkerRepoCloneRequest,
    ) -> WorkerRepoCloneRecord:
        response = self._request(
            "POST",
            f"/v1/sessions/{session_id}/repo",
            json=request.model_dump(mode="json", exclude_none=True),
        )
        return WorkerRepoCloneRecord.model_validate(
            self._json_dict(response, operation="clone repo")
        )

    def prepare_task_worktree(
        self,
        session_id: str,
        request: WorkerTaskWorktreeRequest,
    ) -> WorkerTaskWorktreeRecord:
        response = self._request(
            "POST",
            f"/v1/sessions/{session_id}/worktrees",
            json=request.model_dump(mode="json", exclude_none=True),
        )
        return WorkerTaskWorktreeRecord.model_validate(
            self._json_dict(response, operation="prepare task worktree")
        )

    def cleanup_task_worktree(
        self,
        session_id: str,
        request: WorkerTaskWorktreeCleanupRequest,
    ) -> WorkerTaskWorktreeCleanupRecord:
        response = self._request(
            "POST",
            f"/v1/sessions/{session_id}/worktrees/cleanup",
            json=request.model_dump(mode="json", exclude_none=True),
        )
        return WorkerTaskWorktreeCleanupRecord.model_validate(
            self._json_dict(response, operation="clean up task worktree")
        )

    def provision_git_write_key(
        self,
        session_id: str,
        request: WorkerGitWriteKeyRequest,
    ) -> WorkerGitWriteKeyRecord:
        response = self._request(
            "POST",
            f"/v1/sessions/{session_id}/git/write-key",
            json=request.model_dump(mode="json", exclude_none=True),
        )
        return WorkerGitWriteKeyRecord.model_validate(
            self._json_dict(response, operation="provision git write key")
        )

    def revoke_git_key(self, session_id: str, key_name: str) -> WorkerGitKeyDeleteRecord:
        response = self._request(
            "DELETE",
            f"/v1/sessions/{quote(session_id, safe='')}/git/keys/{quote(key_name, safe='')}",
        )
        return WorkerGitKeyDeleteRecord.model_validate(
            self._json_dict(response, operation="revoke git key")
        )

    def write_files(
        self,
        session_id: str,
        request: WorkerFileWriteRequest,
    ) -> WorkerFileWriteRecord:
        response = self._request(
            "POST",
            f"/v1/sessions/{session_id}/files",
            json=request.model_dump(mode="json", exclude_none=True),
        )
        return WorkerFileWriteRecord.model_validate(
            self._json_dict(response, operation="write files")
        )

    def read_session_artifact_text(self, session_id: str, relative_path: str) -> str:
        encoded_path = quote(str(relative_path or "").strip(), safe="/")
        response = self._request(
            "GET",
            f"/v1/sessions/{session_id}/artifacts/{encoded_path}",
        )
        return response.text

    def provision_mssql_database(
        self,
        session_id: str,
        request: WorkerMssqlDatabaseRequest,
    ) -> WorkerMssqlDatabaseRecord:
        response = self._request(
            "POST",
            f"/v1/sessions/{session_id}/mssql/databases",
            json=request.model_dump(mode="json", exclude_none=True),
        )
        return WorkerMssqlDatabaseRecord.model_validate(
            self._json_dict(response, operation="provision mssql database")
        )

    def list_mssql_databases(self, session_id: str) -> WorkerMssqlDatabaseListRecord:
        response = self._request("GET", f"/v1/sessions/{session_id}/mssql/databases")
        return WorkerMssqlDatabaseListRecord.model_validate(
            self._json_dict(response, operation="list mssql databases")
        )

    def drop_mssql_database(
        self,
        session_id: str,
        logical_name: str,
    ) -> WorkerMssqlDatabaseDeleteRecord:
        response = self._request(
            "DELETE",
            f"/v1/sessions/{session_id}/mssql/databases/{logical_name}",
        )
        return WorkerMssqlDatabaseDeleteRecord.model_validate(
            self._json_dict(response, operation="drop mssql database")
        )

    def get(self, job_id: str) -> WorkerJobRecord:
        response = self._request("GET", f"/v1/jobs/{job_id}")
        return WorkerJobRecord.model_validate(self._json_dict(response, operation="get job"))

    def stop(self, job_id: str) -> WorkerJobRecord:
        response = self._request("POST", f"/v1/jobs/{job_id}/stop")
        return WorkerJobRecord.model_validate(self._json_dict(response, operation="stop job"))

    def delete(self, job_id: str) -> dict[str, Any]:
        response = self._request("DELETE", f"/v1/jobs/{job_id}")
        return self._json_dict(response, operation="delete job")

    def delete_session(self, session_id: str) -> dict[str, Any]:
        response = self._request("DELETE", f"/v1/sessions/{session_id}")
        return self._json_dict(response, operation="delete session")

    def telemetry(self, *, refresh: bool = False) -> WorkerTelemetryPayload:
        kwargs: dict[str, Any] = {}
        if refresh:
            kwargs["params"] = {"refresh": "1"}
        response = self._request("GET", "/v1/telemetry", **kwargs)
        return WorkerTelemetryPayload.model_validate(
            self._json_dict(response, operation="telemetry")
        )

    def logs(
        self,
        job_id: str,
        *,
        mode: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        query: str | None = None,
    ) -> str:
        text, _metadata = self.logs_page(
            job_id,
            mode=mode,
            limit=limit,
            offset=offset,
            query=query,
        )
        return text

    def logs_page(
        self,
        job_id: str,
        *,
        mode: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        query: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        params: dict[str, str] = {}
        if mode:
            params["mode"] = mode
        if limit is not None:
            params["limit"] = str(limit)
        if offset is not None:
            params["offset"] = str(offset)
        if query:
            params["query"] = query
        response = self._request("GET", f"/v1/jobs/{job_id}/logs", params=params)
        metadata = {
            "paged": response.headers.get("x-evander-log-paged") == "true",
            "limit": _int_header(response, "x-evander-log-limit"),
            "offset": _int_header(response, "x-evander-log-offset"),
            "returned_lines": _int_header(response, "x-evander-log-returned-lines"),
            "total_lines": _int_header(response, "x-evander-log-total-lines"),
            "start_line": _int_header(response, "x-evander-log-start-line"),
            "end_line": _int_header(response, "x-evander-log-end-line"),
            "has_more_before": response.headers.get("x-evander-log-has-more-before") == "true",
        }
        return response.text, metadata

    def _request(
        self, method: str, path: str, *, auth: bool = True, **kwargs: Any
    ) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        if auth:
            token = os.environ.get(self.token_env, "")
            if not token:
                raise ContainerWorkerClientError(
                    f"container worker bearer token is not configured in env var {self.token_env}"
                )
            headers["Authorization"] = f"Bearer {token}"
        method_normalized = method.upper()
        for attempt in range(1, self.max_connect_attempts + 1):
            try:
                response = self._send_once(method_normalized, path, headers=headers, **kwargs)
                break
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                if attempt >= self.max_connect_attempts:
                    raise ContainerWorkerClientError(
                        f"container worker {method_normalized} {path} failed after connect "
                        f"retries attempts={attempt}/{self.max_connect_attempts}: "
                        f"{sanitize_text(str(exc))}"
                    ) from exc
                sleep_seconds = self._connect_retry_sleep_seconds(attempt)
                logger.warning(
                    "container worker request connect failure; retrying method=%s path=%s "
                    "attempt=%d/%d sleep=%.3fs error=%s",
                    method_normalized,
                    path,
                    attempt,
                    self.max_connect_attempts,
                    sleep_seconds,
                    sanitize_text(str(exc)),
                )
                self._sleep(sleep_seconds)
            except httpx.HTTPError as exc:
                raise ContainerWorkerClientError(
                    f"container worker {method_normalized} {path} failed: {sanitize_text(str(exc))}"
                ) from exc
        else:  # pragma: no cover - loop always returns or raises.
            raise ContainerWorkerClientError(
                f"container worker {method_normalized} {path} failed unexpectedly"
            )
        if response.status_code >= 400:
            try:
                error_payload = response.json()
            except ValueError:
                error_payload = None
            raise ContainerWorkerClientError(
                f"container worker {method_normalized} {path} returned HTTP "
                f"{response.status_code}: "
                f"{self._safe_response_text(response)}",
                payload=error_payload if isinstance(error_payload, dict) else None,
            )
        return response

    def _send_once(
        self, method: str, path: str, *, headers: dict[str, str], **kwargs: Any
    ) -> httpx.Response:
        if self._client is not None:
            return self._client.request(method, self._url(path), headers=headers, **kwargs)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            return client.request(method, self._url(path), headers=headers, **kwargs)

    def _connect_retry_sleep_seconds(self, attempt: int) -> float:
        planned = min(
            self.max_connect_backoff_seconds,
            self.base_connect_backoff_seconds * (2 ** (attempt - 1)),
        )
        jitter_multiplier = 0.8 + (random.random() * 0.4)
        return min(self.max_connect_backoff_seconds, planned * jitter_multiplier)

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _json_dict(self, response: httpx.Response, *, operation: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ContainerWorkerClientError(
                f"container worker {operation} returned non-JSON response"
            ) from exc
        if not isinstance(payload, dict):
            raise ContainerWorkerClientError(
                f"container worker {operation} returned a non-object JSON payload"
            )
        return payload

    def _safe_response_text(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return sanitize_text(response.text)
        if isinstance(payload, dict):
            return sanitize_text(str(sanitize_metadata(payload)))
        return sanitize_text(str(payload))


def _int_header(response: httpx.Response, name: str) -> int | None:
    value = response.headers.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
