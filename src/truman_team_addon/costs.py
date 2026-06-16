from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

from agent_sandbox.model_usage.models import ModelInferenceUsageEvent
from agent_sandbox.model_usage.pricing import (
    ModelCostEstimate,
    ModelPricingCatalog,
    default_model_pricing_catalog,
    estimate_model_usage_cost,
    extract_cache_write_input_tokens,
    extract_cached_input_tokens,
)
from agent_sandbox.model_usage.store import ModelUsageStore
from agent_sandbox.models import utc_now

from .models import EphemeralAssignment, PersistentAgent, TeamState

CLAUDE_TRANSCRIPT_ROOTS_ENV = "AGENT_SANDBOX_CLAUDE_TRANSCRIPT_ROOTS"
WORKER_ROOT_ENV = "AGENT_SANDBOX_WORKER_ROOT"
DEFAULT_WORKER_ROOT = Path("/var/lib/agent-worker")
TRANSCRIPT_SOURCE_NAME = "claude_transcript_backfill"
DEFAULT_REMOTE_AGENT_HOME = "/home/agent"
REMOTE_TRANSCRIPT_SCAN_TIMEOUT_SECONDS = 20
SSH_CONNECT_TIMEOUT_SECONDS = 5
_SSH_HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_SSH_USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")

_REMOTE_CLAUDE_TRANSCRIPT_SCAN_SCRIPT = r"""
import json
import sys
from pathlib import Path


def optional_int(value):
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def optional_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def first_text(*values):
    for value in values:
        text = optional_text(value)
        if text is not None:
            return text
    return None


def sanitized_usage(usage):
    if not isinstance(usage, dict):
        return {}
    allowed_scalar_keys = {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "service_tier",
    }
    sanitized = {}
    for key in allowed_scalar_keys:
        value = usage.get(key)
        if value is None:
            continue
        if key == "service_tier":
            text = optional_text(value)
            if text:
                sanitized[key] = text
            continue
        number = optional_int(value)
        if number is not None:
            sanitized[key] = number
    cache_creation = usage.get("cache_creation")
    if isinstance(cache_creation, dict):
        cache_payload = {}
        for key in ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"):
            number = optional_int(cache_creation.get(key))
            if number is not None:
                cache_payload[key] = number
        if cache_payload:
            sanitized["cache_creation"] = cache_payload
    return sanitized


def usage_contains_billable_tokens(usage):
    if not usage:
        return False
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        number = optional_int(usage.get(key))
        if number is not None and number > 0:
            return True
    cache_creation = usage.get("cache_creation")
    if not isinstance(cache_creation, dict):
        return False
    return any(
        number is not None and number > 0
        for number in (
            optional_int(cache_creation.get(key))
            for key in ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")
        )
    )


agent_home = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/home/agent")
roots = [agent_home / ".claude" / "projects", agent_home / ".claude" / "sessions"]
paths = []
for root in roots:
    if not root.is_dir():
        continue
    try:
        paths.extend(path for path in root.rglob("*.jsonl") if path.is_file())
    except OSError as exc:
        print(
            json.dumps(
                {
                    "__team_cost_marker": "error",
                    "path": str(root),
                    "error": str(exc),
                },
                separators=(",", ":"),
            )
        )

deduped_paths = sorted(set(paths), key=lambda item: (item.stat().st_mtime, str(item)))
for path in deduped_paths:
    print(
        json.dumps(
            {"__team_cost_marker": "file", "path": str(path)},
            separators=(",", ":"),
        )
    )
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError as exc:
        print(
            json.dumps(
                {
                    "__team_cost_marker": "error",
                    "path": str(path),
                    "error": str(exc),
                },
                separators=(",", ":"),
            )
        )
        continue
    with handle:
        for line_number, raw_line in enumerate(handle, start=1):
            try:
                event = json.loads(raw_line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            message = event.get("message")
            message_payload = message if isinstance(message, dict) else event
            usage = message_payload.get("usage")
            if not isinstance(usage, dict):
                usage = event.get("usage")
            usage_payload = sanitized_usage(usage)
            if not usage_contains_billable_tokens(usage_payload):
                continue
            model = first_text(message_payload.get("model"), event.get("model"))
            print(
                json.dumps(
                    {
                        "__team_cost_marker": "record",
                        "__source_path": str(path),
                        "__line_number": line_number,
                        "sessionId": first_text(
                            event.get("sessionId"),
                            event.get("session_id"),
                            event.get("conversation_id"),
                        ),
                        "requestId": first_text(
                            event.get("requestId"),
                            event.get("request_id"),
                            event.get("requestID"),
                            message_payload.get("request_id"),
                            message_payload.get("requestId"),
                        ),
                        "timestamp": first_text(
                            event.get("timestamp"),
                            event.get("created_at"),
                            message_payload.get("created_at"),
                        ),
                        "uuid": first_text(event.get("uuid")),
                        "id": first_text(event.get("id")),
                        "model": model,
                        "message": {
                            "id": first_text(
                                message_payload.get("id"),
                                event.get("message_id"),
                            ),
                            "model": model,
                            "usage": usage_payload,
                        },
                    },
                    separators=(",", ":"),
                )
            )
"""


@dataclass(frozen=True)
class TeamCostAttribution:
    team_id: str | None
    project_id: str | None
    agent_id: str | None
    source: str
    project_confident: bool


