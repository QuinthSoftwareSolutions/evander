"""Backlog request construction shared by Azure DevOps polling triggers."""

from __future__ import annotations

from typing import Any

from evander.ado_query_mode import ADO_QUERY_MODE_MANUAL_WIQL
from evander.events.provider_models import (
    AdoWorkItemCommentCreatedTriggerDefinition,
    AdoWorkItemMatchingTriggerDefinition,
)
from evander_azure_devops_addon.issues import AdoCommentPollReader
from evander.issues.service import IssueService
from evander.models import AdoBacklogQueryRequest, AdoCurrentIterationBacklogRequest


def query_ado_work_items(
    trigger: AdoWorkItemMatchingTriggerDefinition | AdoWorkItemCommentCreatedTriggerDefinition,
    issue_service: IssueService | AdoCommentPollReader,
    *,
    directory_path: str,
) -> Any:
    if trigger.current_iteration:
        request = AdoCurrentIterationBacklogRequest.model_validate(
            {
                "project": trigger.project,
                "team": trigger.team,
                "area_path": trigger.area_path,
                "states": trigger.states,
                "types": trigger.types,
                "tags_any": trigger.tags_any,
                "text_search": trigger.text_search,
                "top": trigger.top,
                "skip": 0,
            }
        )
        return issue_service.list_remote_current_iteration_backlog_items(
            provider="azure_devops",
            directory_path=directory_path,
            project=request.project,
            team=request.team,
            area_path=request.area_path,
            states=request.states,
            types=[item_type.value for item_type in request.types],
            tags_any=request.tags_any,
            text_search=request.text_search,
            top=request.top,
            skip=request.skip,
        )
    request_payload: dict[str, Any] = {
        "project": trigger.project,
        "query_mode": trigger.query_mode,
        "wiql": trigger.wiql,
        "iteration": trigger.iteration,
        "area_path": trigger.area_path,
        "states": trigger.states,
        "types": trigger.types,
        "tags_any": trigger.tags_any,
        "text_search": trigger.text_search,
        "top": trigger.top,
        "skip": 0,
    }
    if trigger.query_mode == ADO_QUERY_MODE_MANUAL_WIQL:
        request_payload = {
            key: request_payload[key]
            for key in ("project", "query_mode", "wiql", "top", "skip")
            if request_payload.get(key) is not None
        }
    request = AdoBacklogQueryRequest.model_validate(request_payload)
    return issue_service.query_remote_backlog_items(
        provider="azure_devops",
        directory_path=directory_path,
        project=request.project,
        wiql=request.wiql,
        iteration=request.iteration,
        area_path=request.area_path,
        states=request.states,
        types=[item_type.value for item_type in request.types],
        tags_any=request.tags_any,
        text_search=request.text_search,
        top=request.top,
        skip=request.skip,
    )
