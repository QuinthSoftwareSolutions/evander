"""Team human reviews endpoints."""

from __future__ import annotations

from evander.git_refs import private_branch_ref

import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import (
    Path,
)

from starlette.background import (
    BackgroundTask,
)
from starlette.concurrency import (
    run_in_threadpool,
)
from starlette.requests import (
    Request,
)
from starlette.responses import (
    FileResponse,
    Response,
)

from evander.admin.services.managed_git_common import (
    ManagedGitConflictError,
    ManagedGitNotFoundError,
)

from .api_authorization import (
    _can_read_human_review_request,
    _current_human_review_decision_eligible_user_ids,
    _human_review_is_assigned_to,
    _reconcile_human_review_request_assignment,
    _reject_client_human_identity_fields,
    _require_human_review_decision_identity,
    _require_human_review_owner_identity,
    _strict_human_identity_from_request,
    _validate_human_review_reassignment_targets,
)
from .api_constants import (
    JSONResponse,
)
from .api_context import TeamApiDependencies
from .api_release import (
    _append_human_review_bundle_manifest,
    _delete_temporary_file,
    _human_review_bundle_filename,
    _human_review_bundle_manifest,
)
from .api_requests import (
    _expected_revision,
    _mutation_request_id,
    _operator_actor_from_request,
    _optional,
    _optional_mapping_list,
    _optional_object,
    _optional_string_list,
    _publish_team_events,
    _query_text,
    _query_values,
    _read_payload,
    _required,
    _required_int,
)
from .human_review_projection import (
    human_review_request_detail_payload,
)
from .services.domain.task_assessment import (
    task_payload_with_assessment,
)


def build_human_reviews_handlers(
    dependencies: TeamApiDependencies,
) -> dict[str, Callable[[Request], Awaitable[Response]]]:
    context = dependencies.context
    repository = dependencies.repository
    admin_service = dependencies.admin_service

    async def list_human_review_requests(request: Request) -> JSONResponse:
        identity = _strict_human_identity_from_request(context.auth, request)
        assigned_to = _query_text(request, "assigned_to")
        if assigned_to not in {None, "me"}:
            raise ValueError("assigned_to must be me")
        states = set(_query_values(request, "state") or ())
        allowed_states = {
            "preparing",
            "pending",
            "blocked",
            "approved",
            "changes_requested",
            "cancelled",
        }
        unknown_states = states - allowed_states
        if unknown_states:
            raise ValueError(f"unknown human review state: {', '.join(sorted(unknown_states))}")
        state = repository.read()
        rows = []
        for human_request in sorted(
            state.human_review_requests.values(),
            key=lambda item: (item.created_at, item.id),
            reverse=True,
        ):
            if states and human_request.state not in states:
                continue
            if assigned_to == "me":
                if not _human_review_is_assigned_to(
                    human_request,
                    identity["user_id"],
                ):
                    continue
            elif not _can_read_human_review_request(
                context.auth,
                request,
                human_request,
                identity=identity,
            ):
                continue
            rows.append(human_review_request_detail_payload(state, human_request))
        return JSONResponse(
            {
                "revision": state.revision,
                "human_review_requests": rows,
            }
        )

    async def get_human_review_request(request: Request) -> JSONResponse:
        identity = _strict_human_identity_from_request(context.auth, request)
        state = repository.read()
        human_request = state.human_review_requests.get(
            request.path_params["human_review_request_id"]
        )
        if human_request is None:
            return JSONResponse(
                {"error": "human review request not found"},
                status_code=404,
            )
        if not _can_read_human_review_request(
            context.auth,
            request,
            human_request,
            identity=identity,
        ):
            raise PermissionError("Human review request is not assigned to this user")
        return JSONResponse(human_review_request_detail_payload(state, human_request))

    async def download_human_review_bundle(request: Request) -> FileResponse | JSONResponse:
        identity = _strict_human_identity_from_request(context.auth, request)
        state = repository.read()
        human_request = state.human_review_requests.get(
            request.path_params["human_review_request_id"]
        )
        if human_request is None:
            return JSONResponse(
                {"error": "human review request not found"},
                status_code=404,
            )
        if not _can_read_human_review_request(
            context.auth,
            request,
            human_request,
            identity=identity,
        ):
            raise PermissionError("Human review request is not assigned to this user")

        release = state.release_packages.get(human_request.release_package_id)
        if release is None:
            return JSONResponse(
                {"error": "release package not found"},
                status_code=404,
            )
        project = state.projects.get(release.project_id)
        git = getattr(project, "git", None)
        candidate_sha = str(getattr(release, "candidate_commit_sha", "") or "").strip()
        candidate_tree = str(getattr(release, "candidate_tree_hash", "") or "").strip()
        release_branch = str(getattr(release, "release_branch", "") or "").strip()
        if (
            git is None
            or not candidate_sha
            or not candidate_tree
            or not release_branch
            or human_request.candidate_commit_sha != candidate_sha
            or human_request.candidate_tree_hash != candidate_tree
        ):
            raise ValueError(
                "Human review request does not have an exportable pinned release candidate"
            )
        manifest = getattr(release, "manifest", None)
        if manifest is not None and (
            str(getattr(manifest, "candidate_commit_sha", "") or "").strip() != candidate_sha
            or str(getattr(manifest, "candidate_tree_hash", "") or "").strip() != candidate_tree
        ):
            raise ValueError("Release manifest does not match the pinned human-review candidate")
        managed_git_admin = getattr(context.service, "managed_git_admin", None)
        export_archive = getattr(managed_git_admin, "export_pinned_source_archive", None)
        if not callable(export_archive):
            return JSONResponse(
                {"error": "Pinned source bundle export is unavailable"},
                status_code=503,
            )

        release_ref = (
            private_branch_ref(release_branch)
        )
        descriptor, archive_name = tempfile.mkstemp(
            prefix="evander-human-review-",
            suffix=".zip",
        )
        os.close(descriptor)
        archive_path = Path(archive_name)
        try:
            await run_in_threadpool(
                export_archive,
                git.managed_repo_id,
                git.directory_path,
                source_ref=release_ref,
                commit_sha=candidate_sha,
                tree_hash=candidate_tree,
                archive_path=archive_path,
            )
            _append_human_review_bundle_manifest(
                archive_path,
                _human_review_bundle_manifest(
                    human_request=human_request,
                    release=release,
                    project=project,
                    source_ref=release_ref,
                ),
            )
        except ManagedGitNotFoundError as exc:
            _delete_temporary_file(archive_path)
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ManagedGitConflictError as exc:
            _delete_temporary_file(archive_path)
            return JSONResponse({"error": str(exc)}, status_code=409)
        except Exception:
            _delete_temporary_file(archive_path)
            raise

        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename=_human_review_bundle_filename(release.id),
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
            background=BackgroundTask(_delete_temporary_file, archive_path),
        )

    async def decide_human_review(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        _reconcile_human_review_request_assignment(
            context.auth,
            repository,
            request.path_params["human_review_request_id"],
        )
        state = repository.read()
        human_request = state.human_review_requests.get(
            request.path_params["human_review_request_id"]
        )
        if human_request is None:
            return JSONResponse(
                {"error": "human review request not found"},
                status_code=404,
            )
        identity = _require_human_review_decision_identity(
            context.auth,
            request,
            human_request,
            payload=payload,
        )
        state, result = admin_service.record_human_review_decision_with_result(
            human_review_request_id=human_request.id,
            reviewer_user_id=identity["user_id"],
            reviewer_display_name=identity["display_name"],
            decision=_required(payload, "decision"),
            request_id=_mutation_request_id(request, payload),
            expected_request_revision=_expected_revision(payload),
            checklist_results=_optional_object(payload, "checklist_results"),
            confirmed_finding_ids=(_optional_string_list(payload, "confirmed_finding_ids") or []),
            summary=_optional(payload, "summary") or "",
            evidence_refs=_optional_string_list(payload, "evidence_refs") or [],
            findings=_optional_mapping_list(payload, "findings"),
        )
        published_event_ids = _publish_team_events(context, result.get("events"))
        return JSONResponse(
            {
                "revision": state.revision,
                "result": result,
                "published_event_ids": published_event_ids,
                "human_review_request": human_review_request_detail_payload(
                    state,
                    state.human_review_requests[human_request.id],
                ),
            }
        )

    async def reassign_human_review_request(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        _reconcile_human_review_request_assignment(
            context.auth,
            repository,
            request.path_params["human_review_request_id"],
        )
        state = repository.read()
        human_request = state.human_review_requests.get(
            request.path_params["human_review_request_id"]
        )
        if human_request is None:
            return JSONResponse(
                {"error": "human review request not found"},
                status_code=404,
            )
        identity = _require_human_review_owner_identity(
            context.auth,
            request,
            human_request,
            payload=payload,
        )
        user_ids = _validate_human_review_reassignment_targets(
            context.auth,
            human_request,
            user_ids=_optional_string_list(payload, "user_ids") or [],
        )
        state, result = admin_service.reassign_human_review_with_result(
            human_review_request_id=human_request.id,
            user_ids=user_ids,
            assigned_by_user_id=identity["user_id"],
            request_id=_mutation_request_id(request, payload),
            expected_request_revision=_expected_revision(payload),
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "result": result,
                "human_review_request": human_review_request_detail_payload(
                    state,
                    state.human_review_requests[human_request.id],
                ),
            }
        )

    async def resume_human_review_request(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        _reconcile_human_review_request_assignment(
            context.auth,
            repository,
            request.path_params["human_review_request_id"],
        )
        state = repository.read()
        human_request = state.human_review_requests.get(
            request.path_params["human_review_request_id"]
        )
        if human_request is None:
            return JSONResponse(
                {"error": "human review request not found"},
                status_code=404,
            )
        identity = _require_human_review_owner_identity(
            context.auth,
            request,
            human_request,
            payload=payload,
        )
        if not _current_human_review_decision_eligible_user_ids(
            context.auth,
            human_request,
        ):
            raise ValueError(
                "human review assignment is unavailable; audited reassignment is required"
            )
        state, result = admin_service.resume_human_review_with_result(
            human_review_request_id=human_request.id,
            resumed_by_user_id=identity["user_id"],
            request_id=_mutation_request_id(request, payload),
            expected_request_revision=_expected_revision(payload),
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "result": result,
                "human_review_request": human_review_request_detail_payload(
                    state,
                    state.human_review_requests[human_request.id],
                ),
            }
        )

    async def record_human_review_preparation(
        request: Request,
    ) -> JSONResponse:
        payload = await _read_payload(request)
        _reconcile_human_review_request_assignment(
            context.auth,
            repository,
            request.path_params["human_review_request_id"],
        )
        state = repository.read()
        human_request = state.human_review_requests.get(
            request.path_params["human_review_request_id"]
        )
        if human_request is None:
            return JSONResponse(
                {"error": "human review request not found"},
                status_code=404,
            )
        identity = _require_human_review_owner_identity(
            context.auth,
            request,
            human_request,
            payload=payload,
        )
        target = _optional_object(payload, "target")
        error = _optional(payload, "error")
        if (target is None) == (error is None):
            raise ValueError("exactly one of target or error is required")
        if error is None and not _current_human_review_decision_eligible_user_ids(
            context.auth,
            human_request,
        ):
            raise ValueError(
                "human review assignment is unavailable; audited reassignment is required"
            )
        state, result = admin_service.record_human_review_preparation_result_with_result(
            human_review_request_id=human_request.id,
            recorded_by_user_id=identity["user_id"],
            request_id=_mutation_request_id(request, payload),
            expected_request_revision=_expected_revision(payload),
            candidate_commit_sha=_required(payload, "candidate_commit_sha"),
            candidate_tree_hash=_required(payload, "candidate_tree_hash"),
            preparation_generation=_required_int(
                payload,
                "preparation_generation",
            ),
            target=target,
            error=error,
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "result": result,
                "human_review_request": human_review_request_detail_payload(
                    state,
                    state.human_review_requests[human_request.id],
                ),
            }
        )

    async def report_release_issue(request: Request) -> JSONResponse:
        payload = await _read_payload(request)
        _reject_client_human_identity_fields(payload)
        identity = _strict_human_identity_from_request(context.auth, request)
        release_package_id = request.path_params["release_package_id"]
        state, result = admin_service.report_post_release_feedback_with_result(
            release_package_id=release_package_id,
            reporter_user_id=identity["user_id"],
            reporter_display_name=identity["display_name"],
            report=_required(payload, "report"),
            evidence_refs=_optional_string_list(payload, "evidence_refs") or [],
            request_id=_mutation_request_id(request, payload),
            expected_state_revision=_expected_revision(payload),
        )
        published_event_ids = _publish_team_events(context, result.get("events"))
        case_id = str(result["case_id"])
        return JSONResponse(
            {
                "revision": state.revision,
                "result": result,
                "published_event_ids": published_event_ids,
                "case": state.cases[case_id].model_dump(mode="json"),
            }
        )

    async def propose_human_finding_resolution(
        request: Request,
    ) -> JSONResponse:
        payload = await _read_payload(request)
        _reject_client_human_identity_fields(payload)
        finding_id = request.path_params["human_review_finding_id"]
        state = repository.read()
        if finding_id not in state.human_review_findings:
            return JSONResponse(
                {"error": "human review finding not found"},
                status_code=404,
            )
        state, result = admin_service.record_human_finding_resolution_proposal_with_result(
            finding_id=finding_id,
            proposed_resolution=_required(payload, "proposed_resolution"),
            evidence_refs=(_optional_string_list(payload, "evidence_refs") or []),
            proposed_by_deliverable_ids=(
                _optional_string_list(
                    payload,
                    "proposed_by_deliverable_ids",
                )
                or []
            ),
            proposed_by_user_id=_operator_actor_from_request(
                context.auth,
                request,
            ),
            request_id=_mutation_request_id(request, payload),
            expected_state_revision=_expected_revision(payload),
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "result": result,
                "human_review_finding": state.human_review_findings[finding_id].model_dump(
                    mode="json"
                ),
            }
        )

    async def route_human_finding_correction(
        request: Request,
    ) -> JSONResponse:
        payload = await _read_payload(request)
        _reject_client_human_identity_fields(payload)
        finding_id = request.path_params["human_review_finding_id"]
        state = repository.read()
        if finding_id not in state.human_review_findings:
            return JSONResponse(
                {"error": "human review finding not found"},
                status_code=404,
            )
        state, result = admin_service.route_human_finding_correction_with_result(
            finding_id=finding_id,
            owner_agent_id=_required(payload, "owner_agent_id"),
            target_deliverable_ids=_optional_string_list(
                payload,
                "target_deliverable_ids",
            ),
            routed_by_user_id=_operator_actor_from_request(
                context.auth,
                request,
            ),
            request_id=_mutation_request_id(request, payload),
            expected_state_revision=_expected_revision(payload),
        )
        correction_task_id = str(result["correction_task_id"])
        return JSONResponse(
            {
                "revision": state.revision,
                "result": result,
                "human_review_finding": state.human_review_findings[finding_id].model_dump(
                    mode="json"
                ),
                "correction_task": task_payload_with_assessment(
                    state,
                    state.tasks[correction_task_id],
                ),
            },
        )

    async def close_post_release_feedback_case(
        request: Request,
    ) -> JSONResponse:
        payload = await _read_payload(request)
        _reject_client_human_identity_fields(payload)
        case_id = request.path_params["case_id"]
        state = repository.read()
        if case_id not in state.cases:
            return JSONResponse({"error": "case not found"}, status_code=404)
        state, result = admin_service.close_post_release_feedback_case_with_result(
            case_id=case_id,
            reason=_required(payload, "reason"),
            evidence_refs=(_optional_string_list(payload, "evidence_refs") or []),
            closed_by_user_id=_operator_actor_from_request(
                context.auth,
                request,
            ),
            request_id=_mutation_request_id(request, payload),
            expected_state_revision=_expected_revision(payload),
        )
        return JSONResponse(
            {
                "revision": state.revision,
                "result": result,
                "case": state.cases[case_id].model_dump(mode="json"),
            }
        )

    return {
        "list_human_review_requests": list_human_review_requests,
        "get_human_review_request": get_human_review_request,
        "download_human_review_bundle": download_human_review_bundle,
        "decide_human_review": decide_human_review,
        "reassign_human_review_request": reassign_human_review_request,
        "resume_human_review_request": resume_human_review_request,
        "record_human_review_preparation": record_human_review_preparation,
        "report_release_issue": report_release_issue,
        "propose_human_finding_resolution": propose_human_finding_resolution,
        "route_human_finding_correction": route_human_finding_correction,
        "close_post_release_feedback_case": close_post_release_feedback_case,
    }
