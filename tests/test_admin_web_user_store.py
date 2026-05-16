from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from agent_sandbox.admin.web.user_store import AdminWebUserStore


def _admin_web_user_store(tmp_path: Path) -> AdminWebUserStore:
    return AdminWebUserStore(tmp_path / "runtime" / "admin-web-users.json")


def test_admin_web_user_store_mutations_use_transactional_updates(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    store = _admin_web_user_store(tmp_path)
    json_store = store._json_store()

    def fail_write_all(*args, **kwargs) -> None:
        raise AssertionError(
            "admin web user mutations must not use split read/write store updates"
        )

    monkeypatch.setattr(json_store, "write_all", fail_write_all)

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
