from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace
import zipfile

import pytest

from evander.admin.services.managed_git_service import (
    ManagedGitAdminService,
    ManagedGitConflictError,
    REGISTRY_SCHEMA,
)
from evander.config import GatewayConfig
from evander.git_pr_notes import agent_pr_notes_ref_for_session
from evander.models import SessionManifest, ToolName
from evander.repositories.session_manifests import JsonSessionManifestRepository


def _run(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        list(args),
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, filename: str, content: str, message: str) -> str:
    (repo / filename).write_text(content, encoding="utf-8")
    _run("git", "add", filename, cwd=repo)
    _run("git", "commit", "-m", message, cwd=repo)
    return _run("git", "rev-parse", "HEAD", cwd=repo)


def _source_repository(tmp_path: Path) -> tuple[Path, Path, str]:
    upstream = tmp_path / "upstream.git"
    source = tmp_path / "source"
    _run("git", "init", "--bare", str(upstream))
    _run("git", "init", "-b", "main", str(source))
    _run("git", "config", "user.name", "Managed Git Test", cwd=source)
    _run("git", "config", "user.email", "managed-git@example.invalid", cwd=source)
    first = _commit(source, "README.md", "first\n", "initial")
    _run("git", "remote", "add", "origin", str(upstream), cwd=source)
    _run("git", "push", "-u", "origin", "main", cwd=source)
    return source, upstream, first


def _service(tmp_path: Path) -> ManagedGitAdminService:
    return ManagedGitAdminService(
        SimpleNamespace(runtime_dir=tmp_path / "runtime"),
        SimpleNamespace(),
        tmp_path,
    )


def _isolated_service(tmp_path: Path) -> ManagedGitAdminService:
    return ManagedGitAdminService(
        SimpleNamespace(runtime_dir=tmp_path / "runtime"),
        SimpleNamespace(),
        tmp_path,
    )


def _legacy_bare(
    tmp_path: Path,
    *,
    name: str,
    source: Path,
    canonical_source: Path,
) -> Path:
    legacy = tmp_path / "git-remotes" / f"{name}.git"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    _run("git", "clone", "--bare", str(source), str(legacy))
    _run(
        "git",
        f"--git-dir={legacy}",
        "config",
        "evander.canonicalOriginUrl",
        str(canonical_source),
    )
    _run("git", f"--git-dir={legacy}", "config", "evander.directoryPath", "root")
    return legacy


def _internal_record(service: ManagedGitAdminService, repo_id: str) -> dict[str, object]:
    registry = json.loads(
        (service.runtime_dir / "managed-git-repositories.json").read_text(encoding="utf-8")
    )
    assert registry["schema"] == REGISTRY_SCHEMA
    return registry["repositories"][repo_id]


def _bare_path(service: ManagedGitAdminService, repo_id: str) -> Path:
    return Path(str(_internal_record(service, repo_id)["internal_bare_path"]))


def test_registration_deduplicates_ancestor_and_isolates_siblings(tmp_path: Path) -> None:
    source, _, _ = _source_repository(tmp_path)
    service = _service(tmp_path)

    owner = service.register_repository("root/team-a", str(source))
    descendant = service.register_repository("root/team-a/project", str(source))
    sibling = service.register_repository("root/team-b", str(source))

    assert descendant["managed_repo_id"] == owner["managed_repo_id"]
    assert sibling["managed_repo_id"] != owner["managed_repo_id"]
    assert len(list((service.runtime_dir / "managed-git").glob("*.git"))) == 2
    assert service.get_repository(owner["managed_repo_id"], "root/team-a/child")
    with pytest.raises(LookupError):
        service.get_repository(owner["managed_repo_id"], "root/team-b")


def test_resolve_branch_commit_uses_scoped_managed_repository_ref(tmp_path: Path) -> None:
    source, _, first = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root/team-a", str(source))
    repo_id = record["managed_repo_id"]
    bare = _bare_path(service, repo_id)
    branch = "agents/session-backend/tasks/task"
    _run(
        "git",
        f"--git-dir={bare}",
        "update-ref",
        f"refs/heads/{branch}",
        first,
    )

    assert (
        service.resolve_branch_commit(
            repo_id,
            directory_path="root/team-a/project",
            branch=branch,
        )
        == first
    )
    assert (
        service.resolve_branch_commit(
            repo_id,
            directory_path="root/team-a",
            branch="agents/session-backend/tasks/missing",
        )
        is None
    )
    with pytest.raises(LookupError):
        service.resolve_branch_commit(
            repo_id,
            directory_path="root/team-b",
            branch=branch,
        )


def test_host_commit_identity_update_is_repository_scoped(tmp_path: Path) -> None:
    source, _, _ = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root/team-a", str(source))
    repo_id = record["managed_repo_id"]

    updated = service.set_host_commit_identity(
        repo_id,
        directory_path="root/team-a/project",
        name=" Jonathan Quinth ",
        email=" operator@example.invalid ",
    )

    assert updated["host_commit_identity"] == {
        "name": "Jonathan Quinth",
        "email": "operator@example.invalid",
    }
    assert _internal_record(service, repo_id)["host_commit_identity"] == {
        "name": "Jonathan Quinth",
        "email": "operator@example.invalid",
    }
    with pytest.raises(LookupError):
        service.set_host_commit_identity(
            repo_id,
            directory_path="root/team-b",
            name="Other Team",
            email="other-team@example.invalid",
        )
    with pytest.raises(ValueError, match="characters Git does not permit"):
        service.set_host_commit_identity(
            repo_id,
            directory_path="root/team-a",
            name="Invalid <Identity>",
            email="invalid@example.invalid",
        )


def test_descendant_cannot_override_ancestor_default_branch_without_owner_authorization(
    tmp_path: Path,
) -> None:
    source, _, _ = _source_repository(tmp_path)
    _run("git", "branch", "trunk", cwd=source)
    service = _service(tmp_path)
    owner = service.register_repository("root/team-a", str(source), refresh=False)
    bare = _bare_path(service, owner["managed_repo_id"])
    authorization_paths: list[str] = []

    def deny_ancestor(directory_path: str) -> None:
        authorization_paths.append(directory_path)
        raise PermissionError("Permission required: admin.owner")

    with pytest.raises(PermissionError, match="admin.owner"):
        service.register_repository(
            "root/team-a/project",
            str(source),
            default_branch="trunk",
            refresh=False,
            authorize_existing_owner=deny_ancestor,
        )

    assert authorization_paths == ["root/team-a"]
    assert service.get_repository(owner["managed_repo_id"])["default_branch"] == "main"
    assert _run("git", f"--git-dir={bare}", "config", "--get", "evander.baseBranch") == "main"


def test_registration_accepts_local_checkout_with_file_origin(tmp_path: Path) -> None:
    source, upstream, _ = _source_repository(tmp_path)
    _run("git", "remote", "set-url", "origin", upstream.as_uri(), cwd=source)

    record = _service(tmp_path).register_repository("root", str(source))

    assert record["canonical_repository_identity"] == f"local:{upstream}"
    assert record["canonical_repository_url"] == str(upstream)


def test_git_environment_drops_inherited_askpass_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    monkeypatch.setenv("EVANDER_GIT_USERNAME", "stale-user")
    monkeypatch.setenv("EVANDER_GIT_PASSWORD", "stale-secret")
    record = {
        "provider": "local",
        "directory_path": "root",
    }

    git_env = service._git_environment(record)
    worktree_env = service._safe_worktree_environment()

    assert "EVANDER_GIT_USERNAME" not in git_env
    assert "EVANDER_GIT_PASSWORD" not in git_env
    assert "EVANDER_GIT_USERNAME" not in worktree_env
    assert "EVANDER_GIT_PASSWORD" not in worktree_env


def test_git_environment_rewrites_azure_ssh_refresh_when_only_general_pat_is_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AZURE_DEVOPS_PR_PAT", raising=False)
    monkeypatch.setenv("AZURE_DEVOPS_PAT", "test-secret")
    service = ManagedGitAdminService(
        GatewayConfig(runtime_dir=tmp_path / "runtime"),
        SimpleNamespace(),
        tmp_path,
    )
    refresh_source = "ssh://git@ssh.dev.azure.com/v3/example/Example%20Project/ExampleRepo"
    canonical_url = "https://dev.azure.com/example/Example%20Project/_git/ExampleRepo"

    git_env = service._git_environment(
        {
            "provider": "azure_devops",
            "directory_path": "root",
            "refresh_source": refresh_source,
            "canonical_repository_url": canonical_url,
        }
    )
    git_config = {
        git_env[f"GIT_CONFIG_KEY_{index}"]: git_env[f"GIT_CONFIG_VALUE_{index}"]
        for index in range(int(git_env["GIT_CONFIG_COUNT"]))
    }

    assert git_env["EVANDER_GIT_USERNAME"] == "evander"
    assert git_env["EVANDER_GIT_PASSWORD"] == "test-secret"
    assert git_config[f"url.{canonical_url}.insteadOf"] == refresh_source


def test_git_environment_preserves_azure_ssh_refresh_without_pat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AZURE_DEVOPS_PR_PAT", raising=False)
    monkeypatch.delenv("AZURE_DEVOPS_PAT", raising=False)
    service = ManagedGitAdminService(
        GatewayConfig(runtime_dir=tmp_path / "runtime"),
        SimpleNamespace(),
        tmp_path,
    )

    git_env = service._git_environment(
        {
            "provider": "azure_devops",
            "directory_path": "root",
            "refresh_source": "ssh://git@ssh.dev.azure.com/v3/example/project/repo",
            "canonical_repository_url": "https://dev.azure.com/example/project/_git/repo",
        }
    )
    git_config_keys = {
        git_env[f"GIT_CONFIG_KEY_{index}"] for index in range(int(git_env["GIT_CONFIG_COUNT"]))
    }

    assert "EVANDER_GIT_PASSWORD" not in git_env
    assert not any(key.startswith("url.") for key in git_config_keys)


