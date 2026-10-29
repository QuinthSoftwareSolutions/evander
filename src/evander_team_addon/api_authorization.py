"""Api authorization."""

from __future__ import annotations

from collections.abc import (
    Mapping,
    Sequence,
)
from typing import (
    Any,
)

from starlette.requests import (
    Request,
)

from evander.addons import (
    AddonContext,
)
from evander.admin.web.rbac import (
    ADMIN_OWNER_PERMISSION,
    TEAM_CHATS_MUTATE_PERMISSION,
    TEAM_CHATS_READ_ALL_PERMISSION,
    TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION,
    normalize_admin_web_group_id,
    scope_matches,
)
from evander.admin.web.shared import (
    request_has_privileged_operations,
)
from evander.admin.web.user_store import (
    normalize_admin_web_login,
)

from .api_requests import (
    _auth_can,
    _normalize_operator_user_id,
    _operator_display_name,
    _operator_identity_from_request,
    _optional_release_policy,
    _query_text,
    _session_repository_from_context,
)
from .chat_attachments import (
    normalize_directory_path,
)
from .models import (
    ProjectReleasePolicy,
    TeamState,
)
from .repository import (
    TeamStateRepository,
)
from .services.chat_service import (
    project_scoped_team_chat_authorization,
    reply_only_team_chat_authorization,
)
from .services.human_review_assignment_service import (
    reconcile_current_human_review_assignments,
)


def _conversation_agent_directory_path(
    context: AddonContext,
    state: TeamState,
    conversation: Any,
) -> str:
    return _strict_conversation_agent_directory_path(context, state, conversation) or "root"


def _strict_conversation_agent_directory_path(
    context: AddonContext,
    state: TeamState,
    conversation: Any,
) -> str | None:
    agent = state.agents.get(conversation.agent_id)
    if agent is None:
        return None
    repository = _session_repository_from_context(context)
    get_session = getattr(repository, "get_session", None)
    if not callable(get_session):
        return None
    try:
        session = get_session(agent.session_id)
    except Exception:
        return None
    if session is None or not getattr(session, "directory_path", None):
        return None
    try:
        return normalize_directory_path(session.directory_path)
    except (TypeError, ValueError):
        return None


def _team_chat_mutation_authorization_for_request(
    context: AddonContext,
    request: Request,
    state: TeamState,
    conversation: Any,
    *,
    principal_id: str,
):
    return _team_chat_mutation_authorization(
        auth=context.auth,
        service=getattr(context, "service", None),
        session_repository=_session_repository_from_context(context),
        request=request,
        state=state,
        conversation=conversation,
        principal_id=principal_id,
    )


def _team_chat_mutation_authorization(
    *,
    auth: Any | None,
    service: Any | None,
    session_repository: Any | None,
    request: Request,
    state: TeamState,
    conversation: Any,
    principal_id: str,
):
    agent = state.agents.get(conversation.agent_id)
    get_session = getattr(session_repository, "get_session", None)
    session = None
    if agent is not None and callable(get_session):
        try:
            session = get_session(agent.session_id)
        except Exception:
            session = None
    raw_directory_path = getattr(session, "directory_path", None)
    try:
        recipient_directory_path = (
            normalize_directory_path(raw_directory_path) if raw_directory_path else None
        )
    except (TypeError, ValueError):
        recipient_directory_path = None
    if recipient_directory_path is None:
        return reply_only_team_chat_authorization(
            source="admin_rbac",
            principal_id=principal_id,
            reason="recipient_directory_unresolved",
        )
    if auth is None or not request_has_privileged_operations(
        auth=auth,
        service=service,
        request=request,
    ):
        return reply_only_team_chat_authorization(
            source="admin_rbac",
            principal_id=principal_id,
            recipient_directory_path=recipient_directory_path,
            reason="privileged_operations_locked",
        )
    if not _auth_can(
        auth,
        request,
        TEAM_CHATS_MUTATE_PERMISSION,
        directory_path=recipient_directory_path,
    ):
        return reply_only_team_chat_authorization(
            source="admin_rbac",
            principal_id=principal_id,
            recipient_directory_path=recipient_directory_path,
            reason="permission_denied",
        )
    if agent is None:
        return reply_only_team_chat_authorization(
            source="admin_rbac",
            principal_id=principal_id,
            recipient_directory_path=recipient_directory_path,
            reason="recipient_agent_missing",
        )
    return project_scoped_team_chat_authorization(
        state,
        agent=agent,
        source="admin_rbac",
        principal_id=principal_id,
        recipient_directory_path=recipient_directory_path,
        project_is_authorized=lambda directory_path: _auth_can(
            auth,
            request,
            TEAM_CHATS_MUTATE_PERMISSION,
            directory_path=directory_path,
        ),
    )


