"""Structured Git failures shared by host verification and execution backends."""

from __future__ import annotations


class GitPreparationFailure(ValueError, RuntimeError):
    """Typed rejection compatible with both worker and host Git error boundaries."""

    def __init__(self, message: str, *, cause: str, evidence: dict | None = None):
        super().__init__(message)
        self.cause = cause
        self.evidence = dict(evidence or {})

    def diagnostic(self) -> dict:
        return {"cause": self.cause, "evidence": self.evidence}


GIT_RECOVERY_PROTOCOL_VERSION = 1
