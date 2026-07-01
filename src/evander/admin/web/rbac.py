from __future__ import annotations

from collections.abc import Iterable, Mapping

from evander.models import DirectoryPath

ADMIN_OWNER_PERMISSION = "admin.owner"
USERS_MANAGE_PERMISSION = "users.manage"
GROUPS_MANAGE_PERMISSION = "groups.manage"
WORKFLOWS_READ_PERMISSION = "workflows.read"
WORKFLOWS_WRITE_PERMISSION = "workflows.write"
WORKFLOW_PACKS_MANAGE_PERMISSION = "workflow_packs.manage"
WORKFLOW_RUNS_READ_PERMISSION = "workflow_runs.read"
WORKFLOW_RUNS_CONTROL_PERMISSION = "workflow_runs.control"
TEAM_CHATS_READ_ALL_PERMISSION = "team_chats.read_all"

DIRECT_WORKFLOW_PERMISSIONS = frozenset(
    {
        WORKFLOWS_READ_PERMISSION,
        WORKFLOWS_WRITE_PERMISSION,
    }
)

PERMISSION_CATALOG = frozenset(
    {
        ADMIN_OWNER_PERMISSION,
        USERS_MANAGE_PERMISSION,
        GROUPS_MANAGE_PERMISSION,
        WORKFLOWS_READ_PERMISSION,
        WORKFLOWS_WRITE_PERMISSION,
        WORKFLOW_PACKS_MANAGE_PERMISSION,
        WORKFLOW_RUNS_READ_PERMISSION,
        WORKFLOW_RUNS_CONTROL_PERMISSION,
        TEAM_CHATS_READ_ALL_PERMISSION,
    }
)

OWNER_GROUP_ID = "owner"
OPERATOR_GROUP_ID = "operator"
WORKFLOW_MAINTAINER_GROUP_ID = "workflow-maintainer"
WORKFLOW_VIEWER_GROUP_ID = "workflow-viewer"
AUDITOR_GROUP_ID = "auditor"
VIEWER_GROUP_ID = "viewer"

REQUIRED_GROUP_IDS = frozenset({OWNER_GROUP_ID, OPERATOR_GROUP_ID})
LEGACY_ROLE_GROUP_IDS = {"admin": OWNER_GROUP_ID, "regular": OPERATOR_GROUP_ID}

BUILTIN_GROUP_DEFINITIONS: dict[str, dict[str, object]] = {
    OWNER_GROUP_ID: {
        "display_name": "Owner",
        "description": "Full control over the admin web interface.",
        "is_required": True,
        "permissions": frozenset({ADMIN_OWNER_PERMISSION}),
    },
    OPERATOR_GROUP_ID: {
        "display_name": "Operator",
        "description": "Operate workflows and workflow runs.",
        "is_required": True,
        "permissions": frozenset(
            {
                WORKFLOWS_READ_PERMISSION,
                WORKFLOWS_WRITE_PERMISSION,
                WORKFLOW_PACKS_MANAGE_PERMISSION,
                WORKFLOW_RUNS_READ_PERMISSION,
                WORKFLOW_RUNS_CONTROL_PERMISSION,
                TEAM_CHATS_READ_ALL_PERMISSION,
            }
        ),
    },
    WORKFLOW_MAINTAINER_GROUP_ID: {
        "display_name": "Workflow Maintainer",
        "description": "Read and update workflow definitions.",
        "is_required": False,
        "permissions": frozenset(
            {
                WORKFLOWS_READ_PERMISSION,
                WORKFLOWS_WRITE_PERMISSION,
                WORKFLOW_PACKS_MANAGE_PERMISSION,
            }
        ),
    },
    WORKFLOW_VIEWER_GROUP_ID: {
        "display_name": "Workflow Viewer",
        "description": "Read workflow definitions.",
        "is_required": False,
        "permissions": frozenset({WORKFLOWS_READ_PERMISSION}),
    },
    AUDITOR_GROUP_ID: {
        "display_name": "Auditor",
        "description": "Review workflows and workflow runs.",
        "is_required": False,
        "permissions": frozenset(
            {
                WORKFLOWS_READ_PERMISSION,
                WORKFLOW_RUNS_READ_PERMISSION,
            }
        ),
    },
    VIEWER_GROUP_ID: {
        "display_name": "Viewer",
        "description": "Read workflow definitions.",
        "is_required": False,
        "permissions": frozenset({WORKFLOWS_READ_PERMISSION}),
    },
}


