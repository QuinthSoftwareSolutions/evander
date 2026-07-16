from __future__ import annotations

import base64
from contextlib import contextmanager
import fcntl
import os
import pwd
import tempfile
from dataclasses import dataclass
from pathlib import Path

from evander.git_pr_notes import agent_pr_notes_ref_for_session


_SUPPORTED_PUBLIC_KEY_TYPES = {
    "ssh-rsa",
    "ssh-ed25519",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
}


@dataclass(frozen=True)
class InstalledGitAuthorizedKey:
    authorized_keys_path: Path
    public_key_body: str
    authorized_repo_path: Path
    authorized_ref_prefix: str
    authorized_pr_notes_ref: str
    allowed_from: str


@dataclass(frozen=True)
class InstalledGitReadAuthorizedKey:
    authorized_keys_path: Path
    public_key_body: str
    authorized_repo_path: Path
    allowed_from: str


def authorized_keys_path_for_user(git_user: str) -> Path:
    normalized = str(git_user or "").strip()
    if not normalized:
        raise ValueError("git_user is required")
    try:
        home = pwd.getpwnam(normalized).pw_dir
    except KeyError as exc:
        raise ValueError(f"Git user does not exist: {normalized}") from exc
    return Path(home) / ".ssh" / "authorized_keys"


def install_git_read_authorized_key(
    *,
    public_key: str,
    git_root: str | Path,
    authorized_repo_path: str | Path,
    allowed_from: str,
    wrapper_path: str | Path,
    authorized_keys_path: str | Path,
) -> InstalledGitReadAuthorizedKey:
    public_key_line, public_key_body = _validated_public_key_line(public_key)
    git_root_real = Path(git_root).resolve(strict=False)
    repo_real = Path(authorized_repo_path).resolve(strict=False)
    try:
        repo_real.relative_to(git_root_real)
    except ValueError as exc:
        raise ValueError(f"authorized repo must be under Git root: {git_root_real}") from exc
    wrapper_real = Path(wrapper_path).resolve(strict=False)
    if not wrapper_real.exists():
        raise ValueError(f"Git SSH wrapper does not exist: {wrapper_real}")
    allowed_from_value = _validate_authorized_keys_option_value(allowed_from, "allowed_from")
    forced_command = _authorized_read_forced_command(
        git_root=git_root_real,
        repo_path=repo_real,
        wrapper_path=wrapper_real,
    )
    entry = (
        f'from="{allowed_from_value}",no-agent-forwarding,no-port-forwarding,'
        f'no-pty,no-user-rc,no-X11-forwarding,command="{_escape_authorized_command(forced_command)}" '
        f"{public_key_line}"
    )
    path = Path(authorized_keys_path)
    _replace_authorized_key_entry(path, public_key_body, entry)
    return InstalledGitReadAuthorizedKey(
        authorized_keys_path=path,
        public_key_body=public_key_body,
        authorized_repo_path=repo_real,
        allowed_from=allowed_from_value,
    )


def install_git_write_authorized_key(
    *,
    public_key: str,
    git_root: str | Path,
    authorized_repo_path: str | Path,
    session_id: str,
    allowed_from: str,
    wrapper_path: str | Path,
    authorized_keys_path: str | Path,
) -> InstalledGitAuthorizedKey:
    public_key_line, public_key_body = _validated_public_key_line(public_key)
    git_root_real = Path(git_root).resolve(strict=False)
    repo_real = Path(authorized_repo_path).resolve(strict=False)
    try:
        repo_real.relative_to(git_root_real)
    except ValueError as exc:
        raise ValueError(f"authorized repo must be under Git root: {git_root_real}") from exc
    ref_prefix = authorized_ref_prefix_for_session(session_id)
    pr_notes_ref = agent_pr_notes_ref_for_session(session_id)
    wrapper_real = Path(wrapper_path).resolve(strict=False)
    if not wrapper_real.exists():
        raise ValueError(f"Git SSH wrapper does not exist: {wrapper_real}")
    allowed_from_value = _validate_authorized_keys_option_value(allowed_from, "allowed_from")
    forced_command = _authorized_write_forced_command(
        git_root=git_root_real,
        repo_path=repo_real,
        session_id=session_id,
        ref_prefix=ref_prefix,
        pr_notes_ref=pr_notes_ref,
        wrapper_path=wrapper_real,
    )
    entry = (
        f'from="{allowed_from_value}",no-agent-forwarding,no-port-forwarding,'
        f'no-pty,no-user-rc,no-X11-forwarding,command="{_escape_authorized_command(forced_command)}" '
        f"{public_key_line}"
    )
    path = Path(authorized_keys_path)
    _replace_authorized_key_entry(path, public_key_body, entry)
    return InstalledGitAuthorizedKey(
        authorized_keys_path=path,
        public_key_body=public_key_body,
        authorized_repo_path=repo_real,
        authorized_ref_prefix=ref_prefix,
        authorized_pr_notes_ref=pr_notes_ref,
        allowed_from=allowed_from_value,
    )


def remove_git_authorized_key_by_public_key_body(
    *,
    authorized_keys_path: str | Path,
    public_key_body: str,
) -> bool:
    body = str(public_key_body or "").strip()
    if not body:
        return False
    path = Path(authorized_keys_path)
    with _authorized_keys_update_lock(path):
        if not path.exists():
            return False
        lines = path.read_text(encoding="utf-8").splitlines()
        filtered = [line for line in lines if body not in line]
        if len(filtered) == len(lines):
            return False
        _write_authorized_keys(path, filtered)
        return True


