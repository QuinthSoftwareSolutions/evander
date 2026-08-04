from __future__ import annotations

import fcntl
import os
import shlex
import subprocess
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator


_IGNORED_PATH_CLEAN_BATCH_COUNT = 1024
_IGNORED_PATH_CLEAN_BATCH_BYTES = 128 * 1024
_IGNORED_PATH_CLEAN_TIMEOUT_SECONDS = 60.0
_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW
)


@dataclass(frozen=True, slots=True)
class TaskWorktreeInspection:
    safe_to_remove: bool
    target_exists: bool
    registered: bool
    actual_branch: str | None = None
    actual_commit_sha: str | None = None
    failed_check: str | None = None
    message: str = ""
    tracked_paths: list[str] = field(default_factory=list)
    untracked_paths: list[str] = field(default_factory=list)
    ignored_paths: list[str] = field(default_factory=list)
    ignored_bytes: int = 0
    worktree_bytes: int = 0


@dataclass(frozen=True, slots=True)
class TaskWorktreeCleanupResult:
    state: str
    safe_to_remove: bool
    removed: bool
    pruned: bool
    validation_count: int
    actual_branch: str | None
    actual_commit_sha: str | None
    failed_check: str | None
    message: str
    tracked_paths: list[str]
    untracked_paths: list[str]
    ignored_paths: list[str]
    ignored_bytes: int
    worktree_bytes: int
    reclaimed_bytes: int


@dataclass(frozen=True, slots=True)
class _BoundTaskWorktreePaths:
    root_fd: int
    repo_fd: int
    worktrees_root_fd: int | None
    target_fd: int | None


