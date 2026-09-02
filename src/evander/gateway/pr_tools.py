"""Pull request and feedback MCP gateway tool registrations."""

from __future__ import annotations

from typing import Annotated, Any, Literal

import httpx
from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field as PydanticField

from evander.models import (
    CancelScheduledWakeResponse,
    GetPullRequestRequest,
    HasPendingFeedbackRequest,
    ListPullRequestsRequest,
    ListUnreadFeedbackRequest,
    PostPrCommentRequest,
    PostPrFeedbackReplyRequest,
    PostPrInlineCommentRequest,
    RequestPrReviewRequest,
    RunScriptRequest,
    RunScriptResponse,
    ScheduleResumeRequest,
    ScheduleResumeResponse,
    SubmitPrReviewRequest,
    ToolName,
    UpdatePullRequestRequest,
    WakeScheduleDefinition,
)
from evander.policy import PolicyError
from evander.services import normalize_pr_automation_provider, normalize_pr_feedback_provider
from evander.gateway.session_context import _resolve_session_id_from_context

AdoMentionRefsArgument = Annotated[
    list[str] | None,
    PydanticField(
        description=(
            "Optional Azure DevOps user refs to mention. Use an ADO identity ID, descriptor, "
            "email/UPN, or display-name search text. The gateway resolves refs and prepends "
            "provider-native mention tokens; plain @name text does not reliably notify users."
        )
    ),
]


