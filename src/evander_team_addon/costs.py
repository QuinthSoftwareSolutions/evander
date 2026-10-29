from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any

from evander.model_usage.cost_balances import (
    UsageCostSummary,
    dedupe_model_usage_cost_events,
    model_usage_request_key,
    model_usage_source,
)
from evander.model_usage.models import (
    ModelInferenceUsageEvent,
    ModelUsageCorrelationContext,
)
from evander.model_usage.pricing import (
    ModelCostEstimate,
    ModelPricingCatalog,
    default_model_pricing_catalog,
    extract_cache_write_input_tokens,
    extract_cached_input_tokens,
    model_pricing_catalog_fingerprint,
)
from evander.model_usage.store import ModelUsageStore
from evander.model_usage.report_snapshot import CostReportSnapshot
from evander.models import utc_now

from .models import EphemeralAssignment, PersistentAgent, TeamState, agent_project_ids
from .cost_report import add_cell_to_bucket, cost_report_cells

CLAUDE_TRANSCRIPT_ROOTS_ENV = "EVANDER_CLAUDE_TRANSCRIPT_ROOTS"
WORKER_ROOT_ENV = "EVANDER_WORKER_ROOT"
DEFAULT_WORKER_ROOT = Path("/var/lib/agent-worker")
TRANSCRIPT_SOURCE_NAME = "claude_transcript_backfill"
DEFAULT_REMOTE_AGENT_HOME = "/home/agent"
REMOTE_TRANSCRIPT_SCAN_TIMEOUT_SECONDS = 20
REMOTE_TRANSCRIPT_STALE_HOST_TTL_SECONDS = 300
SSH_CONNECT_TIMEOUT_SECONDS = 5
COST_BALANCE_RECONCILIATION_INTERVAL_SECONDS = 15 * 60
_LIFETIME_BALANCE_START = datetime.min.replace(tzinfo=UTC)
_SSH_HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_SSH_USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_REMOTE_TRANSCRIPT_FAILURES: dict[tuple[str, str, str], float] = {}
_REMOTE_TRANSCRIPT_FAILURES_LOCK = Lock()

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
        self._context_cache: dict[str, ModelUsageCorrelationContext | None] = {}
        self._request_attribution_cache: dict[tuple[str, str], TeamCostAttribution] = {}
        self._cost_report_snapshot: CostReportSnapshot | None = None

    def costs_payload(
        self,
        *,
        backfill_transcripts: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        backfill_result = self.backfill_claude_transcripts() if backfill_transcripts else None
        catalog = self._load_pricing_catalog()
        effective_now = _ensure_utc(now or utc_now())
        team_budget_windows = {
            team_id: _current_team_budget_window(team, now=effective_now)
            for team_id, team in self.state.teams.items()
        }

        team_buckets: dict[str, dict[str, Any]] = {}
        team_budget_window_buckets: dict[str, dict[str, Any]] = {}
        project_buckets: dict[str, dict[str, Any]] = {}
        unattributed_bucket = _new_bucket()
        root_bucket = _new_bucket()

        for cell, amounts in cost_report_cells(self, catalog, team_budget_windows):
            team_id, project_id, _, _, _, _, in_window = cell
            add_cell_to_bucket(root_bucket, cell, amounts)
            if team_id:
                team_bucket = team_buckets.setdefault(team_id, _new_bucket())
                add_cell_to_bucket(team_bucket, cell, amounts)
                if in_window:
                    budget_bucket = team_budget_window_buckets.setdefault(
                        team_id,
                        _new_bucket(),
                    )
                    add_cell_to_bucket(budget_bucket, cell, amounts)
                if project_id:
                    project_bucket = project_buckets.setdefault(
                        project_id,
                        _new_bucket(team_id=team_id, project_id=project_id),
                    )
                    add_cell_to_bucket(project_bucket, cell, amounts)
                else:
                    add_cell_to_bucket(unattributed_bucket, cell, amounts)
                    team_unattributed = team_bucket.setdefault(
                        "unattributed_costs",
                        _new_bucket(team_id=team_id),
                    )
                    add_cell_to_bucket(team_unattributed, cell, amounts)
                continue

            add_cell_to_bucket(unattributed_bucket, cell, amounts)

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
                team_bucket["unattributed_costs"] = _finalize_bucket(_new_bucket(team_id=team_id))

        coverage_reasons = set(root_bucket.get("_coverage_reasons", set()))
        if backfill_result is not None and backfill_result.coverage_status != "complete":
            coverage_reasons.add("transcript_backfill_partial")

        team_cost_payloads = {
            team_id: _finalize_team_bucket(
                bucket,
                team=self.state.teams.get(team_id),
                budget_window=team_budget_windows.get(team_id),
                budget_window_bucket=team_budget_window_buckets.get(team_id),
            )
            for team_id, bucket in sorted(team_buckets.items())
        }
        payload = {
            "generated_at": utc_now().isoformat().replace("+00:00", "Z"),
            "coverage_status": "partial" if coverage_reasons else "complete",
            "coverage_reasons": sorted(coverage_reasons),
            "team_costs": team_cost_payloads,
            "governance_group_costs": _governance_group_costs(
                self.state,
                team_cost_payloads=team_cost_payloads,
            ),
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

    def current_budget_costs_payload(
        self,
        *,
        team_ids: Iterable[str] | None = None,
        now: datetime | None = None,
        max_reconciliation_age_seconds: int | None = None,
        backfill_transcripts_when_reconciling: bool = False,
        include_uncapped_teams: bool = False,
    ) -> dict[str, Any]:
        """Read incrementally maintained active-window balances for wake-time decisions.

        A missing, dirty, repriced, or periodically stale balance is rebuilt from only
        that team's current budget-window interval. Ordinary reads never enumerate
        model usage once a balance is healthy.
        """
        effective_now = _ensure_utc(now or utc_now())
        selected_ids = (
            {
                normalized
                for team_id in team_ids
                if (normalized := _optional_text(team_id)) is not None
            }
            if team_ids is not None
            else set(self.state.teams)
        )
        selected_teams = {
            team_id: team
            for team_id, team in self.state.teams.items()
            if team_id in selected_ids and (include_uncapped_teams or team.max_cost_usd is not None)
        }
        self._sync_incremental_cost_attributions()
        catalog = self._load_pricing_catalog()
        if catalog is None:
            return {
                "generated_at": _json_datetime(effective_now),
                "coverage_status": "partial",
                "coverage_reasons": ["pricing_unavailable"],
                "team_costs": {
                    team_id: self._incremental_team_cost_payload(
                        team=team,
                        window=self._incremental_team_window(team, now=effective_now),
                        summary=UsageCostSummary(),
                        balance=None,
                        coverage_reasons=["pricing_unavailable"],
                    )
                    for team_id, team in sorted(selected_teams.items())
                },
                "reconciled_team_ids": [],
            }

        pricing_fingerprint = model_pricing_catalog_fingerprint(catalog)
        plans: list[dict[str, Any]] = []
        for team_id, team in sorted(selected_teams.items()):
            window = self._incremental_team_window(team, now=effective_now)
            balance = self.usage_store.get_cost_balance(
                dimension_kind="team",
                dimension_id=team_id,
                window_id=window["window_id"],
            )
            reason = self._balance_reconciliation_reason(
                balance,
                window=window,
                pricing_fingerprint=pricing_fingerprint,
                now=effective_now,
                max_age_seconds=max_reconciliation_age_seconds,
            )
            plans.append(
                {
                    "team_id": team_id,
                    "team": team,
                    "window": window,
                    "balance": balance,
                    "reconciliation_reason": reason,
                }
            )

        backfill_result = None
        if backfill_transcripts_when_reconciling and any(
            plan["reconciliation_reason"] is not None for plan in plans
        ):
            backfill_result = self.backfill_claude_transcripts()

        reconciled_team_ids: list[str] = []
        team_costs: dict[str, dict[str, Any]] = {}
        for plan in plans:
            team_id = plan["team_id"]
            balance = plan["balance"]
            if plan["reconciliation_reason"] is not None:
                balance = self._reconcile_incremental_team_balance(
                    dimension_kind="team",
                    team_id=team_id,
                    window=plan["window"],
                    catalog=catalog,
                    now=effective_now,
                )
                reconciled_team_ids.append(team_id)
            summary = balance["summary"] if balance is not None else UsageCostSummary()
            team_costs[team_id] = self._incremental_team_cost_payload(
                team=plan["team"],
                window=plan["window"],
                summary=summary,
                balance=balance,
            )

        coverage_reasons = sorted(
            {reason for cost in team_costs.values() for reason in cost.get("coverage_reasons", [])}
        )
        if backfill_result is not None and backfill_result.coverage_status != "complete":
            coverage_reasons.append("transcript_backfill_partial")
            coverage_reasons = sorted(set(coverage_reasons))
        payload: dict[str, Any] = {
            "generated_at": _json_datetime(effective_now),
            "coverage_status": "partial" if coverage_reasons else "complete",
            "coverage_reasons": coverage_reasons,
            "team_costs": team_costs,
            "reconciled_team_ids": reconciled_team_ids,
        }
        if backfill_result is not None:
            payload["backfill"] = backfill_result.model_dump()
        return payload

    def governance_costs_payload(
        self,
        *,
        team_ids: Iterable[str] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return incremental active-window and lifetime Team costs for governance.

        Governance retains lifetime advisory context for every member Team, while
        cap decisions continue to use the active budget window. Lifetime balances
        are bootstrapped once and then receive the same transactional event deltas
        as active-window balances.
        """
        effective_now = _ensure_utc(now or utc_now())
        selected_ids = (
            {
                normalized
                for team_id in team_ids
                if (normalized := _optional_text(team_id)) is not None
            }
            if team_ids is not None
            else set(self.state.teams)
        )
        active_payload = self.current_budget_costs_payload(
            team_ids=selected_ids,
            now=effective_now,
            include_uncapped_teams=True,
        )
        catalog = self._load_pricing_catalog()
        pricing_fingerprint = (
            model_pricing_catalog_fingerprint(catalog) if catalog is not None else None
        )
        team_costs: dict[str, dict[str, Any]] = {}
        reconciled_lifetime_team_ids: list[str] = []
        lifetime_coverage_reasons: set[str] = set()
        active_team_costs = active_payload.get("team_costs")
        active_team_costs = active_team_costs if isinstance(active_team_costs, Mapping) else {}
        for team_id, team in sorted(self.state.teams.items()):
            if team_id not in selected_ids:
                continue
            active_cost = dict(active_team_costs.get(team_id) or {})
            active_window = self._incremental_team_window(team, now=effective_now)
            lifetime_balance = None
            if catalog is not None and pricing_fingerprint is not None:
                if active_window.get("explicit"):
                    lifetime_window = self._lifetime_team_window(team)
                    lifetime_balance = self.usage_store.get_cost_balance(
                        dimension_kind="team_lifetime",
                        dimension_id=team_id,
                        window_id=lifetime_window["window_id"],
                    )
                    reason = self._balance_reconciliation_reason(
                        lifetime_balance,
                        window=lifetime_window,
                        pricing_fingerprint=pricing_fingerprint,
                        now=effective_now,
                        max_age_seconds=None,
                    )
                    if reason is not None:
                        lifetime_balance = self._reconcile_incremental_team_balance(
                            dimension_kind="team_lifetime",
                            team_id=team_id,
                            window=lifetime_window,
                            catalog=catalog,
                            now=effective_now,
                        )
                        reconciled_lifetime_team_ids.append(team_id)
                else:
                    lifetime_balance = self.usage_store.get_cost_balance(
                        dimension_kind="team",
                        dimension_id=team_id,
                        window_id=active_window["window_id"],
                    )
            lifetime_summary = (
                lifetime_balance["summary"] if lifetime_balance is not None else UsageCostSummary()
            )
            team_cost = self._governance_team_cost_payload(
                team=team,
                active_cost=active_cost,
                lifetime_summary=lifetime_summary,
                lifetime_balance=lifetime_balance,
            )
            team_costs[team_id] = team_cost
            lifetime_coverage_reasons.update(team_cost.get("coverage_reasons") or [])

        coverage_reasons = sorted(
            set(active_payload.get("coverage_reasons") or []) | lifetime_coverage_reasons
        )
        payload: dict[str, Any] = {
            "generated_at": _json_datetime(effective_now),
            "coverage_status": "partial" if coverage_reasons else "complete",
            "coverage_reasons": coverage_reasons,
            "team_costs": team_costs,
            "governance_group_costs": _governance_group_costs(
                self.state,
                team_cost_payloads=team_costs,
            ),
            "reconciled_team_ids": list(active_payload.get("reconciled_team_ids") or []),
            "reconciled_lifetime_team_ids": reconciled_lifetime_team_ids,
        }
        if "backfill" in active_payload:
            payload["backfill"] = active_payload["backfill"]
        return payload

    def reconcile_current_cost_balances(
        self,
        *,
        team_ids: Iterable[str] | None = None,
        now: datetime | None = None,
        max_age_seconds: int = COST_BALANCE_RECONCILIATION_INTERVAL_SECONDS,
        backfill_transcripts: bool = True,
    ) -> dict[str, Any]:
        return self.current_budget_costs_payload(
            team_ids=team_ids,
            now=now,
            max_reconciliation_age_seconds=max(0, int(max_age_seconds)),
            backfill_transcripts_when_reconciling=backfill_transcripts,
        )

    def _sync_incremental_cost_attributions(self) -> None:
        attributions: dict[str, tuple[str | None, str | None, str | None]] = {}
        for agent in self.state.agents.values():
            project_ids = agent_project_ids(self.state, agent.id)
            attributions[agent.session_id] = (
                agent.team_id,
                project_ids[0] if project_ids else None,
                agent.id,
            )
        for assignment in self.state.ephemeral_assignments.values():
            parent = self.state.agents.get(assignment.parent_agent_id)
            attributions[assignment.session_id] = (
                parent.team_id if parent is not None else None,
                assignment.project_id,
                assignment.parent_agent_id,
            )
        self.usage_store.upsert_cost_attributions(
            [
                {
                    "session_id": session_id,
                    "team_id": team_id,
                    "project_id": project_id,
                    "agent_id": agent_id,
                }
                for session_id, (team_id, project_id, agent_id) in attributions.items()
            ]
        )

    @staticmethod
    def _incremental_team_window(team: Any, *, now: datetime) -> dict[str, Any]:
        active = _current_team_budget_window(team, now=now)
        if active is not None:
            return {**active, "explicit": True}
        return {
            "window_id": f"legacy-lifetime:{team.id}",
            "started_at": _LIFETIME_BALANCE_START,
            "ends_at": None,
            "renewal_mode": "manual",
            "explicit": False,
        }

    @staticmethod
    def _lifetime_team_window(team: Any) -> dict[str, Any]:
        return {
            "window_id": f"lifetime:{team.id}",
            "started_at": _LIFETIME_BALANCE_START,
            "ends_at": None,
            "renewal_mode": "manual",
            "explicit": False,
        }

    @staticmethod
    def _balance_reconciliation_reason(
        balance: Mapping[str, Any] | None,
        *,
        window: Mapping[str, Any],
        pricing_fingerprint: str,
        now: datetime,
        max_age_seconds: int | None,
    ) -> str | None:
        if balance is None:
            return "missing"
        if balance.get("dirty_reason"):
            return str(balance["dirty_reason"])
        if balance.get("pricing_fingerprint") != pricing_fingerprint:
            return "pricing_changed"
        if _ensure_utc(balance["started_at"]) != _ensure_utc(window["started_at"]):
            return "window_changed"
        balance_end = balance.get("ended_at")
        window_end = window.get("ends_at")
        if (balance_end is None) != (window_end is None) or (
            balance_end is not None
            and window_end is not None
            and _ensure_utc(balance_end) != _ensure_utc(window_end)
        ):
            return "window_changed"
        if max_age_seconds is not None:
            age_seconds = (now - _ensure_utc(balance["reconciled_at"])).total_seconds()
            if age_seconds >= max_age_seconds:
                return "periodic"
        return None

    def _reconcile_incremental_team_balance(
        self,
        *,
        dimension_kind: str,
        team_id: str,
        window: Mapping[str, Any],
        catalog: ModelPricingCatalog,
        now: datetime,
    ) -> dict[str, Any]:
        return self.usage_store.reconcile_team_cost_balance(
            dimension_kind=dimension_kind,
            team_id=team_id,
            window_id=window["window_id"],
            started_at=window["started_at"],
            ended_at=window.get("ends_at"),
            pricing_catalog=catalog,
            reconciled_at=now,
        )

    @staticmethod
    def _incremental_team_cost_payload(
        *,
        team: Any,
        window: Mapping[str, Any],
        summary: UsageCostSummary,
        balance: Mapping[str, Any] | None,
        coverage_reasons: Iterable[str] = (),
    ) -> dict[str, Any]:
        payload = summary.public_payload()
        reasons = sorted(set(payload["coverage_reasons"]) | set(coverage_reasons))
        estimated = summary.estimated_cost_usd
        reported = summary.reported_cost_usd
        total = summary.total_cost_usd
        max_cost = Decimal(str(team.max_cost_usd)) if team.max_cost_usd is not None else None
        explicit_window = bool(window.get("explicit"))
        payload.update(
            {
                "team_id": team.id,
                "coverage_status": "partial" if reasons else "complete",
                "coverage_reasons": reasons,
                "max_cost_usd": float(max_cost) if max_cost is not None else None,
                "remaining_cost_usd": (
                    float(max(max_cost - total, Decimal("0"))) if max_cost is not None else None
                ),
                "cost_limit_reached": bool(max_cost is not None and total >= max_cost),
                "cost_limit_basis": "budget_window" if explicit_window else "lifetime",
                "lifetime_estimated_cost": float(estimated) if not explicit_window else None,
                "budget_window_estimated_cost": float(estimated) if explicit_window else None,
                "lifetime_reported_cost": float(reported) if not explicit_window else None,
                "budget_window_reported_cost": float(reported) if explicit_window else None,
                "lifetime_total_cost": float(total) if not explicit_window else None,
                "budget_window_total_cost": float(total) if explicit_window else None,
                "budget_window_events_count": summary.events_count if explicit_window else 0,
                "budget_window_started_at": (
                    _json_datetime(window["started_at"]) if explicit_window else None
                ),
                "budget_window_ends_at": (
                    _json_datetime(window.get("ends_at")) if explicit_window else None
                ),
                "budget_window_renewal_mode": (
                    window.get("renewal_mode") if explicit_window else None
                ),
                "balance_window_id": window["window_id"],
                "balance_updated_at": (
                    _json_datetime(balance.get("updated_at")) if balance is not None else None
                ),
                "balance_reconciled_at": (
                    _json_datetime(balance.get("reconciled_at")) if balance is not None else None
                ),
            }
        )
        return payload

    @staticmethod
    def _governance_team_cost_payload(
        *,
        team: Any,
        active_cost: Mapping[str, Any],
        lifetime_summary: UsageCostSummary,
        lifetime_balance: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        payload = lifetime_summary.public_payload()
        coverage_reasons = sorted(
            set(payload.get("coverage_reasons") or [])
            | set(active_cost.get("coverage_reasons") or [])
        )
        payload.update(
            {
                "team_id": team.id,
                "coverage_status": "partial" if coverage_reasons else "complete",
                "coverage_reasons": coverage_reasons,
                "max_cost_usd": active_cost.get("max_cost_usd"),
                "remaining_cost_usd": active_cost.get("remaining_cost_usd"),
                "cost_limit_reached": bool(active_cost.get("cost_limit_reached")),
                "cost_limit_basis": active_cost.get("cost_limit_basis"),
                "lifetime_estimated_cost": float(lifetime_summary.estimated_cost_usd),
                "budget_window_estimated_cost": active_cost.get("budget_window_estimated_cost"),
                "lifetime_reported_cost": float(lifetime_summary.reported_cost_usd),
                "budget_window_reported_cost": active_cost.get("budget_window_reported_cost"),
                "lifetime_total_cost": float(lifetime_summary.total_cost_usd),
                "budget_window_total_cost": active_cost.get("budget_window_total_cost"),
                "budget_window_events_count": int(
                    active_cost.get("budget_window_events_count") or 0
                ),
                "budget_window_started_at": active_cost.get("budget_window_started_at"),
                "budget_window_ends_at": active_cost.get("budget_window_ends_at"),
                "budget_window_renewal_mode": active_cost.get("budget_window_renewal_mode"),
                "balance_window_id": active_cost.get("balance_window_id"),
                "balance_updated_at": active_cost.get("balance_updated_at"),
                "balance_reconciled_at": active_cost.get("balance_reconciled_at"),
                "lifetime_balance_updated_at": (
                    _json_datetime(lifetime_balance.get("updated_at"))
                    if lifetime_balance is not None
                    else None
                ),
                "lifetime_balance_reconciled_at": (
                    _json_datetime(lifetime_balance.get("reconciled_at"))
                    if lifetime_balance is not None
                    else None
                ),
            }
        )
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
            return default_model_pricing_catalog(runtime_dir=self.runtime_dir)
        except Exception:
            return None

    def _attribute_event(self, event: ModelInferenceUsageEvent) -> TeamCostAttribution:
        session_id = _optional_text(event.session_id)
        dispatch_id = _optional_text(event.dispatch_id)
        metadata_attribution = _usage_metadata_attribution(event)
        request_attribution = None
        request_key = _event_request_key(event)
        if request_key is not None:
            request_attribution = self._request_attribution_cache.get(request_key)
        if request_attribution is not None:
            return request_attribution
        if metadata_attribution is not None:
            return metadata_attribution

        if dispatch_id:
            context_attribution = self._dispatch_context_attribution(dispatch_id)
            if context_attribution is not None:
                return context_attribution

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
                project_ids = agent_project_ids(self.state, agent.id)
                project_id = project_ids[0] if project_ids else None
                return TeamCostAttribution(
                    team_id=agent.team_id,
                    project_id=project_id,
                    agent_id=agent.id,
                    source="agent_session",
                    project_confident=bool(project_id),
                )

        if dispatch_id:
            for record in self.state.action_records.values():
                if record.dispatch_id != dispatch_id:
                    continue
                agent = self.state.agents.get(record.agent_id)
                if agent is None:
                    break
                project_ids = agent_project_ids(self.state, agent.id)
                project_id = project_ids[0] if project_ids else None
                return TeamCostAttribution(
                    team_id=agent.team_id,
                    project_id=project_id,
                    agent_id=agent.id,
                    source="dispatch_action_record",
                    project_confident=bool(project_id),
                )

        return TeamCostAttribution(
            team_id=None,
            project_id=None,
            agent_id=None,
            source="unattributed",
            project_confident=False,
        )

    def _request_attributions(
        self, events: Iterable[ModelInferenceUsageEvent]
    ) -> dict[tuple[str, str], TeamCostAttribution]:
        attributions: dict[tuple[str, str], TeamCostAttribution] = {}
        ranks: dict[tuple[str, str], tuple[int, int, int]] = {}
        for event in events:
            request_key = _event_request_key(event)
            if request_key is None:
                continue
            attribution = _usage_metadata_attribution(event)
            dispatch_id = _optional_text(event.dispatch_id)
            if attribution is None and dispatch_id is not None:
                attribution = self._dispatch_context_attribution(dispatch_id)
            if attribution is None:
                continue
            rank = _request_attribution_rank(event, attribution)
            existing = attributions.get(request_key)
            if existing is None or rank > ranks[request_key]:
                attributions[request_key] = attribution
                ranks[request_key] = rank
        return attributions

    def _dispatch_context_attribution(self, dispatch_id: str) -> TeamCostAttribution | None:
        context = self._dispatch_context(dispatch_id)
        if context is None:
            return None
        if not any((context.team_id, context.project_id, context.agent_id)):
            return None
        return TeamCostAttribution(
            team_id=context.team_id,
            project_id=context.project_id,
            agent_id=context.agent_id,
            source="dispatch_correlation_context",
            project_confident=bool(context.project_id),
        )

    def _dispatch_context(self, dispatch_id: str) -> ModelUsageCorrelationContext | None:
        if dispatch_id not in self._context_cache:
            if self._cost_report_snapshot is not None:
                self._context_cache[dispatch_id] = self._cost_report_snapshot.context(dispatch_id)
                return self._context_cache[dispatch_id]
            try:
                self._context_cache[dispatch_id] = self.usage_store.get_correlation_context(
                    dispatch_id
                )
            except Exception:
                self._context_cache[dispatch_id] = None
        return self._context_cache[dispatch_id]

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
                project_id=(
                    agent_project_ids(self.state, agent.id)[0]
                    if agent_project_ids(self.state, agent.id)
                    else None
                ),
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
    retry_after_seconds = _remote_transcript_circuit_retry_after_seconds(source)
    if retry_after_seconds is not None:
        result.errors.append(
            {
                "session_id": source.session_id,
                "remote_host": source.remote_host,
                "error": (
                    "remote transcript scan skipped after recent failure; "
                    f"retry in {max(1, int(retry_after_seconds) + 1)}s"
                ),
            }
        )
        return
    try:
        entries = _scan_remote_claude_transcripts(source)
    except Exception as exc:
        _record_remote_transcript_scan_failure(source)
        result.errors.append(
            {
                "session_id": source.session_id,
                "remote_host": source.remote_host,
                "error": f"{exc.__class__.__name__}: {exc}",
            }
        )
        return
    _clear_remote_transcript_scan_failure(source)

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


def _remote_transcript_circuit_key(
    source: ClaudeTranscriptSource,
) -> tuple[str, str, str] | None:
    host = _optional_text(source.remote_host)
    if host is None:
        return None
    user = _optional_text(source.remote_user) or "agent"
    remote_home = _optional_text(source.remote_agent_home) or DEFAULT_REMOTE_AGENT_HOME
    return (user, host, remote_home)


def _remote_transcript_circuit_retry_after_seconds(
    source: ClaudeTranscriptSource,
) -> float | None:
    key = _remote_transcript_circuit_key(source)
    if key is None:
        return None
    now = time.monotonic()
    with _REMOTE_TRANSCRIPT_FAILURES_LOCK:
        retry_at = _REMOTE_TRANSCRIPT_FAILURES.get(key)
        if retry_at is None:
            return None
        if retry_at <= now:
            _REMOTE_TRANSCRIPT_FAILURES.pop(key, None)
            return None
        return retry_at - now


def _record_remote_transcript_scan_failure(source: ClaudeTranscriptSource) -> None:
    key = _remote_transcript_circuit_key(source)
    if key is None:
        return
    with _REMOTE_TRANSCRIPT_FAILURES_LOCK:
        _REMOTE_TRANSCRIPT_FAILURES[key] = (
            time.monotonic() + REMOTE_TRANSCRIPT_STALE_HOST_TTL_SECONDS
        )


def _clear_remote_transcript_scan_failure(source: ClaudeTranscriptSource) -> None:
    key = _remote_transcript_circuit_key(source)
    if key is None:
        return
    with _REMOTE_TRANSCRIPT_FAILURES_LOCK:
        _REMOTE_TRANSCRIPT_FAILURES.pop(key, None)


def _reset_remote_transcript_scan_failures_for_tests() -> None:
    with _REMOTE_TRANSCRIPT_FAILURES_LOCK:
        _REMOTE_TRANSCRIPT_FAILURES.clear()


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
        "_reported_cost": Decimal("0"),
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
    reported_total = estimate.reported_total
    bucket["events_count"] += 1
    if estimated_total is not None:
        bucket["_estimated_cost"] += estimated_total
        bucket["priced_events_count"] += 1
    elif reported_total is not None:
        bucket["_reported_cost"] += reported_total
        bucket["priced_events_count"] += 1
    else:
        bucket["unpriced_events_count"] += 1
        bucket["_coverage_reasons"].add(estimate.status)
    input_tokens = int(event.input_tokens or 0)
    output_tokens = int(event.output_tokens or 0)
    total_tokens = (
        int(event.total_tokens) if event.total_tokens is not None else input_tokens + output_tokens
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
        "_reported_cost": Decimal("0"),
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
    reported_total = estimate.reported_total
    leaf["events_count"] += 1
    if estimated_total is not None:
        leaf["_estimated_cost"] += estimated_total
        leaf["priced_events_count"] += 1
    elif reported_total is not None:
        leaf["_reported_cost"] += reported_total
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
        int(event.total_tokens) if event.total_tokens is not None else input_tokens + output_tokens
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
        if key == "_reported_cost":
            finalized["reported_cost"] = _decimal_to_float(value)
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
    if "reported_cost" not in finalized:
        finalized["reported_cost"] = 0.0
    finalized["total_cost"] = float(finalized["estimated_cost"] + finalized["reported_cost"])
    return finalized


def _finalize_team_bucket(
    bucket: dict[str, Any],
    *,
    team: Any | None,
    budget_window: dict[str, Any] | None = None,
    budget_window_bucket: dict[str, Any] | None = None,
) -> dict[str, Any]:
    finalized = _finalize_bucket(bucket)
    max_cost = _optional_decimal(getattr(team, "max_cost_usd", None))
    lifetime_estimated = _optional_decimal(finalized.get("estimated_cost")) or Decimal("0")
    lifetime_reported = _optional_decimal(finalized.get("reported_cost")) or Decimal("0")
    lifetime_total = lifetime_estimated + lifetime_reported
    window_finalized = (
        _finalize_bucket(budget_window_bucket or _new_bucket())
        if budget_window is not None
        else finalized
    )
    budget_window_estimated = _optional_decimal(window_finalized.get("estimated_cost")) or Decimal(
        "0"
    )
    budget_window_reported = _optional_decimal(window_finalized.get("reported_cost")) or Decimal(
        "0"
    )
    budget_window_total = budget_window_estimated + budget_window_reported
    capped_total = budget_window_total if budget_window is not None else lifetime_total
    finalized["max_cost_usd"] = _decimal_to_float(max_cost)
    finalized["remaining_cost_usd"] = (
        _decimal_to_float(max(max_cost - capped_total, Decimal("0")))
        if max_cost is not None
        else None
    )
    finalized["cost_limit_reached"] = bool(max_cost is not None and capped_total >= max_cost)
    finalized["cost_limit_basis"] = "budget_window" if budget_window is not None else "lifetime"
    finalized["lifetime_estimated_cost"] = _decimal_to_float(lifetime_estimated)
    finalized["budget_window_estimated_cost"] = _decimal_to_float(budget_window_estimated)
    finalized["lifetime_reported_cost"] = _decimal_to_float(lifetime_reported)
    finalized["budget_window_reported_cost"] = _decimal_to_float(budget_window_reported)
    finalized["lifetime_total_cost"] = _decimal_to_float(lifetime_total)
    finalized["budget_window_total_cost"] = _decimal_to_float(budget_window_total)
    finalized["budget_window_events_count"] = int(window_finalized.get("events_count") or 0)
    finalized["budget_window_started_at"] = (
        _json_datetime(budget_window["started_at"]) if budget_window is not None else None
    )
    finalized["budget_window_ends_at"] = (
        _json_datetime(budget_window.get("ends_at")) if budget_window is not None else None
    )
    finalized["budget_window_renewal_mode"] = (
        budget_window.get("renewal_mode") if budget_window is not None else None
    )
    return finalized


def _current_team_budget_window(team: Any | None, *, now: datetime) -> dict[str, Any] | None:
    if team is None:
        return None
    active = next(
        (
            window
            for window in getattr(team, "cost_budget_windows", [])
            if getattr(window, "ended_at", None) is None
        ),
        None,
    )
    if active is None:
        return None
    started_at = _ensure_utc(active.started_at)
    raw_renewal_mode = getattr(team, "cost_budget_renewal_mode", "manual")
    renewal_mode = str(getattr(raw_renewal_mode, "value", raw_renewal_mode))
    if renewal_mode == "monthly_utc":
        ends_at = _next_utc_month_start(started_at)
        monthly_renewal_not_before = getattr(active, "monthly_renewal_not_before", None)
        if monthly_renewal_not_before is not None:
            ends_at = max(ends_at, _ensure_utc(monthly_renewal_not_before))
        effective_now = _ensure_utc(now)
        while ends_at <= effective_now:
            started_at = ends_at
            ends_at = _next_utc_month_start(started_at)
    else:
        ends_at = None
        renewal_mode = "manual"
    return {
        "window_id": active.id,
        "started_at": started_at,
        "ends_at": ends_at,
        "renewal_mode": renewal_mode,
    }


def _governance_group_costs(
    state: TeamState,
    *,
    team_cost_payloads: Mapping[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    group_costs: dict[str, dict[str, Any]] = {}
    for group_id, group in sorted(state.governance_groups.items()):
        estimated = Decimal("0")
        reported = Decimal("0")
        events_count = 0
        priced_events_count = 0
        unpriced_events_count = 0
        input_tokens = 0
        cached_input_tokens = 0
        cache_write_input_tokens = 0
        output_tokens = 0
        total_tokens = 0
        coverage_reasons: set[str] = set()
        member_team_costs: dict[str, dict[str, Any]] = {}
        cap_reached_team_ids: list[str] = []
        cap_warning_team_ids: list[str] = []

        for team_id in group.member_team_ids:
            team_cost = dict(team_cost_payloads.get(team_id) or {})
            if not team_cost and team_id in state.teams:
                team_cost = _finalize_team_bucket(
                    _new_bucket(team_id=team_id), team=state.teams[team_id]
                )
            if not team_cost:
                continue
            member_team_costs[team_id] = team_cost
            estimated += _optional_decimal(team_cost.get("estimated_cost")) or Decimal("0")
            reported += _optional_decimal(team_cost.get("reported_cost")) or Decimal("0")
            events_count += int(team_cost.get("events_count") or 0)
            priced_events_count += int(team_cost.get("priced_events_count") or 0)
            unpriced_events_count += int(team_cost.get("unpriced_events_count") or 0)
            input_tokens += int(team_cost.get("input_tokens") or 0)
            cached_input_tokens += int(team_cost.get("cached_input_tokens") or 0)
            cache_write_input_tokens += int(team_cost.get("cache_write_input_tokens") or 0)
            output_tokens += int(team_cost.get("output_tokens") or 0)
            total_tokens += int(team_cost.get("total_tokens") or 0)
            coverage_reasons.update(str(item) for item in team_cost.get("coverage_reasons") or [])
            if team_cost.get("cost_limit_reached"):
                cap_reached_team_ids.append(team_id)
                continue
            max_cost = _optional_decimal(team_cost.get("max_cost_usd"))
            if max_cost is not None and max_cost > 0:
                window_total = _optional_decimal(
                    team_cost.get(
                        "budget_window_total_cost", team_cost.get("budget_window_estimated_cost")
                    )
                )
                team_total = (
                    window_total
                    if window_total is not None
                    else _optional_decimal(
                        team_cost.get("total_cost", team_cost.get("estimated_cost"))
                    ) or Decimal("0")
                )
                warning_ratio = _optional_decimal(
                    getattr(state.teams.get(team_id), "cost_warning_ratio", None)
                ) or Decimal("0.8")
                if team_total / max_cost >= warning_ratio:
                    cap_warning_team_ids.append(team_id)

        max_cost = _optional_decimal(getattr(group, "max_cost_usd", None))
        total = estimated + reported
        remaining = (
            _decimal_to_float(max(max_cost - total, Decimal("0")))
            if max_cost is not None
            else None
        )
        group_costs[group_id] = {
            "governance_group_id": group_id,
            "currency": "usd",
            "estimated_cost": _decimal_to_float(estimated),
            "reported_cost": _decimal_to_float(reported),
            "total_cost": _decimal_to_float(total),
            "events_count": events_count,
            "priced_events_count": priced_events_count,
            "unpriced_events_count": unpriced_events_count,
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "cache_write_input_tokens": cache_write_input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "coverage_status": "partial" if coverage_reasons else "complete",
            "coverage_reasons": sorted(coverage_reasons),
            "max_cost_usd": _decimal_to_float(max_cost),
            "remaining_cost_usd": remaining,
            "cost_limit_reached": bool(max_cost is not None and total >= max_cost),
            "cost_limit_basis": "lifetime",
            "hard_enforced": False,
            "member_team_ids": list(group.member_team_ids),
            "member_team_costs": member_team_costs,
            "member_team_cap_reached_ids": sorted(cap_reached_team_ids),
            "member_team_cap_warning_ids": sorted(cap_warning_team_ids),
        }
    return group_costs


def _increment(counter: dict[str, int], key: str) -> None:
    counter[key] = int(counter.get(key, 0)) + 1


def _usage_metadata_attribution(
    event: ModelInferenceUsageEvent,
) -> TeamCostAttribution | None:
    request_metadata = event.raw_usage_metadata.get("request_metadata")
    if not isinstance(request_metadata, Mapping):
        return None
    if not any(key in request_metadata for key in ("team_id", "project_id", "agent_id")):
        return None
    team_id = _optional_text(request_metadata.get("team_id"))
    project_id = _optional_text(request_metadata.get("project_id"))
    agent_id = _optional_text(request_metadata.get("agent_id"))
    return TeamCostAttribution(
        team_id=team_id,
        project_id=project_id,
        agent_id=agent_id,
        source="usage_metadata",
        project_confident=bool(project_id),
    )


def _attribution_score(attribution: TeamCostAttribution) -> int:
    score = 0
    if attribution.team_id:
        score += 4
    if attribution.project_id and attribution.project_confident:
        score += 2
    if attribution.agent_id:
        score += 1
    if attribution.source == "usage_metadata":
        score += 1
    return score


def _request_attribution_rank(
    event: ModelInferenceUsageEvent,
    attribution: TeamCostAttribution,
) -> tuple[int, int, int]:
    relay_rank = 1 if _usage_source(event) != "transcript" else 0
    context_rank = 1 if attribution.source == "dispatch_correlation_context" else 0
    return (relay_rank, context_rank, _attribution_score(attribution))


def _dedupe_cost_events(
    events: Iterable[ModelInferenceUsageEvent],
) -> list[ModelInferenceUsageEvent]:
    return dedupe_model_usage_cost_events(events)


def _event_request_key(event: ModelInferenceUsageEvent) -> tuple[str, str] | None:
    return model_usage_request_key(event)


def _usage_source(event: ModelInferenceUsageEvent) -> str:
    return model_usage_source(event)


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


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json_datetime(value: datetime | None) -> str | None:
    return _ensure_utc(value).isoformat().replace("+00:00", "Z") if value is not None else None


def _next_utc_month_start(value: datetime) -> datetime:
    normalized = _ensure_utc(value)
    if normalized.month == 12:
        return datetime(normalized.year + 1, 1, 1, tzinfo=UTC)
    return datetime(normalized.year, normalized.month + 1, 1, tzinfo=UTC)


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
    configured = os.environ.get("EVANDER_GUEST_KNOWN_HOSTS_FILE", "").strip()
    if configured:
        path = Path(configured).expanduser()
    else:
        path = _caller_home() / ".ssh" / "evander-known_hosts"
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
            / f"evander-known_hosts-{os.environ.get('SUDO_USER') or os.environ.get('USER') or 'agent'}"
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
