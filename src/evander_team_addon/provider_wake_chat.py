"""Provider wake chat."""

from __future__ import annotations

import os
from collections.abc import (
    Mapping,
    Sequence,
)
from typing import (
    Any,
)

from evander.addons import (
    addon_storage_path,
)
from evander.effects.providers.agent_copy_files import (
    AgentCopyFilesEffectProvider,
)
from evander.effects.providers.base import (
    EffectProviderBase,
)
from evander.events.models import (
    WorkflowNodeEnvelope,
)
from evander.events.provider_models import (
    AgentCopyFilesEffectDefinition,
)
from evander.runtime_generation import (
    require_current_runtime_generation,
)

from .chat_attachments import (
    TeamChatAttachmentPolicyService,
    TeamChatAttachmentStorage,
)
from .costs import (
    TeamCostService,
)
from .mcp import (
    GOVERNANCE_TEAM_MCP_TOOL_NAMES,
    TEAM_ACKNOWLEDGE_OPERATOR_CHAT_MESSAGES_TOOL,
    TEAM_LIST_CONVERSATION_MESSAGES_TOOL,
    TEAM_REPLY_TO_CHAT_TOOL,
    TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
    TEAM_STAGE_CHAT_ATTACHMENT_TOOL,
)
from .models import (
    TeamBlockerState,
    TeamDispatchStatus,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    agent_project_ids,
    external_source_evidence_snapshot_payload,
    team_session_health_payload,
)
from .provider_constants import (
    _TEAM_GOVERNANCE_WAKE_DEFINITION_PAYLOAD_FIELDS,
    TEAM_WAKE_OUTPUT_MAX_LEASE_SUMMARIES,
)
from .provider_events import (
    _materialize,
)
from .provider_guidance import (
    _seed_team_agent_guidance,
    _seed_team_governance_guidance,
)
from .provider_projection import (
    _string_items,
)
from .provider_prompt_queue import (
    _coerce_mapping,
    _enqueue_team_agent_prompt,
    _result_reports_failure,
    _resume_failure_message,
)
from .provider_prompts import (
    _build_governance_prompt,
    _build_team_chat_prompt,
)
from .provider_runtime import (
    _ordinary_team_tools_for_agent,
    _prompt_session_health_agent_ids,
    _register_team_dispatch_model_usage_context,
    _session_repository_from_context,
    _set_session_team_mcp_tools,
    _team_chat_mutation_allowed,
    _team_chat_mutation_scope_lost,
)
from .provider_schemas import (
    TeamWakeGovernanceManagersEffectDefinition,
)
from .provider_values import (
    _definition_payload,
    _optional_int,
    _optional_text,
    _required_text,
    _runtime_dir,
)
from .provider_wake_payloads import (
    _team_wake_invocation_key,
    _team_wake_lease_summary,
)
from .repository import (
    TeamStateRepository,
)
from .services.admin_service import (
    TeamAdminService,
)
from .services.chat_service import (
    TeamChatService,
)
from .services.domain.agent_access import (
    _agent_payload,
    _subordinate_agent_payloads,
    agent_role_description_fingerprint,
)
from .services.domain.chat import (
    _operator_chat_participant_payloads,
)
from .services.domain.outcome_catalog import (
    effective_outcome_kind_payloads,
)
from .services.domain.project_status import (
    project_blocking_sources,
)
from .services.domain.task_assessment import (
    task_payload_with_assessment,
)
from .services.domain.wake import (
    _case_payloads_for_agent,
    _dispatch_case_payloads,
    _release_package_payloads_for_agent,
    _runtime_attention_payloads_for_agent,
)
from .services.read_service import (
    TeamReadService,
)
from .services.runtime_service import (
    TeamRuntimeService,
)
from .services.wake_prompt_context import (
    build_compact_wake_context,
    wake_uses_fresh_context,
    work_lineage_resume_thread_id,
)
from .services.wake_singleflight import (
    TEAM_WAKE_SINGLE_FLIGHT,
)
from .sqlite_store import (
    ADDON_ID,
    STATE_DB_FILE,
)


