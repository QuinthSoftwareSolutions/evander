from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.ado_builds import AdoBuildClient, positive_id

from .base import QueryProviderBase, scoped_query_directory_path


class AdoBuildsQueryProvider(QueryProviderBase):
    """Find the newest build attempt for a pinned pull request revision."""

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self.query_definition.model_dump()
        payload.update(resolved_config or {})
        directory = scoped_query_directory_path(self.runtime_context, payload.get("directory_path"))
        pipeline_id = positive_id(payload["pipeline_id"], "pipeline_id")
        pr_id = positive_id(payload["pr_id"], "pr_id")
        repository_id = str(payload["repository_id"]).strip()
        source_branch = str(payload["source_branch"]).strip()
        source_commit = str(payload["source_commit"]).strip()
        merge_commit = str(payload.get("merge_commit") or "").strip()
        if not repository_id or not source_commit or not source_branch.startswith("refs/heads/"):
            raise ValueError(
                "repository_id, source_commit and a full source_branch ref are required"
            )

        candidates: list[dict[str, Any]] = []
        branches = {source_branch: source_commit}
        if merge_commit:
            branches[f"refs/pull/{pr_id}/merge"] = merge_commit
        with AdoBuildClient(
            self.runtime_context, directory_path=directory, project=payload.get("project")
        ) as client:
            for branch, commit in branches.items():
                continuation = None
                seen: set[str] = set()
                while True:
                    params = {
                        "definitions": pipeline_id,
                        "repositoryId": repository_id,
                        "repositoryType": "TfsGit",
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
                        and str(build.get("sourceVersion") or "").lower() == commit.lower()
                        and str(build.get("repository", {}).get("id")) == repository_id
                        and str(build.get("definition", {}).get("id")) == str(pipeline_id)
                        and not build.get("deleted")
                    ]
                    if matching:
                        candidates.extend(client.output(build) for build in matching)
                        break
                    if not continuation:
                        break
                    if continuation in seen or len(seen) >= 100:
                        raise RuntimeError("Azure DevOps build pagination could not be completed")
                    seen.add(continuation)

        newest = max(
            candidates, key=lambda run: (run["queue_time"], int(run["run_id"])), default=None
        )
        return {
            "found": newest is not None,
            "build": newest,
            "branch_name": source_branch.removeprefix("refs/heads/"),
        }
