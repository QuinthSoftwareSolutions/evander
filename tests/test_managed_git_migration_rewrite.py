from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from evander.admin.payloads import AdminRevisionConflict
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


def _repositories(
    tmp_path: Path,
    *,
    name: str = "project",
    directory_path: str = "root",
) -> tuple[Path, Path, Path]:
    suffix = "" if name == "project" else f"-{name}"
    upstream = tmp_path / f"upstream{suffix}.git"
    source = tmp_path / f"source{suffix}"
    legacy = tmp_path / "git-remotes" / f"{name}.git"
    _run("git", "init", "--bare", str(upstream))
    _run("git", "init", "-b", "main", str(source))
    _run("git", "config", "user.name", "Migration Test", cwd=source)
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
    _run(
        "git",
        f"--git-dir={legacy}",
        "config",
        "evander.directoryPath",
        directory_path,
    )
    return source, upstream, legacy


def _service(tmp_path: Path) -> ManagedGitAdminService:
    return ManagedGitAdminService(
        SimpleNamespace(runtime_dir=tmp_path / "runtime"),
        SimpleNamespace(),
        tmp_path,
    )


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _b64(value: Path | str) -> str:
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


class _FakeWorkflowConfigAdmin:
    def __init__(self, path: Path, config: dict[str, object]) -> None:
        self.path = path.resolve(strict=False)
        self.config = json.loads(json.dumps(config))
        self.content = self.path.read_bytes()
        self.revision = hashlib.sha256(self.content).hexdigest()
        self.raise_revision_conflict = False
        self.save_calls: list[dict[str, object]] = []

    def read_worker_global_config(self, worker_id: str) -> SimpleNamespace:
        assert worker_id == "trigger-orchestrator"
        return SimpleNamespace(config={"workflow_packs": ["live.json"]})

    def read_workflow_pack_for_migration(self, *, workflow_pack: str) -> SimpleNamespace:
        assert workflow_pack == "live.json"
        return SimpleNamespace(
            content=self.content,
            revision=self.revision,
            path=str(self.path),
            reference="live.json",
        )

    def rewrite_workflow_pack_for_migration(self, **kwargs: object) -> SimpleNamespace:
        self.save_calls.append(dict(kwargs))
        if self.raise_revision_conflict:
            raise AdminRevisionConflict(
                "changed",
                details={"current_revision": "concurrent-revision"},
            )
        assert kwargs["expected_revision"] == self.revision
        content = kwargs["content"]
        assert isinstance(content, bytes)
        self.content = content
        self.config = json.loads(content)
        self.path.write_bytes(content)
        self.revision = hashlib.sha256(content).hexdigest()
        return SimpleNamespace(revision=self.revision)


