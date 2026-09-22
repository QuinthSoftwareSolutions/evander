from __future__ import annotations

import errno
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any

AGENT_ARTIFACTS_ENV = "EVANDER_ARTIFACTS_DIR"
DEFAULT_AGENT_ARTIFACTS_DIR = "/artifacts"


_DIR_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_NOFOLLOW_FLAGS = (
    getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
)


def normalize_artifact_relative_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        raise ValueError("artifact path is required")
    path = PurePosixPath(text)
    if path.is_absolute():
        raise ValueError("artifact path must be relative")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("artifact path must not contain empty, dot, or parent segments")
    return path.as_posix()


def open_relative_regular_file_no_follow(
    root: Path,
    relative_path: Any,
    *,
    flags: int = os.O_RDONLY,
    mode: int = 0o666,
    create_parents: bool = False,
    missing_ok: bool = False,
    path_label: str = "path",
    already_exists_message: str | None = None,
    unsafe_path_message: str | None = None,
) -> int | None:
    """Open a root-relative regular file without following path symlinks."""
    normalized_relative_path = normalize_artifact_relative_path(relative_path)
    try:
        parent_fd, leaf_name = _open_relative_parent_directory(
            root,
            normalized_relative_path,
            create_parents=create_parents,
            path_label=path_label,
            unsafe_path_message=unsafe_path_message,
        )
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    try:
        try:
            file_fd = os.open(
                leaf_name,
                flags | _FILE_NOFOLLOW_FLAGS,
                mode,
                dir_fd=parent_fd,
            )
        except FileExistsError as exc:
            if already_exists_message is not None:
                raise ValueError(already_exists_message) from exc
            raise
        except FileNotFoundError:
            if missing_ok:
                return None
            raise
        except OSError as exc:
            _raise_path_open_error(
                exc,
                path_label=path_label,
                normalized_relative_path=normalized_relative_path,
                unsafe_path_message=unsafe_path_message,
            )
        try:
            file_stat = os.fstat(file_fd)
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError(f"{path_label} must name a regular file")
        except Exception:
            os.close(file_fd)
            raise
        return file_fd
    finally:
        os.close(parent_fd)


def read_artifact_text_no_follow(
    root: Path, relative_path: Any, *, max_bytes: int | None = None
) -> str:
    if max_bytes is not None and max_bytes <= 0:
        raise ValueError("Artifact byte limit must be positive")
    normalized_relative_path = normalize_artifact_relative_path(relative_path)
    fd = open_relative_regular_file_no_follow(
        root,
        normalized_relative_path,
        flags=os.O_RDONLY,
        path_label="artifact path",
    )
    if fd is None:
        raise FileNotFoundError(f"artifact path is not a regular file: {normalized_relative_path}")
    try:
        if max_bytes is None:
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                return handle.read()
        with os.fdopen(fd, "rb") as handle:
            if os.fstat(handle.fileno()).st_size > max_bytes:
                raise ValueError("Worker artifact exceeds import limit")
            # Bound the read itself too: a writer may grow the file after fstat.
            content = handle.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise ValueError("Worker artifact exceeds import limit")
            return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("artifact file is not valid UTF-8 text") from exc


def resolve_artifact_file(root: Path, relative_path: Any) -> Path:
    normalized_relative_path = normalize_artifact_relative_path(relative_path)
    resolved_root = root.resolve(strict=True)
    candidate = (root / normalized_relative_path).resolve(strict=True)
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("artifact path must stay within the session artifacts folder") from exc
    if not candidate.is_file():
        raise FileNotFoundError(f"artifact path is not a regular file: {normalized_relative_path}")
    return candidate


def _open_relative_parent_directory(
    root: Path,
    normalized_relative_path: str,
    *,
    create_parents: bool,
    path_label: str,
    unsafe_path_message: str | None,
) -> tuple[int, str]:
    parts = PurePosixPath(normalized_relative_path).parts
    if not parts:
        raise ValueError(f"{path_label} is required")
    current_fd = _open_root_directory(
        root,
        path_label=path_label,
        unsafe_path_message=unsafe_path_message,
    )
    try:
        for part in parts[:-1]:
            while True:
                try:
                    next_fd = os.open(part, _DIR_OPEN_FLAGS, dir_fd=current_fd)
                    break
                except FileNotFoundError:
                    if not create_parents:
                        raise
                    try:
                        os.mkdir(part, 0o755, dir_fd=current_fd)
                    except FileExistsError:
                        pass
                except OSError as exc:
                    _raise_path_open_error(
                        exc,
                        path_label=path_label,
                        normalized_relative_path=normalized_relative_path,
                        unsafe_path_message=unsafe_path_message,
                    )
            try:
                directory_stat = os.fstat(next_fd)
                if not stat.S_ISDIR(directory_stat.st_mode):
                    raise ValueError(f"{path_label} must not contain non-directory segments")
            except Exception:
                os.close(next_fd)
                raise
            os.close(current_fd)
            current_fd = next_fd
        return current_fd, parts[-1]
    except Exception:
        os.close(current_fd)
        raise


def _open_root_directory(
    root: Path,
    *,
    path_label: str,
    unsafe_path_message: str | None,
) -> int:
    try:
        root_fd = os.open(root, _DIR_OPEN_FLAGS)
    except OSError as exc:
        _raise_path_open_error(
            exc,
            path_label=path_label,
            normalized_relative_path=str(root),
            unsafe_path_message=unsafe_path_message,
        )
    try:
        root_stat = os.fstat(root_fd)
        if not stat.S_ISDIR(root_stat.st_mode):
            raise ValueError(f"{path_label} root must be a directory")
    except Exception:
        os.close(root_fd)
        raise
    return root_fd


def _raise_path_open_error(
    exc: OSError,
    *,
    path_label: str,
    normalized_relative_path: str,
    unsafe_path_message: str | None = None,
) -> None:
    if exc.errno == errno.ENOENT:
        raise FileNotFoundError(
            f"{path_label} is not a regular file: {normalized_relative_path}"
        ) from exc
    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
        raise ValueError(
            unsafe_path_message
            or f"{path_label} must not contain symlinks or non-directory segments"
        ) from exc
    if exc.errno in {errno.EISDIR, errno.ENXIO, errno.ENODEV}:
        raise ValueError(f"{path_label} must name a regular file") from exc
    raise exc
