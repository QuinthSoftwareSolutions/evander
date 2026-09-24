"""Directory-scoped Azure DevOps build reads shared by workflow providers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx


class AdoBuildClient:
    def __init__(self, context: Any, *, directory_path: str, project: str | None = None):
        resolver = context.provider_config_resolver
        config = (
            resolver.resolve_for_directory(directory_path) if resolver else context.resolved_config
        )
        if config is None:
            raise RuntimeError("ADO build operations require directory configuration")
        organization = str(config.azure_devops_organization_url or "").rstrip("/")
        self.project = str(project or config.azure_devops_project or "").strip()
        if not organization or not self.project:
            raise ValueError("Azure DevOps organization and project must be configured")
        token = context.resolve_secret(config.azure_devops_pat_env)
        if not token:
            raise RuntimeError("Azure DevOps build credential is not configured")
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
            raise RuntimeError("Azure DevOps build request could not be completed") from None
        if not response.is_success:
            raise RuntimeError(
                f"Azure DevOps build request failed with HTTP {response.status_code}"
            )
        try:
            value = response.json()
        except ValueError:
            raise RuntimeError("Azure DevOps returned invalid build data") from None
        if not isinstance(value, dict):
            raise RuntimeError("Azure DevOps returned invalid build data")
        return value, response.headers.get("x-ms-continuationtoken")

    def get_build(self, run_id: Any) -> dict[str, Any]:
        data, _ = self.get(f"build/builds/{positive_id(run_id, 'run_id')}")
        return self.output(data)

    def list_builds(self, **params: Any) -> tuple[list[dict[str, Any]], str | None]:
        data, continuation = self.get("build/builds", **params)
        builds = data.get("value")
        if not isinstance(builds, list) or any(not isinstance(build, dict) for build in builds):
            raise RuntimeError("Azure DevOps returned an invalid build list")
        return builds, continuation

    def output(self, build: Mapping[str, Any]) -> dict[str, Any]:
        run_id = positive_id(build.get("id"), "build id")
        status = str(build.get("status") or "").lower()
        result = str(build.get("result") or "").lower()
        if status not in {"notstarted", "inprogress", "postponed", "cancelling", "completed"}:
            raise RuntimeError("Azure DevOps returned an unsupported build status")
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


def positive_id(value: Any, name: str) -> int:
    if isinstance(value, bool) or not str(value).isdigit() or int(value) <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return int(value)
