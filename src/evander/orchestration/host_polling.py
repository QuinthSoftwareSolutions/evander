from __future__ import annotations

import fcntl
import logging
import os
import select
import socket
import stat
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _SocketPathIdentity:
    device: int
    inode: int
    mode: int


@dataclass(frozen=True)
class _LockHandle:
    path: Path
    fd: int

    def __enter__(self) -> _LockHandle:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def single_instance_lock(
    path: Path, *, busy_error_text: str = "worker already running"
) -> _LockHandle:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise RuntimeError(f"{busy_error_text} (lock held at {path})") from exc
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
    return _LockHandle(path, fd)


class HostFileLockManager:
    def __init__(self, *, busy_error_text: str = "worker already running") -> None:
        self._busy_error_text = busy_error_text

    def lock(self, path: Any) -> _LockHandle:
        return single_instance_lock(Path(path), busy_error_text=self._busy_error_text)


class HostSleeper:
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def wake_socket_path_for_lock(lock_path: Path | str) -> Path:
    return Path(lock_path).with_suffix(".wake.sock")


class HostWakeSleeper:
    def __init__(
        self,
        path: Path | str,
        *,
        additional_paths: Iterable[Path | str] = (),
    ) -> None:
        self.path = Path(path)
        self._additional_paths = _unique_paths(additional_paths, excluding=self.path)
        self._socket: socket.socket | None = None
        self._sockets: dict[Path, socket.socket] = {}
        self._socket_path_identities: dict[Path, _SocketPathIdentity] = {}

    @property
    def paths(self) -> tuple[Path, ...]:
        return (self.path, *self._additional_paths)

    def set_path(self, path: Path | str) -> None:
        self.set_paths(path, additional_paths=self._additional_paths)

    def set_paths(
        self,
        path: Path | str,
        *,
        additional_paths: Iterable[Path | str] = (),
    ) -> None:
        next_path = Path(path)
        next_additional_paths = _unique_paths(additional_paths, excluding=next_path)
        if next_path == self.path and next_additional_paths == self._additional_paths:
            return
        was_started = bool(self._sockets)
        self.close()
        self.path = next_path
        self._additional_paths = next_additional_paths
        if was_started:
            self.start()

    def start(self) -> None:
        if not hasattr(socket, "AF_UNIX"):
            return
        self.close()
        for path in self.paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path == self.path:
                # The primary socket is protected by the worker's single-instance
                # lock, so retaining its unconditional stale-path recovery preserves
                # the existing ownership contract.
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            elif not _prepare_additional_wake_socket_path(path):
                continue
            listener: socket.socket | None = None
            try:
                listener = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                listener.bind(str(path))
                listener.setblocking(False)
            except OSError:
                if listener is not None:
                    listener.close()
                logger.warning("Failed to bind wake socket path=%s", path, exc_info=True)
                continue
            path_identity = _socket_path_identity(path)
            if path_identity is None:
                listener.close()
                logger.warning("Wake socket path disappeared after bind path=%s", path)
                continue
            self._sockets[path] = listener
            self._socket_path_identities[path] = path_identity
        self._socket = self._sockets.get(self.path)

    def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        listeners = list(self._sockets.values())
        if not listeners:
            time.sleep(seconds)
            return
        ready, _, _ = select.select(listeners, [], [], seconds)
        if not ready:
            return
        for listener in ready:
            while True:
                try:
                    listener.recv(1024)
                except BlockingIOError:
                    break
                except OSError:
                    break

    def close(self) -> None:
        owned_sockets = [
            (path, listener, self._socket_path_identities.get(path))
            for path, listener in self._sockets.items()
        ]
        self._sockets.clear()
        self._socket_path_identities.clear()
        self._socket = None
        for _path, listener, _path_identity in owned_sockets:
            listener.close()
        for path, _listener, path_identity in owned_sockets:
            if path_identity is not None:
                _unlink_socket_path_if_owned(path, path_identity)


class HostWakeNotifier:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def notify(self) -> bool:
        if not hasattr(socket, "AF_UNIX"):
            return False
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
                notifier.setblocking(False)
                notifier.sendto(b"wake", str(self.path))
        except OSError:
            return False
        return True


def notify_wake_socket_for_lock(lock_path: Path | str) -> bool:
    return HostWakeNotifier(wake_socket_path_for_lock(lock_path)).notify()


def _prepare_additional_wake_socket_path(path: Path) -> bool:
    """Return whether this instance may safely bind an additional socket path."""

    path_identity = _socket_path_identity(path)
    if path_identity is None:
        return True
    if not stat.S_ISSOCK(path_identity.mode):
        logger.warning(
            "Refusing to replace non-socket wake path=%s",
            path,
        )
        return False
    if _unix_datagram_listener_is_live(path):
        logger.debug("Wake socket already has a live listener path=%s", path)
        return False
    return _unlink_socket_path_if_owned(path, path_identity)


def _unix_datagram_listener_is_live(path: Path) -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as probe:
            probe.connect(str(path))
    except FileNotFoundError:
        return False
    except ConnectionRefusedError:
        return False
    except OSError:
        # Permission, protocol, and transient errors do not prove that the path is
        # stale. Preserve it rather than risking another process's live listener.
        return True
    return True


def _socket_path_identity(path: Path) -> _SocketPathIdentity | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    return _SocketPathIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
    )


def _unlink_socket_path_if_owned(path: Path, expected: _SocketPathIdentity) -> bool:
    current = _socket_path_identity(path)
    if current is None:
        return True
    if (current.device, current.inode) != (expected.device, expected.inode):
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return True
    except OSError:
        logger.debug("Failed to remove wake socket path=%s", path, exc_info=True)
        return False
    return True


def _unique_paths(
    paths: Iterable[Path | str],
    *,
    excluding: Path,
) -> tuple[Path, ...]:
    unique: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path == excluding or path in unique:
            continue
        unique.append(path)
    return tuple(unique)
