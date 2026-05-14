from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx

from agent_sandbox.worker.models import (
    WorkerFileWriteRequest,
    WorkerFileWriteRecord,
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
)
from agent_sandbox.worker.security import sanitize_metadata, sanitize_text


class ContainerWorkerClientError(RuntimeError):
    pass


class ContainerWorkerClient:
    def __init__(
        self,
        *,
        base_url: str,
        token_env: str = "AGENT_SANDBOX_WORKER_TOKEN",
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        normalized_url = str(base_url or "").strip().rstrip("/")
        if not normalized_url:
            raise ValueError("container_worker_base_url is required for container_worker_vm.")
        self.base_url = normalized_url
        self.token_env = token_env
        self.timeout_seconds = timeout_seconds
        self._client = client

    def health(self) -> dict[str, Any]:
        response = self._request("GET", "/healthz", auth=False)
        return self._json_dict(response, operation="health")

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
            "paged": response.headers.get("x-agent-sandbox-log-paged") == "true",
            "limit": _int_header(response, "x-agent-sandbox-log-limit"),
            "offset": _int_header(response, "x-agent-sandbox-log-offset"),
            "returned_lines": _int_header(response, "x-agent-sandbox-log-returned-lines"),
            "total_lines": _int_header(response, "x-agent-sandbox-log-total-lines"),
            "start_line": _int_header(response, "x-agent-sandbox-log-start-line"),
            "end_line": _int_header(response, "x-agent-sandbox-log-end-line"),
            "has_more_before": response.headers.get(
                "x-agent-sandbox-log-has-more-before"
            )
            == "true",
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
        try:
            if self._client is not None:
                response = self._client.request(method, self._url(path), headers=headers, **kwargs)
            else:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.request(method, self._url(path), headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise ContainerWorkerClientError(
                f"container worker {method} {path} failed: {sanitize_text(str(exc))}"
            ) from exc
        if response.status_code >= 400:
            raise ContainerWorkerClientError(
                f"container worker {method} {path} returned HTTP {response.status_code}: "
                f"{self._safe_response_text(response)}"
            )
        return response

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
