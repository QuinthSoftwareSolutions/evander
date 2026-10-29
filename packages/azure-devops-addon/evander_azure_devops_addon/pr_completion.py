"""Shared, commit-pinned Azure DevOps PR completion for MCP and workflows."""

from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any


def complete_pull_request(
    request: Callable[..., dict[str, Any]],
    *,
    pr_path: str,
    expected_source_commit: str,
    expected_target_branch: str,
) -> dict[str, Any]:
    """Request one merge and verify provider state; never bypass branch policies."""
    source = expected_source_commit.lower()
    target = expected_target_branch.strip()
    if not target.startswith("refs/heads/"):
        target = "refs/heads/" + target
    if not re.fullmatch(r"[0-9a-f]{40}", source) or target == "refs/heads/":
        raise ValueError("A full source commit and target branch are required")
    repository_path = pr_path.rsplit("/pullRequests/", 1)[0]

    def snapshot(pr: dict[str, Any]) -> dict[str, Any]:
        blockers = []
        if str((pr.get("lastMergeSourceCommit") or {}).get("commitId") or "").lower() != source:
            blockers.append("source_commit_changed")
        if pr.get("targetRefName") != target:
            blockers.append("target_branch_changed")
        return {
            "status": pr.get("status"),
            "merged": False,
            "pending": False,
            "merge_commit": (pr.get("lastMergeCommit") or {}).get("commitId"),
            "blockers": blockers,
        }

    def verified(pr: dict[str, Any]) -> dict[str, Any]:
        result = snapshot(pr)
        if result["blockers"]:
            return result
        if pr.get("status") != "completed":
            result["pending"] = pr.get("status") == "active"
            if not result["pending"]:
                result["blockers"].append("pull_request_not_active")
            return result
        commit = str(result["merge_commit"] or "").lower()
        refs = request("GET", repository_path + "/refs", params={"filter": target[5:]})
        result["merged"] = bool(re.fullmatch(r"[0-9a-f]{40}", commit)) and any(
            ref.get("name") == target and str(ref.get("objectId") or "").lower() == commit
            for ref in refs.get("value", [])
        )
        if not result["merged"]:
            result["blockers"].append("merge_target_needs_verification")
        return result

    pr = request("GET", pr_path)
    result = snapshot(pr)
    if result["blockers"]:
        return result
    if pr.get("status") == "completed":
        return verified(pr)
    if pr.get("status") != "active":
        result["blockers"].append("pull_request_not_active")
    if pr.get("isDraft"):
        result["blockers"].append("pull_request_is_draft")
    if pr.get("mergeStatus") in {"conflicts", "failure", "rejectedByPolicy"}:
        result["blockers"].append("merge_not_ready")
    if any(r.get("vote") in {-10, -5} for r in pr.get("reviewers", [])):
        result["blockers"].append("review_changes_requested")
    project_id = str(((pr.get("repository") or {}).get("project") or {}).get("id") or "")
    if not project_id:
        result["blockers"].append("policy_scope_unavailable")
    else:
        policies = request(
            "GET",
            "policy/evaluations",
            params={
                "api-version": "7.1-preview.1",
                "artifactId": f"vstfs:///CodeReview/CodeReviewId/{project_id}/{pr['pullRequestId']}",
                "$top": 200,
            },
        ).get("value")
        if not isinstance(policies, list) or len(policies) >= 200:
            result["blockers"].append("policy_evaluation_incomplete")
        elif any(
            item.get("configuration", {}).get("isEnabled", True)
            and item.get("configuration", {}).get("isBlocking", True)
            and item.get("status") not in {"approved", "notApplicable"}
            for item in policies
        ):
            result["blockers"].append("branch_policy_not_satisfied")
    threads = request("GET", pr_path + "/threads").get("value")
    if not isinstance(threads, list):
        result["blockers"].append("review_threads_unavailable")
    elif any(t.get("status") in {"active", 1} and not t.get("isDeleted") for t in threads):
        result["blockers"].append("review_threads_unresolved")
    if result["blockers"]:
        return result
    request(
        "PATCH",
        pr_path,
        json={
            "status": "completed",
            "lastMergeSourceCommit": {"commitId": source},
            "completionOptions": {
                "bypassPolicy": False,
                "deleteSourceBranch": False,
                "mergeStrategy": "noFastForward",
            },
        },
    )
    return verified(request("GET", pr_path))
