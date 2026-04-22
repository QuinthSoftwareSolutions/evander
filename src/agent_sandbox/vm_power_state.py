from __future__ import annotations

_POWER_STATE_MAP = {
    "running": "running",
    "idle": "running",
    "paused": "suspended",
    "paused (user)": "suspended",
    "suspended": "suspended",
    "in shutdown": "stopping",
    "stopping": "stopping",
    "starting": "starting",
    "shut off": "stopped",
    "stopped": "stopped",
    "deallocated": "stopped",
    "deallocating": "stopping",
    "powerstate/running": "running",
    "powerstate/stopped": "stopped",
    "powerstate/deallocated": "stopped",
    "powerstate/stopping": "stopping",
    "powerstate/starting": "starting",
    "crashed": "failed",
    "failed": "failed",
    "pmsuspended": "suspended",
}


def normalize_power_state(raw_state: str | None) -> str:
    normalized = str(raw_state or "").strip().lower()
    if not normalized:
        return "unknown"
    return _POWER_STATE_MAP.get(normalized, "unknown")
