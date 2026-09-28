from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Protocol


DEFAULT_STALE_WORKTREE_SECONDS = 60 * 60
_MANIFEST_FILENAME = "manifest.json"
_HEX_OBJECT_ID = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
_SAFE_OPERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ManagedGitNotFoundError(LookupError):
    status_code = 404

    def __init__(self, message: str, *, cause: str = "unknown", evidence: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.cause = cause
        self.evidence = dict(evidence or {})


class ManagedGitConflictError(RuntimeError):
    status_code = 409

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})


class _RunGit(Protocol):
    def __call__(
        self,
        command: list[str],
        *,
        env: Mapping[str, str] | None = None,
        input_text: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]: ...


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_ref(value: str, *, field_name: str) -> str:
    ref_name = str(value or "").strip()
    if not ref_name:
        raise ValueError(f"{field_name} is required")
    if not ref_name.startswith("refs/"):
        ref_name = f"refs/heads/{ref_name}"
    result = subprocess.run(
        ["git", "check-ref-format", ref_name],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ValueError(f"{field_name} is not a valid Git ref: {ref_name}")
    return ref_name


def _normalize_object_id(value: str, *, field_name: str) -> str:
    object_id = str(value or "").strip().lower()
    if not _HEX_OBJECT_ID.fullmatch(object_id):
        raise ValueError(f"{field_name} must be an exact 40- or 64-character Git object ID")
    return object_id


def _managed_bare_path(managed_root: Path, record: Mapping[str, Any]) -> Path:
    path = Path(str(record["internal_bare_path"])).resolve(strict=False)
    expected = (managed_root / f"{record['managed_repo_id']}.git").resolve(strict=False)
    if path != expected:
        raise ManagedGitConflictError(
            f"Managed repository {record['managed_repo_id']} has an unsafe internal path"
        )
    return path


def _resolve_ref(run_git: _RunGit, bare_path: Path, ref_name: str) -> str | None:
    result = run_git(
        ["git", f"--git-dir={bare_path}", "rev-parse", "--verify", ref_name],
        check=False,
    )
    value = result.stdout.strip().lower()
    return value if result.returncode == 0 and _HEX_OBJECT_ID.fullmatch(value) else None


def _require_commit(run_git: _RunGit, bare_path: Path, object_id: str) -> None:
    result = run_git(
        ["git", f"--git-dir={bare_path}", "cat-file", "-e", f"{object_id}^{{commit}}"],
        check=False,
    )
    if result.returncode != 0:
        raise ManagedGitNotFoundError(
            f"Unknown commit: {object_id}", cause="reviewed_object_unavailable",
            evidence={"commit_sha": object_id},
        )


def _run_update_ref_transaction(
    run_git: _RunGit,
    bare_path: Path,
    commands: list[str],
) -> None:
    payload = "\n".join(["start", *commands, "prepare", "commit", ""])
    run_git(
        ["git", f"--git-dir={bare_path}", "update-ref", "--stdin"],
        input_text=payload,
    )


def _delete_ref_cas(
    run_git: _RunGit,
    bare_path: Path,
    ref_name: str,
    object_id: str,
) -> None:
    current = _resolve_ref(run_git, bare_path, ref_name)
    if current != object_id:
        return
    run_git(
        ["git", f"--git-dir={bare_path}", "update-ref", "-d", ref_name, object_id],
        check=False,
    )


def _with_git_config_environment(
    env: Mapping[str, str], config: Mapping[str, str]
) -> dict[str, str]:
    result = dict(env)
    for key in list(result):
        if (
            key in {"GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS"}
            or re.fullmatch(r"GIT_CONFIG_(?:KEY|VALUE)_\d+", key)
            or key.startswith("GIT_TRACE")
            or key in {"GIT_CURL_VERBOSE", "GIT_TRANSPORT_HELPER_DEBUG"}
        ):
            result.pop(key, None)
    result["GIT_CONFIG_COUNT"] = str(len(config))
    for index, (key, value) in enumerate(config.items()):
        result[f"GIT_CONFIG_KEY_{index}"] = key
        result[f"GIT_CONFIG_VALUE_{index}"] = value
    return result


def _write_json_atomic(path: Path, payload: Mapping[str, Any], *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_path.exists():
            temp_path.unlink()
