"""Inline media helpers for communication MCP tools."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from inspect import signature
import os
from typing import Any
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from evander.gateway.communication_artifact_helpers import CommunicationArtifactHelpers
from evander.gateway.communication_attachment_helpers import CommunicationAttachmentHelpers
from evander.gateway.dependencies import GatewayToolDependencies, _compat_gateway_attr
from evander.gateway.tool_audit import (
    _mark_tool_call_audited,
    _tool_call_audit_fields,
)
from evander.inline_media import (
    download_url_with_limit,
    inline_media_download_from_embedded_payload,
    normalize_inline_media_provider,
    require_provider_media_url,
    sanitize_inline_media_metadata,
)
from evander.issues.service import AzureDevOpsIssueInlineMediaProvider
from evander.models import (
    AdoBacklogGetResponse,
    AuditEvent,
    CommunicationFileArtifact,
    CommunicationListAttachmentsRequest,
    DownloadPrFeedbackAttachmentRequest,
    DownloadInlineMediaRequest,
    InlineMediaRef,
    ListPrFeedbackAttachmentsRequest,
    ListPrFeedbackAttachmentsResponse,
    ListInlineMediaRequest,
    PrFeedbackAttachmentRef,
    ToolName,
    tool_name_value,
)
from evander.policy import PolicyError
from evander.pr_feedback.providers import (
    ADO_PR_FEEDBACK_ATTACHMENT_SCOPE_KEY,
    AzureDevOpsPrAttachmentScope,
    AzureDevOpsFeedbackAdapter,
    GitHubFeedbackAdapter,
    ado_pr_attachment_scope_from_pull_request,
    extract_ado_pr_feedback_attachments,
    require_ado_pr_feedback_attachment_url,
)
from evander.runtime import RuntimeContext
from evander.services import TeamsInlineMediaProvider

_INLINE_MEDIA_SOURCE_KINDS = {"pr_feedback", "ado_backlog", "communication"}
_INLINE_MEDIA_SECRET_FIELDS = {
    "authorization",
    "token",
    "access_token",
    "content",
    "content_bytes",
    "content_base64",
    "data",
    "bytes",
}


def _normalize_inline_media_source_kind(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _INLINE_MEDIA_SOURCE_KINDS:
        raise ToolError("source_kind must be one of: pr_feedback, ado_backlog, communication")
    return normalized


@dataclass(frozen=True)
class InlineMediaHelpers:
    context: RuntimeContext
    deps: GatewayToolDependencies
    artifact_helpers: CommunicationArtifactHelpers
    attachment_helpers: CommunicationAttachmentHelpers
    session_actor_role: Callable[[Any], str | None]
    session_actor_user_id: Callable[[Any], str | None]

    def inline_media_id(self, raw: dict[str, Any], *, fallback: str) -> str:
        return str(
            raw.get("media_id")
            or raw.get("attachment_id")
            or raw.get("id")
            or raw.get("content_id")
            or raw.get("url")
            or raw.get("filename")
            or raw.get("name")
            or fallback
        ).strip()

    def azure_devops_inline_media_provider(
        self,
        session_id: str | None = None,
        *,
        source_kind: str = "pr_feedback",
    ) -> AzureDevOpsFeedbackAdapter:
        resolved_config = (
            self.context.resolve_provider_config_for_session(session_id)
            if session_id is not None
            else self.context.config
        )
        if source_kind == "ado_backlog":
            pat_env_name = (
                str(resolved_config.azure_devops_pat_env or "").strip() or "AZURE_DEVOPS_PAT"
            )
        else:
            pat_env_name = (
                str(resolved_config.azure_devops_pr_pat_env or "").strip() or "AZURE_DEVOPS_PR_PAT"
            )
        pat = os.environ.get(pat_env_name, "").strip()
        adapter_cls = _compat_gateway_attr(
            "AzureDevOpsFeedbackAdapter",
            AzureDevOpsFeedbackAdapter,
        )
        return adapter_cls(
            organization_url=resolved_config.azure_devops_organization_url,
            project=resolved_config.azure_devops_project,
            pat=pat,
            pat_env_name=pat_env_name,
        )

    def github_inline_media_provider(self, session_id: str | None = None) -> GitHubFeedbackAdapter:
        resolved_config = (
            self.context.resolve_provider_config_for_session(session_id)
            if session_id is not None
            else self.context.config
        )
        token = os.environ.get(resolved_config.github_token_env, "").strip()
        adapter_cls = _compat_gateway_attr("GitHubFeedbackAdapter", GitHubFeedbackAdapter)
        return adapter_cls(token=token)

    @staticmethod
    def _contains_ado_git_attachment_route(values: list[str]) -> bool:
        return any("/_apis/git/repositories/" in value.casefold() for value in values)

    def ado_pr_attachment_scope(
        self,
        *,
        session_id: str,
        repo: str,
        pr_id: str,
        candidate_values: list[str],
    ) -> AzureDevOpsPrAttachmentScope | None:
        if not self._contains_ado_git_attachment_route(candidate_values):
            return None
        resolved_config = self.context.resolve_provider_config_for_session(session_id)
        try:
            response = self.context.pr_query_service_for_session(session_id).get_pull_request(
                provider="azure_devops",
                repo=repo,
                pr_id=pr_id,
                include_reviewers=False,
                include_commits=False,
            )
            if (
                normalize_inline_media_provider(response.provider) != "azure_devops"
                or str(response.pr_id).strip() != pr_id
            ):
                raise ValueError(
                    "Azure DevOps pull request lookup returned a different feedback stream"
                )
            return ado_pr_attachment_scope_from_pull_request(
                payload=response.raw,
                organization_url=resolved_config.azure_devops_organization_url,
                project=resolved_config.azure_devops_project,
                repo=repo,
                pr_id=pr_id,
            )
        except (httpx.HTTPError, ValueError) as exc:
            raise ToolError(
                "Unable to verify the Azure DevOps repository identity for this authorized "
                "PR feedback stream"
            ) from exc

    @staticmethod
    def ado_pr_attachment_scope_metadata(
        *,
        repo: str,
        pr_id: str,
        scope: AzureDevOpsPrAttachmentScope | None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {"repo": repo, "pr_id": pr_id}
        if scope is not None:
            metadata["project_identifiers"] = list(scope.project_identifiers)
            metadata["repository_identifiers"] = list(scope.repository_identifiers)
        return metadata

    def inline_media_provider_for_request(
        self,
        request: ListInlineMediaRequest | DownloadInlineMediaRequest,
        ref: InlineMediaRef | None = None,
    ) -> Any:
        provider = normalize_inline_media_provider(
            str((ref.provider if ref is not None else request.provider) or "")
        )
        if provider == "github":
            return self.github_inline_media_provider(request.session_id)
        if provider in {"azure_devops", "ado"}:
            if request.source_kind == "ado_backlog":
                session = self.context.policy.get_session(request.session_id)
                return AzureDevOpsIssueInlineMediaProvider(
                    self.context.provider_config,
                    directory_path=session.directory_path or "root",
                )
            return self.azure_devops_inline_media_provider(
                request.session_id, source_kind=request.source_kind
            )
        if provider == "teams":
            return TeamsInlineMediaProvider(
                self.context.teams_service_for_session(request.session_id)
            )
        return None

    def raw_inline_media_items(self, resource: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        raw_media = resource.get("inline_media")
        if isinstance(raw_media, list):
            items.extend(item for item in raw_media if isinstance(item, dict))
        metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
        metadata_media = metadata.get("inline_media") or metadata.get("images")
        if isinstance(metadata_media, list):
            items.extend(item for item in metadata_media if isinstance(item, dict))
        return items

    def raw_inline_media_for_id(
        self, resource: dict[str, Any], media_id: str
    ) -> dict[str, Any] | None:
        for item in self.raw_inline_media_items(resource):
            if self.inline_media_id(item, fallback="") == media_id:
                return item
        return None

    def inline_media_refs_from_raw(
        self,
        *,
        source_kind: str,
        provider: str,
        resource_id: str,
        raw_media: object,
        comment_id: str | None = None,
    ) -> list[tuple[InlineMediaRef, dict[str, Any]]]:
        if raw_media is None:
            return []
        raw_items = raw_media if isinstance(raw_media, list) else [raw_media]
        refs: list[tuple[InlineMediaRef, dict[str, Any]]] = []
        for index, raw_item in enumerate(raw_items):
            if isinstance(raw_item, InlineMediaRef):
                refs.append((raw_item, raw_item.model_dump(mode="json")))
                continue
            if hasattr(raw_item, "model_dump"):
                raw_item = raw_item.model_dump()
            if not isinstance(raw_item, dict):
                continue
            raw_comment_id = raw_item.get("comment_id") or comment_id
            media_id = self.inline_media_id(raw_item, fallback=str(index))
            filename = str(
                raw_item.get("filename")
                or raw_item.get("name")
                or raw_item.get("title")
                or f"inline-media-{index}"
            ).strip()
            if not media_id or not filename:
                continue
            byte_count_raw = raw_item.get(
                "byte_count", raw_item.get("size", raw_item.get("contentSize"))
            )
            byte_count: int | None = None
            if byte_count_raw is not None:
                try:
                    byte_count = int(byte_count_raw)
                except (TypeError, ValueError):
                    byte_count = None
            mime_type = (
                raw_item.get("mime_type")
                or raw_item.get("content_type")
                or raw_item.get("contentType")
            )
            safe_metadata = sanitize_inline_media_metadata(raw_item)
            ref = InlineMediaRef(
                source_kind=source_kind,
                provider=normalize_inline_media_provider(provider),
                resource_id=resource_id,
                comment_id=str(raw_comment_id).strip() if raw_comment_id else None,
                media_id=media_id,
                filename=filename,
                mime_type=str(mime_type).strip() if mime_type else None,
                byte_count=byte_count,
                url=(
                    str(
                        raw_item.get("url")
                        or raw_item.get("download_url")
                        or raw_item.get("web_url")
                        or ""
                    ).strip()
                    or None
                ),
                metadata=safe_metadata,
            )
            refs.append((ref, raw_item))
        return refs

    def inline_media_from_feedback(
        self,
        request: ListInlineMediaRequest,
        *,
        tool_name: ToolName = ToolName.LIST_INLINE_MEDIA,
    ) -> list[tuple[InlineMediaRef, dict[str, Any]]]:
        session = self.context.policy.require_tool(request.session_id, tool_name)
        self.context.policy.require_inline_media_receive_enabled(session, source_kind="pr_feedback")
        raw_feedback = self.context.pr_feedback_service.store.read_raw().get("feedback", {})
        if not isinstance(raw_feedback, dict):
            return []
        raw_item = raw_feedback.get(request.resource_id)
        if not isinstance(raw_item, dict):
            raise ToolError(f"Unknown PR feedback resource_id: {request.resource_id}")
        self.context.policy.require_pr_feedback_stream(
            session,
            provider=str(raw_item.get("provider") or ""),
            repo=str(raw_item.get("repo") or ""),
            pr_id=str(raw_item.get("pr_id") or ""),
        )
        provider = str(raw_item.get("provider") or request.provider).strip().lower()
        comment_id = str(raw_item.get("comment_id") or "").strip() or None
        adapter = self.inline_media_provider_for_request(
            request.model_copy(update={"provider": provider})
        )
        if adapter is not None and hasattr(adapter, "list_inline_media"):
            refs = adapter.list_inline_media(
                source_kind="pr_feedback",
                resource_id=request.resource_id,
                resource=raw_item,
                comment_id=comment_id,
            )
            if refs:
                return self.scope_pr_feedback_media(
                    request,
                    raw_item,
                    [
                        (
                            ref,
                            self.raw_inline_media_for_id(raw_item, ref.media_id)
                            or ref.model_dump(mode="json"),
                        )
                        for ref in refs
                    ],
                )
        media: list[tuple[InlineMediaRef, dict[str, Any]]] = []
        media.extend(
            self.inline_media_refs_from_raw(
                source_kind="pr_feedback",
                provider=provider,
                resource_id=request.resource_id,
                raw_media=raw_item.get("inline_media"),
                comment_id=comment_id,
            )
        )
        metadata = raw_item.get("metadata") if isinstance(raw_item.get("metadata"), dict) else {}
        media.extend(
            self.inline_media_refs_from_raw(
                source_kind="pr_feedback",
                provider=provider,
                resource_id=request.resource_id,
                raw_media=metadata.get("inline_media") or metadata.get("images"),
                comment_id=comment_id,
            )
        )
        return self.scope_pr_feedback_media(request, raw_item, media)

    def scope_pr_feedback_media(
        self,
        request: ListInlineMediaRequest,
        raw_feedback: dict[str, Any],
        media: list[tuple[InlineMediaRef, dict[str, Any]]],
    ) -> list[tuple[InlineMediaRef, dict[str, Any]]]:
        provider = normalize_inline_media_provider(str(raw_feedback.get("provider") or ""))
        if provider != "azure_devops":
            return media
        repo = str(raw_feedback.get("repo") or "").strip()
        pr_id = str(raw_feedback.get("pr_id") or "").strip()
        resolved_config = self.context.resolve_provider_config_for_session(request.session_id)
        attachment_scope = self.ado_pr_attachment_scope(
            session_id=request.session_id,
            repo=repo,
            pr_id=pr_id,
            candidate_values=[ref.url for ref, _raw in media if ref.url],
        )
        scope_metadata = self.ado_pr_attachment_scope_metadata(
            repo=repo,
            pr_id=pr_id,
            scope=attachment_scope,
        )
        scoped: list[tuple[InlineMediaRef, dict[str, Any]]] = []
        for ref, raw in media:
            if ref.url:
                try:
                    require_ado_pr_feedback_attachment_url(
                        url=ref.url,
                        organization_url=resolved_config.azure_devops_organization_url,
                        project=resolved_config.azure_devops_project,
                        repo=repo,
                        pr_id=pr_id,
                        project_identifiers=(
                            attachment_scope.project_identifiers
                            if attachment_scope is not None
                            else None
                        ),
                        repository_identifiers=(
                            attachment_scope.repository_identifiers
                            if attachment_scope is not None
                            else None
                        ),
                    )
                except ValueError:
                    continue
            scoped.append(
                (
                    ref.model_copy(
                        update={
                            "metadata": {
                                **ref.metadata,
                                ADO_PR_FEEDBACK_ATTACHMENT_SCOPE_KEY: scope_metadata,
                            }
                        }
                    ),
                    raw,
                )
            )
        return scoped

    def pr_feedback_attachments(
        self,
        request: ListPrFeedbackAttachmentsRequest,
        *,
        tool_name: ToolName = ToolName.LIST_PR_FEEDBACK_ATTACHMENTS,
    ) -> ListPrFeedbackAttachmentsResponse:
        session = self.context.policy.require_tool(request.session_id, tool_name)
        self.context.policy.require_communication_file_receive_enabled(session)
        raw_feedback = self.context.pr_feedback_service.store.read_raw().get("feedback", {})
        if not isinstance(raw_feedback, dict):
            raise ToolError("PR feedback storage is unavailable")
        raw_item = raw_feedback.get(request.feedback_id)
        if not isinstance(raw_item, dict):
            raise ToolError(f"Unknown PR feedback feedback_id: {request.feedback_id}")

        provider = str(raw_item.get("provider") or "").strip().lower().replace("-", "_")
        repo = str(raw_item.get("repo") or "").strip()
        pr_id = str(raw_item.get("pr_id") or "").strip()
        self.context.policy.require_pr_feedback_stream(
            session,
            provider=provider,
            repo=repo,
            pr_id=pr_id,
        )
        if provider != "azure_devops":
            raise ToolError(
                "PR feedback attachment download currently supports Azure DevOps feedback"
            )

        resolved_config = self.context.resolve_provider_config_for_session(request.session_id)
        body = str(raw_item.get("body") or "")
        attachment_scope = self.ado_pr_attachment_scope(
            session_id=request.session_id,
            repo=repo,
            pr_id=pr_id,
            candidate_values=[body],
        )
        scope_metadata = self.ado_pr_attachment_scope_metadata(
            repo=repo,
            pr_id=pr_id,
            scope=attachment_scope,
        )
        refs = extract_ado_pr_feedback_attachments(
            content=body,
            organization_url=resolved_config.azure_devops_organization_url,
            project=resolved_config.azure_devops_project,
            feedback_id=request.feedback_id,
            repo=repo,
            pr_id=pr_id,
            thread_id=str(raw_item.get("thread_id") or "").strip() or None,
            comment_id=str(raw_item.get("comment_id") or "").strip() or None,
            project_identifiers=(
                attachment_scope.project_identifiers if attachment_scope is not None else None
            ),
            repository_identifiers=(
                attachment_scope.repository_identifiers if attachment_scope is not None else None
            ),
        )
        safe_refs: list[PrFeedbackAttachmentRef] = []
        for ref in refs:
            try:
                require_ado_pr_feedback_attachment_url(
                    url=ref.url,
                    organization_url=resolved_config.azure_devops_organization_url,
                    project=resolved_config.azure_devops_project,
                    repo=repo,
                    pr_id=pr_id,
                    project_identifiers=(
                        attachment_scope.project_identifiers
                        if attachment_scope is not None
                        else None
                    ),
                    repository_identifiers=(
                        attachment_scope.repository_identifiers
                        if attachment_scope is not None
                        else None
                    ),
                )
            except ValueError:
                continue
            safe_refs.append(
                ref.model_copy(
                    update={
                        "metadata": {
                            **ref.metadata,
                            ADO_PR_FEEDBACK_ATTACHMENT_SCOPE_KEY: scope_metadata,
                        }
                    }
                )
            )
        return ListPrFeedbackAttachmentsResponse(
            provider=provider,
            feedback_id=request.feedback_id,
            repo=repo,
            pr_id=pr_id,
            thread_id=str(raw_item.get("thread_id") or "").strip() or None,
            comment_id=str(raw_item.get("comment_id") or "").strip() or None,
            attachments=safe_refs,
        )

    def download_pr_feedback_attachment_bytes(
        self,
        request: DownloadPrFeedbackAttachmentRequest,
        ref: PrFeedbackAttachmentRef,
    ) -> tuple[bytes, str, str | None]:
        inline_request = DownloadInlineMediaRequest(
            session_id=request.session_id,
            source_kind="pr_feedback",
            provider=ref.provider,
            resource_id=ref.feedback_id,
            media_id=ref.attachment_id,
            comment_id=ref.comment_id,
        )
        inline_ref = InlineMediaRef(
            source_kind="pr_feedback_attachment",
            provider=ref.provider,
            resource_id=ref.feedback_id,
            comment_id=ref.comment_id,
            media_id=ref.attachment_id,
            filename=ref.filename,
            mime_type=ref.mime_type,
            byte_count=ref.byte_count,
            url=ref.url,
            metadata={
                **ref.metadata,
                ADO_PR_FEEDBACK_ATTACHMENT_SCOPE_KEY: {
                    **(
                        ref.metadata.get(ADO_PR_FEEDBACK_ATTACHMENT_SCOPE_KEY)
                        if isinstance(ref.metadata.get(ADO_PR_FEEDBACK_ATTACHMENT_SCOPE_KEY), dict)
                        else {}
                    ),
                    "repo": ref.repo,
                    "pr_id": ref.pr_id,
                },
            },
        )
        return self.download_inline_media_bytes(
            inline_request,
            inline_ref,
            {
                "url": ref.url,
                "filename": ref.filename,
                "mime_type": ref.mime_type,
                "byte_count": ref.byte_count,
                "metadata": ref.metadata,
            },
        )

    def stage_pr_feedback_attachment_artifact(
        self,
        request: DownloadPrFeedbackAttachmentRequest,
        ref: PrFeedbackAttachmentRef,
        content: bytes,
        *,
        filename: str,
        mime_type: str | None,
    ) -> CommunicationFileArtifact:
        record = self.artifact_helpers.communication_artifact_store().stage(
            provider=ref.provider,
            session_id=request.session_id,
            content=content,
            original_filename=filename,
            mime_type=mime_type,
            source_message_id=ref.feedback_id,
            source_attachment_id=ref.attachment_id,
            metadata={
                "source_kind": "pr_feedback_attachment",
                "feedback_id": ref.feedback_id,
                "repo": ref.repo,
                "pr_id": ref.pr_id,
                "thread_id": ref.thread_id,
                "comment_id": ref.comment_id,
                "attachment": ref.model_dump(mode="json"),
            },
        )
        return self.artifact_helpers.artifact_to_model(record)

    def inline_media_from_ado_backlog(
        self,
        request: ListInlineMediaRequest,
        *,
        tool_name: ToolName = ToolName.LIST_INLINE_MEDIA,
    ) -> list[tuple[InlineMediaRef, dict[str, Any]]]:
        session = self.context.policy.require_tool(request.session_id, tool_name)
        self.context.policy.require_inline_media_receive_enabled(session, source_kind="ado_backlog")
        issue_service = self.deps.issue_service_factory()
        payload = issue_service.get_remote_backlog_item_by_id(
            provider=normalize_inline_media_provider(request.provider),
            directory_path=session.directory_path or "root",
            backlog_item_id=request.resource_id,
            project=request.project,
            include_comments=True,
            include_relations=True,
            include_updates=False,
        )
        response = AdoBacklogGetResponse.model_validate(payload)
        aggregate_media = self.inline_media_refs_from_raw(
            source_kind="ado_backlog",
            provider=normalize_inline_media_provider(response.provider),
            resource_id=response.id,
            raw_media=response.inline_media or response.metadata.get("inline_media"),
        )
        comment_media: list[tuple[InlineMediaRef, dict[str, Any]]] = []
        for comment in response.comments:
            if not isinstance(comment, dict):
                continue
            comment_id = str(comment.get("comment_id") or comment.get("id") or "").strip() or None
            if request.comment_id and comment_id != request.comment_id:
                continue
            comment_media.extend(
                self.inline_media_refs_from_raw(
                    source_kind="ado_backlog",
                    provider=normalize_inline_media_provider(response.provider),
                    resource_id=response.id,
                    raw_media=comment.get("inline_media") or comment.get("images"),
                    comment_id=comment_id,
                )
            )
        if request.comment_id:
            return comment_media or [
                item for item in aggregate_media if item[0].comment_id == request.comment_id
            ]
        return [item for item in aggregate_media if item[0].comment_id is None] + comment_media

    def inline_media_from_communication(
        self,
        request: ListInlineMediaRequest,
        *,
        tool_name: ToolName = ToolName.LIST_INLINE_MEDIA,
    ) -> list[tuple[InlineMediaRef, dict[str, Any]]]:
        list_request = CommunicationListAttachmentsRequest(
            session_id=request.session_id,
            provider=request.provider,
            message_id=request.resource_id,
            mailbox_id=request.mailbox_id,
            channel_id=request.channel_id,
            channel_target=request.channel_target,
        )
        attachments = self.attachment_helpers.resolve_communication_attachments(
            list_request, tool_name=tool_name
        )
        pairs: list[tuple[InlineMediaRef, dict[str, Any]]] = []
        for item in attachments:
            raw = item.model_dump(mode="json")
            ref = InlineMediaRef(
                source_kind="communication",
                provider=normalize_inline_media_provider(item.provider),
                resource_id=item.message_id,
                media_id=item.attachment_id,
                filename=item.filename,
                mime_type=item.mime_type,
                byte_count=item.byte_count,
                url=(
                    str(item.metadata.get("url") or item.metadata.get("download_url") or "").strip()
                    or None
                ),
                metadata=sanitize_inline_media_metadata(item.metadata),
            )
            pairs.append((ref, raw))
        return pairs

    def resolve_inline_media(
        self,
        request: ListInlineMediaRequest,
        *,
        tool_name: ToolName = ToolName.LIST_INLINE_MEDIA,
    ) -> list[tuple[InlineMediaRef, dict[str, Any]]]:
        source_kind = _normalize_inline_media_source_kind(request.source_kind)
        if source_kind == "pr_feedback":
            return self.inline_media_from_feedback(request, tool_name=tool_name)
        if source_kind == "ado_backlog":
            return self.inline_media_from_ado_backlog(request, tool_name=tool_name)
        return self.inline_media_from_communication(request, tool_name=tool_name)

    def inline_media_max_bytes_for_session(self, session: Any) -> int | None:
        return self.artifact_helpers.communication_file_max_bytes_for_session(session)

    def preflight_inline_media_content(self, session: Any, ref: InlineMediaRef) -> None:
        mime_type = ref.mime_type
        if not mime_type:
            allowed_mime_types = self.context.policy._communication_file_policy_value(
                session, "communication_file_allowed_mime_types"
            )
            if allowed_mime_types:
                # Preserve cheap filename/extension/declared-size checks without
                # rejecting solely because the HTTP response MIME type has not been
                # observed yet; the post-download content policy enforces MIME.
                mime_type = next(iter(allowed_mime_types), None)
        self.context.policy.require_inline_media_content(
            session,
            source_kind=ref.source_kind,
            filename=ref.filename,
            mime_type=mime_type,
            byte_count=ref.byte_count if ref.byte_count is not None else 0,
        )

    def allowed_inline_media_hosts_for_request(
        self,
        request: DownloadInlineMediaRequest,
        ref: InlineMediaRef,
    ) -> list[str]:
        provider = normalize_inline_media_provider(ref.provider or request.provider)
        if provider != "azure_devops":
            return []
        resolved_config = self.context.resolve_provider_config_for_session(request.session_id)
        host = urlparse(resolved_config.azure_devops_organization_url).hostname
        return [host] if host else []

    def call_inline_media_provider_download(
        self,
        provider: Any,
        request: DownloadInlineMediaRequest,
        ref: InlineMediaRef,
        raw: dict[str, Any],
        *,
        max_bytes: int | None,
    ) -> Any:
        kwargs: dict[str, Any] = {"media": ref, "raw": raw}
        try:
            parameters = signature(provider.download_inline_media).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "channel_target" in parameters:
            kwargs["channel_target"] = request.channel_target
        if "max_bytes" in parameters:
            kwargs["max_bytes"] = max_bytes
        return provider.download_inline_media(**kwargs)

    def download_inline_media_bytes(
        self,
        request: DownloadInlineMediaRequest,
        ref: InlineMediaRef,
        raw: dict[str, Any],
    ) -> tuple[bytes, str, str | None]:
        session = self.context.policy.get_session(request.session_id)
        self.preflight_inline_media_content(session, ref)
        max_bytes = self.inline_media_max_bytes_for_session(session)
        provider = self.inline_media_provider_for_request(request, ref)
        if provider is not None and hasattr(provider, "download_inline_media"):
            downloaded = self.call_inline_media_provider_download(
                provider, request, ref, raw, max_bytes=max_bytes
            )
            if max_bytes is not None and len(downloaded.content) > max_bytes:
                raise PolicyError(
                    f"Inline media exceeds max bytes for session {request.session_id}: "
                    f"{len(downloaded.content)} > {max_bytes}"
                )
            return downloaded.content, downloaded.filename, downloaded.mime_type
        embedded = inline_media_download_from_embedded_payload(ref, raw)
        if embedded is not None:
            if max_bytes is not None and len(embedded.content) > max_bytes:
                raise PolicyError(
                    f"Inline media exceeds max bytes for session {request.session_id}: "
                    f"{len(embedded.content)} > {max_bytes}"
                )
            return embedded.content, embedded.filename, embedded.mime_type
        if ref.url:
            require_provider_media_url(
                provider=ref.provider,
                url=ref.url,
                allowed_hosts=self.allowed_inline_media_hosts_for_request(request, ref),
            )
            content, mime_type = download_url_with_limit(
                url=ref.url,
                max_bytes=max_bytes,
                provider=ref.provider,
                allowed_hosts=self.allowed_inline_media_hosts_for_request(request, ref),
            )
            return content, ref.filename, mime_type or ref.mime_type
        raise ToolError(
            "Inline media content is not available from the configured provider metadata"
        )

    def stage_inline_media_artifact(
        self,
        request: DownloadInlineMediaRequest,
        ref: InlineMediaRef,
        content: bytes,
        *,
        filename: str,
        mime_type: str | None,
    ) -> CommunicationFileArtifact:
        record = self.artifact_helpers.communication_artifact_store().stage(
            provider=ref.provider,
            session_id=request.session_id,
            content=content,
            original_filename=filename,
            mime_type=mime_type,
            source_message_id=ref.resource_id,
            source_attachment_id=ref.media_id,
            metadata={
                "source_kind": ref.source_kind,
                "inline_media_ref": ref.model_dump(mode="json"),
            },
        )
        return self.artifact_helpers.artifact_to_model(record)

    def inline_media_source_audit_fields(
        self,
        request: ListInlineMediaRequest | DownloadInlineMediaRequest,
    ) -> dict[str, Any]:
        source_kind = _normalize_inline_media_source_kind(request.source_kind)
        fields: dict[str, Any] = {
            "source_kind": source_kind,
            "provider": normalize_inline_media_provider(str(request.provider or "")),
        }
        if source_kind == "pr_feedback":
            raw_feedback = self.context.pr_feedback_service.store.read_raw().get("feedback", {})
            raw_item = (
                raw_feedback.get(request.resource_id) if isinstance(raw_feedback, dict) else None
            )
            if isinstance(raw_item, dict):
                fields.update(
                    {
                        "feedback_id": request.resource_id,
                        "repo": str(raw_item.get("repo") or "").strip() or None,
                        "pr_id": str(raw_item.get("pr_id") or "").strip() or None,
                        "comment_id": str(raw_item.get("comment_id") or "").strip() or None,
                    }
                )
            else:
                fields["feedback_id"] = request.resource_id
        elif source_kind == "ado_backlog":
            fields.update(
                {
                    "work_item_id": request.resource_id,
                    "project": request.project,
                    "comment_id": request.comment_id,
                }
            )
        else:
            fields.update(
                {
                    "message_id": request.resource_id,
                    "mailbox_id": request.mailbox_id,
                    "channel_id": request.channel_id,
                    "channel_target": request.channel_target,
                }
            )
        return fields

    def inline_media_ref_audit_payload(self, ref: InlineMediaRef) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "provider": ref.provider,
            "source_kind": ref.source_kind,
            "resource_id": ref.resource_id,
            "media_id": ref.media_id,
            "comment_id": ref.comment_id,
            "filename": ref.filename,
            "mime_type": ref.mime_type,
            "byte_count": ref.byte_count,
        }
        if ref.source_kind == "pr_feedback":
            payload["feedback_id"] = ref.resource_id
        elif ref.source_kind == "ado_backlog":
            payload["work_item_id"] = ref.resource_id
        else:
            payload["message_id"] = ref.resource_id
        return payload

    def audit_inline_media_operation(
        self,
        *,
        event_type: str,
        session_id: str,
        request: ListInlineMediaRequest | DownloadInlineMediaRequest,
        media: list[InlineMediaRef],
        result: dict[str, Any],
        decision: str = "allowed",
    ) -> None:
        session = self.context.policy.get_session(session_id)
        source_fields = self.inline_media_source_audit_fields(request)
        tool_name = (
            ToolName.LIST_INLINE_MEDIA
            if event_type == "inline_media_list"
            else ToolName.DOWNLOAD_INLINE_MEDIA
        )
        self.context.audit.log(
            AuditEvent(
                event_type=event_type,
                session_id=session_id,
                vm_id=session.vm_id,
                directory_path=session.directory_path,
                agent_role=self.session_actor_role(session),
                provider=source_fields.get("provider"),
                repo=source_fields.get("repo"),
                pr_id=source_fields.get("pr_id"),
                tool_name=tool_name_value(tool_name),
                actor_user_id=self.session_actor_user_id(session),
                decision=decision,
                **_tool_call_audit_fields(
                    session=session,
                    tool_name=tool_name,
                    args={**request.model_dump(mode="json"), **source_fields},
                    result={
                        **result,
                        "media": [self.inline_media_ref_audit_payload(item) for item in media],
                    },
                ),
                args={**request.model_dump(mode="json"), **source_fields},
                result={
                    **result,
                    "media": [self.inline_media_ref_audit_payload(item) for item in media],
                },
            )
        )
        _mark_tool_call_audited()
