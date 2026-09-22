"""Transport-neutral verifier evidence; interpretation belongs to configured evaluators."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..models import ReleaseVerificationCommand

RESULT_PREFIX = "EVANDER_VERIFICATION_RESULT="


class VerificationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    outcome: Literal["passed", "baseline_exception", "failed", "infrastructure"]
    explanation: str = Field(min_length=1, max_length=8000)
    candidate_commit_sha: str
    base_commit_sha: str | None = None
    artifact_refs: list[str] = Field(default_factory=list, max_length=50)


def read_verification_evidence(
    stdout: str,
    *,
    command: ReleaseVerificationCommand,
    candidate_commit_sha: str,
    base_commit_sha: str | None,
) -> VerificationEvidence:
    records = [
        line[len(RESULT_PREFIX) :] for line in stdout.splitlines() if line.startswith(RESULT_PREFIX)
    ]
    if len(records) != 1 or len(records[0]) > 65536:
        raise ValueError("verifier must emit exactly one bounded json-v1 result")
    report = VerificationEvidence.model_validate(json.loads(records[0]))
    if report.candidate_commit_sha != candidate_commit_sha:
        raise ValueError("verifier evidence candidate does not match frozen manifest")
    if report.base_commit_sha is not None and report.base_commit_sha != base_commit_sha:
        raise ValueError("verifier evidence base does not match frozen manifest")
    if report.outcome == "baseline_exception":
        if not command.allow_baseline_exceptions:
            raise ValueError("baseline exception is not authorized by the frozen command policy")
        if not base_commit_sha or report.base_commit_sha != base_commit_sha:
            raise ValueError("baseline exception requires the frozen base commit")
        if not report.artifact_refs or any(not ref.strip() for ref in report.artifact_refs):
            raise ValueError("baseline exception requires evidence artifact references")
    return report