@dataclass(frozen=True)
class ClaudeTranscriptSource:
    session_id: str
    agent_id: str | None
    team_id: str | None
    project_id: str | None
    agent_home: Path | None = None
    session_manifest: Any | None = None
    remote_host: str | None = None
    remote_host_alias: str | None = None
    remote_user: str = "agent"
    remote_agent_home: str = DEFAULT_REMOTE_AGENT_HOME

    @property
    def is_remote(self) -> bool:
        return bool(self.remote_host)


@dataclass(frozen=True)
class _RemoteClaudeTranscriptEntry:
    kind: str
    path: str
    line_number: int | None = None
    payload: str | None = None
    error: str | None = None


@dataclass
class ClaudeTranscriptBackfillResult:
    sources_checked: int = 0
    sources_missing: int = 0
    files_scanned: int = 0
    records_seen: int = 0
    records_imported: int = 0
    duplicate_records: int = 0
    records_skipped: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def coverage_status(self) -> str:
        if self.sources_checked == 0 or self.sources_missing or self.errors:
            return "partial"
        return "complete"

    def model_dump(self) -> dict[str, Any]:
        return {
            "coverage_status": self.coverage_status,
            "sources_checked": self.sources_checked,
            "sources_missing": self.sources_missing,
            "files_scanned": self.files_scanned,
            "records_seen": self.records_seen,
            "records_imported": self.records_imported,
            "duplicate_records": self.duplicate_records,
            "records_skipped": self.records_skipped,
            "errors": list(self.errors),
        }


class TeamCostService:
    def __init__(
        self,
        *,
        runtime_dir: Path | str,
        state: TeamState,
        session_repository: Any | None = None,
        usage_store: ModelUsageStore | None = None,
        pricing_catalog: ModelPricingCatalog | None = None,
    ) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.state = state
        self.session_repository = session_repository
        self.usage_store = usage_store or ModelUsageStore(self.runtime_dir / "model-usage.db")
        self._pricing_catalog = pricing_catalog

    def costs_payload(self, *, backfill_transcripts: bool = True) -> dict[str, Any]:
        backfill_result = (
            self.backfill_claude_transcripts() if backfill_transcripts else None
        )
        events = _dedupe_cost_events(self.usage_store.list_events())
        catalog = self._load_pricing_catalog()

        team_buckets: dict[str, dict[str, Any]] = {}
        project_buckets: dict[str, dict[str, Any]] = {}
        unattributed_bucket = _new_bucket()
        root_bucket = _new_bucket()

        for event in events:
            estimate = estimate_model_usage_cost(event, catalog, currency="usd")
            attribution = self._attribute_event(event)
            source = _usage_source(event)

            _add_event_to_bucket(root_bucket, event, estimate, source=source)
            if attribution.team_id:
                team_bucket = team_buckets.setdefault(attribution.team_id, _new_bucket())
                _add_event_to_bucket(team_bucket, event, estimate, source=source)
                if attribution.project_id and attribution.project_confident:
                    project_bucket = project_buckets.setdefault(
                        attribution.project_id,
                        _new_bucket(team_id=attribution.team_id, project_id=attribution.project_id),
                    )
                    _add_event_to_bucket(project_bucket, event, estimate, source=source)
                else:
                    _add_event_to_bucket(unattributed_bucket, event, estimate, source=source)
                    team_unattributed = team_bucket.setdefault(
                        "unattributed_costs",
                        _new_bucket(team_id=attribution.team_id),
                    )
                    _add_event_to_bucket(team_unattributed, event, estimate, source=source)
                continue

            _add_event_to_bucket(unattributed_bucket, event, estimate, source=source)

        for team_id, team in self.state.teams.items():
            if team_id not in team_buckets and team.max_cost_usd is not None:
                team_buckets[team_id] = _new_bucket(team_id=team_id)

        for team_id, team_bucket in team_buckets.items():
            team_bucket["team_id"] = team_id
            team_bucket["project_costs"] = {
                project_id: _finalize_bucket(project_bucket)
                for project_id, project_bucket in sorted(project_buckets.items())
                if project_bucket.get("team_id") == team_id
            }
            if "unattributed_costs" in team_bucket:
                team_bucket["unattributed_costs"] = _finalize_bucket(
                    team_bucket["unattributed_costs"]
                )
            else:
                team_bucket["unattributed_costs"] = _finalize_bucket(
                    _new_bucket(team_id=team_id)
                )

        coverage_reasons = set(root_bucket.get("_coverage_reasons", set()))
        if backfill_result is not None and backfill_result.coverage_status != "complete":
            coverage_reasons.add("transcript_backfill_partial")

        payload = {
            "generated_at": utc_now().isoformat().replace("+00:00", "Z"),
            "coverage_status": "partial" if coverage_reasons else "complete",
            "coverage_reasons": sorted(coverage_reasons),
            "team_costs": {
                team_id: _finalize_team_bucket(
                    bucket,
                    team=self.state.teams.get(team_id),
                )
                for team_id, bucket in sorted(team_buckets.items())
            },
            "project_costs": {
                project_id: _finalize_bucket(bucket)
                for project_id, bucket in sorted(project_buckets.items())
            },
            "unattributed_costs": _finalize_bucket(unattributed_bucket),
            "source_counts": dict(sorted(root_bucket.get("source_counts", {}).items())),
            "events_count": int(root_bucket.get("events_count", 0)),
            "priced_events_count": int(root_bucket.get("priced_events_count", 0)),
            "unpriced_events_count": int(root_bucket.get("unpriced_events_count", 0)),
        }
        if backfill_result is not None:
            payload["backfill"] = backfill_result.model_dump()
        return payload

    def backfill_claude_transcripts(self) -> ClaudeTranscriptBackfillResult:
        return backfill_claude_transcript_usage(
            store=self.usage_store,
            sources=self._claude_transcript_sources(),
        )

    def _load_pricing_catalog(self) -> ModelPricingCatalog | None:
        if self._pricing_catalog is not None:
            return self._pricing_catalog
        try:
            return default_model_pricing_catalog()
        except Exception:
            return None

    def _attribute_event(self, event: ModelInferenceUsageEvent) -> TeamCostAttribution:
        session_id = _optional_text(event.session_id)
        dispatch_id = _optional_text(event.dispatch_id)

        if session_id:
            assignment = _ephemeral_assignment_for_session(self.state, session_id)
            if assignment is not None:
                agent = self.state.agents.get(assignment.parent_agent_id)
                team_id = agent.team_id if agent is not None else None
                return TeamCostAttribution(
                    team_id=team_id,
                    project_id=assignment.project_id,
                    agent_id=assignment.parent_agent_id,
                    source="ephemeral_session",
                    project_confident=True,
                )

            agent = _agent_for_session(self.state, session_id)
            if agent is not None:
                return TeamCostAttribution(
                    team_id=agent.team_id,
                    project_id=agent.project_id,
                    agent_id=agent.id,
                    source="agent_session",
                    project_confident=bool(agent.project_id),
                )

        if dispatch_id:
            for record in self.state.action_records.values():
                if record.dispatch_id != dispatch_id:
                    continue
                agent = self.state.agents.get(record.agent_id)
                if agent is None:
                    break
                return TeamCostAttribution(
                    team_id=agent.team_id,
                    project_id=agent.project_id,
                    agent_id=agent.id,
                    source="dispatch_action_record",
                    project_confident=bool(agent.project_id),
                )

        return TeamCostAttribution(
            team_id=None,
            project_id=None,
            agent_id=None,
            source="unattributed",
            project_confident=False,
        )

    def _claude_transcript_sources(self) -> list[ClaudeTranscriptSource]:
        sources: list[ClaudeTranscriptSource] = []
        seen: set[tuple[str, str]] = set()
        for agent in self.state.agents.values():
            session_id = _optional_text(agent.session_id)
            if session_id is None:
                continue
            session = self._session(session_id)
            _append_claude_transcript_sources(
                sources,
                seen=seen,
                runtime_dir=self.runtime_dir,
                session_id=session_id,
                session_manifest=session,
                agent_id=agent.id,
                team_id=agent.team_id,
                project_id=agent.project_id,
            )
        for assignment in self.state.ephemeral_assignments.values():
            session_id = _optional_text(assignment.session_id)
            if session_id is None:
                continue
            parent = self.state.agents.get(assignment.parent_agent_id)
            session = self._session(session_id) or assignment
            _append_claude_transcript_sources(
                sources,
                seen=seen,
                runtime_dir=self.runtime_dir,
                session_id=session_id,
                session_manifest=session,
                agent_id=assignment.parent_agent_id,
                team_id=parent.team_id if parent is not None else None,
                project_id=assignment.project_id,
            )
        return sources

    def _session(self, session_id: str) -> Any | None:
        if self.session_repository is None:
            return None
        get_session = getattr(self.session_repository, "get_session", None)
        if callable(get_session):
            return get_session(session_id)
        get = getattr(self.session_repository, "get", None)
        if callable(get):
            return get(session_id)
        read_all_sessions = getattr(self.session_repository, "read_all_sessions", None)
        if callable(read_all_sessions):
            sessions = read_all_sessions()
            if isinstance(sessions, Mapping):
                return sessions.get(session_id)
        read_all = getattr(self.session_repository, "read_all", None)
        if callable(read_all):
            sessions = read_all()
            if isinstance(sessions, Mapping):
                return sessions.get(session_id)
        return None