def test_git_environment_fails_closed_when_scoped_config_resolution_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_config = SimpleNamespace(
        runtime_dir=tmp_path / "runtime",
        github_token_env="ROOT_GITHUB_TOKEN",
    )

    def fail_resolution(directory_path: str) -> object:
        raise ValueError(f"malformed directory config for {directory_path}")

    runtime = SimpleNamespace(
        provider_config=SimpleNamespace(resolve_for_directory=fail_resolution)
    )
    service = ManagedGitAdminService(root_config, runtime, tmp_path)
    monkeypatch.setenv("ROOT_GITHUB_TOKEN", "root-secret")

    with pytest.raises(ValueError, match="malformed directory config for root/team-a"):
        service._git_environment(
            {
                "provider": "github",
                "directory_path": "root/team-a",
            }
        )


def test_git_subprocess_timeout_is_bounded_and_path_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = SimpleNamespace(
        runtime_dir=tmp_path / "runtime",
        managed_git_operation_timeout_seconds=0.25,
    )
    service = ManagedGitAdminService(config, SimpleNamespace(), tmp_path)
    captured: dict[str, object] = {}

    def timeout_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout_run)

    with pytest.raises(ManagedGitConflictError, match="timed out after 0.25 seconds") as exc:
        service._run_git(["git", "-C", "/secret/worktree", "status"])

    assert captured["timeout"] == 0.25
    assert "/secret/worktree" not in str(exc.value)


def test_resolve_snapshot_and_evidence_are_pinned_and_directory_scoped(tmp_path: Path) -> None:
    source, _, commit_sha = _source_repository(tmp_path)
    tree_hash = _run("git", "rev-parse", "HEAD^{tree}", cwd=source)
    service = _service(tmp_path)
    record = service.register_repository("root/team-a", str(source))

    snapshot = service.resolve_snapshot(
        record["managed_repo_id"],
        "root/team-a/project",
        source_ref="main",
        commit_sha=commit_sha,
    )
    assert snapshot == {
        "managed_repo_id": record["managed_repo_id"],
        "directory_path": "root/team-a/project",
        "source_ref": "refs/heads/main",
        "commit_sha": commit_sha,
    }
    assert service.resolve_snapshot(
        record["managed_repo_id"],
        "root/team-a/project",
        source_ref="main",
        commit_sha=commit_sha,
        tree_hash=tree_hash,
    ) == {
        **snapshot,
        "tree_hash": tree_hash,
    }
    with pytest.raises(ManagedGitConflictError, match="not the pinned tree"):
        service.resolve_snapshot(
            record["managed_repo_id"],
            "root/team-a/project",
            source_ref="main",
            commit_sha=commit_sha,
            tree_hash="f" * 40,
        )
    assert (
        service.validate_snapshot_evidence(
            record["managed_repo_id"],
            "root/team-a/project",
            commit_sha=commit_sha,
            evidence=[
                {
                    "path": "README.md",
                    "line_start": 1,
                    "line_end": 1,
                    "explanation": "The initial content is here.",
                }
            ],
        )[0]["path"]
        == "README.md"
    )

    with pytest.raises(LookupError):
        service.resolve_snapshot(record["managed_repo_id"], "root/team-b", source_ref="main")
    with pytest.raises(ValueError, match="outside"):
        service.validate_snapshot_evidence(
            record["managed_repo_id"],
            "root/team-a",
            commit_sha=commit_sha,
            evidence=[
                {
                    "path": "README.md",
                    "line_start": 1,
                    "line_end": 2,
                    "explanation": "Invalid range.",
                }
            ],
        )


def test_consultation_snapshot_pin_survives_source_ref_movement_until_release(
    tmp_path: Path,
) -> None:
    source, _, first_commit = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root/team-a", str(source))
    repo_id = record["managed_repo_id"]

    pinned = service.pin_snapshot(
        repo_id,
        "root/team-a",
        consultation_id="consult-pin-one",
        source_ref="main",
    )
    second_commit = _commit(source, "README.md", "second\n", "second")
    _run("git", "push", "origin", "main", cwd=source)
    service.refresh_repository(repo_id)
    bare_path = _bare_path(service, repo_id)

    assert pinned["commit_sha"] == first_commit
    assert _run("git", f"--git-dir={bare_path}", "rev-parse", "refs/heads/main") == (second_commit)
    assert _run("git", f"--git-dir={bare_path}", "rev-parse", pinned["pin_ref"]) == (first_commit)
    assert any(
        binding.get("kind") == "consultations" and binding.get("binding_id") == "consult-pin-one"
        for binding in service.get_repository(repo_id)["bindings"]
    )
    with pytest.raises(ManagedGitConflictError, match="active bindings"):
        service.delete_repository(repo_id)

    released = service.release_snapshot_pin(
        repo_id,
        "root/team-a",
        consultation_id="consult-pin-one",
        expected_commit_sha=first_commit,
    )
    assert released["released"] is True
    assert not any(
        binding.get("kind") == "consultations"
        for binding in service.get_repository(repo_id)["bindings"]
    )
    assert (
        subprocess.run(
            ["git", f"--git-dir={bare_path}", "rev-parse", "--verify", pinned["pin_ref"]],
            check=False,
            capture_output=True,
            text=True,
        ).returncode
        != 0
    )


def test_registration_rehomes_unused_descendant_when_ancestor_is_registered_later(
    tmp_path: Path,
) -> None:
    source, _, _ = _source_repository(tmp_path)
    service = _service(tmp_path)

    child = service.register_repository("root/team/project", str(source))
    ancestor = service.register_repository("root/team", str(source))

    assert ancestor["managed_repo_id"] != child["managed_repo_id"]
    assert ancestor["directory_path"] == "root/team"
    assert service.get_repository(ancestor["managed_repo_id"], "root/team/project")
    with pytest.raises(LookupError):
        service.get_repository(child["managed_repo_id"])
    assert len(list((service.runtime_dir / "managed-git").glob("*.git"))) == 1


def test_registration_refuses_to_widen_a_bound_descendant_trust_boundary(
    tmp_path: Path,
) -> None:
    source, _, _ = _source_repository(tmp_path)
    service = _service(tmp_path)
    child = service.register_repository("root/team/project", str(source))
    service.bind(child["managed_repo_id"], "sessions", "session-1")

    with pytest.raises(ManagedGitConflictError, match="active bindings"):
        service.register_repository("root/team", str(source))

    assert service.get_repository(child["managed_repo_id"])["directory_path"] == (
        "root/team/project"
    )


def test_remote_url_variants_normalize_without_persisting_credentials(tmp_path: Path) -> None:
    service = _service(tmp_path)

    github_ssh = service.register_repository(
        "root/github", "git@github.com:Example/Project.git", refresh=False
    )
    github_https = service.register_repository(
        "root/github",
        "https://token:super-secret@github.com/example/project.git?x=1#frag",
        refresh=False,
    )
    azure_ssh = service.register_repository(
        "root/azure", "git@ssh.dev.azure.com:v3/Org/Project/Repo.git", refresh=False
    )
    azure_https = service.register_repository(
        "root/azure", "https://Org@dev.azure.com/Org/Project/_git/Repo", refresh=False
    )

    assert github_ssh["managed_repo_id"] == github_https["managed_repo_id"]
    assert github_ssh["canonical_repository_url"] == "https://github.com/Example/Project.git"
    assert (
        service.clone_info(github_ssh["managed_repo_id"], "root/github", "session-1")["repo_name"]
        == "Project"
    )
    assert azure_ssh["managed_repo_id"] == azure_https["managed_repo_id"]
    registry_text = (service.runtime_dir / "managed-git-repositories.json").read_text(
        encoding="utf-8"
    )
    assert "super-secret" not in registry_text
    for repo_id in (github_ssh["managed_repo_id"], azure_ssh["managed_repo_id"]):
        config = _run(
            "git",
            f"--git-dir={_bare_path(service, repo_id)}",
            "config",
            "--local",
            "--list",
        ).casefold()
        assert "credential" not in config
        assert "remote." not in config


