from __future__ import annotations

import logging
import os
import random
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from evander.codex_auth import CodexSubscriptionDeferral
from evander.worker.models import (
    WorkerFileWriteRecord,
    WorkerFileWriteRequest,
    WorkerGitKeyDeleteRecord,
    WorkerGitWriteKeyRecord,
    WorkerGitWriteKeyRequest,
    WorkerJobRecord,
    WorkerJobRequest,
    WorkerMssqlDatabaseDeleteRecord,
    WorkerMssqlDatabaseListRecord,
    WorkerMssqlDatabaseRecord,
    WorkerMssqlDatabaseRequest,
    WorkerRepoCloneRecord,
    WorkerRepoCloneRequest,
    WorkerReferenceRepoPrepareRecord,
    WorkerReferenceRepoPrepareRequest,
    WorkerSessionPrepareRequest,
    WorkerSessionRecord,
    WorkerTaskWorktreeCleanupRecord,
    WorkerTaskWorktreeCleanupRequest,
    WorkerTaskWorktreeRecord,
    WorkerTaskWorktreeRequest,
    WorkerTelemetryPayload,
)
from evander.worker.security import sanitize_metadata, sanitize_text
from evander.worker.model_usage import WorkerUsagePage

logger = logging.getLogger(__name__)


class ContainerWorkerClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        payload: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.payload = dict(payload) if payload is not None else None
        self.status_code = status_code