def test_apply_rewrites_all_legacy_state_and_records_bindings(tmp_path: Path) -> None:
    source, _upstream, legacy = _repositories(tmp_path)
    runtime = tmp_path / "runtime"
    authorized_keys = tmp_path / "authorized_keys"
    authorized_keys.write_text(
        'command="'
        f"EVANDER_GIT_ROOT_B64={_b64(legacy.parent)} "
        "EVANDER_GIT_AUTH_ACCESS=write "
        f"EVANDER_GIT_AUTH_REPO_B64={_b64(legacy)} wrapper"  # noqa: S608
        '" ssh-ed25519 AAAAC3NzaMigrationKey session\n',
        encoding="utf-8",
    )
    _write_json(
        runtime / "local-issues.json",
        {
            "root:local:7": {
                "issue_id": "root:local:7",
                "issue_number": "7",
                "directory_path": "root",
                "repo_source": str(source),
            }
        },
    )
    _write_json(
        runtime / "sessions.json",
        {
            "session-7": {
                "session_id": "session-7",
                "directory_path": "root",
                "expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                "metadata": {
                    "execution_backend": "container_worker_vm",
                    "container_git_write_access": {
                        "repo_source": str(source),
                        "bare_repo_path": str(legacy),
                        "authorized_repo_path": str(legacy),
                        "authorized_keys_path": str(authorized_keys),
                    },
                },
            }
        },
    )
    _write_json(
        runtime / "addons" / "team-addon" / "state.json",
        {
            "projects": {
                "project-7": {
                    "id": "project-7",
                    "git": {
                        "host_repo_path": str(source),
                        "bare_repo_name": legacy.name,
                        "canonical_origin_url": str(source),
                        "provider": "local",
                        "base_branch": "main",
                        "canonical_release_branch_prefix": "releases/<project-id>/",
                    },
                }
            },
            "git_promotions": {
                "promotion-7": {
                    "outputs": {"host_repo_path": str(source)},
                }
            },
        },
    )
    _write_json(
        runtime / "workflow-packs" / "legacy.json",
        {
            "nodes": [
                {
                    "id": "clone",
                    "type": "effect:agent:clone-repo",
                    "repo_source": str(source),
                    "host_repo_path": str(source),
                }
            ]
        },
    )
    issue_setup_path = runtime / "directory-config" / "root" / "issue-setup.json"
    _write_json(
        issue_setup_path,
        {"git_root": str(legacy.parent), "git_host": "192.0.2.10", "git_user": "git"},
    )
    service = _service(tmp_path)

    dry_run = service.migrate("dry-run")
    assert dry_run["can_apply"] is True
    assert dry_run["inventory"]["team_projects_with_legacy_git_fields"]
    assert dry_run["inventory"]["session_bindings_with_legacy_git_fields"]
    assert dry_run["inventory"]["issue_bindings_with_legacy_git_fields"]
    assert dry_run["inventory"]["authorized_key_bindings"]
    assert dry_run["inventory"]["legacy_configuration_files"]

    applied = service.migrate("apply")
    repo_id = applied["managed_repo_ids"][0]

    issue = json.loads((runtime / "local-issues.json").read_text())["root:local:7"]
    assert issue["managed_repo_id"] == repo_id
    assert "repo_source" not in issue
    session = json.loads((runtime / "sessions.json").read_text())["session-7"]
    metadata = session["metadata"]
    assert metadata["managed_repo_id"] == repo_id
    assert metadata["managed_git_remote_reconciliation_required"] is True
    assert "container_git_write_access" not in metadata
    migrated_grant = metadata["managed_git_access_grants"][repo_id]
    assert migrated_grant["managed_repo_id"] == repo_id
    assert migrated_grant["lifetime"] == "session"
    assert "bare_repo_path" not in migrated_grant
    assert "authorized_repo_path" not in migrated_grant
    team_git = json.loads((runtime / "addons" / "team-addon" / "state.json").read_text())[
        "projects"
    ]["project-7"]["git"]
    assert team_git == {
        "base_branch": "main",
        "canonical_release_branch_prefix": "releases/<project-id>/",
        "managed_repo_id": repo_id,
    }
    promotion_output = json.loads((runtime / "addons" / "team-addon" / "state.json").read_text())[
        "git_promotions"
    ]["promotion-7"]["outputs"]
    assert promotion_output == {"host_repo_path": str(source)}
    workflow_node = json.loads((runtime / "workflow-packs" / "legacy.json").read_text())["nodes"][0]
    assert workflow_node["managed_repo_id"] == repo_id
    assert "repo_source" not in workflow_node
    assert "host_repo_path" not in workflow_node
    assert "git_root" not in json.loads(issue_setup_path.read_text(encoding="utf-8"))

    registry = json.loads((runtime / "managed-git-repositories.json").read_text(encoding="utf-8"))
    bindings = {
        (item["kind"], item["binding_id"]) for item in registry["repositories"][repo_id]["bindings"]
    }
    assert {
        ("issues", "root:local:7"),
        ("sessions", "session-7"),
        ("authorized_sessions", "session-7"),
        ("team_projects", "project-7"),
    } <= bindings
    auth_text = authorized_keys.read_text(encoding="utf-8")
    assert _b64(legacy) not in auth_text
    assert _b64(runtime / "managed-git" / f"{repo_id}.git") in auth_text

    repeated = service.migrate("apply")
    assert repeated["migration_id"] == applied["migration_id"]
    assert repeated["restartable"] is True
    service.unbind(repo_id, kind="team_projects", binding_id="project-7")
    assert "project-7" not in service.get_repository(repo_id)["bindings_by_kind"].get(
        "team_projects", []
    )