def test_github_pr_create_does_not_mix_fill_with_explicit_title_and_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_commit = "1" * 40
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    service = _service(tmp_path)
    record = {
        "canonical_repository_url": "https://github.com/example/project.git",
        "provider": "github",
        "directory_path": "root",
    }
    commands: list[list[str]] = []

    def _fake_run_git(
        command: list[str],
        *,
        env=None,
        input_text: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del env, input_text, check
        commands.append(command)
        if command[:3] == ["gh", "pr", "list"]:
            stdout = "[]"
        elif command[:3] == ["gh", "pr", "view"]:
            stdout = json.dumps(
                {
                    "number": 1,
                    "url": "https://example/pr/1",
                    "headRefOid": expected_commit,
                }
            )
        else:
            stdout = "https://example/pr/1\n"
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(service, "_run_git", _fake_run_git)

    result = service._create_or_reuse_github_pr(
        record,
        destination_branch="review/topic",
        base_branch="main",
        expected_commit_sha=expected_commit,
        title="Explicit title",
        body="",
        draft=False,
        fill=True,
    )

    create_command = next(command for command in commands if command[:3] == ["gh", "pr", "create"])
    assert result["created"] is True
    assert "--title" in create_command
    assert "--body-file" in create_command
    assert "--fill" not in create_command


def test_github_pr_reuse_rejects_moved_source_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_commit = "1" * 40
    current_commit = "2" * 40
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    service = _service(tmp_path)
    record = {
        "canonical_repository_url": "https://github.com/example/project.git",
        "provider": "github",
        "directory_path": "root",
    }

    def _fake_run_git(
        command: list[str],
        *,
        env=None,
        input_text: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del env, input_text, check
        assert command[:3] == ["gh", "pr", "list"]
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                [
                    {
                        "number": 17,
                        "url": "https://example/pr/17",
                        "headRefOid": current_commit,
                    }
                ]
            ),
            "",
        )

    monkeypatch.setattr(service, "_run_git", _fake_run_git)

    with pytest.raises(ManagedGitConflictError, match="source branch moved") as exc_info:
        service._create_or_reuse_github_pr(
            record,
            destination_branch="review/topic",
            base_branch="main",
            expected_commit_sha=expected_commit,
            title="Review topic",
            body="",
            draft=False,
            fill=True,
        )

    assert exc_info.value.details == {
        "reason": "source_ref_moved",
        "provider": "GitHub",
        "expected_commit_sha": expected_commit,
        "current_commit_sha": current_commit,
    }


def test_github_pr_create_rejects_moved_source_branch_after_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_commit = "1" * 40
    current_commit = "2" * 40
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    service = _service(tmp_path)
    record = {
        "canonical_repository_url": "https://github.com/example/project.git",
        "provider": "github",
        "directory_path": "root",
    }
    commands: list[list[str]] = []

    def _fake_run_git(
        command: list[str],
        *,
        env=None,
        input_text: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del env, input_text, check
        commands.append(command)
        if command[:3] == ["gh", "pr", "list"]:
            stdout = "[]"
        elif command[:3] == ["gh", "pr", "create"]:
            stdout = "https://example/pr/18\n"
        elif command[:3] == ["gh", "pr", "view"]:
            stdout = json.dumps(
                {
                    "number": 18,
                    "url": "https://example/pr/18",
                    "headRefOid": current_commit,
                }
            )
        else:
            assert command[:3] == ["gh", "pr", "close"]
            stdout = ""
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(service, "_run_git", _fake_run_git)

    with pytest.raises(ManagedGitConflictError, match="source branch moved") as exc_info:
        service._create_or_reuse_github_pr(
            record,
            destination_branch="review/topic",
            base_branch="main",
            expected_commit_sha=expected_commit,
            title="Review topic",
            body="",
            draft=False,
            fill=True,
        )

    assert exc_info.value.details["current_commit_sha"] == current_commit
    assert any(command[:3] == ["gh", "pr", "close"] for command in commands)


@pytest.mark.parametrize("reuse_existing", [True, False])
def test_azure_pr_create_and_reuse_reject_moved_source_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reuse_existing: bool,
) -> None:
    expected_commit = "1" * 40
    current_commit = "2" * 40
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "test-token")
    service = _service(tmp_path)
    record = {
        "canonical_repository_url": "https://dev.azure.com/Org/Project/_git/Repo",
        "provider": "azure_devops",
        "directory_path": "root",
    }
    pr_item = {
        "pullRequestId": 19,
        "url": "https://example/pr/19",
        "lastMergeSourceCommit": {"commitId": current_commit},
    }
    if reuse_existing:
        now = [0.0]
        monkeypatch.setattr(
            "evander.admin.services.managed_git_service."
            "_AZURE_PR_SOURCE_CONFIRMATION_TIMEOUT_SECONDS",
            1.0,
        )
        monkeypatch.setattr(
            "evander.admin.services.managed_git_service.time.monotonic",
            lambda: now[0],
        )
        monkeypatch.setattr(
            "evander.admin.services.managed_git_service.time.sleep",
            lambda seconds: now.__setitem__(0, now[0] + seconds),
        )
    responses = iter(
        [
            {"value": [pr_item]},
            {
                "value": [
                    {
                        "id": 1,
                        "sourceRefCommit": {"commitId": current_commit},
                    }
                ]
            },
        ]
        if reuse_existing
        else [{"value": []}, pr_item, {"status": "abandoned"}]
    )
    methods: list[str | None] = []

    def _fake_azure_request_json(
        url: str,
        *,
        auth: str,
        payload=None,
        method: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object]:
        del url, auth, payload, timeout_seconds
        methods.append(method)
        return next(responses)

    monkeypatch.setattr(service, "_azure_request_json", _fake_azure_request_json)

    with pytest.raises(ManagedGitConflictError, match="source branch moved") as exc_info:
        service._create_or_reuse_azure_pr(
            record,
            destination_branch="review/topic",
            base_branch="main",
            expected_commit_sha=expected_commit,
            title="Review topic",
            body="",
            draft=False,
        )

    assert exc_info.value.details == {
        "reason": "source_ref_moved",
        "provider": "Azure DevOps",
        "expected_commit_sha": expected_commit,
        "current_commit_sha": current_commit,
    }
    assert ("PATCH" in methods) is (not reuse_existing)


def test_azure_pr_reuse_waits_for_latest_iteration_source_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_commit = "1" * 40
    previous_commit = "2" * 40
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "test-token")
    service = _service(tmp_path)
    record = {
        "canonical_repository_url": "https://dev.azure.com/Org/Project/_git/Repo",
        "provider": "azure_devops",
        "directory_path": "root",
    }
    pr_item = {
        "pullRequestId": 19,
        "url": "https://example/pr/19",
        "lastMergeSourceCommit": {"commitId": previous_commit},
    }
    responses = iter(
        [
            {"value": [pr_item]},
            {
                "value": [
                    {
                        "id": 1,
                        "sourceRefCommit": {"commitId": previous_commit},
                    }
                ]
            },
            {
                "value": [
                    {
                        "id": 2,
                        "sourceRefCommit": {"commitId": expected_commit},
                    },
                    {
                        "id": 1,
                        "sourceRefCommit": {"commitId": previous_commit},
                    },
                ]
            },
            pr_item,
        ]
    )
    calls: list[dict[str, object]] = []
    sleeps: list[float] = []
    timeouts: list[float] = []
    now = [0.0]

    def _fake_azure_request_json(
        url: str,
        *,
        auth: str,
        payload=None,
        method: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object]:
        del auth
        calls.append(
            {
                "url": url,
                "payload": payload,
                "method": method,
                "timeout_seconds": timeout_seconds,
            }
        )
        if "/iterations?" in url:
            timeouts.append(timeout_seconds)
        return next(responses)

    monkeypatch.setattr(service, "_azure_request_json", _fake_azure_request_json)
    monkeypatch.setattr(
        "evander.admin.services.managed_git_service.time.monotonic",
        lambda: now[0],
    )

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(
        "evander.admin.services.managed_git_service.time.sleep",
        _sleep,
    )

    result = service._create_or_reuse_azure_pr(
        record,
        destination_branch="feature/issue-42",
        base_branch="main",
        expected_commit_sha=expected_commit,
        title="Updated title",
        body="Updated body",
        draft=False,
    )

    assert result["created"] is False
    assert sleeps == [1.0]
    assert timeouts == [10.0, 9.0]
    assert "/pullrequests/19/iterations?api-version=7.1" in str(calls[1]["url"])
    assert "/pullrequests/19/iterations?api-version=7.1" in str(calls[2]["url"])
    assert calls[3]["method"] == "PATCH"
    assert calls[3]["payload"] == {
        "description": "Updated body",
        "title": "Updated title",
    }


