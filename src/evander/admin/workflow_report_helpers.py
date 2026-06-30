from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import ValidationError

from evander.admin.common import _coerce_optional_string
from evander.admin.payloads import WorkflowRunNodeActivityPayload
from evander.admin.services import process_helpers

_WORKFLOW_FAILURE_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_WORKFLOW_FAILURE_ISO_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b"
)
_WORKFLOW_FAILURE_LONG_HEX_RE = re.compile(r"\b[0-9a-fA-F]{12,}\b")
_WORKFLOW_FAILURE_URL_RE = re.compile(r"(?P<url>(?:https?|ssh|git)://[^\s'\"`<>]+)")
_WORKFLOW_FAILURE_TEMP_PATH_RE = re.compile(
    r"(?P<path>(?:/tmp|/var/tmp|/private/tmp|/run/user/\d+)/[^\s'\"`<>]+)"
)
_WORKFLOW_FAILURE_AGENT_REF_RE = re.compile(r"\b(?:refs/(?:heads|remotes)/)?agents/[^\s'\"`<>]+")
_WORKFLOW_FAILURE_LABELLED_ID_RE = re.compile(
    r"(?i)\b(?P<label>trace|session|thread|execution|event|request|correlation|"
    r"approval_request|dispatch|artifact|pr|issue|ado_item)(?:_id)?\b(?P<sep>\s*[:=]\s*)"
    r"(?P<value>[^\s,;)\]}]+)"
)
_WORKFLOW_FAILURE_WS_RE = re.compile(r"\s+")
_WORKFLOW_FAILURE_ID_PLACEHOLDERS: dict[str, str] = {
    "trace": "<trace_id>",
    "session": "<session_id>",
    "thread": "<thread_id>",
    "execution": "<execution_id>",
    "event": "<event_id>",
    "request": "<request_id>",
    "correlation": "<correlation_id>",
    "approval_request": "<approval_request_id>",
    "dispatch": "<dispatch_id>",
    "artifact": "<artifact_id>",
    "pr": "<pr_id>",
    "issue": "<issue_id>",
    "ado_item": "<ado_item_id>",
}


def _workflow_failure_replace_url(match: re.Match[str]) -> str:
    url = match.group("url")
    try:
        split = urlsplit(url)
    except ValueError:
        return "<url>"
    return urlunsplit((split.scheme, split.netloc, split.path, "", ""))


def _workflow_failure_replace_labelled_id(match: re.Match[str]) -> str:
    label = match.group("label").lower()
    placeholder = _WORKFLOW_FAILURE_ID_PLACEHOLDERS.get(label, f"<{label}_id>")
    return f"{label}_id={placeholder}"


def _workflow_failure_fallback_text(outputs: Mapping[str, object]) -> str:
    error_class = _coerce_optional_string(outputs.get("error_class"))
    source_kind = (
        _coerce_optional_string(outputs.get("source_kind"))
        or _coerce_optional_string(outputs.get("node_type"))
        or _coerce_optional_string(outputs.get("effect_type"))
        or _coerce_optional_string(outputs.get("gate_type"))
        or _coerce_optional_string(outputs.get("trigger_type"))
    )
    if error_class and source_kind:
        return f"{error_class} {source_kind}"
    if error_class:
        return error_class
    if source_kind:
        return source_kind
    return "workflow failure"


def _normalize_workflow_failure_signature(
    error_text: object | None, outputs: object | None = None
) -> tuple[str, str]:
    output_payload = outputs if isinstance(outputs, Mapping) else {}
    summary = _coerce_optional_string(error_text)
    if summary is None:
        summary = _coerce_optional_string(output_payload.get("error_message"))
    if summary is None:
        summary = _coerce_optional_string(output_payload.get("message"))
    if summary is None:
        summary = _workflow_failure_fallback_text(output_payload)

    normalized = summary
    normalized = _WORKFLOW_FAILURE_URL_RE.sub(_workflow_failure_replace_url, normalized)
    normalized = _WORKFLOW_FAILURE_TEMP_PATH_RE.sub("<temp_path>", normalized)
    normalized = _WORKFLOW_FAILURE_AGENT_REF_RE.sub("<agent_ref>", normalized)
    normalized = _WORKFLOW_FAILURE_LABELLED_ID_RE.sub(
        _workflow_failure_replace_labelled_id, normalized
    )
    normalized = _WORKFLOW_FAILURE_UUID_RE.sub("<uuid>", normalized)
    normalized = _WORKFLOW_FAILURE_ISO_TIMESTAMP_RE.sub("<timestamp>", normalized)
    normalized = _WORKFLOW_FAILURE_LONG_HEX_RE.sub("<hex>", normalized)
    normalized = _WORKFLOW_FAILURE_WS_RE.sub(" ", normalized).strip()
    if not normalized:
        normalized = _workflow_failure_fallback_text(output_payload)
    key = _WORKFLOW_FAILURE_WS_RE.sub(" ", normalized).strip().casefold()
    return normalized, key


