from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from evander.admin.common import (
    _coerce_optional_datetime,
    _coerce_optional_string,
    _read_merged_json_objects,
    _utc_now,
)
from evander.admin.payloads import (
    ConformanceReportPayload,
    ConformanceReportRunPayload,
    ConformanceReportsPayload,
    ConformanceRequirementSnippetPayload,
    WorkflowEditorDocumentPayload,
    WorkflowFailureClusterExamplePayload as WorkflowFailureClusterExamplePayload,
    WorkflowFailureClusterPayload as WorkflowFailureClusterPayload,
    WorkflowFailureClusterReportPayload,
    WorkflowFailureClusterSourceKind as WorkflowFailureClusterSourceKind,
    WorkflowGraphActivityGraphPayload as WorkflowGraphActivityGraphPayload,
    WorkflowGraphActivityNodePayload as WorkflowGraphActivityNodePayload,
    WorkflowGraphActivityReportPayload,
    WorkflowNodeExecutionDetailPayload,
    WorkflowNodeExecutionFieldsPayload,
    WorkflowNodeExecutionSearchPayload,
    WorkflowRunDetailPayload,
    WorkflowRunLookupPayload,
    WorkflowRunNodeActivityPayload as WorkflowRunNodeActivityPayload,
    WorkflowRunRecentNodeActivityPayload as WorkflowRunRecentNodeActivityPayload,
    WorkflowRunSearchPayload,
    WorkflowRunSnapshotPayload,
    WorkflowRunSummaryPayload as WorkflowRunSummaryPayload,
)
from evander.admin.services.snapshot_cache import SnapshotCache
from evander.admin.workflow_report_helpers import (
    CONFORMANCE_REPORT_RUN_TIMEOUT_SECONDS,
    CONFORMANCE_REPORT_SNIPPET_LIMIT,
    CONFORMANCE_REPORT_SPECS,
    CONFORMANCE_REPORT_STALE_AFTER,
    WORKFLOW_FAILURE_CLUSTER_DEFAULT_RECENT_THRESHOLD_SECONDS,
    WORKFLOW_FAILURE_CLUSTER_EXAMPLE_LIMIT as WORKFLOW_FAILURE_CLUSTER_EXAMPLE_LIMIT,
    WORKFLOW_FAILURE_CLUSTER_LIMIT_MAX,
    WORKFLOW_FAILURE_CLUSTER_STATE_PRIORITY as WORKFLOW_FAILURE_CLUSTER_STATE_PRIORITY,
    _as_utc,
    _join_conformance_process_output,
    _merge_run_node_activity as _merge_run_node_activity,
    _normalize_workflow_failure_signature as _normalize_workflow_failure_signature,
    _trim_conformance_process_output,
    _trim_conformance_text,
)
from evander.communication_artifacts import CommunicationArtifactStore
from evander.models import AgentRecord
from evander.repositories.execution_history import (
    list_node_execution_authorization_paths,
    list_node_execution_fields,
    read_node_execution_authorization_path,
    read_node_execution_detail,
    search_node_executions,
)
from evander.repositories.operational_payloads import load_record
from evander.repositories.orchestration_db import (
    WORKFLOW_REPORT_READ_DEADLINE_SECONDS,
    connect_orchestration_db,
    connect_orchestration_db_read_only,
    datetime_to_storage as datetime_to_storage,
    ensure_orchestration_schema,
)
from evander.workflow.events import SqliteWorkflowEventJournal

WORKFLOW_GRAPH_ACTIVITY_SNAPSHOT_TTL_SECONDS = 15.0
WORKFLOW_RUN_SNAPSHOT_TTL_SECONDS = 1.0


@dataclass(slots=True)
class WorkflowReportsAdminDependencies:
    project_dir: Path
    config: Any
    runtime: Any
    workflow_config_admin: Any
    list_agents: Callable[..., list[AgentRecord]]
    project_dir_provider: Callable[[], Path] | None = None


