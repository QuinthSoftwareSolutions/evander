from __future__ import annotations
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
import httpx
from evander.ado_builds import positive_id
from evander.ado_mentions import AdoResolvedMention, render_ado_mentions_prefix
from evander_azure_devops_addon.mentions import AdoMentionResolver, resolve_ado_mentions
from evander.models import (
    AdoPrCommentActivityResponse,
    CompletePullRequestResponse,
    AdoPrCommitActivityResponse,
    GetPullRequestResponse,
    ListPullRequestsResponse,
    PostPrFeedbackReplyResponse,
    PullRequestCommit,
    PullRequestItem,
    PullRequestReviewer,
    PullRequestWorkItemsResponse,
    ResolveAdoIdentityResponse,
    UpdatePullRequestResponse,
)
from evander.pr_feedback.providers import (
    _ado_line_from_context,
    _ado_should_skip_comment,
    _ado_thread_resolved,
)
from evander.pr_feedback.identity import (
    normalize_azure_devops_repo_identifier as _normalize_azure_devops_repo_identifier,
)
from evander.integration_services.common import _ado_identity_summary, _datetime_in_range, _identity_match_values, _identity_matches, _normalize_optional_text, _normalize_string_list, _parse_dt, _parse_optional_dt, _pull_request_work_item_from_reference, _snippet, _text_or_none, _unique_positive_ints

from evander.integration_services.pull_requests import (
    _pr_artifact_filename,
    _pr_artifact_mime_type,
    _pr_artifact_is_image,
    _pr_artifact_bytes,
    _append_markdown_attachment_links,
    _activity_statuses,
    _activity_max_candidates,
    _parse_pull_request_tag_expression,
    _pull_request_tags_match,
    _azure_pr_web_url,
    _normalize_azure_devops_pr_path,
    _raise_azure_devops_pr_publish_error,
    _normalize_pr_review_comments,
    _as_int_or_none,
)

_AZURE_DEVOPS_REVIEW_VOTES = {
    "APPROVE": 10,
    "APPROVE_WITH_SUGGESTIONS": 5,
    "WAITING_FOR_AUTHOR": -5,
    "REQUEST_CHANGES": -5,
    "RESET": 0,
}