@contextmanager
def task_worktree_repository_lock(lock_path: Path) -> Iterator[None]:
    """Serialize task-worktree preparation and cleanup for one session repository."""

    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path.parent.chmod(0o700)
    with lock_path.open("a+", encoding="utf-8") as handle:
        os.chmod(lock_path, 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def cleanup_task_worktree(
    *,
    trusted_root: Path,
    repo_dir: Path,
    worktrees_root: Path,
    target_dir: Path,
    branch: str,
    expected_commit_sha: str,
    lock_path: Path,
    dry_run: bool = False,
) -> TaskWorktreeCleanupResult:
    """Validate twice and remove one task worktree while holding its repository lock."""

    with task_worktree_repository_lock(lock_path):
        first = inspect_task_worktree(
            repo_dir=repo_dir,
            worktrees_root=worktrees_root,
            target_dir=target_dir,
            branch=branch,
            expected_commit_sha=expected_commit_sha,
        )
        if not first.safe_to_remove:
            return _refused_result(first, validation_count=1)
        if dry_run:
            return _result_from_inspection(
                first,
                state="dry_run",
                removed=False,
                pruned=False,
                validation_count=1,
                message=(
                    "task worktree is safe to remove"
                    if first.target_exists
                    else "task worktree is already absent"
                ),
            )

        try:
            with _open_bound_task_worktree_paths(
                trusted_root=trusted_root,
                repo_dir=repo_dir,
                worktrees_root=worktrees_root,
                target_dir=target_dir,
                target_exists=first.target_exists,
            ) as bound:
                pass_fds = _bound_pass_fds(bound)
                bound_repo_dir = _fd_path(bound.repo_fd)
                bound_worktrees_root = (
                    _fd_path(bound.worktrees_root_fd)
                    if bound.worktrees_root_fd is not None
                    else worktrees_root
                )
                bound_target_dir = (
                    _fd_path(bound.target_fd) if bound.target_fd is not None else target_dir
                )
                second = inspect_task_worktree(
                    repo_dir=bound_repo_dir,
                    worktrees_root=bound_worktrees_root,
                    target_dir=bound_target_dir,
                    branch=branch,
                    expected_commit_sha=expected_commit_sha,
                    pass_fds=pass_fds,
                )
                if not first.target_exists and second.target_exists:
                    return _refused_result(
                        _unsafe_inspection(
                            second,
                            failed_check="worktree_path_changed",
                            message="task worktree appeared during final validation",
                        ),
                        validation_count=2,
                    )
                if bound.target_fd is not None and not _fd_still_names_path(
                    bound.target_fd, target_dir
                ):
                    return _refused_result(
                        _unsafe_inspection(
                            second,
                            failed_check="worktree_path_changed",
                            message="task worktree changed during final validation",
                        ),
                        validation_count=2,
                    )
                if not second.safe_to_remove:
                    return _refused_result(second, validation_count=2)

                if second.target_exists and second.ignored_paths:
                    assert bound.target_fd is not None
                    cleanup_error = _remove_proven_ignored_paths(
                        bound.target_fd,
                        second.ignored_paths,
                    )
                    if cleanup_error is not None:
                        return _refused_result(
                            _unsafe_inspection(
                                second,
                                failed_check="ignored_path_cleanup_failed",
                                message=cleanup_error,
                            ),
                            validation_count=2,
                        )

                removed = False
                if second.target_exists:
                    assert bound.target_fd is not None
                    removal = _run(
                        [
                            "git",
                            "-C",
                            str(bound_repo_dir),
                            "worktree",
                            "remove",
                            str(bound_target_dir),
                        ],
                        check=False,
                        pass_fds=pass_fds,
                    )
                    if removal.returncode != 0:
                        final = inspect_task_worktree(
                            repo_dir=bound_repo_dir,
                            worktrees_root=bound_worktrees_root,
                            target_dir=bound_target_dir,
                            branch=branch,
                            expected_commit_sha=expected_commit_sha,
                            pass_fds=pass_fds,
                        )
                        if not final.safe_to_remove:
                            return _refused_result(final, validation_count=3)
                        detail = removal.stderr.strip() or removal.stdout.strip()
                        return _refused_result(
                            TaskWorktreeInspection(
                                safe_to_remove=False,
                                target_exists=final.target_exists,
                                registered=final.registered,
                                actual_branch=final.actual_branch,
                                actual_commit_sha=final.actual_commit_sha,
                                failed_check="worktree_removal_failed",
                                message=(
                                    "Git refused non-force task worktree removal"
                                    + (f": {detail}" if detail else "")
                                ),
                                tracked_paths=list(final.tracked_paths),
                                untracked_paths=list(final.untracked_paths),
                                ignored_paths=list(final.ignored_paths),
                                ignored_bytes=final.ignored_bytes,
                                worktree_bytes=final.worktree_bytes,
                            ),
                            validation_count=3,
                        )
                    removed = True
                _run(
                    ["git", "-C", str(bound_repo_dir), "worktree", "prune", "--expire", "now"],
                    pass_fds=pass_fds,
                )

                if os.path.lexists(target_dir):
                    return _result_from_inspection(
                        second,
                        state="refused",
                        removed=removed,
                        pruned=True,
                        validation_count=2,
                        failed_check="worktree_removal_incomplete",
                        message=f"task worktree still exists after removal: {target_dir}",
                        safe_to_remove=False,
                    )
                registered = _registered_worktree(
                    bound_repo_dir,
                    bound_target_dir,
                    pass_fds=pass_fds,
                )
                if registered is not None:
                    return _result_from_inspection(
                        second,
                        state="refused",
                        removed=removed,
                        pruned=True,
                        validation_count=2,
                        failed_check="worktree_registration_active",
                        message=f"task worktree registration remains after pruning: {target_dir}",
                        safe_to_remove=False,
                    )
                return _result_from_inspection(
                    second,
                    state="removed" if removed else "already_absent",
                    removed=removed,
                    pruned=True,
                    validation_count=2,
                    message=(
                        "task worktree removed and registration pruned"
                        if removed
                        else "task worktree was already absent; registrations were pruned"
                    ),
                )
        except (OSError, ValueError) as exc:
            return _refused_result(
                _unsafe_inspection(
                    first,
                    failed_check="worktree_path_changed",
                    message=f"task worktree changed before final validation: {exc}",
                ),
                validation_count=2,
            )


@contextmanager
def _open_bound_task_worktree_paths(
    *,
    trusted_root: Path,
    repo_dir: Path,
    worktrees_root: Path,
    target_dir: Path,
    target_exists: bool,
) -> Iterator[_BoundTaskWorktreePaths]:
    """Open task-worktree paths from an immutable session-home directory."""

    root_fd = _open_directory_no_follow(trusted_root)
    opened_fds = [root_fd]
    try:
        repo_fd = _open_relative_directory(
            root_fd,
            _relative_directory_parts(
                root=trusted_root,
                path=repo_dir,
                path_label="task worktree repository",
            ),
        )
        opened_fds.append(repo_fd)
        worktrees_root_fd: int | None = None
        target_fd: int | None = None
        if target_exists:
            worktrees_root_fd = _open_relative_directory(
                root_fd,
                _relative_directory_parts(
                    root=trusted_root,
                    path=worktrees_root,
                    path_label="task worktree root",
                ),
            )
            opened_fds.append(worktrees_root_fd)
            target_fd = _open_relative_directory(
                worktrees_root_fd,
                _relative_directory_parts(
                    root=worktrees_root,
                    path=target_dir,
                    path_label="task worktree",
                ),
            )
            opened_fds.append(target_fd)
        yield _BoundTaskWorktreePaths(
            root_fd=root_fd,
            repo_fd=repo_fd,
            worktrees_root_fd=worktrees_root_fd,
            target_fd=target_fd,
        )
    finally:
        for fd in reversed(opened_fds):
            os.close(fd)


def _open_directory_no_follow(path: Path) -> int:
    return os.open(path, _DIRECTORY_OPEN_FLAGS)


def _open_relative_directory(root_fd: int, parts: tuple[str, ...]) -> int:
    current_fd = os.dup(root_fd)
    try:
        for part in parts:
            next_fd = os.open(part, _DIRECTORY_OPEN_FLAGS, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _relative_directory_parts(
    *,
    root: Path,
    path: Path,
    path_label: str,
) -> tuple[str, ...]:
    root_path = _absolute_path_without_parent_traversal(root, path_label="trusted root")
    candidate_path = _absolute_path_without_parent_traversal(path, path_label=path_label)
    try:
        relative = candidate_path.relative_to(root_path)
    except ValueError as exc:
        raise ValueError(f"{path_label} must stay below {root_path}") from exc
    if not relative.parts:
        raise ValueError(f"{path_label} must stay below {root_path}")
    return relative.parts


def _absolute_path_without_parent_traversal(path: Path, *, path_label: str) -> Path:
    candidate = Path(path)
    if ".." in candidate.parts:
        raise ValueError(f"{path_label} must not contain parent segments")
    return candidate if candidate.is_absolute() else Path.cwd() / candidate


def _bound_pass_fds(bound: _BoundTaskWorktreePaths) -> tuple[int, ...]:
    return tuple(
        fd for fd in (bound.repo_fd, bound.worktrees_root_fd, bound.target_fd) if fd is not None
    )


def _fd_path(directory_fd: int) -> Path:
    return Path(f"/proc/self/fd/{directory_fd}")


def inspect_task_worktree(
    *,
    repo_dir: Path,
    worktrees_root: Path,
    target_dir: Path,
    branch: str,
    expected_commit_sha: str,
    pass_fds: tuple[int, ...] = (),
) -> TaskWorktreeInspection:
    expected_sha = expected_commit_sha.casefold()
    root = worktrees_root.resolve(strict=False)
    target = target_dir.resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError:
        return _unsafe(
            target_exists=os.path.lexists(target_dir),
            failed_check="worktree_path_escape",
            message=f"task worktree path must stay below {root}",
        )
    if target == root:
        return _unsafe(
            target_exists=os.path.lexists(target_dir),
            failed_check="worktree_path_mismatch",
            message="task worktree path cannot equal the worktree root",
        )
    if not repo_dir.is_dir() or not (repo_dir / ".git").exists():
        return _unsafe(
            target_exists=os.path.lexists(target_dir),
            failed_check="repository_missing",
            message=f"session repository does not exist: {repo_dir}",
        )
    if (
        _run(
            ["git", "check-ref-format", "--branch", branch],
            check=False,
        ).returncode
        != 0
    ):
        return _unsafe(
            target_exists=os.path.lexists(target_dir),
            failed_check="invalid_branch",
            message=f"invalid task branch: {branch!r}",
        )

    branch_commit = _git_output(
        ["git", "-C", str(repo_dir), "rev-parse", "--verify", f"refs/heads/{branch}"],
        check=False,
        pass_fds=pass_fds,
    )
    if branch_commit is None or branch_commit.casefold() != expected_sha:
        return _unsafe(
            target_exists=os.path.lexists(target_dir),
            failed_check="retained_commit_mismatch",
            message=(f"branch {branch!r} does not retain expected commit {expected_commit_sha}"),
        )

    registration = _registered_worktree(repo_dir, target_dir, pass_fds=pass_fds)
    target_exists = os.path.lexists(target_dir)
    if not target_exists:
        if registration is not None and registration.get("path") != str(target):
            return _unsafe(
                target_exists=False,
                failed_check="worktree_registration_mismatch",
                message=f"unexpected worktree registration for {target_dir}",
            )
        return TaskWorktreeInspection(
            safe_to_remove=True,
            target_exists=False,
            registered=registration is not None,
            actual_branch=branch,
            actual_commit_sha=branch_commit,
            message="task worktree is already absent",
        )
    if not target_dir.is_dir():
        return _unsafe(
            target_exists=True,
            failed_check="worktree_path_type",
            message=f"task worktree path is not a directory: {target_dir}",
        )
    if registration is None:
        return _unsafe(
            target_exists=True,
            failed_check="worktree_registration_missing",
            message=f"task worktree is not registered by repository: {target_dir}",
        )
    registered_branch = registration.get("branch")
    if registered_branch != f"refs/heads/{branch}":
        return _unsafe(
            target_exists=True,
            failed_check="worktree_registration_mismatch",
            message=(
                f"task worktree registration uses {registered_branch or 'no branch'}, "
                f"expected refs/heads/{branch}"
            ),
        )

    top_level = _git_output(
        ["git", "-C", str(target_dir), "rev-parse", "--show-toplevel"],
        pass_fds=pass_fds,
    )
    if top_level is None or Path(top_level).resolve(strict=False) != target:
        return _unsafe(
            target_exists=True,
            failed_check="worktree_path_mismatch",
            message=f"Git reports an unexpected task worktree path: {top_level}",
        )
    actual_branch = _git_output(
        ["git", "-C", str(target_dir), "branch", "--show-current"],
        pass_fds=pass_fds,
    )
    if actual_branch != branch:
        return _unsafe(
            target_exists=True,
            failed_check="branch_mismatch",
            message=f"task worktree uses branch {actual_branch!r}, expected {branch!r}",
            actual_branch=actual_branch,
        )
    actual_commit = _git_output(
        ["git", "-C", str(target_dir), "rev-parse", "HEAD"],
        pass_fds=pass_fds,
    )
    if actual_commit is None or actual_commit.casefold() != expected_sha:
        return _unsafe(
            target_exists=True,
            failed_check="head_mismatch",
            message=(
                f"task worktree HEAD is {actual_commit or 'unavailable'}, "
                f"expected {expected_commit_sha}"
            ),
            actual_branch=actual_branch,
            actual_commit_sha=actual_commit,
        )

    tracked = _tracked_change_paths(target_dir, pass_fds=pass_fds)
    if tracked:
        return _unsafe(
            target_exists=True,
            failed_check="tracked_changes",
            message="task worktree contains tracked modifications",
            actual_branch=actual_branch,
            actual_commit_sha=actual_commit,
            tracked_paths=tracked,
        )
    untracked = _git_path_list(
        ["git", "-C", str(target_dir), "ls-files", "--others", "--exclude-standard", "-z"],
        pass_fds=pass_fds,
    )
    if untracked:
        return _unsafe(
            target_exists=True,
            failed_check="untracked_files",
            message="task worktree contains non-ignored untracked files",
            actual_branch=actual_branch,
            actual_commit_sha=actual_commit,
            untracked_paths=untracked,
        )
    ignored = _git_path_list(
        [
            "git",
            "-C",
            str(target_dir),
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "-z",
        ],
        pass_fds=pass_fds,
    )
    return TaskWorktreeInspection(
        safe_to_remove=True,
        target_exists=True,
        registered=True,
        actual_branch=actual_branch,
        actual_commit_sha=actual_commit,
        message="task worktree passed cleanup safety validation",
        ignored_paths=ignored,
        ignored_bytes=_path_inventory_bytes(target_dir, ignored),
        worktree_bytes=_tree_inventory_bytes(target_dir),
    )


def build_libvirt_task_worktree_cleanup_command(
    *,
    trusted_root: str,
    repo_dir: str,
    worktrees_root: str,
    target_dir: str,
    branch: str,
    expected_commit_sha: str,
    lock_path: str,
    dry_run: bool = False,
) -> str:
    """Build a self-contained guest command for locked worktree cleanup."""

    invocation = [
        "python3",
        "-c",
        Path(__file__).read_text(encoding="utf-8"),
        "--trusted-root",
        trusted_root,
        "--repo-dir",
        repo_dir,
        "--worktrees-root",
        worktrees_root,
        "--target-dir",
        target_dir,
        "--branch",
        branch,
        "--expected-commit-sha",
        expected_commit_sha,
        "--lock-path",
        lock_path,
    ]
    if dry_run:
        invocation.append("--dry-run")
    return " ".join(shlex.quote(item) for item in invocation)


def _registered_worktree(
    repo_dir: Path,
    target_dir: Path,
    *,
    pass_fds: tuple[int, ...] = (),
) -> dict[str, str] | None:
    output = _run(
        ["git", "-C", str(repo_dir), "worktree", "list", "--porcelain", "-z"],
        pass_fds=pass_fds,
    ).stdout
    target = target_dir.resolve(strict=False)
    current: dict[str, str] = {}
    records: list[dict[str, str]] = []
    for entry in output.split("\0"):
        if not entry:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = entry.partition(" ")
        if key == "worktree":
            current["path"] = str(Path(value).resolve(strict=False))
        elif key in {"HEAD", "branch"}:
            current[key.casefold()] = value
    if current:
        records.append(current)
    return next(
        (
            record
            for record in records
            if Path(record.get("path", "")).resolve(strict=False) == target
        ),
        None,
    )


def _tracked_change_paths(
    target_dir: Path,
    *,
    pass_fds: tuple[int, ...] = (),
) -> list[str]:
    output = _run(
        [
            "git",
            "-C",
            str(target_dir),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=no",
        ],
        pass_fds=pass_fds,
    ).stdout
    fields = output.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if not field:
            continue
        status = field[:2]
        path = field[3:] if len(field) > 3 else ""
        if path:
            paths.append(path)
        if ("R" in status or "C" in status) and index < len(fields):
            renamed_path = fields[index]
            index += 1
            if renamed_path:
                paths.append(renamed_path)
    return sorted(set(paths))


def _git_path_list(args: list[str], *, pass_fds: tuple[int, ...] = ()) -> list[str]:
    return sorted({path for path in _run(args, pass_fds=pass_fds).stdout.split("\0") if path})


def _remove_proven_ignored_paths(target_fd: int, paths: list[str]) -> str | None:
    """Remove only ignored paths observed by the final pre-cleanup inspection."""

    bound_target = f"/proc/self/fd/{target_fd}"
    for batch in _ignored_path_clean_batches(paths):
        try:
            result = subprocess.run(
                [
                    "git",
                    "--literal-pathspecs",
                    "-C",
                    bound_target,
                    "clean",
                    "-f",
                    "-d",
                    "-X",
                    "--",
                    *batch,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=_IGNORED_PATH_CLEAN_TIMEOUT_SECONDS,
                pass_fds=(target_fd,),
            )
        except subprocess.TimeoutExpired:
            return (
                "timed out after "
                f"{_IGNORED_PATH_CLEAN_TIMEOUT_SECONDS:g} seconds while removing "
                f"{len(batch)} proven ignored path(s)"
            )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            return f"failed to remove {len(batch)} proven ignored path(s)" + (
                f": {detail}" if detail else ""
            )
    return None


def _fd_still_names_path(directory_fd: int, path: Path) -> bool:
    try:
        opened = os.fstat(directory_fd)
        current = path.stat(follow_symlinks=False)
        return (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino)
    except OSError:
        return False


def _unsafe_inspection(
    inspection: TaskWorktreeInspection,
    *,
    failed_check: str,
    message: str,
) -> TaskWorktreeInspection:
    values = asdict(inspection)
    values.update(safe_to_remove=False, failed_check=failed_check, message=message)
    return TaskWorktreeInspection(**values)


def _ignored_path_clean_batches(paths: list[str]) -> Iterator[list[str]]:
    batch: list[str] = []
    batch_bytes = 0
    for relative_path in paths:
        path_bytes = len(os.fsencode(relative_path)) + 1
        if batch and (
            len(batch) >= _IGNORED_PATH_CLEAN_BATCH_COUNT
            or batch_bytes + path_bytes > _IGNORED_PATH_CLEAN_BATCH_BYTES
        ):
            yield batch
            batch = []
            batch_bytes = 0
        batch.append(relative_path)
        batch_bytes += path_bytes
    if batch:
        yield batch


def _path_inventory_bytes(root: Path, paths: list[str]) -> int:
    total = 0
    root_resolved = root.resolve(strict=False)
    for relative in paths:
        candidate = root / relative
        try:
            candidate.resolve(strict=False).relative_to(root_resolved)
            total += candidate.lstat().st_size
        except (FileNotFoundError, OSError, ValueError):
            continue
    return total


def _tree_inventory_bytes(root: Path) -> int:
    total = 0
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        stat_result = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    total += stat_result.st_size
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
        except OSError:
            continue
    return total


def _git_output(
    args: list[str],
    *,
    check: bool = True,
    pass_fds: tuple[int, ...] = (),
) -> str | None:
    result = _run(args, check=check, pass_fds=pass_fds)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _run(
    args: list[str],
    *,
    check: bool = True,
    timeout_seconds: float | None = None,
    pass_fds: tuple[int, ...] = (),
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        pass_fds=pass_fds,
    )


def _unsafe(
    *,
    target_exists: bool,
    failed_check: str,
    message: str,
    actual_branch: str | None = None,
    actual_commit_sha: str | None = None,
    tracked_paths: list[str] | None = None,
    untracked_paths: list[str] | None = None,
) -> TaskWorktreeInspection:
    return TaskWorktreeInspection(
        safe_to_remove=False,
        target_exists=target_exists,
        registered=False,
        actual_branch=actual_branch,
        actual_commit_sha=actual_commit_sha,
        failed_check=failed_check,
        message=message,
        tracked_paths=list(tracked_paths or []),
        untracked_paths=list(untracked_paths or []),
    )


def _refused_result(
    inspection: TaskWorktreeInspection,
    *,
    validation_count: int,
) -> TaskWorktreeCleanupResult:
    return _result_from_inspection(
        inspection,
        state="refused",
        removed=False,
        pruned=False,
        validation_count=validation_count,
        safe_to_remove=False,
    )


def _result_from_inspection(
    inspection: TaskWorktreeInspection,
    *,
    state: str,
    removed: bool,
    pruned: bool,
    validation_count: int,
    message: str | None = None,
    safe_to_remove: bool | None = None,
    failed_check: str | None = None,
) -> TaskWorktreeCleanupResult:
    return TaskWorktreeCleanupResult(
        state=state,
        safe_to_remove=(inspection.safe_to_remove if safe_to_remove is None else safe_to_remove),
        removed=removed,
        pruned=pruned,
        validation_count=validation_count,
        actual_branch=inspection.actual_branch,
        actual_commit_sha=inspection.actual_commit_sha,
        failed_check=failed_check or inspection.failed_check,
        message=message or inspection.message,
        tracked_paths=list(inspection.tracked_paths),
        untracked_paths=list(inspection.untracked_paths),
        ignored_paths=list(inspection.ignored_paths),
        ignored_bytes=inspection.ignored_bytes,
        worktree_bytes=inspection.worktree_bytes,
        reclaimed_bytes=inspection.worktree_bytes if removed else 0,
    )


def _main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("--trusted-root", required=True)
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--worktrees-root", required=True)
    parser.add_argument("--target-dir", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--expected-commit-sha", required=True)
    parser.add_argument("--lock-path", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = cleanup_task_worktree(
        trusted_root=Path(args.trusted_root),
        repo_dir=Path(args.repo_dir),
        worktrees_root=Path(args.worktrees_root),
        target_dir=Path(args.target_dir),
        branch=args.branch,
        expected_commit_sha=args.expected_commit_sha,
        lock_path=Path(args.lock_path),
        dry_run=args.dry_run,
    )
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "TaskWorktreeCleanupResult",
    "TaskWorktreeInspection",
    "build_libvirt_task_worktree_cleanup_command",
    "cleanup_task_worktree",
    "inspect_task_worktree",
    "task_worktree_repository_lock",
]
