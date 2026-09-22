"""Team reviews endpoints."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import (
    datetime,
)
from typing import (
    Any,
)

from starlette.concurrency import (
    run_in_threadpool,
)
from starlette.requests import (
    Request,
)
from starlette.responses import Response

from .api_constants import (
    JSONResponse,
)
from .api_context import TeamApiDependencies
from .api_requests import (
    _expected_revision,
    _idle_context_rotation_outbox_publication_extra,
    _operator_actor_from_request,
    _optional,
    _optional_list,
    _optional_object,
    _optional_present_text,
    _optional_string_list,
    _publish_team_events,
    _read_payload,
    _required,
)
from .models import (
    ReviewProfileCriterion,
    TeamRecordState,
)
from .outbox_publication import (
    publish_team_wake_events_with_outbox_recovery,
)
from .outbox_publication import (
    request_team_outbox_publication as _request_team_outbox_publication,
)
from .repository import (
    TeamStateRepository,
)
from .services.domain.acceptance_verification import (
    ACCEPTANCE_VERIFICATION_RETIRED_WARNING,
)
from .services.domain.wake import (
    AGENT_WAKE_REQUESTED_EVENT_TYPE,
)
from .services.review_dispatch_steering import (
    steer_closed_review_dispatch,
)


def build_reviews_handlers(
    dependencies: TeamApiDependencies,
) -> dict[str, Callable[[Request], Awaitable[Response]]]:
    context = dependencies.context
    repository = dependencies.repository
    admin_service = dependencies.admin_service
    _state_response = dependencies._state_response
    _run_ui_rebasable_mutation = dependencies._run_ui_rebasable_mutation

    async def record_deliverable(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_state = _optional(payload, "state")
        deliverable_id = _optional(payload, "id") or _optional(payload, "deliverable_id")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.record_deliverable(
                deliverable_id=deliverable_id,
                task_id=_required(payload, "task_id"),
                producer_agent_id=_required(payload, "producer_agent_id"),
                summary=_required(payload, "summary"),
                artifact_refs=_optional_string_list(payload, "artifact_refs"),
                outcomes=_optional_list(payload, "outcomes"),
                source_deliverable_ids=_optional_string_list(
                    payload,
                    "source_deliverable_ids",
                ),
                completion=_optional_object(payload, "completion"),
                state=TeamRecordState(raw_state) if raw_state else TeamRecordState.PENDING,
                supersedes_deliverable_id=_optional(
                    payload,
                    "supersedes_deliverable_id",
                ),
                enforce_task_assessment=True,
                expected_revision=expected_revision,
            ),
            create_collection="deliverables",
            create_id=deliverable_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def record_review_request(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        raw_state = _optional(payload, "state")
        raw_decision_at = _optional(payload, "decision_at")
        review_request_id = _optional(payload, "id") or _optional(payload, "review_request_id")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.record_review_request(
                review_request_id=review_request_id,
                deliverable_id=_required(payload, "deliverable_id"),
                reviewer_agent_id=_required(payload, "reviewer_agent_id"),
                state=TeamRecordState(raw_state) if raw_state else TeamRecordState.PENDING,
                findings=_optional_string_list(payload, "findings"),
                review_profile_id=_optional(payload, "review_profile_id"),
                instructions=_optional(payload, "instructions"),
                requested_by_actor=_operator_actor_from_request(
                    context.auth,
                    request,
                ),
                decision_at=datetime.fromisoformat(raw_decision_at) if raw_decision_at else None,
                expected_revision=expected_revision,
            ),
            create_collection="review_requests",
            create_id=review_request_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def decide_review_request(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        criteria = (
            _optional_list(payload, "criteria") if payload.get("criteria") is not None else None
        )
        finding_resolutions = (
            _optional_list(payload, "finding_resolutions")
            if payload.get("finding_resolutions") is not None
            else None
        )
        review_request_id = request.path_params["review_request_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.record_review_decision(
                review_request_id=review_request_id,
                state=TeamRecordState(_required(payload, "state")),
                findings=_optional_string_list(payload, "findings"),
                criteria=criteria,
                finding_resolutions=finding_resolutions,
                expected_revision=expected_revision,
            ),
            target_collection="review_requests",
            target_id=review_request_id,
        )
        review = state.review_requests.get(review_request_id)
        return _state_response(
            request,
            state,
            rebased_from_revision=rebased_from_revision,
            extra=(
                _idle_context_rotation_outbox_publication_extra(
                    context,
                    state=state,
                    agent_id=review.reviewer_agent_id,
                )
                if review is not None
                else None
            ),
        )

    async def retire_review_request(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        review_request_id = request.path_params["review_request_id"]
        operator_actor_id = _operator_actor_from_request(context.auth, request)
        reason = _required(payload, "reason")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.retire_review_request(
                review_request_id=review_request_id,
                reason=reason,
                operator_actor_id=operator_actor_id,
                expected_revision=expected_revision,
            ),
            target_collection="review_requests",
            target_id=review_request_id,
        )
        steering = await run_in_threadpool(
            steer_closed_review_dispatch,
            runtime_dir=context.runtime_dir,
            state=state,
            review_request_id=review_request_id,
            action="retired",
            reason=reason,
            created_by=operator_actor_id,
        )
        return _state_response(
            request,
            state,
            rebased_from_revision=rebased_from_revision,
            extra={"review_dispatch_steering": steering},
        )

    async def withdraw_review_request(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        review_request_id = request.path_params["review_request_id"]
        operator_actor_id = _operator_actor_from_request(context.auth, request)
        reason = _required(payload, "reason")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.withdraw_review_request(
                review_request_id=review_request_id,
                reason=reason,
                operator_actor_id=operator_actor_id,
                expected_revision=expected_revision,
            ),
            target_collection="review_requests",
            target_id=review_request_id,
        )
        steering = await run_in_threadpool(
            steer_closed_review_dispatch,
            runtime_dir=context.runtime_dir,
            state=state,
            review_request_id=review_request_id,
            action="withdrawn",
            reason=reason,
            created_by=operator_actor_id,
        )
        return _state_response(
            request,
            state,
            rebased_from_revision=rebased_from_revision,
            extra={"review_dispatch_steering": steering},
        )

    async def create_operator_directive(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        created_by = _optional(payload, "created_by") or _operator_actor_from_request(
            context.auth,
            request,
        )
        state, item, wake_result = admin_service.create_operator_directive(
            team_id=_required(payload, "team_id"),
            message=_required(payload, "message"),
            recipient_agent_id=_optional(payload, "recipient_agent_id"),
            project_id=_optional(payload, "project_id"),
            task_id=_optional(payload, "task_id"),
            title=_optional(payload, "title"),
            source=_optional(payload, "source"),
            source_message_id=_optional(payload, "source_message_id"),
            created_by=created_by,
            requesting_session_id=_optional(payload, "requesting_session_id"),
            expected_revision=_expected_revision(payload),
        )
        wake_delivery = publish_team_wake_events_with_outbox_recovery(
            context,
            repository=TeamStateRepository(context.runtime_dir),
            events=wake_result.get("events"),
            outbox_record_ids=wake_result.get("outbox_record_ids") or [],
            publish_events=lambda events: _publish_team_events(context, events),
        )
        return _state_response(
            request,
            state,
            status_code=201,
            extra={
                "operator_directive": item.model_dump(mode="json"),
                **wake_delivery,
            },
        )

    async def dismiss_action_record(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        action_record_id = request.path_params["action_record_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.dismiss_action_record_attention(
                action_record_id=action_record_id,
                dismissed_by=_optional(payload, "dismissed_by"),
                dismissed_reason=_optional(payload, "dismissed_reason"),
                expected_revision=expected_revision,
            ),
            target_collection="action_records",
            target_id=action_record_id,
        )
        return _state_response(request, state, rebased_from_revision=rebased_from_revision)

    async def resolve_terminal_blocker(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        blocker_id = request.path_params["blocker_id"]
        state, result = admin_service.resolve_terminal_blocker_as_operator(
            blocker_id=blocker_id,
            note=_required(payload, "note"),
            resolution_refs=_optional_string_list(payload, "resolution_refs"),
            operator_actor_id=_operator_actor_from_request(context.auth, request),
            expected_revision=_expected_revision(payload),
        )
        extra: dict[str, Any] = {"resolved_blocker": result["blocker"]}
        if any(
            event.get("event_type") == AGENT_WAKE_REQUESTED_EVENT_TYPE
            for event in result.get("events") or []
        ):
            extra["outbox_publication_wake"] = _request_team_outbox_publication(
                context,
                correlation_key=(f"terminal-blocker-resolution:{blocker_id}:{state.revision}"),
            )
        return _state_response(
            request,
            state,
            extra=extra,
        )

    async def authorize_deliverable_acceptance_verification_waiver(
        request: Request,
    ) -> JSONResponse:
        payload = await _read_payload(request)
        deliverable_id = request.path_params["deliverable_id"]
        state, deliverable = (
            admin_service.authorize_deliverable_acceptance_verification_waiver_as_operator(
                deliverable_id=deliverable_id,
                reason=_required(payload, "reason"),
                operator_actor_id=_operator_actor_from_request(context.auth, request),
                expected_revision=_expected_revision(payload),
            )
        )
        return _state_response(
            request,
            state,
            extra={
                "warning": ACCEPTANCE_VERIFICATION_RETIRED_WARNING,
                "deliverable_acceptance_verification": (
                    deliverable.verification.model_dump(mode="json", exclude_none=True)
                    if deliverable.verification is not None
                    else None
                ),
            },
        )

    async def recover_candidate_correction_successor(
        request: Request,
    ) -> JSONResponse:
        payload = await _read_payload(request)
        blocker_id = request.path_params["blocker_id"]
        state, result = admin_service.recover_candidate_correction_successor_as_operator(
            blocker_id=blocker_id,
            action=_required(payload, "action"),
            reason=_optional(payload, "reason"),
            instruction=_optional(payload, "instruction"),
            owner_agent_id=_optional(payload, "owner_agent_id"),
            operator_actor_id=_operator_actor_from_request(
                context.auth,
                request,
            ),
            expected_revision=_expected_revision(payload),
        )
        result["outbox_publication_wake"] = _request_team_outbox_publication(
            context,
            correlation_key=(f"candidate-correction-recovery:{blocker_id}:{state.revision}"),
        )
        return _state_response(
            request,
            state,
            extra={"candidate_correction_recovery": result},
        )

    async def start_dependency_composition_recovery(
        request: Request,
    ) -> JSONResponse:
        payload = await _read_payload(request)
        blocker_id = request.path_params["blocker_id"]
        state, result = admin_service.start_dependency_composition_recovery_as_operator(
            blocker_id=blocker_id,
            resolver_agent_id=_required(payload, "resolver_agent_id"),
            operator_actor_id=_operator_actor_from_request(
                context.auth,
                request,
            ),
            expected_revision=_expected_revision(payload),
        )
        return _state_response(
            request,
            state,
            status_code=201,
            extra={"dependency_composition_recovery": result},
        )

    async def create_outcome_kind_definition(
        request: Request,
    ) -> JSONResponse:
        payload = await _read_payload(request)
        definition_id = _optional(payload, "id") or _optional(
            payload,
            "definition_id",
        )
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.create_outcome_kind_definition(
                definition_id=definition_id,
                team_id=_required(payload, "team_id"),
                project_id=_optional(payload, "project_id"),
                kind=_required(payload, "kind"),
                name=_required(payload, "name"),
                description=_optional(payload, "description") or "",
                reference_requirements=(_optional(payload, "reference_requirements") or ""),
                evidence_requirements=(_optional(payload, "evidence_requirements") or ""),
                expected_revision=expected_revision,
            ),
            create_collection="outcome_kind_definitions",
            create_id=definition_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def patch_outcome_kind_definition(
        request: Request,
    ) -> JSONResponse:
        payload = await _read_payload(request)
        raw_archived = payload.get("archived")
        definition_id = request.path_params["definition_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.update_outcome_kind_definition(
                definition_id=definition_id,
                name=_optional(payload, "name"),
                description=_optional_present_text(payload, "description"),
                reference_requirements=_optional_present_text(
                    payload,
                    "reference_requirements",
                ),
                evidence_requirements=_optional_present_text(
                    payload,
                    "evidence_requirements",
                ),
                archived=raw_archived if isinstance(raw_archived, bool) else None,
                expected_revision=expected_revision,
            ),
            target_collection="outcome_kind_definitions",
            target_id=definition_id,
        )
        return _state_response(
            request,
            state,
            rebased_from_revision=rebased_from_revision,
        )

    async def list_outcome_kind_definitions(
        _request: Request,
    ) -> JSONResponse:
        state = repository.read()
        return JSONResponse(
            {
                "revision": state.revision,
                "outcome_kind_definitions": [
                    definition.model_dump(mode="json")
                    for definition in state.outcome_kind_definitions.values()
                ],
            }
        )

    async def get_outcome_kind_definition(
        request: Request,
    ) -> JSONResponse:
        state = repository.read()
        definition = state.outcome_kind_definitions.get(request.path_params["definition_id"])
        if definition is None:
            return JSONResponse(
                {"error": "outcome kind definition not found"},
                status_code=404,
            )
        return JSONResponse(definition.model_dump(mode="json"))

    async def create_review_profile(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        criteria = [
            ReviewProfileCriterion.model_validate(item)
            for item in _optional_list(payload, "criteria")
        ]
        profile_id = _optional(payload, "id") or _optional(payload, "review_profile_id")
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.create_review_profile(
                profile_id=profile_id,
                team_id=_required(payload, "team_id"),
                project_id=_optional(payload, "project_id"),
                name=_required(payload, "name"),
                description=_optional(payload, "description") or "",
                applicable_role_ids=_optional_string_list(payload, "applicable_role_ids"),
                criteria=criteria,
                required_outcome_kinds=(
                    _optional_string_list(payload, "required_outcome_kinds")
                    if "required_outcome_kinds" in payload
                    else None
                ),
                expected_revision=expected_revision,
            ),
            create_collection="review_profiles",
            create_id=profile_id,
        )
        return _state_response(
            request,
            state,
            status_code=201,
            rebased_from_revision=rebased_from_revision,
        )

    async def patch_review_profile(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        has_project = "project_id" in payload
        has_criteria = "criteria" in payload
        criteria = (
            [
                ReviewProfileCriterion.model_validate(item)
                for item in _optional_list(payload, "criteria")
            ]
            if has_criteria
            else None
        )
        raw_archived = payload.get("archived")
        profile_id = request.path_params["review_profile_id"]
        state, rebased_from_revision = _run_ui_rebasable_mutation(
            payload,
            lambda expected_revision: admin_service.update_review_profile(
                profile_id=profile_id,
                name=_optional(payload, "name"),
                description=_optional_present_text(payload, "description"),
                applicable_role_ids=(
                    _optional_string_list(payload, "applicable_role_ids")
                    if "applicable_role_ids" in payload
                    else None
                ),
                criteria=criteria,
                required_outcome_kinds=(
                    _optional_string_list(payload, "required_outcome_kinds")
                    if "required_outcome_kinds" in payload
                    else None
                ),
                archived=raw_archived if isinstance(raw_archived, bool) else None,
                project_id=_optional(payload, "project_id"),
                set_project_id=has_project,
                expected_revision=expected_revision,
            ),
            target_collection="review_profiles",
            target_id=profile_id,
        )
        return _state_response(request, state, rebased_from_revision=rebased_from_revision)

    async def list_review_profiles(_request: Request) -> JSONResponse:
        state = repository.read()
        return JSONResponse(
            {
                "revision": state.revision,
                "review_profiles": [
                    profile.model_dump(mode="json") for profile in state.review_profiles.values()
                ],
            }
        )

    async def get_review_profile(request: Request) -> JSONResponse:
        state = repository.read()
        profile = state.review_profiles.get(request.path_params["review_profile_id"])
        if profile is None:
            return JSONResponse({"error": "review profile not found"}, status_code=404)
        return JSONResponse(profile.model_dump(mode="json"))

    return {
        "record_deliverable": record_deliverable,
        "record_review_request": record_review_request,
        "decide_review_request": decide_review_request,
        "retire_review_request": retire_review_request,
        "withdraw_review_request": withdraw_review_request,
        "create_operator_directive": create_operator_directive,
        "dismiss_action_record": dismiss_action_record,
        "resolve_terminal_blocker": resolve_terminal_blocker,
        "authorize_deliverable_acceptance_verification_waiver": authorize_deliverable_acceptance_verification_waiver,
        "recover_candidate_correction_successor": recover_candidate_correction_successor,
        "start_dependency_composition_recovery": start_dependency_composition_recovery,
        "create_outcome_kind_definition": create_outcome_kind_definition,
        "patch_outcome_kind_definition": patch_outcome_kind_definition,
        "list_outcome_kind_definitions": list_outcome_kind_definitions,
        "get_outcome_kind_definition": get_outcome_kind_definition,
        "create_review_profile": create_review_profile,
        "patch_review_profile": patch_review_profile,
        "list_review_profiles": list_review_profiles,
        "get_review_profile": get_review_profile,
    }
