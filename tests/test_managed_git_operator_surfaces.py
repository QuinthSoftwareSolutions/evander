from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.testclient import TestClient
from typer.testing import CliRunner

from evander.admin.web.git_repositories import git_repository_routes


class FakeAuth:
    def __init__(self, *, privileged: bool = True, deny: bool = False) -> None:
        self.privileged = privileged
        self.deny = deny
        self.permission_paths: list[str | None] = []

    def require(self, _request: Request) -> dict[str, str]:
        if self.deny:
            raise PermissionError("Authentication required")
        return {"login": "owner"}

    def require_permission(
        self,
        _request: Request,
        _permission: object,
        directory_path: object | None = None,
    ) -> None:
        self.permission_paths.append(str(directory_path) if directory_path is not None else None)
        if self.deny:
            raise PermissionError("Permission required: admin.owner")

    def privileged_for_request(self, _request: Request) -> bool:
        return self.privileged


class ManagedGitConflict(RuntimeError):
    status_code = 409

    def __init__(self, message: str, *, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.details = details


class FakeManagedGitAdmin:
    def __init__(self) -> None:
        self.repository = {
            "managed_repo_id": "mr_0123456789abcdef0123",
            "directory_path": "root/team-a",
            "canonical_repository_url": "https://github.com/example/project",
            "default_branch": "main",
        }
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.delete_error: Exception | None = None

    def register_repository(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("register", args, kwargs))
        return dict(self.repository)

    def list_repositories(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(("list", args, kwargs))
        return [dict(self.repository)]

    def get_repository(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get", args, kwargs))
        return dict(self.repository)

    def refresh_repository(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("refresh", args, kwargs))
        return {**self.repository, "refreshed": True}

    def delete_repository(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("delete", args, kwargs))
        if self.delete_error is not None:
            raise self.delete_error
        return dict(self.repository)

    def maintenance(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("maintenance", args, kwargs))
        return {"maintained": [self.repository["managed_repo_id"]]}

    def migrate(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("migrate", args, kwargs))
        return {"stage": args[0], "migration_id": kwargs.get("migration_id")}


def _route_client(
    managed_git: FakeManagedGitAdmin,
    *,
    privileged: bool = True,
    deny: bool = False,
) -> tuple[TestClient, FakeAuth]:
    auth = FakeAuth(privileged=privileged, deny=deny)
    service = SimpleNamespace(managed_git_admin=managed_git)
    return TestClient(Starlette(routes=git_repository_routes(service, auth))), auth


def test_managed_git_routes_scope_list_and_registration() -> None:
    managed_git = FakeManagedGitAdmin()
    client, auth = _route_client(managed_git)

    listed = client.get(
        "/api/admin/git-repositories",
        params={"directory_path": "root/team-a", "include_descendants": "false"},
    )
    registered = client.post(
        "/api/admin/git-repositories",
        json={
            "directory_path": " root/team-a ",
            "repo_source": " https://github.com/example/project.git ",
            "default_branch": " trunk ",
            "refresh": False,
        },
    )

    assert listed.status_code == 200
    assert listed.json()["repositories"][0]["managed_repo_id"].startswith("mr_")
    assert registered.status_code == 200
    assert registered.json()["repository"]["directory_path"] == "root/team-a"
    assert managed_git.calls == [
        (
            "list",
            (),
            {"directory_path": "root/team-a", "include_descendants": False},
        ),
        (
            "register",
            ("root/team-a", "https://github.com/example/project.git"),
            {"default_branch": "trunk", "refresh": False},
        ),
    ]
    assert auth.permission_paths == ["root/team-a", "root/team-a", "root/team-a"]


def test_managed_git_routes_require_privilege_unlock_for_mutations() -> None:
    managed_git = FakeManagedGitAdmin()
    client, auth = _route_client(managed_git, privileged=False)

    response = client.post(
        f"/api/admin/git-repositories/{managed_git.repository['managed_repo_id']}/refresh"
    )

    assert response.status_code == 403
    assert response.json() == {"error": "Privileged operations are locked."}
    assert [call[0] for call in managed_git.calls] == ["get"]
    assert auth.permission_paths == ["root/team-a", "root/team-a"]


def test_managed_git_delete_returns_structured_conflict() -> None:
    managed_git = FakeManagedGitAdmin()
    managed_git.delete_error = ManagedGitConflict(
        "Repository still has active bindings",
        details={"bindings": ["issue:123"]},
    )
    client, _ = _route_client(managed_git)

    response = client.delete(
        f"/api/admin/git-repositories/{managed_git.repository['managed_repo_id']}"
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": "Repository still has active bindings",
        "details": {"bindings": ["issue:123"]},
    }


def test_managed_git_maintenance_accepts_empty_body_for_all_repositories() -> None:
    managed_git = FakeManagedGitAdmin()
    client, auth = _route_client(managed_git)

    response = client.post("/api/admin/git-repositories/maintenance")

    assert response.status_code == 200
    assert response.json() == {
        "maintenance": {"maintained": [managed_git.repository["managed_repo_id"]]}
    }
    assert managed_git.calls == [("maintenance", (None,), {})]
    assert auth.permission_paths == ["root", "root"]


def test_managed_git_maintenance_cannot_expand_child_scope_to_all_repositories() -> None:
    managed_git = FakeManagedGitAdmin()
    client, auth = _route_client(managed_git)

    response = client.post(
        "/api/admin/git-repositories/maintenance",
        json={"directory_path": "root/team-a"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": "directory_path requires managed_repo_id for repository-scoped maintenance"
    }
    assert managed_git.calls == []
    assert auth.permission_paths == []


def test_managed_git_migration_route_validates_stage_and_forwards_finalize_id() -> None:
    managed_git = FakeManagedGitAdmin()
    client, _ = _route_client(managed_git)

    missing_id = client.post(
        "/api/admin/git-repositories/migrate",
        json={"stage": "finalize"},
    )
    finalized = client.post(
        "/api/admin/git-repositories/migrate",
        json={"stage": "--FINALIZE", "migration_id": " migration-7 "},
    )

    assert missing_id.status_code == 400
    assert "migration_id is required" in missing_id.json()["error"]
    assert finalized.status_code == 200
    assert finalized.json() == {"migration": {"stage": "finalize", "migration_id": "migration-7"}}
    assert managed_git.calls == [("migrate", ("finalize",), {"migration_id": "migration-7"})]


def test_managed_git_migration_dry_run_does_not_require_privilege_unlock() -> None:
    managed_git = FakeManagedGitAdmin()
    client, auth = _route_client(managed_git, privileged=False)

    dry_run = client.post(
        "/api/admin/git-repositories/migrate",
        json={"stage": "dry_run"},
    )
    apply = client.post(
        "/api/admin/git-repositories/migrate",
        json={"stage": "apply"},
    )

    assert dry_run.status_code == 200
    assert dry_run.json()["migration"]["stage"] == "dry-run"
    assert apply.status_code == 403
    assert apply.json() == {"error": "Privileged operations are locked."}
    assert managed_git.calls == [("migrate", ("dry-run",), {"migration_id": None})]
    assert auth.permission_paths == ["root", "root", "root"]


def test_git_repos_cli_emits_json_and_forwards_registration(monkeypatch) -> None:
    from evander.cli import app

    managed_git = FakeManagedGitAdmin()
    monkeypatch.setattr(
        "evander.cli.build_admin_runtime",
        lambda _config: SimpleNamespace(managed_git_admin=managed_git),
    )

    result = CliRunner().invoke(
        app,
        [
            "git-repos",
            "register",
            "--directory-path",
            "root/team-a",
            "--repo-source",
            "https://github.com/example/project.git",
            "--default-branch",
            "trunk",
            "--no-refresh",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["managed_repo_id"] == managed_git.repository["managed_repo_id"]
    assert managed_git.calls == [
        (
            "register",
            ("root/team-a", "https://github.com/example/project.git"),
            {"default_branch": "trunk", "refresh": False},
        )
    ]


def test_git_repos_cli_exposes_repository_lifecycle_commands(monkeypatch) -> None:
    from evander.cli import app

    managed_git = FakeManagedGitAdmin()
    monkeypatch.setattr(
        "evander.cli.build_admin_runtime",
        lambda _config: SimpleNamespace(managed_git_admin=managed_git),
    )
    runner = CliRunner()
    managed_repo_id = managed_git.repository["managed_repo_id"]

    results = [
        runner.invoke(
            app,
            [
                "git-repos",
                "list",
                "--directory-path",
                "root/team-a",
                "--no-include-descendants",
            ],
        ),
        runner.invoke(
            app,
            ["git-repos", "show", managed_repo_id, "--directory-path", "root/team-a"],
        ),
        runner.invoke(app, ["git-repos", "refresh", managed_repo_id]),
        runner.invoke(app, ["git-repos", "delete", managed_repo_id]),
        runner.invoke(
            app,
            ["git-repos", "maintenance", "--managed-repo-id", managed_repo_id],
        ),
    ]

    assert all(result.exit_code == 0 for result in results), [result.output for result in results]
    assert json.loads(results[0].output)["repositories"][0]["managed_repo_id"] == managed_repo_id
    assert [call[0] for call in managed_git.calls] == [
        "list",
        "get",
        "refresh",
        "delete",
        "maintenance",
    ]
    assert managed_git.calls[0][2] == {
        "directory_path": "root/team-a",
        "include_descendants": False,
    }
    assert managed_git.calls[1][2] == {"directory_path": "root/team-a"}


def test_git_repos_cli_requires_exactly_one_migration_stage(monkeypatch) -> None:
    from evander.cli import app

    managed_git = FakeManagedGitAdmin()
    monkeypatch.setattr(
        "evander.cli.build_admin_runtime",
        lambda _config: SimpleNamespace(managed_git_admin=managed_git),
    )
    runner = CliRunner()

    missing = runner.invoke(app, ["git-repos", "migrate"])
    conflicting = runner.invoke(app, ["git-repos", "migrate", "--dry-run", "--apply"])
    finalized = runner.invoke(
        app,
        ["git-repos", "migrate", "--finalize", "migration-9"],
    )

    assert missing.exit_code == 2
    assert "Provide exactly one migration stage" in missing.output
    assert conflicting.exit_code == 2
    assert "Provide exactly one migration stage" in conflicting.output
    assert finalized.exit_code == 0, finalized.output
    assert json.loads(finalized.output) == {
        "migration_id": "migration-9",
        "stage": "finalize",
    }
    assert managed_git.calls == [("migrate", ("finalize",), {"migration_id": "migration-9"})]