def register_pr_tools(
    server: Any,
    context: Any,
    deps: Any,
    *,
    transport_session_map: dict[str, str] | None = None,
    audit_success: Any,
    audit_failure: Any,
    artifact_audit_summaries: Any,
    ado_artifacts_for_ids: Any,
    ado_artifact_approval_payload: Any,
) -> None:
    """Register pull request and feedback tools with the MCP gateway server."""

    if transport_session_map is None:
        transport_session_map = getattr(server, "_evander_transport_session_map", {})

    @server.tool()
    def list_pull_requests(
        session_id: str,
        provider: str,
        repo: str,
        project: str | None = None,
        status: str | None = None,
        statuses: list[str] | None = None,
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
    ) -> dict[str, Any]:
        request = ListPullRequestsRequest(
            session_id=session_id.strip(),
            provider=normalize_pr_feedback_provider(provider),
            repo=repo.strip(),
            project=project.strip() if isinstance(project, str) else None,
            status=status.strip() if isinstance(status, str) else None,
            statuses=statuses or [],
            creator_id=creator_id.strip() if isinstance(creator_id, str) else None,
            reviewer_id=reviewer_id.strip() if isinstance(reviewer_id, str) else None,
            source_ref_name=source_ref_name.strip() if isinstance(source_ref_name, str) else None,
            target_ref_name=target_ref_name.strip() if isinstance(target_ref_name, str) else None,
            source_repository_id=source_repository_id.strip()
            if isinstance(source_repository_id, str)
            else None,
            repository_id=repository_id.strip() if isinstance(repository_id, str) else None,
            min_time=min_time.strip() if isinstance(min_time, str) else None,
            max_time=max_time.strip() if isinstance(max_time, str) else None,
            query_time_range_type=query_time_range_type.strip()
            if isinstance(query_time_range_type, str)
            else None,
            include_links=include_links,
            max_comment_length=max_comment_length,
            tags=tags.strip() if isinstance(tags, str) else None,
            top=top,
            skip=skip,
            cursor=cursor.strip() if isinstance(cursor, str) else None,
        )
        args = request.model_dump(mode="json")
        try:
            context.policy.require_tool(request.session_id, ToolName.LIST_PULL_REQUESTS)
            response = context.pr_query_service_for_session(request.session_id).list_pull_requests(
                provider=request.provider,
                repo=request.repo,
                project=request.project,
                status=request.status,
                statuses=request.statuses,
                creator_id=request.creator_id,
                reviewer_id=request.reviewer_id,
                source_ref_name=request.source_ref_name,
                target_ref_name=request.target_ref_name,
                source_repository_id=request.source_repository_id,
                repository_id=request.repository_id,
                min_time=request.min_time,
                max_time=request.max_time,
                query_time_range_type=request.query_time_range_type,
                include_links=request.include_links,
                max_comment_length=request.max_comment_length,
                tags=request.tags,
                top=request.top,
                skip=request.skip,
                cursor=request.cursor,
            )
            result = response.model_dump(mode="json")
            audit_success(
                request.session_id,
                ToolName.LIST_PULL_REQUESTS,
                args,
                {"count": len(response.pull_requests), "next_cursor": response.next_cursor},
            )
            return result
        except PolicyError as exc:
            audit_failure(request.session_id, ToolName.LIST_PULL_REQUESTS, args, str(exc))
            raise
        except (httpx.HTTPError, ValueError) as exc:
            audit_failure(
                request.session_id,
                ToolName.LIST_PULL_REQUESTS,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                request.session_id,
                ToolName.LIST_PULL_REQUESTS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def get_pull_request(
        session_id: str,
        provider: str,
        repo: str,
        pr_id: str,
        include_reviewers: bool | None = None,
        include_commits: bool | None = None,
    ) -> dict[str, Any]:
        request = GetPullRequestRequest(
            session_id=session_id.strip(),
            provider=normalize_pr_feedback_provider(provider),
            repo=repo.strip(),
            pr_id=pr_id.strip(),
            include_reviewers=include_reviewers,
            include_commits=include_commits,
        )
        args = request.model_dump(mode="json")
        try:
            context.policy.require_tool(request.session_id, ToolName.GET_PULL_REQUEST)
            response = context.pr_query_service_for_session(request.session_id).get_pull_request(
                provider=request.provider,
                repo=request.repo,
                pr_id=request.pr_id,
                include_reviewers=request.include_reviewers,
                include_commits=request.include_commits,
            )
            result = response.model_dump(mode="json")
            audit_success(
                request.session_id,
                ToolName.GET_PULL_REQUEST,
                args,
                {"pr_id": response.pr_id, "status": response.status},
            )
            return result
        except PolicyError as exc:
            audit_failure(request.session_id, ToolName.GET_PULL_REQUEST, args, str(exc))
            raise
        except (httpx.HTTPError, ValueError) as exc:
            audit_failure(
                request.session_id,
                ToolName.GET_PULL_REQUEST,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                request.session_id,
                ToolName.GET_PULL_REQUEST,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def update_pull_request(
        provider: Literal["azure_devops", "azure-devops", "ado"],
        repo: str,
        pr_id: str,
        project: str | None = None,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        add_tags: list[str] | None = None,
        remove_tags: list[str] | None = None,
        expected_statuses: list[str] | None = None,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Update an Azure DevOps pull request through the session's host integration.

        Omitted fields remain unchanged. ``description`` replaces the complete PR description;
        pass an empty string to clear it. Use ``expected_statuses`` to skip the mutation when the
        PR is no longer in an expected state, such as ``active``.
        """
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        request = UpdatePullRequestRequest(
            session_id=resolved_session_id,
            provider=normalize_pr_automation_provider(provider),
            repo=repo.strip(),
            pr_id=pr_id.strip(),
            project=project.strip() if isinstance(project, str) and project.strip() else None,
            title=title,
            description=description,
            status=status.strip() if isinstance(status, str) and status.strip() else None,
            add_tags=add_tags or [],
            remove_tags=remove_tags or [],
            expected_statuses=expected_statuses or [],
        )
        args = request.model_dump(mode="json")
        try:
            context.policy.require_tool(resolved_session_id, ToolName.UPDATE_PULL_REQUEST)
            response = context.pr_query_service_for_session(
                resolved_session_id
            ).update_pull_request(
                provider=request.provider,
                repo=request.repo,
                pr_id=request.pr_id,
                project=request.project,
                title=request.title,
                description=request.description,
                status=request.status,
                add_tags=request.add_tags,
                remove_tags=request.remove_tags,
                expected_statuses=request.expected_statuses,
            )
            result = response.model_dump(mode="json")
            audit_success(
                resolved_session_id,
                ToolName.UPDATE_PULL_REQUEST,
                args,
                {
                    "provider": response.provider,
                    "repo": response.repo,
                    "pr_id": response.pr_id,
                    "updated": response.updated,
                    "skipped": response.skipped,
                    "status": response.status,
                    "tags": response.tags,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.UPDATE_PULL_REQUEST, args, str(exc))
            raise
        except (httpx.HTTPError, ValueError) as exc:
            audit_failure(
                resolved_session_id,
                ToolName.UPDATE_PULL_REQUEST,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.UPDATE_PULL_REQUEST,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def has_pending_feedback(session_id: str) -> dict[str, Any]:
        args = HasPendingFeedbackRequest(session_id=session_id).model_dump(mode="json")
        try:
            session = context.policy.require_tool(session_id, ToolName.HAS_PENDING_FEEDBACK)
            streams = context.policy.require_pr_feedback_streams(session)
            result = context.pr_feedback_service.has_pending_feedback(
                streams=streams,
                session_id=session_id,
            ).model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.HAS_PENDING_FEEDBACK,
                args,
                {"pending": result["pending"]},
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.HAS_PENDING_FEEDBACK, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.HAS_PENDING_FEEDBACK,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def list_unread_feedback(
        session_id: str, cursor: str | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        args = ListUnreadFeedbackRequest(
            session_id=session_id, cursor=cursor, limit=limit
        ).model_dump(mode="json")
        try:
            session = context.policy.require_tool(session_id, ToolName.LIST_UNREAD_FEEDBACK)
            streams = context.policy.require_pr_feedback_streams(session)
            response = context.pr_feedback_service.list_unread_feedback(
                streams=streams,
                session_id=session_id,
                cursor=cursor,
                limit=limit,
            )
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.LIST_UNREAD_FEEDBACK,
                args,
                {"count": len(response.feedback), "next_cursor": response.next_cursor},
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.LIST_UNREAD_FEEDBACK, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.LIST_UNREAD_FEEDBACK,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def list_all_feedback(session_id: str, limit: int | None = None) -> dict[str, Any]:
        args = {"session_id": session_id, "limit": limit}
        try:
            session = context.policy.require_tool(session_id, ToolName.LIST_ALL_FEEDBACK)
            streams = context.policy.require_pr_feedback_streams(session)
            response = context.pr_feedback_service.list_all_feedback(streams=streams, limit=limit)
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.LIST_ALL_FEEDBACK,
                args,
                {"count": len(response.feedback), "next_cursor": response.next_cursor},
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.LIST_ALL_FEEDBACK, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.LIST_ALL_FEEDBACK,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def schedule_resume(
        session_id: str | None = None,
        resume_at: str = "",
        prompt: str = "",
        request_id: str | None = None,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "resume_at": resume_at,
            "prompt": prompt,
            "request_id": request_id,
            "source": source,
            "metadata": metadata or {},
        }
        try:
            request = ScheduleResumeRequest(
                session_id=resolved_session_id,
                resume_at=resume_at,
                cron=None,
                prompt=prompt,
                resume_mode="same_context",
                enabled=True,
                request_id=request_id.strip() if isinstance(request_id, str) else None,
                source=source.strip() if isinstance(source, str) else None,
                metadata=metadata or {},
            )
            args = request.model_dump(mode="json")
            context.policy.require_tool(resolved_session_id, ToolName.SCHEDULE_RESUME)
            scheduled = context.wake_store.schedule_resume_request(
                session_id=request.session_id,
                prompt=request.prompt,
                schedule=WakeScheduleDefinition(resume_at=request.resume_at, cron=request.cron),
                request_id=request.request_id,
                source=request.source,
                resume_mode=request.resume_mode,
                enabled=request.enabled,
                metadata=request.metadata,
            )
            result = ScheduleResumeResponse(
                session_id=scheduled.session_id,
                resume_at=scheduled.schedule.resume_at,
                cron=scheduled.schedule.cron,
                request_id=scheduled.request_id,
            ).model_dump(mode="json")
            audit_success(
                resolved_session_id,
                ToolName.SCHEDULE_RESUME,
                args,
                {
                    "session_id": request.session_id,
                    "resume_at": result["resume_at"],
                    "request_id": request.request_id,
                    "scheduled": True,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.SCHEDULE_RESUME, args, str(exc))
            raise
        except ValueError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SCHEDULE_RESUME,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.SCHEDULE_RESUME,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def cancel_scheduled_wake(
        session_id: str | None = None,
        request_id: str = "",
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Cancel an obsolete wake request owned by the caller session.

        Use this to retire pending future wakes that are no longer needed. The
        caller cannot cancel wakes owned by another session, and completed or
        already-cancelled wakes remain immutable.
        """

        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "request_id": request_id,
            "reason": reason,
            "metadata": metadata or {},
        }
        try:
            context.policy.require_tool(resolved_session_id, ToolName.CANCEL_SCHEDULED_WAKE)
            normalized_request_id = str(request_id or "").strip()
            normalized_reason = None
            if reason is not None:
                normalized_reason = str(reason).strip()[:1024] or None
            if metadata is None:
                normalized_metadata: dict[str, Any] = {}
            elif isinstance(metadata, dict):
                normalized_metadata = metadata
            else:
                raise ValueError("metadata must be an object")

            if not normalized_request_id:
                result = CancelScheduledWakeResponse(
                    session_id=resolved_session_id,
                    request_id=None,
                    status="missing_request_id",
                    request=None,
                ).model_dump(mode="json")
                audit_success(
                    resolved_session_id,
                    ToolName.CANCEL_SCHEDULED_WAKE,
                    args,
                    result,
                )
                return result

            cancellation = context.wake_store.cancel_request(
                request_id=normalized_request_id,
                caller_session_id=resolved_session_id,
                reason=normalized_reason,
                metadata=normalized_metadata,
            )
            result = CancelScheduledWakeResponse(
                session_id=resolved_session_id,
                request_id=cancellation.request_id,
                status=cancellation.status,
                request=cancellation.request,
            ).model_dump(mode="json")
            if cancellation.status == "permission_denied":
                denied_args = dict(args)
                if denied_args.get("reason") is not None:
                    denied_args["reason"] = "<redacted>"
                audit_failure(
                    resolved_session_id,
                    ToolName.CANCEL_SCHEDULED_WAKE,
                    denied_args,
                    "permission_denied",
                    decision="denied",
                    error_class="policy_error",
                )
                return result

            audit_success(
                resolved_session_id,
                ToolName.CANCEL_SCHEDULED_WAKE,
                args,
                result,
            )
            return result
        except PolicyError as exc:
            denied_args = dict(args)
            if denied_args.get("reason") is not None:
                denied_args["reason"] = "<redacted>"
            audit_failure(
                resolved_session_id,
                ToolName.CANCEL_SCHEDULED_WAKE,
                denied_args,
                str(exc),
            )
            raise
        except ValueError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.CANCEL_SCHEDULED_WAKE,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.CANCEL_SCHEDULED_WAKE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def run_script(
        session_id: str | None = None,
        script_name: str = "",
        parameters: list[str] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        resolved_session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": resolved_session_id,
            "script_name": script_name,
            "parameters": list(parameters or []),
        }
        try:
            request = RunScriptRequest(
                session_id=resolved_session_id,
                script_name=script_name,
                parameters=parameters or [],
            )
            args = request.model_dump(mode="json")
            session = context.policy.require_tool(resolved_session_id, ToolName.RUN_SCRIPT)
            normalized_script_name = context.policy.require_script_name(
                session, request.script_name
            )
            queued = context.script_run_request_store.enqueue(
                session_id=resolved_session_id,
                directory_path=session.directory_path,
                script_name=normalized_script_name,
                parameters=list(request.parameters),
                source="mcp_tool",
            )
            result = RunScriptResponse(
                request_id=queued.request_id,
                script_name=queued.script_name,
                parameters=list(queued.parameters),
                queued=True,
            ).model_dump(mode="json")
            audit_success(
                resolved_session_id,
                ToolName.RUN_SCRIPT,
                args,
                {
                    "request_id": queued.request_id,
                    "script_name": queued.script_name,
                    "queued": True,
                    "parameter_count": len(queued.parameters),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(resolved_session_id, ToolName.RUN_SCRIPT, args, str(exc))
            raise
        except ValueError as exc:
            audit_failure(
                resolved_session_id,
                ToolName.RUN_SCRIPT,
                args,
                str(exc),
                decision="error",
                error_class="validation_error",
            )
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            audit_failure(
                resolved_session_id,
                ToolName.RUN_SCRIPT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def post_pr_feedback_reply(
        session_id: str,
        provider: str,
        repo: str,
        pr_id: str,
        body_text: str,
        thread_id: str | None = None,
        comment_id: str | None = None,
        mentions: AdoMentionRefsArgument = None,
        artifact_ids: list[str] | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_thread_id = thread_id.strip() if isinstance(thread_id, str) else None
        normalized_comment_id = comment_id.strip() if isinstance(comment_id, str) else None
        request = PostPrFeedbackReplyRequest(
            session_id=session_id.strip(),
            provider=normalize_pr_feedback_provider(provider),
            repo=repo.strip(),
            pr_id=pr_id.strip(),
            body_text=body_text.strip(),
            thread_id=normalized_thread_id,
            comment_id=normalized_comment_id,
            mentions=mentions,
            artifact_ids=artifact_ids or [],
            idempotency_key=idempotency_key.strip() if isinstance(idempotency_key, str) else None,
            request_id=request_id.strip() if isinstance(request_id, str) else None,
        )
        args = request.model_dump(mode="json")
        if not request.thread_id and not request.comment_id:
            message = "Either thread_id or comment_id is required for post_pr_feedback_reply"
            audit_failure(session_id, ToolName.POST_PR_FEEDBACK_REPLY, args, message)
            raise PolicyError(message)
        try:
            session = context.policy.require_tool(session_id, ToolName.POST_PR_FEEDBACK_REPLY)
            artifacts = ado_artifacts_for_ids(session, request.artifact_ids)
            approval_payload = ado_artifact_approval_payload(request)
            if request.artifact_ids:
                args["artifacts"] = artifact_audit_summaries(request.artifact_ids)
                context.policy.require_communication_file_send_approval(
                    session,
                    request_id=request.request_id,
                    payload=approval_payload,
                )
            if context.policy.effective_policy_for_session(session).pr_reply_require_approval:
                if not request.request_id:
                    raise PolicyError(
                        "request_id is required when pr_reply_require_approval is enabled"
                    )
                context.policy.require_approved(request.request_id, payload=approval_payload)
            reply_kwargs: dict[str, Any] = {
                "provider": request.provider,
                "repo": request.repo,
                "pr_id": request.pr_id,
                "thread_id": request.thread_id,
                "comment_id": request.comment_id,
                "body_text": request.body_text,
                "attachments": artifacts,
                "idempotency_key": request.idempotency_key,
            }
            if request.mentions:
                reply_kwargs["mentions"] = request.mentions
            result = (
                context.pr_reply_service_for_session(session_id)
                .post_reply(**reply_kwargs)
                .model_dump(mode="json")
            )
            audit_result = {
                "status": result.get("status"),
                "provider": result.get("provider"),
                "reply_id": result.get("reply_id"),
                "reply_url": result.get("reply_url"),
            }
            if result.get("resolved_mentions"):
                audit_result["resolved_mentions"] = result.get("resolved_mentions")
            audit_success(
                session_id,
                ToolName.POST_PR_FEEDBACK_REPLY,
                args,
                audit_result,
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.POST_PR_FEEDBACK_REPLY, args, str(exc))
            raise
        except (httpx.HTTPError, ValueError) as exc:
            audit_failure(
                session_id,
                ToolName.POST_PR_FEEDBACK_REPLY,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.POST_PR_FEEDBACK_REPLY,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def request_pr_review(
        session_id: str,
        provider: Literal["github", "azure_devops"],
        repo: str,
        pr_id: str,
        requested_by: str | None = None,
        request_channel: str | None = None,
        review_type: str | None = None,
        instructions: str | None = None,
        directory_path: str | None = None,
        model: str | None = None,
        labels: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = RequestPrReviewRequest(
            session_id=session_id.strip(),
            provider=normalize_pr_feedback_provider(provider),
            repo=repo.strip(),
            pr_id=pr_id.strip(),
            requested_by=requested_by.strip() if isinstance(requested_by, str) else None,
            request_channel=request_channel.strip() if isinstance(request_channel, str) else None,
            review_type=review_type.strip() if isinstance(review_type, str) else None,
            instructions=instructions.strip() if isinstance(instructions, str) else None,
            directory_path=directory_path.strip() if isinstance(directory_path, str) else None,
            model=model.strip() if isinstance(model, str) else None,
            labels=labels or {},
            metadata=metadata or {},
        )
        args = request.model_dump(mode="json")
        try:
            context.policy.require_tool(request.session_id, ToolName.REQUEST_PR_REVIEW)
            result = (
                context.review_request_service_for_session(request.session_id)
                .enqueue_request(
                    session_id=request.session_id,
                    provider=request.provider,
                    repo=request.repo,
                    pr_id=request.pr_id,
                    requested_by=request.requested_by,
                    request_channel=request.request_channel,
                    review_type=request.review_type,
                    instructions=request.instructions,
                    directory_path=request.directory_path,
                    model=request.model,
                    labels=request.labels,
                    metadata=request.metadata,
                )
                .model_dump(mode="json")
            )
            audit_success(
                request.session_id,
                ToolName.REQUEST_PR_REVIEW,
                args,
                {
                    "status": result.get("status"),
                    "request_id": result.get("request_id"),
                    "provider": result.get("provider"),
                    "repo": result.get("repo"),
                    "pr_id": result.get("pr_id"),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(request.session_id, ToolName.REQUEST_PR_REVIEW, args, str(exc))
            raise
        except (httpx.HTTPError, ValueError) as exc:
            audit_failure(
                request.session_id,
                ToolName.REQUEST_PR_REVIEW,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                request.session_id,
                ToolName.REQUEST_PR_REVIEW,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def post_pr_comment(
        session_id: str,
        provider: str,
        repo: str,
        pr_id: str,
        body_text: str,
        mentions: AdoMentionRefsArgument = None,
        artifact_ids: list[str] | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        request = PostPrCommentRequest(
            session_id=session_id.strip(),
            provider=normalize_pr_feedback_provider(provider),
            repo=repo.strip(),
            pr_id=pr_id.strip(),
            body_text=body_text.strip(),
            mentions=mentions,
            artifact_ids=artifact_ids or [],
            idempotency_key=idempotency_key.strip() if isinstance(idempotency_key, str) else None,
            request_id=request_id.strip() if isinstance(request_id, str) else None,
        )
        args = request.model_dump(mode="json")
        try:
            session = context.policy.require_tool(request.session_id, ToolName.POST_PR_COMMENT)
            artifacts = ado_artifacts_for_ids(session, request.artifact_ids)
            approval_payload = ado_artifact_approval_payload(request)
            if request.artifact_ids:
                args["artifacts"] = artifact_audit_summaries(request.artifact_ids)
                context.policy.require_communication_file_send_approval(
                    session,
                    request_id=request.request_id,
                    payload=approval_payload,
                )
            if context.policy.effective_policy_for_session(session).pr_reply_require_approval:
                if not request.request_id:
                    raise PolicyError(
                        "request_id is required when pr_reply_require_approval is enabled"
                    )
                context.policy.require_approved(request.request_id, payload=approval_payload)
            comment_kwargs: dict[str, Any] = {
                "provider": request.provider,
                "repo": request.repo,
                "pr_id": request.pr_id,
                "body_text": request.body_text,
                "attachments": artifacts,
                "idempotency_key": request.idempotency_key,
            }
            if request.mentions:
                comment_kwargs["mentions"] = request.mentions
            result = context.pr_publish_service_for_session(request.session_id).post_comment(
                **comment_kwargs
            )
            audit_result = {
                "status": result.get("status"),
                "provider": result.get("provider"),
                "comment_id": result.get("comment_id"),
                "comment_url": result.get("comment_url"),
            }
            if result.get("resolved_mentions"):
                audit_result["resolved_mentions"] = result.get("resolved_mentions")
            audit_success(
                request.session_id,
                ToolName.POST_PR_COMMENT,
                args,
                audit_result,
            )
            return result
        except PolicyError as exc:
            audit_failure(request.session_id, ToolName.POST_PR_COMMENT, args, str(exc))
            raise
        except (httpx.HTTPError, ValueError) as exc:
            audit_failure(
                request.session_id,
                ToolName.POST_PR_COMMENT,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                request.session_id,
                ToolName.POST_PR_COMMENT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def post_pr_inline_comment(
        session_id: str,
        provider: str,
        repo: str,
        pr_id: str,
        path: str,
        line: int,
        body_text: str,
        start_line: int | None = None,
        side: str | None = "RIGHT",
        mentions: AdoMentionRefsArgument = None,
        artifact_ids: list[str] | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        request = PostPrInlineCommentRequest(
            session_id=session_id.strip(),
            provider=normalize_pr_feedback_provider(provider),
            repo=repo.strip(),
            pr_id=pr_id.strip(),
            path=path.strip(),
            line=line,
            body_text=body_text.strip(),
            start_line=start_line,
            side=side.strip().upper() if isinstance(side, str) else None,
            mentions=mentions,
            artifact_ids=artifact_ids or [],
            idempotency_key=idempotency_key.strip() if isinstance(idempotency_key, str) else None,
            request_id=request_id.strip() if isinstance(request_id, str) else None,
        )
        args = request.model_dump(mode="json")
        try:
            session = context.policy.require_tool(
                request.session_id, ToolName.POST_PR_INLINE_COMMENT
            )
            artifacts = ado_artifacts_for_ids(session, request.artifact_ids)
            approval_payload = ado_artifact_approval_payload(request)
            if request.artifact_ids:
                args["artifacts"] = artifact_audit_summaries(request.artifact_ids)
                context.policy.require_communication_file_send_approval(
                    session,
                    request_id=request.request_id,
                    payload=approval_payload,
                )
            if context.policy.effective_policy_for_session(session).pr_reply_require_approval:
                if not request.request_id:
                    raise PolicyError(
                        "request_id is required when pr_reply_require_approval is enabled"
                    )
                context.policy.require_approved(request.request_id, payload=approval_payload)
            inline_kwargs: dict[str, Any] = {
                "provider": request.provider,
                "repo": request.repo,
                "pr_id": request.pr_id,
                "path": request.path,
                "line": request.line,
                "body_text": request.body_text,
                "start_line": request.start_line,
                "side": request.side,
                "attachments": artifacts,
                "idempotency_key": request.idempotency_key,
            }
            if request.mentions:
                inline_kwargs["mentions"] = request.mentions
            result = context.pr_publish_service_for_session(request.session_id).post_inline_comment(
                **inline_kwargs
            )
            audit_result = {
                "status": result.get("status"),
                "provider": result.get("provider"),
                "review_id": result.get("review_id"),
                "review_url": result.get("review_url"),
                "comment_count": len(result.get("comment_ids") or []),
            }
            inline_resolved_mentions = result.get("inline_resolved_mentions") or []
            if any(inline_resolved_mentions):
                audit_result["inline_resolved_mentions"] = result.get("inline_resolved_mentions")
            audit_success(
                request.session_id,
                ToolName.POST_PR_INLINE_COMMENT,
                args,
                audit_result,
            )
            return result
        except PolicyError as exc:
            audit_failure(request.session_id, ToolName.POST_PR_INLINE_COMMENT, args, str(exc))
            raise
        except (httpx.HTTPError, ValueError) as exc:
            audit_failure(
                request.session_id,
                ToolName.POST_PR_INLINE_COMMENT,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                request.session_id,
                ToolName.POST_PR_INLINE_COMMENT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def submit_pr_review(
        session_id: str,
        provider: str,
        repo: str,
        pr_id: str,
        body_text: str,
        event: str | None = None,
        comments: list[dict[str, Any]] | None = None,
        mentions: AdoMentionRefsArgument = None,
        artifact_ids: list[str] | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        request = SubmitPrReviewRequest(
            session_id=session_id.strip(),
            provider=normalize_pr_feedback_provider(provider),
            repo=repo.strip(),
            pr_id=pr_id.strip(),
            body_text=body_text.strip(),
            event=event.strip() if isinstance(event, str) else None,
            comments=comments or [],
            mentions=mentions,
            artifact_ids=artifact_ids or [],
            idempotency_key=idempotency_key.strip() if isinstance(idempotency_key, str) else None,
            request_id=request_id.strip() if isinstance(request_id, str) else None,
        )
        args = request.model_dump(mode="json")
        try:
            session = context.policy.require_tool(request.session_id, ToolName.SUBMIT_PR_REVIEW)
            summary_artifacts = ado_artifacts_for_ids(session, request.artifact_ids)
            comment_payloads: list[dict[str, Any]] = []
            comment_artifact_ids: list[str] = []
            for item in request.comments:
                payload = item.model_dump(mode="json")
                artifacts = ado_artifacts_for_ids(session, item.artifact_ids)
                if artifacts:
                    payload["attachments"] = artifacts
                    comment_artifact_ids.extend(item.artifact_ids)
                comment_payloads.append(payload)
            all_artifact_ids = [*request.artifact_ids, *comment_artifact_ids]
            approval_payload = ado_artifact_approval_payload(request)
            if all_artifact_ids:
                args["artifacts"] = artifact_audit_summaries(all_artifact_ids)
                context.policy.require_communication_file_send_approval(
                    session,
                    request_id=request.request_id,
                    payload=approval_payload,
                )
            if context.policy.effective_policy_for_session(session).pr_reply_require_approval:
                if not request.request_id:
                    raise PolicyError(
                        "request_id is required when pr_reply_require_approval is enabled"
                    )
                context.policy.require_approved(request.request_id, payload=approval_payload)
            review_kwargs: dict[str, Any] = {
                "provider": request.provider,
                "repo": request.repo,
                "pr_id": request.pr_id,
                "body_text": request.body_text,
                "event": request.event,
                "comments": comment_payloads,
                "attachments": summary_artifacts,
                "idempotency_key": request.idempotency_key,
            }
            if request.mentions:
                review_kwargs["mentions"] = request.mentions
            result = context.pr_publish_service_for_session(request.session_id).submit_review(
                **review_kwargs
            )
            audit_result = {
                "status": result.get("status"),
                "provider": result.get("provider"),
                "review_id": result.get("review_id"),
                "review_url": result.get("review_url"),
                "comment_count": len(result.get("comment_ids") or []),
            }
            if result.get("resolved_mentions"):
                audit_result["resolved_mentions"] = result.get("resolved_mentions")
            inline_resolved_mentions = result.get("inline_resolved_mentions") or []
            if any(inline_resolved_mentions):
                audit_result["inline_resolved_mentions"] = result.get("inline_resolved_mentions")
            audit_success(
                request.session_id,
                ToolName.SUBMIT_PR_REVIEW,
                args,
                audit_result,
            )
            return result
        except PolicyError as exc:
            audit_failure(request.session_id, ToolName.SUBMIT_PR_REVIEW, args, str(exc))
            raise
        except (httpx.HTTPError, ValueError) as exc:
            audit_failure(
                request.session_id,
                ToolName.SUBMIT_PR_REVIEW,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                request.session_id,
                ToolName.SUBMIT_PR_REVIEW,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise
