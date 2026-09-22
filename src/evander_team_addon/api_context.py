"""Shared Team API services, mutation responses, and route protection.

All endpoint groups receive one explicitly constructed dependency record. The
record owns request revision rebasing and the common error/privileged-operation
wrappers, so splitting route groups cannot silently change those policies."""

from __future__ import annotations

from collections.abc import (
    Callable,
    Mapping,
    Sequence,
)
from dataclasses import dataclass
from typing import (
    Any,
)

from starlette.requests import (
    Request,
)
from starlette.routing import (
    Route,
)

from evander.addons import (
    AddonContext,
)
from evander.admin.web.rbac import (
    ADMIN_OWNER_PERMISSION,
)
from evander.orchestration.internal_events import (
    build_internal_event_bus_for_runtime,
)

from .api_constants import (
    JSONResponse,
)
from .api_projection import (
    _add_release_outbox_metadata,
    _team_state_payload_for_request,
)
from .api_requests import (
    _agent_execution_records_by_session,
    _auth_can,
    _expected_revision,
    _mutation_view_response_payload,
    _request_wants_view_scoped_response,
    _route_requires_privileged_operations,
    _session_repository_from_context,
    _with_errors,
    _with_privileged_operations,
)
from .api_revision import (
    _can_rebase_ui_mutation,
)
from .avatars import (
    AvatarStorage,
)
from .chat_attachments import (
    TeamChatAttachmentPolicyService,
    TeamChatAttachmentStorage,
)
from .models import (
    TeamState,
)
from .outbox_publication import (
    request_team_outbox_publication_for_records,
)
from .provider_runtime import (
    _session_records_by_id,
)
from .repository import (
    TeamStateConflictError,
    TeamStateRepository,
)
from .services.admin_service import (
    TeamAdminService,
)
from .services.chat_service import (
    TeamChatService,
)
from .services.human_review_assignment_service import (
    human_review_eligibility_resolver,
)
from .services.runtime_service import (
    TeamRuntimeService,
)


@dataclass(frozen=True)
class TeamApiDependencies:
    """Services and shared response/authorization behavior used by route groups."""

    context: AddonContext
    repository: TeamStateRepository
    admin_service: TeamAdminService
    chat_service: TeamChatService
    runtime_service: TeamRuntimeService
    avatar_storage: AvatarStorage
    chat_attachment_storage: TeamChatAttachmentStorage
    chat_attachment_policy_service: TeamChatAttachmentPolicyService
    _state_payload: Callable[..., Any]
    _state_response: Callable[..., Any]
    _run_ui_rebasable_mutation: Callable[..., Any]
    _task_project_owner_authorization_error: Callable[..., Any]
    _directory_owner_authorization_error: Callable[..., Any]
    route: Callable[..., Any]


