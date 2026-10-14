from __future__ import annotations
import base64
from typing import Any, Callable
from urllib.parse import urlparse
import httpx
from evander.inline_media import (
    InlineMediaDownload,
    inline_media_download_from_embedded_payload,
)
from evander.models import InlineMediaRef
from evander.pr_feedback.identity import (
    normalize_azure_devops_repo_identifier as _normalize_azure_devops_repo_identifier,
)
from evander.pr_feedback.store import (
    FeedbackEnvelope,
    FeedbackProviderAuthError,
    FeedbackProviderConfigurationError,
    ProviderPollResult,
    _latest_cursor,
    _parse_dt,
)

from evander.pr_feedback.providers import (
    ADO_PR_FEEDBACK_ATTACHMENT_SCOPE_KEY,
    _ado_comment_metadata,
    _ado_scope_identifiers,
    require_ado_pr_feedback_attachment_url,
    _extract_ado_inline_media,
    _inline_media_refs_from_resource,
    _download_url_with_optional_headers,
    _ado_should_skip_comment,
    _ado_thread_resolved,
    _ado_line_from_context,
 )

class AzureDevOpsFeedbackAdapter:
    provider_name = "azure_devops"

    def __init__(
        self,
        organization_url: str,
        project: str,
        pat: str | None = None,
        *,
        pat_env_name: str = "AZURE_DEVOPS_PR_PAT",
    ) -> None:
        self.organization_url = organization_url.rstrip("/")
        self.project = project
        self.pat = pat.strip() if pat else None
        self.pat_env_name = pat_env_name.strip() or "AZURE_DEVOPS_PR_PAT"

    def _headers(self) -> dict[str, str]:
        if not self.pat:
            return {}
        token = base64.b64encode(f":{self.pat}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    def list_inline_media(
        self,
        *,
        source_kind: str,
        resource_id: str,
        resource: dict[str, Any] | None = None,
        comment_id: str | None = None,
    ) -> list[InlineMediaRef]:
        return _inline_media_refs_from_resource(
            source_kind=source_kind,
            provider=self.provider_name,
            resource_id=resource_id,
            resource=resource,
            comment_id=comment_id,
        )

    def download_inline_media(
        self,
        *,
        media: InlineMediaRef,
        raw: dict[str, Any] | None = None,
        max_bytes: int | None = None,
    ) -> InlineMediaDownload:
        if not self.pat:
            raise FeedbackProviderConfigurationError(
                "Azure DevOps inline media downloads require a PAT in "
                f"environment variable {self.pat_env_name!r}."
            )
        embedded = inline_media_download_from_embedded_payload(media, raw)
        if embedded is not None:
            return embedded
        if not media.url:
            raise ValueError("Azure DevOps inline media does not include a download URL")
        allowed_host = urlparse(self.organization_url).hostname
        url_validator: Callable[[str], None] | None = None
        if media.source_kind in {"pr_feedback", "pr_feedback_attachment"}:
            scope = media.metadata.get(ADO_PR_FEEDBACK_ATTACHMENT_SCOPE_KEY)
            if not isinstance(scope, dict):
                raise ValueError("Azure DevOps PR feedback media is missing attachment scope")
            scope_repo = str(scope.get("repo") or "").strip()
            scope_pr_id = str(scope.get("pr_id") or "").strip()
            if not scope_repo or not scope_pr_id:
                raise ValueError("Azure DevOps PR feedback media has incomplete attachment scope")

            def validate_scoped_url(candidate_url: str) -> None:
                require_ado_pr_feedback_attachment_url(
                    url=candidate_url,
                    organization_url=self.organization_url,
                    project=self.project,
                    repo=scope_repo,
                    pr_id=scope_pr_id,
                    project_identifiers=_ado_scope_identifiers(scope.get("project_identifiers")),
                    repository_identifiers=_ado_scope_identifiers(
                        scope.get("repository_identifiers")
                    ),
                )

            url_validator = validate_scoped_url
        content, mime_type = _download_url_with_optional_headers(
            url=media.url,
            headers=self._headers(),
            max_bytes=max_bytes,
            provider=self.provider_name,
            allowed_hosts=[allowed_host] if allowed_host else [],
            url_validator=url_validator,
        )
        return InlineMediaDownload(content, media.filename, mime_type or media.mime_type)

    def poll(
        self,
        *,
        client: httpx.Client,
        repo: str,
        pr_id: str,
        cursor: str | None,
    ) -> ProviderPollResult:
        if not self.pat:
            raise FeedbackProviderConfigurationError(
                "Azure DevOps feedback polling requires a PAT in "
                f"environment variable {self.pat_env_name!r}."
            )
        repo_identifier = _normalize_azure_devops_repo_identifier(repo)
        url = (
            f"{self.organization_url}/{self.project}/_apis/git/repositories/{repo_identifier}/"
            f"pullRequests/{pr_id}/threads"
        )
        response = client.get(url, params={"api-version": "7.1"}, headers=self._headers())
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                raise FeedbackProviderAuthError(
                    "Azure DevOps feedback polling was rejected with 401 Unauthorized. "
                    f"Check environment variable {self.pat_env_name!r}."
                ) from exc
            raise
        payload = response.json()
        threads = payload.get("value", []) if isinstance(payload, dict) else []
        cursor_dt = _parse_dt(cursor) if cursor else None

        feedback: list[FeedbackEnvelope] = []
        seen_timestamps: list[str] = []
        for thread in threads:
            thread_id = str(thread.get("id"))
            resolved = _ado_thread_resolved(thread.get("status"))
            thread_context = (
                thread.get("threadContext") if isinstance(thread.get("threadContext"), dict) else {}
            )
            file_path = thread_context.get("filePath")
            line = _ado_line_from_context(thread_context)
            raw_comments = [
                comment for comment in thread.get("comments", []) if isinstance(comment, dict)
            ]
            for comment_index, comment in enumerate(raw_comments):
                if comment.get("isDeleted"):
                    continue
                created_at = _parse_dt(
                    comment.get("publishedDate") or comment.get("lastUpdatedDate")
                )
                if cursor_dt and created_at <= cursor_dt:
                    continue
                seen_timestamps.append(created_at.isoformat())
                comment_id = str(comment.get("id"))
                parent_comment_id = comment.get("parentCommentId")
                in_reply_to = (
                    str(parent_comment_id)
                    if isinstance(parent_comment_id, int) and parent_comment_id > 0
                    else None
                )
                author = (comment.get("author", {}).get("displayName") or "unknown").strip()
                body = (comment.get("content") or "").strip()
                feedback_id = f"azure_devops:thread_comment:{thread_id}:{comment_id}"
                if _ado_should_skip_comment(
                    author=author,
                    body=body,
                    comment_type=comment.get("commentType"),
                    file_path=file_path,
                ):
                    continue
                feedback.append(
                    FeedbackEnvelope(
                        feedback_id=feedback_id,
                        provider="azure_devops",
                        repo=repo,
                        pr_id=str(pr_id),
                        thread_id=thread_id,
                        comment_id=comment_id,
                        author=author,
                        body=body,
                        file_path=file_path,
                        line=line,
                        created_at=created_at,
                        in_reply_to=in_reply_to,
                        resolved=resolved,
                        metadata=_ado_comment_metadata(
                            comments=raw_comments,
                            current_comment=comment,
                            current_index=comment_index,
                            body=body,
                        ),
                        inline_media=_extract_ado_inline_media(
                            content=body,
                            provider="azure_devops",
                            feedback_id=feedback_id,
                            comment_id=comment_id,
                        ),
                    )
                )

        ordered = sorted(feedback, key=lambda item: item.created_at)
        next_cursor = _latest_cursor([cursor, *seen_timestamps])
        return ProviderPollResult(feedback=ordered, next_cursor=next_cursor)
