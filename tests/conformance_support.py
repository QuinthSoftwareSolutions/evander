"""Conformance matrices and report formatting shared by tests and their reporter."""

from __future__ import annotations

import json
import os
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_report(
    requirements: list[dict[str, Any]],
    matrix: dict[str, list[str]],
    *,
    failures: list[str] | None = None,
    missing_tests: list[str] | None = None,
) -> dict[str, Any]:
    """Only successful recorded requirements count towards traceability."""
    seen = {entry["requirement_id"] for entry in requirements if entry["status"] == "pass"}
    missing = {
        task: [requirement for requirement in required if requirement not in seen]
        for task, required in matrix.items()
        if any(requirement not in seen for requirement in required)
    }
    failed = failures or []
    unexecuted = missing_tests or []
    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "fail"
        if missing or failed or unexecuted or any(entry["status"] != "pass" for entry in requirements)
        else "pass",
        "requirements": sorted(requirements, key=lambda entry: entry["requirement_id"]),
        "traceability_matrix": matrix,
        "missing_coverage": missing,
        "test_failures": sorted(set(failed)),
        "missing_tests": sorted(set(unexecuted)),
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    """Publish a complete snapshot without exposing a partially written document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        # Exclusive creation honors the caller's umask, just like the original
        # report writer. Preserve access for an existing operator/service report.
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
        if path.exists():
            temporary.chmod(stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


MATRICES = {
    "effect": {
        "task_schema_validation": ["schema:supported-effect-types", "schema:required-fields"],
        "task_provider_registration": ["provider:registration-and-factory"],
        "task_binding_and_chain": ["binding:resolution-and-chaining"],
        "task_queue_idempotency": ["queue:idempotency"],
        "task_failure_regressions": [
            "regression:missing-upstream-output",
            "regression:provider-failure",
            "regression:cycle-detection",
        ],
    },
    "trigger": {
        "task_trigger_schema": [
            "schema:trigger:cron",
            "schema:trigger:ticket:created",
            "schema:trigger:ticket:changed",
            "schema:trigger:communication:new-message",
            "schema:trigger:discord:new-message",
            "schema:trigger:teams:new-message",
            "schema:trigger:teams:direct-message",
            "schema:trigger:email:new-message",
            "schema:trigger:pull-request:new-feedback",
            "schema:trigger:pull-request:completed",
            "schema:trigger:ci:run:completed",
            "schema:trigger:review:new-request",
            "schema:trigger:approval:pending-created",
            "schema:trigger:git:push",
            "schema:duplicate-ids",
        ],
        "task_trigger_schema_invalid": ["schema:invalid:trigger:ci:run:completed"],
        "task_trigger_event_contract": [
            "event-contract:provider-payload-shape",
            "provider:pr-modes-and-git-push",
        ],
        "task_provider_behavior": [
            "provider:cron:cursor",
            "provider:cron:no-retroactive-catch-up",
            "provider:directory-and-filter-scoping",
            "provider:pr-modes-and-git-push",
        ],
        "task_orchestrator_integration": ["orchestrator:integration-idempotency-loop"],
    },
}