def test_latest_azure_pr_iteration_source_commit_uses_highest_iteration_id(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    assert (
        service._latest_azure_pr_iteration_source_commit(
            {
                "value": [
                    {
                        "id": 3,
                        "sourceRefCommit": {"commitId": "3" * 40},
                    },
                    {
                        "id": 1,
                        "sourceRefCommit": {"commitId": "1" * 40},
                    },
                    {
                        "id": 2,
                        "sourceRefCommit": {"commitId": "2" * 40},
                    },
                ]
            }
        )
        == "3" * 40
    )


def test_latest_azure_pr_iteration_source_commit_does_not_fallback_from_unverified_newest(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    assert (
        service._latest_azure_pr_iteration_source_commit(
            {
                "value": [
                    {
                        "id": 1,
                        "sourceRefCommit": {"commitId": "1" * 40},
                    },
                    {"id": 2},
                ]
            }
        )
        is None
    )


def test_azure_pr_reuse_does_not_confirm_older_matching_iteration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_commit = "1" * 40
    previous_commit = "2" * 40
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "test-token")
    monkeypatch.setattr(
        "evander.admin.services.managed_git_service."
        "_AZURE_PR_SOURCE_CONFIRMATION_TIMEOUT_SECONDS",
        1.0,
    )
    service = _service(tmp_path)
    record = {
        "canonical_repository_url": "https://dev.azure.com/Org/Project/_git/Repo",
        "provider": "azure_devops",
        "directory_path": "root",
    }
    pr_item = {
        "pullRequestId": 19,
        "url": "https://example/pr/19",
        "lastMergeSourceCommit": {"commitId": previous_commit},
    }
    now = [0.0]

    def _fake_azure_request_json(
        url: str,
        *,
        auth: str,
        payload=None,
        method: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object]:
        del auth, payload, method, timeout_seconds
        if "/iterations?" not in url:
            return {"value": [pr_item]}
        return {
            "value": [
                {
                    "id": 1,
                    "sourceRefCommit": {"commitId": expected_commit},
                },
                {"id": 2},
            ]
        }

    monkeypatch.setattr(service, "_azure_request_json", _fake_azure_request_json)
    monkeypatch.setattr(
        "evander.admin.services.managed_git_service.time.monotonic",
        lambda: now[0],
    )
    monkeypatch.setattr(
        "evander.admin.services.managed_git_service.time.sleep",
        lambda seconds: now.__setitem__(0, now[0] + seconds),
    )

    with pytest.raises(
        ManagedGitConflictError,
        match="source commit could not be verified",
    ) as exc_info:
        service._create_or_reuse_azure_pr(
            record,
            destination_branch="feature/issue-42",
            base_branch="main",
            expected_commit_sha=expected_commit,
            title="Updated title",
            body="Updated body",
            draft=False,
        )

    assert exc_info.value.details == {
        "reason": "provider_pr_source_commit_unverified",
        "provider": "Azure DevOps",
        "expected_commit_sha": expected_commit,
        "current_commit_sha": None,
    }


def test_azure_pr_reuse_confirmation_enforces_wall_clock_request_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_commit = "1" * 40
    current_commit = "2" * 40
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "test-token")
    monkeypatch.setattr(
        "evander.admin.services.managed_git_service."
        "_AZURE_PR_SOURCE_CONFIRMATION_TIMEOUT_SECONDS",
        2.0,
    )
    service = _service(tmp_path)
    record = {
        "canonical_repository_url": "https://dev.azure.com/Org/Project/_git/Repo",
        "provider": "azure_devops",
        "directory_path": "root",
    }
    pr_item = {
        "pullRequestId": 19,
        "url": "https://example/pr/19",
        "lastMergeSourceCommit": {"commitId": current_commit},
    }
    now = [100.0]
    timeouts: list[float] = []
    calls = 0

    def _fake_azure_request_json(
        url: str,
        *,
        auth: str,
        payload=None,
        method: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, object]:
        nonlocal calls
        del auth, payload, method
        calls += 1
        if "/iterations?" not in url:
            return {"value": [pr_item]}
        timeouts.append(timeout_seconds)
        now[0] += timeout_seconds
        return {
            "value": [
                {
                    "id": 1,
                    "sourceRefCommit": {"commitId": current_commit},
                }
            ]
        }

    monkeypatch.setattr(service, "_azure_request_json", _fake_azure_request_json)
    monkeypatch.setattr(
        "evander.admin.services.managed_git_service.time.monotonic",
        lambda: now[0],
    )

    with pytest.raises(ManagedGitConflictError, match="source branch moved"):
        service._create_or_reuse_azure_pr(
            record,
            destination_branch="feature/issue-42",
            base_branch="main",
            expected_commit_sha=expected_commit,
            title="Updated title",
            body="Updated body",
            draft=False,
        )

    assert calls == 2
    assert timeouts == [2.0]
    assert now[0] == 102.0


def test_azure_request_json_uses_explicit_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    timeouts: list[float] = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read() -> bytes:
            return b"{}"

    def _fake_urlopen(request, *, timeout: float):
        del request
        timeouts.append(timeout)
        return _Response()

    monkeypatch.setattr(
        "evander.admin.services.managed_git_service.urlopen",
        _fake_urlopen,
    )

    assert service._azure_request_json(
        "https://example.invalid/pr",
        auth="Basic test",
        timeout_seconds=2.5,
    ) == {}
    assert timeouts == [2.5]


def test_azure_pr_reuse_refreshes_metadata_links_work_item_and_returns_web_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_commit = "1" * 40
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "test-token")
    service = _service(tmp_path)
    record = {
        "canonical_repository_url": "https://dev.azure.com/Org/Project/_git/Repo",
        "provider": "azure_devops",
        "directory_path": "root",
    }
    pr_item = {
        "pullRequestId": 19,
        "url": "https://dev.azure.com/Org/Project/_apis/git/repositories/Repo/pullRequests/19",
        "_links": {"web": {"href": "https://dev.azure.com/Org/Project/_git/Repo/pullrequest/19"}},
        "lastMergeSourceCommit": {"commitId": expected_commit},
        "repository": {"id": "repo-id", "project": {"id": "project-id"}},
    }
    responses = iter(
        [
            {"value": [pr_item]},
            pr_item,
            {"relations": []},
            {"id": 42},
        ]
    )
    calls: list[dict[str, object]] = []

    def _fake_azure_request_json(
        url: str,
        *,
        auth: str,
        payload=None,
        method: str | None = None,
        content_type: str = "application/json",
    ) -> dict[str, object]:
        del auth
        calls.append(
            {
                "url": url,
                "payload": payload,
                "method": method,
                "content_type": content_type,
            }
        )
        return next(responses)

    monkeypatch.setattr(service, "_azure_request_json", _fake_azure_request_json)

    result = service._create_or_reuse_azure_pr(
        record,
        destination_branch="feature/issue-42",
        base_branch="main",
        expected_commit_sha=expected_commit,
        title="Updated title",
        body="x" * 4100,
        draft=False,
        title_explicit=True,
        work_item_id="42",
    )

    assert result["created"] is False
    assert result["pr_url"] == "https://dev.azure.com/Org/Project/_git/Repo/pullrequest/19"
    assert result["description_truncated"] is True
    assert result["description_original_length"] == 4100
    assert result["description_length"] == 4000
    assert result["work_item_linked"] is True
    assert all("/iterations?" not in str(call["url"]) for call in calls)
    assert calls[1]["method"] == "PATCH"
    assert calls[1]["payload"] == {"description": "x" * 4000, "title": "Updated title"}
    assert calls[3]["method"] == "PATCH"
    assert calls[3]["content_type"] == "application/json-patch+json"
    relation = calls[3]["payload"][0]["value"]  # type: ignore[index]
    assert relation["rel"] == "ArtifactLink"
    assert relation["url"] == "vstfs:///Git/PullRequestId/project-id%2Frepo-id%2F19"


def test_azure_pr_create_bounds_description_and_returns_web_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_commit = "1" * 40
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "test-token")
    service = _service(tmp_path)
    record = {
        "canonical_repository_url": "https://dev.azure.com/Org/Project/_git/Repo",
        "provider": "azure_devops",
        "directory_path": "root",
    }
    created = {
        "pullRequestId": 20,
        "url": "https://dev.azure.com/Org/Project/_apis/git/repositories/Repo/pullRequests/20",
        "_links": {"web": {"href": "https://dev.azure.com/Org/Project/_git/Repo/pullrequest/20"}},
        "lastMergeSourceCommit": {"commitId": expected_commit},
    }
    calls: list[dict[str, object]] = []

    def _fake_azure_request_json(
        url: str,
        *,
        auth: str,
        payload=None,
        method: str | None = None,
        content_type: str = "application/json",
    ) -> dict[str, object]:
        del url, auth, content_type
        calls.append({"payload": payload, "method": method})
        return {"value": []} if len(calls) == 1 else created

    monkeypatch.setattr(service, "_azure_request_json", _fake_azure_request_json)

    result = service._create_or_reuse_azure_pr(
        record,
        destination_branch="feature/topic",
        base_branch="main",
        expected_commit_sha=expected_commit,
        title="Topic",
        body="y" * 4001,
        draft=True,
    )

    assert result["created"] is True
    assert result["pr_url"] == "https://dev.azure.com/Org/Project/_git/Repo/pullrequest/20"
    assert result["description_truncated"] is True
    assert len(calls[1]["payload"]["description"]) == 4000  # type: ignore[index]


