from __future__ import annotations

from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
from typing import Any

from evander.admin.services.managed_git_worktree_service import (
    ManagedGitWorktreeDependencies,
    ManagedGitWorktreeService,
)

_COMMIT_SHA = "a" * 40
_REPO_ID = "mr_0123456789abcdef0123"


class _FakeGit:
    def __init__(self) -> None:
        self.refs = {"refs/heads/main": _COMMIT_SHA}
        self.commands: list[list[str]] = []

    def __call__(
        self,
        command: list[str],
        *,
        env=None,
        input_text: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        del env, check
        self.commands.append(command)
        stdout = ""
        returncode = 0
        if "rev-parse" in command:
            ref_name = command[-1]
            value = self.refs.get(ref_name)
            if value is None:
                returncode = 1
            else:
                stdout = f"{value}\n"
        elif "cat-file" in command:
            returncode = 0
        elif command[-2:] == ["update-ref", "--stdin"]:
            for line in str(input_text or "").splitlines():
                parts = line.split()
                if not parts:
                    continue
                if parts[0] == "verify":
                    assert self.refs.get(parts[1]) == parts[2]
                elif parts[0] == "create":
                    self.refs[parts[1]] = parts[2]
        elif "update-ref" in command and "-d" in command:
            self.refs.pop(command[-2], None)
        elif command[-3:] == ["worktree", "list", "--porcelain"]:
            stdout = ""
        return subprocess.CompletedProcess(command, returncode, stdout, "")


def _service(
    tmp_path: Path,
) -> tuple[ManagedGitWorktreeService, _FakeGit, dict[str, Any], list[str]]:
    managed_root = tmp_path / "managed-git"
    worktree_root = tmp_path / "git-worktrees"
    record = {
        "managed_repo_id": _REPO_ID,
        "internal_bare_path": str(managed_root / f"{_REPO_ID}.git"),
    }
    fake_git = _FakeGit()
    preflights: list[str] = []

    def require_record(managed_repo_id: str) -> dict[str, Any]:
        assert managed_repo_id == _REPO_ID
        preflights.append("require-record")
        return record

    service = ManagedGitWorktreeService(
        ManagedGitWorktreeDependencies(
            stale_worktree_seconds=3600.0,
            runtime_dir=tmp_path,
            managed_root=managed_root,
            worktree_root=worktree_root,
            require_record=require_record,
            require_migration_preflight=lambda: preflights.append("reconcile"),
            read_registry=lambda: {"repositories": {_REPO_ID: record}},
            repo_lock=lambda _managed_repo_id, *, exclusive: nullcontext(),
            run_git=fake_git,
        )
    )
    return service, fake_git, record, preflights


def test_temporary_worktree_service_owns_manifest_ref_and_cleanup(tmp_path: Path) -> None:
    service, fake_git, _record, preflights = _service(tmp_path)
    operation_ref = "refs/evander/worktrees/direct-test"
    operation_dir = tmp_path / "git-worktrees" / _REPO_ID / "direct-test"

    with service.temporary_worktree(
        _REPO_ID,
        source_ref="refs/heads/main",
        operation_id="direct-test",
        purpose="isolated-service-test",
    ) as checkout:
        assert checkout == operation_dir / "checkout"
        assert fake_git.refs[operation_ref] == _COMMIT_SHA
        manifest = json.loads((operation_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["purpose"] == "isolated-service-test"
        assert manifest["commit_sha"] == _COMMIT_SHA

    assert preflights == ["require-record"]
    assert operation_ref not in fake_git.refs
    assert not operation_dir.exists()


def test_worktree_service_reconciles_stale_manifest_without_admin_facade(
    tmp_path: Path,
) -> None:
    service, fake_git, record, preflights = _service(tmp_path)
    operation_id = "stale-test"
    operation_ref = f"refs/evander/worktrees/{operation_id}"
    operation_dir = tmp_path / "git-worktrees" / _REPO_ID / operation_id
    operation_dir.mkdir(parents=True)
    (operation_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "evander.managed-git-worktree/v1",
                "managed_repo_id": _REPO_ID,
                "operation_id": operation_id,
                "operation_ref": operation_ref,
                "commit_sha": _COMMIT_SHA,
                "source_ref": "refs/heads/main",
                "purpose": "stale-test",
                "created_at": "2020-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    fake_git.refs[operation_ref] = _COMMIT_SHA

    result = service.reconcile_stale_worktrees(_REPO_ID, older_than_seconds=0)

    assert record["managed_repo_id"] == _REPO_ID
    assert result == {
        "removed": [{"managed_repo_id": _REPO_ID, "operation_id": operation_id}],
        "skipped_locked": [],
        "skipped_recent": [],
        "invalid": [],
    }
    assert preflights == ["reconcile"]
    assert operation_ref not in fake_git.refs
    assert not operation_dir.exists()
