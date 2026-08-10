from __future__ import annotations

from hashlib import sha256
from pathlib import PurePosixPath
import stat


HOST_DIRECTORY_TRASH_NAME = ".evander-trash"


def normalize_relative_host_path(value: object, *, allow_empty: bool) -> tuple[str, ...]:
    text = "" if value is None else str(value)
    if "\x00" in text or "\\" in text:
        raise ValueError("host directory path must be a relative POSIX path")
    if not text:
        if allow_empty:
            return ()
        raise ValueError("host directory path must be non-empty")
    path = PurePosixPath(text)
    if path.is_absolute():
        raise ValueError("host directory path must be relative")
    parts = path.parts
    if not parts:
        if allow_empty:
            return ()
        raise ValueError("host directory path must be non-empty")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("host directory path must not contain traversal")
    if parts and parts[0] == HOST_DIRECTORY_TRASH_NAME:
        raise ValueError("host directory path is reserved")
    return tuple(parts)


def host_directory_revision(info: object) -> str:
    mode = int(getattr(info, "st_mode"))
    kind = "directory" if stat.S_ISDIR(mode) else "file" if stat.S_ISREG(mode) else "unsupported"
    source = ":".join(
        [
            kind,
            str(int(getattr(info, "st_dev"))),
            str(int(getattr(info, "st_ino"))),
            str(int(getattr(info, "st_size"))),
            str(int(getattr(info, "st_mtime_ns"))),
            str(int(getattr(info, "st_ctime_ns"))),
        ]
    )
    return sha256(source.encode("ascii")).hexdigest()