def _upload_azure_devops_pr_attachments(
    *,
    client: httpx.Client,
    organization_url: str,
    project: str,
    pat: str,
    repo: str,
    pr_id: str,
    attachments: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    uploaded: list[dict[str, Any]] = []
    for artifact in attachments or []:
        filename = _pr_artifact_filename(artifact)
        response = client.post(
            (
                f"{organization_url}/{project}/_apis/git/repositories/"
                f"{_normalize_azure_devops_repo_identifier(repo)}/pullRequests/{pr_id}/"
                f"attachments/{quote(filename, safe='')}"
            ),
            params={"api-version": "7.1"},
            auth=("", pat),
            content=_pr_artifact_bytes(artifact),
            headers={"Content-Type": "application/octet-stream"},
        )
        response.raise_for_status()
        payload = response.json()
        uploaded.append(
            {
                "provider": "azure_devops",
                "surface": "pull_request",
                "artifact_id": artifact.get("artifact_id"),
                "filename": filename,
                "mime_type": _pr_artifact_mime_type(artifact),
                "byte_count": artifact.get("byte_count"),
                "url": str(payload.get("url") or "").strip(),
                "id": payload.get("id"),
                "is_image": _pr_artifact_is_image(artifact),
                "raw": payload,
            }
        )
    return uploaded


class AzureDevOpsPrReplyProvider:
    def __init__(
        self,
        *,
        organization_url: str,
        project: str,
        pat: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.organization_url = organization_url.rstrip("/")
        self.project = project
        self.pat = pat
        self.timeout_seconds = timeout_seconds

    def post_reply(
        self,
        *,
        repo: str,
        pr_id: str,
        thread_id: str | None,
        comment_id: str | None,
        body_text: str,
        feedback_kind: str | None = None,
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> PostPrFeedbackReplyResponse:
        if not thread_id:
            raise ValueError("Azure DevOps reply posting requires thread_id")
        if not comment_id:
            raise ValueError("Azure DevOps reply posting requires comment_id")
        repo_identifier = _normalize_azure_devops_repo_identifier(repo)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            resolved_mentions = resolve_ado_mentions(
                client=client,
                organization_url=self.organization_url,
                pat=self.pat,
                mention_refs=mentions,
            )
            uploaded_attachments = _upload_azure_devops_pr_attachments(
                client=client,
                organization_url=self.organization_url,
                project=self.project,
                pat=self.pat,
                repo=repo,
                pr_id=pr_id,
                attachments=attachments,
            )
            body_with_mentions = render_ado_mentions_prefix(body_text, resolved_mentions)
            content = _append_markdown_attachment_links(body_with_mentions, uploaded_attachments)
            response = client.post(
                (
                    f"{self.organization_url}/{self.project}/_apis/git/repositories/{repo_identifier}/"
                    f"pullRequests/{pr_id}/threads/{thread_id}/comments"
                ),
                params={"api-version": "7.1"},
                auth=("", self.pat),
                json={"content": content, "parentCommentId": int(comment_id)},
            )
            response.raise_for_status()
            payload = response.json()
        return PostPrFeedbackReplyResponse(
            status="posted",
            provider="azure_devops",
            repo=repo,
            pr_id=pr_id,
            thread_id=thread_id,
            comment_id=comment_id,
            feedback_kind="thread_comment",
            reply_id=str(payload.get("id", "")) or None,
            reply_url=payload.get("_links", {}).get("web", {}).get("href"),
            uploaded_attachments=uploaded_attachments,
            resolved_mentions=_ado_resolved_mention_payloads(resolved_mentions),
        )


class AzureDevOpsPrQueryProvider:
    def __init__(
        self,
        *,
        organization_url: str,
        project: str,
        pat: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.organization_url = organization_url.rstrip("/")
        self.project = project
        self.pat = pat
        self.timeout_seconds = timeout_seconds

    def resolve_identity(
        self,
        *,
        query: str,
        project: str | None = None,
    ) -> ResolveAdoIdentityResponse:
        del project
        normalized_query = str(query or "").strip()
        if not normalized_query:
            raise ValueError("query is required")
        with httpx.Client(timeout=self.timeout_seconds) as client:
            identities: list[dict[str, Any]]
            if normalized_query.casefold() == "me":
                response = client.get(
                    f"{self.organization_url}/_apis/connectionData",
                    params={"api-version": "7.1-preview.1"},
                    auth=("", self.pat),
                )
                response.raise_for_status()
                payload = response.json()
                authenticated = (
                    payload.get("authenticatedUser") if isinstance(payload, dict) else None
                )
                identities = [authenticated] if isinstance(authenticated, dict) else []
            else:
                resolver = AdoMentionResolver(
                    client=client,
                    organization_url=self.organization_url,
                    pat=self.pat,
                )
                try:
                    resolved = resolver.resolve(normalized_query)
                    matches = [
                        {
                            "id": resolved.id,
                            "descriptor": resolved.descriptor,
                            "display_name": resolved.display_name,
                            "unique_name": resolved.unique_name,
                            "email": resolved.unique_name
                            if resolved.unique_name and "@" in resolved.unique_name
                            else None,
                            "raw": resolved.audit_payload(),
                        }
                    ]
                    return ResolveAdoIdentityResponse(
                        query=normalized_query,
                        confidence="exact",
                        id=resolved.id,
                        descriptor=resolved.descriptor,
                        display_name=resolved.display_name,
                        unique_name=resolved.unique_name,
                        email=matches[0]["email"],
                        matches=matches,
                    )
                except ValueError:
                    identities = resolver._read_identities(  # noqa: SLF001 - reuse ADO identity API wrapper.
                        {
                            "searchFilter": "General",
                            "filterValue": normalized_query,
                            "queryMembership": "None",
                        }
                    )

        matches = [
            _ado_identity_summary(normalized_query, identity)
            for identity in identities
            if isinstance(identity, dict)
            and identity.get("isContainer") is not True
            and identity.get("isActive") is not False
            and _text_or_none(identity.get("id"))
        ]
        exact_matches = [
            item for item in matches if normalized_query.casefold() in _identity_match_values(item)
        ]
        selected = exact_matches[0] if len(exact_matches) == 1 else None
        if selected is None and len(matches) == 1:
            selected = matches[0]
        confidence = "none"
        if selected is not None:
            confidence = "exact" if len(matches) == 1 or len(exact_matches) == 1 else "low"
        elif matches:
            confidence = "ambiguous"
        return ResolveAdoIdentityResponse(
            query=normalized_query,
            confidence=confidence,
            id=selected.get("id") if selected else None,
            descriptor=selected.get("descriptor") if selected else None,
            display_name=selected.get("display_name") if selected else None,
            unique_name=selected.get("unique_name") if selected else None,
            email=selected.get("email") if selected else None,
            matches=matches,
        )

    def list_pr_comment_activity(
        self,
        *,
        repo: str,
        author: str | None,
        since: datetime,
        until: datetime | None = None,
        project: str | None = None,
        statuses: list[str] | None = None,
        include_system_comments: bool = False,
        top: int = 100,
        skip: int = 0,
        exhaustive: bool = False,
        max_candidates: int = 50,
    ) -> AdoPrCommentActivityResponse:
        repo_identifier = _normalize_azure_devops_repo_identifier(repo)
        project_name = _normalize_optional_text(project) or self.project
        status_values = _activity_statuses(statuses)
        pull_requests, candidate_scan_complete = self._activity_pull_request_candidates(
            repo=repo,
            project=project_name,
            statuses=status_values,
            exhaustive=exhaustive,
            max_candidates=max_candidates,
        )
        comments: list[dict[str, Any]] = []
        with httpx.Client(timeout=self.timeout_seconds) as client:
            for pull_request in pull_requests:
                response = client.get(
                    (
                        f"{self.organization_url}/{project_name}/_apis/git/repositories/"
                        f"{repo_identifier}/pullRequests/{pull_request.pr_id}/threads"
                    ),
                    params={"api-version": "7.1"},
                    auth=("", self.pat),
                )
                response.raise_for_status()
                payload = response.json()
                raw_threads = payload.get("value") if isinstance(payload, dict) else None
                for thread in raw_threads if isinstance(raw_threads, list) else []:
                    if not isinstance(thread, dict):
                        continue
                    thread_id = str(thread.get("id") or "").strip() or None
                    thread_context = (
                        thread.get("threadContext")
                        if isinstance(thread.get("threadContext"), dict)
                        else {}
                    )
                    file_path = _text_or_none(thread_context.get("filePath"))
                    line = _ado_line_from_context(thread_context)
                    resolved = _ado_thread_resolved(thread.get("status"))
                    raw_comments = [
                        item for item in thread.get("comments", []) if isinstance(item, dict)
                    ]
                    for comment in raw_comments:
                        if comment.get("isDeleted"):
                            continue
                        created_at = _parse_optional_dt(
                            comment.get("publishedDate") or comment.get("lastUpdatedDate")
                        )
                        if not _datetime_in_range(created_at, since=since, until=until):
                            continue
                        author_identity = (
                            comment.get("author") if isinstance(comment.get("author"), dict) else {}
                        )
                        author_name = _text_or_none(author_identity.get("displayName"))
                        body = _text_or_none(comment.get("content")) or ""
                        if not include_system_comments and _ado_should_skip_comment(
                            author=author_name or "unknown",
                            body=body,
                            comment_type=comment.get("commentType"),
                            file_path=file_path,
                        ):
                            continue
                        if not _identity_matches(author, author_identity or author_name):
                            continue
                        comment_id = str(comment.get("id") or "").strip() or None
                        comment_links = (
                            comment.get("_links") if isinstance(comment.get("_links"), dict) else {}
                        )
                        comment_web_link = (
                            comment_links.get("web")
                            if isinstance(comment_links.get("web"), dict)
                            else {}
                        )
                        comments.append(
                            {
                                "repo": repo,
                                "project": project_name,
                                "pr_id": pull_request.pr_id,
                                "pr_title": pull_request.title,
                                "pr_status": pull_request.status,
                                "pr_url": pull_request.url,
                                "thread_id": thread_id,
                                "comment_id": comment_id,
                                "author": author_name,
                                "author_identity": author_identity,
                                "created_at": created_at,
                                "body": body,
                                "body_snippet": _snippet(body),
                                "file_path": file_path,
                                "line": line,
                                "resolved": resolved,
                                "url": _text_or_none(comment_web_link.get("href"))
                                or pull_request.url,
                                "raw": comment,
                            }
                        )
        comments.sort(key=lambda item: item["created_at"], reverse=True)
        total_activity_count = len(comments)
        paged_comments = comments[skip : skip + top]
        next_skip = (
            skip + len(paged_comments)
            if skip + len(paged_comments) < total_activity_count
            else None
        )
        has_more = next_skip is not None or not candidate_scan_complete
        return AdoPrCommentActivityResponse(
            repo=repo,
            project=project_name,
            comments=paged_comments,
            count=len(paged_comments),
            paging={
                "skip": skip,
                "top": top,
                "candidate_count": len(pull_requests),
                "candidate_scanned_count": len(pull_requests),
                "candidate_scan_complete": candidate_scan_complete,
                "total_activity_count": total_activity_count,
                "total_activity_count_complete": candidate_scan_complete,
                "returned_count": len(paged_comments),
                "has_more": has_more,
                "next_skip": next_skip,
                "statuses": status_values,
                "exhaustive": exhaustive,
                "max_candidates": _activity_max_candidates(max_candidates),
            },
        )

    def list_pr_commit_activity(
        self,
        *,
        repo: str,
        author: str | None,
        since: datetime,
        until: datetime | None = None,
        project: str | None = None,
        statuses: list[str] | None = None,
        top: int = 100,
        skip: int = 0,
        exhaustive: bool = False,
        max_candidates: int = 50,
    ) -> AdoPrCommitActivityResponse:
        repo_identifier = _normalize_azure_devops_repo_identifier(repo)
        project_name = _normalize_optional_text(project) or self.project
        status_values = _activity_statuses(statuses)
        pull_requests, candidate_scan_complete = self._activity_pull_request_candidates(
            repo=repo,
            project=project_name,
            statuses=status_values,
            exhaustive=exhaustive,
            max_candidates=max_candidates,
        )
        commits: list[dict[str, Any]] = []
        with httpx.Client(timeout=self.timeout_seconds) as client:
            for pull_request in pull_requests:
                response = client.get(
                    (
                        f"{self.organization_url}/{project_name}/_apis/git/repositories/"
                        f"{repo_identifier}/pullRequests/{pull_request.pr_id}/commits"
                    ),
                    params={"api-version": "7.1"},
                    auth=("", self.pat),
                )
                response.raise_for_status()
                payload = response.json()
                raw_commits = payload.get("value") if isinstance(payload, dict) else None
                for commit in raw_commits if isinstance(raw_commits, list) else []:
                    if not isinstance(commit, dict):
                        continue
                    commit_author = (
                        commit.get("author") if isinstance(commit.get("author"), dict) else {}
                    )
                    committer = (
                        commit.get("committer") if isinstance(commit.get("committer"), dict) else {}
                    )
                    if author and not (
                        _identity_matches(author, commit_author)
                        or _identity_matches(author, committer)
                    ):
                        continue
                    authored_at = _parse_optional_dt(commit_author.get("date"))
                    committed_at = _parse_optional_dt(committer.get("date"))
                    best_date = authored_at or committed_at
                    unverified_date = False
                    if best_date is None:
                        pr_date = pull_request.updated_at or pull_request.created_at
                        if not _datetime_in_range(pr_date, since=since, until=until):
                            continue
                        unverified_date = True
                    elif not _datetime_in_range(best_date, since=since, until=until):
                        continue
                    commit_id = str(commit.get("commitId") or commit.get("commit_id") or "").strip()
                    if not commit_id:
                        continue
                    commits.append(
                        {
                            "repo": repo,
                            "project": project_name,
                            "pr_id": pull_request.pr_id,
                            "pr_title": pull_request.title,
                            "pr_status": pull_request.status,
                            "pr_url": pull_request.url,
                            "commit_id": commit_id,
                            "comment": _text_or_none(commit.get("comment")),
                            "url": _text_or_none(commit.get("url")),
                            "author": commit_author,
                            "committer": committer,
                            "authored_at": authored_at,
                            "committed_at": committed_at,
                            "unverified_date": unverified_date,
                            "raw": commit,
                        }
                    )
        commits.sort(
            key=lambda item: (
                item.get("authored_at")
                or item.get("committed_at")
                or datetime.min.replace(tzinfo=UTC)
            ),
            reverse=True,
        )
        total_activity_count = len(commits)
        paged_commits = commits[skip : skip + top]
        next_skip = (
            skip + len(paged_commits) if skip + len(paged_commits) < total_activity_count else None
        )
        has_more = next_skip is not None or not candidate_scan_complete
        return AdoPrCommitActivityResponse(
            repo=repo,
            project=project_name,
            commits=paged_commits,
            count=len(paged_commits),
            paging={
                "skip": skip,
                "top": top,
                "candidate_count": len(pull_requests),
                "candidate_scanned_count": len(pull_requests),
                "candidate_scan_complete": candidate_scan_complete,
                "total_activity_count": total_activity_count,
                "total_activity_count_complete": candidate_scan_complete,
                "returned_count": len(paged_commits),
                "has_more": has_more,
                "next_skip": next_skip,
                "statuses": status_values,
                "exhaustive": exhaustive,
                "max_candidates": _activity_max_candidates(max_candidates),
            },
        )

    def _activity_pull_request_candidates(
        self,
        *,
        repo: str,
        project: str,
        statuses: list[str],
        exhaustive: bool,
        max_candidates: int,
    ) -> tuple[list[PullRequestItem], bool]:
        page_size = 200
        candidate_limit = None if exhaustive else _activity_max_candidates(max_candidates)
        candidates: dict[tuple[str, str, str], PullRequestItem] = {}
        for status_index, status in enumerate(statuses):
            page_skip = 0
            page_cursor: str | None = None
            seen_pages: set[tuple[int, str | None]] = set()
            while True:
                remaining = (
                    None if candidate_limit is None else max(0, candidate_limit - len(candidates))
                )
                if remaining == 0:
                    return list(candidates.values()), False
                page_key = (page_skip, page_cursor)
                if page_key in seen_pages:
                    break
                seen_pages.add(page_key)
                request_top = page_size if remaining is None else min(page_size, remaining)
                response = self.list_pull_requests(
                    repo=repo,
                    project=project,
                    status=status,
                    top=request_top,
                    skip=page_skip,
                    cursor=page_cursor,
                    include_links=True,
                )
                for item in response.pull_requests:
                    candidates[(item.provider, item.repo, item.pr_id)] = item
                paging = response.paging or {}
                if candidate_limit is not None and len(candidates) >= candidate_limit:
                    more_statuses = status_index < len(statuses) - 1
                    return list(candidates.values()), not (paging.get("has_more") or more_statuses)
                next_cursor = str(response.next_cursor or paging.get("next_cursor") or "").strip()
                if next_cursor and next_cursor != page_cursor:
                    page_cursor = next_cursor
                    continue
                raw_next_skip = paging.get("next_skip")
                next_skip = raw_next_skip if isinstance(raw_next_skip, int) else None
                if next_skip is not None and next_skip > page_skip:
                    page_skip = next_skip
                    page_cursor = None
                    continue
                if not paging.get("has_more"):
                    break
                if not response.pull_requests:
                    break
                page_skip += len(response.pull_requests)
                page_cursor = None
        return list(candidates.values()), True

    def list_pull_requests(
        self,
        *,
        repo: str,
        project: str | None = None,
        status: str | None = None,
        creator_id: str | None = None,
        reviewer_id: str | None = None,
        source_ref_name: str | None = None,
        target_ref_name: str | None = None,
        source_repository_id: str | None = None,
        repository_id: str | None = None,
        min_time: str | None = None,
        max_time: str | None = None,
        query_time_range_type: str | None = None,
        include_links: bool | None = None,
        max_comment_length: int | None = None,
        tags: str | None = None,
        top: int | None = None,
        skip: int | None = None,
        cursor: str | None = None,
    ) -> ListPullRequestsResponse:
        repo_identifier = _normalize_azure_devops_repo_identifier(repo)
        project_name = _normalize_optional_text(project) or self.project
        tag_filter = _parse_pull_request_tag_expression(tags)
        params: dict[str, Any] = {"api-version": "7.1"}
        for param_name, value in (
            ("searchCriteria.status", status.strip().lower() if status else None),
            ("searchCriteria.creatorId", creator_id),
            ("searchCriteria.reviewerId", reviewer_id),
            ("searchCriteria.sourceRefName", source_ref_name),
            ("searchCriteria.targetRefName", target_ref_name),
            ("searchCriteria.sourceRepositoryId", source_repository_id),
            ("searchCriteria.repositoryId", repository_id),
            ("searchCriteria.minTime", min_time),
            ("searchCriteria.maxTime", max_time),
            ("searchCriteria.queryTimeRangeType", query_time_range_type),
        ):
            text = _normalize_optional_text(value)
            if text:
                params[param_name] = text
        if include_links is not None:
            params["searchCriteria.includeLinks"] = include_links
        if max_comment_length is not None:
            params["maxCommentLength"] = max_comment_length
        if top is not None:
            params["$top"] = top
        if skip is not None:
            params["$skip"] = skip
        if cursor and cursor.strip():
            params["continuationToken"] = cursor.strip()

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                (
                    f"{self.organization_url}/{project_name}/_apis/git/repositories/{repo_identifier}/"
                    "pullrequests"
                ),
                params=params,
                auth=("", self.pat),
            )
            response.raise_for_status()
            payload = response.json()
            raw_items = payload.get("value")
            pr_items = raw_items if isinstance(raw_items, list) else []
            normalized_items = [
                self._normalize_pull_request_item(item, repo=repo)
                for item in pr_items
                if isinstance(item, dict)
            ]
            if tag_filter is not None:
                for item in normalized_items:
                    item.tags = self._list_pr_labels(
                        client=client,
                        repo_identifier=repo_identifier,
                        pr_id=item.pr_id,
                        project=project_name,
                    )

        filtered_items = [
            item
            for item in normalized_items
            if tag_filter is None or _pull_request_tags_match(item.tags, tag_filter=tag_filter)
        ]

        raw_count = payload.get("count")
        candidate_count = len(normalized_items)
        next_cursor_header = response.headers.get("x-ms-continuationtoken")
        next_cursor_payload = payload.get("continuationToken")
        next_cursor = str(next_cursor_header or next_cursor_payload or "").strip() or None
        next_skip = None
        if top is not None and candidate_count >= top:
            next_skip = (skip or 0) + candidate_count
        paging = {
            "cursor": cursor,
            "next_cursor": next_cursor,
            "skip": skip,
            "top": top,
            "next_skip": next_skip,
            "candidate_count": candidate_count,
            "returned_count": len(filtered_items),
            "server_count": raw_count if isinstance(raw_count, int) else None,
            "tag_filter_client_side": tag_filter is not None,
            "has_more": bool(next_cursor or next_skip is not None),
        }
        return ListPullRequestsResponse(
            provider="azure_devops",
            repo=repo,
            project=project_name,
            pull_requests=filtered_items,
            count=len(filtered_items),
            paging=paging,
            next_cursor=next_cursor,
        )

    def _normalize_pull_request_item(self, item: dict[str, Any], *, repo: str) -> PullRequestItem:
        created_by = item.get("createdBy") if isinstance(item.get("createdBy"), dict) else {}
        creation_date = _parse_dt(item["creationDate"]) if item.get("creationDate") else None
        closed_date = _parse_dt(item["closedDate"]) if item.get("closedDate") else None
        return PullRequestItem(
            provider="azure_devops",
            repo=repo,
            pr_id=str(item.get("pullRequestId", "")).strip(),
            title=(str(item.get("title", "")).strip() or None),
            description=(str(item.get("description", "")).strip() or None),
            status=(str(item.get("status", "")).strip() or None),
            source_branch=str(item.get("sourceRefName", "")).strip() or None,
            target_branch=str(item.get("targetRefName", "")).strip() or None,
            created_at=creation_date,
            updated_at=closed_date or creation_date,
            closed_at=closed_date,
            created_by=(
                str(created_by.get("displayName") or created_by.get("uniqueName") or "").strip()
                or None
            ),
            url=_azure_pr_web_url(item),
            raw=item,
        )

    def _list_pr_labels(
        self,
        *,
        client: httpx.Client,
        repo_identifier: str,
        pr_id: str,
        project: str | None = None,
    ) -> list[str]:
        if not pr_id:
            return []
        project_name = _normalize_optional_text(project) or self.project
        response = client.get(
            (
                f"{self.organization_url}/{project_name}/_apis/git/repositories/{repo_identifier}/"
                f"pullRequests/{pr_id}/labels"
            ),
            params={"api-version": "7.1"},
            auth=("", self.pat),
        )
        response.raise_for_status()
        payload = response.json()
        raw_items = payload.get("value")
        label_items = raw_items if isinstance(raw_items, list) else []
        return [
            name
            for name in (
                str(item.get("name", "")).strip() for item in label_items if isinstance(item, dict)
            )
            if name
        ]

    def _add_pr_label(
        self,
        *,
        client: httpx.Client,
        repo_identifier: str,
        pr_id: str,
        tag: str,
        project: str | None = None,
    ) -> None:
        project_name = _normalize_optional_text(project) or self.project
        response = client.post(
            (
                f"{self.organization_url}/{project_name}/_apis/git/repositories/{repo_identifier}/"
                f"pullRequests/{pr_id}/labels"
            ),
            params={"api-version": "7.1"},
            auth=("", self.pat),
            json={"name": tag},
        )
        response.raise_for_status()

    def _remove_pr_label(
        self,
        *,
        client: httpx.Client,
        repo_identifier: str,
        pr_id: str,
        tag: str,
        project: str | None = None,
    ) -> None:
        project_name = _normalize_optional_text(project) or self.project
        response = client.delete(
            (
                f"{self.organization_url}/{project_name}/_apis/git/repositories/{repo_identifier}/"
                f"pullRequests/{pr_id}/labels/{quote(tag, safe='')}"
            ),
            params={"api-version": "7.1"},
            auth=("", self.pat),
        )
        response.raise_for_status()

    def complete_pull_request(
        self, *, repo: str, pr_id: str, expected_source_commit: str,
        expected_target_branch: str,
    ) -> CompletePullRequestResponse:
        from .pr_completion import complete_pull_request

        repository = quote(_normalize_azure_devops_repo_identifier(repo), safe="")
        pr_path = f"git/repositories/{repository}/pullRequests/{positive_id(pr_id, 'pr_id')}"
        with httpx.Client(timeout=self.timeout_seconds) as client:
            def request(method, path, **kwargs):
                params = {"api-version": "7.1", **kwargs.pop("params", {})}
                response = client.request(
                    method, f"{self.organization_url}/{quote(self.project, safe='')}/_apis/{path}",
                    params=params, auth=("", self.pat), **kwargs,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Azure DevOps returned invalid completion data")
                return payload

            result = complete_pull_request(
                request, pr_path=pr_path, expected_source_commit=expected_source_commit,
                expected_target_branch=expected_target_branch,
            )
        return CompletePullRequestResponse(repo=repo, pr_id=pr_id, **result)

    def update_pull_request(
        self,
        *,
        repo: str,
        pr_id: str,
        project: str | None = None,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        add_tags: list[str] | None = None,
        remove_tags: list[str] | None = None,
        expected_statuses: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> UpdatePullRequestResponse:
        if (status or "").strip().lower() == "completed":
            raise ValueError("Use complete_pull_request to complete a pull request")
        repo_identifier = _normalize_azure_devops_repo_identifier(repo)
        project_name = _normalize_optional_text(project) or self.project
        expected = _normalize_string_list(expected_statuses)
        tags_to_add = _normalize_string_list(add_tags)
        tags_to_remove = _normalize_string_list(remove_tags)
        pr_url = (
            f"{self.organization_url}/{project_name}/_apis/git/repositories/{repo_identifier}/"
            f"pullRequests/{pr_id}"
        )

        with httpx.Client(timeout=self.timeout_seconds) as client:
            current_response = client.get(
                pr_url,
                params={"api-version": "7.1"},
                auth=("", self.pat),
            )
            current_response.raise_for_status()
            current_payload = current_response.json()
            current_status = str(current_payload.get("status", "")).strip() or None
            current_tags = self._list_pr_labels(
                client=client,
                repo_identifier=repo_identifier,
                pr_id=pr_id,
                project=project_name,
            )
            if expected and (current_status or "").casefold() not in {
                item.casefold() for item in expected
            }:
                return UpdatePullRequestResponse(
                    success=True,
                    updated=False,
                    skipped=True,
                    skipped_reason="unexpected_status",
                    provider="azure_devops",
                    repo=repo,
                    pr_id=pr_id,
                    status=current_status,
                    title=str(current_payload.get("title", "")).strip() or None,
                    description=_normalize_optional_text(current_payload.get("description")),
                    url=_azure_pr_web_url(current_payload),
                    tags=current_tags,
                    raw=current_payload,
                )

            patch_payload: dict[str, Any] = {}
            if title is not None:
                patch_payload["title"] = _normalize_optional_text(title) or ""
            if description is not None:
                patch_payload["description"] = _normalize_optional_text(description) or ""
            normalized_status = _normalize_optional_text(status)
            if normalized_status:
                patch_payload["status"] = normalized_status.lower()

            uploaded_attachments = _upload_azure_devops_pr_attachments(
                client=client,
                organization_url=self.organization_url,
                project=project_name,
                pat=self.pat,
                repo=repo,
                pr_id=pr_id,
                attachments=attachments,
            )
            if uploaded_attachments:
                if any(not attachment["url"] for attachment in uploaded_attachments):
                    raise ValueError("Azure DevOps did not return an attachment download URL")
                description_text = description
                if description_text is None:
                    # Preserve description edits made while the files were uploading.
                    description_response = client.get(
                        pr_url,
                        params={"api-version": "7.1"},
                        auth=("", self.pat),
                    )
                    description_response.raise_for_status()
                    description_text = description_response.json().get("description")
                patch_payload["description"] = _append_markdown_attachment_links(
                    str(description_text or ""), uploaded_attachments
                )

            latest_payload = current_payload
            if patch_payload:
                patch_response = client.patch(
                    (
                        f"{self.organization_url}/{project_name}/_apis/git/repositories/{repo_identifier}/"
                        f"pullrequests/{pr_id}"
                    ),
                    params={"api-version": "7.1"},
                    auth=("", self.pat),
                    json=patch_payload,
                )
                patch_response.raise_for_status()
                latest_payload = patch_response.json()

            existing_by_key = {tag.casefold(): tag for tag in current_tags}
            added_tags: list[str] = []
            for tag in tags_to_add:
                if tag.casefold() in existing_by_key:
                    continue
                self._add_pr_label(
                    client=client,
                    repo_identifier=repo_identifier,
                    pr_id=pr_id,
                    tag=tag,
                    project=project_name,
                )
                existing_by_key[tag.casefold()] = tag
                added_tags.append(tag)

            removed_tags: list[str] = []
            for tag in tags_to_remove:
                existing_tag = existing_by_key.get(tag.casefold())
                if not existing_tag:
                    continue
                self._remove_pr_label(
                    client=client,
                    repo_identifier=repo_identifier,
                    pr_id=pr_id,
                    tag=existing_tag,
                    project=project_name,
                )
                existing_by_key.pop(tag.casefold(), None)
                removed_tags.append(existing_tag)

            latest_tags = (
                self._list_pr_labels(
                    client=client,
                    repo_identifier=repo_identifier,
                    pr_id=pr_id,
                    project=project_name,
                )
                if added_tags or removed_tags
                else current_tags
            )

        return UpdatePullRequestResponse(
            success=True,
            updated=bool(patch_payload or added_tags or removed_tags),
            skipped=False,
            skipped_reason=None,
            provider="azure_devops",
            repo=repo,
            pr_id=pr_id,
            status=str(latest_payload.get("status", "")).strip() or current_status,
            title=str(latest_payload.get("title", "")).strip()
            or str(current_payload.get("title", "")).strip()
            or None,
            description=(
                _normalize_optional_text(latest_payload.get("description"))
                if "description" in latest_payload
                else _normalize_optional_text(patch_payload["description"])
                if "description" in patch_payload
                else _normalize_optional_text(current_payload.get("description"))
            ),
            url=_azure_pr_web_url(latest_payload) or _azure_pr_web_url(current_payload),
            tags=latest_tags,
            added_tags=added_tags,
            removed_tags=removed_tags,
            uploaded_attachments=uploaded_attachments,
            raw=latest_payload if isinstance(latest_payload, dict) else {},
        )

    def get_pull_request(
        self,
        *,
        repo: str,
        pr_id: str,
        include_reviewers: bool | None = None,
        include_commits: bool | None = None,
    ) -> GetPullRequestResponse:
        repo_identifier = _normalize_azure_devops_repo_identifier(repo)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(
                (
                    f"{self.organization_url}/{self.project}/_apis/git/repositories/{repo_identifier}/"
                    f"pullRequests/{pr_id}"
                ),
                params={"api-version": "7.1"},
                auth=("", self.pat),
            )
            response.raise_for_status()
            payload = response.json()

            commits_payload: list[dict[str, Any]] = []
            if include_commits:
                commits_response = client.get(
                    (
                        f"{self.organization_url}/{self.project}/_apis/git/repositories/{repo_identifier}/"
                        f"pullRequests/{pr_id}/commits"
                    ),
                    params={"api-version": "7.1"},
                    auth=("", self.pat),
                )
                commits_response.raise_for_status()
                commits_raw = commits_response.json()
                raw_items = commits_raw.get("value")
                if isinstance(raw_items, list):
                    commits_payload = [item for item in raw_items if isinstance(item, dict)]

        created_by = payload.get("createdBy") if isinstance(payload.get("createdBy"), dict) else {}
        raw_payload = payload if isinstance(payload, dict) else {}
        if include_commits:
            raw_payload = {**raw_payload, "commits": commits_payload}

        reviewer_items = payload.get("reviewers")
        reviewer_payload = reviewer_items if isinstance(reviewer_items, list) else []
        normalized_reviewers = (
            [
                PullRequestReviewer(
                    reviewer_id=str(item.get("id", "")).strip() or None,
                    display_name=str(item.get("displayName", "")).strip() or None,
                    vote=int(item["vote"]) if isinstance(item.get("vote"), int) else None,
                    status=str(item.get("vote", "")).strip() or None,
                    is_required=item.get("isRequired")
                    if isinstance(item.get("isRequired"), bool)
                    else None,
                )
                for item in reviewer_payload
                if isinstance(item, dict)
            ]
            if include_reviewers is not False
            else []
        )
        normalized_commits = [
            PullRequestCommit(
                commit_id=str(item.get("commitId", "")).strip(),
                url=str(item.get("url", "")).strip() or None,
                comment=str(item.get("comment", "")).strip() or None,
            )
            for item in commits_payload
            if str(item.get("commitId", "")).strip()
        ]
        return GetPullRequestResponse(
            provider="azure_devops",
            repo=repo,
            pr_id=pr_id,
            title=str(payload.get("title", "")).strip() or None,
            status=str(payload.get("status", "")).strip() or None,
            url=(
                str(payload.get("_links", {}).get("web", {}).get("href") or "").strip()
                or str(payload.get("remoteUrl") or payload.get("url") or "").strip()
            ),
            source_branch=str(payload.get("sourceRefName", "")).strip() or None,
            target_branch=str(payload.get("targetRefName", "")).strip() or None,
            source_commit=str(payload.get("lastMergeSourceCommit", {}).get("commitId", "")).strip()
            or None,
            target_commit=str(payload.get("lastMergeTargetCommit", {}).get("commitId", "")).strip()
            or None,
            created_by=(
                str(created_by.get("displayName") or created_by.get("uniqueName") or "").strip()
                or None
            ),
            reviewers=normalized_reviewers,
            commits=normalized_commits,
            raw=raw_payload,
        )

    def get_pull_request_work_items(
        self,
        *,
        repo: str,
        pr_id: str,
    ) -> PullRequestWorkItemsResponse:
        """Return the Azure DevOps work items associated with a pull request.

        Azure DevOps returns work-item references from the Git API. Fetch the
        lightweight work-item records separately so workflow configuration can
        use the assignee's normalized name and email without exposing the PAT
        to an agent.
        """

        repo_identifier = _normalize_azure_devops_repo_identifier(repo)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            references_response = client.get(
                (
                    f"{self.organization_url}/{self.project}/_apis/git/repositories/"
                    f"{repo_identifier}/pullRequests/{pr_id}/workitems"
                ),
                params={"api-version": "7.1"},
                auth=("", self.pat),
            )
            references_response.raise_for_status()
            references_payload = references_response.json()

            raw_references = (
                references_payload.get("value") if isinstance(references_payload, dict) else None
            )
            references = [item for item in raw_references or [] if isinstance(item, dict)]
            numeric_ids = _unique_positive_ints(item.get("id") for item in references)
            details_by_id = self._get_work_item_details(client=client, work_item_ids=numeric_ids)

        work_items = [
            _pull_request_work_item_from_reference(
                reference=reference,
                details=details_by_id.get(str(reference.get("id") or "").strip()),
            )
            for reference in references
            if str(reference.get("id") or "").strip()
        ]
        return PullRequestWorkItemsResponse(
            provider="azure_devops",
            repo=repo,
            pr_id=pr_id,
            work_items=work_items,
            count=len(work_items),
            raw={"references": references},
        )

    def _get_work_item_details(
        self,
        *,
        client: httpx.Client,
        work_item_ids: list[int],
    ) -> dict[str, dict[str, Any]]:
        details_by_id: dict[str, dict[str, Any]] = {}
        for start in range(0, len(work_item_ids), 200):
            response = client.post(
                f"{self.organization_url}/_apis/wit/workitemsbatch",
                params={"api-version": "7.1"},
                headers={"Content-Type": "application/json"},
                auth=("", self.pat),
                json={
                    "ids": work_item_ids[start : start + 200],
                    "fields": ["System.Id", "System.Title", "System.AssignedTo"],
                    "errorPolicy": "Omit",
                },
            )
            response.raise_for_status()
            payload = response.json()
            values = payload.get("value") if isinstance(payload, dict) else None
            for item in values or []:
                if not isinstance(item, dict):
                    continue
                work_item_id = str(item.get("id") or "").strip()
                if work_item_id:
                    details_by_id[work_item_id] = item
        return details_by_id


class AzureDevOpsPrPublishProvider:
    def __init__(
        self,
        *,
        organization_url: str,
        project: str,
        pat: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.organization_url = organization_url.rstrip("/")
        self.project = project
        self.pat = pat
        self.timeout_seconds = timeout_seconds

    def _repo_identifier(self, repo: str) -> str:
        return _normalize_azure_devops_repo_identifier(repo)

    def _comment_thread_url(self, *, repo: str, pr_id: str, thread_id: str | None) -> str:
        if not self.organization_url or not self.project.strip():
            raise ValueError("Azure DevOps PR comment operations require organization URL and project")
        pr_id = str(positive_id(pr_id.strip(), "pr_id"))
        thread_id = str(positive_id(thread_id.strip() if thread_id else None, "thread_id"))
        repository_id = self._repo_identifier(repo)
        if not repository_id or repository_id in {".", ".."}:
            raise ValueError("Azure DevOps PR comment operations require repo")
        return (
            f"{self.organization_url}/{quote(self.project, safe='')}/_apis/git/repositories/"
            f"{quote(repository_id, safe='')}/pullRequests/{pr_id}/threads/{thread_id}"
        )

    def delete_comment(
        self,
        *,
        repo: str,
        pr_id: str,
        comment_id: str,
        thread_id: str | None = None,
        feedback_kind: str | None = None,
    ) -> dict[str, Any]:
        if feedback_kind not in {None, "thread_comment"}:
            raise ValueError("Azure DevOps PR comments require feedback_kind=thread_comment")
        comment_id = str(positive_id(comment_id.strip(), "comment_id"))
        url = self._comment_thread_url(repo=repo, pr_id=pr_id, thread_id=thread_id)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.delete(
                f"{url}/comments/{comment_id}", params={"api-version": "7.1"}, auth=("", self.pat)
            )
            _raise_azure_devops_pr_publish_error(response, action="delete PR comment")
        return {
            "status": "deleted",
            "provider": "azure_devops",
            "repo": repo,
            "pr_id": str(positive_id(pr_id.strip(), "pr_id")),
            "thread_id": str(positive_id(thread_id.strip(), "thread_id")),
            "comment_id": comment_id,
            "feedback_kind": "thread_comment",
            "deleted": True,
        }

    def resolve_comment(
        self,
        *,
        repo: str,
        pr_id: str,
        thread_id: str | None = None,
        comment_id: str | None = None,
    ) -> dict[str, Any]:
        if comment_id is not None:
            raise ValueError("Azure DevOps PR resolution requires thread_id without comment_id")
        url = self._comment_thread_url(repo=repo, pr_id=pr_id, thread_id=thread_id)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.patch(
                url,
                params={"api-version": "7.1"},
                auth=("", self.pat),
                json={"status": "fixed"},
            )
            _raise_azure_devops_pr_publish_error(response, action="resolve PR comment thread")
            payload = response.json()
        normalized_thread_id = str(positive_id(thread_id.strip(), "thread_id"))
        if (
            str(payload.get("id")) != normalized_thread_id
            or payload.get("status") not in {"fixed", 2}
            or payload.get("isDeleted") is True
        ):
            raise ValueError("Azure DevOps did not confirm PR comment thread resolution")
        return {
            "status": "resolved",
            "provider": "azure_devops",
            "repo": repo,
            "pr_id": str(positive_id(pr_id.strip(), "pr_id")),
            "thread_id": normalized_thread_id,
            "thread_status": "fixed",
            "resolved": True,
        }

    def _post_thread_comment(
        self,
        *,
        client: httpx.Client,
        repo: str,
        pr_id: str,
        body_text: str,
        mentions: list[str] | None = None,
        resolved_mentions: list[AdoResolvedMention] | None = None,
        thread_context: dict[str, Any] | None = None,
        pull_request_thread_context: dict[str, Any] | None = None,
        error_context: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if resolved_mentions is None:
            resolved_mentions = resolve_ado_mentions(
                client=client,
                organization_url=self.organization_url,
                pat=self.pat,
                mention_refs=mentions,
            )
        uploaded_attachments = _upload_azure_devops_pr_attachments(
            client=client,
            organization_url=self.organization_url,
            project=self.project,
            pat=self.pat,
            repo=repo,
            pr_id=pr_id,
            attachments=attachments,
        )
        body_with_mentions = render_ado_mentions_prefix(body_text, resolved_mentions)
        content = _append_markdown_attachment_links(body_with_mentions, uploaded_attachments)
        payload: dict[str, Any] = {
            "comments": [{"parentCommentId": 0, "content": content.strip(), "commentType": "text"}],
            "status": "active",
        }
        if thread_context is not None:
            payload["threadContext"] = thread_context
        if pull_request_thread_context is not None:
            payload["pullRequestThreadContext"] = pull_request_thread_context
        response = client.post(
            (
                f"{self.organization_url}/{self.project}/_apis/git/repositories/{self._repo_identifier(repo)}/"
                f"pullRequests/{pr_id}/threads"
            ),
            params={"api-version": "7.1"},
            auth=("", self.pat),
            json=payload,
        )
        _raise_azure_devops_pr_publish_error(
            response,
            action="post PR thread comment",
            context=error_context,
        )
        response_payload = response.json()
        thread_id = str(response_payload.get("id", "")).strip() or None
        comments = (
            response_payload.get("comments")
            if isinstance(response_payload.get("comments"), list)
            else []
        )
        first_comment = comments[0] if comments and isinstance(comments[0], dict) else {}
        comment_id = str(first_comment.get("id", "")).strip() or None
        comment_url = response_payload.get("_links", {}).get("web", {}).get("href")
        published_feedback_ids = []
        if thread_id and comment_id:
            published_feedback_ids.append(f"azure_devops:thread_comment:{thread_id}:{comment_id}")
        return {
            "thread_id": thread_id,
            "comment_id": comment_id,
            "comment_url": comment_url,
            "published_feedback_ids": published_feedback_ids,
            "uploaded_attachments": uploaded_attachments,
            "resolved_mentions": _ado_resolved_mention_payloads(resolved_mentions),
        }

    def post_comment(
        self,
        *,
        repo: str,
        pr_id: str,
        body_text: str,
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        normalized_body = body_text.strip()
        if not normalized_body:
            raise ValueError("Azure DevOps PR comment publishing requires body_text")
        with httpx.Client(timeout=self.timeout_seconds) as client:
            result = self._post_thread_comment(
                client=client,
                repo=repo,
                pr_id=pr_id,
                body_text=normalized_body,
                mentions=mentions,
                error_context="top-level PR comment",
                attachments=attachments,
            )
        return {
            "status": "posted",
            "provider": "azure_devops",
            "repo": repo,
            "pr_id": pr_id,
            **result,
        }

    def submit_review(
        self,
        *,
        repo: str,
        pr_id: str,
        body_text: str,
        comments: list[dict[str, Any]] | None = None,
        event: str | None = None,
        mentions: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        normalized_event = str(event or "COMMENT").strip().upper() or "COMMENT"
        if normalized_event != "COMMENT" and normalized_event not in _AZURE_DEVOPS_REVIEW_VOTES:
            raise ValueError(
                "Azure DevOps PR review event must be one of: COMMENT, "
                + ", ".join(_AZURE_DEVOPS_REVIEW_VOTES)
            )
        vote = _AZURE_DEVOPS_REVIEW_VOTES.get(normalized_event)
        normalized_body = body_text.strip()
        normalized_comments = _normalize_pr_review_comments(comments)
        if not normalized_body and not normalized_comments:
            raise ValueError("Azure DevOps PR review publishing requires body_text or comments")

        comment_ids: list[str] = []
        comment_urls: list[str] = []
        published_feedback_ids: list[str] = []
        summary_thread_id: str | None = None
        summary_comment_id: str | None = None
        summary_comment_url: str | None = None
        summary_resolved_mentions: list[dict[str, str]] = []
        inline_comment_ids: list[str] = []
        inline_comment_urls: list[str] = []
        inline_published_feedback_ids: list[str] = []
        inline_resolved_mentions: list[list[dict[str, str]]] = []
        uploaded_attachments: list[dict[str, Any]] = []

        with httpx.Client(timeout=self.timeout_seconds) as client:
            reviewer_id = None
            reviewer_url = None
            if vote is not None:
                reviewer_id, reviewer_url = self._reviewer_vote_target(
                    client=client, repo=repo, pr_id=pr_id
                )
            resolved_summary_mentions = resolve_ado_mentions(
                client=client,
                organization_url=self.organization_url,
                pat=self.pat,
                mention_refs=mentions,
            )
            summary_resolved_mentions = _ado_resolved_mention_payloads(resolved_summary_mentions)
            iteration_context = (
                _azure_devops_pr_iteration_context(
                    client=client,
                    organization_url=self.organization_url,
                    project=self.project,
                    repo_identifier=self._repo_identifier(repo),
                    pr_id=pr_id,
                    pat=self.pat,
                )
                if normalized_comments
                else None
            )
            total_inline_comments = len(normalized_comments)
            prepared_inline_comments: list[
                tuple[
                    dict[str, Any],
                    dict[str, Any],
                    dict[str, Any] | None,
                    str,
                    list[AdoResolvedMention],
                ]
            ] = []
            for index, comment in enumerate(normalized_comments, start=1):
                resolved_comment_mentions = resolve_ado_mentions(
                    client=client,
                    organization_url=self.organization_url,
                    pat=self.pat,
                    mention_refs=comment.get("mentions"),
                )
                thread_context = _azure_devops_thread_context_for_review_comment(comment)
                pull_request_thread_context = (
                    _azure_devops_pull_request_thread_context_for_review_comment(
                        comment,
                        iteration_context=iteration_context,
                    )
                    if iteration_context is not None
                    else None
                )
                error_context = _azure_devops_inline_comment_error_context(
                    comment,
                    index=index,
                    total=total_inline_comments,
                )
                prepared_inline_comments.append(
                    (
                        comment,
                        thread_context,
                        pull_request_thread_context,
                        error_context,
                        resolved_comment_mentions,
                    )
                )

            for (
                comment,
                thread_context,
                pull_request_thread_context,
                error_context,
                resolved_comment_mentions,
            ) in prepared_inline_comments:
                try:
                    comment_result = self._post_thread_comment(
                        client=client,
                        repo=repo,
                        pr_id=pr_id,
                        body_text=comment["body_text"],
                        resolved_mentions=resolved_comment_mentions,
                        thread_context=thread_context,
                        pull_request_thread_context=pull_request_thread_context,
                        error_context=error_context,
                        attachments=comment.get("attachments"),
                    )
                except httpx.HTTPStatusError as exc:
                    posted_count = len(inline_comment_ids)
                    message = (
                        f"{exc}; Azure DevOps submit_review stopped at {error_context}. "
                        f"Posted {posted_count} of {total_inline_comments} inline comment(s); "
                        "summary comment was not posted."
                    )
                    raise httpx.HTTPStatusError(
                        message,
                        request=exc.request,
                        response=exc.response,
                    ) from exc
                if comment_result["comment_id"] is not None:
                    inline_comment_ids.append(comment_result["comment_id"])
                if comment_result["comment_url"]:
                    inline_comment_urls.append(comment_result["comment_url"])
                inline_published_feedback_ids.extend(comment_result["published_feedback_ids"])
                inline_resolved_mentions.append(list(comment_result.get("resolved_mentions") or []))
                uploaded_attachments.extend(comment_result.get("uploaded_attachments") or [])

            if normalized_body:
                summary_result = self._post_thread_comment(
                    client=client,
                    repo=repo,
                    pr_id=pr_id,
                    body_text=normalized_body,
                    resolved_mentions=resolved_summary_mentions,
                    error_context="review summary comment",
                    attachments=attachments,
                )
                summary_thread_id = summary_result["thread_id"]
                summary_comment_id = summary_result["comment_id"]
                summary_comment_url = summary_result["comment_url"]
                if summary_comment_id is not None:
                    comment_ids.append(summary_comment_id)
                if summary_comment_url:
                    comment_urls.append(summary_comment_url)
                published_feedback_ids.extend(summary_result["published_feedback_ids"])
                summary_resolved_mentions = list(summary_result.get("resolved_mentions") or [])
                uploaded_attachments.extend(summary_result.get("uploaded_attachments") or [])

            if vote is not None:
                vote_request = {"id": reviewer_id, "vote": vote}
                if vote > 0:
                    vote_request["isReapprove"] = True
                vote_response = client.put(
                    reviewer_url,
                    params={"api-version": "7.1"},
                    auth=("", self.pat),
                    json=vote_request,
                )
                vote_response.raise_for_status()
                vote_payload = vote_response.json()
                if (
                    not isinstance(vote_payload, dict)
                    or str(vote_payload.get("id") or "").casefold() != reviewer_id.casefold()
                    or type(vote_payload.get("vote")) is not int
                    or vote_payload["vote"] != vote
                ):
                    raise ValueError("Azure DevOps did not confirm the requested reviewer vote")

            comment_ids.extend(inline_comment_ids)
            comment_urls.extend(inline_comment_urls)
            published_feedback_ids.extend(inline_published_feedback_ids)

        review_url = summary_comment_url or (comment_urls[0] if comment_urls else None)
        result = {
            "status": "posted",
            "provider": "azure_devops",
            "repo": repo,
            "pr_id": pr_id,
            "event": event.strip().upper() if isinstance(event, str) and event.strip() else None,
            "review_id": None,
            "review_url": review_url,
            "summary_thread_id": summary_thread_id,
            "summary_comment_id": summary_comment_id,
            "summary_comment_url": summary_comment_url,
            "resolved_mentions": summary_resolved_mentions,
            "inline_resolved_mentions": inline_resolved_mentions,
            "comment_ids": comment_ids,
            "comment_urls": comment_urls,
            "published_feedback_ids": published_feedback_ids,
            "uploaded_attachments": uploaded_attachments,
        }
        if vote is not None:
            result.update({"reviewer_id": reviewer_id, "vote": vote})
        return result

    def _reviewer_vote_target(
        self, *, client: httpx.Client, repo: str, pr_id: str
    ) -> tuple[str, str]:
        response = client.get(
            f"{self.organization_url}/_apis/connectionData",
            params={"api-version": "7.1-preview.1"},
            auth=("", self.pat),
        )
        response.raise_for_status()
        payload = response.json()
        identity = payload.get("authenticatedUser") if isinstance(payload, dict) else None
        reviewer_id = (
            _normalize_optional_text(identity.get("id")) if isinstance(identity, dict) else None
        )
        if not reviewer_id:
            raise ValueError("Azure DevOps did not return an authenticated reviewer identity")
        reviewer_url = (
            f"{self.organization_url}/{self.project}/_apis/git/repositories/"
            f"{self._repo_identifier(repo)}/pullRequests/{pr_id}/reviewers/"
            f"{quote(reviewer_id, safe='')}"
        )
        response = client.get(reviewer_url, params={"api-version": "7.1"}, auth=("", self.pat))
        if response.status_code == 404:
            raise ValueError(
                "The authenticated Azure DevOps identity must already be a PR reviewer"
            )
        response.raise_for_status()
        reviewer = response.json()
        if (
            not isinstance(reviewer, dict)
            or str(reviewer.get("id") or "").casefold() != reviewer_id.casefold()
        ):
            raise ValueError("Azure DevOps did not confirm the assigned reviewer identity")
        return reviewer_id, reviewer_url


def _ado_resolved_mention_payloads(
    mentions: list[AdoResolvedMention],
) -> list[dict[str, str]]:
    return [mention.audit_payload() for mention in mentions]


@dataclass(frozen=True)
class _AzureDevOpsPrChange:
    change_tracking_id: int
    change_type: str
    current_paths: tuple[str, ...]
    original_paths: tuple[str, ...]


@dataclass(frozen=True)
class _AzureDevOpsPrIterationContext:
    first_iteration_id: int
    latest_iteration_id: int
    changes: tuple[_AzureDevOpsPrChange, ...]


def _azure_devops_thread_context_for_review_comment(comment: dict[str, Any]) -> dict[str, Any]:
    thread_context: dict[str, Any] = {"filePath": _azure_devops_comment_file_path(comment["path"])}
    line = comment.get("line")
    if line is None:
        return thread_context

    side = str(comment.get("side") or "RIGHT").strip().upper()
    position_prefix = "leftFile" if side == "LEFT" else "rightFile"
    start_line = comment.get("start_line") or line
    thread_context[f"{position_prefix}Start"] = {"line": start_line, "offset": 1}
    thread_context[f"{position_prefix}End"] = {"line": line, "offset": 1}
    return thread_context


def _azure_devops_pr_iteration_context(
    *,
    client: httpx.Client,
    organization_url: str,
    project: str,
    repo_identifier: str,
    pr_id: str,
    pat: str,
) -> _AzureDevOpsPrIterationContext:
    base_url = (
        f"{organization_url}/{project}/_apis/git/repositories/{repo_identifier}/"
        f"pullRequests/{pr_id}/iterations"
    )
    response = client.get(
        base_url,
        params={"api-version": "7.1"},
        auth=("", pat),
    )
    _raise_azure_devops_pr_publish_error(
        response,
        action="resolve PR iterations for inline comment anchoring",
        context=f"pr_id={pr_id}",
    )
    payload = response.json()
    raw_iterations = payload.get("value") if isinstance(payload, dict) else payload
    if not isinstance(raw_iterations, list):
        raw_iterations = []
    iteration_ids = sorted(
        {
            iteration_id
            for item in raw_iterations
            if isinstance(item, dict)
            for iteration_id in [_as_int_or_none(item.get("id"))]
            if iteration_id is not None and iteration_id > 0
        }
    )
    if not iteration_ids:
        raise ValueError(
            f"Azure DevOps PR {pr_id} has no iterations; cannot anchor inline comments"
        )

    latest_iteration_id = iteration_ids[-1]
    changes: list[_AzureDevOpsPrChange] = []
    skip = 0
    while True:
        params: dict[str, Any] = {
            "api-version": "7.1",
            "$top": 2000,
        }
        if skip:
            params["$skip"] = skip
        changes_response = client.get(
            f"{base_url}/{latest_iteration_id}/changes",
            params=params,
            auth=("", pat),
        )
        _raise_azure_devops_pr_publish_error(
            changes_response,
            action="resolve PR iteration changes for inline comment anchoring",
            context=f"pr_id={pr_id} iteration={latest_iteration_id}",
        )
        changes_payload = changes_response.json()
        raw_changes = (
            changes_payload.get("changeEntries") or changes_payload.get("value")
            if isinstance(changes_payload, dict)
            else []
        )
        if not isinstance(raw_changes, list):
            raw_changes = []
        for raw_change in raw_changes:
            change = _azure_devops_pr_change_from_payload(raw_change)
            if change is not None:
                changes.append(change)
        next_skip = (
            _as_int_or_none(changes_payload.get("nextSkip"))
            if isinstance(changes_payload, dict)
            else None
        )
        if not next_skip:
            break
        skip = next_skip

    if not changes:
        raise ValueError(
            f"Azure DevOps PR {pr_id} latest iteration {latest_iteration_id} has no changes; "
            "cannot anchor inline comments"
        )

    return _AzureDevOpsPrIterationContext(
        first_iteration_id=iteration_ids[0],
        latest_iteration_id=latest_iteration_id,
        changes=tuple(changes),
    )


def _azure_devops_pr_change_from_payload(raw_change: Any) -> _AzureDevOpsPrChange | None:
    if not isinstance(raw_change, dict):
        return None
    change_tracking_id = _as_int_or_none(raw_change.get("changeTrackingId"))
    if change_tracking_id is None:
        return None
    current_paths = []
    item = raw_change.get("item")
    if isinstance(item, dict):
        current_paths.append(item.get("path"))
    current_paths.append(raw_change.get("sourceServerItem"))
    original_paths = [raw_change.get("originalPath")]
    normalized_current_paths = tuple(
        dict.fromkeys(
            path
            for raw_path in current_paths
            for path in [_normalize_azure_devops_pr_path(raw_path)]
            if path is not None
        )
    )
    normalized_original_paths = tuple(
        dict.fromkeys(
            path
            for raw_path in original_paths
            for path in [_normalize_azure_devops_pr_path(raw_path)]
            if path is not None
        )
    )
    if not normalized_current_paths and not normalized_original_paths:
        return None
    change_type = str(raw_change.get("changeType") or "").strip().lower()
    return _AzureDevOpsPrChange(
        change_tracking_id=change_tracking_id,
        change_type=change_type,
        current_paths=normalized_current_paths,
        original_paths=normalized_original_paths,
    )


def _azure_devops_pull_request_thread_context_for_review_comment(
    comment: dict[str, Any],
    *,
    iteration_context: _AzureDevOpsPrIterationContext,
) -> dict[str, Any]:
    path = _normalize_azure_devops_pr_path(comment["path"])
    if path is None:
        raise ValueError("Azure DevOps inline comment requires a file path")
    side = str(comment.get("side") or "RIGHT").strip().upper()
    matching_change = next(
        (
            change
            for change in iteration_context.changes
            if path
            in (
                (*change.original_paths, *change.current_paths)
                if side == "LEFT"
                else change.current_paths
            )
        ),
        None,
    )
    if matching_change is None:
        raise ValueError(
            f"Azure DevOps inline comment path {comment['path']!r} is not present in the latest "
            f"PR iteration {iteration_context.latest_iteration_id}; refusing to post a detached "
            "inline comment"
        )

    change_types = {
        part.strip()
        for part in matching_change.change_type.replace(";", ",").split(",")
        if part.strip()
    }
    if side != "LEFT" and "delete" in change_types:
        raise ValueError(
            f"Azure DevOps inline comment path {comment['path']!r} is deleted in the latest "
            "PR iteration; use side=LEFT or a top-level comment instead"
        )

    return {
        "changeTrackingId": matching_change.change_tracking_id,
        "iterationContext": {
            "firstComparingIteration": iteration_context.first_iteration_id,
            "secondComparingIteration": iteration_context.latest_iteration_id,
        },
    }


def _azure_devops_comment_file_path(path: Any) -> str:
    normalized = _normalize_azure_devops_pr_path(path)
    if normalized is None:
        raise ValueError("Azure DevOps inline comment requires a file path")
    return normalized


def _azure_devops_inline_comment_error_context(
    comment: dict[str, Any],
    *,
    index: int,
    total: int,
) -> str:
    parts = [f"inline comment {index}/{total}", f"path={comment['path']}"]
    line = comment.get("line")
    if line is not None:
        parts.append(f"line={line}")
    start_line = comment.get("start_line")
    if start_line is not None and start_line != line:
        parts.append(f"start_line={start_line}")
    side = str(comment.get("side") or "").strip()
    if side:
        parts.append(f"side={side}")
    return " ".join(parts)


def _azure_devops_error_details(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
        return text[:1000] if text else ""

    if not isinstance(payload, dict):
        return ""

    error = payload.get("error")
    if isinstance(error, dict):
        parts: list[str] = []
        code = str(error.get("code") or "").strip()
        message = str(error.get("message") or "").strip()
        if code:
            parts.append(f"code={code}")
        if message:
            parts.append(f"message={message}")
        if parts:
            return ", ".join(parts)

    parts = []
    message = str(payload.get("message") or "").strip()
    type_key = str(payload.get("typeKey") or "").strip()
    type_name = str(payload.get("typeName") or "").strip()
    error_code = str(payload.get("errorCode") or "").strip()
    event_id = str(payload.get("eventId") or "").strip()
    if message:
        parts.append(f"message={message}")
    if type_key:
        parts.append(f"typeKey={type_key}")
    elif type_name:
        parts.append(f"typeName={type_name}")
    if error_code:
        parts.append(f"errorCode={error_code}")
    if event_id:
        parts.append(f"eventId={event_id}")
    if parts:
        return ", ".join(parts)
    text = response.text.strip()
    return text[:1000] if text else ""