def backfill_claude_transcript_usage(
    *,
    store: ModelUsageStore,
    sources: Iterable[ClaudeTranscriptSource],
) -> ClaudeTranscriptBackfillResult:
    result = ClaudeTranscriptBackfillResult()
    for source in sources:
        result.sources_checked += 1
        if source.is_remote:
            _backfill_remote_claude_transcripts(store, source, result)
            continue
        if not source.agent_home or not _has_claude_transcripts(source.agent_home):
            result.sources_missing += 1
            continue
        for path in _claude_transcript_paths(source.agent_home):
            result.files_scanned += 1
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line_number, raw_line in enumerate(handle, start=1):
                        imported = _event_from_claude_transcript_line(
                            raw_line,
                            source=source,
                            path=path,
                            line_number=line_number,
                        )
                        if imported is None:
                            continue
                        result.records_seen += 1
                        if store.get_event(imported.event_id) is not None:
                            result.duplicate_records += 1
                            continue
                        store.upsert_event(imported)
                        result.records_imported += 1
            except OSError as exc:
                result.errors.append(
                    {
                        "path": str(path),
                        "error": str(exc),
                    }
                )
            except Exception as exc:
                result.errors.append(
                    {
                        "path": str(path),
                        "error": f"{exc.__class__.__name__}: {exc}",
                    }
                )
    return result