def normalize_admin_web_group_id(value: object) -> str | None:
    group_id = str(value or "").strip().lower()
    return group_id or None


def normalize_admin_web_permission(value: object) -> str | None:
    permission = str(value or "").strip().lower()
    return permission or None


def normalize_admin_web_directory_path(value: object | None, *, default: str = "root") -> str:
    text = str(value or "").strip() or default
    return DirectoryPath(segments=text).as_posix()


def scope_matches(scope: object | None, directory_path: object | None = None) -> bool:
    normalized_scope = normalize_admin_web_directory_path(scope)
    normalized_directory_path = normalize_admin_web_directory_path(directory_path)
    return (
        normalized_directory_path == normalized_scope
        or normalized_directory_path.startswith(f"{normalized_scope}/")
    )


def expand_direct_workflow_permissions(permissions: Iterable[str]) -> frozenset[str]:
    normalized: set[str] = set()
    for value in permissions:
        permission = normalize_admin_web_permission(value)
        if permission is None or permission not in DIRECT_WORKFLOW_PERMISSIONS:
            continue
        normalized.add(permission)
    if WORKFLOWS_WRITE_PERMISSION in normalized:
        normalized.add(WORKFLOWS_READ_PERMISSION)
    return frozenset(normalized)


def effective_permissions(
    *,
    assignments: Iterable[tuple[str, str]],
    group_permissions: Mapping[str, Iterable[str]],
    direct_workflow_grants: Iterable[tuple[str, str]],
    directory_path: object | None = None,
) -> frozenset[str]:
    resolved_permissions: set[str] = set()
    target_directory_path = normalize_admin_web_directory_path(directory_path)

    for group_id, assignment_directory_path in assignments:
        if not scope_matches(assignment_directory_path, target_directory_path):
            continue
        for permission in group_permissions.get(group_id, ()):
            normalized_permission = normalize_admin_web_permission(permission)
            if normalized_permission is not None:
                resolved_permissions.add(normalized_permission)

    for permission, grant_directory_path in direct_workflow_grants:
        if not scope_matches(grant_directory_path, target_directory_path):
            continue
        resolved_permissions.update(expand_direct_workflow_permissions((permission,)))

    if ADMIN_OWNER_PERMISSION in resolved_permissions:
        return frozenset(PERMISSION_CATALOG)
    return frozenset(resolved_permissions)


def can(
    *,
    assignments: Iterable[tuple[str, str]],
    group_permissions: Mapping[str, Iterable[str]],
    direct_workflow_grants: Iterable[tuple[str, str]],
    permission: object,
    directory_path: object | None = None,
) -> bool:
    normalized_permission = normalize_admin_web_permission(permission)
    if normalized_permission is None:
        return False
    return normalized_permission in effective_permissions(
        assignments=assignments,
        group_permissions=group_permissions,
        direct_workflow_grants=direct_workflow_grants,
        directory_path=directory_path,
    )


def legacy_role_group_id(role: object) -> str | None:
    normalized_role = str(role or "").strip().lower()
    return LEGACY_ROLE_GROUP_IDS.get(normalized_role)


def is_effective_owner(
    *,
    assignments: Iterable[tuple[str, str]],
    group_permissions: Mapping[str, Iterable[str]],
    direct_workflow_grants: Iterable[tuple[str, str]],
    directory_path: object | None = None,
) -> bool:
    return can(
        assignments=assignments,
        group_permissions=group_permissions,
        direct_workflow_grants=direct_workflow_grants,
        permission=ADMIN_OWNER_PERMISSION,
        directory_path=directory_path,
    )