def test_pr_tracking_persists_map_and_session_feedback_binding(tmp_path: Path) -> None:
    config = SimpleNamespace(
        runtime_dir=tmp_path / "runtime",
        pr_map_path=tmp_path / "runtime" / "pr-map.json",
        session_store_path=tmp_path / "runtime" / "sessions.json",
    )
    sessions = JsonSessionManifestRepository(config.session_store_path)
    sessions.upsert_session(
        SessionManifest(
            session_id="issue-42-run",
            directory_path="root/team-a",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    service = ManagedGitAdminService(
        config,
        SimpleNamespace(session_repository=sessions),
        tmp_path,
    )

    service._persist_pr_tracking(
        {
            "canonical_repository_url": "https://github.com/example/repo.git",
            "provider": "github",
        },
        pr={
            "provider": "github",
            "pr_id": "42",
            "pr_url": "https://github.com/example/repo/pull/42",
        },
        source_branch="feature/issue-42",
        target_branch="main",
        session_id="issue-42-run",
        directory_path="root/team-a",
    )

    pr_map = json.loads(config.pr_map_path.read_text(encoding="utf-8"))
    entry = pr_map["entries"]["github:example/repo:42"]
    assert entry["session_id"] == "issue-42-run"
    assert entry["directory_path"] == "root/team-a"
    session = sessions.get_session("issue-42-run")
    assert session is not None
    assert session.metadata["pr_feedback_streams"] == [
        {"provider": "github", "repo": "example/repo", "pr_id": "42"}
    ]
    assert ToolName.LIST_UNREAD_FEEDBACK in session.allowed_tools
    assert ToolName.POST_PR_FEEDBACK_REPLY in session.allowed_tools


def test_pr_tracking_uses_session_directory_instead_of_workflow_directory(
    tmp_path: Path,
) -> None:
    config = SimpleNamespace(
        runtime_dir=tmp_path / "runtime",
        pr_map_path=tmp_path / "runtime" / "pr-map.json",
        session_store_path=tmp_path / "runtime" / "sessions.json",
    )
    sessions = JsonSessionManifestRepository(config.session_store_path)
    sessions.upsert_session(
        SessionManifest(
            session_id="issue-42-run",
            directory_path="root/team-a",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    service = ManagedGitAdminService(
        config,
        SimpleNamespace(session_repository=sessions),
        tmp_path,
    )

    service._persist_pr_tracking(
        {
            "canonical_repository_url": "https://dev.azure.com/Org/Project/_git/Repo",
            "provider": "azure_devops",
        },
        pr={
            "provider": "azure_devops",
            "pr_id": "42",
            "pr_url": "https://dev.azure.com/Org/Project/_git/Repo/pullrequest/42",
        },
        source_branch="feature/issue-42",
        target_branch="main",
        session_id="issue-42-run",
        directory_path="root",
    )

    pr_map = json.loads(config.pr_map_path.read_text(encoding="utf-8"))
    entry = pr_map["entries"]["azure_devops:Org/Project/Repo:42"]
    assert entry["session_id"] == "issue-42-run"
    assert entry["directory_path"] == "root/team-a"


def test_pr_tracking_without_session_keeps_workflow_directory(tmp_path: Path) -> None:
    config = SimpleNamespace(
        runtime_dir=tmp_path / "runtime",
        pr_map_path=tmp_path / "runtime" / "pr-map.json",
        session_store_path=tmp_path / "runtime" / "sessions.json",
    )
    service = ManagedGitAdminService(
        config,
        SimpleNamespace(
            session_repository=JsonSessionManifestRepository(config.session_store_path)
        ),
        tmp_path,
    )

    service._persist_pr_tracking(
        {
            "canonical_repository_url": "https://github.com/example/repo.git",
            "provider": "github",
        },
        pr={
            "provider": "github",
            "pr_id": "42",
            "pr_url": "https://github.com/example/repo/pull/42",
        },
        source_branch="feature/issue-42",
        target_branch="main",
        session_id="",
        directory_path="root/team-a",
    )

    pr_map = json.loads(config.pr_map_path.read_text(encoding="utf-8"))
    entry = pr_map["entries"]["github:example/repo:42"]
    assert entry["session_id"] == ""
    assert entry["directory_path"] == "root/team-a"


def test_pr_create_rejects_unsupported_provider_before_push(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(
        service,
        "_require_scoped_record",
        lambda *_args, **_kwargs: {
            "provider": "local",
            "default_branch": "main",
        },
    )
    push_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        service,
        "push_branch",
        lambda *args, **kwargs: push_calls.append((args, kwargs)),
    )

    with pytest.raises(ValueError, match="unsupported for provider local"):
        service.pr_create(
            managed_repo_id="mr_0123456789abcdefabcd",
            source_ref="refs/evander/operations/op-1/source",
            expected_commit_sha="a" * 40,
            destination_branch="feature/topic",
            directory_path="root",
        )

    assert push_calls == []


def test_pr_create_rejects_unknown_tracking_session_before_push(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(
        service,
        "_require_scoped_record",
        lambda *_args, **_kwargs: {
            "provider": "github",
            "canonical_repository_url": "https://github.com/example/repo.git",
            "default_branch": "main",
        },
    )
    push_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        service,
        "push_branch",
        lambda *args, **kwargs: push_calls.append((args, kwargs)),
    )

    with pytest.raises(ValueError, match="Unknown session for PR feedback tracking: missing"):
        service.pr_create(
            managed_repo_id="mr_0123456789abcdefabcd",
            source_ref="refs/evander/operations/op-1/source",
            expected_commit_sha="a" * 40,
            destination_branch="feature/topic",
            directory_path="root",
            session_id="missing",
        )

    assert push_calls == []
    assert not service._pr_map_path().exists()


def test_azure_work_item_id_is_derived_from_issue_session() -> None:
    assert ManagedGitAdminService._azure_work_item_id("", "issue-14760-run-1") == "14760"
    assert ManagedGitAdminService._azure_work_item_id("42", "issue-14760-run-1") == "42"


def test_local_registration_never_mutates_checkout(tmp_path: Path) -> None:
    source, _, _ = _source_repository(tmp_path)
    service = _service(tmp_path)
    before_head = _run("git", "rev-parse", "HEAD", cwd=source)
    before_config = (source / ".git" / "config").read_bytes()
    before_status = _run("git", "status", "--porcelain=v1", cwd=source)

    record = service.register_repository("root", str(source))

    assert record["managed_repo_id"].startswith("mr_")
    assert (
        _run(
            "git",
            f"--git-dir={_bare_path(service, record['managed_repo_id'])}",
            "config",
            "--get",
            "evander.baseBranch",
        )
        == "main"
    )
    assert _run("git", "rev-parse", "HEAD", cwd=source) == before_head
    assert (source / ".git" / "config").read_bytes() == before_config
    assert _run("git", "status", "--porcelain=v1", cwd=source) == before_status
    assert "internal_bare_path" not in record
    assert "refresh_source" not in record


def test_materialize_candidate_checkout_creates_exact_detached_checkout_without_internal_path(
    tmp_path: Path,
) -> None:
    source, _, commit_sha = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root/team", str(source))
    tree_hash = _run("git", "rev-parse", f"{commit_sha}^{{tree}}", cwd=source)
    target = tmp_path / "candidate-review"

    result = service.materialize_candidate_checkout(
        record["managed_repo_id"],
        "root/team/project",
        source_ref="refs/heads/main",
        commit_sha=commit_sha,
        tree_hash=tree_hash,
        target_dir=target,
    )

    assert result == {
        "managed_repo_id": record["managed_repo_id"],
        "directory_path": "root/team/project",
        "canonical_repository_identity": record["canonical_repository_identity"],
        "source_ref": "refs/heads/main",
        "commit_sha": commit_sha,
        "tree_hash": tree_hash,
        "target_dir": str(target),
        "detached": True,
    }
    assert _run("git", "rev-parse", "HEAD", cwd=target) == commit_sha
    assert _run("git", "rev-parse", "HEAD^{tree}", cwd=target) == tree_hash
    assert subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"],
        cwd=target,
        check=False,
        capture_output=True,
        text=True,
    ).returncode == 1
    assert _run("git", "remote", "get-url", "origin", cwd=target) == record[
        "canonical_repository_url"
    ]
    internal_path = str(_bare_path(service, record["managed_repo_id"]))
    git_metadata = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (target / ".git").rglob("*")
        if path.is_file()
    )
    assert internal_path not in git_metadata
    assert str(service.runtime_dir / "git-worktrees") not in git_metadata


def test_export_pinned_source_archive_writes_verified_source_only_zip(
    tmp_path: Path,
) -> None:
    source, _, commit_sha = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root/team", str(source))
    tree_hash = _run("git", "rev-parse", f"{commit_sha}^{{tree}}", cwd=source)
    archive_path = tmp_path / "candidate-review.zip"

    result = service.export_pinned_source_archive(
        record["managed_repo_id"],
        "root/team/project",
        source_ref="refs/heads/main",
        commit_sha=commit_sha,
        tree_hash=tree_hash,
        archive_path=archive_path,
    )

    assert result == {
        "managed_repo_id": record["managed_repo_id"],
        "directory_path": "root/team/project",
        "source_ref": "refs/heads/main",
        "commit_sha": commit_sha,
        "tree_hash": tree_hash,
    }
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == ["README.md"]
        assert archive.read("README.md") == b"first\n"

    _commit(source, "README.md", "second\n", "move main")
    _run("git", "push", "origin", "main", cwd=source)
    service.refresh_repository(record["managed_repo_id"])
    with pytest.raises(ManagedGitConflictError, match=f"not {commit_sha}"):
        service.export_pinned_source_archive(
            record["managed_repo_id"],
            "root/team/project",
            source_ref="refs/heads/main",
            commit_sha=commit_sha,
            tree_hash=tree_hash,
            archive_path=tmp_path / "moved-candidate-review.zip",
        )


def test_materialize_candidate_checkout_refuses_existing_or_mismatched_targets(
    tmp_path: Path,
) -> None:
    source, _, commit_sha = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root", str(source))
    tree_hash = _run("git", "rev-parse", f"{commit_sha}^{{tree}}", cwd=source)
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep.txt").write_text("keep\n", encoding="utf-8")

    with pytest.raises(ManagedGitConflictError, match="never overwrites"):
        service.materialize_candidate_checkout(
            record["managed_repo_id"],
            "root",
            source_ref="main",
            commit_sha=commit_sha,
            tree_hash=tree_hash,
            target_dir=existing,
        )
    assert (existing / "keep.txt").read_text(encoding="utf-8") == "keep\n"

    mismatched = tmp_path / "mismatched"
    with pytest.raises(ManagedGitConflictError, match="pinned tree"):
        service.materialize_candidate_checkout(
            record["managed_repo_id"],
            "root",
            source_ref="main",
            commit_sha=commit_sha,
            tree_hash="f" * 40,
            target_dir=mismatched,
        )
    assert not mismatched.exists()

    _commit(source, "README.md", "second\n", "move main")
    _run("git", "push", "origin", "main", cwd=source)
    service.refresh_repository(record["managed_repo_id"])
    moved_ref_target = tmp_path / "moved-ref"
    with pytest.raises(ManagedGitConflictError, match="no longer equals"):
        service.materialize_candidate_checkout(
            record["managed_repo_id"],
            "root",
            source_ref="main",
            commit_sha=commit_sha,
            tree_hash=tree_hash,
            target_dir=moved_ref_target,
        )
    assert not moved_ref_target.exists()

    with pytest.raises(ValueError, match="outside the Evander runtime"):
        service.materialize_candidate_checkout(
            record["managed_repo_id"],
            "root",
            source_ref="main",
            commit_sha=commit_sha,
            tree_hash=tree_hash,
            target_dir=service.runtime_dir / "review",
        )


def test_default_branch_override_is_stored_in_registry_and_bare_metadata(
    tmp_path: Path,
) -> None:
    source, _, _ = _source_repository(tmp_path)
    _run("git", "branch", "trunk", cwd=source)
    service = _service(tmp_path)

    record = service.register_repository("root", str(source), default_branch="trunk")
    bare = _bare_path(service, record["managed_repo_id"])

    assert record["default_branch"] == "trunk"
    assert _run("git", f"--git-dir={bare}", "config", "--get", "evander.baseBranch") == "trunk"
    assert _run("git", f"--git-dir={bare}", "symbolic-ref", "HEAD") == "refs/heads/trunk"


def test_failed_refresh_does_not_persist_invalid_default_branch_override(
    tmp_path: Path,
) -> None:
    source, _, _ = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root", str(source))
    bare = _bare_path(service, record["managed_repo_id"])

    with pytest.raises(ManagedGitConflictError, match="not present upstream"):
        service.register_repository(
            "root",
            str(source),
            default_branch="missing-branch",
            refresh=True,
        )

    stored = _internal_record(service, record["managed_repo_id"])
    assert stored["default_branch"] == "main"
    assert stored["default_branch_source"] == "upstream"
    assert _run("git", f"--git-dir={bare}", "config", "--get", "evander.baseBranch") == ("main")


def test_failed_refresh_after_descendant_promotion_rolls_back_default_override(
    tmp_path: Path,
) -> None:
    source, _, _ = _source_repository(tmp_path)
    service = _service(tmp_path)
    service.register_repository("root/team/project", str(source))

    with pytest.raises(ManagedGitConflictError, match="not present upstream"):
        service.register_repository(
            "root/team",
            str(source),
            default_branch="missing-branch",
            refresh=True,
        )

    records = service.list_repositories()
    assert len(records) == 1
    assert records[0]["directory_path"] == "root/team"
    assert records[0]["default_branch"] == "main"
    bare = _bare_path(service, records[0]["managed_repo_id"])
    assert _run("git", f"--git-dir={bare}", "config", "--get", "evander.baseBranch") == ("main")


def test_refresh_updates_canonical_refs_and_preserves_host_and_guest_namespaces(
    tmp_path: Path,
) -> None:
    source, _, first = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root", str(source))
    repo_id = record["managed_repo_id"]
    bare = _bare_path(service, repo_id)
    notes_ref = agent_pr_notes_ref_for_session("session-1")
    _run("git", "push", str(bare), f"{first}:refs/heads/agents/session-1/topic", cwd=source)
    _run("git", "notes", f"--ref={notes_ref}", "add", "-m", "pinned note", first, cwd=source)
    _run("git", "push", str(bare), f"{notes_ref}:{notes_ref}", cwd=source)
    _run("git", f"--git-dir={bare}", "update-ref", "refs/heads/review/session-1/topic", first)
    second = _commit(source, "README.md", "second\n", "second")

    service.refresh_repository(repo_id)

    assert _run("git", f"--git-dir={bare}", "rev-parse", "refs/heads/main") == second
    assert (
        _run("git", f"--git-dir={bare}", "rev-parse", "refs/heads/agents/session-1/topic") == first
    )
    assert (
        _run("git", f"--git-dir={bare}", "rev-parse", "refs/heads/review/session-1/topic") == first
    )
    assert (
        _run("git", f"--git-dir={bare}", "notes", f"--ref={notes_ref}", "show", first)
        == "pinned note"
    )
    assert (
        _run(
            "git",
            f"--git-dir={bare}",
            "for-each-ref",
            "--format=%(refname)",
            "refs/evander/refresh",
        )
        == ""
    )


def test_registration_rejects_reserved_upstream_branch_and_rolls_back(tmp_path: Path) -> None:
    source, _, _ = _source_repository(tmp_path)
    _run("git", "branch", "agents/upstream-owned", cwd=source)
    service = _service(tmp_path)

    with pytest.raises(ManagedGitConflictError, match="reserved branch"):
        service.register_repository("root", str(source))

    assert service.list_repositories() == []
    assert list((service.runtime_dir / "managed-git").glob("*.git")) == []


def test_promotion_pins_exact_sha_and_stale_event_cannot_regress_target(tmp_path: Path) -> None:
    source, _, first = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root", str(source))
    repo_id = record["managed_repo_id"]
    bare = _bare_path(service, repo_id)
    _run("git", "push", str(bare), f"{first}:refs/heads/agents/session-1/topic", cwd=source)

    promoted = service.promote_agent_branch(
        repo_id,
        "agents/session-1/topic",
        "review/session-1/topic",
        first,
        "root",
    )
    second = _commit(source, "feature.txt", "new\n", "feature update")
    _run(
        "git",
        "push",
        "--force",
        str(bare),
        f"{second}:refs/heads/agents/session-1/topic",
        cwd=source,
    )
    stale = service.promote_agent_branch(
        repo_id,
        "agents/session-1/topic",
        "review/session-1/topic",
        first,
        "root",
    )

    assert promoted["success"] is True
    assert stale["reason"] == "source_ref_moved"
    assert (
        _run("git", f"--git-dir={bare}", "rev-parse", "refs/heads/review/session-1/topic") == first
    )
    assert (
        _run(
            "git",
            f"--git-dir={bare}",
            "for-each-ref",
            "--format=%(refname)",
            "refs/evander/promotions",
        )
        == ""
    )


def test_promotion_reports_source_ref_moved_when_repush_wins_pin_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _, first = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root", str(source))
    repo_id = record["managed_repo_id"]
    bare = _bare_path(service, repo_id)
    source_ref = "refs/heads/agents/session-1/topic"
    _run("git", "push", str(bare), f"{first}:{source_ref}", cwd=source)
    second = _commit(source, "repush.txt", "new\n", "repush")
    _run("git", "push", str(bare), f"{second}:refs/heads/agents/session-1/staged", cwd=source)
    original_transaction = service._run_update_ref_transaction

    def race_pin(path: Path, commands: list[str]) -> None:
        if any("refs/evander/promotions/" in command for command in commands):
            _run("git", f"--git-dir={bare}", "update-ref", source_ref, second, first)
        original_transaction(path, commands)

    monkeypatch.setattr(service, "_run_update_ref_transaction", race_pin)

    result = service.promote_agent_branch(
        repo_id,
        "agents/session-1/topic",
        "review/session-1/topic",
        first,
        "root",
    )

    assert result["reason"] == "source_ref_moved"
    assert result["current_commit_sha"] == second
    assert (
        subprocess.run(
            [
                "git",
                f"--git-dir={bare}",
                "show-ref",
                "--verify",
                "--quiet",
                "refs/heads/review/session-1/topic",
            ],
            check=False,
        ).returncode
        == 1
    )


def test_pr_note_lookup_reads_only_the_exact_commit(tmp_path: Path) -> None:
    source, _, first = _source_repository(tmp_path)
    second = _commit(source, "next.txt", "next\n", "next")
    notes_ref = agent_pr_notes_ref_for_session("session-1")
    _run("git", "notes", f"--ref={notes_ref}", "add", "-m", "first only", first, cwd=source)
    service = _service(tmp_path)
    record = service.register_repository("root", str(source))
    bare = _bare_path(service, record["managed_repo_id"])
    _run("git", "push", str(bare), f"{notes_ref}:{notes_ref}", cwd=source)

    exact = service.read_pr_note(record["managed_repo_id"], first, notes_ref, "root")
    missing = service.read_pr_note(record["managed_repo_id"], second, notes_ref, "root")

    assert exact["body"] == "first only\n"
    assert missing == {
        "managed_repo_id": record["managed_repo_id"],
        "commit_sha": second,
        "notes_ref": notes_ref,
        "has_note": False,
        "body": None,
    }


def test_push_branch_publishes_exact_sha_with_destination_lease(tmp_path: Path) -> None:
    source, upstream, _ = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root", str(source))
    repo_id = record["managed_repo_id"]
    bare = _bare_path(service, repo_id)
    approved = _commit(source, "approved.txt", "approved\n", "approved")
    _run("git", f"--git-dir={bare}", "fetch", str(source), f"{approved}:refs/heads/review/topic")

    result = service.push_branch(repo_id, "review/topic", approved, "main", "root")

    assert result["success"] is True
    assert result["commit_sha"] == approved
    assert _run("git", f"--git-dir={upstream}", "rev-parse", "refs/heads/main") == approved
    assert (
        _run(
            "git",
            f"--git-dir={bare}",
            "for-each-ref",
            "--format=%(refname)",
            "refs/evander/publication",
        )
        == ""
    )


def test_push_branch_publishes_when_destination_matches_reviewed_base(tmp_path: Path) -> None:
    source, upstream, reviewed_base = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root", str(source))
    repo_id = record["managed_repo_id"]
    bare = _bare_path(service, repo_id)
    approved = _commit(source, "approved.txt", "approved\n", "approved")
    _run("git", f"--git-dir={bare}", "fetch", str(source), f"{approved}:refs/heads/review/topic")

    result = service.push_branch(
        repo_id,
        "review/topic",
        approved,
        "main",
        "root",
        expected_destination_commit_sha=reviewed_base,
    )

    assert result["success"] is True
    assert result["expected_destination_commit_sha"] == reviewed_base
    assert result["previous_commit_sha"] == reviewed_base
    assert _run("git", f"--git-dir={upstream}", "rev-parse", "refs/heads/main") == approved


def test_push_branch_rejects_destination_that_predates_reviewed_base(tmp_path: Path) -> None:
    source, upstream, canonical_commit = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root", str(source))
    repo_id = record["managed_repo_id"]
    bare = _bare_path(service, repo_id)
    reviewed_base = _commit(source, "preexisting.txt", "unreviewed\n", "preexisting feature work")
    approved = _commit(source, "allowed.txt", "allowed\n", "allowed release change")
    _run("git", f"--git-dir={bare}", "fetch", str(source), f"{approved}:refs/heads/review/topic")

    result = service.push_branch(
        repo_id,
        "review/topic",
        approved,
        "main",
        "root",
        expected_destination_commit_sha=reviewed_base,
    )

    assert result["success"] is False
    assert result["preflight_success"] is False
    assert result["reason"] == "destination_base_mismatch"
    assert result["expected_destination_commit_sha"] == reviewed_base
    assert result["destination_commit"] == canonical_commit
    assert _run("git", f"--git-dir={upstream}", "rev-parse", "refs/heads/main") == canonical_commit


def test_push_branch_rejects_missing_destination_when_base_is_required(tmp_path: Path) -> None:
    source, upstream, reviewed_base = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root", str(source))
    repo_id = record["managed_repo_id"]
    bare = _bare_path(service, repo_id)
    approved = _commit(source, "approved.txt", "approved\n", "approved")
    _run("git", f"--git-dir={bare}", "fetch", str(source), f"{approved}:refs/heads/review/topic")

    result = service.push_branch(
        repo_id,
        "review/topic",
        approved,
        "new-release",
        "root",
        expected_destination_commit_sha=reviewed_base,
    )

    assert result["success"] is False
    assert result["reason"] == "destination_base_mismatch"
    assert result["destination_commit"] is None
    assert _run("git", f"--git-dir={upstream}", "branch", "--list", "new-release") == ""


def test_push_branch_returns_merge_required_when_destination_is_ahead(tmp_path: Path) -> None:
    source, upstream, first = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root", str(source))
    repo_id = record["managed_repo_id"]
    bare = _bare_path(service, repo_id)
    approved = _commit(source, "approved.txt", "approved\n", "approved")
    _run("git", f"--git-dir={bare}", "fetch", str(source), f"{approved}:refs/heads/review/topic")
    _run("git", "checkout", "-b", "destination", first, cwd=source)
    destination = _commit(source, "destination.txt", "destination\n", "destination")
    _run("git", "push", "origin", "destination:main", cwd=source)

    result = service.push_branch(repo_id, "review/topic", approved, "main", "root")

    assert result["success"] is False
    assert result["preflight_success"] is False
    assert result["reason"] == "destination_branch_ahead"
    assert result["destination_branch"] == "main"
    assert result["destination_ahead_count"] == 1
    assert result["local_ahead_count"] == 1
    assert _run("git", f"--git-dir={upstream}", "rev-parse", "refs/heads/main") == destination


def test_push_reports_source_ref_moved_when_repush_wins_pin_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, upstream, first = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root", str(source))
    repo_id = record["managed_repo_id"]
    bare = _bare_path(service, repo_id)
    source_ref = "refs/heads/review/topic"
    _run("git", f"--git-dir={bare}", "update-ref", source_ref, first)
    second = _commit(source, "repush.txt", "new\n", "repush")
    _run("git", "push", str(bare), f"{second}:refs/heads/review/staged", cwd=source)
    original_transaction = service._run_update_ref_transaction

    def race_pin(path: Path, commands: list[str]) -> None:
        if any("refs/evander/publication/" in command for command in commands):
            _run("git", f"--git-dir={bare}", "update-ref", source_ref, second, first)
        original_transaction(path, commands)

    monkeypatch.setattr(service, "_run_update_ref_transaction", race_pin)

    result = service.push_branch(repo_id, source_ref, first, "main", "root")

    assert result["reason"] == "source_ref_moved"
    assert result["current_commit_sha"] == second
    assert _run("git", f"--git-dir={upstream}", "rev-parse", "refs/heads/main") == first


def test_temporary_worktree_is_detached_and_always_cleans_up(tmp_path: Path) -> None:
    source, _, first = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root", str(source))
    repo_id = record["managed_repo_id"]
    bare = _bare_path(service, repo_id)
    yielded_path: Path | None = None

    with pytest.raises(RuntimeError, match="provider failed"):
        with service.temporary_worktree(
            repo_id,
            commit_sha=first,
            operation_id="test-failure",
            purpose="test",
        ) as worktree:
            yielded_path = worktree
            assert _run("git", "branch", "--show-current", cwd=worktree) == ""
            manifest = json.loads((worktree.parent / "manifest.json").read_text(encoding="utf-8"))
            assert manifest["managed_repo_id"] == repo_id
            assert "path" not in json.dumps(manifest).casefold()
            raise RuntimeError("provider failed")

    assert yielded_path is not None and not yielded_path.exists()
    assert (
        _run(
            "git",
            f"--git-dir={bare}",
            "for-each-ref",
            "--format=%(refname)",
            "refs/evander/worktrees",
        )
        == ""
    )
    assert service.reconcile_stale_worktrees(repo_id, older_than_seconds=0)["removed"] == []


def test_active_worktree_can_be_cleaned_from_effect_worker_shutdown(
    tmp_path: Path,
) -> None:
    source, _, first = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root", str(source))
    repo_id = record["managed_repo_id"]
    bare = _bare_path(service, repo_id)
    ready = threading.Event()
    release = threading.Event()
    failures: list[BaseException] = []
    paths: list[Path] = []

    def hold_worktree() -> None:
        try:
            with service.temporary_worktree(
                repo_id,
                commit_sha=first,
                operation_id="effect-shutdown",
                purpose="test-effect-shutdown",
            ) as worktree:
                paths.append(worktree)
                ready.set()
                release.wait(timeout=10)
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    worker = threading.Thread(target=hold_worktree)
    worker.start()
    assert ready.wait(timeout=10)

    cleanup = service.cleanup_active_worktrees()
    release.set()
    worker.join(timeout=10)

    assert cleanup == {"cleaned": 1, "failed": 0}
    assert not worker.is_alive()
    assert failures == []
    assert paths and not paths[0].exists()
    assert (
        _run(
            "git",
            f"--git-dir={bare}",
            "for-each-ref",
            "--format=%(refname)",
            "refs/evander/worktrees",
        )
        == ""
    )


def test_consolidation_merges_pinned_sources_in_managed_worktree(tmp_path: Path) -> None:
    source, _, base_commit = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root", str(source))
    repo_id = record["managed_repo_id"]
    bare = _bare_path(service, repo_id)
    service.set_host_commit_identity(
        repo_id,
        directory_path="root",
        name="Jonathan Quinth",
        email="operator@example.invalid",
    )
    feature_a = tmp_path / "feature-a.txt"
    _run("git", "checkout", "-b", "agents/session-1/a", cwd=source)
    first_feature = _commit(source, feature_a.name, "a\n", "feature a")
    _run("git", "push", str(bare), "agents/session-1/a", cwd=source)
    _run("git", "checkout", "main", cwd=source)
    _run("git", "checkout", "-b", "agents/session-2/b", cwd=source)
    second_feature = _commit(source, "feature-b.txt", "b\n", "feature b")
    _run("git", "push", str(bare), "agents/session-2/b", cwd=source)
    _run("git", "checkout", "main", cwd=source)

    result = service.consolidate_branches(
        repo_id,
        ["agents/session-1/a", "agents/session-2/b"],
        "team/releases/release-1",
        directory_path="root",
        base_branch="main",
        required_source_ancestor_commit_sha=base_commit,
        expected_source_commits={
            "agents/session-1/a": first_feature,
            "agents/session-2/b": second_feature,
        },
        allowed_changed_paths=["feature-a.txt", "feature-b.txt"],
    )

    assert result["success"] is True
    assert result["merged_branches"] == [
        "agents/session-1/a",
        "agents/session-2/b",
    ]
    assert result["base_commit_sha"] == base_commit
    assert result["tree_hash"] == _run(
        "git", f"--git-dir={bare}", "rev-parse", f"{result['commit_sha']}^{{tree}}"
    )
    assert result["source_manifests"] == {
        "agents/session-1/a": {
            "base_commit_sha": base_commit,
            "reviewed_commit_sha": first_feature,
            "tree_hash": _run("git", f"--git-dir={bare}", "rev-parse", f"{first_feature}^{{tree}}"),
            "commit_shas": [first_feature],
            "changed_paths": ["A\tfeature-a.txt"],
        },
        "agents/session-2/b": {
            "base_commit_sha": base_commit,
            "reviewed_commit_sha": second_feature,
            "tree_hash": _run(
                "git", f"--git-dir={bare}", "rev-parse", f"{second_feature}^{{tree}}"
            ),
            "commit_shas": [second_feature],
            "changed_paths": ["A\tfeature-b.txt"],
        },
    }
    assert (
        _run("git", f"--git-dir={bare}", "rev-parse", "refs/heads/team/releases/release-1")
        == result["commit_sha"]
    )
    assert (
        _run(
            "git",
            f"--git-dir={bare}",
            "show",
            "-s",
            "--format=%an <%ae>|%cn <%ce>",
            result["commit_sha"],
        )
        == (
            "Jonathan Quinth <operator@example.invalid>"
            "|Jonathan Quinth <operator@example.invalid>"
        )
    )
    assert (
        _run(
            "git",
            f"--git-dir={bare}",
            "for-each-ref",
            "--format=%(refname)",
            "refs/evander/consolidations",
        )
        == ""
    )


def test_consolidation_rejects_source_outside_required_ancestor(
    tmp_path: Path,
) -> None:
    source, _, base_commit = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root", str(source))
    repo_id = record["managed_repo_id"]
    bare = _bare_path(service, repo_id)
    base_tree = _run("git", "rev-parse", f"{base_commit}^{{tree}}", cwd=source)
    unrelated_commit = _run(
        "git",
        "commit-tree",
        base_tree,
        "-m",
        "unrelated correction source",
        cwd=source,
    )
    _run(
        "git",
        "update-ref",
        "refs/heads/agents/session-1/unrelated",
        unrelated_commit,
        cwd=source,
    )
    _run(
        "git",
        "push",
        str(bare),
        "agents/session-1/unrelated",
        cwd=source,
    )

    result = service.consolidate_branches(
        repo_id,
        ["agents/session-1/unrelated"],
        "team/corrections/release-1",
        directory_path="root",
        base_commit_sha=base_commit,
        required_source_ancestor_commit_sha=base_commit,
        expected_source_commits={
            "agents/session-1/unrelated": unrelated_commit,
        },
    )

    assert result["success"] is False
    assert result["error_code"] == "scope_mismatch"
    assert result["non_descendant_source_branches"] == [
        "agents/session-1/unrelated"
    ]
    assert result["required_source_ancestor_commit_sha"] == base_commit


def test_reconcile_removes_only_lock_free_managed_stale_worktrees(tmp_path: Path) -> None:
    source, _, first = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root", str(source))
    repo_id = record["managed_repo_id"]
    bare = _bare_path(service, repo_id)
    operation_id = "stale-op"
    operation_ref = f"refs/evander/worktrees/{operation_id}"
    operation_dir = service.runtime_dir / "git-worktrees" / repo_id / operation_id
    checkout = operation_dir / "checkout"
    operation_dir.mkdir(parents=True, mode=0o700)
    _run("git", f"--git-dir={bare}", "update-ref", operation_ref, first)
    _run("git", f"--git-dir={bare}", "worktree", "add", "--detach", str(checkout), operation_ref)
    stale_time = datetime.now(UTC) - timedelta(hours=2)
    (operation_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "evander.managed-git-worktree/v1",
                "managed_repo_id": repo_id,
                "operation_id": operation_id,
                "operation_ref": operation_ref,
                "commit_sha": first,
                "source_ref": None,
                "purpose": "recovery test",
                "created_at": stale_time.isoformat(),
            }
        ),
        encoding="utf-8",
    )

    result = service.reconcile_stale_worktrees(repo_id)

    assert result["removed"] == [{"managed_repo_id": repo_id, "operation_id": operation_id}]
    assert not operation_dir.exists()
    assert (
        _run("git", f"--git-dir={bare}", "for-each-ref", "--format=%(refname)", operation_ref) == ""
    )


