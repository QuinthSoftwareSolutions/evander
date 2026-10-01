"""Workflow failure activity collection, clustering, and report projection."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from sqlite3 import Connection
from typing import TYPE_CHECKING, Any

from evander.admin.common import _coerce_optional_datetime, _coerce_optional_string
from evander.admin.payloads import (
    WorkflowFailureClusterExamplePayload,
    WorkflowFailureClusterPayload,
    WorkflowFailureClusterReportPayload,
    WorkflowFailureClusterSourceKind,
)
from evander.admin.workflow_report_helpers import (
    WORKFLOW_FAILURE_CLUSTER_EXAMPLE_LIMIT,
    WORKFLOW_FAILURE_CLUSTER_STATE_PRIORITY,
    _collect_workflow_artifact_ids,
    _collect_workflow_keyed_values,
    _normalize_workflow_failure_signature,
)
from evander.repositories.orchestration_db import (
    WORKFLOW_REPORT_READ_DEADLINE_SECONDS,
    connect_orchestration_db_read_only,
    datetime_to_storage,
    ensure_orchestration_schema,
)

if TYPE_CHECKING:
    from .workflow_reports_service import WorkflowReportsAdminService


class WorkflowFailureClusterReportBuilder:
    """Keep one report's metadata, activity, and cluster state local to its read."""

    node_metadata: dict[str, dict[str, str | None]]
    graph_metadata_by_id: dict[str, dict[str, str | None]]
    node_to_graph_id: dict[str, str]
    root_graph_metadata: dict[str, str | None]
    failure_statuses: set[str]
    activity_records: list[dict[str, Any]]
    latest_success_at_by_node_source: dict[tuple[str, str, str], datetime]
    clusters: dict[tuple[str, str, str, str, str], dict[str, Any]]
    cluster_payloads: list[WorkflowFailureClusterPayload]

    def __init__(
        self,
        *,
        service: WorkflowReportsAdminService,
        now: datetime,
        start_at: datetime,
        end_at: datetime,
        limit: int,
        recent_threshold_seconds: int,
        recent_threshold: timedelta,
        record_loader: Callable[[Connection, str | bytes], dict[str, Any]],
    ) -> None:
        self.service = service
        self.now = now
        self.start_at = start_at
        self.end_at = end_at
        self.limit = limit
        self.recent_threshold_seconds = recent_threshold_seconds
        self.recent_threshold = recent_threshold
        self.record_loader = record_loader

    def build(self) -> WorkflowFailureClusterReportPayload:
        self._load_graph_metadata()
        self._read_activity()
        self._group_failures()
        self._classify_clusters()
        return self._build_report()

    def _load_graph_metadata(self) -> None:
        document = self.service._read_composed_workflow_editor_document()
        self.node_metadata: dict[str, dict[str, str | None]] = {}
        self.graph_metadata_by_id: dict[str, dict[str, str | None]] = {}
        self.node_to_graph_id: dict[str, str] = {}
        for raw_node in document.config.get("triggers", []):
            if not isinstance(raw_node, dict):
                continue
            node_id = str(raw_node.get("id") or "").strip()
            if not node_id:
                continue
            self.node_metadata[node_id] = {
                "node_type": _coerce_optional_string(raw_node.get("type")),
                "display_name": _coerce_optional_string(raw_node.get("display_name")),
            }
        for collection_name in ("gates", "effects", "queries"):
            nodes = document.config.get(collection_name)
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

        for graph in document.graphs:
            graph_id = graph.id.strip()
            if not graph_id:
                continue
            self.graph_metadata_by_id[graph_id] = {
                "graph_id": graph_id,
                "graph_name": graph.name,
                "graph_description": graph.description,
                "directory_path": graph.directory_path,
            }
            for node_id in graph.node_ids:
                self.node_to_graph_id.setdefault(node_id, graph_id)

        self.root_graph_metadata = {
            "graph_id": "root",
            "graph_name": "Unassigned",
            "graph_description": None,
            "directory_path": "root",
        }

    def _read_activity(self) -> None:
        self.failure_statuses = {"failed", "error_routed"}
        self.activity_records: list[dict[str, Any]] = []
        stores = self.service._workflow_run_store_paths()
        if stores:
            start_text = datetime_to_storage(self.start_at)
            end_text = datetime_to_storage(self.end_at)
            db_path = Path(stores["orchestration_store_path"])
            ensure_orchestration_schema(db_path)
            with connect_orchestration_db_read_only(
                db_path,
                deadline_seconds=WORKFLOW_REPORT_READ_DEADLINE_SECONDS,
            ) as connection:
                connection.execute("BEGIN")
                for row in connection.execute(
                    """
                    SELECT payload_json
                    FROM trigger_events
                    WHERE COALESCE(delivered_at, inflight_at, occurred_at) BETWEEN ? AND ?
                    ORDER BY COALESCE(delivered_at, inflight_at, occurred_at) ASC, event_id ASC
                    """,
                    (start_text, end_text),
                ):
                    raw_record = self.record_loader(connection, row["payload_json"])
                    self._register_record(
                        raw_record,
                        source_kind="trigger",
                        record_key=str(
                            _coerce_optional_string(raw_record.get("event_id"))
                            or _coerce_optional_string(raw_record.get("trigger_id"))
                            or ""
                        ),
                    )
                for row in connection.execute(
                    """
                    SELECT payload_json
                    FROM effect_executions
                    WHERE COALESCE(finished_at, started_at) BETWEEN ? AND ?
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    (start_text, end_text),
                ):
                    raw_record = self.record_loader(connection, row["payload_json"])
                    self._register_record(
                        raw_record,
                        source_kind="effect",
                        record_key=str(
                            _coerce_optional_string(raw_record.get("execution_id"))
                            or _coerce_optional_string(raw_record.get("event_id"))
                            or ""
                        ),
                    )
                for row in connection.execute(
                    """
                    SELECT payload_json
                    FROM gate_executions
                    WHERE COALESCE(finished_at, started_at) BETWEEN ? AND ?
                    ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                    """,
                    (start_text, end_text),
                ):
                    raw_record = self.record_loader(connection, row["payload_json"])
                    self._register_record(
                        raw_record,
                        source_kind="gate",
                        record_key=str(
                            _coerce_optional_string(raw_record.get("execution_id"))
                            or _coerce_optional_string(raw_record.get("event_id"))
                            or ""
                        ),
                    )
        self.activity_records.sort(
            key=lambda item: (
                item["activity_at"] or datetime.min.replace(tzinfo=UTC),
                item["source_kind"],
                item["node_id"],
                item["event_id"] or item["record_key"],
            ),
            reverse=True,
        )

    def _group_failures(self) -> None:
        self.latest_success_at_by_node_source: dict[tuple[str, str, str], datetime] = {}
        self.clusters: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
        for record in self.activity_records:
            self._add_activity_record(record)

    def _classify_clusters(self) -> None:
        self.cluster_payloads: list[WorkflowFailureClusterPayload] = []
        for cluster in self.clusters.values():
            latest_failure_at = cluster.pop("_latest_failure_at", None)
            latest_failure_dismissed = bool(cluster.pop("_latest_failure_dismissed", False))
            latest_failure_cleared = bool(cluster.pop("_latest_failure_cleared", False))
            latest_failure_status = _coerce_optional_string(
                cluster.pop("_latest_failure_status", None)
            )
            node_source_key = cluster.pop("_node_source_key", None)
            latest_success_at = (
                self.latest_success_at_by_node_source.get(node_source_key)
                if isinstance(node_source_key, tuple)
                else None
            )
            if (
                latest_failure_at is not None
                and latest_success_at is not None
                and latest_success_at > latest_failure_at
            ):
                cluster["state"] = "resolved"
            elif latest_failure_dismissed or latest_failure_cleared:
                cluster["state"] = "historical"
            elif latest_failure_status == "failed":
                cluster["state"] = "active"
            elif (
                latest_failure_at is not None
                and latest_failure_at >= self.end_at - self.recent_threshold
            ):
                cluster["state"] = "recent"
            else:
                cluster["state"] = "historical"

            cluster["examples"] = list(cluster["examples"])

    def _build_report(self) -> WorkflowFailureClusterReportPayload:
        cluster_items = list(self.clusters.values())
        cluster_items.sort(
            key=lambda item: (
                item["last_seen_at"] or datetime.min.replace(tzinfo=UTC),
                item["cluster_id"],
            ),
            reverse=True,
        )
        cluster_items.sort(
            key=lambda item: WORKFLOW_FAILURE_CLUSTER_STATE_PRIORITY.get(
                str(item["state"]), len(WORKFLOW_FAILURE_CLUSTER_STATE_PRIORITY)
            )
        )

        limited_clusters = cluster_items[: self.limit]
        has_more = len(cluster_items) > len(limited_clusters)
        for cluster in limited_clusters:
            self.cluster_payloads.append(
                WorkflowFailureClusterPayload.model_validate(
                    {key: value for key, value in cluster.items() if not key.startswith("_")}
                )
            )

        return WorkflowFailureClusterReportPayload(
            generated_at=self.now,
            period_start=self.start_at,
            period_end=self.end_at,
            recent_threshold_seconds=self.recent_threshold_seconds,
            limit=self.limit,
            has_more=has_more,
            clusters=self.cluster_payloads,
        )

    def _graph_context_for_node(self, node_id: str) -> tuple[str, dict[str, str | None]]:
        graph_id = self.node_to_graph_id.get(node_id)
        if graph_id is None:
            return "root", self.root_graph_metadata
        graph_metadata = self.graph_metadata_by_id.get(graph_id)
        if graph_metadata is None:
            return "root", self.root_graph_metadata
        return graph_id, graph_metadata

    def _register_record(
        self,
        raw_record: dict[str, Any],
        *,
        source_kind: WorkflowFailureClusterSourceKind,
        record_key: str,
    ) -> None:
        node_field = {
            "trigger": "trigger_id",
            "effect": "effect_id",
            "gate": "gate_id",
        }[source_kind]
        type_field = {
            "trigger": "trigger_type",
            "effect": "effect_type",
            "gate": "gate_type",
        }[source_kind]
        node_id = _coerce_optional_string(raw_record.get(node_field))
        if node_id is None:
            return
        if source_kind == "trigger":
            activity_at = _latest_of(
                raw_record.get("delivered_at"),
                raw_record.get("inflight_at"),
                raw_record.get("occurred_at"),
            )
        else:
            activity_at = _latest_of(raw_record.get("finished_at"), raw_record.get("started_at"))
        if activity_at is None:
            return

        node_info = self.node_metadata.get(node_id, {})
        graph_id, graph_info = self._graph_context_for_node(node_id)
        raw_status = _coerce_optional_string(raw_record.get("status"))
        node_type = _coerce_optional_string(raw_record.get(type_field)) or _coerce_optional_string(
            node_info.get("node_type")
        )
        display_name = _coerce_optional_string(node_info.get("display_name"))
        linked_payloads = _payload_sources(raw_record)
        session_ids = _collect_keyed_values(
            *linked_payloads, suffixes=("session_id", "session_ids")
        )
        pr_ids = _collect_keyed_values(*linked_payloads, suffixes=("pr_id", "pr_ids"))
        ado_item_ids = _collect_keyed_values(
            *linked_payloads,
            suffixes=("ado_item_id", "ado_item_ids", "work_item_id", "work_item_ids"),
        )
        issue_ids = _collect_keyed_values(*linked_payloads, suffixes=("issue_id", "issue_ids"))
        artifact_ids = _collect_artifact_ids(*linked_payloads)
        raw_error: str | None = _coerce_optional_string(raw_record.get("error"))
        if raw_error is None:
            raw_error = _coerce_optional_string(raw_record.get("binding_error"))
        if raw_error is None and isinstance(raw_record.get("outputs"), dict):
            outputs = raw_record.get("outputs")
            raw_error = _coerce_optional_string(outputs.get("error_message"))
        if raw_error is None and isinstance(raw_record.get("outputs"), dict):
            outputs = raw_record.get("outputs")
            raw_error = _coerce_optional_string(outputs.get("message"))
        if raw_error is None:
            raw_error = _coerce_optional_string(raw_record.get("clear_reason"))
        error_text, error_signature, error_signature_key = _normalize_error_signature(
            raw_record,
            source_kind=source_kind,
            node_type=node_type,
            error_text=raw_error,
        )
        failure_candidate = (
            (source_kind == "trigger" and bool(raw_error))
            or (source_kind == "effect" and raw_status in self.failure_statuses)
            or (source_kind == "gate" and raw_status == "failed")
        )
        success_activity = not failure_candidate
        event_id = _coerce_optional_string(raw_record.get("event_id"))
        if source_kind != "trigger" and event_id is None:
            event_id = _coerce_optional_string(raw_record.get("execution_id"))
        record = {
            "activity_at": activity_at,
            "event_id": event_id,
            "trace_id": _coerce_optional_string(raw_record.get("trace_id")),
            "session_ids": session_ids,
            "pr_ids": pr_ids,
            "ado_item_ids": ado_item_ids,
            "issue_ids": issue_ids,
            "artifact_ids": artifact_ids,
            "error_text": error_text,
            "error_signature": error_signature,
            "error_signature_key": error_signature_key,
            "failure_candidate": failure_candidate,
            "success_activity": success_activity,
            "dismissed": bool(raw_record.get("dismissed_at")),
            "cleared": bool(
                raw_record.get("cleared_at")
                or (source_kind == "trigger" and raw_record.get("clear_reason"))
            ),
            "raw_status": raw_status,
            "graph_id": graph_id,
            "graph_name": graph_info["graph_name"],
            "graph_description": graph_info["graph_description"],
            "directory_path": graph_info["directory_path"],
            "node_id": node_id,
            "node_display_name": display_name,
            "node_type": node_type,
            "source_kind": source_kind,
            "trigger_type": _coerce_optional_string(raw_record.get("trigger_type"))
            if source_kind == "trigger"
            else None,
            "effect_type": _coerce_optional_string(raw_record.get("effect_type"))
            if source_kind == "effect"
            else None,
            "gate_type": _coerce_optional_string(raw_record.get("gate_type"))
            if source_kind == "gate"
            else None,
            "started_at": _coerce_optional_datetime(raw_record.get("started_at")),
            "finished_at": _coerce_optional_datetime(raw_record.get("finished_at")),
            "occurred_at": _coerce_optional_datetime(raw_record.get("occurred_at")),
            "record_key": record_key,
            "payload": raw_record,
        }
        self.activity_records.append(record)

    def _add_activity_record(self, record: dict[str, Any]) -> None:
        node_source_key = (
            str(record["graph_id"]),
            str(record["node_id"]),
            str(record["source_kind"]),
        )
        if record["success_activity"]:
            self.latest_success_at_by_node_source.setdefault(node_source_key, record["activity_at"])
        if not record["failure_candidate"]:
            return

        key = _cluster_key(record)
        cluster = self.clusters.get(key)
        if cluster is None:
            cluster = self._new_cluster(record, key, node_source_key)
            self.clusters[key] = cluster
        else:
            if record["activity_at"] < cluster["first_seen_at"]:
                cluster["first_seen_at"] = record["activity_at"]
        cluster["count"] += 1
        if record["activity_at"] > cluster["last_seen_at"]:
            cluster["last_seen_at"] = record["activity_at"]
            cluster["latest_error_summary"] = record["error_signature"]
            cluster["_latest_failure_at"] = record["activity_at"]
            cluster["_latest_failure_dismissed"] = bool(record["dismissed"])
            cluster["_latest_failure_cleared"] = bool(record["cleared"])
            cluster["_latest_failure_status"] = record["raw_status"]
        elif cluster.get("_latest_failure_at") is None:
            cluster["_latest_failure_at"] = record["activity_at"]
            cluster["_latest_failure_dismissed"] = bool(record["dismissed"])
            cluster["_latest_failure_cleared"] = bool(record["cleared"])
            cluster["_latest_failure_status"] = record["raw_status"]
        self._append_cluster_references(cluster, record)
        self._append_cluster_example(cluster, record)

    @staticmethod
    def _new_cluster(
        record: dict[str, Any],
        key: tuple[str, str, str, str, str],
        node_source_key: tuple[str, str, str],
    ) -> dict[str, Any]:
        return {
            "cluster_id": hashlib.sha256("|".join(key).encode("utf-8")).hexdigest()[:16],
            "graph_id": record["graph_id"],
            "graph_name": record["graph_name"],
            "graph_description": record["graph_description"],
            "directory_path": record["directory_path"],
            "node_id": record["node_id"],
            "node_display_name": record["node_display_name"],
            "node_type": record["node_type"],
            "trigger_type": record["trigger_type"],
            "effect_type": record["effect_type"],
            "gate_type": record["gate_type"],
            "source_kind": record["source_kind"],
            "error_signature": record["error_signature"],
            "error_signature_key": record["error_signature_key"],
            "count": 0,
            "first_seen_at": record["activity_at"],
            "last_seen_at": record["activity_at"],
            "latest_error_summary": record["error_signature"],
            "state": "historical",
            "event_ids": [],
            "trace_ids": [],
            "session_ids": [],
            "pr_ids": [],
            "ado_item_ids": [],
            "issue_ids": [],
            "artifact_ids": [],
            "examples": [],
            "_example_limit": WORKFLOW_FAILURE_CLUSTER_EXAMPLE_LIMIT,
            "_latest_failure_at": record["activity_at"],
            "_latest_failure_dismissed": bool(record["dismissed"]),
            "_latest_failure_cleared": bool(record["cleared"]),
            "_latest_failure_status": record["raw_status"],
            "_node_source_key": node_source_key,
        }

    @staticmethod
    def _append_cluster_references(cluster: dict[str, Any], record: dict[str, Any]) -> None:
        for value in [record["event_id"]]:
            if value and value not in cluster["event_ids"]:
                cluster["event_ids"].append(value)
        for value in [record["trace_id"]]:
            if value and value not in cluster["trace_ids"]:
                cluster["trace_ids"].append(value)
        for value in record["session_ids"]:
            if value not in cluster["session_ids"]:
                cluster["session_ids"].append(value)
        for value in record["pr_ids"]:
            if value not in cluster["pr_ids"]:
                cluster["pr_ids"].append(value)
        for value in record["ado_item_ids"]:
            if value not in cluster["ado_item_ids"]:
                cluster["ado_item_ids"].append(value)
        for value in record["issue_ids"]:
            if value not in cluster["issue_ids"]:
                cluster["issue_ids"].append(value)
        for value in record["artifact_ids"]:
            if value not in cluster["artifact_ids"]:
                cluster["artifact_ids"].append(value)

    @staticmethod
    def _append_cluster_example(cluster: dict[str, Any], record: dict[str, Any]) -> None:
        if len(cluster["examples"]) < cluster["_example_limit"]:
            cluster["examples"].append(
                WorkflowFailureClusterExamplePayload(
                    event_id=record["event_id"],
                    trace_id=record["trace_id"],
                    session_id=record["session_ids"][0] if record["session_ids"] else None,
                    occurred_at=record["activity_at"],
                    started_at=record["started_at"],
                    finished_at=record["finished_at"],
                    error_summary=record["error_signature"],
                    run_state="errored" if record["failure_candidate"] else "active",
                    run_detail_href=(
                        f"#workflow?event_id={record['event_id']}" if record["event_id"] else None
                    ),
                    api_run_detail_href=(
                        f"/api/workflow-editor/run-detail?event_id={record['event_id']}"
                        if record["event_id"]
                        else None
                    ),
                    artifact_ids=list(record["artifact_ids"]),
                )
            )


def _latest_of(*values: object) -> datetime | None:
    parsed_values = [
        parsed
        for parsed in (_coerce_optional_datetime(value) for value in values)
        if parsed is not None
    ]
    return max(parsed_values) if parsed_values else None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _collect_keyed_values(*payloads: object, suffixes: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for payload in payloads:
        for suffix in suffixes:
            values.extend(_collect_workflow_keyed_values(payload, key_suffix=suffix))
    return _dedupe(values)


def _collect_artifact_ids(*payloads: object) -> list[str]:
    values: list[str] = []
    for payload in payloads:
        values.extend(_collect_workflow_artifact_ids(payload))
    return _dedupe(values)


def _payload_sources(raw_record: dict[str, Any]) -> tuple[object, ...]:
    return (
        raw_record,
        raw_record.get("resolved_config"),
        raw_record.get("input_payload"),
        raw_record.get("outputs"),
        raw_record.get("payload"),
        raw_record.get("inputs_by_source"),
    )


def _normalize_error_signature(
    raw_record: dict[str, Any],
    *,
    source_kind: WorkflowFailureClusterSourceKind,
    node_type: str | None,
    error_text: object | None = None,
) -> tuple[str, str, str | None]:
    outputs = raw_record.get("outputs")
    signature_outputs = dict(outputs) if isinstance(outputs, dict) else {}
    signature_outputs["source_kind"] = source_kind
    if node_type:
        signature_outputs["node_type"] = node_type
    if source_kind == "trigger":
        signature_outputs.setdefault(
            "trigger_type",
            _coerce_optional_string(raw_record.get("trigger_type")),
        )
    elif source_kind == "effect":
        signature_outputs.setdefault(
            "effect_type",
            _coerce_optional_string(raw_record.get("effect_type")),
        )
    elif source_kind == "gate":
        signature_outputs.setdefault(
            "gate_type",
            _coerce_optional_string(raw_record.get("gate_type")),
        )
    summary, key = _normalize_workflow_failure_signature(error_text, signature_outputs)
    return _coerce_optional_string(error_text) or summary, summary, key


def _cluster_key(record: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(record["graph_id"]),
        str(record["node_id"]),
        str(record["source_kind"]),
        str(record["node_type"] or ""),
        str(record["error_signature_key"]),
    )