def remove_git_authorized_keys_by_session_id(
    *,
    authorized_keys_path: str | Path,
    session_id: str,
) -> int:
    """Remove exact Evander write-key entries for one session.

    Libvirt guests generate their Git key inside the VM, so the host lifecycle
    code does not retain the public key body. The forced command contains the
    base64-encoded session id as an exact, secret-free capability marker that is
    safe to use for revocation.
    """

    normalized_session_id = str(session_id or "").strip()
    # Keep this validation aligned with authorized_ref_prefix_for_session().
    if (
        not normalized_session_id
        or "/" in normalized_session_id
        or normalized_session_id in {".", ".."}
    ):
        raise ValueError("session_id must be a single non-empty ref path component")
    marker = (
        "EVANDER_GIT_AUTH_SESSION_ID_B64="
        f"{_one_line_base64(normalized_session_id)}"
    )
    path = Path(authorized_keys_path)
    with _authorized_keys_update_lock(path):
        if not path.exists():
            return 0
        lines = path.read_text(encoding="utf-8").splitlines()
        filtered = [line for line in lines if marker not in line]
        removed = len(lines) - len(filtered)
        if removed:
            _write_authorized_keys(path, filtered)
        return removed


def public_key_body_from_line(public_key: str) -> str:
    return _validated_public_key_line(public_key)[1]


def _validated_public_key_line(public_key: str) -> tuple[str, str]:
    normalized = str(public_key or "").strip()
    if "\n" in normalized or "\r" in normalized:
        raise ValueError("public key must be a single line")
    parts = normalized.split()
    if len(parts) < 2:
        raise ValueError("public key is missing key material")
    key_type, key_body = parts[0], parts[1]
    if key_type not in _SUPPORTED_PUBLIC_KEY_TYPES and not key_type.startswith("ecdsa-sha2-nistp"):
        raise ValueError("public key has an unsupported key type")
    if not key_body:
        raise ValueError("public key is missing key material")
    return normalized, key_body


def authorized_ref_prefix_for_session(session_id: str) -> str:
    normalized = str(session_id or "").strip()
    if not normalized or "/" in normalized or normalized in {".", ".."}:
        raise ValueError("session_id must be a single non-empty ref path component")
    return f"refs/heads/agents/{normalized}/"


def _authorized_write_forced_command(
    *,
    git_root: Path,
    repo_path: Path,
    session_id: str,
    ref_prefix: str,
    pr_notes_ref: str,
    wrapper_path: Path,
) -> str:
    return (
        f"EVANDER_GIT_ROOT_B64={_one_line_base64(str(git_root))}"
        " EVANDER_GIT_AUTH_ACCESS=write"
        f" EVANDER_GIT_AUTH_REPO_B64={_one_line_base64(str(repo_path))}"
        f" EVANDER_GIT_AUTH_SESSION_ID_B64={_one_line_base64(session_id)}"
        f" EVANDER_GIT_AUTH_REF_PREFIX_B64={_one_line_base64(ref_prefix)}"
        f" EVANDER_GIT_AUTH_PR_NOTES_REF_B64={_one_line_base64(pr_notes_ref)}"
        f" {wrapper_path}"
    )


def _authorized_read_forced_command(
    *,
    git_root: Path,
    repo_path: Path,
    wrapper_path: Path,
) -> str:
    return (
        f"EVANDER_GIT_ROOT_B64={_one_line_base64(str(git_root))}"
        " EVANDER_GIT_AUTH_ACCESS=read-only"
        f" EVANDER_GIT_AUTH_REPO_B64={_one_line_base64(str(repo_path))}"
        f" {wrapper_path}"
    )


def _replace_authorized_key_entry(
    authorized_keys_path: Path,
    public_key_body: str,
    entry: str,
) -> None:
    if not public_key_body:
        raise ValueError("refusing authorized_keys update with empty public key body")
    with _authorized_keys_update_lock(authorized_keys_path):
        existing = (
            authorized_keys_path.read_text(encoding="utf-8").splitlines()
            if authorized_keys_path.exists()
            else []
        )
        filtered = [line for line in existing if public_key_body not in line]
        filtered.append(entry)
        _write_authorized_keys(authorized_keys_path, filtered)


@contextmanager
def _authorized_keys_update_lock(authorized_keys_path: Path):
    authorized_keys_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = authorized_keys_path.with_name(f".{authorized_keys_path.name}.lock")
    with lock_path.open("a", encoding="utf-8") as handle:
        try:
            stat_source = (
                authorized_keys_path if authorized_keys_path.exists() else lock_path.parent
            )
            stat_result = stat_source.stat()
            if os.geteuid() == 0:
                os.chown(lock_path, stat_result.st_uid, stat_result.st_gid)
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_authorized_keys(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + ("\n" if lines else ""))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        if path.exists():
            stat_result = path.stat()
            if os.geteuid() == 0:
                os.chown(temp_path, stat_result.st_uid, stat_result.st_gid)
            os.chmod(temp_path, stat_result.st_mode & 0o777)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _validate_authorized_keys_option_value(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    if '"' in normalized or "\n" in normalized or "\r" in normalized:
        raise ValueError(f"{field_name} must not contain quotes or newlines")
    return normalized


def _escape_authorized_command(command: str) -> str:
    return str(command).replace("\\", "\\\\").replace('"', '\\"')


def _one_line_base64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii").replace("\n", "")
