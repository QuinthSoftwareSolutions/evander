from __future__ import annotations


def within_ticket_directories(candidate: str, directories: list[str]) -> bool:
    if not directories:
        return True
    for directory in directories:
        normalized = directory.strip().strip("/")
        if candidate == normalized or candidate.startswith(f"{normalized}/"):
            return True
    return False
