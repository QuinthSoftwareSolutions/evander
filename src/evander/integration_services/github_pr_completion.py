"""Bound, SHA-checked GitHub completion without bypassing repository rules."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import httpx

from evander.ado_builds import positive_id
from evander.integration_services.pr_comments import GitHubPrComments
from evander.models import CompletePullRequestResponse


def complete_github_pull_request(
    *,
    client: httpx.Client,
    api_base_url: str,
    headers: dict[str, str],
    repo: str,
    pr_id: str,
    expected_source_commit: str,
    expected_target_branch: str,
    completion_id: str | None = None,
) -> CompletePullRequestResponse:
    repo = GitHubPrComments._repo(repo)
    pr_id = str(positive_id(pr_id, "pr_id"))
    source = expected_source_commit.strip().lower()
    target = expected_target_branch.strip().removeprefix("refs/heads/")
    if not re.fullmatch(r"[0-9a-f]{40}", source) or not target:
        raise ValueError("A full source commit and target branch are required")
    base_url = f"{api_base_url.rstrip('/')}/repos/{repo}"
    pr_url = f"{base_url}/pulls/{pr_id}"

    def get_pr() -> dict[str, Any]:
        response = client.get(pr_url, headers=headers)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("GitHub returned invalid pull request data")
        return payload

    def snapshot(pr: dict[str, Any]) -> CompletePullRequestResponse:
        result = CompletePullRequestResponse(
            provider="github",
            repo=repo,
            pr_id=pr_id,
            status="merged" if pr.get("merged") is True else pr.get("state"),
        )
        base = pr.get("base") or {}
        if (
            pr.get("number") != int(pr_id)
            or (base.get("repo") or {}).get("full_name", "").casefold() != repo.casefold()
        ):
            result.blockers.append("pull_request_scope_mismatch")
        if str((pr.get("head") or {}).get("sha") or "").lower() != source:
            result.blockers.append("source_commit_changed")
        if base.get("ref") != target:
            result.blockers.append("target_branch_changed")
        if result.blockers:
            return result
        if pr.get("merged") is True:
            commit = str(pr.get("merge_commit_sha") or "").lower()
            if re.fullmatch(r"[0-9a-f]{40}", commit):
                result.merged = True
                result.merge_commit = commit
            else:
                result.blockers.append("merge_commit_unavailable")
        return result

    if completion_id is not None:
        try:
            completion_id = str(UUID(completion_id))
        except ValueError as exc:
            raise ValueError("Invalid GitHub completion ID") from exc

    pr = get_pr()
    result = snapshot(pr)
    result.completion_id = completion_id
    if result.blockers or result.merged or result.status == "merged":
        return result

    if completion_id is None:
        if pr.get("state") != "open":
            result.blockers.append("pull_request_not_active")
        if pr.get("draft"):
            result.blockers.append("pull_request_is_draft")
        if result.blockers:
            return result
        if pr.get("mergeable") is False:
            result.blockers.append("merge_not_ready")
            return result
        if pr.get("mergeable") is not True:
            result.pending = True
            result.message = "GitHub is computing mergeability; retry with the same inputs."
            return result

        # GitHub's asynchronous API can merge open downstack PRs as well. Do not
        # expand a single bound PR into ownership of its target branch's open PR.
        response = client.get(
            f"{base_url}/pulls",
            headers=headers,
            params={"state": "open", "head": f"{repo.split('/')[0]}:{target}", "per_page": 1},
        )
        response.raise_for_status()
        downstack = response.json()
        if not isinstance(downstack, list):
            raise ValueError("GitHub returned invalid target-branch pull request data")
        if downstack:
            result.blockers.append("stacked_pull_request_requires_separate_completion")
            return result

        response = client.put(
            pr_url + "/merge-async",
            headers=headers,
            json={"sha": source, "merge_action": "default", "bypass_rules": False},
        )
        if response.status_code not in {200, 202, 409}:
            response.raise_for_status()
            raise ValueError("GitHub returned an unexpected completion status")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("GitHub returned invalid completion data")
        details = payload.get("details") or {}
        if not isinstance(details, dict):
            raise ValueError("GitHub returned invalid completion details")
        if response.status_code == 409 and (
            str(details.get("expected_head_sha") or "").lower() != source
            or details.get("bypass_rules") is not False
        ):
            result.blockers.append("existing_merge_request_mismatch")
            return result
        completion_id = details.get("uuid")
        if completion_id is not None:
            try:
                completion_id = str(UUID(str(completion_id)))
            except ValueError as exc:
                raise ValueError("GitHub returned an invalid completion ID") from exc

    # Supplying an ID is a read-only poll, even after the old job has failed or
    # the PR has become unmergeable. Never replace that result with a new job.
    if completion_id is not None:
        response = client.get(pr_url + "/merge-async/" + completion_id, headers=headers)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("GitHub returned invalid completion result")
        details = payload.get("details") or {}
        if not isinstance(details, dict):
            raise ValueError("GitHub returned invalid completion details")
        if (
            details.get("expected_head_sha") is not None
            and str(details["expected_head_sha"]).lower() != source
        ) or details.get("bypass_rules") is True:
            result.blockers.append("existing_merge_request_mismatch")
            return result
    elif payload.get("status") == "pending":
        raise ValueError("GitHub returned pending completion without an ID")

    result = snapshot(get_pr())
    result.completion_id = completion_id
    result.message = str(details.get("message") or "")[:2000] or None
    if result.blockers or result.merged:
        return result
    state = payload.get("status")
    if state == "failed":
        result.blockers.append("github_merge_failed")
    elif state in {"pending", "enqueued", "merged"}:
        # Even a finished queue request only confirms admission to the queue.
        # The fresh PR read above is the authority for completed merges.
        result.pending = result.status == "open"
        if not result.pending:
            result.blockers.append("pull_request_not_active")
    else:
        raise ValueError("GitHub returned an unknown completion state")
    return result
