from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote
import httpx

from evander.ado_builds import (
    positive_id,
 )

class AdoBuildError(RuntimeError):
    """An expected build-service failure whose message contains no provider body."""


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
