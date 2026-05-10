from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
from threading import RLock
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class JsonStore(Generic[T]):
    def __init__(self, path: Path, model: type[T], *, file_mode: int | None = None) -> None:
        self.path = path
        self.model = model
        self.file_mode = file_mode
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self._file_lock():
                if not self.path.exists():
                    self._write_initial_file("{}")
                elif self.file_mode is not None:
                    self.path.chmod(self.file_mode)

    def read_all(self) -> dict[str, T]:
        with self._lock:
            with self._file_lock():
                return self._read_all_unlocked()

    def write_all(self, items: dict[str, T]) -> None:
        with self._lock:
            with self._file_lock():
                self._write_all_unlocked(items)

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read_all_unlocked(self) -> dict[str, T]:
        text = self.path.read_text(encoding="utf-8")
        if not text.strip():
            return {}
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError(f"JSON store at {self.path} must contain an object at the top level")
        return {key: self.model.model_validate(value) for key, value in raw.items()}

    def _write_all_unlocked(self, items: dict[str, T]) -> None:
        payload = {key: value.model_dump(mode="json") for key, value in items.items()}
        serialized = json.dumps(payload, indent=2, sort_keys=True)
        temp_path = self._temp_path()
        try:
            with self._open_temp_file(temp_path) as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
            self._fsync_parent_dir()
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _write_initial_file(self, content: str) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(self.path, flags, self.file_mode or 0o666)
        if self.file_mode is not None:
            os.fchmod(fd, self.file_mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        self._fsync_parent_dir()

    def _open_temp_file(self, temp_path: Path):
        mode = self._write_mode()
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        os.chmod(temp_path, mode)
        return os.fdopen(fd, "w", encoding="utf-8")

    def _temp_path(self) -> Path:
        fd, name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        os.close(fd)
        temp_path = Path(name)
        temp_path.unlink()
        return temp_path

    def _write_mode(self) -> int:
        if self.file_mode is not None:
            return self.file_mode
        if self.path.exists():
            return stat.S_IMODE(self.path.stat().st_mode)
        return 0o666

    def get(self, key: str) -> T | None:
        return self.read_all().get(key)

    def put(self, key: str, value: T) -> None:
        with self._lock:
            with self._file_lock():
                items = self._read_all_unlocked()
                items[key] = value
                self._write_all_unlocked(items)

    def delete(self, key: str) -> None:
        with self._lock:
            with self._file_lock():
                items = self._read_all_unlocked()
                items.pop(key, None)
                self._write_all_unlocked(items)

    def _fsync_parent_dir(self) -> None:
        flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
        fd = os.open(self.path.parent, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
