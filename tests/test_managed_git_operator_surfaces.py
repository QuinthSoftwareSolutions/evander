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
            "host_commit_identity": {
                "name": "Truman Bot",
                "email": "truman@quinth.se",
            },
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

    def set_host_commit_identity(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("set-host-identity", args, kwargs))
        self.repository["host_commit_identity"] = {
            "name": kwargs["name"],
            "email": kwargs["email"],
        }
        return dict(self.repository)

    def delete_repository(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("delete", args, kwargs))
        if self.delete_error is not None:
            raise self.delete_error
        return dict(self.repository)

    def maintenance(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("maintenance", args, kwargs))
        return {"maintained": [self.repository["managed_repo_id"]]}


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
    assert managed_git.calls[0] == (
        "list",
        (),
        {"directory_path": "root/team-a", "include_descendants": False},
    )
    operation, args, kwargs = managed_git.calls[1]
    assert operation == "register"
    assert args == ("root/team-a", "https://github.com/example/project.git")
    authorize_existing_owner = kwargs.pop("authorize_existing_owner")
    assert callable(authorize_existing_owner)
    assert kwargs == {"default_branch": "trunk", "refresh": False}
    assert auth.permission_paths == ["root/team-a", "root/team-a", "root/team-a"]


def test_managed_git_registration_reauthorizes_reused_ancestor_owner() -> None:
    class AncestorReuseManagedGitAdmin(FakeManagedGitAdmin):
        def register_repository(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            authorize_existing_owner = kwargs["authorize_existing_owner"]
            authorize_existing_owner("root")
            return super().register_repository(*args, **kwargs)

    class ChildOnlyAuth(FakeAuth):
        def require_permission(
            self,
            request: Request,
            permission: object,
            directory_path: object | None = None,
        ) -> None:
            super().require_permission(request, permission, directory_path)
            if str(directory_path) == "root":
                raise PermissionError("Permission required: admin.owner")

    managed_git = AncestorReuseManagedGitAdmin()
    auth = ChildOnlyAuth()
    service = SimpleNamespace(managed_git_admin=managed_git)
    client = TestClient(Starlette(routes=git_repository_routes(service, auth)))

    response = client.post(
        "/api/admin/git-repositories",
        json={
            "directory_path": "root/team-a",
            "repo_source": "https://github.com/example/project.git",
            "default_branch": "trunk",
            "refresh": False,
        },
    )

    assert response.status_code == 403
    assert response.json() == {"error": "Permission required: admin.owner"}
    assert auth.permission_paths == ["root/team-a", "root/team-a", "root"]


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


def test_managed_git_route_sets_repository_scoped_host_commit_identity() -> None:
    managed_git = FakeManagedGitAdmin()
    client, auth = _route_client(managed_git)

    response = client.put(
        (
            f"/api/admin/git-repositories/{managed_git.repository['managed_repo_id']}"
            "/host-commit-identity"
        ),
        json={
            "name": " Jonathan Quinth ",
            "email": " operator@example.invalid ",
        },
    )

    assert response.status_code == 200
    assert response.json()["repository"]["host_commit_identity"] == {
        "name": "Jonathan Quinth",
        "email": "operator@example.invalid",
    }
    assert [call[0] for call in managed_git.calls] == ["get", "set-host-identity"]
    assert managed_git.calls[1] == (
        "set-host-identity",
        (managed_git.repository["managed_repo_id"],),
        {
            "directory_path": "root/team-a",
            "name": "Jonathan Quinth",
            "email": "operator@example.invalid",
        },
    )
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


def test_managed_git_migration_cli_and_route_are_retired() -> None:
    from evander.cli import app

    managed_git = FakeManagedGitAdmin()
    client, _ = _route_client(managed_git)

    route_response = client.post(
        "/api/admin/git-repositories/migrate",
        json={"stage": "dry-run"},
    )
    cli_help = CliRunner().invoke(app, ["git-repos", "--help"])

    assert route_response.status_code == 405
    assert cli_help.exit_code == 0
    assert "migrate" not in cli_help.output
    assert managed_git.calls == []


def test_git_repos_cli_emits_json_and_forwards_registration(monkeypatch) -> None:
    from evander.cli import app

    managed_git = FakeManagedGitAdmin()
    monkeypatch.setattr(
        "evander.cli_runtime.build_admin_runtime",
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
        "evander.cli_runtime.build_admin_runtime",
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
        runner.invoke(
            app,
            [
                "git-repos",
                "set-host-identity",
                managed_repo_id,
                "--directory-path",
                "root/team-a",
                "--name",
                "Jonathan Quinth",
                "--email",
                "operator@example.invalid",
            ],
        ),
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
        "set-host-identity",
        "delete",
        "maintenance",
    ]
    assert managed_git.calls[0][2] == {
        "directory_path": "root/team-a",
        "include_descendants": False,
    }
    assert managed_git.calls[1][2] == {"directory_path": "root/team-a"}
    assert managed_git.calls[3][2] == {
        "directory_path": "root/team-a",
        "name": "Jonathan Quinth",
        "email": "operator@example.invalid",
    }


def test_git_repos_cli_exposes_agent_grant_revoke_and_clone_commands(monkeypatch) -> None:
    from evander.cli import app

    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
    repo_id = "mr_11111111111111111111"

    class VmAgentAdmin:
        def list_managed_git_access(self, *args: Any, **kwargs: Any):
            calls.append(("list", args, kwargs))
            return [{"managed_repo_id": repo_id, "lifetime": "session"}]

        def grant_managed_git_access(self, *args: Any, **kwargs: Any):
            calls.append(("grant", args, kwargs))
            return {"managed_repo_id": repo_id, "lifetime": "session"}

        def revoke_managed_git_access(self, *args: Any, **kwargs: Any):
            calls.append(("revoke", args, kwargs))
            return {"managed_repo_id": repo_id, "revoked": True}

        def clone_managed_repo_into_agent(self, *args: Any, **kwargs: Any):
            calls.append(("clone", args, kwargs))
            return {"managed_repo_id": repo_id, "target_dir": "/home/agent/repo"}

    monkeypatch.setattr(
        "evander.cli_runtime.build_admin_runtime",
        lambda _config: SimpleNamespace(vm_agent_admin=VmAgentAdmin()),
    )
    runner = CliRunner()
    results = [
        runner.invoke(app, ["git-repos", "grants", "session-1"]),
        runner.invoke(
            app,
            [
                "git-repos",
                "grant",
                "session-1",
                "--managed-repo-id",
                repo_id,
                "--access-mode",
                "read-only",
            ],
        ),
        runner.invoke(
            app,
            [
                "git-repos",
                "clone-into-agent",
                "session-1",
                "--managed-repo-id",
                repo_id,
                "--access-mode",
                "read-only",
                "--lifetime",
                "session",
            ],
        ),
        runner.invoke(
            app,
            ["git-repos", "revoke-grant", "session-1", "--managed-repo-id", repo_id],
        ),
    ]

    assert all(result.exit_code == 0 for result in results), [result.output for result in results]
    assert calls == [
        ("list", ("session-1",), {}),
        (
            "grant",
            ("session-1", repo_id),
            {"access_mode": "read-only", "lifetime": "session"},
        ),
        (
            "clone",
            ("session-1", repo_id),
            {"access_mode": "read-only", "lifetime": "session"},
        ),
        ("revoke", ("session-1", repo_id), {}),
    ]
