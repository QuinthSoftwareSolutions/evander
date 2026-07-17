from __future__ import annotations

from datetime import UTC, datetime, timedelta
import fcntl
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from evander.admin.services.managed_git_service import (
    ManagedGitAdminService,
    ManagedGitConflictError,
)


def _run(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        list(args),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _legacy_repository(
    tmp_path: Path,
    *,
    legacy_root: Path | None = None,
    with_directory: bool = True,
) -> tuple[Path, Path, Path]:
    upstream = tmp_path / "upstream.git"
    source = tmp_path / "source"
    legacy = (legacy_root or (tmp_path / "git-remotes")) / "project.git"
    _run("git", "init", "--bare", str(upstream))
    _run("git", "init", "-b", "main", str(source))
    _run("git", "config", "user.name", "Migration Safety", cwd=source)
    _run("git", "config", "user.email", "migration@example.invalid", cwd=source)
    (source / "README.md").write_text("initial\n", encoding="utf-8")
    _run("git", "add", "README.md", cwd=source)
    _run("git", "commit", "-m", "initial", cwd=source)
    _run("git", "remote", "add", "origin", str(upstream), cwd=source)
    _run("git", "push", "-u", "origin", "main", cwd=source)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    _run("git", "clone", "--bare", str(source), str(legacy))
    _run(
        "git",
        f"--git-dir={legacy}",
        "config",
        "evander.canonicalOriginUrl",
        str(upstream),
    )
    _run(
        "git",
        f"--git-dir={legacy}",
        "config",
        "evander.trustedRepoPath",
        str(source),
    )
    if with_directory:
        _run(
            "git",
            f"--git-dir={legacy}",
            "config",
            "evander.directoryPath",
            "root",
        )
    return source, upstream, legacy


def _service(tmp_path: Path) -> ManagedGitAdminService:
    return ManagedGitAdminService(
        SimpleNamespace(runtime_dir=tmp_path / "runtime"),
        SimpleNamespace(),
        tmp_path,
    )


def test_migration_requires_explicit_legacy_directory_ownership(tmp_path: Path) -> None:
    _legacy_repository(tmp_path, with_directory=False)

    dry_run = _service(tmp_path).migrate("dry-run")

    assert dry_run["can_apply"] is False
    assert "owning-directory metadata" in " ".join(dry_run["blockers"])
    assert dry_run["inventory"]["bare_repositories"][0]["has_directory_metadata"] is False


def test_preflight_does_not_scan_new_issue_setup_git_user_as_legacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue_setup = tmp_path / "runtime" / "directory-config" / "root" / "issue-setup.json"
    issue_setup.parent.mkdir(parents=True)
    issue_setup.write_text(json.dumps({"git_user": "operator"}), encoding="utf-8")
    monkeypatch.setattr(
        "evander.admin.services.managed_git_service.pwd.getpwnam",
        lambda _user: (_ for _ in ()).throw(AssertionError("unexpected user lookup")),
    )

    service = _service(tmp_path)

    assert service._legacy_state_inventory()["authorized_key_bindings"] == []
    assert service._legacy_managed_git_artifacts_present() is False


def test_migration_refuses_custom_legacy_root_as_automatic_rollback_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    custom_root = tmp_path / "operator-owned-git"
    _legacy_repository(tmp_path, legacy_root=custom_root)
    monkeypatch.setenv("EVANDER_GIT_ROOT", str(custom_root))

    dry_run = _service(tmp_path).migrate("dry-run")

    assert dry_run["can_apply"] is False
    assert dry_run["inventory"]["custom_legacy_git_root"] == str(custom_root)
    assert "outside Evander-owned rollback roots" in " ".join(dry_run["blockers"])


def test_migration_refreshes_from_trusted_checkout_but_keeps_canonical_identity(
    tmp_path: Path,
) -> None:
    source, upstream, _legacy = _legacy_repository(tmp_path)
    (source / "README.md").write_text("trusted checkout is newer\n", encoding="utf-8")
    _run("git", "add", "README.md", cwd=source)
    trusted_commit = _run("git", "commit", "-m", "trusted update", cwd=source)
    trusted_commit = _run("git", "rev-parse", "HEAD", cwd=source)

    service = _service(tmp_path)
    applied = service.migrate("apply")
    repository = service.get_repository(applied["managed_repo_ids"][0])
    internal = json.loads(
        (service.runtime_dir / "managed-git-repositories.json").read_text(encoding="utf-8")
    )["repositories"][repository["managed_repo_id"]]

    assert repository["canonical_repository_url"] == str(upstream)
    assert internal["refresh_source"] == str(source)
    assert (
        _run(
            "git",
            f"--git-dir={internal['internal_bare_path']}",
            "rev-parse",
            "refs/heads/main",
        )
        == trusted_commit
    )


def test_migration_blocks_running_but_not_stopped_unexpired_sessions(
    tmp_path: Path,
) -> None:
    _legacy_repository(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "sessions.json").write_text(
        json.dumps(
            {
                "vm-session": {
                    "session_id": "vm-session",
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "metadata": {},
                },
                "container-session": {
                    "session_id": "container-session",
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "metadata": {"execution_backend": "container_worker_vm"},
                },
            }
        ),
        encoding="utf-8",
    )
    service = _service(tmp_path)
    service.set_agent_inventory_admin(
        SimpleNamespace(
            list_agents=lambda **_kwargs: [
                SimpleNamespace(session_id="vm-session", status="stopped"),
                SimpleNamespace(session_id="container-session", status="idle"),
            ]
        )
    )

    assert service.migrate("dry-run")["can_apply"] is True

    service.set_agent_inventory_admin(
        SimpleNamespace(
            list_agents=lambda **_kwargs: [
                SimpleNamespace(session_id="vm-session", status="running"),
                SimpleNamespace(session_id="container-session", status="stopped"),
            ]
        )
    )
    dry_run = service.migrate("dry-run")
    assert dry_run["can_apply"] is False
    assert dry_run["inventory"]["active_session_ids"] == ["vm-session"]


def test_new_managed_workflow_packs_do_not_self_trigger_legacy_migration() -> None:
    for path in (
        Path("config/workflow-packs/agent-default.v1-example.json"),
        Path("config/workflow-packs/agent-container-worker.v1-example.json"),
    ):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert ManagedGitAdminService._workflow_legacy_field_paths(payload) == []


def test_migration_manifest_never_persists_credentialed_legacy_alias(
    tmp_path: Path,
) -> None:
    _source, _upstream, legacy = _legacy_repository(tmp_path)
    credentialed = (
        "https://legacy-user:super-secret@example.invalid/org/repo.git?token=hidden#fragment"
    )
    _run(
        "git",
        f"--git-dir={legacy}",
        "config",
        "evander.mirrorSource",
        credentialed,
    )
    service = _service(tmp_path)

    applied = service.migrate("apply")
    manifest = (
        service.runtime_dir / "managed-git-migrations" / f"{applied['migration_id']}.json"
    ).read_text(encoding="utf-8")

    assert "super-secret" not in manifest
    assert "legacy-user" not in manifest
    assert "token=hidden" not in manifest
    assert "#fragment" not in manifest


def test_migration_does_not_fallback_from_unmatched_explicit_repository(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    records = [
        {
            "managed_repo_id": "mr_0123456789abcdef0123",
            "directory_path": "root",
            "legacy_repository": str(tmp_path / "git-remotes" / "expected.git"),
            "legacy_name": "expected.git",
            "canonical_repository_identity": "local:/expected",
            "aliases": [str(tmp_path / "git-remotes" / "expected.git")],
        }
    ]

    with pytest.raises(ManagedGitConflictError, match="no matching repository"):
        service._resolve_migration_repository(
            [str(tmp_path / "operator" / "different.git")],
            directory_path="root",
            mapping_records=records,
            field_context="test binding",
        )


def test_migration_conflict_details_strip_url_credentials_and_query(tmp_path: Path) -> None:
    service = _service(tmp_path)
    credentialed = (
        "https://legacy-user:super-secret@example.invalid/org/repo.git?token=hidden#fragment"
    )
    records = [
        {
            "managed_repo_id": "mr_0123456789abcdef0123",
            "directory_path": "root",
            "legacy_repository": str(tmp_path / "git-remotes" / "expected.git"),
            "legacy_name": "expected.git",
            "canonical_repository_identity": "local:/expected",
            "aliases": [str(tmp_path / "git-remotes" / "expected.git")],
        }
    ]

    with pytest.raises(ManagedGitConflictError) as exc_info:
        service._resolve_migration_repository(
            [credentialed],
            directory_path="root",
            mapping_records=records,
            field_context="test binding",
        )

    serialized = json.dumps(exc_info.value.details)
    assert "legacy-user" not in serialized
    assert "super-secret" not in serialized
    assert "token=hidden" not in serialized
    assert "fragment" not in serialized
    assert "https://example.invalid/org/repo.git" in serialized


def test_migration_detects_and_holds_manual_worker_locks(tmp_path: Path) -> None:
    _legacy_repository(tmp_path)
    service = _service(tmp_path)
    worker_lock = service.runtime_dir / "trigger-orchestrator.lock"
    worker_lock.parent.mkdir(parents=True, exist_ok=True)

    with worker_lock.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        dry_run = service.migrate("dry-run")
        assert dry_run["can_apply"] is False
        assert dry_run["inventory"]["worker_lock_states"]["trigger-orchestrator"] == "active"
        with pytest.raises(ManagedGitConflictError, match="workers to be stopped"):
            service.migrate("apply")
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    applied = service.migrate("apply")
    assert applied["status"] == "applied"


def test_migration_retry_repairs_rollback_artifact_permissions(tmp_path: Path) -> None:
    _source, _upstream, legacy = _legacy_repository(tmp_path)
    service = _service(tmp_path)

    applied = service.migrate("apply")
    legacy.chmod(legacy.stat().st_mode | 0o200)
    assert legacy.stat().st_mode & 0o200

    repeated = service.migrate("apply")

    assert repeated["migration_id"] == applied["migration_id"]
    assert legacy.stat().st_mode & 0o222 == 0


def test_migration_does_not_mark_applied_when_read_only_enforcement_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _legacy_repository(tmp_path)
    service = _service(tmp_path)

    with monkeypatch.context() as patch:
        patch.setattr(
            service,
            "_make_tree_read_only",
            lambda _path: (_ for _ in ()).throw(ManagedGitConflictError("simulated chmod failure")),
        )
        with pytest.raises(ManagedGitConflictError, match="simulated chmod failure"):
            service.migrate("apply")

    manifest_path = next((service.runtime_dir / "managed-git-migrations").glob("*.json"))
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "prepared"

    recovered = service.migrate("apply")
    assert recovered["status"] == "applied"
    assert recovered["recovered"] is True


def test_migration_refuses_ambiguous_legacy_repository_glob(tmp_path: Path) -> None:
    service = _service(tmp_path)
    records = [
        {
            "managed_repo_id": "mr_11111111111111111111",
            "directory_path": "root",
            "legacy_repository": str(tmp_path / "git-remotes" / "one.git"),
            "legacy_name": "one.git",
        },
        {
            "managed_repo_id": "mr_22222222222222222222",
            "directory_path": "root",
            "legacy_repository": str(tmp_path / "git-remotes" / "two.git"),
            "legacy_name": "two.git",
        },
    ]

    with pytest.raises(ManagedGitConflictError, match="exactly one repository"):
        service._managed_id_for_legacy_glob(
            "*.git",
            directory_path="root",
            mapping_records=records,
            field_context="test trigger",
        )


def test_migration_matches_exact_suffixless_legacy_repository_name_and_glob(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    legacy_path = tmp_path / "git-remotes" / "project.git"
    repo_id = "mr_11111111111111111111"
    records = service._migration_mapping_records(
        [
            {
                "path": str(legacy_path),
                "name": "project.git",
                "directory_path": "root",
            }
        ],
        [{"legacy_repository": str(legacy_path), "managed_repo_id": repo_id}],
    )

    assert (
        service._resolve_migration_repository(
            ["project"],
            directory_path="root",
            mapping_records=records,
            field_context="test binding",
        )
        == repo_id
    )
    assert (
        service._managed_id_for_legacy_glob(
            "project",
            directory_path="root",
            mapping_records=records,
            field_context="test trigger",
        )
        == repo_id
    )


def test_suffixless_legacy_repository_alias_refuses_ambiguous_variants(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    first_path = tmp_path / "git-remotes" / "one" / "project.git"
    second_path = tmp_path / "git-remotes" / "two" / "project"
    first_id = "mr_11111111111111111111"
    second_id = "mr_22222222222222222222"
    records = service._migration_mapping_records(
        [
            {
                "path": str(first_path),
                "name": "project.git",
                "directory_path": "root",
            },
            {
                "path": str(second_path),
                "name": "project",
                "directory_path": "root",
            },
        ],
        [
            {"legacy_repository": str(first_path), "managed_repo_id": first_id},
            {"legacy_repository": str(second_path), "managed_repo_id": second_id},
        ],
    )

    with pytest.raises(ManagedGitConflictError, match="multiple matching repositories"):
        service._resolve_migration_repository(
            ["project"],
            directory_path="root",
            mapping_records=records,
            field_context="test binding",
        )
    with pytest.raises(ManagedGitConflictError, match="exactly one repository"):
        service._managed_id_for_legacy_glob(
            "project",
            directory_path="root",
            mapping_records=records,
            field_context="test trigger",
        )
