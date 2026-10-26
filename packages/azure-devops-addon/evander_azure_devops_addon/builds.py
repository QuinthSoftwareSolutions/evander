from __future__ import annotations
import base64
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote
from uuid import UUID
import httpx

from evander.credentials.sanitize import reject_reflected_credentials
from evander.credentials.store import CredentialError
from evander.ado_builds import (
    positive_id,
 )

class AdoBuildError(RuntimeError):
    """An expected build-service failure whose message contains no provider body."""


_DIAGNOSTIC_JSON_MAX_BYTES = 2 * 1024 * 1024
_LOG_MAX_BYTES = 64 * 1024


class AdoBuildClient:
    def __init__(self, context: Any, *, directory_path: str, project: str | None = None):
        resolver = context.provider_config_resolver
        config = (
            resolver.resolve_for_directory(directory_path) if resolver else context.resolved_config
        )
        from evander.optional_integrations import require_azure_devops_module
        require_azure_devops_module(config=config)
        if config is None:
            raise AdoBuildError("ADO build operations require directory configuration")
        organization = str(config.azure_devops_organization_url or "").rstrip("/")
        self.project = str(project or config.azure_devops_project or "").strip()
        if not organization or not self.project:
            raise ValueError("Azure DevOps organization and project must be configured")
        token = context.resolve_secret(config.azure_devops_pat_env)
        if not token:
            raise AdoBuildError("Azure DevOps build credential is not configured")
        encoded_auth = base64.b64encode(f":{token}".encode()).decode()
        self._sensitive_values = (token, encoded_auth)
        self.base_url = f"{organization}/{quote(self.project, safe='')}"
        self.client = httpx.Client(
            base_url=self.base_url + "/_apis/",
            auth=("", token),
            headers={"Accept": "application/json"},
            timeout=30.0,
        )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.client.close()

    def get(self, path: str, **params: Any) -> tuple[dict[str, Any], str | None]:
        try:
            response = self.client.get(path, params={"api-version": "7.1", **params})
        except httpx.RequestError:
            raise AdoBuildError("Azure DevOps build request could not be completed") from None
        if not response.is_success:
            raise AdoBuildError(
                f"Azure DevOps build request failed with HTTP {response.status_code}"
            )
        try:
            value = response.json()
        except ValueError:
            raise AdoBuildError("Azure DevOps returned invalid build data") from None
        if not isinstance(value, dict):
            raise AdoBuildError("Azure DevOps returned invalid build data")
        return value, response.headers.get("x-ms-continuationtoken")

    def get_build(self, run_id: Any) -> dict[str, Any]:
        data, _ = self.get(f"build/builds/{positive_id(run_id, 'run_id')}")
        return self.output(data)

    def _diagnostic_bytes(
        self, path: str, *, max_bytes: int, truncate: bool = False,
        accept: str = "application/json", **params: Any,
    ) -> tuple[bytes, bool]:
        content = bytearray()
        try:
            with self.client.stream(
                "GET", path, params={"api-version": "7.1", **params},
                headers={"Accept": accept},
            ) as response:
                if not response.is_success:
                    raise AdoBuildError(
                        f"Azure DevOps build request failed with HTTP {response.status_code}"
                    )
                # iter_bytes decodes compression once; never rebuild a Response with
                # decoded content and the upstream content-encoding header.
                for chunk in response.iter_bytes(chunk_size=4096):
                    content.extend(chunk[:max_bytes + 1 - len(content)])
                    if len(content) > max_bytes:
                        if not truncate:
                            raise AdoBuildError("Azure DevOps build response exceeded its byte limit")
                        return bytes(content[:max_bytes]), True
        except httpx.RequestError:
            raise AdoBuildError("Azure DevOps build request could not be completed") from None
        return bytes(content), False

    def _protect_diagnostic(self, value: Any) -> None:
        try:
            reject_reflected_credentials(value, self._sensitive_values)
        except CredentialError:
            raise AdoBuildError(
                "Azure DevOps build response contains host authentication material"
            ) from None

    def _diagnostic_json(self, path: str) -> dict[str, Any]:
        content, _ = self._diagnostic_bytes(path, max_bytes=_DIAGNOSTIC_JSON_MAX_BYTES)
        try:
            data = json.loads(content)
        except (ValueError, UnicodeDecodeError):
            raise AdoBuildError("Azure DevOps returned invalid build diagnostic data") from None
        if not isinstance(data, dict):
            raise AdoBuildError("Azure DevOps returned invalid build diagnostic data")
        self._protect_diagnostic(data)
        return data

    def get_timeline(
        self, build_id: Any, *, timeline_id: str | None = None,
        skip: int = 0, limit: int = 200,
    ) -> dict[str, Any]:
        build_id = positive_id(build_id, "build_id")
        _bounded_integer(skip, "skip", minimum=0)
        _bounded_integer(limit, "limit", minimum=1, maximum=1000)
        path = f"build/builds/{build_id}/timeline"
        if timeline_id is not None:
            try:
                path += "/" + str(UUID(timeline_id))
            except (ValueError, AttributeError, TypeError):
                raise ValueError("timeline_id must be a UUID") from None
        data = self._diagnostic_json(path)
        records = data.get("records")
        if (
            not isinstance(data.get("id"), str) or not data["id"]
            or not isinstance(records, list)
            or any(not isinstance(record, dict) for record in records)
        ):
            raise AdoBuildError("Azure DevOps returned invalid build timeline data")
        selected = records[skip:skip + limit]
        next_skip = skip + len(selected)
        return {
            "build_id": str(build_id), "project": self.project,
            "timeline_id": data["id"], "change_id": data.get("changeId"),
            "records": [_timeline_record(record) for record in selected],
            "count": len(selected), "total_count": len(records),
            "skip": skip, "limit": limit,
            "truncated": next_skip < len(records),
            "next_skip": next_skip if next_skip < len(records) else None,
        }

    def get_log(
        self, build_id: Any, log_id: Any, *, start_line: int | None = None,
        max_lines: int = 200,
    ) -> dict[str, Any]:
        build_id = positive_id(build_id, "build_id")
        log_id = positive_id(log_id, "log_id")
        _bounded_integer(max_lines, "max_lines", minimum=1, maximum=1000)
        if start_line is not None:
            _bounded_integer(start_line, "start_line", minimum=0)
        # Resolve metadata by IDs on the configured project, never by an upstream
        # log URL. The metadata also makes a bounded tail possible without a full download.
        data = self._diagnostic_json(f"build/builds/{build_id}/logs")
        logs = data.get("value")
        if not isinstance(logs, list) or any(not isinstance(log, dict) for log in logs):
            raise AdoBuildError("Azure DevOps returned invalid build log metadata")
        matching = [log for log in logs if str(log.get("id")) == str(log_id)]
        if len(matching) != 1:
            raise AdoBuildError("The requested log is not available for this build")
        total = matching[0].get("lineCount")
        if type(total) is not int or total < 0:
            raise AdoBuildError("Azure DevOps returned invalid build log metadata")
        start = max(0, total - max_lines) if start_line is None else start_line
        stop = min(total, start + max_lines)
        text = ""
        byte_truncated = False
        partial_last_line = False
        if start < stop:
            # Inspect beyond the public byte cap so credentials that straddle its
            # boundary are rejected before any prefix is returned.
            guard_bytes = max(4096, *(8 * len(value.encode()) for value in self._sensitive_values))
            content, stream_truncated = self._diagnostic_bytes(
                f"build/builds/{build_id}/logs/{log_id}",
                max_bytes=_LOG_MAX_BYTES + guard_bytes, truncate=True, accept="text/plain",
                # ADO uses one-based inclusive offsets; the MCP contract uses
                # zero-based offsets with an exclusive end.
                startLine=start + 1, endLine=stop,
            )
            self._protect_diagnostic(content)
            decoded = content.decode("utf-8", errors="replace")
            selected = "".join(_log_lines(decoded)[:stop - start]).encode("utf-8")
            byte_truncated = len(selected) > _LOG_MAX_BYTES or stream_truncated
            text = selected[:_LOG_MAX_BYTES].decode("utf-8", errors="ignore")
            partial_last_line = len(selected) > _LOG_MAX_BYTES and not text.endswith("\n")
        returned = len(_log_lines(text))
        end = start + returned
        return {
            "build_id": str(build_id), "project": self.project, "log_id": str(log_id),
            "start_line": start, "end_line": end, "total_lines": total,
            "returned_lines": returned, "max_lines": max_lines, "text": text,
            "truncated": start > 0 or end < total or byte_truncated,
            "byte_truncated": byte_truncated, "partial_last_line": partial_last_line,
            "next_start_line": end if returned and end < total else None,
        }

    def get_build_work_item_ids(self, run_id: Any, *, limit: int = 200) -> dict[str, Any]:
        build_id = positive_id(run_id, "run_id")
        top = positive_id(limit, "limit")
        if top > 1000:
            raise ValueError("limit must be at most 1000")
        data, _ = self.get(f"build/builds/{build_id}/workitems", **{"$top": top + 1})
        refs = data.get("value")
        if not isinstance(refs, list) or any(not isinstance(ref, dict) for ref in refs):
            raise AdoBuildError("Azure DevOps returned invalid build work item data")
        ids = [str(positive_id(ref.get("id"), "work item id")) for ref in refs[:top]]
        return {
            "build_id": str(build_id),
            "project": self.project,
            "work_item_ids": ids,
            "count": len(ids),
            "truncated": len(refs) > top,
            "same_project_only": True,
        }

    def list_builds(self, **params: Any) -> tuple[list[dict[str, Any]], str | None]:
        data, continuation = self.get("build/builds", **params)
        builds = data.get("value")
        if not isinstance(builds, list) or any(not isinstance(build, dict) for build in builds):
            raise AdoBuildError("Azure DevOps returned an invalid build list")
        return builds, continuation

    def search_builds(
        self,
        *,
        build_number: str | None = None,
        pipeline_id: int | str | None = None,
        repository_id: str | None = None,
        branch: str | None = None,
        limit: int = 25,
        continuation_token: str | None = None,
    ) -> dict[str, Any]:
        top = positive_id(limit, "limit")
        if top > 100:
            raise ValueError("limit must be at most 100")
        params: dict[str, Any] = {"$top": top, "queryOrder": "queueTimeDescending"}
        for name, parameter, value in (
            ("build_number", "buildNumber", build_number),
            ("repository_id", "repositoryId", repository_id),
            ("branch", "branchName", branch),
        ):
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"{name} must be a non-empty string")
                params[parameter] = value.strip()
        if pipeline_id is not None:
            params["definitions"] = positive_id(pipeline_id, "pipeline_id")
        if continuation_token is not None:
            if not isinstance(continuation_token, str) or not continuation_token.strip():
                raise ValueError("continuation_token must be a non-empty string")
            params["continuationToken"] = continuation_token

        builds, continuation = self.list_builds(**params)
        if len(builds) > top:
            raise AdoBuildError("Azure DevOps returned more builds than requested")
        matches = []
        for build in builds:
            definition = build.get("definition") or {}
            repository = build.get("repository") or {}
            if not isinstance(definition, dict) or not isinstance(repository, dict):
                raise AdoBuildError("Azure DevOps returned invalid build identity data")
            matches.append(
                {
                    **self.output(build),
                    "pipeline_id": (
                        str(positive_id(definition["id"], "pipeline id"))
                        if definition.get("id") is not None
                        else None
                    ),
                    "pipeline_name": definition.get("name"),
                    "repository_id": repository.get("id"),
                    "repository_name": repository.get("name"),
                }
            )
        return {
            "project": self.project,
            "builds": matches,
            "count": len(matches),
            "continuation_token": continuation or None,
        }

    def output(self, build: Mapping[str, Any]) -> dict[str, Any]:
        run_id = positive_id(build.get("id"), "build id")
        status = str(build.get("status") or "").lower()
        result = str(build.get("result") or "").lower()
        if status not in {"notstarted", "inprogress", "postponed", "cancelling", "completed"}:
            raise AdoBuildError("Azure DevOps returned an unsupported build status")
        return {
            "run_id": str(run_id),
            "build_number": str(build.get("buildNumber") or run_id),
            "run_url": f"{self.base_url}/_build/results?buildId={run_id}",
            "status": status,
            "result": result or None,
            "completed": status == "completed",
            "success": status == "completed" and result == "succeeded",
            "source_version": str(build.get("sourceVersion") or ""),
            "source_branch": str(build.get("sourceBranch") or ""),
            "queue_time": str(build.get("queueTime") or ""),
            "finish_time": build.get("finishTime"),
        }


