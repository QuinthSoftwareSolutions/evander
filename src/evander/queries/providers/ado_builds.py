from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.ado_builds import AdoBuildClient, positive_id

from .base import QueryProviderBase, scoped_query_directory_path


class AdoBuildsQueryProvider(QueryProviderBase):
    """List builds by explicit repository, definition and branch/version filters."""

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self.query_definition.model_dump()
        payload.update(resolved_config or {})
        directory = scoped_query_directory_path(self.runtime_context, payload.get("directory_path"))
        pipeline_id = positive_id(payload["pipeline_id"], "pipeline_id")
        repository_id = payload["repository_id"]
        filters = payload["filters"]
        limit = positive_id(payload.get("limit", 100), "limit")
        if (
            limit > 1000
            or not isinstance(repository_id, str)
            or not repository_id.strip()
            or not isinstance(filters, list)
            or not filters
        ):
            raise ValueError(
                "repository_id, nonempty filters and a limit of at most 1000 are required"
            )
        repository_id = repository_id.strip()
        candidates: dict[str, dict[str, Any]] = {}
        with AdoBuildClient(
            self.runtime_context, directory_path=directory, project=payload.get("project")
        ) as client:
            for selector in filters:
                if (
                    not isinstance(selector, Mapping)
                    or not isinstance(selector.get("branch"), str)
                    or not selector["branch"].strip()
                ):
                    raise ValueError("Each build filter requires a branch ref")
                branch = selector["branch"]
                commit = selector.get("source_version")
                if commit is not None and not isinstance(commit, str):
                    raise ValueError("source_version must be a string or null")
                if "source_version" in selector and (commit is None or not commit.strip()):
                    continue
                continuation = None
                seen: set[str] = set()
                matching_count = 0
                while True:
                    params = {
                        "definitions": pipeline_id,
                        "repositoryId": repository_id,
                        "repositoryType": payload.get("repository_type", "TfsGit"),
                        "branchName": branch,
                        "queryOrder": "queueTimeDescending",
                        "$top": 100,
                    }
                    if continuation:
                        params["continuationToken"] = continuation
                    page, continuation = client.list_builds(**params)
                    matching = [
                        build
                        for build in page
                        if build.get("sourceBranch") == branch
                        and (
                            commit is None
                            or str(build.get("sourceVersion") or "").lower() == commit.lower()
                        )
                        and str(build.get("repository", {}).get("id")) == repository_id
                        and str(build.get("definition", {}).get("id")) == str(pipeline_id)
                        and not build.get("deleted")
                    ]
                    for build in matching:
                        output = client.output(build)
                        candidates[output["run_id"]] = output
                    matching_count += len(matching)
                    if matching_count >= limit:
                        break
                    if not continuation:
                        break
                    if continuation in seen or len(seen) >= 100:
                        raise RuntimeError("Azure DevOps build pagination could not be completed")
                    seen.add(continuation)

        builds = sorted(
            candidates.values(),
            key=lambda run: (run["queue_time"], int(run["run_id"])),
            reverse=True,
        )[:limit]
        return {"builds": builds, "count": len(builds)}
