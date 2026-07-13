from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace

import pytest

from evander.admin.services.managed_git_service import (
    ManagedGitAdminService,
    ManagedGitConflictError,
    REGISTRY_SCHEMA,
)
from evander.git_pr_notes import agent_pr_notes_ref_for_session


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
    responses = iter(
        [{"value": [pr_item]}]
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
    ) -> dict[str, object]:
        del url, auth, payload
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
            "for-each-ref",
            "--format=%(refname)",
            "refs/evander/consolidations",
        )
        == ""
    )


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