def _bounded_integer(value: Any, name: str, *, minimum: int, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        bound = f" from {minimum} to {maximum}" if maximum is not None else f" of at least {minimum}"
        raise ValueError(f"{name} must be an integer{bound}")
    return value


def _log_lines(text: str) -> list[str]:
    # ADO offsets count LF-delimited log lines. Progress carriage returns and
    # Unicode line separators inside a message must not hide later diagnostics
    # or advance the continuation offset by more than one physical line.
    parts = text.split("\n")
    return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _timeline_record(record: dict[str, Any]) -> dict[str, Any]:
    issues = record.get("issues") or []
    attempts = record.get("previousAttempts") or []
    log = record.get("log") or {}
    details = record.get("details") or {}
    if (
        not isinstance(record.get("id"), str)
        or not isinstance(issues, list) or any(not isinstance(item, dict) for item in issues)
        or not isinstance(attempts, list) or any(not isinstance(item, dict) for item in attempts)
        or not isinstance(log, dict) or not isinstance(details, dict)
    ):
        raise AdoBuildError("Azure DevOps returned invalid build timeline data")
    return {
        "id": record["id"], "parent_id": record.get("parentId"),
        "type": record.get("type"), "name": record.get("name"),
        "state": record.get("state"), "result": record.get("result"),
        "attempt": record.get("attempt"), "order": record.get("order"),
        "error_count": record.get("errorCount", 0), "warning_count": record.get("warningCount", 0),
        "start_time": record.get("startTime"), "finish_time": record.get("finishTime"),
        "issues": [{key: item.get(key) for key in ("type", "category", "message")} for item in issues],
        "log_id": str(log["id"]) if log.get("id") is not None else None,
        "details_timeline_id": details.get("id"),
        "previous_attempts": [
            {"attempt": item.get("attempt"), "timeline_id": item.get("timelineId"),
             "record_id": item.get("recordId")} for item in attempts
        ],
    }
