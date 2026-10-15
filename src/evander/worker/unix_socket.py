"""Filesystem Unix sockets inside worker-owned job directories."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import sys


def bind_unix_socket(listener: socket.socket, path: Path) -> None:
    """Keep the socket in its private directory without Linux's sockaddr limit.

    Containers access the same inode through a short, job-private bind mount.
    Binding via an open parent descriptor avoids putting the host's complete
    session path in sockaddr_un, without moving the socket or changing cwd.
    """
    if sys.platform != "linux":
        listener.bind(str(path))
        return
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        listener.bind(f"/proc/self/fd/{parent}/{path.name}")
    finally:
        os.close(parent)