@pytest.mark.parametrize("prefix_scoped", [False, True])
def test_apply_rewrites_directory_owned_authorized_key(
    tmp_path: Path,
    prefix_scoped: bool,
) -> None:
    source, _upstream, legacy = _repositories(
        tmp_path,
        directory_path="root/team-a",
    )
    authorized_keys = tmp_path / "authorized_keys"
    assignment_name = (
        "EVANDER_GIT_AUTH_REPO_PREFIX_B64" if prefix_scoped else "EVANDER_GIT_AUTH_REPO_B64"
    )
    assignment_value = legacy.parent if prefix_scoped else legacy
    authorized_keys.write_text(
        'command="'
        f"EVANDER_GIT_ROOT_B64={_b64(legacy.parent)} "
        "EVANDER_GIT_AUTH_ACCESS=write "
        f"{assignment_name}={_b64(assignment_value)} "
        f"EVANDER_GIT_AUTH_SESSION_ID_B64={_b64('session-team-a')} wrapper"
        '" ssh-ed25519 AAAAC3NzaTeamA session-team-a\n',
        encoding="utf-8",
    )
    _write_json(
        tmp_path / "runtime" / "sessions.json",
        {
            "session-team-a": {
                "session_id": "session-team-a",
                "directory_path": "root/team-a",
                "expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                "metadata": {
                    "container_git_write_access": {
                        "repo_source": str(source),
                        "bare_repo_path": str(legacy),
                        "authorized_repo_path": str(legacy),
                        "authorized_keys_path": str(authorized_keys),
                    }
                },
            }
        },
    )
    service = _service(tmp_path)

    assert service.migrate("dry-run")["can_apply"] is True
    applied = service.migrate("apply")
    repo_id = applied["managed_repo_ids"][0]

    auth_text = authorized_keys.read_text(encoding="utf-8")
    assert "EVANDER_GIT_AUTH_REPO_PREFIX_B64=" not in auth_text
    assert "EVANDER_GIT_REFRESH_REQUIRED=1" in auth_text
    assert _b64(tmp_path / "runtime" / "managed-git" / f"{repo_id}.git") in auth_text
    assert service.get_repository(repo_id)["bindings_by_kind"]["authorized_sessions"] == [
        "session-team-a"
    ]


