from __future__ import annotations

import json
from pathlib import Path

import pytest

from evander.admin.web.rbac import (
    ADMIN_OWNER_PERMISSION,
    OPERATOR_GROUP_ID,
    OWNER_GROUP_ID,
    PERMISSION_CATALOG,
    TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION,
    USERS_MANAGE_PERMISSION,
    VIEWER_GROUP_ID,
    WORKFLOW_RUNS_CONTROL_PERMISSION,
    WORKFLOWS_READ_PERMISSION,
    WORKFLOWS_WRITE_PERMISSION,
)
from evander.admin.web.user_store import AdminWebUserStore


def _admin_web_user_store(tmp_path: Path) -> AdminWebUserStore:
    runtime_dir = tmp_path / "runtime"
    return AdminWebUserStore(
        runtime_dir / "admin-web.db",
        legacy_json_path=runtime_dir / "admin-web-users.json",
    )


def test_admin_web_rbac_seeds_builtin_groups(tmp_path: Path) -> None:
    store = _admin_web_user_store(tmp_path)

    groups = store.list_groups()
    group_ids = [group.group_id for group in groups]

    assert group_ids == [
        "auditor",
        "operator",
        "owner",
        "viewer",
        "workflow-maintainer",
        "workflow-viewer",
    ]
    assert store.get_group(OWNER_GROUP_ID).is_required is True
    assert store.get_group(OWNER_GROUP_ID).permissions == (ADMIN_OWNER_PERMISSION,)
    assert WORKFLOWS_READ_PERMISSION in store.get_group(OPERATOR_GROUP_ID).permissions
    assert WORKFLOWS_WRITE_PERMISSION in store.get_group(OPERATOR_GROUP_ID).permissions
    assert WORKFLOW_RUNS_CONTROL_PERMISSION in store.get_group(OPERATOR_GROUP_ID).permissions
    assert TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION in PERMISSION_CATALOG
    assert (
        TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION
        not in store.get_group(OPERATOR_GROUP_ID).permissions
    )


def test_admin_web_rbac_migrates_legacy_roles_to_assignments(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "admin-web-users.json").write_text(
        json.dumps(
            {
                "Owner@example.com": {
                    "login": "Owner@example.com",
                    "role": "admin",
                    "display_name": "Owner",
                    "created_at": "2026-05-24T12:00:00Z",
                    "updated_at": "2026-05-24T12:00:00Z",
                    "last_login_at": None,
                },
                "Operator@example.com": {
                    "login": "Operator@example.com",
                    "role": "regular",
                    "display_name": "Operator",
                    "created_at": "2026-05-24T12:00:00Z",
                    "updated_at": "2026-05-24T12:00:00Z",
                    "last_login_at": None,
                },
            }
        ),
        encoding="utf-8",
    )
    store = _admin_web_user_store(tmp_path)

    assignments = {
        (assignment.login, assignment.group_id, assignment.directory_path)
        for assignment in store.list_user_group_assignments()
    }

    assert assignments == {
        ("operator@example.com", OPERATOR_GROUP_ID, "root"),
        ("owner@example.com", OWNER_GROUP_ID, "root"),
    }
    assert store.can("owner@example.com", ADMIN_OWNER_PERMISSION)
    assert store.can("owner@example.com", TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION)
    assert store.can("operator@example.com", WORKFLOWS_WRITE_PERMISSION)
    assert store.can("operator@example.com", WORKFLOW_RUNS_CONTROL_PERMISSION)
    assert (
        store.can("operator@example.com", TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION)
        is False
    )
    assert store.can("operator@example.com", USERS_MANAGE_PERMISSION) is False


def test_admin_web_rbac_applies_directory_scope_and_direct_workflow_grants(
    tmp_path: Path,
) -> None:
    store = _admin_web_user_store(tmp_path)
    store.upsert_user(login="owner@example.com", role="admin")
    store.upsert_user(login="reader@example.com", role="regular")
    store.remove_user_group_assignment(
        login="reader@example.com", group_id=OPERATOR_GROUP_ID, directory_path="root"
    )
    store.assign_user_group(
        login="reader@example.com", group_id=VIEWER_GROUP_ID, directory_path="root/team"
    )
    store.grant_user_workflow_permission(
        login="reader@example.com",
        permission=WORKFLOWS_WRITE_PERMISSION,
        directory_path="root/team",
    )

    assert store.can(
        "reader@example.com", WORKFLOWS_READ_PERMISSION, directory_path="root/team/sub"
    )
    assert store.can(
        "reader@example.com", WORKFLOWS_WRITE_PERMISSION, directory_path="root/team/sub"
    )
    assert not store.can(
        "reader@example.com", WORKFLOWS_WRITE_PERMISSION, directory_path="root/other"
    )


def test_admin_web_rbac_scopes_human_review_decisions_to_assigned_directory(
    tmp_path: Path,
) -> None:
    store = _admin_web_user_store(tmp_path)
    store.upsert_user(login="owner@example.com", role="admin")
    store.upsert_user(login="reviewer@example.com", role="regular")
    store.remove_user_group_assignment(
        login="reviewer@example.com",
        group_id=OPERATOR_GROUP_ID,
        directory_path="root",
    )
    store.upsert_group(
        group_id="human-reviewers",
        display_name="Human Reviewers",
        description="Decide assigned human release reviews.",
    )
    store.set_group_permissions(
        "human-reviewers",
        [TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION],
    )
    store.assign_user_group(
        login="reviewer@example.com",
        group_id="human-reviewers",
        directory_path="root/team",
    )

    assert store.can(
        "reviewer@example.com",
        TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION,
        directory_path="root/team/sub",
    )
    assert not store.can(
        "reviewer@example.com",
        TEAM_HUMAN_REVIEWS_DECIDE_PERMISSION,
        directory_path="root/other",
    )


def test_admin_web_rbac_blocks_removing_last_effective_owner(tmp_path: Path) -> None:
    store = _admin_web_user_store(tmp_path)
    store.upsert_user(login="owner@example.com", role="admin")

    with pytest.raises(ValueError, match="effective owner"):
        store.set_group_permissions(OWNER_GROUP_ID, set())

    assert store.can("owner@example.com", ADMIN_OWNER_PERMISSION)
