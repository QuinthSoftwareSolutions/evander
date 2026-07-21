from __future__ import annotations

import os


def process_is_alive(pid: object) -> bool | None:
    """Return owner liveness, or ``None`` when no trustworthy PID was recorded."""

    if isinstance(pid, bool):
        return None
    try:
        normalized_pid = int(pid)
    except (TypeError, ValueError):
        return None
    if normalized_pid <= 0:
        return None
    try:
        os.kill(normalized_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, OverflowError):
        return None
    return True
