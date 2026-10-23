"""Azure DevOps MCP gateway tool registrations."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Annotated, Any

import httpx
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field as PydanticField
from pydantic import StrictInt

from evander_azure_devops_addon.reports import DEFAULT_DONE_STATES, DEFAULT_EFFORT_FIELDS, DEFAULT_IN_PROGRESS_STATES, build_ado_daily_status_report_data
from evander_azure_devops_addon.builds import AdoBuildClient, AdoBuildError
from evander.ado_builds import positive_id as _positive_id
from evander.issues.service import IssueService
from evander.models import (
    ADO_DAILY_STATUS_REPORT_DEFAULT_MAX_ITEMS,
    AdoBacklogCommentRequest,
    AdoBacklogCreateRequest,
    AdoBacklogCreateResponse,
    AdoBacklogDeleteRequest,
    AdoBacklogDeleteResponse,
    AdoBacklogGetRequest,
    AdoBacklogGetResponse,
    AdoBacklogQueryRequest,
    AdoBacklogQueryResponse,
    AdoBacklogStateUpdateRequest,
    AdoBacklogTagsUpdateRequest,
    AdoBacklogTextUpdateRequest,
    AdoBacklogTitleUpdateRequest,
    AdoCurrentIterationBacklogRequest,
    AdoCurrentIterationBacklogResponse,
    AdoDailyStatusReportRequest,
    AdoPrCommentActivityRequest,
    AdoPrCommentActivityResponse,
    AdoPrCommitActivityRequest,
    AdoPrCommitActivityResponse,
    AdoWorkItemActivityRequest,
    AdoWorkItemActivityResponse,
    ResolveAdoIdentityRequest,
    ResolveAdoIdentityResponse,
    ToolName,
)
from evander.policy import PolicyEngine, PolicyError
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


def positive_id(value: Any, field: str) -> int:
    """Keep deliberate identifier validation actionable under SDK 2."""
    try:
        return _positive_id(value, field)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def _ado_inline_media_access(policy: PolicyEngine, session_id: str) -> dict[str, Any]:
    """Explain the existing image receive gates without granting capabilities."""
    required_tools = (
        ToolName.LIST_INLINE_MEDIA,
        ToolName.DOWNLOAD_INLINE_MEDIA,
        ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE,
    )
    unavailable_tools: dict[str, str] = {}
    for tool in required_tools:
        try:
            policy.require_tool(session_id, tool)
        except PolicyError as exc:
            unavailable_tools[tool.value] = str(exc)
    receive_enabled = True
    try:
        policy.require_inline_media_receive_enabled(
            policy.get_session(session_id), source_kind="ado_backlog"
        )
    except PolicyError:
        receive_enabled = False
    return {
        "receive_enabled": receive_enabled,
        "required_tools": [tool.value for tool in required_tools],
        "unavailable_tools": unavailable_tools,
        "instructions": (
            "These are image references, not image contents. If receive_enabled is false, ask an "
            "operator to enable communication_file_receive_enabled. If unavailable_tools is not "
            "empty, ask an operator to grant access to those tools before retrieval. "
            "Use download_inline_media with source_kind=ado_backlog, provider=azure_devops, "
            "resource_id, project and a returned media_id; then use "
            "communication_copy_artifact_to_workspace with the returned artifact_id and a unique "
            "destination_filename for each image. Open the returned destination_path with your "
            "local image viewer. File size, type and tool argument policies still apply."
        ),
    }


def _normalize_ado_tag_list(value: Any) -> list[str]:
    raw_values = value if isinstance(value, list) else str(value or "").split(";")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_tag in raw_values:
        tag = str(raw_tag).strip()
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(tag)
    return normalized


def _extract_ado_tags(remote_payload: dict[str, Any]) -> list[str]:
    fields = remote_payload.get("fields") if isinstance(remote_payload.get("fields"), dict) else {}
    raw_tags = remote_payload.get("tags")
    if raw_tags is None:
        raw_tags = fields.get("System.Tags")
    return _normalize_ado_tag_list(raw_tags)


def _merge_ado_tags(
    previous_tags: list[str], *, add_tags: list[str], remove_tags: list[str]
) -> list[str]:
    remove_keys = {tag.casefold() for tag in remove_tags}
    merged: list[str] = []
    seen: set[str] = set()
    for tag in previous_tags:
        key = tag.casefold()
        if key in remove_keys or key in seen:
            continue
        seen.add(key)
        merged.append(tag)
    for tag in add_tags:
        key = tag.casefold()
        if key in remove_keys or key in seen:
            continue
        seen.add(key)
        merged.append(tag)
    return merged


def register_ado_tools(
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
    """Register Azure DevOps backlog, build, report, identity, and activity tools."""

    if transport_session_map is None:
        transport_session_map = getattr(server, "_evander_transport_session_map", {})

    def _build_client(directory_path: str) -> AdoBuildClient:
        return AdoBuildClient(
            SimpleNamespace(
                provider_config_resolver=context.provider_config,
                resolve_secret=os.environ.get,
            ),
            directory_path=directory_path,
        )

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def list_ado_builds(
        build_number: str | None = None,
        pipeline_id: StrictInt | str | None = None,
        repository_id: str | None = None,
        branch: str | None = None,
        limit: Annotated[StrictInt, PydanticField(ge=1, le=100)] = 25,
        continuation_token: str | None = None,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Find builds in the session directory's Azure DevOps project, newest queued first.

        Filters are optional and combined. build_number matches exactly; append * for a
        prefix search. pipeline_id is a build definition ID, repository_id a repository
        ID, and branch a full ref such as refs/heads/main. Returns all matches on this
        page with pipeline and repository identities; do not assume build numbers are
        unique. Pass continuation_token back with the same filters for the next page.
        Use a returned run_id as build_id with get_ado_build or list_ado_build_work_items.
        """
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": session_id,
            "build_number": build_number,
            "pipeline_id": pipeline_id,
            "repository_id": repository_id,
            "branch": branch,
            "limit": limit,
            "continuation_token": continuation_token,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.LIST_ADO_BUILDS)
            with _build_client(session.directory_path) as client:
                payload = client.search_builds(
                    build_number=build_number,
                    pipeline_id=pipeline_id,
                    repository_id=repository_id,
                    branch=branch,
                    limit=limit,
                    continuation_token=continuation_token,
                )
            audit_success(
                session_id,
                ToolName.LIST_ADO_BUILDS,
                args,
                {
                    "count": payload["count"],
                    "has_more": payload["continuation_token"] is not None,
                    "directory_path": session.directory_path,
                },
            )
            return payload
        except PolicyError as exc:
            audit_failure(session_id, ToolName.LIST_ADO_BUILDS, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.LIST_ADO_BUILDS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            if isinstance(exc, (AdoBuildError, ValueError)):
                raise ToolError(str(exc)) from exc
            raise

    @server.tool()
    def get_ado_build(
        build_id: int | str,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Read one Azure DevOps build, including its source commit and branch."""
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {"session_id": session_id, "build_id": build_id}
        try:
            session = context.policy.require_tool(session_id, ToolName.GET_ADO_BUILD)
            normalized_id = positive_id(build_id, "build_id")
            with _build_client(session.directory_path) as client:
                payload = client.get_build(normalized_id)
            audit_success(
                session_id,
                ToolName.GET_ADO_BUILD,
                args,
                {"build_id": str(normalized_id), "directory_path": session.directory_path},
            )
            return payload
        except PolicyError as exc:
            audit_failure(session_id, ToolName.GET_ADO_BUILD, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.GET_ADO_BUILD,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            if isinstance(exc, AdoBuildError):
                raise ToolError(str(exc)) from exc
            raise

    @server.tool()
    def list_ado_build_work_items(
        build_id: int | str,
        limit: Annotated[int, PydanticField(ge=1, le=1000)] = 200,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """List same-project Azure DevOps work-item IDs associated with one build."""
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {"session_id": session_id, "build_id": build_id, "limit": limit}
        try:
            session = context.policy.require_tool(session_id, ToolName.LIST_ADO_BUILD_WORK_ITEMS)
            normalized_id = positive_id(build_id, "build_id")
            with _build_client(session.directory_path) as client:
                payload = client.get_build_work_item_ids(normalized_id, limit=limit)
            audit_success(
                session_id,
                ToolName.LIST_ADO_BUILD_WORK_ITEMS,
                args,
                {
                    "build_id": str(normalized_id),
                    "count": payload["count"],
                    "truncated": payload["truncated"],
                    "directory_path": session.directory_path,
                },
            )
            return payload
        except PolicyError as exc:
            audit_failure(session_id, ToolName.LIST_ADO_BUILD_WORK_ITEMS, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.LIST_ADO_BUILD_WORK_ITEMS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            if isinstance(exc, AdoBuildError):
                raise ToolError(str(exc)) from exc
            raise

    def _fetch_ado_current_iteration_report_pages(
        issue_service: IssueService,
        *,
        directory_path: str,
        request_model: AdoDailyStatusReportRequest,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], int, bool]:
        items: list[dict[str, Any]] = []
        first_payload: dict[str, Any] = {}
        page_count = 0
        truncated = False
        skip = 0
        while len(items) < request_model.max_items:
            top = min(200, request_model.max_items - len(items))
            remote_payload = issue_service.list_remote_current_iteration_backlog_items(
                provider="azure_devops",
                directory_path=directory_path,
                project=request_model.project,
                team=request_model.team,
                area_path=request_model.area_path,
                states=request_model.states,
                types=[item_type.value for item_type in request_model.types],
                exclude_types=[item_type.value for item_type in request_model.exclude_types],
                tags_any=request_model.tags_any,
                text_search=request_model.text_search,
                top=top,
                skip=skip,
                include_relations=True,
            )
            if not first_payload:
                first_payload = dict(remote_payload)
            page_count += 1
            page_items = [
                item for item in (remote_payload.get("items") or []) if isinstance(item, dict)
            ]
            items.extend(page_items)
            paging = (
                remote_payload.get("paging")
                if isinstance(remote_payload.get("paging"), dict)
                else {}
            )
            total_matches = paging.get("total_matches")
            next_skip = paging.get("next_skip")
            if len(items) >= request_model.max_items:
                if isinstance(total_matches, int):
                    truncated = total_matches > len(items)
                else:
                    truncated = len(page_items) >= top
                break
            if isinstance(next_skip, int) and next_skip > skip:
                skip = next_skip
                continue
            if len(page_items) >= top and top > 0:
                skip += len(page_items)
                continue
            break
        return first_payload, items[: request_model.max_items], page_count, truncated

    @server.tool()
    def create_ado_backlog_item(
        type: str,
        project: str,
        iteration: str,
        title: str,
        description: str | None = None,
        acceptance_criteria: str | None = None,
        description_artifact_ids: list[str] | None = None,
        acceptance_criteria_artifact_ids: list[str] | None = None,
        tags: list[str] | None = None,
        assigned_user: str | None = None,
        parent_backlog_item_id: int | str | None = None,
        request_id: str | None = None,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": session_id,
            "type": type,
            "project": project,
            "iteration": iteration,
            "title": title,
            "description": description,
            "acceptance_criteria": acceptance_criteria,
            "description_artifact_ids": description_artifact_ids,
            "acceptance_criteria_artifact_ids": acceptance_criteria_artifact_ids,
            "tags": tags,
            "assigned_user": assigned_user,
            "parent_backlog_item_id": parent_backlog_item_id,
            "request_id": request_id,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.CREATE_ADO_BACKLOG_ITEM)
            request_model = AdoBacklogCreateRequest(
                type=type,
                project=project,
                iteration=iteration,
                title=title,
                description=description,
                acceptance_criteria=acceptance_criteria,
                description_artifact_ids=description_artifact_ids or [],
                acceptance_criteria_artifact_ids=acceptance_criteria_artifact_ids or [],
                tags=tags or [],
                assigned_user=assigned_user,
                parent_backlog_item_id=parent_backlog_item_id,
                request_id=request_id,
            )
            args = {
                "session_id": session_id,
                **request_model.model_dump(mode="json"),
            }
            issue_service = deps.issue_service_factory()
            description_artifacts = ado_artifacts_for_ids(
                session, request_model.description_artifact_ids
            )
            acceptance_artifacts = ado_artifacts_for_ids(
                session, request_model.acceptance_criteria_artifact_ids
            )
            artifact_ids = [
                *request_model.description_artifact_ids,
                *request_model.acceptance_criteria_artifact_ids,
            ]
            if artifact_ids:
                args["artifacts"] = artifact_audit_summaries(artifact_ids)
                context.policy.require_communication_file_send_approval(
                    session,
                    request_id=request_model.request_id,
                    payload=ado_artifact_approval_payload(request_model),
                )
            create_kwargs: dict[str, Any] = {
                "provider": "azure_devops",
                "directory_path": session.directory_path,
                "project": request_model.project,
                "issue_type": request_model.type.value,
                "title": request_model.title,
                "description": request_model.description,
                "iteration_path": request_model.iteration,
                "acceptance_criteria": request_model.acceptance_criteria,
                "tags": request_model.tags,
                "assigned_to": request_model.assigned_user,
                "parent_backlog_item_id": request_model.parent_backlog_item_id,
            }
            if description_artifacts:
                create_kwargs["description_attachments"] = description_artifacts
            if acceptance_artifacts:
                create_kwargs["acceptance_criteria_attachments"] = acceptance_artifacts
            remote_payload = issue_service.create_remote_issue(**create_kwargs)
            deps.ado_activity_cache.invalidate_directory(session.directory_path)
            remote_fields = (
                remote_payload.get("fields")
                if isinstance(remote_payload.get("fields"), dict)
                else {}
            )
            assigned_to_value = remote_fields.get("System.AssignedTo")
            if isinstance(assigned_to_value, dict):
                normalized_assigned_to = next(
                    (
                        candidate
                        for key in (
                            "displayName",
                            "uniqueName",
                            "mailAddress",
                            "signInAddress",
                            "name",
                        )
                        if (candidate := str(assigned_to_value.get(key) or "").strip())
                    ),
                    None,
                )
            elif assigned_to_value is None:
                normalized_assigned_to = None
            else:
                normalized_assigned_to = str(assigned_to_value).strip() or None
            response_model = AdoBacklogCreateResponse(
                created_item_id=str(
                    remote_payload.get("remote_issue_id") or remote_payload.get("id") or ""
                ).strip(),
                url=str(remote_payload.get("url") or "").strip(),
                provider=str(remote_payload.get("provider") or "azure_devops").strip()
                or "azure_devops",
                project=str(remote_payload.get("project") or request_model.project).strip(),
                type=request_model.type,
                iteration=request_model.iteration,
                title=str((remote_fields.get("System.Title") or request_model.title)).strip(),
                description=remote_fields.get("System.Description") or request_model.description,
                acceptance_criteria=remote_fields.get("Microsoft.VSTS.Common.AcceptanceCriteria")
                or request_model.acceptance_criteria,
                tags=request_model.tags,
                assigned_user=normalized_assigned_to or request_model.assigned_user,
                parent_backlog_item_id=request_model.parent_backlog_item_id,
                uploaded_attachments=remote_payload.get("uploaded_attachments") or [],
            )
            payload = response_model.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.CREATE_ADO_BACKLOG_ITEM,
                args,
                {
                    "created_item_id": payload.get("created_item_id"),
                    "project": payload.get("project", request_model.project),
                    "type": payload.get("type", request_model.type.value),
                    "directory_path": session.directory_path,
                },
            )
            return payload
        except PolicyError as exc:
            audit_failure(session_id, ToolName.CREATE_ADO_BACKLOG_ITEM, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.CREATE_ADO_BACKLOG_ITEM,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def post_ado_backlog_comment(
        id: int | str,
        comment: str,
        project: str | None = None,
        mentions: AdoMentionRefsArgument = None,
        artifact_ids: list[str] | None = None,
        request_id: str | None = None,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        request_model = AdoBacklogCommentRequest(
            id=id,
            project=project,
            comment=comment,
            mentions=mentions,
            artifact_ids=artifact_ids or [],
            request_id=request_id,
        )
        args = {"session_id": session_id, **request_model.model_dump(mode="json")}
        try:
            session = context.policy.require_tool(session_id, ToolName.POST_ADO_BACKLOG_COMMENT)
            artifacts = ado_artifacts_for_ids(session, request_model.artifact_ids)
            if request_model.artifact_ids:
                args["artifacts"] = artifact_audit_summaries(request_model.artifact_ids)
                context.policy.require_communication_file_send_approval(
                    session,
                    request_id=request_model.request_id,
                    payload=ado_artifact_approval_payload(request_model),
                )
            result = deps.issue_service_factory().post_remote_comment(
                provider="azure_devops",
                remote_issue_id=request_model.id,
                directory_path=session.directory_path,
                project=request_model.project,
                comment=request_model.comment,
                mentions=request_model.mentions or None,
                attachments=artifacts,
            )
            deps.ado_activity_cache.invalidate_directory(session.directory_path)
            audit_result = {
                "work_item_id": request_model.id,
                "project": request_model.project,
                "comment_id": (result.get("comment") or {}).get("id"),
                "uploaded_attachment_count": len(result.get("uploaded_attachments") or []),
            }
            if result.get("resolved_mentions"):
                audit_result["resolved_mentions"] = result.get("resolved_mentions")
            audit_success(
                session_id,
                ToolName.POST_ADO_BACKLOG_COMMENT,
                args,
                audit_result,
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.POST_ADO_BACKLOG_COMMENT, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.POST_ADO_BACKLOG_COMMENT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def delete_ado_backlog_comment(
        id: StrictInt | str,
        comment_id: StrictInt | str,
        project: str | None = None,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Delete one Azure DevOps work-item comment using IDs from get_ado_backlog_item."""
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {"session_id": session_id, "id": id, "comment_id": comment_id, "project": project}
        try:
            session = context.policy.require_tool(session_id, ToolName.DELETE_ADO_BACKLOG_COMMENT)
            work_item_id = str(positive_id(id.strip() if isinstance(id, str) else id, "id"))
            normalized_comment_id = str(
                positive_id(
                    comment_id.strip() if isinstance(comment_id, str) else comment_id, "comment_id"
                )
            )
            result = deps.issue_service_factory().delete_remote_comment(
                provider="azure_devops",
                remote_issue_id=work_item_id,
                comment_id=normalized_comment_id,
                directory_path=session.directory_path,
                project=project.strip() if isinstance(project, str) and project.strip() else None,
            )
            deps.ado_activity_cache.invalidate_directory(session.directory_path)
            audit_success(session_id, ToolName.DELETE_ADO_BACKLOG_COMMENT, args, result)
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.DELETE_ADO_BACKLOG_COMMENT, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.DELETE_ADO_BACKLOG_COMMENT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def delete_ado_backlog_item(
        id: int | str,
        project: str | None = None,
        destroy: bool = False,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        request_model = AdoBacklogDeleteRequest(
            id=id,
            project=project,
            destroy=destroy,
        )
        args = {"session_id": session_id, **request_model.model_dump(mode="json")}
        try:
            session = context.policy.require_tool(session_id, ToolName.DELETE_ADO_BACKLOG_ITEM)
            remote_payload = deps.issue_service_factory().delete_remote_issue(
                provider="azure_devops",
                remote_issue_id=request_model.id,
                directory_path=session.directory_path,
                project=request_model.project,
                destroy=request_model.destroy,
            )
            deps.ado_activity_cache.invalidate_directory(session.directory_path)
            payload_source = remote_payload if isinstance(remote_payload, dict) else {}
            normalized_destroy = bool(payload_source.get("destroy", request_model.destroy))
            response_model = AdoBacklogDeleteResponse.model_validate(
                {
                    "provider": str(payload_source.get("provider") or "azure_devops").strip()
                    or "azure_devops",
                    "work_item_id": str(
                        payload_source.get("work_item_id")
                        or payload_source.get("remote_issue_id")
                        or payload_source.get("id")
                        or request_model.id
                    ).strip()
                    or request_model.id,
                    "project": str(
                        payload_source.get("project") or request_model.project or ""
                    ).strip()
                    or request_model.project,
                    "deleted": bool(payload_source.get("deleted", True)),
                    "destroy": normalized_destroy,
                    "delete_kind": str(
                        payload_source.get("delete_kind")
                        or ("permanent_destroy" if normalized_destroy else "soft_delete")
                    ).strip(),
                }
            )
            payload = response_model.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.DELETE_ADO_BACKLOG_ITEM,
                args,
                {
                    "work_item_id": payload.get("work_item_id"),
                    "project": payload.get("project"),
                    "deleted": payload.get("deleted"),
                    "destroy": payload.get("destroy"),
                    "delete_kind": payload.get("delete_kind"),
                    "directory_path": session.directory_path,
                },
            )
            return payload
        except PolicyError as exc:
            audit_failure(session_id, ToolName.DELETE_ADO_BACKLOG_ITEM, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.DELETE_ADO_BACKLOG_ITEM,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def update_ado_backlog_text(
        id: int | str,
        project: str | None = None,
        description: str | None = None,
        acceptance_criteria: str | None = None,
        description_artifact_ids: list[str] | None = None,
        acceptance_criteria_artifact_ids: list[str] | None = None,
        request_id: str | None = None,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        request_model = AdoBacklogTextUpdateRequest(
            id=id,
            project=project,
            description=description,
            acceptance_criteria=acceptance_criteria,
            description_artifact_ids=description_artifact_ids or [],
            acceptance_criteria_artifact_ids=acceptance_criteria_artifact_ids or [],
            request_id=request_id,
        )
        args = {"session_id": session_id, **request_model.model_dump(mode="json")}
        try:
            session = context.policy.require_tool(session_id, ToolName.UPDATE_ADO_BACKLOG_TEXT)
            description_artifacts = ado_artifacts_for_ids(
                session, request_model.description_artifact_ids
            )
            acceptance_artifacts = ado_artifacts_for_ids(
                session, request_model.acceptance_criteria_artifact_ids
            )
            artifact_ids = [
                *request_model.description_artifact_ids,
                *request_model.acceptance_criteria_artifact_ids,
            ]
            if artifact_ids:
                args["artifacts"] = artifact_audit_summaries(artifact_ids)
                context.policy.require_communication_file_send_approval(
                    session,
                    request_id=request_model.request_id,
                    payload=ado_artifact_approval_payload(request_model),
                )
            fields: dict[str, Any] = {}
            field_attachments: dict[str, list[dict[str, Any]]] = {}
            if request_model.description is not None or description_artifacts:
                fields["System.Description"] = request_model.description or ""
                field_attachments["System.Description"] = description_artifacts
            if request_model.acceptance_criteria is not None or acceptance_artifacts:
                fields["Microsoft.VSTS.Common.AcceptanceCriteria"] = (
                    request_model.acceptance_criteria or ""
                )
                field_attachments["Microsoft.VSTS.Common.AcceptanceCriteria"] = acceptance_artifacts
            result = deps.issue_service_factory().update_remote_issue(
                provider="azure_devops",
                remote_issue_id=request_model.id,
                directory_path=session.directory_path,
                project=request_model.project,
                state=None,
                fields=fields,
                field_attachments=field_attachments,
            )
            deps.ado_activity_cache.invalidate_directory(session.directory_path)
            audit_success(
                session_id,
                ToolName.UPDATE_ADO_BACKLOG_TEXT,
                args,
                {
                    "work_item_id": request_model.id,
                    "project": request_model.project,
                    "updated": result.get("updated"),
                    "uploaded_attachment_count": len(result.get("uploaded_attachments") or []),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.UPDATE_ADO_BACKLOG_TEXT, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.UPDATE_ADO_BACKLOG_TEXT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def update_ado_backlog_title(
        id: int | str,
        title: str,
        project: str | None = None,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        request_model = AdoBacklogTitleUpdateRequest(
            id=id,
            project=project,
            title=title,
        )
        args = {"session_id": session_id, **request_model.model_dump(mode="json")}
        try:
            session = context.policy.require_tool(session_id, ToolName.UPDATE_ADO_BACKLOG_TITLE)
            result = deps.issue_service_factory().update_remote_issue(
                provider="azure_devops",
                remote_issue_id=request_model.id,
                directory_path=session.directory_path,
                project=request_model.project,
                state=None,
                fields={"System.Title": request_model.title},
            )
            deps.ado_activity_cache.invalidate_directory(session.directory_path)
            result_fields = result.get("fields")
            updated_title = request_model.title
            if isinstance(result_fields, dict) and "System.Title" in result_fields:
                updated_title = result_fields["System.Title"]
            response = {
                **result,
                "work_item_id": request_model.id,
                "title": updated_title,
            }
            audit_success(
                session_id,
                ToolName.UPDATE_ADO_BACKLOG_TITLE,
                args,
                {
                    "work_item_id": request_model.id,
                    "project": request_model.project,
                    "updated": result.get("updated"),
                    "title": updated_title,
                },
            )
            return response
        except PolicyError as exc:
            audit_failure(session_id, ToolName.UPDATE_ADO_BACKLOG_TITLE, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.UPDATE_ADO_BACKLOG_TITLE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def update_ado_backlog_state(
        id: int | str,
        state: str,
        project: str | None = None,
        expected_state: str | None = None,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Update only System.State using the session's host-owned ADO connection.

        Use a state name valid for this item's ADO process. Pass expected_state to
        atomically reject an update if the item has moved to another state. For
        example, state="In Progress", expected_state="Committed". On a conflict
        or uncertain result, read the item with get_ado_backlog_item before
        retrying; do not retry unconditionally. This tool requires its own grant.
        """
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        request_model = AdoBacklogStateUpdateRequest(
            id=id,
            project=project,
            state=state,
            expected_state=expected_state,
        )
        args = {"session_id": session_id, **request_model.model_dump(mode="json")}
        try:
            session = context.policy.require_tool(session_id, ToolName.UPDATE_ADO_BACKLOG_STATE)
            result = deps.issue_service_factory().update_remote_issue(
                provider="azure_devops",
                remote_issue_id=request_model.id,
                directory_path=session.directory_path,
                project=request_model.project,
                state=request_model.state,
                expected_state=request_model.expected_state,
                fields=None,
            )
            deps.ado_activity_cache.invalidate_directory(session.directory_path)
            response = {**result, "work_item_id": request_model.id}
            audit_success(
                session_id,
                ToolName.UPDATE_ADO_BACKLOG_STATE,
                args,
                {
                    "work_item_id": request_model.id,
                    "project": request_model.project,
                    "state": result.get("state"),
                    "expected_state": request_model.expected_state,
                    "updated": result.get("updated"),
                },
            )
            return response
        except PolicyError as exc:
            audit_failure(session_id, ToolName.UPDATE_ADO_BACKLOG_STATE, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.UPDATE_ADO_BACKLOG_STATE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def update_ado_backlog_tags(
        id: int | str,
        project: str | None = None,
        add_tags: list[str] | None = None,
        remove_tags: list[str] | None = None,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        request_model = AdoBacklogTagsUpdateRequest(
            id=id,
            project=project,
            add_tags=add_tags or [],
            remove_tags=remove_tags or [],
        )
        args = {"session_id": session_id, **request_model.model_dump(mode="json")}
        try:
            session = context.policy.require_tool(session_id, ToolName.UPDATE_ADO_BACKLOG_TAGS)
            issue_service = deps.issue_service_factory()
            current = issue_service.read_remote_issue(
                provider="azure_devops",
                remote_issue_id=request_model.id,
                directory_path=session.directory_path,
                project=request_model.project,
                include_comments=False,
            )
            previous_tags = _extract_ado_tags(current)
            merged_tags = _merge_ado_tags(
                previous_tags,
                add_tags=request_model.add_tags,
                remove_tags=request_model.remove_tags,
            )
            result = issue_service.update_remote_issue(
                provider="azure_devops",
                remote_issue_id=request_model.id,
                directory_path=session.directory_path,
                project=request_model.project,
                state=None,
                fields={"System.Tags": "; ".join(merged_tags)},
            )
            deps.ado_activity_cache.invalidate_directory(session.directory_path)
            response = {
                **result,
                "work_item_id": request_model.id,
                "previous_tags": previous_tags,
                "tags": merged_tags,
                "added_tags": [
                    tag
                    for tag in request_model.add_tags
                    if tag.casefold() not in {item.casefold() for item in previous_tags}
                ],
                "removed_tags": [
                    tag
                    for tag in previous_tags
                    if tag.casefold() in {item.casefold() for item in request_model.remove_tags}
                ],
            }
            audit_success(
                session_id,
                ToolName.UPDATE_ADO_BACKLOG_TAGS,
                args,
                {
                    "work_item_id": request_model.id,
                    "project": request_model.project,
                    "updated": result.get("updated"),
                    "tag_count": len(merged_tags),
                    "added_tag_count": len(response["added_tags"]),
                    "removed_tag_count": len(response["removed_tags"]),
                },
            )
            return response
        except PolicyError as exc:
            audit_failure(session_id, ToolName.UPDATE_ADO_BACKLOG_TAGS, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.UPDATE_ADO_BACKLOG_TAGS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def query_ado_backlog_items(
        project: str | None = None,
        iteration: str | None = None,
        area_path: str | None = None,
        states: list[str] | None = None,
        types: list[str] | None = None,
        tags_any: list[str] | None = None,
        text_search: str | None = None,
        top: int = 50,
        skip: int = 0,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": session_id,
            "project": project,
            "iteration": iteration,
            "area_path": area_path,
            "states": states,
            "types": types,
            "tags_any": tags_any,
            "text_search": text_search,
            "top": top,
            "skip": skip,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.QUERY_ADO_BACKLOG_ITEMS)
            request_model = AdoBacklogQueryRequest(
                project=project,
                iteration=iteration,
                area_path=area_path,
                states=states or [],
                types=types or [],
                tags_any=tags_any or [],
                text_search=text_search,
                top=top,
                skip=skip,
            )
            args = {"session_id": session_id, **request_model.model_dump(mode="json")}
            issue_service = deps.issue_service_factory()
            remote_payload = issue_service.query_remote_backlog_items(
                provider="azure_devops",
                directory_path=session.directory_path,
                project=request_model.project,
                iteration=request_model.iteration,
                area_path=request_model.area_path,
                states=request_model.states,
                types=[item_type.value for item_type in request_model.types],
                tags_any=request_model.tags_any,
                text_search=request_model.text_search,
                top=request_model.top,
                skip=request_model.skip,
            )
            response_model = AdoBacklogQueryResponse.model_validate(
                {
                    "items": remote_payload.get("items") or [],
                    "count": remote_payload.get("count", 0),
                    "paging": remote_payload.get("paging")
                    or {
                        "skip": request_model.skip,
                        "top": request_model.top,
                        "returned_count": 0,
                        "total_matches": 0,
                        "has_more": False,
                        "next_skip": None,
                    },
                }
            )
            payload = response_model.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.QUERY_ADO_BACKLOG_ITEMS,
                args,
                {
                    "project": request_model.project,
                    "count": payload.get("count", 0),
                    "has_more": ((payload.get("paging") or {}).get("has_more", False)),
                    "directory_path": session.directory_path,
                },
            )
            return payload
        except PolicyError as exc:
            audit_failure(session_id, ToolName.QUERY_ADO_BACKLOG_ITEMS, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.QUERY_ADO_BACKLOG_ITEMS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def get_ado_backlog_item(
        id: int | str,
        project: str | None = None,
        include_comments: bool = True,
        include_relations: bool = True,
        include_updates: bool = False,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Read an ADO work item, including HTML/Markdown image references.

        inline_media contains references, not image bytes. When images are present,
        metadata.inline_media_access explains permissions and the download/copy/view
        steps. Reading the item does not enable file reception or grant media tools.
        """
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": session_id,
            "id": id,
            "project": project,
            "include_comments": include_comments,
            "include_relations": include_relations,
            "include_updates": include_updates,
        }
        try:
            session = context.policy.require_tool(session_id, ToolName.GET_ADO_BACKLOG_ITEM)
            request_model = AdoBacklogGetRequest(
                id=id,
                project=project,
                include_comments=include_comments,
                include_relations=include_relations,
                include_updates=include_updates,
            )
            args = {"session_id": session_id, **request_model.model_dump(mode="json")}
            issue_service = deps.issue_service_factory()
            remote_payload = issue_service.get_remote_backlog_item_by_id(
                provider="azure_devops",
                directory_path=session.directory_path,
                backlog_item_id=request_model.id,
                project=request_model.project,
                include_comments=request_model.include_comments,
                include_relations=request_model.include_relations,
                include_updates=request_model.include_updates,
            )
            response_model = AdoBacklogGetResponse.model_validate(remote_payload)
            payload = response_model.model_dump(mode="json")
            if payload.get("inline_media"):
                payload["metadata"]["inline_media_access"] = _ado_inline_media_access(
                    context.policy, session_id
                )
            audit_success(
                session_id,
                ToolName.GET_ADO_BACKLOG_ITEM,
                args,
                {
                    "id": payload.get("id"),
                    "project": payload.get("project"),
                    "include_comments": request_model.include_comments,
                    "include_relations": request_model.include_relations,
                    "include_updates": request_model.include_updates,
                    "directory_path": session.directory_path,
                },
            )
            return payload
        except PolicyError as exc:
            audit_failure(session_id, ToolName.GET_ADO_BACKLOG_ITEM, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.GET_ADO_BACKLOG_ITEM,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def query_ado_work_item_activity(
        since: str,
        project: str | None = None,
        actor: str | None = None,
        until: str | None = None,
        activity_types: list[str] | None = None,
        states: list[str] | None = None,
        types: list[str] | None = None,
        tags_any: list[str] | None = None,
        top: int = 100,
        skip: int = 0,
        exhaustive: bool = False,
        max_candidates: int = 50,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        request = AdoWorkItemActivityRequest(
            session_id=session_id,
            project=project,
            actor=actor,
            since=since,
            until=until,
            activity_types=activity_types or ["created", "changed", "commented"],
            states=states or [],
            types=types or [],
            tags_any=tags_any or [],
            top=top,
            skip=skip,
            exhaustive=exhaustive,
            max_candidates=max_candidates,
        )
        args = request.model_dump(mode="json")
        try:
            session = context.policy.require_tool(
                request.session_id, ToolName.QUERY_ADO_WORK_ITEM_ACTIVITY
            )
            issue_service = deps.issue_service_factory()
            remote_payload = issue_service.query_remote_work_item_activity(
                provider="azure_devops",
                directory_path=session.directory_path,
                project=request.project,
                actor=request.actor,
                since=request.since,
                until=request.until,
                activity_types=request.activity_types,
                states=request.states,
                types=[item_type.value for item_type in request.types],
                tags_any=request.tags_any,
                top=request.top,
                skip=request.skip,
                exhaustive=request.exhaustive,
                max_candidates=request.max_candidates,
                _activity_cache=deps.ado_activity_cache,
                _cache_scope=request.session_id,
            )
            response_model = AdoWorkItemActivityResponse.model_validate(remote_payload)
            payload = response_model.model_dump(mode="json")
            audit_success(
                request.session_id,
                ToolName.QUERY_ADO_WORK_ITEM_ACTIVITY,
                args,
                {
                    "project": request.project,
                    "count": payload.get("count", 0),
                    "directory_path": session.directory_path,
                },
            )
            return payload
        except PolicyError as exc:
            audit_failure(request.session_id, ToolName.QUERY_ADO_WORK_ITEM_ACTIVITY, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                request.session_id,
                ToolName.QUERY_ADO_WORK_ITEM_ACTIVITY,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def list_ado_current_iteration_backlog_items(
        project: str | None = None,
        team: str | None = None,
        area_path: str | None = None,
        states: list[str] | None = None,
        types: list[str] | None = None,
        tags_any: list[str] | None = None,
        text_search: str | None = None,
        top: int = 50,
        skip: int = 0,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": session_id,
            "project": project,
            "team": team,
            "area_path": area_path,
            "states": states,
            "types": types,
            "tags_any": tags_any,
            "text_search": text_search,
            "top": top,
            "skip": skip,
        }
        try:
            session = context.policy.require_tool(
                session_id, ToolName.LIST_ADO_CURRENT_ITERATION_BACKLOG_ITEMS
            )
            request_model = AdoCurrentIterationBacklogRequest(
                project=project,
                team=team,
                area_path=area_path,
                states=states or [],
                types=types or [],
                tags_any=tags_any or [],
                text_search=text_search,
                top=top,
                skip=skip,
            )
            args = {"session_id": session_id, **request_model.model_dump(mode="json")}
            issue_service = deps.issue_service_factory()
            remote_payload = issue_service.list_remote_current_iteration_backlog_items(
                provider="azure_devops",
                directory_path=session.directory_path,
                project=request_model.project,
                team=request_model.team,
                area_path=request_model.area_path,
                states=request_model.states,
                types=[item_type.value for item_type in request_model.types],
                tags_any=request_model.tags_any,
                text_search=request_model.text_search,
                top=request_model.top,
                skip=request_model.skip,
            )
            response_model = AdoCurrentIterationBacklogResponse.model_validate(
                {
                    "project": remote_payload.get("project"),
                    "team": remote_payload.get("team"),
                    "iteration_path": remote_payload.get("iteration_path") or "",
                    "items": remote_payload.get("items") or [],
                    "count": remote_payload.get("count", 0),
                }
            )
            payload = response_model.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.LIST_ADO_CURRENT_ITERATION_BACKLOG_ITEMS,
                args,
                {
                    "project": payload.get("project") or request_model.project,
                    "requested_team": request_model.team,
                    "team": payload.get("team") or request_model.team,
                    "iteration_path": payload.get("iteration_path"),
                    "count": payload.get("count", 0),
                    "directory_path": session.directory_path,
                },
            )
            return payload
        except PolicyError as exc:
            audit_failure(
                session_id, ToolName.LIST_ADO_CURRENT_ITERATION_BACKLOG_ITEMS, args, str(exc)
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.LIST_ADO_CURRENT_ITERATION_BACKLOG_ITEMS,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def get_ado_daily_status_report_data(
        project: str | None = None,
        team: str | None = None,
        area_path: str | None = None,
        states: list[str] | None = None,
        types: list[str] | None = None,
        exclude_types: list[str] | None = None,
        tags_any: list[str] | None = None,
        text_search: str | None = None,
        title: str = "Daily Sprint Status",
        report_date: str | None = None,
        sprint_label: str | None = None,
        sprint_length: str | None = None,
        elapsed_sprint_percent: float | None = None,
        elapsed_time_mode: str = "swedish_work_time",
        workday_start: str = "08:00",
        workday_end: str = "17:00",
        include_common_swedish_non_working_days: bool = True,
        done_states: list[str] | None = None,
        in_progress_states: list[str] | None = None,
        effort_fields: list[str] | None = None,
        max_items: int = ADO_DAILY_STATUS_REPORT_DEFAULT_MAX_ITEMS,
        *,
        session_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Build the JSON data block for templates/daily-status-report.html from ADO.

        Leave elapsed_sprint_percent unset unless elapsed_time_mode is manual_override.
        """
        session_id = _resolve_session_id_from_context(
            session_id, ctx, transport_session_map=transport_session_map
        )
        args = {
            "session_id": session_id,
            "project": project,
            "team": team,
            "area_path": area_path,
            "states": states,
            "types": types,
            "exclude_types": exclude_types,
            "tags_any": tags_any,
            "text_search": text_search,
            "title": title,
            "report_date": report_date,
            "sprint_label": sprint_label,
            "sprint_length": sprint_length,
            "elapsed_sprint_percent": elapsed_sprint_percent,
            "elapsed_time_mode": elapsed_time_mode,
            "workday_start": workday_start,
            "workday_end": workday_end,
            "include_common_swedish_non_working_days": include_common_swedish_non_working_days,
            "done_states": done_states,
            "in_progress_states": in_progress_states,
            "effort_fields": effort_fields,
            "max_items": max_items,
        }
        try:
            session = context.policy.require_tool(
                session_id, ToolName.GET_ADO_DAILY_STATUS_REPORT_DATA
            )
            request_model = AdoDailyStatusReportRequest(
                project=project,
                team=team,
                area_path=area_path,
                states=states or [],
                types=types or [],
                exclude_types=exclude_types if exclude_types is not None else ["Task"],
                tags_any=tags_any or [],
                text_search=text_search,
                title=title,
                report_date=report_date,
                sprint_label=sprint_label,
                sprint_length=sprint_length,
                elapsed_sprint_percent=elapsed_sprint_percent,
                elapsed_time_mode=elapsed_time_mode,
                workday_start=workday_start,
                workday_end=workday_end,
                include_common_swedish_non_working_days=(include_common_swedish_non_working_days),
                done_states=done_states or [],
                in_progress_states=in_progress_states or [],
                effort_fields=effort_fields or [],
                max_items=max_items,
            )
            args = {"session_id": session_id, **request_model.model_dump(mode="json")}
            issue_service = deps.issue_service_factory()
            first_payload, items, page_count, truncated = _fetch_ado_current_iteration_report_pages(
                issue_service,
                directory_path=session.directory_path,
                request_model=request_model,
            )
            payload = build_ado_daily_status_report_data(
                items=items,
                project=first_payload.get("project") or request_model.project,
                team=first_payload.get("team") or request_model.team,
                iteration_path=first_payload.get("iteration_path"),
                iteration_start_date=first_payload.get("iteration_start_date"),
                iteration_finish_date=first_payload.get("iteration_finish_date"),
                report_date=request_model.report_date,
                title=request_model.title,
                sprint_label=request_model.sprint_label,
                sprint_length=request_model.sprint_length,
                elapsed_sprint_percent=request_model.elapsed_sprint_percent,
                elapsed_time_mode=request_model.elapsed_time_mode,
                workday_start=request_model.workday_start,
                workday_end=request_model.workday_end,
                include_common_swedish_non_working_days=(
                    request_model.include_common_swedish_non_working_days
                ),
                done_states=request_model.done_states or DEFAULT_DONE_STATES,
                in_progress_states=request_model.in_progress_states or DEFAULT_IN_PROGRESS_STATES,
                effort_fields=request_model.effort_fields or DEFAULT_EFFORT_FIELDS,
                truncated=truncated,
                page_count=page_count,
                max_items=request_model.max_items,
            )
            payload["source"]["excluded_types"] = [
                item_type.value for item_type in request_model.exclude_types
            ]
            template_items = payload["template_data"]["items"]
            audit_success(
                session_id,
                ToolName.GET_ADO_DAILY_STATUS_REPORT_DATA,
                args,
                {
                    "project": payload["source"].get("project"),
                    "team": payload["source"].get("team"),
                    "iteration_path": payload["source"].get("iteration_path"),
                    "item_count": template_items.get("total"),
                    "done_count": template_items.get("done"),
                    "with_pr_count": template_items.get("withPrs"),
                    "directory_path": session.directory_path,
                },
            )
            return payload
        except PolicyError as exc:
            audit_failure(session_id, ToolName.GET_ADO_DAILY_STATUS_REPORT_DATA, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.GET_ADO_DAILY_STATUS_REPORT_DATA,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def resolve_ado_identity(
        session_id: str,
        query: str,
        project: str | None = None,
    ) -> dict[str, Any]:
        request = ResolveAdoIdentityRequest(
            session_id=session_id.strip(),
            query=query,
            project=project,
        )
        args = request.model_dump(mode="json")
        try:
            context.policy.require_tool(request.session_id, ToolName.RESOLVE_ADO_IDENTITY)
            response = context.pr_query_service_for_session(
                request.session_id
            ).resolve_ado_identity(
                query=request.query,
                project=request.project,
            )
            result = ResolveAdoIdentityResponse.model_validate(response).model_dump(mode="json")
            audit_success(
                request.session_id,
                ToolName.RESOLVE_ADO_IDENTITY,
                args,
                {
                    "confidence": result.get("confidence"),
                    "match_count": len(result.get("matches") or []),
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(request.session_id, ToolName.RESOLVE_ADO_IDENTITY, args, str(exc))
            raise
        except (httpx.HTTPError, ValueError) as exc:
            audit_failure(
                request.session_id,
                ToolName.RESOLVE_ADO_IDENTITY,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                request.session_id,
                ToolName.RESOLVE_ADO_IDENTITY,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def list_ado_pr_comment_activity(
        session_id: str,
        repo: str,
        since: str,
        author: str | None = None,
        until: str | None = None,
        project: str | None = None,
        statuses: list[str] | None = None,
        include_system_comments: bool = False,
        top: int = 100,
        skip: int = 0,
        exhaustive: bool = False,
        max_candidates: int = 50,
    ) -> dict[str, Any]:
        request = AdoPrCommentActivityRequest(
            session_id=session_id.strip(),
            repo=repo,
            author=author,
            since=since,
            until=until,
            project=project,
            statuses=statuses or [],
            include_system_comments=include_system_comments,
            top=top,
            skip=skip,
            exhaustive=exhaustive,
            max_candidates=max_candidates,
        )
        args = request.model_dump(mode="json")
        try:
            context.policy.require_tool(request.session_id, ToolName.LIST_ADO_PR_COMMENT_ACTIVITY)
            response = context.pr_query_service_for_session(
                request.session_id
            ).list_ado_pr_comment_activity(
                repo=request.repo,
                author=request.author,
                since=request.since,
                until=request.until,
                project=request.project,
                statuses=request.statuses,
                include_system_comments=request.include_system_comments,
                top=request.top,
                skip=request.skip,
                exhaustive=request.exhaustive,
                max_candidates=request.max_candidates,
            )
            result = AdoPrCommentActivityResponse.model_validate(response).model_dump(mode="json")
            audit_success(
                request.session_id,
                ToolName.LIST_ADO_PR_COMMENT_ACTIVITY,
                args,
                {"repo": request.repo, "count": result.get("count", 0)},
            )
            return result
        except PolicyError as exc:
            audit_failure(request.session_id, ToolName.LIST_ADO_PR_COMMENT_ACTIVITY, args, str(exc))
            raise
        except (httpx.HTTPError, ValueError) as exc:
            audit_failure(
                request.session_id,
                ToolName.LIST_ADO_PR_COMMENT_ACTIVITY,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                request.session_id,
                ToolName.LIST_ADO_PR_COMMENT_ACTIVITY,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    @server.tool()
    def list_ado_pr_commit_activity(
        session_id: str,
        repo: str,
        since: str,
        author: str | None = None,
        until: str | None = None,
        project: str | None = None,
        statuses: list[str] | None = None,
        top: int = 100,
        skip: int = 0,
        exhaustive: bool = False,
        max_candidates: int = 50,
    ) -> dict[str, Any]:
        request = AdoPrCommitActivityRequest(
            session_id=session_id.strip(),
            repo=repo,
            author=author,
            since=since,
            until=until,
            project=project,
            statuses=statuses or [],
            top=top,
            skip=skip,
            exhaustive=exhaustive,
            max_candidates=max_candidates,
        )
        args = request.model_dump(mode="json")
        try:
            context.policy.require_tool(request.session_id, ToolName.LIST_ADO_PR_COMMIT_ACTIVITY)
            response = context.pr_query_service_for_session(
                request.session_id
            ).list_ado_pr_commit_activity(
                repo=request.repo,
                author=request.author,
                since=request.since,
                until=request.until,
                project=request.project,
                statuses=request.statuses,
                top=request.top,
                skip=request.skip,
                exhaustive=request.exhaustive,
                max_candidates=request.max_candidates,
            )
            result = AdoPrCommitActivityResponse.model_validate(response).model_dump(mode="json")
            audit_success(
                request.session_id,
                ToolName.LIST_ADO_PR_COMMIT_ACTIVITY,
                args,
                {"repo": request.repo, "count": result.get("count", 0)},
            )
            return result
        except PolicyError as exc:
            audit_failure(request.session_id, ToolName.LIST_ADO_PR_COMMIT_ACTIVITY, args, str(exc))
            raise
        except (httpx.HTTPError, ValueError) as exc:
            audit_failure(
                request.session_id,
                ToolName.LIST_ADO_PR_COMMIT_ACTIVITY,
                args,
                str(exc),
                decision="error",
                error_class="adapter_http_error",
            )
            raise
        except Exception as exc:
            audit_failure(
                request.session_id,
                ToolName.LIST_ADO_PR_COMMIT_ACTIVITY,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise
