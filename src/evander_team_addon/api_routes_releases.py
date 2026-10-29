"""Team releases endpoints."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from starlette.concurrency import (
    run_in_threadpool,
)
from starlette.requests import (
    Request,
)
from starlette.responses import Response

from evander.admin.web.rbac import (
    ADMIN_OWNER_PERMISSION,
)

from .api_authorization import (
    _can_read_release_human_review,
)
from .api_constants import (
    JSONResponse,
)
from .api_context import TeamApiDependencies
from .api_release import (
    _release_candidate_checkout_metadata,
    _release_verification_live_log_limit,
    _release_verification_live_payload,
)
from .api_requests import (
    _auth_can,
    _expected_revision,
    _operator_actor_from_request,
    _optional,
    _optional_bool,
    _optional_string_list,
    _publish_team_events,
    _read_payload,
    _request_wants_view_scoped_response,
    _required,
    _runtime_from_context,
)
from .human_review_projection import (
    release_package_api_payload,
)
from .outbox_publication import request_team_outbox_publication as _request_team_outbox_publication
from .services.worktree_cleanup_service import (
    EffectTaskWorktreeCleanupBackend,
    TeamWorktreeCleanupService,
)


def build_releases_handlers(
    dependencies: TeamApiDependencies,
) -> dict[str, Callable[[Request], Awaitable[Response]]]:
    context = dependencies.context
    repository = dependencies.repository
    admin_service = dependencies.admin_service
    _state_payload = dependencies._state_payload
    _state_response = dependencies._state_response
    _directory_owner_authorization_error = dependencies._directory_owner_authorization_error

    async def create_release_package(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        state, result = admin_service.create_release_package_with_result(
            project_id=_required(payload, "project_id"),
            deliverable_ids=_optional_string_list(payload, "deliverable_ids") or [],
            release_id=_optional(payload, "id") or _optional(payload, "release_package_id"),
            supersedes_release_package_id=_optional(
                payload,
                "supersedes_release_package_id",
            ),
            post_release_feedback_case_id=_optional(
                payload,
                "post_release_feedback_case_id",
            ),
            release_branch=_optional(payload, "release_branch"),
            canonical_branch=_optional(payload, "canonical_branch"),
            summary=_optional(payload, "summary"),
            verification_waivers=(
                payload.get("verification_waivers")
                if payload.get("verification_waivers") is not None
                else []
                if "verification_waivers" in payload
                else None
            ),
            operator_actor_id=_operator_actor_from_request(context.auth, request),
            expected_revision=_expected_revision(payload),
        )
        response_payload = _state_payload(request, state)
        published_event_ids = _publish_team_events(context, result.get("events"))
        if published_event_ids:
            response_payload["published_event_ids"] = published_event_ids
        warnings = list(result.get("warnings") or [])
        if warnings:
            response_payload["warnings"] = warnings
        if _request_wants_view_scoped_response(request):
            return _state_response(
                request,
                state,
                status_code=201,
                extra={
                    "published_event_ids": published_event_ids,
                    **({"warnings": warnings} if warnings else {}),
                },
            )
        return JSONResponse(response_payload, status_code=201)

    async def list_release_packages(_request: Request) -> JSONResponse:
        state = repository.read()
        return JSONResponse(
            {
                "revision": state.revision,
                "release_packages": [
                    release_package_api_payload(
                        state,
                        release,
                        include_human_details=False,
                    )
                    for release in state.release_packages.values()
                ],
            }
        )

    async def cancel_release_package(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        release_id = request.path_params["release_package_id"]
        current = repository.read()
        release = current.release_packages.get(release_id)
        if release is None:
            return JSONResponse({"error": "release package not found"}, status_code=404)
        project = current.projects.get(release.project_id)
        authorization_error = _directory_owner_authorization_error(
            request,
            directory_path=project.directory_path if project is not None else "root",
            action="Release cancellation",
        )
        if authorization_error is not None:
            return authorization_error
        expected_revision = _expected_revision(payload)
        if expected_revision is None:
            return JSONResponse({"error": "expected_revision is required"}, status_code=400)
        state, result = admin_service.cancel_release_package(
            release_package_id=release_id,
            operator_actor_id=_operator_actor_from_request(context.auth, request),
            reason=_optional(payload, "reason") or "",
            expected_revision=expected_revision,
        )
        return _state_response(request, state, extra={"release_cancellation": result})

    async def retry_release_gate(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        release_id = request.path_params["release_package_id"]
        state, result = admin_service.retry_release_gate_with_result(
            release_package_id=release_id,
            gate_kind=_required(payload, "gate_kind"),
            gate_id=_required(payload, "gate_id"),
            reason=_required(payload, "reason"),
            operator_actor_id=_operator_actor_from_request(context.auth, request),
            allow_rejected_review_exception=(
                _optional_bool(payload, "allow_rejected_review_exception") or False
            ),
            allow_rejected_verification_retry=(
                _optional_bool(payload, "allow_rejected_verification_retry") or False
            ),
            expected_revision=_expected_revision(payload),
        )
        published_event_ids = _publish_team_events(context, result.get("events"))
        if _request_wants_view_scoped_response(request):
            return _state_response(
                request,
                state,
                extra={
                    "release_package": result["release_package"],
                    "replacement_gate_id": result.get("replacement_gate_id"),
                    "published_event_ids": published_event_ids,
                },
            )
        return JSONResponse(
            {
                **result,
                "published_event_ids": published_event_ids,
            }
        )

    async def authorize_release_gate_obligation_waiver(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        release_id = request.path_params["release_package_id"]
        current = repository.read()
        release = current.release_packages.get(release_id)
        if release is None:
            return JSONResponse({"error": "release package not found"}, status_code=404)
        project = current.projects.get(release.project_id)
        directory_path = project.directory_path if project is not None else "root"
        authorization_error = _directory_owner_authorization_error(
            request,
            directory_path=directory_path,
            action="Release gate obligation waiver",
        )
        if authorization_error is not None:
            return authorization_error
        state, result = admin_service.authorize_release_gate_obligation_waiver_with_result(
            release_package_id=release_id,
            gate_kind=_required(payload, "gate_kind"),
            candidate_commit_sha=_required(payload, "candidate_commit_sha"),
            reason=_required(payload, "reason"),
            operator_actor_id=_operator_actor_from_request(context.auth, request),
            expected_revision=_expected_revision(payload),
        )
        published_event_ids = _publish_team_events(context, result.get("events"))
        return JSONResponse({**result, "published_event_ids": published_event_ids})

    async def retry_worktree_cleanup(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        candidate_id = request.path_params["cleanup_candidate_id"]
        current = repository.read()
        candidate = current.worktree_cleanup_candidates.get(candidate_id)
        if candidate is None:
            return JSONResponse(
                {"error": "worktree cleanup candidate not found"},
                status_code=404,
            )
        project = current.projects.get(candidate.project_id)
        directory_path = project.directory_path if project is not None else "root"
        if not _auth_can(
            context.auth,
            request,
            ADMIN_OWNER_PERMISSION,
            directory_path=directory_path,
        ):
            return JSONResponse(
                {"error": "Worktree cleanup retry requires directory owner permission."},
                status_code=403,
            )
        state, requeued = TeamWorktreeCleanupService(
            repository,
            EffectTaskWorktreeCleanupBackend(context, repository=repository),
        ).requeue_operator_action(
            candidate_id,
            requested_by=_operator_actor_from_request(context.auth, request),
            reason=_required(payload, "reason"),
            expected_revision=_expected_revision(payload),
        )
        if _request_wants_view_scoped_response(request):
            return _state_response(
                request,
                state,
                extra={"worktree_cleanup_candidate": requeued.model_dump(mode="json")},
            )
        return JSONResponse(
            {
                "revision": state.revision,
                "worktree_cleanup_candidate": requeued.model_dump(mode="json"),
            }
        )

    async def case_reintegration_candidates(request: Request) -> JSONResponse:
        from .services.reintegration_service import TeamReintegrationService

        current = repository.read()
        case = current.cases.get(request.path_params["case_id"])
        project = current.projects.get(case.project_id) if case else None
        if project is None:
            return JSONResponse({"error": "project-scoped case not found"}, status_code=404)
        if not _auth_can(
            context.auth, request, ADMIN_OWNER_PERMISSION, directory_path=project.directory_path
        ):
            return JSONResponse({"error": "Directory owner permission required"}, status_code=403)
        candidates = await run_in_threadpool(
            TeamReintegrationService(admin_service).candidates_for_case, case.id
        )
        return JSONResponse({"candidates": candidates})

    async def reintegrate_release(request: Request) -> JSONResponse:
        from .services.reintegration_service import TeamReintegrationService

        payload = await _read_payload(request)
        current = repository.read()
        release_id = request.path_params["release_package_id"]
        release = current.release_packages.get(release_id)
        if release is None:
            return JSONResponse({"error": "release not found"}, status_code=404)
        project = current.projects[release.project_id]
        if not _auth_can(
            context.auth, request, ADMIN_OWNER_PERMISSION, directory_path=project.directory_path
        ):
            return JSONResponse({"error": "Directory owner permission required"}, status_code=403)
        service = TeamReintegrationService(admin_service)
        if payload.get("action", "preview") == "preview":
            preview = await run_in_threadpool(
                service.preview, release_id, owner_agent_id=payload.get("owner_agent_id")
            )
            return JSONResponse({"preview": preview})
        preview = payload.get("preview")
        if (
            payload.get("action") != "submit"
            or not isinstance(preview, dict)
            or preview.get("release_package_id") != release_id
        ):
            raise ValueError("invalid reintegration action or preview")
        updated, result = await run_in_threadpool(
            service.submit,
            preview=payload["preview"],
            actor_id=_operator_actor_from_request(context.auth, request),
            request_id=_required(payload, "request_id"),
        )
        result["outbox_publication_wake"] = _request_team_outbox_publication(
            context, correlation_key=f"reintegration:{result['task_id']}"
        )
        return _state_response(request, updated, extra={"reintegration": result})

    async def get_release_package(request: Request) -> JSONResponse:
        state = repository.read()
        release = state.release_packages.get(request.path_params["release_package_id"])
        if release is None:
            return JSONResponse({"error": "release package not found"}, status_code=404)
        include_human_details = _can_read_release_human_review(
            context.auth,
            request,
            state,
            release.id,
        )
        payload = release_package_api_payload(
            state,
            release,
            include_human_details=include_human_details,
        )
        if include_human_details:
            candidate_checkout = _release_candidate_checkout_metadata(
                getattr(context.service, "managed_git_admin", None),
                state,
                release,
                runtime_dir=context.runtime_dir,
            )
            if candidate_checkout is not None:
                payload["candidate_checkout"] = candidate_checkout
        return JSONResponse(payload)

    async def get_release_verification_live(request: Request) -> JSONResponse:
        state = repository.read()
        release = state.release_packages.get(request.path_params["release_package_id"])
        if release is None:
            return JSONResponse({"error": "release package not found"}, status_code=404)
        project = state.projects.get(release.project_id)
        directory_path = project.directory_path if project is not None else "root"
        if not _auth_can(
            context.auth,
            request,
            ADMIN_OWNER_PERMISSION,
            directory_path=directory_path,
        ):
            return JSONResponse(
                {"error": "Live verification output is restricted to directory owners."},
                status_code=403,
            )
        from .services.verification_logs import read_verification_log

        requested_job = request.query_params.get("job_id")
        if requested_job:
            matching = any(
                row.get("job_id") == requested_job
                for run_id in release.verification_run_ids
                if (run := state.release_verification_runs.get(run_id)) is not None
                for row in [
                    *(c.model_dump() for c in run.command_results),
                    *(
                        c
                        for generation in run.generation_history
                        for c in generation.get("command_results", [])
                    ),
                ]
            )
            if not matching:
                return JSONResponse({"error": "verification job not found"}, status_code=404)
            retained = await run_in_threadpool(
                read_verification_log, Path(context.runtime_dir), requested_job
            )
            if retained is None:
                return JSONResponse(
                    {"error": "retained log unavailable or expired"}, status_code=404
                )
            return JSONResponse({"log": {"available": True, "retained": True, "text": retained}})
        runtime = _runtime_from_context(context)
        payload = await run_in_threadpool(
            _release_verification_live_payload,
            state,
            release.id,
            directory_path,
            runtime.provider_config,
            _release_verification_live_log_limit(request),
        )
        return JSONResponse(payload)

    async def list_git_promotions(_request: Request) -> JSONResponse:
        state = repository.read()
        return JSONResponse(
            {
                "revision": state.revision,
                "git_promotions": [
                    promotion.model_dump(mode="json") for promotion in state.git_promotions.values()
                ],
            }
        )

    async def get_git_promotion(request: Request) -> JSONResponse:
        state = repository.read()
        promotion = state.git_promotions.get(request.path_params["promotion_id"])
        if promotion is None:
            return JSONResponse({"error": "git promotion not found"}, status_code=404)
        return JSONResponse(promotion.model_dump(mode="json"))

    return {
        "create_release_package": create_release_package,
        "list_release_packages": list_release_packages,
        "cancel_release_package": cancel_release_package,
        "retry_release_gate": retry_release_gate,
        "authorize_release_gate_obligation_waiver": authorize_release_gate_obligation_waiver,
        "retry_worktree_cleanup": retry_worktree_cleanup,
        "case_reintegration_candidates": case_reintegration_candidates,
        "reintegrate_release": reintegrate_release,
        "get_release_package": get_release_package,
        "get_release_verification_live": get_release_verification_live,
        "list_git_promotions": list_git_promotions,
        "get_git_promotion": get_git_promotion,
    }