def _event_from_claude_transcript_line(
    raw_line: str,
    *,
    source: ClaudeTranscriptSource,
    path: Path,
    line_number: int,
) -> ModelInferenceUsageEvent | None:
    try:
        event = json.loads(raw_line)
    except ValueError:
        return None
    if not isinstance(event, dict):
        return None

    message = event.get("message")
    message_payload = message if isinstance(message, dict) else event
    usage = message_payload.get("usage")
    if not isinstance(usage, dict):
        usage = event.get("usage")
    if not isinstance(usage, dict) or not _usage_contains_billable_tokens(usage):
        return None

    usage_payload = _sanitized_usage_payload(usage)
    request_id = _first_text(
        event.get("requestId"),
        event.get("request_id"),
        event.get("requestID"),
        message_payload.get("request_id"),
        message_payload.get("requestId"),
    )
    message_id = _first_text(message_payload.get("id"), event.get("message_id"))
    transcript_session_id = _first_text(
        event.get("sessionId"),
        event.get("session_id"),
        event.get("conversation_id"),
        source.session_id,
    )
    event_id = _claude_transcript_event_id(
        request_id=request_id,
        message_id=message_id,
        transcript_session_id=transcript_session_id,
        event=event,
        path=path,
        line_number=line_number,
    )
    occurred_at = _optional_datetime(
        event.get("timestamp"),
        event.get("created_at"),
        message_payload.get("created_at"),
    )
    input_tokens = _optional_non_negative_int(usage_payload.get("input_tokens"))
    output_tokens = _optional_non_negative_int(usage_payload.get("output_tokens"))
    total_tokens = _optional_non_negative_int(usage_payload.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    session = source.session_manifest
    execution_backend = _session_metadata_text(session, "execution_backend")
    directory_path = _first_text(getattr(session, "directory_path", None))
    model = _first_text(
        message_payload.get("model"),
        event.get("model"),
        getattr(session, "model", None),
        _session_metadata_text(session, "model"),
    )

    return ModelInferenceUsageEvent(
        event_id=event_id,
        occurred_at=occurred_at or _file_mtime(path),
        provider="anthropic",
        provider_endpoint_identifier="messages",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        request_status="success",
        normalized_usage_status="available",
        raw_usage_metadata={
            "usage": usage_payload,
            "request_metadata": {
                "source": TRANSCRIPT_SOURCE_NAME,
                "path": "claude_transcript",
                "transcript_path": str(path),
                "line_number": line_number,
                "team_id": source.team_id,
                "project_id": source.project_id,
                "agent_id": source.agent_id,
            },
            "response_metadata": {
                "source": TRANSCRIPT_SOURCE_NAME,
                "message_id": message_id,
                "provider_request_id": request_id,
            },
        },
        session_id=source.session_id,
        agent_harness="claude",
        execution_backend=execution_backend,
        directory_path=directory_path,
        request_id=request_id,
    )


def _backfill_remote_claude_transcripts(
    store: ModelUsageStore,
    source: ClaudeTranscriptSource,
    result: ClaudeTranscriptBackfillResult,
) -> None:
    files_seen = 0
    try:
        entries = _scan_remote_claude_transcripts(source)
    except Exception as exc:
        result.errors.append(
            {
                "session_id": source.session_id,
                "remote_host": source.remote_host,
                "error": f"{exc.__class__.__name__}: {exc}",
            }
        )
        return

    for entry in entries:
        if entry.kind == "file":
            files_seen += 1
            result.files_scanned += 1
            continue
        if entry.kind == "error":
            result.errors.append(
                {
                    "session_id": source.session_id,
                    "remote_host": source.remote_host,
                    "path": entry.path,
                    "error": entry.error or "remote transcript scan failed",
                }
            )
            continue
        if entry.kind != "record" or entry.payload is None:
            result.records_skipped += 1
            continue
        imported = _event_from_claude_transcript_line(
            entry.payload,
            source=source,
            path=Path(entry.path),
            line_number=entry.line_number or 0,
        )
        if imported is None:
            result.records_skipped += 1
            continue
        result.records_seen += 1
        if store.get_event(imported.event_id) is not None:
            result.duplicate_records += 1
            continue
        store.upsert_event(imported)
        result.records_imported += 1

    if files_seen == 0:
        result.sources_missing += 1


def _scan_remote_claude_transcripts(
    source: ClaudeTranscriptSource,
) -> list[_RemoteClaudeTranscriptEntry]:
    destination = _ssh_destination(source.remote_user, source.remote_host)
    if destination is None:
        raise ValueError(f"invalid remote transcript host {source.remote_host!r}")
    remote_home = _optional_text(source.remote_agent_home) or DEFAULT_REMOTE_AGENT_HOME
    remote_command = (
        f"python3 -c {shlex.quote(_REMOTE_CLAUDE_TRANSCRIPT_SCAN_SCRIPT)} "
        f"{shlex.quote(remote_home)}"
    )
    completed = subprocess.run(
        [
            "ssh",
            *_remote_transcript_ssh_options(source),
            destination,
            remote_command,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=REMOTE_TRANSCRIPT_SCAN_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        detail = stderr or stdout or f"ssh exited with status {completed.returncode}"
        raise RuntimeError(detail)

    entries: list[_RemoteClaudeTranscriptEntry] = []
    for raw_line in completed.stdout.splitlines():
        try:
            payload = json.loads(raw_line)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        marker = _optional_text(payload.get("__team_cost_marker"))
        if marker == "file":
            path = _optional_text(payload.get("path"))
            if path:
                entries.append(_RemoteClaudeTranscriptEntry(kind="file", path=path))
            continue
        if marker == "error":
            path = _optional_text(payload.get("path")) or "remote"
            entries.append(
                _RemoteClaudeTranscriptEntry(
                    kind="error",
                    path=path,
                    error=_optional_text(payload.get("error")),
                )
            )
            continue
        if marker == "record":
            path = _optional_text(payload.get("__source_path")) or "remote"
            line_number = _optional_non_negative_int(payload.get("__line_number"))
            entries.append(
                _RemoteClaudeTranscriptEntry(
                    kind="record",
                    path=path,
                    line_number=line_number,
                    payload=json.dumps(payload, separators=(",", ":")),
                )
            )
    return entries


def _claude_transcript_event_id(
    *,
    request_id: str | None,
    message_id: str | None,
    transcript_session_id: str | None,
    event: Mapping[str, Any],
    path: Path,
    line_number: int,
) -> str:
    if request_id:
        return f"claude-transcript:{request_id}"
    if message_id:
        return f"claude-transcript:{message_id}"
    uuid_value = _first_text(event.get("uuid"), event.get("id"))
    if uuid_value:
        return f"claude-transcript:{uuid_value}"
    fingerprint = sha256(
        json.dumps(
            {
                "session_id": transcript_session_id,
                "timestamp": event.get("timestamp"),
                "path": str(path),
                "line_number": line_number,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"claude-transcript:{fingerprint}"


def _agent_home_candidates(
    *,
    runtime_dir: Path,
    session_id: str,
    session_manifest: Any | None,
) -> list[Path]:
    candidates: list[Path] = []
    for value in _session_agent_home_values(session_manifest):
        candidates.append(Path(value).expanduser())

    for root in _configured_transcript_roots(runtime_dir):
        candidates.extend(
            [
                root / session_id / "agent_home",
                root / "sessions" / session_id / "agent_home",
                root / session_id,
            ]
        )
        if (root / ".claude").exists():
            candidates.append(root)

    candidates.append(DEFAULT_WORKER_ROOT / "sessions" / session_id / "agent_home")

    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _append_claude_transcript_sources(
    sources: list[ClaudeTranscriptSource],
    *,
    seen: set[tuple[str, str]],
    runtime_dir: Path,
    session_id: str,
    session_manifest: Any | None,
    agent_id: str | None,
    team_id: str | None,
    project_id: str | None,
) -> None:
    candidates = _agent_home_candidates(
        runtime_dir=runtime_dir,
        session_id=session_id,
        session_manifest=session_manifest,
    )
    existing = [candidate for candidate in candidates if _has_claude_transcripts(candidate)]
    if not existing and not _is_claude_session(session_manifest):
        return
    if not existing:
        remote_host = _session_remote_host(session_manifest)
        if remote_host:
            key = (session_id, f"ssh://agent@{remote_host}{DEFAULT_REMOTE_AGENT_HOME}")
            if key not in seen:
                seen.add(key)
                sources.append(
                    ClaudeTranscriptSource(
                        session_id=session_id,
                        agent_id=agent_id,
                        team_id=team_id,
                        project_id=project_id,
                        agent_home=None,
                        session_manifest=session_manifest,
                        remote_host=remote_host,
                        remote_host_alias=_session_remote_host_alias(session_manifest),
                        remote_user="agent",
                        remote_agent_home=DEFAULT_REMOTE_AGENT_HOME,
                    )
                )
            return
    if not existing:
        sources.append(
            ClaudeTranscriptSource(
                session_id=session_id,
                agent_id=agent_id,
                team_id=team_id,
                project_id=project_id,
                agent_home=candidates[0] if candidates else Path(),
                session_manifest=session_manifest,
            )
        )
        return
    for agent_home in existing:
        key = (session_id, str(agent_home))
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            ClaudeTranscriptSource(
                session_id=session_id,
                agent_id=agent_id,
                team_id=team_id,
                project_id=project_id,
                agent_home=agent_home,
                session_manifest=session_manifest,
            )
        )


def _session_agent_home_values(session_manifest: Any | None) -> list[str]:
    metadata = getattr(session_manifest, "metadata", None)
    if not isinstance(metadata, Mapping):
        return []
    values: list[str] = []
    for key in (
        "agent_home_path",
        "claude_agent_home_path",
        "transcript_agent_home_path",
    ):
        value = _optional_text(metadata.get(key))
        if value:
            values.append(value)
    container_worker = metadata.get("container_worker")
    if isinstance(container_worker, Mapping):
        for key in ("agent_home_path", "host_agent_home_path"):
            value = _optional_text(container_worker.get(key))
            if value:
                values.append(value)
    return values


def _configured_transcript_roots(runtime_dir: Path) -> list[Path]:
    roots: list[Path] = []
    configured = os.environ.get(CLAUDE_TRANSCRIPT_ROOTS_ENV, "")
    for raw_entry in configured.split(os.pathsep):
        entry = raw_entry.strip()
        if entry:
            roots.append(Path(entry).expanduser())
    worker_root = os.environ.get(WORKER_ROOT_ENV, "").strip()
    if worker_root:
        roots.append(Path(worker_root).expanduser())
    roots.extend(
        [
            runtime_dir / "claude-transcripts",
            runtime_dir / "worker-sessions",
        ]
    )
    return roots


def _claude_transcript_paths(agent_home: Path) -> list[Path]:
    roots = [
        agent_home / ".claude" / "projects",
        agent_home / ".claude" / "sessions",
    ]
    paths: dict[Path, None] = {}
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for path in root.rglob("*.jsonl"):
                if path.is_file():
                    paths[path] = None
        except OSError:
            continue
    return sorted(paths, key=lambda path: (path.stat().st_mtime, str(path)))


def _has_claude_transcripts(agent_home: Path) -> bool:
    if not agent_home:
        return False
    return bool(_claude_transcript_paths(agent_home))


def _usage_contains_billable_tokens(usage: Mapping[str, Any]) -> bool:
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        number = _optional_non_negative_int(usage.get(key))
        if number is not None and number > 0:
            return True
    cache_creation = usage.get("cache_creation")
    return isinstance(cache_creation, Mapping) and any(
        number is not None and number > 0
        for number in (
            _optional_non_negative_int(cache_creation.get(key))
            for key in ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")
        )
    )


def _sanitized_usage_payload(usage: Mapping[str, Any]) -> dict[str, Any]:
    allowed_scalar_keys = {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "service_tier",
    }
    sanitized: dict[str, Any] = {}
    for key in allowed_scalar_keys:
        value = usage.get(key)
        if value is None:
            continue
        if key == "service_tier":
            text = _optional_text(value)
            if text:
                sanitized[key] = text
            continue
        number = _optional_non_negative_int(value)
        if number is not None:
            sanitized[key] = number

    cache_creation = usage.get("cache_creation")
    if isinstance(cache_creation, Mapping):
        cache_payload: dict[str, int] = {}
        for key in ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"):
            number = _optional_non_negative_int(cache_creation.get(key))
            if number is not None:
                cache_payload[key] = number
        if cache_payload:
            sanitized["cache_creation"] = cache_payload
    return sanitized


def _new_bucket(*, team_id: str | None = None, project_id: str | None = None) -> dict[str, Any]:
    bucket: dict[str, Any] = {
        "currency": "usd",
        "_estimated_cost": Decimal("0"),
        "events_count": 0,
        "priced_events_count": 0,
        "unpriced_events_count": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "source_counts": {},
        "provider_breakdown": {},
        "model_breakdown": {},
        "cost_status_counts": {},
        "_coverage_reasons": set(),
    }
    if team_id is not None:
        bucket["team_id"] = team_id
    if project_id is not None:
        bucket["project_id"] = project_id
    return bucket


def _add_event_to_bucket(
    bucket: dict[str, Any],
    event: ModelInferenceUsageEvent,
    estimate: ModelCostEstimate,
    *,
    source: str,
) -> None:
    estimated_total = estimate.estimated_total
    bucket["events_count"] += 1
    if estimated_total is not None:
        bucket["_estimated_cost"] += estimated_total
        bucket["priced_events_count"] += 1
    else:
        bucket["unpriced_events_count"] += 1
        bucket["_coverage_reasons"].add(estimate.status)
    input_tokens = int(event.input_tokens or 0)
    output_tokens = int(event.output_tokens or 0)
    total_tokens = (
        int(event.total_tokens)
        if event.total_tokens is not None
        else input_tokens + output_tokens
    )
    cached_input_tokens = int(extract_cached_input_tokens(event) or 0)
    cache_write_tokens = extract_cache_write_input_tokens(event).total
    bucket["input_tokens"] += input_tokens
    bucket["cached_input_tokens"] += cached_input_tokens
    bucket["cache_write_input_tokens"] += cache_write_tokens
    bucket["output_tokens"] += output_tokens
    bucket["total_tokens"] += total_tokens
    _increment(bucket["source_counts"], source)
    _increment(bucket["cost_status_counts"], estimate.status)

    provider_key = _optional_text(event.provider) or "unknown"
    model_key = f"{provider_key}/{_optional_text(event.model) or '<missing>'}"
    _add_event_to_leaf(
        bucket["provider_breakdown"].setdefault(provider_key, _new_leaf()),
        event,
        estimate,
        source=source,
    )
    _add_event_to_leaf(
        bucket["model_breakdown"].setdefault(model_key, _new_leaf()),
        event,
        estimate,
        source=source,
    )


def _new_leaf() -> dict[str, Any]:
    return {
        "currency": "usd",
        "_estimated_cost": Decimal("0"),
        "events_count": 0,
        "priced_events_count": 0,
        "unpriced_events_count": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "source_counts": {},
        "cost_status_counts": {},
        "_coverage_reasons": set(),
    }


def _add_event_to_leaf(
    leaf: dict[str, Any],
    event: ModelInferenceUsageEvent,
    estimate: ModelCostEstimate,
    *,
    source: str,
) -> None:
    estimated_total = estimate.estimated_total
    leaf["events_count"] += 1
    if estimated_total is not None:
        leaf["_estimated_cost"] += estimated_total
        leaf["priced_events_count"] += 1
    else:
        leaf["unpriced_events_count"] += 1
        leaf["_coverage_reasons"].add(estimate.status)
    input_tokens = int(event.input_tokens or 0)
    output_tokens = int(event.output_tokens or 0)
    leaf["input_tokens"] += input_tokens
    leaf["cached_input_tokens"] += int(extract_cached_input_tokens(event) or 0)
    leaf["cache_write_input_tokens"] += extract_cache_write_input_tokens(event).total
    leaf["output_tokens"] += output_tokens
    leaf["total_tokens"] += (
        int(event.total_tokens)
        if event.total_tokens is not None
        else input_tokens + output_tokens
    )
    _increment(leaf["source_counts"], source)
    _increment(leaf["cost_status_counts"], estimate.status)


def _finalize_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    coverage_reasons = set(bucket.get("_coverage_reasons", set()))
    finalized: dict[str, Any] = {}
    for key, value in bucket.items():
        if key == "_coverage_reasons":
            continue
        if key == "_estimated_cost":
            finalized["estimated_cost"] = _decimal_to_float(value)
            continue
        if key == "provider_breakdown":
            finalized[key] = {
                item_key: _finalize_bucket(item_value)
                for item_key, item_value in sorted(value.items())
            }
            continue
        if key == "model_breakdown":
            finalized[key] = {
                item_key: _finalize_bucket(item_value)
                for item_key, item_value in sorted(value.items())
            }
            continue
        if key in {"source_counts", "cost_status_counts"}:
            finalized[key] = dict(sorted(value.items()))
            continue
        finalized[key] = value
    finalized["coverage_status"] = "partial" if coverage_reasons else "complete"
    finalized["coverage_reasons"] = sorted(coverage_reasons)
    if "estimated_cost" not in finalized:
        finalized["estimated_cost"] = 0.0
    return finalized


def _finalize_team_bucket(bucket: dict[str, Any], *, team: Any | None) -> dict[str, Any]:
    finalized = _finalize_bucket(bucket)
    max_cost = _optional_decimal(getattr(team, "max_cost_usd", None))
    estimated = _optional_decimal(finalized.get("estimated_cost")) or Decimal("0")
    finalized["max_cost_usd"] = _decimal_to_float(max_cost)
    finalized["remaining_cost_usd"] = (
        _decimal_to_float(max(max_cost - estimated, Decimal("0")))
        if max_cost is not None
        else None
    )
    finalized["cost_limit_reached"] = bool(
        max_cost is not None and estimated >= max_cost
    )
    finalized["cost_limit_basis"] = "lifetime"
    return finalized


def _increment(counter: dict[str, int], key: str) -> None:
    counter[key] = int(counter.get(key, 0)) + 1


def _dedupe_cost_events(
    events: Iterable[ModelInferenceUsageEvent],
) -> list[ModelInferenceUsageEvent]:
    materialized = [
        event
        for event in events
        if _usage_source(event) != "transcript" or _event_has_billable_tokens(event)
    ]
    anthropic_transcript_request_keys = {
        key
        for event in materialized
        if (key := _anthropic_transcript_request_key(event)) is not None
    }
    if not anthropic_transcript_request_keys:
        return materialized
    return [
        event
        for event in materialized
        if not (
            _usage_source(event) != "transcript"
            and _optional_text(event.provider) == "anthropic"
            and _event_request_key(event) in anthropic_transcript_request_keys
        )
    ]


def _anthropic_transcript_request_key(
    event: ModelInferenceUsageEvent,
) -> tuple[str, str] | None:
    if _usage_source(event) != "transcript":
        return None
    if _optional_text(event.provider) != "anthropic":
        return None
    return _event_request_key(event)


def _event_request_key(event: ModelInferenceUsageEvent) -> tuple[str, str] | None:
    session_id = _optional_text(event.session_id)
    request_id = _first_text(
        event.request_id,
        _raw_metadata_text(
            event.raw_usage_metadata, "response_metadata", "provider_request_id"
        ),
        _raw_metadata_text(event.raw_usage_metadata, "response_metadata", "request_id"),
        _raw_metadata_text(
            event.raw_usage_metadata, "request_metadata", "provider_request_id"
        ),
        _raw_metadata_text(event.raw_usage_metadata, "request_metadata", "request_id"),
    )
    if session_id is None or request_id is None:
        return None
    return (session_id, request_id)


def _raw_metadata_text(
    raw_usage_metadata: Mapping[str, Any], section: str, key: str
) -> str | None:
    payload = raw_usage_metadata.get(section)
    if not isinstance(payload, Mapping):
        return None
    return _optional_text(payload.get(key))


def _event_has_billable_tokens(event: ModelInferenceUsageEvent) -> bool:
    for value in (
        event.input_tokens,
        event.output_tokens,
        event.total_tokens,
        extract_cached_input_tokens(event),
        extract_cache_write_input_tokens(event).total,
    ):
        number = _optional_non_negative_int(value)
        if number is not None and number > 0:
            return True
    return False


def _usage_source(event: ModelInferenceUsageEvent) -> str:
    request_metadata = event.raw_usage_metadata.get("request_metadata")
    if isinstance(request_metadata, Mapping):
        source = _optional_text(request_metadata.get("source"))
        if source == TRANSCRIPT_SOURCE_NAME:
            return "transcript"
    return "relay"


def _agent_for_session(state: TeamState, session_id: str) -> PersistentAgent | None:
    for agent in state.agents.values():
        if agent.session_id == session_id:
            return agent
    return None


def _ephemeral_assignment_for_session(
    state: TeamState, session_id: str
) -> EphemeralAssignment | None:
    for assignment in state.ephemeral_assignments.values():
        if assignment.session_id == session_id:
            return assignment
    return None


def _is_claude_session(session_manifest: Any | None) -> bool:
    if session_manifest is None:
        return False
    metadata = getattr(session_manifest, "metadata", None)
    if isinstance(metadata, Mapping):
        for key in ("agent_provider", "agent_harness", "model_provider"):
            value = _optional_text(metadata.get(key))
            if value and value.casefold() in {"claude", "anthropic"}:
                return True
        model = _optional_text(metadata.get("model"))
        if model and model.casefold().startswith("claude"):
            return True
    for key in ("agent_provider", "agent_harness", "model_provider"):
        value = _optional_text(getattr(session_manifest, key, None))
        if value and value.casefold() in {"claude", "anthropic"}:
            return True
    model = _optional_text(getattr(session_manifest, "model", None))
    return bool(model and model.casefold().startswith("claude"))


def _session_metadata_text(session_manifest: Any | None, key: str) -> str | None:
    metadata = getattr(session_manifest, "metadata", None)
    if isinstance(metadata, Mapping):
        value = _optional_text(metadata.get(key))
        if value:
            return value
    return _optional_text(getattr(session_manifest, key, None))


def _session_remote_host(session_manifest: Any | None) -> str | None:
    metadata = getattr(session_manifest, "metadata", None)
    team_addon = metadata.get("team_addon") if isinstance(metadata, Mapping) else None
    return _first_text(
        _mapping_text(metadata, "last_known_vm_ip"),
        _mapping_text(team_addon, "last_known_vm_ip"),
        getattr(session_manifest, "last_known_vm_ip", None),
    )


def _session_remote_host_alias(session_manifest: Any | None) -> str | None:
    metadata = getattr(session_manifest, "metadata", None)
    team_addon = metadata.get("team_addon") if isinstance(metadata, Mapping) else None
    alias = _first_text(
        getattr(session_manifest, "vm_id", None),
        getattr(session_manifest, "vm_provider_id", None),
        _mapping_text(metadata, "vm_id"),
        _mapping_text(metadata, "vm_name"),
        _mapping_text(metadata, "target_name"),
        _mapping_text(team_addon, "vm_id"),
        _mapping_text(team_addon, "vm_name"),
    )
    if alias is None or alias.startswith("-") or not _SSH_HOST_RE.fullmatch(alias):
        return None
    return alias


def _mapping_text(value: Any, key: str) -> str | None:
    if not isinstance(value, Mapping):
        return None
    return _optional_text(value.get(key))


def _file_mtime(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return utc_now()


def _optional_datetime(*values: Any) -> datetime | None:
    for value in values:
        text = _optional_text(value)
        if text is None:
            continue
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return None


def _optional_non_negative_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _first_text(*values: Any) -> str | None:
    for value in values:
        text = _optional_text(value)
        if text is not None:
            return text
    return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_decimal(value: Any) -> Decimal | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        return Decimal(text)
    except Exception:
        return None


def _ssh_destination(user: str | None, host: str | None) -> str | None:
    normalized_user = _optional_text(user) or "agent"
    normalized_host = _optional_text(host)
    if normalized_host is None:
        return None
    if normalized_user.startswith("-") or not _SSH_USER_RE.fullmatch(normalized_user):
        return None
    if normalized_host.startswith("-") or not _SSH_HOST_RE.fullmatch(normalized_host):
        return None
    return f"{normalized_user}@{normalized_host}"


def _remote_transcript_ssh_options(source: ClaudeTranscriptSource) -> list[str]:
    options = [
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={SSH_CONNECT_TIMEOUT_SECONDS}",
    ]
    private_key_path = _resolve_private_key_path()
    if private_key_path is not None:
        options.extend(
            [
                "-o",
                "IdentitiesOnly=yes",
                "-i",
                str(private_key_path),
            ]
        )
    elif os.environ.get("SSH_PRIVATE_KEY_PATH", "").strip():
        raise RuntimeError(
            f"configured SSH private key not found: {os.environ['SSH_PRIVATE_KEY_PATH']}"
        )

    known_hosts_path = _known_hosts_path()
    options.extend(["-o", f"UserKnownHostsFile={known_hosts_path}"])

    alias = _optional_text(source.remote_host_alias)
    if alias and not alias.startswith("-") and _SSH_HOST_RE.fullmatch(alias):
        options.extend(
            [
                "-o",
                f"HostKeyAlias={alias}",
                "-o",
                "CheckHostIP=no",
            ]
        )
    return options


def _resolve_private_key_path() -> Path | None:
    configured = os.environ.get("SSH_PRIVATE_KEY_PATH", "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_file() else None

    home = _caller_home()
    for candidate in (home / ".ssh" / "id_ed25519", home / ".ssh" / "id_rsa"):
        if candidate.is_file():
            return candidate
    return None


def _known_hosts_path() -> Path:
    configured = os.environ.get("AGENT_SANDBOX_GUEST_KNOWN_HOSTS_FILE", "").strip()
    if configured:
        path = Path(configured).expanduser()
    else:
        path = _caller_home() / ".ssh" / "agent-sandbox-known_hosts"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path
    except OSError:
        fallback = (
            Path("/tmp")
            / f"agent-sandbox-known_hosts-{os.environ.get('SUDO_USER') or os.environ.get('USER') or 'agent'}"
        )
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.touch(exist_ok=True)
        try:
            fallback.chmod(0o600)
        except OSError:
            pass
        return fallback


def _caller_home() -> Path:
    caller_user = os.environ.get("SUDO_USER") or os.environ.get("USER") or ""
    caller_home = os.environ.get("HOME", "")
    if caller_user:
        try:
            passwd_entry = subprocess.run(
                ["getent", "passwd", caller_user],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            passwd_entry = None
        if passwd_entry and passwd_entry.returncode == 0:
            fields = (passwd_entry.stdout or "").strip().split(":")
            if len(fields) >= 6 and fields[5]:
                caller_home = fields[5]
    return Path(caller_home or Path.home())


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


__all__ = [
    "CLAUDE_TRANSCRIPT_ROOTS_ENV",
    "TeamCostService",
    "ClaudeTranscriptBackfillResult",
    "ClaudeTranscriptSource",
    "backfill_claude_transcript_usage",
]