def _chat_list_scope(
    auth: Any | None,
    request: Request,
    state: TeamState,
) -> dict[str, Any]:
    identity = _operator_identity_from_request(auth, request)
    requested_operator_user_id = _query_text(request, "operator_user_id") or _query_text(
        request,
        "view_operator_user_id",
    )
    if requested_operator_user_id is None:
        return _chat_scope_for_operator(
            auth, request, state, identity["user_id"], identity=identity
        )
    normalized_requested = _normalize_operator_user_id(requested_operator_user_id)
    if normalized_requested != identity["user_id"] and not _can_view_all_team_chats(
        auth,
        request,
    ):
        raise PermissionError(f"Permission required: {TEAM_CHATS_READ_ALL_PERMISSION}")
    return _chat_scope_for_operator(
        auth,
        request,
        state,
        normalized_requested,
        identity=identity,
    )


def _add_team_chat_mutation_modes(
    scope: dict[str, Any],
    *,
    auth: Any | None,
    service: Any | None,
    session_repository: Any | None,
    request: Request,
    state: TeamState,
) -> dict[str, Any]:
    current_operator_user_id = str(scope["current_operator"]["user_id"])
    selected_operator_user_id = str(scope["selected_operator"]["user_id"])
    modes: dict[str, str] = {}
    for conversation in state.conversations.values():
        if conversation.operator_user_id != selected_operator_user_id:
            continue
        if conversation.operator_user_id != current_operator_user_id:
            modes[conversation.id] = "reply_only"
            continue
        authorization = _team_chat_mutation_authorization(
            auth=auth,
            service=service,
            session_repository=session_repository,
            request=request,
            state=state,
            conversation=conversation,
            principal_id=current_operator_user_id,
        )
        modes[conversation.id] = authorization.mode
    return {**scope, "mutation_modes": modes}


