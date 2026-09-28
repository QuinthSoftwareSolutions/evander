"""Serialize local Team dispatch plans without reserving SQLite's writer."""

from pathlib import Path
from threading import Lock

TEAM_DISPATCH_PREPARED_COMMIT_MAX_ATTEMPTS = 3
_PLANNING_LOCKS_GUARD = Lock()
_PLANNING_LOCKS: dict[str, Lock] = {}


def team_dispatch_planning_lock(state_db_path: object) -> Lock:
    key = str(Path(str(state_db_path)).resolve())
    with _PLANNING_LOCKS_GUARD:
        return _PLANNING_LOCKS.setdefault(key, Lock())