def test_apply_correlates_each_authorized_key_with_its_exact_session(
    tmp_path: Path,
) -> None:
    first_source, first_upstream, first_legacy = _repositories(
        tmp_path,
        name="first",
    )
    second_source, second_upstream, second_legacy = _repositories(
        tmp_path,
        name="second",
    )
    authorized_keys = tmp_path / "authorized_keys"
    authorized_keys.write_text(
        "\n".join(
            [
                (
                    'command="'
                    f"EVANDER_GIT_ROOT_B64={_b64(first_legacy.parent)} "
                    "EVANDER_GIT_AUTH_ACCESS=write "
                    f"EVANDER_GIT_AUTH_REPO_B64={_b64(first_legacy)} "
                    f"EVANDER_GIT_AUTH_SESSION_ID_B64={_b64('session-first')} wrapper"
                    '" ssh-ed25519 AAAAC3NzaFirst session-first'
                ),
                (
                    'command="'
                    f"EVANDER_GIT_ROOT_B64={_b64(second_legacy.parent)} "
                    "EVANDER_GIT_AUTH_ACCESS=write "
                    f"EVANDER_GIT_AUTH_REPO_B64={_b64(second_legacy)} "
                    f"EVANDER_GIT_AUTH_SESSION_ID_B64={_b64('session-second')} wrapper"
                    '" ssh-ed25519 AAAAC3NzaSecond session-second'
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    expired = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    _write_json(
        tmp_path / "runtime" / "sessions.json",
        {
            "session-first": {
                "session_id": "session-first",
                "directory_path": "root",
                "expires_at": expired,
                "metadata": {
                    "container_git_write_access": {
                        "repo_source": str(first_source),
                        "bare_repo_path": str(first_legacy),
                        "authorized_repo_path": str(first_legacy),
                        "authorized_keys_path": str(authorized_keys),
                    }
                },
            },
            "session-second": {
                "session_id": "session-second",
                "directory_path": "root",
                "expires_at": expired,
                "metadata": {
                    "container_git_write_access": {
                        "repo_source": str(second_source),
                        "bare_repo_path": str(second_legacy),
                        "authorized_repo_path": str(second_legacy),
                        "authorized_keys_path": str(authorized_keys),
                    }
                },
            },
        },
    )
    service = _service(tmp_path)

    applied = service.migrate("apply")
    records = {
        record["canonical_repository_url"]: record
        for record in (service.get_repository(repo_id) for repo_id in applied["managed_repo_ids"])
    }

    assert records[str(first_upstream.resolve())]["bindings_by_kind"]["authorized_sessions"] == [
        "session-first"
    ]
    assert records[str(second_upstream.resolve())]["bindings_by_kind"]["authorized_sessions"] == [
        "session-second"
    ]


@pytest.mark.parametrize("session_state", ["absent", "incomplete"])
def test_apply_binds_authorized_key_session_without_complete_session_metadata(
    tmp_path: Path,
    session_state: str,
) -> None:
    _source, _upstream, legacy = _repositories(tmp_path)
    runtime = tmp_path / "runtime"
    authorized_keys = tmp_path / "authorized_keys"
    session_id = "session-orphan"
    authorized_keys.write_text(
        'command="'
        f"EVANDER_GIT_ROOT_B64={_b64(legacy.parent)} "
        "EVANDER_GIT_AUTH_ACCESS=write "
        f"EVANDER_GIT_AUTH_REPO_B64={_b64(legacy)} "
        f"EVANDER_GIT_AUTH_SESSION_ID_B64={_b64(session_id)} wrapper"
        '" ssh-ed25519 AAAAC3NzaOrphan session-orphan\n',
        encoding="utf-8",
    )
    if session_state == "incomplete":
        _write_json(
            runtime / "sessions.json",
            {
                session_id: {
                    "session_id": session_id,
                    "expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                    "metadata": {"authorized_keys_path": str(authorized_keys)},
                }
            },
        )
    service = ManagedGitAdminService(
        SimpleNamespace(
            runtime_dir=runtime,
            git_authorized_keys_path=str(authorized_keys),
        ),
        SimpleNamespace(),
        tmp_path,
    )

    dry_run = service.migrate("dry-run")
    assert dry_run["can_apply"] is True
    assert dry_run["inventory"]["authorized_key_bindings"][0]["session_id"] == session_id

    applied = service.migrate("apply")
    repo_id = applied["managed_repo_ids"][0]

    assert service.get_repository(repo_id)["bindings_by_kind"]["authorized_sessions"] == [
        session_id
    ]

    service.migrate("finalize", migration_id=applied["migration_id"])
    service.unbind(repo_id, "authorized_sessions", session_id)
    with pytest.raises(ManagedGitConflictError, match="authorized SSH keys"):
        service.delete_repository(repo_id)

    authorized_keys.write_text("", encoding="utf-8")
    assert service.delete_repository(repo_id)["deleted"] is True


def test_finalize_allows_unrelated_changes_to_rewritten_state(tmp_path: Path) -> None:
    source, _upstream, _legacy = _repositories(tmp_path)
    issues_path = tmp_path / "runtime" / "local-issues.json"
    _write_json(
        issues_path,
        {
            "issue": {
                "issue_id": "issue",
                "issue_number": "1",
                "directory_path": "root",
                "repo_source": str(source),
            }
        },
    )
    service = _service(tmp_path)
    applied = service.migrate("apply")
    payload = json.loads(issues_path.read_text(encoding="utf-8"))
    payload["issue"]["title"] = "changed after apply"
    _write_json(issues_path, payload)

    finalized = service.migrate("finalize", migration_id=applied["migration_id"])

    assert finalized["status"] == "finalized"
    assert json.loads(issues_path.read_text(encoding="utf-8"))["issue"]["title"] == (
        "changed after apply"
    )


def test_finalize_allows_migrated_issue_to_be_deleted_and_unbound(tmp_path: Path) -> None:
    source, _upstream, _legacy = _repositories(tmp_path)
    issues_path = tmp_path / "runtime" / "local-issues.json"
    _write_json(
        issues_path,
        {
            "issue": {
                "issue_id": "issue",
                "issue_number": "1",
                "directory_path": "root",
                "repo_source": str(source),
            }
        },
    )
    service = _service(tmp_path)
    applied = service.migrate("apply")
    repo_id = applied["managed_repo_ids"][0]

    _write_json(issues_path, {})
    service.unbind(repo_id, "issues", "issue")

    finalized = service.migrate("finalize", migration_id=applied["migration_id"])

    assert finalized["status"] == "finalized"


def test_apply_rewrites_team_sqlite_project_git_json(tmp_path: Path) -> None:
    source, _upstream, legacy = _repositories(tmp_path)
    db_path = tmp_path / "runtime" / "addons" / "team-addon" / "state.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE team_state_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO team_state_metadata(key, value) VALUES (?, ?)",
            [("revision", "0"), ("updated_at", "2026-01-01T00:00:00Z")],
        )
        connection.execute("CREATE TABLE team_projects (id TEXT PRIMARY KEY, git_json TEXT)")
        connection.execute(
            "INSERT INTO team_projects(id, git_json) VALUES (?, ?)",
            (
                "sqlite-project",
                json.dumps(
                    {
                        "host_repo_path": str(source),
                        "bare_repo_path": str(legacy),
                        "base_branch": "main",
                    }
                ),
            ),
        )
    service = _service(tmp_path)

    applied = service.migrate("apply")
    repo_id = applied["managed_repo_ids"][0]

    with sqlite3.connect(db_path) as connection:
        raw_git = connection.execute(
            "SELECT git_json FROM team_projects WHERE id = 'sqlite-project'"
        ).fetchone()[0]
    assert json.loads(raw_git) == {"base_branch": "main", "managed_repo_id": repo_id}
    registry = json.loads(
        (tmp_path / "runtime" / "managed-git-repositories.json").read_text(encoding="utf-8")
    )
    assert {
        (binding["kind"], binding["binding_id"])
        for binding in registry["repositories"][repo_id]["bindings"]
    } == {("team_projects", "sqlite-project")}


def test_apply_updates_team_sqlite_in_place_with_wal_connection(tmp_path: Path) -> None:
    source, _upstream, legacy = _repositories(tmp_path)
    db_path = tmp_path / "runtime" / "addons" / "team-addon" / "state.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            "CREATE TABLE team_state_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        writer.executemany(
            "INSERT INTO team_state_metadata(key, value) VALUES (?, ?)",
            [("revision", "0"), ("updated_at", "2026-01-01T00:00:00Z")],
        )
        writer.execute("CREATE TABLE team_projects (id TEXT PRIMARY KEY, git_json TEXT)")
        writer.execute("CREATE TABLE unrelated_state (id TEXT PRIMARY KEY, value TEXT)")
        writer.execute(
            "INSERT INTO team_projects(id, git_json) VALUES (?, ?)",
            (
                "wal-project",
                json.dumps(
                    {
                        "host_repo_path": str(source),
                        "bare_repo_path": str(legacy),
                        "base_branch": "main",
                    }
                ),
            ),
        )
        writer.execute("INSERT INTO unrelated_state(id, value) VALUES ('before', 'preserved')")
        writer.commit()
        assert db_path.with_name(f"{db_path.name}-wal").exists()

        service = _service(tmp_path)
        applied = service.migrate("apply")
        repo_id = applied["managed_repo_ids"][0]

        writer.execute("INSERT INTO unrelated_state(id, value) VALUES ('after', 'also preserved')")
        writer.commit()

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        raw_git = connection.execute(
            "SELECT git_json FROM team_projects WHERE id = 'wal-project'"
        ).fetchone()[0]
        unrelated = connection.execute(
            "SELECT id, value FROM unrelated_state ORDER BY id"
        ).fetchall()
    assert json.loads(raw_git) == {
        "base_branch": "main",
        "managed_repo_id": repo_id,
    }
    assert unrelated == [
        ("after", "also preserved"),
        ("before", "preserved"),
    ]


def test_dry_run_blocks_active_target_workers_and_preflight_requires_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _upstream, _legacy = _repositories(tmp_path)
    service = _service(tmp_path)
    monkeypatch.setattr(
        service,
        "_worker_service_states",
        lambda: {
            "evander-trigger-orchestrator.service": "active",
            "evander-effect-orchestrator.service": "inactive",
        },
    )

    dry_run = service.migrate("dry-run")
    assert dry_run["can_apply"] is False
    assert "must be stopped" in " ".join(dry_run["blockers"])
    with pytest.raises(ManagedGitConflictError, match="git-repos migrate --dry-run"):
        service.register_repository("root", str(source))

    monkeypatch.setattr(
        service,
        "_worker_service_states",
        lambda: {
            "evander-trigger-orchestrator.service": "inactive",
            "evander-effect-orchestrator.service": "inactive",
        },
    )
    applied = service.migrate("apply")
    assert service.list_repositories()[0]["managed_repo_id"] == applied["managed_repo_ids"][0]


def test_apply_recovers_prepared_manifest_after_state_replacement(tmp_path: Path) -> None:
    source, _upstream, _legacy = _repositories(tmp_path)
    issues_path = tmp_path / "runtime" / "local-issues.json"
    _write_json(
        issues_path,
        {
            "issue": {
                "issue_id": "issue",
                "issue_number": "1",
                "directory_path": "root",
                "repo_source": str(source),
            }
        },
    )
    service = _service(tmp_path)
    applied = service.migrate("apply")
    manifest_path = (
        service.runtime_dir / "managed-git-migrations" / f"{applied['migration_id']}.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "prepared"
    _write_json(manifest_path, manifest)
    registry_path = service.runtime_dir / "managed-git-repositories.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["migrations"] = {}
    _write_json(registry_path, registry)

    recovered = service.migrate("apply")

    assert recovered["migration_id"] == applied["migration_id"]
    assert recovered["recovered"] is True
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "applied"


def test_apply_rewrites_nested_clone_config_and_legacy_expressions(
    tmp_path: Path,
) -> None:
    source, _upstream, _legacy = _repositories(tmp_path)
    workflow_path = tmp_path / "config" / "workflow-packs" / "nested-config.json"
    _write_json(
        workflow_path,
        {
            "nodes": [
                {
                    "id": "ensure",
                    "type": "effect:git:ensure-managed-repo",
                    "config": {
                        "directory_path": "root",
                        "repo_source": str(source),
                    },
                },
                {
                    "id": "clone",
                    "type": "effect:agent:clone-repo",
                    "config": {
                        "directory_path": "root",
                        "repo_source": str(source),
                    },
                },
                {
                    "id": "prompt",
                    "type": "effect:agent:enqueue-prompt",
                    "config": {
                        "prompt": "Repository: ${{clone.host_repo_path ?? null}}",
                    },
                },
            ]
        },
    )
    service = _service(tmp_path)

    dry_run = service.migrate("dry-run")

    assert "nested-config.json" in dry_run["inventory"]["workflow_packs_with_legacy_fields"]

    applied = service.migrate("apply")
    repo_id = applied["managed_repo_ids"][0]
    nodes = {
        node["id"]: node for node in json.loads(workflow_path.read_text(encoding="utf-8"))["nodes"]
    }
    assert nodes["ensure"]["config"] == {
        "directory_path": "root",
        "repo_source": str(source),
    }
    assert nodes["clone"]["config"] == {"managed_repo_id": repo_id}
    assert nodes["prompt"]["config"]["prompt"] == ("Repository: ${{clone.managed_repo_id ?? null}}")


def test_migrated_workflow_verifier_rejects_legacy_expression(tmp_path: Path) -> None:
    workflow_path = tmp_path / "workflow.json"
    _write_json(
        workflow_path,
        {
            "nodes": [
                {
                    "id": "prompt",
                    "type": "effect:agent:enqueue-prompt",
                    "config": {
                        "prompt": "Repository: ${{clone.host_repo_path ?? null}}",
                    },
                }
            ]
        },
    )

    with pytest.raises(ManagedGitConflictError, match="legacy"):
        _service(tmp_path)._verify_migrated_file_has_no_legacy_fields(
            workflow_path,
            kind="workflow_pack",
            mapping_records=[],
        )


def test_apply_rewrites_live_workflow_pack_through_revision_service(
    tmp_path: Path,
) -> None:
    source, _upstream, _legacy = _repositories(tmp_path)
    workflow_path = tmp_path / "config" / "workflow-packs" / "live.json"
    workflow_payload: dict[str, object] = {
        "nodes": [
            {
                "id": "clone",
                "type": "effect:agent:clone-repo",
                "repo_source": str(source),
                "host_repo_path": str(source),
            }
        ]
    }
    _write_json(workflow_path, workflow_payload)
    workflow_admin = _FakeWorkflowConfigAdmin(workflow_path, workflow_payload)
    before_revision = workflow_admin.revision
    service = _service(tmp_path)
    service.set_workflow_config_admin(workflow_admin)

    dry_run = service.migrate("dry-run")

    inventory = dry_run["inventory"]["workflow_pack_files_with_legacy_fields"]
    assert inventory == [
        {
            "path": str(workflow_path.resolve()),
            "sha256": hashlib.sha256(workflow_path.read_bytes()).hexdigest(),
            "legacy_fields": ["nodes[0].host_repo_path", "nodes[0].repo_source"],
            "live": True,
            "workflow_pack": "live.json",
            "revision": before_revision,
        }
    ]

    applied = service.migrate("apply")
    repo_id = applied["managed_repo_ids"][0]

    assert len(workflow_admin.save_calls) == 1
    save_call = workflow_admin.save_calls[0]
    assert save_call["expected_revision"] == before_revision
    assert save_call["workflow_pack"] == "live.json"
    assert save_call["operation"] == "managed_git_migration_apply"
    assert save_call["actor"].login == "system"
    assert json.loads(save_call["content"]) == {
        "nodes": [
            {
                "id": "clone",
                "type": "effect:agent:clone-repo",
                "managed_repo_id": repo_id,
            }
        ]
    }
    manifest_record = next(
        item for item in applied["rewritten_files"] if item["path"] == str(workflow_path.resolve())
    )
    assert manifest_record == {
        "path": str(workflow_path.resolve()),
        "kind": "workflow_pack_live",
        "before_sha256": manifest_record["before_sha256"],
        "after_sha256": manifest_record["after_sha256"],
        "workflow_pack": "live.json",
        "before_revision": before_revision,
        "after_revision": workflow_admin.revision,
    }


def test_apply_aborts_when_live_workflow_revision_changes_during_save(
    tmp_path: Path,
) -> None:
    source, _upstream, _legacy = _repositories(tmp_path)
    workflow_path = tmp_path / "config" / "workflow-packs" / "live.json"
    workflow_payload: dict[str, object] = {
        "nodes": [
            {
                "id": "clone",
                "type": "effect:agent:clone-repo",
                "repo_source": str(source),
                "host_repo_path": str(source),
            }
        ]
    }
    _write_json(workflow_path, workflow_payload)
    before = workflow_path.read_bytes()
    workflow_admin = _FakeWorkflowConfigAdmin(workflow_path, workflow_payload)
    workflow_admin.raise_revision_conflict = True
    service = _service(tmp_path)
    service.set_workflow_config_admin(workflow_admin)

    with pytest.raises(
        ManagedGitConflictError,
        match="changed concurrently during migration apply",
    ) as exc_info:
        service.migrate("apply")

    assert exc_info.value.details["current_revision"] == "concurrent-revision"
    assert len(workflow_admin.save_calls) == 1
    assert workflow_admin.save_calls[0]["expected_revision"] == workflow_admin.revision
    assert workflow_path.read_bytes() == before


def test_apply_restores_live_workflow_bytes_when_later_migration_step_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _upstream, _legacy = _repositories(tmp_path)
    workflow_path = tmp_path / "config" / "workflow-packs" / "live.json"
    before = (
        b'{\n "nodes": [{"id": "clone", "type": "effect:agent:clone-repo", '
        + json.dumps("repo_source").encode()
        + b": "
        + json.dumps(str(source)).encode()
        + b"}]\n}\n"
    )
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_bytes(before)
    workflow_payload = json.loads(before)
    workflow_admin = _FakeWorkflowConfigAdmin(workflow_path, workflow_payload)
    service = _service(tmp_path)
    service.set_workflow_config_admin(workflow_admin)

    def fail_bindings(_bindings: object) -> None:
        raise RuntimeError("binding write failed")

    monkeypatch.setattr(service, "_record_migration_bindings", fail_bindings)

    with pytest.raises(RuntimeError, match="binding write failed"):
        service.migrate("apply")

    assert workflow_path.read_bytes() == before
    assert [call["operation"] for call in workflow_admin.save_calls] == [
        "managed_git_migration_apply",
        "managed_git_migration_rollback",
    ]
