"""Graph activity SQL aggregation and report projection."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evander.admin.common import _coerce_optional_datetime, _coerce_optional_string
from evander.admin.payloads import (
    WorkflowEditorDocumentPayload,
    WorkflowGraphActivityGraphPayload,
    WorkflowGraphActivityNodePayload,
    WorkflowGraphActivityReportPayload,
)
from evander.repositories.orchestration_db import (
    WORKFLOW_REPORT_READ_DEADLINE_SECONDS,
    datetime_to_storage,
)


class WorkflowGraphActivityReportBuilder:
    """Collect and project one graph report without sharing accumulation state."""

    def __init__(
        self,
        *,
        document: WorkflowEditorDocumentPayload,
        stores: dict[str, Any],
        now: datetime,
        start_at: datetime,
        end_at: datetime,
        limit: int | None,
        connection_factory: Callable[..., Any],
        schema_initializer: Callable[[Path], None],
    ) -> None:
        self.document = document
        self.stores = stores
        self.now = now
        self.start_at = start_at
        self.end_at = end_at
        self.limit = limit
        self.connection_factory = connection_factory
        self.schema_initializer = schema_initializer

    def build(self) -> WorkflowGraphActivityReportPayload:
        self._load_graph_metadata()
        self._read_activity()
        return self._build_payload()

    def _load_graph_metadata(self) -> None:
        self.node_metadata: dict[str, dict[str, str | None]] = {}
        for collection_name in ("triggers", "gates", "effects", "queries"):
            nodes = self.document.config.get(collection_name)
            if not isinstance(nodes, list):
                continue
            for raw_node in nodes:
                if not isinstance(raw_node, dict):
                    continue
                node_id = str(raw_node.get("id") or "").strip()
                if not node_id:
                    continue
                self.node_metadata[node_id] = {
                    "node_type": _coerce_optional_string(raw_node.get("type")),
                    "display_name": _coerce_optional_string(raw_node.get("display_name")),
                }

        self.graph_states: dict[str, dict[str, Any]] = {}
        self.node_to_graph_id: dict[str, str] = {}
        for graph in self.document.graphs:
            graph_id = graph.id.strip()
            if not graph_id:
                continue
            self.graph_states[graph_id] = {
                "graph": graph,
                "run_keys": set(),
                "activity_count": 0,
                "trigger_event_count": 0,
                "gate_execution_count": 0,
                "effect_execution_count": 0,
                "status_counts": {},
                "first_activity_at": None,
                "latest_activity_at": None,
                "nodes": {},
            }
            for node_id in graph.node_ids:
                self.node_to_graph_id.setdefault(node_id, graph_id)

    def _update_bounds(self, state: dict[str, Any], timestamp: datetime) -> None:
        first_activity_at = state.get("first_activity_at")
        if not isinstance(first_activity_at, datetime) or timestamp < first_activity_at:
            state["first_activity_at"] = timestamp
        latest_activity_at = state.get("latest_activity_at")
        if not isinstance(latest_activity_at, datetime) or timestamp > latest_activity_at:
            state["latest_activity_at"] = timestamp

    def _register_aggregated_activity(
        self,
        *,
        graph_id: str,
        node_id: str,
        source: str,
        status: str,
        activity_count: int,
        first_activity_at: datetime | None,
        latest_activity_at: datetime | None,
    ) -> None:
        state = self.graph_states.get(graph_id)
        if state is None or activity_count <= 0:
            return
        state["activity_count"] += activity_count
        if source == "trigger":
            state["trigger_event_count"] += activity_count
        elif source == "gate":
            state["gate_execution_count"] += activity_count
        elif source == "effect":
            state["effect_execution_count"] += activity_count
        normalized_status = status.strip() or "unknown"
        state["status_counts"][normalized_status] = (
            state["status_counts"].get(normalized_status, 0) + activity_count
        )
        if first_activity_at is not None:
            self._update_bounds(state, first_activity_at)
        if latest_activity_at is not None:
            self._update_bounds(state, latest_activity_at)

        nodes = state["nodes"]
        node_state = nodes.setdefault(
            node_id,
            {
                "activity_count": 0,
                "status_counts": {},
                "first_activity_at": None,
                "latest_activity_at": None,
            },
        )
        node_state["activity_count"] += activity_count
        node_state["status_counts"][normalized_status] = (
            node_state["status_counts"].get(normalized_status, 0) + activity_count
        )
        if first_activity_at is not None:
            self._update_bounds(node_state, first_activity_at)
        if latest_activity_at is not None:
            self._update_bounds(node_state, latest_activity_at)

    def _read_activity(self) -> None:
        if self.stores:
            start_text = datetime_to_storage(self.start_at)
            end_text = datetime_to_storage(self.end_at)
            db_path = Path(self.stores["orchestration_store_path"])
            self.schema_initializer(db_path)
            with self.connection_factory(
                db_path, deadline_seconds=WORKFLOW_REPORT_READ_DEADLINE_SECONDS,
            ) as connection:
                connection.execute(
                    """
                    CREATE TEMP TABLE workflow_graph_activity_nodes (
                        node_id TEXT PRIMARY KEY,
                        graph_id TEXT NOT NULL
                    )
                    """
                )
                connection.executemany(
                    """
                    INSERT INTO workflow_graph_activity_nodes(node_id, graph_id)
                    VALUES (?, ?)
                    """,
                    tuple(self.node_to_graph_id.items()),
                )
                activity_sql = """
                    WITH activity AS MATERIALIZED (
                        SELECT nodes.graph_id AS graph_id,
                               events.trigger_id AS node_id,
                               'trigger' AS source,
                               COALESCE(NULLIF(events.delivery_state, ''), 'event') AS status,
                               events.occurred_at AS activity_at,
                               COALESCE(NULLIF(events.trace_id, ''), events.event_id) AS run_key
                        FROM trigger_events AS events INDEXED BY idx_trigger_events_occurred
                        INNER JOIN workflow_graph_activity_nodes AS nodes
                            ON nodes.node_id = events.trigger_id
                        WHERE events.occurred_at BETWEEN ? AND ?
                        UNION ALL
                        SELECT nodes.graph_id AS graph_id,
                               executions.effect_id AS node_id,
                               'effect' AS source,
                               COALESCE(NULLIF(executions.status, ''), 'unknown') AS status,
                               COALESCE(executions.finished_at, executions.started_at) AS activity_at,
                               COALESCE(
                                   NULLIF(executions.trace_id, ''),
                                   NULLIF(executions.event_id, ''),
                                   executions.execution_id
                               ) AS run_key
                        FROM effect_executions AS executions
                            INDEXED BY idx_effect_executions_activity
                        INNER JOIN workflow_graph_activity_nodes AS nodes
                            ON nodes.node_id = executions.effect_id
                        WHERE COALESCE(executions.finished_at, executions.started_at) BETWEEN ? AND ?
                        UNION ALL
                        SELECT nodes.graph_id AS graph_id,
                               executions.gate_id AS node_id,
                               'gate' AS source,
                               COALESCE(NULLIF(executions.status, ''), 'unknown') AS status,
                               COALESCE(executions.finished_at, executions.started_at) AS activity_at,
                               COALESCE(
                                   NULLIF(executions.trace_id, ''),
                                   NULLIF(executions.event_id, ''),
                                   executions.execution_id
                               ) AS run_key
                        FROM gate_executions AS executions
                            INDEXED BY idx_gate_executions_activity
                        INNER JOIN workflow_graph_activity_nodes AS nodes
                            ON nodes.node_id = executions.gate_id
                        WHERE COALESCE(executions.finished_at, executions.started_at) BETWEEN ? AND ?
                    )
                """
                activity_parameters = (
                    start_text,
                    end_text,
                    start_text,
                    end_text,
                    start_text,
                    end_text,
                )
                for row in connection.execute(
                    activity_sql
                    + """
                    SELECT graph_id, node_id, source, status,
                           COUNT(*) AS activity_count,
                           MIN(activity_at) AS first_activity_at,
                           MAX(activity_at) AS latest_activity_at,
                           NULL AS run_count
                    FROM activity
                    GROUP BY graph_id, node_id, source, status
                    UNION ALL
                    SELECT graph_id, NULL, NULL, NULL, NULL, NULL, NULL,
                           COUNT(DISTINCT run_key) AS run_count
                    FROM activity
                    GROUP BY graph_id
                    """,
                    activity_parameters,
                ):
                    if row["run_count"] is not None:
                        state = self.graph_states.get(str(row["graph_id"] or ""))
                        if state is not None:
                            state["run_count"] = int(row["run_count"])
                        continue
                    self._register_aggregated_activity(
                        graph_id=str(row["graph_id"] or ""),
                        node_id=str(row["node_id"] or ""),
                        source=str(row["source"] or ""),
                        status=str(row["status"] or "unknown"),
                        activity_count=int(row["activity_count"] or 0),
                        first_activity_at=_coerce_optional_datetime(row["first_activity_at"]),
                        latest_activity_at=_coerce_optional_datetime(row["latest_activity_at"]),
                    )

    def _build_payload(self) -> WorkflowGraphActivityReportPayload:
        graphs: list[WorkflowGraphActivityGraphPayload] = []
        for graph_id, state in self.graph_states.items():
            graph = state["graph"]
            node_payloads: list[WorkflowGraphActivityNodePayload] = []
            for node_id, node_state in state["nodes"].items():
                metadata = self.node_metadata.get(node_id, {})
                node_payloads.append(
                    WorkflowGraphActivityNodePayload(
                        node_id=node_id,
                        node_type=metadata.get("node_type"),
                        display_name=metadata.get("display_name"),
                        activity_count=node_state["activity_count"],
                        status_counts=dict(sorted(node_state["status_counts"].items())),
                        first_activity_at=node_state.get("first_activity_at"),
                        latest_activity_at=node_state.get("latest_activity_at"),
                    )
                )
            node_payloads.sort(
                key=lambda node: (
                    node.activity_count,
                    node.latest_activity_at or datetime.min.replace(tzinfo=UTC),
                    node.display_name or node.node_id,
                ),
                reverse=True,
            )
            graphs.append(
                WorkflowGraphActivityGraphPayload(
                    graph_id=graph_id,
                    name=graph.name,
                    description=graph.description,
                    directory_path=graph.directory_path,
                    user_ids=graph.user_ids,
                    node_ids=graph.node_ids,
                    node_count=len(graph.node_ids),
                    run_count=int(state.get("run_count", len(state["run_keys"]))),
                    activity_count=state["activity_count"],
                    trigger_event_count=state["trigger_event_count"],
                    gate_execution_count=state["gate_execution_count"],
                    effect_execution_count=state["effect_execution_count"],
                    status_counts=dict(sorted(state["status_counts"].items())),
                    first_activity_at=state.get("first_activity_at"),
                    latest_activity_at=state.get("latest_activity_at"),
                    nodes=node_payloads,
                )
            )
        graphs.sort(
            key=lambda graph: (
                graph.activity_count,
                graph.run_count,
                graph.latest_activity_at or datetime.min.replace(tzinfo=UTC),
                graph.name,
            ),
            reverse=True,
        )
        if self.limit is not None and self.limit > 0:
            graphs = graphs[:self.limit]

        return WorkflowGraphActivityReportPayload(
            generated_at=self.now,
            period_start=self.start_at,
            period_end=self.end_at,
            graphs=graphs,
        )