def _dispatch_queued_team_chat_messages(
    context: Any,
    *,
    repo: TeamStateRepository,
    state: Any,
    claims: list[dict[str, Any]],
    resumer: Any,
    session_records: Mapping[str, Any],
    configured_resume_mode: str,
) -> tuple[Any, list[dict[str, Any]], list[str]]:
    dispatches: list[dict[str, Any]] = []
    errors: list[str] = []
    for claim in claims:
        agent_id = _required_text(claim.get("agent_id"), "agent_id")
        message_id = _required_text(claim.get("message_id"), "message_id")
        message_ids = _string_items(claim.get("message_ids")) or [message_id]
        conversation_id = _required_text(claim.get("conversation_id"), "conversation_id")
        dispatch_id = _required_text(claim.get("dispatch_id"), "dispatch_id")
        agent = state.agents[agent_id]
        messages = [state.conversation_messages[item] for item in message_ids]
        message = messages[0]
        conversation = state.conversations[conversation_id]
        try:
            agent_payload = agent.model_dump(mode="json")
            dispatch = state.dispatches[dispatch_id]
            if _team_chat_mutation_scope_lost(message, dispatch):
                error = (
                    "Team Chat authorization changed before dispatch; no authorized projects remain"
                )
                tool_grant_result = _set_session_team_mcp_tools(
                    context,
                    session_id=agent.session_id,
                    tool_names=[],
                    dispatch_id=dispatch_id,
                )
                state, _message = TeamChatService(repo).mark_chat_message_dispatch_failed(
                    message_id=message_id,
                    error=error,
                )
                dispatches.append(
                    {
                        "agent_id": agent_id,
                        "session_id": agent.session_id,
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "message_ids": message_ids,
                        "dispatch_id": dispatch_id,
                        "authorization_denied": True,
                        "error": error,
                        "team_chat_mcp_tools": tool_grant_result,
                    }
                )
                continue
            attachment_paths = _copy_team_chat_attachments_for_dispatch(
                context,
                repo=repo,
                state=state,
                agent=agent,
                messages=messages,
            )
            mutation_allowed = _team_chat_mutation_allowed(message, dispatch)
            chat_tool_names = [TEAM_REPLY_TO_CHAT_TOOL]
            if mutation_allowed:
                chat_tool_names.append(TEAM_STAGE_CHAT_ATTACHMENT_TOOL)
                chat_tool_names.extend(_ordinary_team_tools_for_agent(agent_payload))
            else:
                chat_tool_names.extend(
                    [
                        TEAM_LIST_CONVERSATION_MESSAGES_TOOL,
                        TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
                        TEAM_ACKNOWLEDGE_OPERATOR_CHAT_MESSAGES_TOOL,
                    ]
                )
            tool_grant_result = _set_session_team_mcp_tools(
                context,
                session_id=agent.session_id,
                tool_names=chat_tool_names,
                dispatch_id=dispatch_id,
            )
            guidance_result = _seed_team_agent_guidance(context, agent=agent_payload)
            chat_lease = _team_chat_wake_lease(
                state,
                agent=agent,
                dispatch_id=dispatch_id,
                session_records=session_records,
                mutation_allowed=mutation_allowed,
            )
            project_repos: list[dict[str, Any]] = []
            effective_resume_mode = (
                "new_context"
                if wake_uses_fresh_context(
                    chat_lease,
                    effective_resume_mode=configured_resume_mode,
                )
                else configured_resume_mode
            )
            fresh_context = effective_resume_mode == "new_context"
            ordinary_team_context = (
                build_compact_wake_context(
                    chat_lease,
                    fresh_context=fresh_context,
                )
                if mutation_allowed
                else None
            )
            prompt = _build_team_chat_prompt(
                state=state,
                conversation=conversation,
                messages=messages,
                mutation_allowed=mutation_allowed,
                attachment_paths=attachment_paths,
                ordinary_team_context=ordinary_team_context,
                fresh_context=fresh_context,
                rotation_handoff_summary=(
                    _optional_text(chat_lease.get("runtime", {}).get("last_handoff_summary"))
                    if fresh_context and isinstance(chat_lease.get("runtime"), Mapping)
                    else None
                ),
            )
            result = _enqueue_team_agent_prompt(
                resumer,
                target=agent.session_id,
                prompt=prompt,
                resume_mode=effective_resume_mode,
                resume_thread_id=work_lineage_resume_thread_id(chat_lease),
                dispatch_id=dispatch_id,
            )
            result_payload = _coerce_mapping(result)
            if _result_reports_failure(result_payload):
                error = _resume_failure_message(result_payload)
                state, _message = TeamChatService(repo).mark_chat_message_dispatch_failed(
                    message_id=message_id,
                    error=error,
                )
                errors.append(f"{agent_id}: {error}")
                continue
            if bool(result_payload.get("queued")):
                state = TeamRuntimeService(repo).mark_agent_dispatch_queued(
                    agent_id=agent_id,
                    dispatch_id=dispatch_id,
                )
                dispatches.append(
                    {
                        "agent_id": agent_id,
                        "session_id": agent.session_id,
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "message_ids": message_ids,
                        "dispatch_id": dispatch_id,
                        "queued": True,
                        "result": result_payload,
                    }
                )
                continue
            state, activation = _activate_delivered_team_prompt(
                repo=repo,
                dispatch_id=dispatch_id,
                result_payload=result_payload,
            )
            dispatch_payload = {
                "agent_id": agent_id,
                "session_id": agent.session_id,
                "conversation_id": conversation_id,
                "message_id": message_id,
                "message_ids": message_ids,
                "dispatch_id": dispatch_id,
                "agent_guidance": guidance_result,
                "team_chat_mcp_tools": tool_grant_result,
                "result": result_payload,
                "resume_mode": effective_resume_mode,
            }
            if ordinary_team_context is not None:
                dispatch_payload["team_context_mode"] = ordinary_team_context["context_mode"]
            if project_repos:
                dispatch_payload["project_repos"] = project_repos
            if activation["context_rotation"] is not None:
                dispatch_payload["context_rotation"] = activation["context_rotation"]
            dispatches.append(dispatch_payload)
        except Exception as exc:  # pragma: no cover - covered through provider tests with fakes
            error = str(exc)
            try:
                state, _message = TeamChatService(repo).mark_chat_message_dispatch_failed(
                    message_id=message_id,
                    error=error,
                )
            except Exception as release_exc:  # pragma: no cover - defensive diagnostics
                error = f"{error}; failed to release team chat dispatch: {release_exc}"
            errors.append(f"{agent_id}: {error}")
    return state, dispatches, errors