def _collect_workflow_keyed_values(payload: Any, *, key_suffix: str | None = None) -> list[str]:
    values: list[str] = []

    def walk(value: Any, key: str | None = None) -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                walk(child_value, str(child_key))
            return
        if isinstance(value, list):
            for child_value in value:
                walk(child_value, key)
            return
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return
            if key_suffix is not None and (key is None or not key.lower().endswith(key_suffix)):
                return
            values.append(normalized)

    walk(payload)
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _collect_workflow_artifact_ids(payload: Any) -> list[str]:
    values: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for child_value in value.values():
                walk(child_value)
            return
        if isinstance(value, list):
            for child_value in value:
                walk(child_value)
            return
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return
            try:
                from evander.communication_artifacts import validate_artifact_id

                values.append(validate_artifact_id(normalized))
            except ValueError:
                pass

    walk(payload)
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


WORKFLOW_FAILURE_CLUSTER_LIMIT_MAX = 100
WORKFLOW_FAILURE_CLUSTER_EXAMPLE_LIMIT = 3
WORKFLOW_FAILURE_CLUSTER_DEFAULT_RECENT_THRESHOLD_SECONDS = 24 * 60 * 60
WORKFLOW_FAILURE_CLUSTER_STATE_PRIORITY: dict[str, int] = {
    "active": 0,
    "recent": 1,
    "resolved": 2,
    "historical": 3,
}

CONFORMANCE_REPORT_STALE_AFTER = timedelta(days=7)
CONFORMANCE_REPORT_SNIPPET_LIMIT = 10
CONFORMANCE_REPORT_TEXT_LIMIT = 500
CONFORMANCE_REPORT_RUN_TIMEOUT_SECONDS = 120
TRIGGER_CONFORMANCE_GUIDANCE = (
    "PYTHONPATH=src python -m pytest -q tests/test_trigger_conformance_suite.py"
)
EFFECT_CONFORMANCE_GUIDANCE = (
    "PYTHONPATH=src python -m pytest -q tests/test_effect_conformance_suite.py"
)
CONFORMANCE_REPORT_SPECS = {
    "trigger": {
        "kind": "trigger",
        "label": "Trigger",
        "artifact_path": "trigger-conformance-report.json",
        "guidance": TRIGGER_CONFORMANCE_GUIDANCE,
        "env_var": "EVANDER_TRIGGER_CONFORMANCE_REPORT",
        "suite_path": "tests/test_trigger_conformance_suite.py",
    },
    "effect": {
        "kind": "effect",
        "label": "Effect",
        "artifact_path": "effect-conformance-report.json",
        "guidance": EFFECT_CONFORMANCE_GUIDANCE,
        "env_var": "EVANDER_EFFECT_CONFORMANCE_REPORT",
        "suite_path": "tests/test_effect_conformance_suite.py",
    },
}


def _trim_conformance_text(
    value: object, *, limit: int = CONFORMANCE_REPORT_TEXT_LIMIT
) -> str | None:
    text = _coerce_optional_string(value)
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _trim_conformance_process_output(value: object, *, limit: int = 2000) -> str:
    text = _coerce_optional_string(value)
    if text is None:
        return ""
    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized
    return f"...{normalized[-limit:].lstrip()}"


def _coerce_conformance_process_stream(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace").strip()
    else:
        text = str(value).strip()
    return text or None


def _join_conformance_process_output(*values: object) -> str:
    parts = [_coerce_conformance_process_stream(value) for value in values]
    return "\n".join(part for part in parts if part)


_resolve_path_setting = process_helpers.resolve_path_setting


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _merge_run_node_activity(items: object) -> list[WorkflowRunNodeActivityPayload]:
    if not isinstance(items, list):
        return []
    merged: dict[tuple[str, str], WorkflowRunNodeActivityPayload] = {}
    for item in items:
        try:
            activity = (
                item
                if isinstance(item, WorkflowRunNodeActivityPayload)
                else WorkflowRunNodeActivityPayload.model_validate(item)
            )
        except ValidationError:
            continue
        key = (activity.node_id, activity.status)
        existing = merged.get(key)
        if existing is None:
            merged[key] = activity.model_copy(deep=True)
            continue
        existing.count += activity.count
        if existing.started_at is None or (
            activity.started_at is not None and activity.started_at < existing.started_at
        ):
            existing.started_at = activity.started_at
        if existing.finished_at is None or (
            activity.finished_at is not None and activity.finished_at > existing.finished_at
        ):
            existing.finished_at = activity.finished_at
        if existing.source is None and activity.source is not None:
            existing.source = activity.source
        if existing.dispatch_id is None and activity.dispatch_id is not None:
            existing.dispatch_id = activity.dispatch_id
    order = {"running": 0, "waiting": 1, "inflight": 2, "pending": 3}
    return sorted(
        merged.values(),
        key=lambda item: (
            order.get(item.status, 99),
            -(item.count or 0),
            item.node_id,
        ),
    )