class CodexSubscriptionDeferredClientError(ContainerWorkerClientError):
    def __init__(
        self,
        message: str,
        *,
        deferral: CodexSubscriptionDeferral,
        payload: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message, payload=payload, status_code=status_code)
        self.deferral = deferral


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

    def list_codex_auth_profiles(self) -> dict[str, Any]:
        response = self._request("GET", "/v1/codex-auth/profiles")
        return self._json_dict(response, operation="list Codex auth profiles")

    def create_codex_auth_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._request("POST", "/v1/codex-auth/profiles", json=payload)
        return self._json_dict(response, operation="create Codex auth profile")

    def codex_auth_profile_status(self, profile_id: str) -> dict[str, Any]:
        encoded = quote(str(profile_id), safe="")
        response = self._request("GET", f"/v1/codex-auth/profiles/{encoded}")
        return self._json_dict(response, operation="read Codex auth profile")

    def patch_codex_auth_profile(self, profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        encoded = quote(str(profile_id), safe="")
        response = self._request("PATCH", f"/v1/codex-auth/profiles/{encoded}", json=payload)
        return self._json_dict(response, operation="update Codex auth profile")

    def start_codex_profile_device_login(self, profile_id: str) -> dict[str, Any]:
        encoded = quote(str(profile_id), safe="")
        response = self._request("POST", f"/v1/codex-auth/profiles/{encoded}/device-login")
        return self._json_dict(response, operation="start Codex profile device login")

    def delete_codex_auth_profile(self, profile_id: str) -> dict[str, Any]:
        encoded = quote(str(profile_id), safe="")
        response = self._request("DELETE", f"/v1/codex-auth/profiles/{encoded}")
        return self._json_dict(response, operation="delete Codex auth profile")

    def consume_codex_profile_reset_credit(
        self, profile_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        encoded = quote(str(profile_id), safe="")
        response = self._request(
            "POST", f"/v1/codex-auth/profiles/{encoded}/reset-credits/consume", json=payload
        )
        return self._json_dict(response, operation="redeem Codex reset credit")

    def codex_profile_device_login_status(self, profile_id: str, login_id: str) -> dict[str, Any]:
        profile = quote(str(profile_id), safe="")
        login = quote(str(login_id), safe="")
        response = self._request("GET", f"/v1/codex-auth/profiles/{profile}/device-login/{login}")
        return self._json_dict(response, operation="read Codex profile device login")

    def cancel_codex_profile_device_login(self, profile_id: str, login_id: str) -> dict[str, Any]:
        profile = quote(str(profile_id), safe="")
        login = quote(str(login_id), safe="")
        response = self._request(
            "DELETE", f"/v1/codex-auth/profiles/{profile}/device-login/{login}"
        )
        return self._json_dict(response, operation="cancel Codex profile device login")

    def logout_codex_auth_profile(self, profile_id: str) -> dict[str, Any]:
        encoded = quote(str(profile_id), safe="")
        response = self._request("DELETE", f"/v1/codex-auth/profiles/{encoded}/session")
        return self._json_dict(response, operation="log out Codex auth profile")

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

    def prepare_reference_repo(
        self,
        session_id: str,
        request: WorkerReferenceRepoPrepareRequest,
    ) -> WorkerReferenceRepoPrepareRecord:
        path = f"/v1/sessions/{session_id}/reference-repo"
        try:
            response = self._request(
                "POST",
                path,
                json=request.model_dump(mode="json", exclude_none=True),
            )
        except ContainerWorkerClientError as exc:
            if not _worker_rejected_preparation_fingerprint(request, exc):
                raise
            response = self._request(
                "POST",
                path,
                json=request.model_copy(update={"preparation_fingerprint": None}).model_dump(
                    mode="json", exclude_none=True
                ),
            )
        return WorkerReferenceRepoPrepareRecord.model_validate(
            self._json_dict(response, operation="prepare reference repo")
        )

    def prepare_task_worktree(
        self,
        session_id: str,
        request: WorkerTaskWorktreeRequest,
    ) -> WorkerTaskWorktreeRecord:
        if request.required_git_recovery_protocol is not None:
            capabilities = self.health().get("capabilities") or {}
            if capabilities.get("git_recovery", 0) < request.required_git_recovery_protocol:
                raise ContainerWorkerClientError("Worker does not support the required Git recovery protocol",
                    status_code=409, payload={"git_preparation_failure": {"cause": "policy_conflict",
                        "evidence": {"required_protocol": request.required_git_recovery_protocol,
                                     "available_protocol": capabilities.get("git_recovery", 0)}}})
        path = f"/v1/sessions/{session_id}/worktrees"
        try:
            response = self._request(
                "POST",
                path,
                json=request.model_dump(mode="json", exclude_none=True),
            )
        except ContainerWorkerClientError as exc:
            if request.required_git_recovery_protocol is not None or not _worker_rejected_preparation_attempt_id(request, exc):
                raise
            response = self._request(
                "POST",
                path,
                json=request.model_copy(update={"preparation_attempt_id": None}).model_dump(
                    mode="json", exclude_none=True
                ),
            )
        return WorkerTaskWorktreeRecord.model_validate(
            self._json_dict(response, operation="prepare task worktree")
        )

    def capture_git_salvage(self, session_id, request):
        if (self.health().get("capabilities") or {}).get("git_salvage", 0) < 1:
            raise ContainerWorkerClientError("Worker does not support Git salvage; retained data was not changed", status_code=409)
        response = self._request("POST", f"/v1/sessions/{session_id}/worktrees/salvage", json=request.model_dump(mode="json"))
        return self._json_dict(response, operation="capture Git salvage")

    def cleanup_task_worktree(
        self,
        session_id: str,
        request: WorkerTaskWorktreeCleanupRequest,
    ) -> WorkerTaskWorktreeCleanupRecord:
        path = f"/v1/sessions/{session_id}/worktrees/cleanup"
        try:
            response = self._request(
                "POST",
                path,
                json=request.model_dump(mode="json", exclude_none=True),
            )
        except ContainerWorkerClientError as exc:
            if not _worker_rejected_preparation_attempt_id(request, exc):
                raise
            response = self._request(
                "POST",
                path,
                json=request.model_copy(update={"preparation_attempt_id": None}).model_dump(
                    mode="json", exclude_none=True
                ),
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
        path = f"/v1/sessions/{session_id}/files"
        try:
            response = self._request(
                "POST",
                path,
                json=request.model_dump(mode="json", exclude_none=True),
            )
        except ContainerWorkerClientError as exc:
            if not _worker_rejected_preparation_fingerprint(request, exc):
                raise
            response = self._request(
                "POST",
                path,
                json=request.model_copy(update={"preparation_fingerprint": None}).model_dump(
                    mode="json", exclude_none=True
                ),
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

    def read_session_artifact_text_bounded(
        self, session_id: str, relative_path: str, *, max_bytes: int
    ) -> str:
        """Bound both the worker's file read and the streamed response body."""
        from contextlib import nullcontext

        if max_bytes <= 0:
            raise ValueError("Artifact byte limit must be positive")
        token = os.environ.get(self.token_env, "")
        if not token:
            raise ContainerWorkerClientError("Container worker bearer token is not configured")
        path = (
            f"/v1/sessions/{quote(session_id, safe='')}/artifacts/{quote(relative_path, safe='/')}"
        )
        manager = (
            nullcontext(self._client)
            if self._client is not None
            else httpx.Client(timeout=self.timeout_seconds)
        )
        with (
            manager as client,
            client.stream(
                "GET", self._url(path), headers={"Authorization": f"Bearer {token}"},
                params={"max_bytes": max_bytes},
            ) as response,
        ):
            if response.status_code >= 400:
                raise ContainerWorkerClientError(
                    f"Worker artifact returned HTTP {response.status_code}",
                    status_code=response.status_code,
                )
            content = bytearray()
            for chunk in response.iter_bytes(chunk_size=65536):
                if len(content) + len(chunk) > max_bytes:
                    raise ValueError("Worker artifact exceeds import limit")
                content.extend(chunk)
        return content.decode("utf-8")

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

    def model_usage(self, job_id: str, *, cursor: int = 0, limit: int = 200) -> WorkerUsagePage:
        response = self._request(
            "GET", f"/v1/jobs/{job_id}/model-usage", params={"cursor": cursor, "limit": limit},
        )
        return WorkerUsagePage.model_validate(self._json_dict(response, operation="model usage"))

    def stop(self, job_id: str) -> WorkerJobRecord:
        response = self._request("POST", f"/v1/jobs/{job_id}/stop")
        return WorkerJobRecord.model_validate(self._json_dict(response, operation="stop job"))

    def steer(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._json_dict(
            self._request("POST", f"/v1/jobs/{job_id}/steer", json=payload), operation="steer job"
        )

    def steering_receipt(self, job_id: str, delivery_id: str) -> dict[str, Any] | None:
        from urllib.parse import quote

        try:
            response = self._request(
                "GET", f"/v1/jobs/{job_id}/steering/{quote(delivery_id, safe='')}"
            )
        except ContainerWorkerClientError as exc:
            if exc.status_code == 404:
                return None
            raise
        return self._json_dict(response, operation="steering receipt")

    def interrupt_active_tool(
        self,
        job_id: str,
        *,
        max_elapsed_seconds: float | None = None,
        reason: str = "operator_requested",
    ) -> WorkerJobRecord:
        payload: dict[str, object] = {"reason": reason}
        if max_elapsed_seconds is not None:
            payload["max_elapsed_seconds"] = float(max_elapsed_seconds)
        response = self._request(
            "POST",
            f"/v1/jobs/{job_id}/interrupt-active-tool",
            json=payload,
        )
        return WorkerJobRecord.model_validate(
            self._json_dict(response, operation="interrupt active tool")
        )

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
                        f"{sanitize_text(str(exc))}",
                        payload={"git_preparation_failure": {"cause": "transport_unavailable"}},
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
                    f"container worker {method_normalized} {path} failed: {sanitize_text(str(exc))}",
                    payload={"git_preparation_failure": {"cause": "transport_unavailable"}},
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
            safe_payload = error_payload if isinstance(error_payload, dict) else None
            deferral_payload = safe_payload.get("deferral") if safe_payload else None
            if isinstance(deferral_payload, dict):
                try:
                    deferral = CodexSubscriptionDeferral.model_validate(deferral_payload)
                except ValueError:
                    deferral = None
                if deferral is not None:
                    raise CodexSubscriptionDeferredClientError(
                        f"container worker deferred subscription work for profile "
                        f"{deferral.profile_id}: {deferral.reason}",
                        deferral=deferral,
                        payload=safe_payload,
                        status_code=response.status_code,
                    )
            raise ContainerWorkerClientError(
                f"container worker {method_normalized} {path} returned HTTP "
                f"{response.status_code}: "
                f"{self._safe_response_text(response)}",
                payload=safe_payload,
                status_code=response.status_code,
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


def _worker_rejected_preparation_attempt_id(
    request: WorkerTaskWorktreeRequest | WorkerTaskWorktreeCleanupRequest,
    exc: ContainerWorkerClientError,
) -> bool:
    """Recognize the strict validation response from pre-receipt worker daemons."""

    if request.preparation_attempt_id is None or exc.status_code not in {400, 409}:
        return False
    payload = exc.payload or {}
    error = str(payload.get("error") or "").casefold()
    return "preparation_attempt_id" in error and (
        "extra_forbidden" in error or "extra inputs are not permitted" in error
    )


def _worker_rejected_preparation_fingerprint(request: Any, exc: ContainerWorkerClientError) -> bool:
    if getattr(request, "preparation_fingerprint", None) is None or exc.status_code not in {
        400,
        409,
        422,
    }:
        return False
    error = str(exc).casefold()
    return "preparation_fingerprint" in error and (
        "extra" in error or "unexpected" in error or "not permitted" in error
    )


def _int_header(response: httpx.Response, name: str) -> int | None:
    value = response.headers.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
