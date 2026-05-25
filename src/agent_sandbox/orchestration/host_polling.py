from __future__ import annotations

import fcntl
import logging
import os
import select
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


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
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._socket: socket.socket | None = None

    def set_path(self, path: Path | str) -> None:
        next_path = Path(path)
        if next_path == self.path:
            return
        was_started = self._socket is not None
        self.close()
        self.path = next_path
        if was_started:
            self.start()

    def start(self) -> None:
        if not hasattr(socket, "AF_UNIX"):
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.close()
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        listener: socket.socket | None = None
        try:
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            listener.bind(str(self.path))
            listener.setblocking(False)
        except OSError:
            if listener is not None:
                listener.close()
            logger.warning("Failed to bind wake socket path=%s", self.path, exc_info=True)
            self._socket = None
            return
        self._socket = listener

    def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        listener = self._socket
        if listener is None:
            time.sleep(seconds)
            return
        ready, _, _ = select.select([listener], [], [], seconds)
        if not ready:
            return
        while True:
            try:
                listener.recv(1024)
            except BlockingIOError:
                return
            except OSError:
                return

    def close(self) -> None:
        listener = self._socket
        self._socket = None
        if listener is not None:
            listener.close()
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logger.debug("Failed to remove wake socket path=%s", self.path, exc_info=True)


class HostWakeNotifier:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def notify(self) -> bool:
        if not hasattr(socket, "AF_UNIX"):
            return False
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
                notifier.sendto(b"wake", str(self.path))
        except OSError:
            return False
        return True


def notify_wake_socket_for_lock(lock_path: Path | str) -> bool:
    return HostWakeNotifier(wake_socket_path_for_lock(lock_path)).notify()