def build_api_dependencies(context: AddonContext) -> TeamApiDependencies:
    repository = TeamStateRepository(context.runtime_dir)
    admin_service = TeamAdminService(
        repository,
        managed_git_admin=getattr(context.service, "managed_git_admin", None),
        human_review_eligibility_resolver=human_review_eligibility_resolver(
            getattr(context.auth, "user_store", None)
        ),
    )
    chat_service = TeamChatService(repository)
    runtime_service = TeamRuntimeService(
        repository,
        event_bus_factory=lambda: build_internal_event_bus_for_runtime(
            context.runtime_dir, project_dir=context.project_dir, base_config=context.config
        ),
    )
    avatar_storage = AvatarStorage(context.runtime_dir)
    chat_attachment_storage = TeamChatAttachmentStorage(context.runtime_dir)
    chat_attachment_policy_service = TeamChatAttachmentPolicyService(repository)

    def _state_payload(request: Request, state: TeamState) -> dict[str, Any]:
        payload = _team_state_payload_for_request(
            context.auth,
            request,
            state,
            runtime_dir=context.runtime_dir,
            session_records=_session_records_by_id(_session_repository_from_context(context)),
            agent_execution_records=_agent_execution_records_by_session(context.service),
        )
        _add_release_outbox_metadata(payload, repository)
        return payload

    def _state_response(
        request: Request,
        state: TeamState,
        *,
        status_code: int = 200,
        rebased_from_revision: int | None = None,
        extra: Mapping[str, Any] | None = None,
        publish_role_queue: bool = False,
    ) -> JSONResponse:
        if publish_role_queue:
            publication = request_team_outbox_publication_for_records(
                context,
                outbox_record_ids=[
                    record.id
                    for record in repository.list_outbox_records(
                        status="pending",
                        intent_type="team.role_queue.wake_requested",
                    )
                ],
            )
            if publication:
                extra = {**dict(extra or {}), "role_queue_outbox_publication": publication}
        if _request_wants_view_scoped_response(request):
            response_payload = _mutation_view_response_payload(
                state,
                rebased_from_revision=rebased_from_revision,
            )
            if extra:
                response_payload.update(extra)
            return JSONResponse(response_payload, status_code=status_code)
        response_payload = _state_payload(request, state)
        if rebased_from_revision is not None:
            response_payload["rebased_from_revision"] = rebased_from_revision
        if extra:
            response_payload.update(extra)
        return JSONResponse(response_payload, status_code=status_code)

    def _run_ui_rebasable_mutation(
        payload: Mapping[str, Any],
        mutate: Any,
        *,
        target_collection: str | None = None,
        target_id: str | None = None,
        create_collection: str | None = None,
        create_id: str | None = None,
    ) -> tuple[TeamState, int | None]:
        expected_revision = _expected_revision(payload)
        try:
            return mutate(expected_revision), None
        except TeamStateConflictError:
            current = repository.read()
            if not _can_rebase_ui_mutation(
                payload,
                current,
                target_collection=target_collection,
                target_id=target_id,
                create_collection=create_collection,
                create_id=create_id,
            ):
                raise TeamStateConflictError(
                    expected_revision=expected_revision,
                    current_revision=current.revision,
                )
            return mutate(current.revision), expected_revision

    def _task_project_owner_authorization_error(
        request: Request,
        *,
        state: TeamState,
        task_id: str,
        action: str,
    ) -> JSONResponse | None:
        task = state.tasks.get(task_id)
        if task is None:
            return JSONResponse({"error": "task not found"}, status_code=404)
        project = state.projects.get(task.project_id)
        if project is None:
            return JSONResponse({"error": "task project not found"}, status_code=404)
        return _directory_owner_authorization_error(
            request,
            directory_path=project.directory_path,
            action=action,
        )

    def _directory_owner_authorization_error(
        request: Request,
        *,
        directory_path: str,
        action: str,
    ) -> JSONResponse | None:
        if _auth_can(
            context.auth,
            request,
            ADMIN_OWNER_PERMISSION,
            directory_path=directory_path,
        ):
            return None
        return JSONResponse(
            {"error": (f"{action} requires directory owner permission for the affected project.")},
            status_code=403,
        )

    def route(
        path: str,
        handler: Any,
        *,
        methods: Sequence[str],
        require_privileged: bool | None = None,
    ) -> Route:
        protected_handler = handler
        needs_privileged = (
            _route_requires_privileged_operations(methods)
            if require_privileged is None
            else require_privileged
        )
        if needs_privileged:
            protected_handler = _with_privileged_operations(context, protected_handler)
        return Route(path, _with_errors(protected_handler), methods=methods)

    return TeamApiDependencies(
        context=context,
        repository=repository,
        admin_service=admin_service,
        chat_service=chat_service,
        runtime_service=runtime_service,
        avatar_storage=avatar_storage,
        chat_attachment_storage=chat_attachment_storage,
        chat_attachment_policy_service=chat_attachment_policy_service,
        _state_payload=_state_payload,
        _state_response=_state_response,
        _run_ui_rebasable_mutation=_run_ui_rebasable_mutation,
        _task_project_owner_authorization_error=_task_project_owner_authorization_error,
        _directory_owner_authorization_error=_directory_owner_authorization_error,
        route=route,
    )