def _team_chat_wake_lease(
    state: Any,
    *,
    agent: Any,
    dispatch_id: str,
    session_records: Mapping[str, Any],
    mutation_allowed: bool = True,
) -> dict[str, Any]:
    dispatch = state.dispatches[dispatch_id]
    agent_payload = _agent_payload(state, agent)
    role_description_fingerprint = agent_role_description_fingerprint(agent_payload)
    runtime = state.agent_runtimes[agent.id]
    projects = [
        {
            **state.projects[project_id].model_dump(mode="json"),
            "blocking_sources": project_blocking_sources(state, project_id),
        }
        for project_id in dispatch.project_ids
        if project_id in state.projects
    ]
    has_prior_successful_wake = any(
        prior.id != dispatch_id
        and prior.agent_id == agent.id
        and prior.session_id == agent.session_id
        and prior.status == TeamDispatchStatus.COMPLETED
        and (
            not mutation_allowed
            or prior.cause_kind != "team_chat"
            or bool(
                (prior_message := state.conversation_messages.get(prior.cause_id or "")) is not None
                and prior_message.metadata.get("ordinary_context_seeded") is True
            )
        )
        for prior in state.dispatches.values()
    )
    return {
        "agent": agent_payload,
        "supervisory_overview": (
            TeamReadService(state=state, actor=agent).supervisory_overview()
            if mutation_allowed
            else {}
        ),
        "runtime": runtime.model_dump(mode="json"),
        "role_description_changed": (
            runtime.last_delivered_role_description_fingerprint != role_description_fingerprint
        ),
        "role_description_fingerprint": role_description_fingerprint,
        "dispatch_id": dispatch_id,
        "has_prior_successful_wake": has_prior_successful_wake,
        "reason": "team_chat",
        "dispatch": dispatch.model_dump(mode="json"),
        "tasks": [
            task_payload_with_assessment(state, state.tasks[task_id])
            for task_id in dispatch.task_ids
            if task_id in state.tasks
        ],
        "projects": projects,
        "outcome_kinds": [
            {
                **definition,
                "effective_project_id": project.id,
            }
            for project in (
                state.projects[project_id]
                for project_id in dispatch.project_ids
                if project_id in state.projects
            )
            for definition in effective_outcome_kind_payloads(
                state,
                team_id=project.team_id,
                project_id=project.id,
            )
        ],
        **_dispatch_case_payloads(
            state,
            agent=agent,
            dispatch=dispatch,
        ),
        "external_source_evidence_snapshots": [
            external_source_evidence_snapshot_payload(
                state.external_source_evidence_snapshots[snapshot_id]
            )
            for snapshot_id in dispatch.source_evidence_snapshot_ids
            if snapshot_id in state.external_source_evidence_snapshots
        ],
        "inbox": [
            state.inbox[item_id].model_dump(mode="json")
            for item_id in dispatch.inbox_item_ids
            if item_id in state.inbox
        ],
        "blockers": [
            state.blockers[blocker_id].model_dump(mode="json")
            for blocker_id in dispatch.blocker_ids
            if blocker_id in state.blockers
        ],
        "reviews": [
            state.review_requests[review_id].model_dump(mode="json")
            for review_id in dispatch.review_request_ids
            if review_id in state.review_requests
        ],
        "release_packages": _release_package_payloads_for_agent(state, agent),
        "runtime_attention": _runtime_attention_payloads_for_agent(state, agent),
        "session_health": team_session_health_payload(
            state,
            session_records=session_records,
            agent_ids=_prompt_session_health_agent_ids(state, agent_payload),
        ),
        "operator_chat_participants": _operator_chat_participant_payloads(
            state,
            agent_id=agent.id,
        ),
        "superior_agent_id": agent.reports_to_agent_id,
        "subordinate_agent_ids": list(agent.subordinate_agent_ids),
        "subordinates": _subordinate_agent_payloads(state, agent),
    }