class WorkflowReportsAdminService:
    """Workflow reporting operations."""

    def __init__(self, dependencies: WorkflowReportsAdminDependencies) -> None:
        self._project_dir = dependencies.project_dir
        self._project_dir_provider = dependencies.project_dir_provider
        self.config = dependencies.config
        self.runtime = dependencies.runtime
        self._workflow_config_admin = dependencies.workflow_config_admin
        self._list_agents = dependencies.list_agents
        self._graph_activity_snapshots: SnapshotCache[WorkflowGraphActivityReportPayload] = (
            SnapshotCache(ttl_seconds=WORKFLOW_GRAPH_ACTIVITY_SNAPSHOT_TTL_SECONDS)
        )
        self._run_snapshots: SnapshotCache[WorkflowRunSnapshotPayload] = SnapshotCache(
            ttl_seconds=WORKFLOW_RUN_SNAPSHOT_TTL_SECONDS
        )

    @property
    def project_dir(self) -> Path:
        if self._project_dir_provider is not None:
            return self._project_dir_provider()
        return self._project_dir

    def read_workflow_graph_activity_report(
        self,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        limit: int | None = None,
        refresh: bool = False,
    ) -> "WorkflowGraphActivityReportPayload":
        kwargs = {"refresh": True} if refresh else {}
        return self._read_workflow_graph_activity_report(
            period_start=period_start, period_end=period_end, limit=limit, **kwargs
        )

    def read_workflow_failure_clusters_report(
        self,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        limit: int | None = None,
        recent_threshold_seconds: int | None = None,
    ) -> "WorkflowFailureClusterReportPayload":
        return self._read_workflow_failure_clusters_report(
            period_start=period_start,
            period_end=period_end,
            limit=limit,
            recent_threshold_seconds=recent_threshold_seconds,
        )

    def read_conformance_reports(self) -> "ConformanceReportsPayload":
        return self._read_conformance_reports()

    def read_workflow_node_execution_search(self, **kwargs: Any):
        return self._read_workflow_node_execution_search(**kwargs)

    def read_workflow_node_execution_fields(self, **kwargs: Any):
        return self._read_workflow_node_execution_fields(**kwargs)

    def read_workflow_node_execution_detail(self, execution_key: str):
        return self._read_workflow_node_execution_detail(execution_key)

    def read_workflow_node_execution_authorization_paths(self, **kwargs: Any):
        return self._read_workflow_node_execution_authorization_paths(**kwargs)

    def read_workflow_node_execution_authorization_path(self, execution_key: str):
        return self._read_workflow_node_execution_authorization_path(execution_key)

    def run_conformance_report(self, kind: str) -> "ConformanceReportRunPayload":
        return self._run_conformance_report(kind)

    def list_agents(self, **kwargs: Any) -> list[AgentRecord]:
        return self._list_agents(**kwargs)

    def _workflow_run_store_paths(self):
        return self._workflow_config_admin._workflow_run_store_paths()

    def _workflow_run_store_bundle(self, *args: Any, **kwargs: Any):
        return self._workflow_config_admin._workflow_run_store_bundle(*args, **kwargs)

    def _workflow_run_event_records(self, *args: Any, **kwargs: Any):
        return self._workflow_config_admin._workflow_run_event_records(*args, **kwargs)

    def _read_composed_workflow_editor_document(self):
        return self._workflow_config_admin._read_composed_workflow_editor_document()

    def _workflow_editor_revision(self, *args: Any, **kwargs: Any):
        return self._workflow_config_admin._workflow_editor_revision(*args, **kwargs)

    def _workflow_config_snapshot_repository(self, *args: Any, **kwargs: Any):
        return self._workflow_config_admin._workflow_config_snapshot_repository(*args, **kwargs)

    def _latest_resumable_failed_workflow_record(self, *args: Any, **kwargs: Any):
        return self._workflow_config_admin._latest_resumable_failed_workflow_record(*args, **kwargs)

    def _communication_artifact_store(self) -> CommunicationArtifactStore:
        return CommunicationArtifactStore(self.config.runtime_dir)

    def _read_workflow_run_snapshot(self) -> WorkflowRunSnapshotPayload:
        stores = self._workflow_run_store_paths()
        db_path = Path(stores["orchestration_store_path"])
        journal = SqliteWorkflowEventJournal(db_path)
        revision = journal.cursor()
        identity = db_path.stat()
        key = (
            db_path.resolve(),
            identity.st_dev,
            identity.st_ino,
            Path(stores["runtime_dir"]).resolve(),
        )
        snapshot = self._run_snapshots.read(
            key,
            lambda: self._collect_workflow_run_snapshot(stores),
            minimum_revision=revision,
            read_revision=journal.cursor,
        )
        return snapshot.model_copy(deep=True)

    def _collect_workflow_run_snapshot(
        self, stores: Mapping[str, Any]
    ) -> WorkflowRunSnapshotPayload:
        from .workflow_run_snapshot import WorkflowRunSnapshotBuilder

        return WorkflowRunSnapshotBuilder(
            service=self,
            stores=stores,
            now=_utc_now(),
            pending_loader=_read_merged_json_objects,
        ).build()

    def _read_workflow_node_execution_search(
        self,
        *,
        node_id: str,
        started_from: datetime,
        started_to: datetime,
        statuses: list[str],
        filters: list[Mapping[str, Any]],
        limit: int,
        cursor: str | None,
        include_total: bool = True,
    ) -> WorkflowNodeExecutionSearchPayload:
        stores = self._workflow_run_store_paths()
        payload = search_node_executions(
            stores["orchestration_store_path"],
            node_id=node_id,
            started_from=started_from,
            started_to=started_to,
            statuses=statuses,
            filters=filters,
            limit=limit,
            cursor=cursor,
            include_total=include_total,
            full_retention_days=int(
                stores["orchestration_retention_days"]
                if stores.get("orchestration_retention_days") is not None
                else 30
            ),
            compact_retention_days=int(
                stores["orchestration_compact_history_retention_days"]
                if stores.get("orchestration_compact_history_retention_days") is not None
                else 365
            ),
        )
        return WorkflowNodeExecutionSearchPayload.model_validate(payload)

    def _read_workflow_node_execution_fields(
        self,
        *,
        node_id: str,
        started_from: datetime,
        started_to: datetime,
    ) -> WorkflowNodeExecutionFieldsPayload:
        stores = self._workflow_run_store_paths()
        return WorkflowNodeExecutionFieldsPayload.model_validate(
            list_node_execution_fields(
                stores["orchestration_store_path"],
                node_id=node_id,
                started_from=started_from,
                started_to=started_to,
            )
        )

    def _read_workflow_node_execution_detail(
        self, execution_key: str
    ) -> WorkflowNodeExecutionDetailPayload:
        stores = self._workflow_run_store_paths()
        return WorkflowNodeExecutionDetailPayload.model_validate(
            read_node_execution_detail(
                stores["orchestration_store_path"], execution_key=execution_key
            )
        )

    def _read_workflow_node_execution_authorization_paths(
        self,
        *,
        node_id: str,
        started_from: datetime,
        started_to: datetime,
    ) -> tuple[str, ...]:
        stores = self._workflow_run_store_paths()
        return list_node_execution_authorization_paths(
            stores["orchestration_store_path"],
            node_id=node_id,
            started_from=started_from,
            started_to=started_to,
        )

    def _read_workflow_node_execution_authorization_path(self, execution_key: str) -> str:
        stores = self._workflow_run_store_paths()
        return read_node_execution_authorization_path(
            stores["orchestration_store_path"], execution_key=execution_key
        )

    def _read_workflow_run_search(
        self,
        *,
        node_id: str | None = None,
        graph_id: str | None = None,
        without_session: bool = False,
        include_completed: bool = True,
        limit: int | None = None,
    ) -> WorkflowRunSearchPayload:
        normalized_node_id = _coerce_optional_string(node_id)
        normalized_graph_id = _coerce_optional_string(graph_id)
        normalized_limit = max(1, min(100, int(limit or 25)))

        from .workflow_run_search import WorkflowRunSearchBuilder

        return WorkflowRunSearchBuilder(
            service=self,
            node_id=normalized_node_id,
            graph_id=normalized_graph_id,
            limit=normalized_limit,
            without_session=without_session,
            include_completed=include_completed,
            clock=_utc_now,
        ).build()

    def _read_workflow_graph_activity_report(
        self,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        limit: int | None = None,
        refresh: bool = False,
    ) -> WorkflowGraphActivityReportPayload:
        document = self._read_composed_workflow_editor_document()
        stores = self._workflow_run_store_paths()

        def collect() -> WorkflowGraphActivityReportPayload:
            return self._collect_workflow_graph_activity_report(
                period_start=period_start,
                period_end=period_end,
                limit=limit,
                document=document,
                stores=stores,
            )

        # Explicit intervals are exact reads, including backfilled executions.
        # Only the default rolling dashboard window is a short-lived snapshot.
        if period_start is not None or period_end is not None:
            return collect()
        key = (
            str(stores.get("orchestration_store_path", "")),
            document.revision or self._workflow_editor_revision(
                config=document.config, layout=document.layout, graphs=document.graphs
            ),
            limit,
        )
        return self._graph_activity_snapshots.read(key, collect, refresh=refresh).model_copy(
            deep=True
        )

    def _collect_workflow_graph_activity_report(
        self,
        *,
        period_start: datetime | None,
        period_end: datetime | None,
        limit: int | None,
        document: WorkflowEditorDocumentPayload,
        stores: dict[str, Any],
    ) -> WorkflowGraphActivityReportPayload:
        now = _utc_now()
        end_at = (
            period_end.astimezone(UTC)
            if period_end is not None and period_end.tzinfo is not None
            else period_end.replace(tzinfo=UTC)
            if period_end is not None
            else now
        )
        start_at = (
            period_start.astimezone(UTC)
            if period_start is not None and period_start.tzinfo is not None
            else period_start.replace(tzinfo=UTC)
            if period_start is not None
            else end_at - timedelta(days=1)
        )
        if start_at > end_at:
            raise ValueError("Workflow graph activity period_start must be before period_end.")

        from .workflow_graph_activity import WorkflowGraphActivityReportBuilder

        return WorkflowGraphActivityReportBuilder(
            document=document,
            stores=stores,
            now=now,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
            connection_factory=connect_orchestration_db,
            schema_initializer=ensure_orchestration_schema,
        ).build()

    def _read_workflow_failure_clusters_report(
        self,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        limit: int | None = None,
        recent_threshold_seconds: int | None = None,
    ) -> WorkflowFailureClusterReportPayload:
        now = _utc_now()
        end_at = _as_utc(period_end) if period_end is not None else now
        start_at = _as_utc(period_start) if period_start is not None else end_at - timedelta(days=1)
        if start_at > end_at:
            raise ValueError("Workflow failure cluster period_start must be before period_end.")

        normalized_limit = (
            WORKFLOW_FAILURE_CLUSTER_LIMIT_MAX
            if limit is None
            else max(1, min(WORKFLOW_FAILURE_CLUSTER_LIMIT_MAX, int(limit)))
        )
        normalized_recent_threshold_seconds = (
            WORKFLOW_FAILURE_CLUSTER_DEFAULT_RECENT_THRESHOLD_SECONDS
            if recent_threshold_seconds is None
            else max(1, int(recent_threshold_seconds))
        )
        recent_threshold = timedelta(seconds=normalized_recent_threshold_seconds)

        from .workflow_failure_clusters import WorkflowFailureClusterReportBuilder

        return WorkflowFailureClusterReportBuilder(
            service=self,
            now=now,
            start_at=start_at,
            end_at=end_at,
            limit=normalized_limit,
            recent_threshold_seconds=normalized_recent_threshold_seconds,
            recent_threshold=recent_threshold,
            record_loader=load_record,
        ).build()

    def _read_conformance_reports(self) -> ConformanceReportsPayload:
        now = _utc_now()
        specs = (CONFORMANCE_REPORT_SPECS["trigger"], CONFORMANCE_REPORT_SPECS["effect"])
        return ConformanceReportsPayload(
            generated_at=now,
            stale_after_seconds=int(CONFORMANCE_REPORT_STALE_AFTER.total_seconds()),
            reports=[self._read_conformance_report(now=now, **spec) for spec in specs],
        )

    def _run_conformance_report(self, kind: str) -> ConformanceReportRunPayload:
        normalized_kind = kind.strip().lower()
        spec = CONFORMANCE_REPORT_SPECS.get(normalized_kind)
        if spec is None:
            allowed = ", ".join(sorted(CONFORMANCE_REPORT_SPECS))
            raise ValueError(f"Unknown conformance report {kind!r}. Expected one of: {allowed}.")

        suite_path = Path(str(spec["suite_path"]))
        suite_file = self.project_dir / suite_path
        if not suite_file.exists():
            raise RuntimeError(f"{spec['label']} conformance suite is missing: {suite_path}")

        artifact_path = (self.config.runtime_dir / str(spec["artifact_path"])).resolve()
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env[str(spec["env_var"])] = str(artifact_path)
        src_path = str((self.project_dir / "src").resolve())
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            src_path if not existing_pythonpath else f"{src_path}{os.pathsep}{existing_pythonpath}"
        )

        command = [sys.executable, "-m", "pytest", "-q", str(suite_path)]
        started_at = _utc_now()
        try:
            result = subprocess.run(
                command,
                cwd=self.project_dir,
                env=env,
                text=True,
                capture_output=True,
                timeout=CONFORMANCE_REPORT_RUN_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            output = _trim_conformance_process_output(
                _join_conformance_process_output(exc.stdout, exc.stderr)
            )
            message = (
                f"{spec['label']} conformance suite timed out after "
                f"{CONFORMANCE_REPORT_RUN_TIMEOUT_SECONDS} seconds."
            )
            if output:
                message = f"{message}\n{output}"
            raise RuntimeError(message) from exc
        except OSError as exc:
            raise RuntimeError(
                f"{spec['label']} conformance suite could not be started: {exc}"
            ) from exc

        completed_at = _utc_now()
        output = _trim_conformance_process_output(
            _join_conformance_process_output(result.stdout, result.stderr)
        )
        return ConformanceReportRunPayload(
            kind=normalized_kind,
            exit_code=result.returncode,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=max(0.0, (completed_at - started_at).total_seconds()),
            output=output or None,
            reports=self._read_conformance_reports(),
        )

    def _read_conformance_report(
        self,
        *,
        kind: str,
        label: str,
        artifact_path: str,
        guidance: str,
        env_var: str,
        suite_path: str,
        now: datetime,
    ) -> ConformanceReportPayload:
        path = self.config.runtime_dir / artifact_path

        def report(
            state: str,
            *,
            status: str | None = None,
            generated_at: datetime | None = None,
            requirement_counts: dict[str, int] | None = None,
            requirement_count: int = 0,
            missing_coverage_count: int = 0,
            missing_coverage: dict[str, list[str]] | None = None,
            traceability_task_count: int = 0,
            failing_requirements: list[ConformanceRequirementSnippetPayload] | None = None,
            remediation_snippets: list[ConformanceRequirementSnippetPayload] | None = None,
            error: str | None = None,
        ) -> ConformanceReportPayload:
            return ConformanceReportPayload(
                kind=kind,
                label=label,
                artifact_path=artifact_path,
                state=state,
                status=status,
                generated_at=generated_at,
                requirement_counts=requirement_counts or {},
                requirement_count=requirement_count,
                missing_coverage_count=missing_coverage_count,
                missing_coverage=missing_coverage or {},
                traceability_task_count=traceability_task_count,
                failing_requirements=failing_requirements or [],
                remediation_snippets=remediation_snippets or [],
                error=error,
                guidance=guidance,
            )

        if not path.exists():
            return report("missing", error=f"{artifact_path} has not been generated yet.")

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return report("invalid", error=f"{artifact_path} is not valid JSON: {exc.msg}.")
        except OSError as exc:
            return report("invalid", error=f"{artifact_path} could not be read: {exc}.")

        if not isinstance(payload, dict):
            return report("invalid", error=f"{artifact_path} must contain a JSON object.")

        raw_generated_at = payload.get("generated_at")
        generated_at = _coerce_optional_datetime(raw_generated_at)
        if generated_at is None:
            return report("invalid", error=f"{artifact_path} is missing a valid generated_at.")

        status = (_coerce_optional_string(payload.get("status")) or "").lower()
        if status not in {"pass", "fail"}:
            return report(
                "invalid",
                generated_at=generated_at,
                error=f"{artifact_path} has unsupported status {payload.get('status')!r}.",
            )

        raw_requirements = payload.get("requirements")
        if not isinstance(raw_requirements, list):
            return report(
                "invalid",
                status=status,
                generated_at=generated_at,
                error=f"{artifact_path} requirements must be a list.",
            )
        raw_traceability_matrix = payload.get("traceability_matrix")
        if not isinstance(raw_traceability_matrix, dict):
            return report(
                "invalid",
                status=status,
                generated_at=generated_at,
                error=f"{artifact_path} traceability_matrix must be an object.",
            )
        raw_missing_coverage = payload.get("missing_coverage")
        if not isinstance(raw_missing_coverage, dict):
            return report(
                "invalid",
                status=status,
                generated_at=generated_at,
                error=f"{artifact_path} missing_coverage must be an object.",
            )

        requirement_counts: dict[str, int] = {}
        failing_requirements: list[ConformanceRequirementSnippetPayload] = []
        remediation_snippets: list[ConformanceRequirementSnippetPayload] = []
        for index, raw_requirement in enumerate(raw_requirements):
            if not isinstance(raw_requirement, dict):
                return report(
                    "invalid",
                    status=status,
                    generated_at=generated_at,
                    error=f"{artifact_path} requirements[{index}] must be an object.",
                )
            requirement_status = (
                _coerce_optional_string(raw_requirement.get("status")) or "unknown"
            ).lower()
            requirement_counts[requirement_status] = (
                requirement_counts.get(requirement_status, 0) + 1
            )
            requirement_id = _coerce_optional_string(raw_requirement.get("requirement_id"))
            raw_coverage = raw_requirement.get("coverage")
            coverage = (
                [str(item) for item in raw_coverage] if isinstance(raw_coverage, list) else []
            )
            snippet = ConformanceRequirementSnippetPayload(
                requirement_id=requirement_id or f"requirement[{index}]",
                status=requirement_status,
                details=_trim_conformance_text(raw_requirement.get("details")),
                remediation=_trim_conformance_text(raw_requirement.get("remediation")),
                coverage=coverage,
            )
            if (
                requirement_status != "pass"
                and len(failing_requirements) < CONFORMANCE_REPORT_SNIPPET_LIMIT
            ):
                failing_requirements.append(snippet)
            if snippet.remediation and len(remediation_snippets) < CONFORMANCE_REPORT_SNIPPET_LIMIT:
                remediation_snippets.append(snippet)

        missing_coverage: dict[str, list[str]] = {}
        missing_coverage_count = 0
        for task_name, raw_missing_items in raw_missing_coverage.items():
            if not isinstance(raw_missing_items, list):
                return report(
                    "invalid",
                    status=status,
                    generated_at=generated_at,
                    error=f"{artifact_path} missing_coverage.{task_name} must be a list.",
                )
            items = [str(item) for item in raw_missing_items]
            missing_coverage[str(task_name)] = items
            missing_coverage_count += len(items)

        state = status
        if generated_at < now - CONFORMANCE_REPORT_STALE_AFTER:
            state = "stale"

        return report(
            state,
            status=status,
            generated_at=generated_at,
            requirement_counts=dict(sorted(requirement_counts.items())),
            requirement_count=len(raw_requirements),
            missing_coverage_count=missing_coverage_count,
            missing_coverage=dict(sorted(missing_coverage.items())),
            traceability_task_count=len(raw_traceability_matrix),
            failing_requirements=failing_requirements,
            remediation_snippets=remediation_snippets,
        )

    def _workflow_run_search_sqlite_event_ids(
        self,
        stores: Mapping[str, Any],
        *,
        target_node_ids: set[str],
        include_completed: bool,
        limit: int,
        without_session: bool,
    ) -> tuple[list[str], int, bool]:
        node_ids = sorted(node_id for node_id in target_node_ids if node_id)
        if not node_ids:
            return [], 0, False

        node_value_placeholders = ", ".join("(?)" for _ in node_ids)
        target_params = node_ids
        candidate_conditions: list[str] = []
        non_completed_cte = ""
        if not include_completed:
            candidate_conditions.append("event_id IN (SELECT event_id FROM non_completed_events)")
            non_completed_cte = """
            ,
            non_completed_events AS (
                SELECT event_id
                FROM trigger_events
                WHERE delivery_state IN ('pending', 'inflight')
                UNION
                SELECT event_id
                FROM effect_executions
                WHERE status IN (
                    'running', 'waiting', 'failed', 'completion_pending', 'error_routed'
                )
                UNION
                SELECT event_id
                FROM gate_executions
                WHERE status IN ('running', 'waiting', 'failed', 'completion_pending')
            )
            """
        no_session_cte = ""
        if without_session:
            candidate_conditions.append("event_id NOT IN (SELECT event_id FROM session_events)")
            session_id_value_predicate = """
                LOWER(CAST(session_value.key AS TEXT)) LIKE '%session_id'
                AND session_value.type = 'text'
                AND TRIM(CAST(session_value.value AS TEXT)) <> ''
            """
            session_selects = []
            for kind, table, key in (("trigger", "trigger_events", "event_id"),
                                     ("effect", "effect_executions", "execution_id"),
                                     ("gate", "gate_executions", "execution_id")):
                session_selects.append(f"""
                    SELECT r.event_id FROM {table} r
                    JOIN candidate_events c ON c.event_id=r.event_id
                    LEFT JOIN workflow_execution_projections p ON p.record_kind='{kind}' AND p.record_id=r.{key}
                    WHERE CASE WHEN p.record_id IS NOT NULL THEN p.has_session_reference
                        ELSE EXISTS(SELECT 1 FROM json_tree(r.payload_json) AS session_value
                            WHERE {session_id_value_predicate}) END
                """)
            no_session_cte = ", session_events AS (" + " UNION ".join(session_selects) + ")"
        candidate_filter = (
            "WHERE " + " AND ".join(candidate_conditions) if candidate_conditions else ""
        )
        cte = f"""
            WITH target_nodes(node_id) AS (
                VALUES {node_value_placeholders}
            ),
            target_activity AS (
                SELECT event_id, COALESCE(delivered_at, inflight_at, occurred_at) AS activity_at
                FROM trigger_events
                WHERE trigger_id IN (SELECT node_id FROM target_nodes)
                UNION ALL
                SELECT event_id, COALESCE(finished_at, started_at) AS activity_at
                FROM effect_executions
                WHERE effect_id IN (SELECT node_id FROM target_nodes)
                UNION ALL
                SELECT event_id, COALESCE(finished_at, started_at) AS activity_at
                FROM gate_executions
                WHERE gate_id IN (SELECT node_id FROM target_nodes)
            ),
            candidate_events AS (
                SELECT event_id, MAX(activity_at) AS latest_activity_at
                FROM target_activity
                GROUP BY event_id
            )
            {non_completed_cte}
            {no_session_cte}
        """
        db_path = Path(stores["orchestration_store_path"])
        ensure_orchestration_schema(db_path)
        with connect_orchestration_db_read_only(
            db_path, deadline_seconds=WORKFLOW_REPORT_READ_DEADLINE_SECONDS,
        ) as connection:
            connection.execute("BEGIN")
            count_row = connection.execute(
                f"{cte} SELECT COUNT(*) AS matched FROM candidate_events {candidate_filter}",
                target_params,
            ).fetchone()
            matched_count = int(count_row["matched"] if count_row is not None else 0)
            fetch_limit = matched_count if without_session else min(500, limit + 1)
            if fetch_limit <= 0:
                return [], matched_count, False
            rows = connection.execute(
                f"""
                {cte}
                SELECT event_id
                FROM candidate_events
                {candidate_filter}
                ORDER BY latest_activity_at DESC, event_id DESC
                LIMIT ?
                """,
                [*target_params, fetch_limit],
            ).fetchall()
        event_ids = [
            event_id
            for row in rows
            for event_id in [_coerce_optional_string(row["event_id"])]
            if event_id
        ]
        return event_ids, matched_count, matched_count > len(event_ids)

    def _workflow_run_sqlite_records_for_event_ids(
        self,
        stores: Mapping[str, Any],
        *,
        event_ids: list[str],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        normalized_event_ids = [
            event_id.strip()
            for event_id in dict.fromkeys(event_ids)
            if event_id and event_id.strip()
        ]
        if not normalized_event_ids:
            return {}, {}, {}

        db_path = Path(stores["orchestration_store_path"])
        ensure_orchestration_schema(db_path)
        queue_records: dict[str, Any] = {}
        effect_records: dict[str, Any] = {}
        gate_records: dict[str, Any] = {}

        def add_payload(
            target: dict[str, Any],
            row: Any,
            *,
            key_field: str,
        ) -> None:
            payload = load_record(connection, row["payload_json"])
            key = _coerce_optional_string(payload.get(key_field)) or _coerce_optional_string(
                row[key_field]
            )
            if key:
                target[key] = payload

        def event_id_batches() -> Iterator[list[str]]:
            batch_size = 500
            for index in range(0, len(normalized_event_ids), batch_size):
                yield normalized_event_ids[index : index + batch_size]

        with connect_orchestration_db_read_only(
            db_path, deadline_seconds=WORKFLOW_REPORT_READ_DEADLINE_SECONDS,
        ) as connection:
            connection.execute("BEGIN")
            for event_id_batch in event_id_batches():
                placeholders = ", ".join("?" for _ in event_id_batch)
                for row in connection.execute(
                    f"""
                    SELECT event_id, payload_json
                    FROM trigger_events
                    WHERE event_id IN ({placeholders})
                    ORDER BY occurred_at ASC, event_id ASC
                    """,
                    event_id_batch,
                ):
                    add_payload(queue_records, row, key_field="event_id")
                for row in connection.execute(
                    f"""
                    SELECT execution_id, payload_json
                    FROM effect_executions
                    WHERE event_id IN ({placeholders})
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    event_id_batch,
                ):
                    add_payload(effect_records, row, key_field="execution_id")
                for row in connection.execute(
                    f"""
                    SELECT execution_id, payload_json
                    FROM gate_executions
                    WHERE event_id IN ({placeholders})
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    event_id_batch,
                ):
                    add_payload(gate_records, row, key_field="execution_id")

        return queue_records, effect_records, gate_records

    def _workflow_run_detail_sqlite_records(
        self,
        stores: Mapping[str, Any],
        lookup: WorkflowRunLookupPayload,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        event_keys = {
            value for value in (lookup.run_id, lookup.event_id, lookup.trigger_event_id) if value
        }
        trace_id = lookup.trace_id
        if not event_keys and trace_id is None:
            return None

        db_path = Path(stores["orchestration_store_path"])
        ensure_orchestration_schema(db_path)
        queue_records: dict[str, Any] = {}
        effect_records: dict[str, Any] = {}
        gate_records: dict[str, Any] = {}

        def add_payload(
            target: dict[str, Any],
            row: Any,
            *,
            key_field: str,
        ) -> None:
            payload = load_record(connection, row["payload_json"])
            key = _coerce_optional_string(payload.get(key_field)) or _coerce_optional_string(
                row[key_field]
            )
            if key:
                target[key] = payload

        def placeholders(values: set[str]) -> str:
            return ", ".join("?" for _ in values)

        with connect_orchestration_db_read_only(
            db_path, deadline_seconds=WORKFLOW_REPORT_READ_DEADLINE_SECONDS,
        ) as connection:
            connection.execute("BEGIN")
            if event_keys:
                event_key_params = sorted(event_keys)
                event_key_placeholders = placeholders(event_keys)
                for row in connection.execute(
                    f"""
                    SELECT event_id, payload_json
                    FROM trigger_events
                    WHERE event_id IN ({event_key_placeholders})
                    ORDER BY occurred_at ASC, event_id ASC
                    """,
                    event_key_params,
                ):
                    add_payload(queue_records, row, key_field="event_id")
                for row in connection.execute(
                    f"""
                    SELECT execution_id, payload_json
                    FROM effect_executions
                    WHERE event_id IN ({event_key_placeholders})
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    event_key_params,
                ):
                    add_payload(effect_records, row, key_field="execution_id")
                for row in connection.execute(
                    f"""
                    SELECT execution_id, payload_json
                    FROM gate_executions
                    WHERE event_id IN ({event_key_placeholders})
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    event_key_params,
                ):
                    add_payload(gate_records, row, key_field="execution_id")

            if trace_id is not None:
                for row in connection.execute(
                    """
                    SELECT event_id, payload_json
                    FROM trigger_events
                    WHERE trace_id = ?
                    ORDER BY occurred_at ASC, event_id ASC
                    """,
                    (trace_id,),
                ):
                    add_payload(queue_records, row, key_field="event_id")
                for row in connection.execute(
                    """
                    SELECT execution_id, payload_json
                    FROM effect_executions
                    WHERE trace_id = ?
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    (trace_id,),
                ):
                    add_payload(effect_records, row, key_field="execution_id")
                for row in connection.execute(
                    """
                    SELECT execution_id, payload_json
                    FROM gate_executions
                    WHERE trace_id = ?
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    (trace_id,),
                ):
                    add_payload(gate_records, row, key_field="execution_id")

            related_event_ids = {
                event_id
                for record in (
                    list(queue_records.values())
                    + list(effect_records.values())
                    + list(gate_records.values())
                )
                for event_id in [_coerce_optional_string(record.get("event_id"))]
                if event_id
            }
            if trace_id is not None and related_event_ids:
                event_id_params = sorted(related_event_ids)
                event_id_placeholders = placeholders(related_event_ids)
                for row in connection.execute(
                    f"""
                    SELECT event_id, payload_json
                    FROM trigger_events
                    WHERE event_id IN ({event_id_placeholders})
                    ORDER BY occurred_at ASC, event_id ASC
                    """,
                    event_id_params,
                ):
                    add_payload(queue_records, row, key_field="event_id")
                for row in connection.execute(
                    f"""
                    SELECT execution_id, payload_json
                    FROM effect_executions
                    WHERE event_id IN ({event_id_placeholders})
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    event_id_params,
                ):
                    add_payload(effect_records, row, key_field="execution_id")
                for row in connection.execute(
                    f"""
                    SELECT execution_id, payload_json
                    FROM gate_executions
                    WHERE event_id IN ({event_id_placeholders})
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    event_id_params,
                ):
                    add_payload(gate_records, row, key_field="execution_id")

        return queue_records, effect_records, gate_records

    def _read_workflow_run_detail(
        self,
        *,
        run_id: str | None = None,
        event_id: str | None = None,
        trigger_event_id: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        artifact_id: str | None = None,
    ) -> WorkflowRunDetailPayload:
        from .workflow_run_detail import WorkflowRunDetailBuilder

        return WorkflowRunDetailBuilder(self).build(
            run_id=run_id,
            event_id=event_id,
            trigger_event_id=trigger_event_id,
            trace_id=trace_id,
            session_id=session_id,
            artifact_id=artifact_id,
        )