def test_reconcile_removes_lock_free_stale_worktree_left_before_manifest(
    tmp_path: Path,
) -> None:
    source, _, first = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root", str(source))
    repo_id = record["managed_repo_id"]
    bare = _bare_path(service, repo_id)
    operation_id = "unmanifested-op"
    operation_ref = f"refs/evander/worktrees/{operation_id}"
    operation_dir = service.runtime_dir / "git-worktrees" / repo_id / operation_id
    checkout = operation_dir / "checkout"
    operation_dir.mkdir(parents=True, mode=0o700)
    _run("git", f"--git-dir={bare}", "update-ref", operation_ref, first)
    _run("git", f"--git-dir={bare}", "worktree", "add", "--detach", str(checkout), operation_ref)
    stale_timestamp = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    os.utime(operation_dir, (stale_timestamp, stale_timestamp))

    result = service.reconcile_stale_worktrees(repo_id)

    assert result["removed"] == [{"managed_repo_id": repo_id, "operation_id": operation_id}]
    assert result["invalid"][0]["operation_id"] == operation_id
    assert not operation_dir.exists()
    assert (
        _run("git", f"--git-dir={bare}", "for-each-ref", "--format=%(refname)", operation_ref) == ""
    )


def test_delete_refuses_repository_with_active_worktree(tmp_path: Path) -> None:
    source, _, first = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root", str(source))
    repo_id = record["managed_repo_id"]

    with service.temporary_worktree(repo_id, commit_sha=first, operation_id="active-delete"):
        with pytest.raises(ManagedGitConflictError, match="worktrees"):
            service.delete_repository(repo_id)

    assert service.delete_repository(repo_id)["deleted"] is True