def _activate_delivered_team_prompt(
    *,
    repo: TeamStateRepository,
    dispatch_id: str,
    result_payload: Mapping[str, Any],
) -> tuple[TeamState, dict[str, Any]]:
    """Apply the same durable transitions for immediate and replayed prompts."""

    return TeamRuntimeService(repo).activate_delivered_agent_dispatch(
        dispatch_id=dispatch_id,
        thread_id=_optional_text(result_payload.get("thread_id")),
    )


def build_voice_reconciliation_context(
    state: TeamState,
    *,
    agent_id: str,
    session_records: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return current compact Team context for a browser-voice reconciliation wake.

    Browser voice calls do not create a durable Team dispatch.  Their terminal
    wake must still give the resumed agent a bounded, current working set rather
    than relying only on the prior conversation thread or the much larger,
    call-start Realtime projection.
    """

    agent = state.agents.get(agent_id)
    if agent is None:
        return {}
    project_ids = agent_project_ids(state, agent.id)
    terminal_task_states = {
        TeamTaskState.FINISHED,
        TeamTaskState.ACCEPTED,
        TeamTaskState.REJECTED,
        TeamTaskState.CANCELLED,
    }
    tasks = [
        task
        for task in state.tasks.values()
        if task.owner_agent_id == agent.id and task.state not in terminal_task_states
    ]
    task_ids = {task.id for task in tasks}
    projects = [
        {
            **state.projects[project_id].model_dump(mode="json"),
            "blocking_sources": project_blocking_sources(state, project_id),
        }
        for project_id in project_ids
        if project_id in state.projects
    ]
    runtime = state.agent_runtimes.get(agent.id)
    lease = {
        "agent": _agent_payload(state, agent),
        "runtime": runtime.model_dump(mode="json") if runtime is not None else {},
        "reason": "voice_session_reconciliation",
        "tasks": [task_payload_with_assessment(state, task) for task in tasks],
        "projects": projects,
        "outcome_kinds": [
            {
                **definition,
                "effective_project_id": project.id,
            }
            for project in (
                state.projects[project_id]
                for project_id in project_ids
                if project_id in state.projects
            )
            for definition in effective_outcome_kind_payloads(
                state,
                team_id=project.team_id,
                project_id=project.id,
            )
        ],
        "cases": _case_payloads_for_agent(state, agent.id),
        "inbox": [
            item.model_dump(mode="json")
            for item in state.inbox.values()
            if item.recipient_agent_id == agent.id
        ],
        "blockers": [
            blocker.model_dump(mode="json")
            for blocker in state.blockers.values()
            if blocker.state == TeamBlockerState.OPEN
            and (
                blocker.owner_agent_id == agent.id
                or blocker.task_id in task_ids
                or blocker.project_id in project_ids
            )
        ],
        "reviews": [
            review.model_dump(mode="json")
            for review in state.review_requests.values()
            if review.reviewer_agent_id == agent.id
            and review.state not in {TeamRecordState.ACCEPTED, TeamRecordState.REJECTED}
        ],
        "release_packages": _release_package_payloads_for_agent(state, agent),
        "runtime_attention": _runtime_attention_payloads_for_agent(state, agent),
        "session_health": team_session_health_payload(
            state,
            session_records=session_records,
            agent_ids=_prompt_session_health_agent_ids(state, _agent_payload(state, agent)),
        ),
        "operator_chat_participants": _operator_chat_participant_payloads(
            state,
            agent_id=agent.id,
        ),
        "superior_agent_id": agent.reports_to_agent_id,
        "subordinate_agent_ids": list(agent.subordinate_agent_ids),
        "subordinates": _subordinate_agent_payloads(state, agent),
    }
    return build_compact_wake_context(lease, fresh_context=True)


def _copy_team_chat_attachments_for_dispatch(
    context: Any,
    *,
    repo: TeamStateRepository,
    state: Any,
    agent: Any,
    messages: Sequence[Any],
) -> list[dict[str, Any]]:
    if not any(getattr(message, "attachment_ids", None) for message in messages):
        return []
    session_repository = _session_repository_from_context(context)
    get_session = getattr(session_repository, "get_session", None)
    session = get_session(agent.session_id) if callable(get_session) else None
    directory_path = str(getattr(session, "directory_path", None) or "root")
    policy = TeamChatAttachmentPolicyService(repo).effective_policy(
        directory_path,
        state=state,
    )
    if not policy.operator_to_agent_enabled:
        raise PermissionError(
            f"Team Chat operator-to-agent attachments are disabled for {directory_path}"
        )
    storage = TeamChatAttachmentStorage(repo.runtime_dir)
    allowed_source_root = storage.assets_dir.resolve(strict=False)
    files: list[dict[str, Any]] = []
    payloads: list[dict[str, Any]] = []
    for message in messages:
        attachment_ids = list(getattr(message, "attachment_ids", None) or [])
        for index, attachment_id in enumerate(attachment_ids, start=1):
            attachment = state.chat_attachments.get(attachment_id)
            if attachment is None or attachment.message_id != message.id:
                raise ValueError(f"Team Chat attachment {attachment_id} is not available")
            source_path = storage.asset_path(attachment.id)
            if not source_path.is_file():
                raise FileNotFoundError(f"Team Chat attachment file is missing: {attachment.id}")
            source_path = source_path.resolve(strict=True)
            destination_name = f"{index:02d}-{attachment.filename}"
            destination_path = f"/home/agent/team-chat-attachments/{message.id}/{destination_name}"
            files.append(
                {
                    "source_path": str(source_path),
                    "destination_path": destination_path,
                    "mode": "0644",
                    "overwrite": True,
                }
            )
            payloads.append(
                {
                    "id": attachment.id,
                    "message_id": message.id,
                    "filename": attachment.filename,
                    "mime_type": attachment.mime_type,
                    "byte_count": attachment.byte_count,
                    "sha256": attachment.sha256,
                    "path": destination_path,
                }
            )
    provider = AgentCopyFilesEffectProvider()
    provider.initialize(
        AgentCopyFilesEffectDefinition(
            id=f"team-chat-attachments-{messages[0].id}",
            display_name="Copy Team Chat attachments",
            type="effect:agent:copy-files",
            session_id=agent.session_id,
            vm_user="agent",
            allowed_source_roots=[str(allowed_source_root)],
            files=files,
        ),
        context,
    )
    result = provider.execute()
    if not bool(result.get("success")):
        detail = str(result.get("message") or result.get("error") or "").strip()
        raise RuntimeError(detail or "failed to copy Team Chat attachments")
    return payloads


class TeamWakeGovernanceManagersEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamWakeGovernanceManagersEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        runtime_dir = _runtime_dir(self.runtime_context)
        if os.environ.get("INVOCATION_ID"):
            require_current_runtime_generation(runtime_dir)
        state_db_path = addon_storage_path(runtime_dir, ADDON_ID, STATE_DB_FILE)
        repo = TeamStateRepository(runtime_dir)
        effective_resolved_config = dict(resolved_config) if resolved_config is not None else None
        invocation_key = _team_wake_invocation_key(
            self.effect_definition,
            resolved_config=effective_resolved_config,
            state_revision=repo.read_revision(),
            field_names=_TEAM_GOVERNANCE_WAKE_DEFINITION_PAYLOAD_FIELDS,
            namespace="governance",
        )
        return TEAM_WAKE_SINGLE_FLIGHT.run(
            state_db_path=state_db_path,
            invocation_key=invocation_key,
            execute=lambda: self._execute_once(
                resolved_config=effective_resolved_config,
                repository=repo,
            ),
        )

    def _execute_once(
        self,
        *,
        resolved_config: Mapping[str, Any] | None = None,
        repository: TeamStateRepository | None = None,
    ) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            *_TEAM_GOVERNANCE_WAKE_DEFINITION_PAYLOAD_FIELDS,
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        context = self.runtime_context
        resumer = getattr(context, "agent_resumer", None)
        if resumer is None or not callable(getattr(resumer, "resume_agent", None)):
            raise RuntimeError("effect:team:wake-governance-managers requires agent_resumer")

        repo = repository or TeamStateRepository(_runtime_dir(context))
        current_state = repo.read()
        current_state = TeamAdminService(repo).advance_team_cost_budget_windows(
            current_state=current_state,
        )
        governance_group_id = _optional_text(payload.get("governance_group_id"))
        governance_team_ids = {
            team_id
            for group_id, group in current_state.governance_groups.items()
            if governance_group_id is None or group_id == governance_group_id
            for team_id in group.member_team_ids
        }
        cost_payload = TeamCostService(
            runtime_dir=_runtime_dir(context),
            state=current_state,
            session_repository=_session_repository_from_context(context),
        ).governance_costs_payload(team_ids=governance_team_ids)
        _state, leases = TeamRuntimeService(repo).lease_due_governance_managers(
            governance_group_id=governance_group_id,
            limit=_optional_int(payload.get("limit"), default=10),
            lease_seconds=_optional_int(payload.get("lease_seconds"), default=900),
            wake_interval_seconds=_optional_int(
                payload.get("wake_interval_seconds"),
                default=3600,
            ),
            cost_payload=cost_payload,
            defer_activation=True,
        )
        resume_mode = _optional_text(payload.get("resume_mode")) or "same_context"
        dispatches: list[dict[str, Any]] = []
        errors: list[str] = []
        for lease in leases:
            agent = lease["agent"]
            group = lease["governance_group"]
            queued_for_delivery = False
            try:
                guidance_result = _seed_team_governance_guidance(context, agent=agent)
                tool_grant_result = _set_session_team_mcp_tools(
                    context,
                    session_id=agent["session_id"],
                    tool_names=GOVERNANCE_TEAM_MCP_TOOL_NAMES,
                    dispatch_id=lease["dispatch_id"],
                )
                _register_team_dispatch_model_usage_context(
                    context,
                    agent=agent,
                    dispatch_id=lease["dispatch_id"],
                    effect_id=_optional_text(getattr(self.effect_definition, "id", None)),
                )
                result = _enqueue_team_agent_prompt(
                    resumer,
                    target=agent["session_id"],
                    prompt=_build_governance_prompt(lease),
                    resume_mode=resume_mode,
                    dispatch_id=lease["dispatch_id"],
                )
                result_payload = _coerce_mapping(result)
                if _result_reports_failure(result_payload):
                    errors.append(
                        f"{group['id']}/{agent['id']}: {_resume_failure_message(result_payload)}"
                    )
                elif bool(result_payload.get("queued")):
                    queued_for_delivery = True
                    TeamRuntimeService(repo).mark_governance_dispatch_queued(
                        governance_group_id=group["id"],
                        dispatch_id=lease["dispatch_id"],
                    )
                else:
                    TeamRuntimeService(repo).activate_governance_dispatch(
                        governance_group_id=group["id"],
                        dispatch_id=lease["dispatch_id"],
                        lease_seconds=_optional_int(payload.get("lease_seconds"), default=900)
                        or 900,
                    )
                dispatches.append(
                    {
                        "governance_group_id": group["id"],
                        "agent_id": agent["id"],
                        "session_id": agent["session_id"],
                        "dispatch_id": lease["dispatch_id"],
                        "reason": lease["reason"],
                        "governance_agent_guidance": guidance_result,
                        "governance_mcp_tools": tool_grant_result,
                        "result": result_payload,
                        "queued": queued_for_delivery,
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive provider diagnostics
                errors.append(f"{group['id']}/{agent['id']}: {exc}")
            finally:
                if not queued_for_delivery:
                    TeamRuntimeService(repo).complete_governance_dispatch(
                        governance_group_id=group["id"],
                        dispatch_id=lease["dispatch_id"],
                    )
        final_state = repo.read()
        lease_summaries = [
            _team_wake_lease_summary(lease, state=final_state)
            for lease in leases[:TEAM_WAKE_OUTPUT_MAX_LEASE_SUMMARIES]
        ]
        return {
            "success": not errors,
            "coalesced": False,
            "revision": final_state.revision,
            "leased_count": len(leases),
            "lease_summary_version": 1,
            "lease_summaries": lease_summaries,
            "leases": lease_summaries,
            "lease_summaries_truncated": len(lease_summaries) < len(leases),
            "dispatches": dispatches,
            "errors": errors,
        }