def _chat_scope_for_operator(
    auth: Any | None,
    request: Request,
    state: TeamState,
    operator_user_id: str,
    *,
    identity: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    current = dict(identity or _operator_identity_from_request(auth, request))
    selected_user_id = _normalize_operator_user_id(operator_user_id)
    selected_display_name = _operator_display_name(
        state,
        selected_user_id,
        fallback=selected_user_id,
    )
    return {
        "current_operator": current,
        "selected_operator": {
            "user_id": selected_user_id,
            "display_name": selected_display_name,
        },
        "can_view_all_operators": _can_view_all_team_chats(auth, request),
        "operator_directory_options": _operator_directory_options(auth),
        "read_only": selected_user_id != current["user_id"],
    }


def _require_conversation_chat_scope(
    auth: Any | None,
    request: Request,
    state: TeamState,
    conversation: Any,
    *,
    write: bool,
    identity: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    scope = _chat_scope_for_operator(
        auth,
        request,
        state,
        conversation.operator_user_id,
        identity=identity,
    )
    if conversation.operator_user_id == scope["current_operator"]["user_id"]:
        return scope
    if write:
        raise PermissionError("Cannot send messages in another operator's Team chat")
    if not bool(scope["can_view_all_operators"]):
        raise PermissionError(f"Permission required: {TEAM_CHATS_READ_ALL_PERMISSION}")
    return scope


def _chat_scope_payload(
    state: TeamState,
    scope: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "current_operator": dict(scope["current_operator"]),
        "selected_operator": dict(scope["selected_operator"]),
        "can_view_all_operators": bool(scope.get("can_view_all_operators")),
        "read_only": bool(scope.get("read_only")),
        "operator_options": _operator_options(state, scope=scope),
    }


def _operator_options(
    state: TeamState,
    *,
    scope: Mapping[str, Any],
) -> list[dict[str, str]]:
    options: dict[str, dict[str, str]] = {}

    def _add(user_id: object, display_name: object | None = None) -> None:
        normalized = _normalize_operator_user_id(user_id)
        label = str(display_name or normalized).strip() or normalized
        existing = options.setdefault(
            normalized,
            {"user_id": normalized, "display_name": label},
        )
        if existing["display_name"] == normalized and label != normalized:
            existing["display_name"] = label

    current = scope["current_operator"]
    selected = scope["selected_operator"]
    _add(current["user_id"], current.get("display_name"))
    _add(selected["user_id"], selected.get("display_name"))
    if scope.get("can_view_all_operators"):
        for option in scope.get("operator_directory_options") or []:
            if isinstance(option, Mapping):
                _add(option.get("user_id"), option.get("display_name"))
        for conversation in state.conversations.values():
            _add(conversation.operator_user_id, conversation.operator_display_name)
    return sorted(options.values(), key=lambda item: item["display_name"].casefold())


def _strict_human_identity_from_request(
    auth: Any | None,
    request: Request,
) -> dict[str, str]:
    config = getattr(auth, "config", None)
    auth_mode = str(getattr(config, "auth_mode", "") or "").strip().casefold()
    if not auth_mode or auth_mode == "none":
        raise PermissionError("A real authenticated Admin Web session is required")
    read_session = getattr(auth, "read_session", None)
    if not callable(read_session):
        raise PermissionError("A real authenticated Admin Web session is required")
    session = read_session(request)
    if not isinstance(session, Mapping):
        raise PermissionError("A real authenticated Admin Web session is required")
    session_kind = str(session.get("kind") or "session").strip().casefold()
    if session_kind != "session":
        raise PermissionError("A real authenticated Admin Web session is required")
    session_id = str(session.get("sid") or "").strip()
    user_id = normalize_admin_web_login(session.get("login"))
    if user_id is None or not session_id or session_id == "local-dev":
        raise PermissionError("A real authenticated Admin Web session is required")
    display_name = str(session.get("name") or user_id).strip() or user_id
    return {
        "user_id": user_id,
        "display_name": display_name,
        "session_id": session_id,
    }


def _require_human_review_decision_identity(
    auth: Any | None,
    request: Request,
    human_request: Any,
    *,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    if payload is not None:
        _reject_client_human_identity_fields(payload)
    identity = _strict_human_identity_from_request(auth, request)
    user_id = identity["user_id"]
    if not _human_review_is_assigned_to(human_request, user_id):
        raise PermissionError("Human review request is not assigned to this user")

    directory_path = str(
        getattr(
            getattr(human_request, "requirement_snapshot", None),
            "directory_path",
            "root",
        )
        or "root"
    )
    assignment = getattr(human_request, "current_assignment", None)
    explicitly_assigned = {
        str(value).strip().casefold()
        for value in (getattr(assignment, "explicitly_assigned_user_ids", None) or ())
        if str(value).strip()
    }
    if user_id not in explicitly_assigned and not _is_current_configured_human_reviewer(
        auth,
        human_request,
        user_id=user_id,
        directory_path=directory_path,
    ):
        raise PermissionError("Human reviewer is no longer a member of an assigned reviewer group")

    require_permission = getattr(auth, "require_permission", None)
    if not callable(require_permission):
        raise PermissionError(f"Permission required: {TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION}")
    require_permission(
        request,
        TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION,
        directory_path=directory_path,
    )
    return identity


def _require_human_review_owner_identity(
    auth: Any | None,
    request: Request,
    human_request: Any,
    *,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    if payload is not None:
        _reject_client_human_identity_fields(payload)
    identity = _strict_human_identity_from_request(auth, request)
    directory_path = str(
        getattr(
            getattr(human_request, "requirement_snapshot", None),
            "directory_path",
            "root",
        )
        or "root"
    )
    require_permission = getattr(auth, "require_permission", None)
    if not callable(require_permission):
        raise PermissionError(f"Permission required: {ADMIN_OWNER_PERMISSION}")
    require_permission(
        request,
        ADMIN_OWNER_PERMISSION,
        directory_path=directory_path,
    )
    return identity


def _reject_client_human_identity_fields(payload: Mapping[str, Any]) -> None:
    forbidden_actor_fields = {
        "actor",
        "actor_id",
        "actor_user_id",
        "assigned_by_user_id",
        "display_name",
        "recorded_by_user_id",
        "reporter_display_name",
        "reporter_user_id",
        "resumed_by_user_id",
        "reviewer_display_name_snapshot",
        "reviewer_user_id",
        "user_id",
    }.intersection(payload)
    if forbidden_actor_fields:
        raise ValueError(
            "Human actor identity is derived from the authenticated session; "
            f"remove: {', '.join(sorted(forbidden_actor_fields))}"
        )


def _validate_human_review_reassignment_targets(
    auth: Any | None,
    human_request: Any,
    *,
    user_ids: Sequence[str],
) -> list[str]:
    normalized_user_ids = sorted(
        {
            normalized
            for value in user_ids
            if (normalized := normalize_admin_web_login(value)) is not None
        }
    )
    if len(normalized_user_ids) != len(set(user_ids)):
        raise ValueError("user_ids must contain valid unique Admin Web logins")
    if not normalized_user_ids:
        raise ValueError("human review reassignment requires at least one user_id")
    user_store = getattr(auth, "user_store", None)
    get_user = getattr(user_store, "get_user", None)
    can = getattr(user_store, "can", None)
    if not callable(get_user) or not callable(can):
        raise PermissionError("Admin Web user directory is unavailable")
    directory_path = str(
        getattr(
            getattr(human_request, "requirement_snapshot", None),
            "directory_path",
            "root",
        )
        or "root"
    )
    for user_id in normalized_user_ids:
        try:
            user = get_user(user_id)
            has_permission = can(
                user_id,
                TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION,
                directory_path=directory_path,
            )
        except Exception as exc:
            raise PermissionError("Admin Web user directory is unavailable") from exc
        if user is None:
            raise ValueError(f"Admin Web user {user_id} does not exist")
        if not has_permission:
            raise ValueError(
                f"Admin Web user {user_id} lacks "
                f"{TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION} in {directory_path}"
            )
    return normalized_user_ids


def _current_human_review_decision_eligible_user_ids(
    auth: Any | None,
    human_request: Any,
) -> list[str]:
    assignment = getattr(human_request, "current_assignment", None)
    assigned_user_ids = {
        normalized
        for value in (getattr(assignment, "eligible_user_ids", None) or ())
        if (normalized := normalize_admin_web_login(value)) is not None
    }
    explicit_user_ids = {
        normalized
        for value in (getattr(assignment, "explicitly_assigned_user_ids", None) or ())
        if (normalized := normalize_admin_web_login(value)) is not None
    }
    user_store = getattr(auth, "user_store", None)
    get_user = getattr(user_store, "get_user", None)
    can = getattr(user_store, "can", None)
    if not callable(get_user) or not callable(can):
        return []
    directory_path = str(
        getattr(
            getattr(human_request, "requirement_snapshot", None),
            "directory_path",
            "root",
        )
        or "root"
    )
    eligible: list[str] = []
    for user_id in sorted(assigned_user_ids):
        try:
            user = get_user(user_id)
            has_permission = can(
                user_id,
                TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION,
                directory_path=directory_path,
            )
        except Exception:
            continue
        if (
            user is not None
            and has_permission
            and (
                user_id in explicit_user_ids
                or _is_current_configured_human_reviewer(
                    auth,
                    human_request,
                    user_id=user_id,
                    directory_path=directory_path,
                )
            )
        ):
            eligible.append(user_id)
    return eligible


def _reconcile_human_review_request_assignment(
    auth: Any | None,
    repository: TeamStateRepository,
    human_review_request_id: str,
) -> dict[str, Any]:
    user_store = getattr(auth, "user_store", None)
    required_methods = (
        "list_user_group_assignments",
        "get_user",
        "can",
    )
    if user_store is None or any(
        not callable(getattr(user_store, method_name, None)) for method_name in required_methods
    ):
        raise PermissionError("Admin Web user directory is unavailable")
    return reconcile_current_human_review_assignments(
        repository=repository,
        user_store=user_store,
        request_ids=[human_review_request_id],
    )


def _can_read_human_review_request(
    auth: Any | None,
    request: Request,
    human_request: Any,
    *,
    identity: Mapping[str, str],
) -> bool:
    if _human_review_is_assigned_to(human_request, identity["user_id"]):
        return True
    directory_path = str(
        getattr(
            getattr(human_request, "requirement_snapshot", None),
            "directory_path",
            "root",
        )
        or "root"
    )
    return _auth_can(
        auth,
        request,
        ADMIN_OWNER_PERMISSION,
        directory_path=directory_path,
    )


def _can_read_release_human_review(
    auth: Any | None,
    request: Request,
    state: TeamState,
    release_package_id: str,
) -> bool:
    try:
        identity = _strict_human_identity_from_request(auth, request)
    except PermissionError:
        return False
    human_requests = [
        human_request
        for human_request in state.human_review_requests.values()
        if human_request.release_package_id == release_package_id
    ]
    if any(
        _can_read_human_review_request(
            auth,
            request,
            human_request,
            identity=identity,
        )
        for human_request in human_requests
    ):
        return True
    release = state.release_packages.get(release_package_id)
    directory_path = _release_human_review_directory(state, release)
    return _auth_can(
        auth,
        request,
        ADMIN_OWNER_PERMISSION,
        directory_path=directory_path,
    )


def _human_review_is_assigned_to(human_request: Any, user_id: str) -> bool:
    assignment = getattr(human_request, "current_assignment", None)
    assigned_ids = {
        str(value).strip().casefold()
        for field_name in (
            "eligible_user_ids",
            "explicitly_assigned_user_ids",
        )
        for value in (getattr(assignment, field_name, None) or ())
        if str(value).strip()
    }
    return user_id.casefold() in assigned_ids


def _is_current_configured_human_reviewer(
    auth: Any | None,
    human_request: Any,
    *,
    user_id: str,
    directory_path: str,
) -> bool:
    configured_group_ids = {
        str(value).strip().casefold()
        for value in (
            getattr(
                getattr(human_request, "requirement_snapshot", None),
                "configured_group_ids",
                None,
            )
            or ()
        )
        if str(value).strip()
    }
    if not configured_group_ids:
        return False
    user_store = getattr(auth, "user_store", None)
    list_assignments = getattr(user_store, "list_user_group_assignments", None)
    if not callable(list_assignments):
        return False
    try:
        assignments = list_assignments(user_id)
    except Exception:
        return False
    return any(
        str(getattr(assignment, "group_id", "") or "").strip().casefold() in configured_group_ids
        and scope_matches(
            getattr(assignment, "directory_path", None),
            directory_path,
        )
        for assignment in assignments
    )


def _release_human_review_directory(
    state: TeamState,
    release: Any | None,
) -> str:
    manifest = getattr(release, "manifest", None)
    requirement = getattr(manifest, "human_review_requirement", None)
    directory_path = str(getattr(requirement, "directory_path", "") or "").strip()
    if directory_path:
        return directory_path
    project = state.projects.get(getattr(release, "project_id", "") or "")
    return str(getattr(project, "directory_path", "") or "root").strip() or "root"


def _external_source_evidence_project_directory_path(
    state: TeamState,
    project_id: str | None,
) -> str:
    project = state.projects.get(project_id or "")
    if project is None:
        return "root"
    return normalize_directory_path(project.directory_path)


def _can_read_external_source_evidence(
    auth: Any | None,
    request: Request,
    state: TeamState,
    snapshot: Any,
) -> bool:
    return _auth_can(
        auth,
        request,
        ADMIN_OWNER_PERMISSION,
        directory_path=_external_source_evidence_project_directory_path(
            state,
            getattr(snapshot, "project_id", None),
        ),
    )


def _authorized_external_source_evidence_snapshot_ids(
    auth: Any | None,
    request: Request,
    state: TeamState,
) -> set[str]:
    return {
        snapshot.id
        for snapshot in state.external_source_evidence_snapshots.values()
        if _can_read_external_source_evidence(auth, request, state, snapshot)
    }


def _filter_external_source_evidence_payload(
    payload: dict[str, Any],
    *,
    allowed_snapshot_ids: set[str],
) -> None:
    snapshots = payload.get("external_source_evidence_snapshots")
    if isinstance(snapshots, dict):
        payload["external_source_evidence_snapshots"] = {
            snapshot_id: snapshot
            for snapshot_id, snapshot in snapshots.items()
            if snapshot_id in allowed_snapshot_ids
        }
    resource_tokens = payload.get("resource_tokens")
    if isinstance(resource_tokens, dict):
        snapshot_tokens = resource_tokens.get("external_source_evidence_snapshots")
        if isinstance(snapshot_tokens, dict):
            resource_tokens["external_source_evidence_snapshots"] = {
                snapshot_id: token
                for snapshot_id, token in snapshot_tokens.items()
                if snapshot_id in allowed_snapshot_ids
            }
    for collection in ("cases", "tasks", "dispatches"):
        rows = payload.get(collection)
        if not isinstance(rows, dict):
            continue
        for row in rows.values():
            if not isinstance(row, dict):
                continue
            snapshot_ids = row.get("source_evidence_snapshot_ids")
            if isinstance(snapshot_ids, list):
                row["source_evidence_snapshot_ids"] = [
                    snapshot_id
                    for snapshot_id in snapshot_ids
                    if snapshot_id in allowed_snapshot_ids
                ]


def _can_view_all_team_chats(auth: Any | None, request: Request) -> bool:
    can = getattr(auth, "can", None)
    if not callable(can):
        return False
    try:
        return bool(can(request, TEAM_CHATS_READ_ALL_PERMISSION))
    except Exception:
        return False


def _operator_directory_options(auth: Any | None) -> list[dict[str, str]]:
    user_store = getattr(auth, "user_store", None)
    list_users = getattr(user_store, "list_users", None)
    if not callable(list_users):
        return []
    options: list[dict[str, str]] = []
    try:
        users = list_users()
    except Exception:
        return []
    for user in users:
        user_id = _normalize_operator_user_id(getattr(user, "login", None))
        display_name = str(getattr(user, "display_name", "") or user_id).strip() or user_id
        options.append({"user_id": user_id, "display_name": display_name})
    return options


def _resolved_release_policy_for_admin(
    auth: Any | None,
    payload: Mapping[str, Any],
    *,
    directory_path: str,
) -> ProjectReleasePolicy | None:
    policy = _optional_release_policy(payload)
    if policy is None or policy.human_review_requirement is None:
        return policy
    requirement = policy.human_review_requirement
    normalized_directory = normalize_directory_path(directory_path)
    group_ids: list[str] = []
    for raw_group_id in requirement.reviewer_group_ids:
        group_id = normalize_admin_web_group_id(raw_group_id)
        if group_id is None:
            raise ValueError(f"invalid human reviewer group {raw_group_id!r}")
        if group_id not in group_ids:
            group_ids.append(group_id)

    user_store = getattr(auth, "user_store", None)
    get_group = getattr(user_store, "get_group", None)
    list_assignments = getattr(user_store, "list_user_group_assignments", None)
    get_user = getattr(user_store, "get_user", None)
    can = getattr(user_store, "can", None)
    if not all(callable(method) for method in (get_group, list_assignments, get_user, can)):
        raise ValueError(
            "Admin Web user directory is unavailable; human review policy cannot be configured"
        )
    missing_group_ids = [group_id for group_id in group_ids if get_group(group_id) is None]
    if missing_group_ids:
        raise ValueError(
            "human review policy references missing Admin groups: " + ", ".join(missing_group_ids)
        )
    try:
        assignments = list_assignments()
    except Exception as exc:
        raise ValueError("Admin Web user directory is unavailable") from exc
    eligible_user_ids: list[str] = []
    for assignment in assignments:
        assignment_group_id = normalize_admin_web_group_id(getattr(assignment, "group_id", None))
        if assignment_group_id not in group_ids or not scope_matches(
            getattr(assignment, "directory_path", None),
            normalized_directory,
        ):
            continue
        user_id = normalize_admin_web_login(getattr(assignment, "login", None))
        if user_id is None or user_id in eligible_user_ids:
            continue
        try:
            user = get_user(user_id)
            has_permission = can(
                user_id,
                TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION,
                directory_path=normalized_directory,
            )
        except Exception as exc:
            raise ValueError("Admin Web user directory is unavailable") from exc
        if user is not None and has_permission:
            eligible_user_ids.append(user_id)
    if not eligible_user_ids:
        raise ValueError(
            "human review policy has no active reviewer with "
            f"{TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION} in {normalized_directory}"
        )
    requirement.reviewer_group_ids = group_ids
    requirement.directory_path = normalized_directory
    requirement.resolved_reviewer_user_ids = sorted(eligible_user_ids)
    return policy