def test_delete_requires_bindings_to_be_removed(tmp_path: Path) -> None:
    source, _, _ = _source_repository(tmp_path)
    service = _service(tmp_path)
    record = service.register_repository("root", str(source))
    repo_id = record["managed_repo_id"]
    service.bind(repo_id, "issue", "123")
    service.bind(repo_id, "sessions", "session-1")

    with pytest.raises(ManagedGitConflictError, match="active bindings"):
        service.delete_repository(repo_id)

    assert service.unbind_all("sessions", "session-1") == [repo_id]
    service.unbind(repo_id, "issue", "123")
    assert service.delete_repository(repo_id) == {
        "success": True,
        "managed_repo_id": repo_id,
        "deleted": True,
    }
    assert service.list_repositories() == []


def test_migration_dry_run_from_empty_runtime_does_not_create_managed_state(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    assert not runtime_dir.exists()
    service = _service(tmp_path)

    result = service.migrate("--dry-run")

    assert result["stage"] == "dry-run"
    assert isinstance(result["can_apply"], bool)
    assert not (runtime_dir / "managed-git-repositories.json").exists()
    assert not (runtime_dir / "managed-git").exists()
    assert not (runtime_dir / "git-worktrees").exists()
    assert not (runtime_dir / "managed-git-locks").exists()
    assert not (runtime_dir / "managed-git-migrations").exists()


def test_migration_apply_imports_preserved_refs_and_finalize_deletes_only_rollback_artifact(
    tmp_path: Path,
) -> None:
    source, upstream, _ = _source_repository(tmp_path)
    legacy = _legacy_bare(
        tmp_path,
        name="legacy",
        source=source,
        canonical_source=upstream,
    )
    _run("git", "checkout", "-b", "agents/session-1/topic", cwd=source)
    agent_commit = _commit(source, "agent.txt", "agent\n", "agent result")
    _run(
        "git",
        "push",
        str(legacy),
        "agents/session-1/topic:refs/heads/agents/session-1/topic",
        cwd=source,
    )
    service = _isolated_service(tmp_path)

    dry_run = service.migrate("dry-run")
    applied = service.migrate("apply")
    repeated = service.migrate("apply")

    assert dry_run["can_apply"] is True
    assert applied["managed_repo_ids"]
    assert applied["migration_id"] == f"mgm_{applied['verification_hash'][:20]}"
    assert repeated["migration_id"] == applied["migration_id"]
    assert repeated["restartable"] is True
    repo_id = applied["managed_repo_ids"][0]
    managed_bare = _bare_path(service, repo_id)
    assert (
        _run(
            "git",
            f"--git-dir={managed_bare}",
            "rev-parse",
            "refs/heads/agents/session-1/topic",
        )
        == agent_commit
    )
    assert legacy.exists()

    finalized = service.migrate("finalize", migration_id=applied["migration_id"])

    assert finalized["status"] == "finalized"
    assert not legacy.exists()
    assert managed_bare.exists()


def test_migration_dry_run_detects_preserved_ref_collisions(tmp_path: Path) -> None:
    source, upstream, _ = _source_repository(tmp_path)
    first_legacy = _legacy_bare(
        tmp_path,
        name="legacy-one",
        source=source,
        canonical_source=upstream,
    )
    second_legacy = _legacy_bare(
        tmp_path,
        name="legacy-two",
        source=source,
        canonical_source=upstream,
    )
    _run("git", "checkout", "-b", "agents/session-1/topic", cwd=source)
    first = _commit(source, "agent.txt", "first\n", "first agent result")
    _run(
        "git",
        "push",
        str(first_legacy),
        "agents/session-1/topic:refs/heads/agents/session-1/topic",
        cwd=source,
    )
    second = _commit(source, "agent.txt", "second\n", "second agent result")
    _run(
        "git",
        "push",
        str(second_legacy),
        "agents/session-1/topic:refs/heads/agents/session-1/topic",
        cwd=source,
    )
    service = _isolated_service(tmp_path)

    result = service.migrate("dry-run")

    assert result["can_apply"] is False
    assert "preserved refs collide" in " ".join(result["blockers"])
    assert result["inventory"]["preserved_ref_collisions"] == [
        {
            "directory_path": "root",
            "canonical_repository_identity": f"local:{upstream}",
            "ref_name": "refs/heads/agents/session-1/topic",
            "object_ids": sorted([first, second]),
        }
    ]
