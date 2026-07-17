from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from evander.admin.web.user_store import AdminWebUserStore


def _admin_web_user_store(tmp_path: Path) -> AdminWebUserStore:
    runtime_dir = tmp_path / "runtime"
    return AdminWebUserStore(
        runtime_dir / "admin-web.db",
        legacy_json_path=runtime_dir / "admin-web-users.json",
    )


def test_admin_web_user_store_initializes_sqlite_schema(tmp_path: Path) -> None:
    store = _admin_web_user_store(tmp_path)
    store.ensure_schema()

    with sqlite3.connect(tmp_path / "runtime" / "admin-web.db") as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert "admin_web_users" in tables
    assert "admin_web_user_preferences" in tables
    assert "admin_web_groups" in tables
    assert "admin_web_group_permissions" in tables
    assert "admin_web_user_group_assignments" in tables
    assert "admin_web_user_workflow_grants" in tables
    assert "admin_web_rbac_migrations" in tables


def test_admin_web_user_store_mutations_use_sqlite(tmp_path: Path) -> None:
    store = _admin_web_user_store(tmp_path)
    bootstrapped = store.bootstrap_admins({"Operator@Example.com"})
    created = store.upsert_user(
        login="regular@example.com", role="regular", display_name="Regular User"
    )
    updated = store.update_user(login="regular@example.com", display_name="Updated User")
    logged_in = store.record_login(login="regular@example.com", display_name="Ignored Login Name")
    deleted = store.delete_user("regular@example.com")

    assert [user.login for user in bootstrapped] == ["operator@example.com"]
    assert created.login == "regular@example.com"
    assert created.role == "regular"
    assert created.display_name == "Regular User"
    assert updated is not None
    assert updated.display_name == "Updated User"
    assert logged_in is not None
    assert logged_in.display_name == "Updated User"
    assert logged_in.last_login_at is not None
    assert deleted is not None
    assert deleted.login == "regular@example.com"
    assert [user.login for user in store.list_users()] == ["operator@example.com"]

    with sqlite3.connect(tmp_path / "runtime" / "admin-web.db") as connection:
        stored_logins = [
            row[0] for row in connection.execute("SELECT login FROM admin_web_users ORDER BY login")
        ]
    assert stored_logins == ["operator@example.com"]


def test_admin_web_user_store_imports_legacy_json_when_sqlite_empty(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "admin-web-users.json").write_text(
        json.dumps(
            {
                "Operator@Example.com": {
                    "login": "Operator@Example.com",
                    "role": "admin",
                    "display_name": "Operator",
                    "created_at": "2026-05-24T12:00:00Z",
                    "updated_at": "2026-05-24T12:00:00Z",
                    "last_login_at": None,
                }
            }
        ),
        encoding="utf-8",
    )
    store = _admin_web_user_store(tmp_path)

    users = store.list_users()

    assert [user.login for user in users] == ["operator@example.com"]
    assert users[0].display_name == "Operator"


def test_admin_web_user_store_does_not_import_legacy_json_when_sqlite_has_users(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "admin-web-users.json").write_text(
        json.dumps(
            {
                "legacy@example.com": {
                    "login": "legacy@example.com",
                    "role": "admin",
                    "created_at": "2026-05-24T12:00:00Z",
                    "updated_at": "2026-05-24T12:00:00Z",
                    "last_login_at": None,
                }
            }
        ),
        encoding="utf-8",
    )
    AdminWebUserStore(runtime_dir / "admin-web.db").upsert_user(
        login="sqlite@example.com", role="admin"
    )
    store = _admin_web_user_store(tmp_path)

    assert [user.login for user in store.list_users()] == ["sqlite@example.com"]


def test_admin_web_user_store_persists_user_preferences(tmp_path: Path) -> None:
    store = _admin_web_user_store(tmp_path)

    assert store.get_user_preference("Operator@Example.com", "workflow_tutorial_progress") is None

    stored = store.put_user_preference(
        "Operator@Example.com",
        "workflow_tutorial_progress",
        {"completed_tutorial_ids": ["first-steps"], "skipped_tutorial_ids": []},
    )

    assert stored == {"completed_tutorial_ids": ["first-steps"], "skipped_tutorial_ids": []}
    assert store.get_user_preference("operator@example.com", "workflow_tutorial_progress") == {
        "completed_tutorial_ids": ["first-steps"],
        "skipped_tutorial_ids": [],
    }
    assert store.get_user_preference("other@example.com", "workflow_tutorial_progress") is None
