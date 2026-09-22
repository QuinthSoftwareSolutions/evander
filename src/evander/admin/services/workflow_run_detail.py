"""Workflow run record correlation and detail projection."""

from __future__ import annotations
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from pydantic import BaseModel
from evander.admin.common import (
    _coerce_optional_datetime,
    _coerce_optional_string,
    _read_merged_json_objects,
    _utc_now,
)
from evander.admin.payloads import (
    ToolCallAuditEntryPayload,
    WorkflowRunAgentLinkPayload,
    WorkflowRunApprovalLinkPayload,
    WorkflowRunCandidateMatchPayload,
    WorkflowRunCommunicationArtifactLinkPayload,
    WorkflowRunDataAvailabilityPayload,
    WorkflowRunDetailPayload,
    WorkflowRunGraphPayload,
    WorkflowRunGraphSnapshotPayload,
    WorkflowRunLookupPayload,
    WorkflowRunNodeActivityPayload,
    WorkflowRunNodeDetailPayload,
    WorkflowRunNodeRecordPayload,
    WorkflowRunRecoveryActionPayload,
    WorkflowRunRecoveryPayload,
    WorkflowRunSummaryPayload,
    WorkflowRunToolCallLinkPayload,
    WorkflowRunTriggerEventPayload,
    WORKFLOW_RUN_DETAIL_AUDIT_SCAN_LIMIT,
)
from evander.models import AgentRecord, ApprovalRequest, SessionManifest
from evander.admin.services.approval_audit_service import (
    iter_file_lines_reverse as _iter_file_lines_reverse,
    tool_call_audit_entry_from_payload as _tool_call_audit_entry_from_payload,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .workflow_reports_service import WorkflowReportsAdminService


class WorkflowRunDetailBuilder:
    """Correlate persisted records into one workflow run detail response."""

    def __init__(self, service: WorkflowReportsAdminService) -> None:
        self.service = service

    def build(
        self,
        *,
        run_id: str | None = None,
        event_id: str | None = None,
        trigger_event_id: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        artifact_id: str | None = None,
    ) -> WorkflowRunDetailPayload:
        normalized_run_id = _coerce_optional_string(run_id)
        normalized_event_id = _coerce_optional_string(event_id)
        normalized_trigger_event_id = _coerce_optional_string(trigger_event_id)
        normalized_trace_id = _coerce_optional_string(trace_id)
        normalized_session_id = _coerce_optional_string(session_id)
        normalized_artifact_id = _coerce_optional_string(artifact_id)
        if normalized_artifact_id is not None:
            normalized_artifact_id = normalized_artifact_id.strip()
            if normalized_artifact_id:
                from evander.communication_artifacts import validate_artifact_id

                normalized_artifact_id = validate_artifact_id(normalized_artifact_id)
        self.lookup = WorkflowRunLookupPayload(
            run_id=normalized_run_id,
            event_id=normalized_event_id,
            trigger_event_id=normalized_trigger_event_id,
            trace_id=normalized_trace_id,
            session_id=normalized_session_id,
            artifact_id=normalized_artifact_id,
        )
        if not any(
            value
            for value in (
                self.lookup.run_id,
                self.lookup.event_id,
                self.lookup.trigger_event_id,
                self.lookup.trace_id,
                self.lookup.session_id,
                self.lookup.artifact_id,
            )
        ):
            raise ValueError(
                "Workflow run detail lookup requires run_id, event_id, trigger_event_id, "
                "trace_id, session_id, or artifact_id."
            )

        self._load_run_records()
        self._group_run_records()
        self._select_run()
        self._load_graph_context()
        self._load_agent_records()
        self._load_tool_call_records()
        self._group_node_records()
        self._link_node_records()
        self._build_nodes_and_tool_links()
        self._build_recovery_and_trigger()
        return self._build_detail_payload()

    def _load_run_records(self) -> None:
        self.stores = self.service._workflow_run_store_paths()
        sqlite_records = self.service._workflow_run_detail_sqlite_records(self.stores, self.lookup)
        if sqlite_records is not None:
            self.queue_records, self.effect_records, self.gate_records = sqlite_records
        else:
            _, effect_store, gate_store = self.service._workflow_run_store_bundle(self.stores)
            self.queue_records = self.service._workflow_run_event_records(self.stores)
            self.effect_records = effect_store.read_all()
            self.gate_records = gate_store.read_all()
        self.await_agent_payload = _read_merged_json_objects(
            self.stores["legacy_await_agent_store_path"],
            self.stores["await_agent_store_path"],
        )
        self.approval_requests = self.service.runtime.approvals.read_all()
        self.artifact_store = self.service._communication_artifact_store()

    def _group_run_records(self) -> None:
        self.groups: dict[str, dict[str, Any]] = {}
        for event_key, record in self.queue_records.items():
            raw_record = self.normalize_record_payload(record)
            if not raw_record:
                continue
            self.register_trigger(
                self.groups, raw_record, event_key=str(raw_record.get("event_id") or event_key)
            )
        for execution_id, record in self.effect_records.items():
            raw_record = self.normalize_record_payload(record)
            if not raw_record:
                continue
            self.register_execution(
                self.groups,
                raw_record,
                event_key=str(raw_record.get("event_id") or execution_id),
                kind="effect",
            )
        for execution_id, record in self.gate_records.items():
            raw_record = self.normalize_record_payload(record)
            if not raw_record:
                continue
            self.register_execution(
                self.groups,
                raw_record,
                event_key=str(raw_record.get("event_id") or execution_id),
                kind="gate",
            )
        for record_key, raw_record in self.await_agent_payload.items():
            if not isinstance(raw_record, dict):
                continue
            event_key = str(raw_record.get("prompt_event_id") or record_key)
            group = self.ensure_group(self.groups, event_key)
            group["await_agent_records"].append(dict(raw_record))
            self.update_group_metadata(
                group,
                trace_id_value=_coerce_optional_string(raw_record.get("trace_id")),
                session_ids=self.collect_keyed_values(raw_record, key_suffix="session_id"),
                artifact_ids=self.collect_artifact_ids(raw_record),
                request_ids=self.collect_keyed_values(raw_record, key_suffix="request_id"),
                occurred_at_value=_coerce_optional_datetime(raw_record.get("occurred_at")),
                latest_activity_at_value=_coerce_optional_datetime(raw_record.get("occurred_at")),
            )

        if not self.groups:
            raise ValueError("Workflow run detail lookup did not match any stored run.")

    def _select_run(self) -> None:
        self.artifact_lookup_payload: dict[str, Any] | None = None
        self.lookup_session_candidates = (
            {self.lookup.session_id} if self.lookup.session_id else set()
        )
        if self.lookup.artifact_id is not None:
            try:
                self.artifact_lookup_payload = self.artifact_store.payload(self.lookup.artifact_id)
            except FileNotFoundError:
                self.artifact_lookup_payload = None
            else:
                artifact_session_id = _coerce_optional_string(
                    self.artifact_lookup_payload.get("session_id")
                )
                if artifact_session_id:
                    self.lookup_session_candidates.add(artifact_session_id)

        self.approval_requests_by_id = {
            request_id: request for request_id, request in self.approval_requests.items()
        }
        self.approval_requests_by_session: dict[str, list[ApprovalRequest]] = {}
        for request in self.approval_requests.values():
            self.approval_requests_by_session.setdefault(request.session_id, []).append(request)

        self.candidate_matches: list[WorkflowRunCandidateMatchPayload] = []
        self.selected_group: dict[str, Any] | None = None
        selected_score = -1
        self.selected_matched_by: list[str] = []
        for group in sorted(
            self.groups.values(),
            key=lambda item: (
                item.get("latest_activity_at") or datetime.min.replace(tzinfo=UTC),
                str(item.get("event_id") or ""),
            ),
            reverse=True,
        ):
            score, matched_by = self.score_group(group)
            if score > 0:
                self.candidate_matches.append(
                    WorkflowRunCandidateMatchPayload(
                        event_id=str(group["event_id"]),
                        trace_id=next(iter(sorted(group["trace_ids"])), None),
                        run_state=None,
                        latest_activity_at=group.get("latest_activity_at"),
                        source_node_id=_coerce_optional_string(
                            (group["trigger"] or {}).get("trigger_id")
                            if isinstance(group.get("trigger"), dict)
                            else None
                        ),
                        source_trigger_type=_coerce_optional_string(
                            (group["trigger"] or {}).get("trigger_type")
                            if isinstance(group.get("trigger"), dict)
                            else None
                        ),
                        delivery_state=_coerce_optional_string(
                            (group["trigger"] or {}).get("delivery_state")
                            if isinstance(group.get("trigger"), dict)
                            else None
                        ),
                        matched_by=matched_by,
                    )
                )
            if score > 0 and score > selected_score:
                self.selected_group = group
                selected_score = score
                self.selected_matched_by = matched_by

        if self.selected_group is None:
            raise ValueError("Workflow run detail lookup did not match any stored run.")

    def _load_graph_context(self) -> None:
        self.selected_event_id = str(self.selected_group["event_id"])
        self.trigger_record = (
            self.selected_group["trigger"]
            if isinstance(self.selected_group.get("trigger"), dict)
            else None
        )
        self.effect_records_for_group = [
            record for record in self.selected_group["effect_records"] if isinstance(record, dict)
        ]
        self.gate_records_for_group = [
            record for record in self.selected_group["gate_records"] if isinstance(record, dict)
        ]
        self.await_agent_records_for_group = [
            record
            for record in self.selected_group["await_agent_records"]
            if isinstance(record, dict)
        ]

        selected_revisions = [
            revision
            for revision in self.selected_group["config_revisions"]
            if isinstance(revision, str) and revision
        ]
        document = self.service._read_composed_workflow_editor_document()
        self.document_config = document.config
        document_revision = document.revision or self.service._workflow_editor_revision(
            self.document_config
        )
        snapshot_repository = self.service._workflow_config_snapshot_repository(self.stores)
        self.config_revision = selected_revisions[0] if selected_revisions else document_revision
        graph_snapshot_payload = snapshot_repository.get_snapshot(self.config_revision)
        self.graph_snapshot = (
            WorkflowRunGraphSnapshotPayload.model_validate(graph_snapshot_payload)
            if graph_snapshot_payload is not None
            else None
        )
        self.graph_data_availability = WorkflowRunDataAvailabilityPayload(
            graph_snapshot="available"
            if self.graph_snapshot is not None
            else "missing_legacy_or_unrecorded",
            binding_trace="missing_legacy_or_unrecorded",
            approvals="missing_legacy_or_unrecorded",
            agent_links="missing_legacy_or_unrecorded",
            communication_artifacts="missing_legacy_or_unrecorded",
            tool_calls="missing_legacy_or_unrecorded",
            trigger_event="available"
            if self.trigger_record is not None
            else "missing_legacy_or_unrecorded",
        )

        self.selected_trace_id = next(
            (trace_id for trace_id in sorted(self.selected_group["trace_ids"]) if trace_id),
            None,
        )
        self.selected_session_ids = {
            str(value)
            for value in self.selected_group["session_ids"]
            if isinstance(value, str) and value.strip()
        }

        self.node_metadata_by_id: dict[str, dict[str, Any]] = {}

        self.register_node_metadata(self.document_config)
        if self.graph_snapshot is not None:
            self.register_node_metadata(
                {
                    "triggers": self.graph_snapshot.triggers,
                    "gates": self.graph_snapshot.gates,
                    "effects": self.graph_snapshot.effects,
                    "queries": self.graph_snapshot.queries,
                    "edges": self.graph_snapshot.edges,
                }
            )

    def _load_agent_records(self) -> None:
        self.agent_records_by_session_id: dict[str, list[AgentRecord]] = {}
        self.agent_records_by_target_name: dict[str, list[AgentRecord]] = {}
        try:
            agent_records = self.service.list_agents(
                include_inactive=True,
                reconcile_containers=False,
                backend="all",
                all_directories=True,
            )
        except Exception:
            agent_records = []
        for agent_record in agent_records:
            session_key = _coerce_optional_string(agent_record.session_id)
            if session_key:
                self.agent_records_by_session_id.setdefault(session_key, []).append(agent_record)
            target_key = _coerce_optional_string(agent_record.target_name)
            if target_key:
                self.agent_records_by_target_name.setdefault(target_key, []).append(agent_record)

    def _load_tool_call_records(self) -> None:
        self.tool_call_audit_entries: list[tuple[ToolCallAuditEntryPayload, dict[str, Any]]] = []
        audit_log_path = self.service.config.audit_log_path

        if audit_log_path.exists():
            try:
                scanned_lines = 0
                for raw_line in _iter_file_lines_reverse(audit_log_path):
                    scanned_lines += 1
                    if scanned_lines > WORKFLOW_RUN_DETAIL_AUDIT_SCAN_LIMIT:
                        break
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    if str(payload.get("event_type") or "").strip().casefold() != "tool_call":
                        continue
                    entry = _tool_call_audit_entry_from_payload(payload)
                    if entry is None:
                        continue
                    if not self.tool_call_entry_matches_selected_run(entry):
                        continue
                    self.tool_call_audit_entries.append((entry, payload))
            except Exception:
                self.tool_call_audit_entries = []

    def _group_node_records(self) -> None:
        self.node_groups: dict[str, list[tuple[str, dict[str, Any], str | None, str | None]]] = {}

        if self.trigger_record is not None:
            self.add_node_record(
                source_kind="trigger_event",
                record=self.trigger_record,
                node_id=_coerce_optional_string(self.trigger_record.get("trigger_id")),
                node_type=_coerce_optional_string(self.trigger_record.get("trigger_type")),
            )
        for record in self.effect_records_for_group:
            self.add_node_record(
                source_kind="effect_execution",
                record=record,
                node_id=_coerce_optional_string(record.get("effect_id")),
                node_type=_coerce_optional_string(record.get("effect_type")),
            )
        for record in self.gate_records_for_group:
            self.add_node_record(
                source_kind="gate_execution",
                record=record,
                node_id=_coerce_optional_string(record.get("gate_id")),
                node_type=_coerce_optional_string(record.get("gate_type")),
            )
        for record in self.await_agent_records_for_group:
            self.add_node_record(
                source_kind="await_agent",
                record=record,
                node_id=_coerce_optional_string(record.get("gate_id")),
                node_type="gate:await-agent",
            )

    def _link_node_records(self) -> None:
        self.approval_links_by_node: dict[str, list[WorkflowRunApprovalLinkPayload]] = {}
        self.agent_links_by_node: dict[str, list[WorkflowRunAgentLinkPayload]] = {}
        self.artifact_links_by_node: dict[
            str, list[WorkflowRunCommunicationArtifactLinkPayload]
        ] = {}
        self.tool_call_links_by_node: dict[str, list[WorkflowRunToolCallLinkPayload]] = {}
        self.run_tool_call_links: list[WorkflowRunToolCallLinkPayload] = []
        node_request_ids_by_node: dict[str, set[str]] = {}
        node_session_ids_by_node: dict[str, set[str]] = {}

        for node_id, record_entries in self.node_groups.items():
            for _source_kind, record, _node_id, node_type in record_entries:
                request_ids = self.collect_keyed_values(record, key_suffix="request_id")
                if request_ids:
                    node_request_ids_by_node.setdefault(node_id, set()).update(request_ids)
                for request_id in request_ids:
                    request = self.approval_requests_by_id.get(request_id)
                    if request is None:
                        continue
                    self.approval_links_by_node.setdefault(node_id, []).append(
                        self.build_approval_link(request)
                    )
                session_candidates = self.collect_keyed_values(record, key_suffix="session_id")
                if session_candidates:
                    node_session_ids_by_node.setdefault(node_id, set()).update(session_candidates)
                for session_candidate in session_candidates:
                    for request in self.approval_requests_by_session.get(session_candidate, []):
                        self.approval_links_by_node.setdefault(node_id, []).append(
                            self.build_approval_link(request)
                        )
                agent_link = self.build_agent_link(
                    node_id=node_id,
                    node_type=node_type,
                    record=record,
                    source_kind=_source_kind,
                )
                if agent_link is not None:
                    self.agent_links_by_node.setdefault(node_id, []).append(agent_link)
                artifact_ids = self.collect_artifact_ids(record)
                for artifact in artifact_ids:
                    try:
                        link_payload = self.build_artifact_link(
                            self.artifact_store.payload(artifact)
                        )
                    except FileNotFoundError:
                        link_payload = WorkflowRunCommunicationArtifactLinkPayload(
                            artifact_id=artifact,
                            status="missing_legacy_or_unrecorded",
                        )
                    self.artifact_links_by_node.setdefault(node_id, []).append(link_payload)

        for entry, _raw_payload in sorted(
            self.tool_call_audit_entries,
            key=lambda item: (
                item[0].created_at,
                item[0].tool_name or "",
                item[0].request_id or "",
                item[0].correlation_id or "",
                item[0].approval_request_id or "",
            ),
            reverse=True,
        ):
            request_node_matches = [
                node_id
                for node_id in sorted(self.node_groups)
                if (
                    (
                        entry.request_id
                        and entry.request_id in node_request_ids_by_node.get(node_id, set())
                    )
                    or (
                        entry.approval_request_id
                        and entry.approval_request_id
                        in node_request_ids_by_node.get(node_id, set())
                    )
                )
            ]
            node_matches = request_node_matches
            entry_session_id = _coerce_optional_string(entry.session_id)
            if not node_matches and entry_session_id is not None:
                session_node_matches = [
                    node_id
                    for node_id in sorted(self.node_groups)
                    if entry_session_id in node_session_ids_by_node.get(node_id, set())
                ]
                if len(session_node_matches) == 1:
                    node_matches = session_node_matches
            link_payload = self.build_tool_call_link(
                entry,
                node_id=node_matches[0] if len(node_matches) == 1 else None,
            )
            if len(node_matches) == 1:
                self.tool_call_links_by_node.setdefault(node_matches[0], []).append(link_payload)
                continue
            self.run_tool_call_links.append(link_payload)

    def _build_nodes_and_tool_links(self) -> None:
        self.node_details = [
            self.build_node_detail(node_id, record_entries)
            for node_id, record_entries in sorted(self.node_groups.items())
        ]

        self.deduped_run_tool_call_links: list[WorkflowRunToolCallLinkPayload] = []
        seen_run_tool_call_keys: set[
            tuple[str | None, str | None, str | None, str | None, datetime]
        ] = set()
        for tool_call_link in sorted(
            self.run_tool_call_links,
            key=lambda item: (
                item.created_at,
                item.tool_name or "",
                item.request_id or "",
                item.correlation_id or "",
                item.approval_request_id or "",
            ),
            reverse=True,
        ):
            tool_call_key = (
                tool_call_link.tool_name,
                tool_call_link.request_id,
                tool_call_link.correlation_id,
                tool_call_link.approval_request_id,
                tool_call_link.created_at,
            )
            if tool_call_key in seen_run_tool_call_keys:
                continue
            seen_run_tool_call_keys.add(tool_call_key)
            self.deduped_run_tool_call_links.append(tool_call_link)
        if self.tool_call_audit_entries:
            self.graph_data_availability.tool_calls = "available"

    def _build_recovery_and_trigger(self) -> None:
        self.resumable_node = next(
            (node for node in self.node_details if node.retry_resume_eligible),
            None,
        )
        self.resume_action = WorkflowRunRecoveryActionPayload(
            eligible=self.resumable_node is not None,
            reason=(
                self.resumable_node.retry_resume_reason
                if self.resumable_node is not None
                else "No failed node with a resumable input payload was found."
            ),
            event_id=self.selected_event_id,
            target_node_id=self.resumable_node.node_id if self.resumable_node is not None else None,
            source_node_id=self.resumable_node.records[0].source_node_id
            if self.resumable_node and self.resumable_node.records
            else None,
            source_trigger_type=self.resumable_node.records[0].source_trigger_type
            if self.resumable_node and self.resumable_node.records
            else None,
            source_event_id=self.resumable_node.records[0].event_id
            if self.resumable_node and self.resumable_node.records
            else None,
        )
        self.clear_action = WorkflowRunRecoveryActionPayload(
            eligible=True,
            reason="Workflow run records can be cleared from the editor.",
            event_id=self.selected_event_id,
            source_event_id=self.selected_event_id,
        )

        self.trigger_payload = None
        if self.trigger_record is not None:
            self.trigger_payload = WorkflowRunTriggerEventPayload(
                event_id=_coerce_optional_string(self.trigger_record.get("event_id"))
                or self.selected_event_id,
                trigger_id=_coerce_optional_string(self.trigger_record.get("trigger_id")) or "",
                trigger_type=_coerce_optional_string(self.trigger_record.get("trigger_type")) or "",
                trace_id=_coerce_optional_string(self.trigger_record.get("trace_id")) or "",
                occurred_at=_coerce_optional_datetime(self.trigger_record.get("occurred_at")),
                delivery_state=_coerce_optional_string(self.trigger_record.get("delivery_state")),
                inflight_at=_coerce_optional_datetime(self.trigger_record.get("inflight_at")),
                delivered_at=_coerce_optional_datetime(self.trigger_record.get("delivered_at")),
                clear_reason=_coerce_optional_string(self.trigger_record.get("clear_reason")),
                error=_coerce_optional_string(self.trigger_record.get("error")),
                idempotency_key=_coerce_optional_string(self.trigger_record.get("idempotency_key")),
                payload=self.normalize_record_payload(self.trigger_record.get("payload")),
            )

        if self.approval_links_by_node:
            self.graph_data_availability.approvals = "available"
        if self.agent_links_by_node:
            self.graph_data_availability.agent_links = "available"
        if self.artifact_links_by_node:
            self.graph_data_availability.communication_artifacts = "available"
        if any(node.binding_trace is not None for node in self.node_details):
            self.graph_data_availability.binding_trace = "available"

    def _build_detail_payload(self) -> WorkflowRunDetailPayload:
        selected_summary = WorkflowRunSummaryPayload.model_validate(
            {
                "event_id": self.selected_event_id,
                "run_state": "errored"
                if any(node.status == "failed" for node in self.node_details)
                else "error_routed"
                if any(node.status == "error_routed" for node in self.node_details)
                else "active",
                "trace_id": self.selected_trace_id,
                "source_node_id": _coerce_optional_string(
                    (self.trigger_record or {}).get("trigger_id") if self.trigger_record else None
                ),
                "source_trigger_type": _coerce_optional_string(
                    (self.trigger_record or {}).get("trigger_type") if self.trigger_record else None
                ),
                "delivery_state": _coerce_optional_string(
                    (self.trigger_record or {}).get("delivery_state")
                    if self.trigger_record
                    else None
                ),
                "occurred_at": _coerce_optional_datetime(
                    (self.trigger_record or {}).get("occurred_at") if self.trigger_record else None
                ),
                "latest_activity_at": self.selected_group.get("latest_activity_at"),
                "active_nodes": [
                    self.build_node_activity_payload(
                        source_kind=source_kind,
                        record=record,
                        node_id=node_id,
                    )
                    for node_id, record_entries in sorted(self.node_groups.items())
                    for source_kind, record, _record_node_id, node_type in record_entries
                ],
                "failed_node_id": next(
                    (node.node_id for node in self.node_details if node.status == "failed"),
                    None,
                ),
                "failed_node_status": "failed"
                if any(node.status == "failed" for node in self.node_details)
                else None,
                "failed_at": next(
                    (
                        node.finished_at or node.started_at
                        for node in self.node_details
                        if node.status == "failed"
                    ),
                    None,
                ),
                "error": next((node.error for node in self.node_details if node.error), None),
                "resumable": self.resumable_node is not None,
            }
        )

        return WorkflowRunDetailPayload(
            generated_at=_utc_now(),
            lookup=self.lookup,
            candidate_matches=self.candidate_matches,
            lookup_candidates=self.candidate_matches,
            matched_by=self.selected_matched_by,
            event_id=selected_summary.event_id,
            trace_id=selected_summary.trace_id,
            source_node_id=selected_summary.source_node_id,
            source_trigger_type=selected_summary.source_trigger_type,
            delivery_state=selected_summary.delivery_state,
            occurred_at=selected_summary.occurred_at,
            latest_activity_at=selected_summary.latest_activity_at,
            run_state=selected_summary.run_state,
            active_nodes=selected_summary.active_nodes,
            failed_node_id=selected_summary.failed_node_id,
            failed_node_status=selected_summary.failed_node_status,
            failed_at=selected_summary.failed_at,
            error=selected_summary.error,
            resumable=selected_summary.resumable,
            recovery=WorkflowRunRecoveryPayload(clear=self.clear_action, resume=self.resume_action),
            trigger_event=self.trigger_payload,
            graph=WorkflowRunGraphPayload(
                config_revision=self.config_revision,
                config=self.normalize_record_payload(self.document_config),
                snapshot=self.graph_snapshot,
                data_availability=self.graph_data_availability,
            ),
            nodes=self.node_details,
            tool_call_links=self.deduped_run_tool_call_links,
            data_availability=self.graph_data_availability,
        )

    def normalize_record_payload(self, record: object) -> dict[str, Any]:
        if isinstance(record, BaseModel):
            return record.model_dump(mode="json")
        if isinstance(record, dict):
            return dict(record)
        return {}

    def latest_of(self, *values: object) -> datetime | None:
        parsed = [
            parsed_value
            for parsed_value in (_coerce_optional_datetime(value) for value in values)
            if parsed_value is not None
        ]
        return max(parsed) if parsed else None

    def duration_ms(self, started_at: datetime | None, finished_at: datetime | None) -> int | None:
        if started_at is None or finished_at is None:
            return None
        delta = finished_at - started_at
        return max(0, int(delta.total_seconds() * 1000))

    def collect_keyed_values(self, payload: Any, *, key_suffix: str | None = None) -> list[str]:
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
                if key_suffix is not None:
                    if key is None or not key.lower().endswith(key_suffix):
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

    def collect_artifact_ids(self, payload: Any) -> list[str]:
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

    def ensure_group(self, groups: dict[str, dict[str, Any]], event_key: str) -> dict[str, Any]:
        group = groups.get(event_key)
        if group is None:
            group = {
                "event_id": event_key,
                "trigger": None,
                "effect_records": [],
                "gate_records": [],
                "await_agent_records": [],
                "trace_ids": set(),
                "session_ids": set(),
                "artifact_ids": set(),
                "request_ids": set(),
                "latest_activity_at": None,
                "occurred_at": None,
                "config_revisions": [],
            }
            groups[event_key] = group
        return group

    def update_group_metadata(
        self,
        group: dict[str, Any],
        *,
        trace_id_value: str | None = None,
        session_ids: list[str] | None = None,
        artifact_ids: list[str] | None = None,
        request_ids: list[str] | None = None,
        occurred_at_value: datetime | None = None,
        latest_activity_at_value: datetime | None = None,
        config_revision_value: str | None = None,
    ) -> None:
        if trace_id_value:
            group["trace_ids"].add(trace_id_value)
        for value in session_ids or []:
            group["session_ids"].add(value)
        for value in artifact_ids or []:
            group["artifact_ids"].add(value)
        for value in request_ids or []:
            group["request_ids"].add(value)
        if occurred_at_value is not None and group.get("occurred_at") is None:
            group["occurred_at"] = occurred_at_value
        previous_latest = group.get("latest_activity_at")
        if latest_activity_at_value is not None and (
            not isinstance(previous_latest, datetime) or latest_activity_at_value > previous_latest
        ):
            group["latest_activity_at"] = latest_activity_at_value
        if config_revision_value:
            revisions = group["config_revisions"]
            if config_revision_value not in revisions:
                revisions.append(config_revision_value)

    def register_trigger(
        self, groups: dict[str, dict[str, Any]], raw_record: dict[str, Any], *, event_key: str
    ) -> None:
        group = self.ensure_group(groups, event_key)
        group["trigger"] = raw_record
        payload = raw_record.get("payload")
        self.update_group_metadata(
            group,
            trace_id_value=_coerce_optional_string(raw_record.get("trace_id")),
            session_ids=self.collect_keyed_values(raw_record, key_suffix="session_id"),
            artifact_ids=self.collect_artifact_ids(payload),
            request_ids=self.collect_keyed_values(raw_record, key_suffix="request_id"),
            occurred_at_value=_coerce_optional_datetime(raw_record.get("occurred_at")),
            latest_activity_at_value=self.latest_of(
                raw_record.get("delivered_at"),
                raw_record.get("inflight_at"),
                raw_record.get("occurred_at"),
            ),
        )

    def register_execution(
        self,
        groups: dict[str, dict[str, Any]],
        raw_record: dict[str, Any],
        *,
        event_key: str,
        kind: str,
    ) -> None:
        group = self.ensure_group(groups, event_key)
        group[f"{kind}_records"].append(raw_record)
        payload_sources = (
            raw_record.get("resolved_config"),
            raw_record.get("input_payload"),
            raw_record.get("outputs"),
        )
        self.update_group_metadata(
            group,
            trace_id_value=_coerce_optional_string(raw_record.get("trace_id")),
            session_ids=[
                value
                for payload_source in payload_sources
                for value in self.collect_keyed_values(payload_source, key_suffix="session_id")
            ],
            artifact_ids=[
                value
                for payload_source in payload_sources
                for value in self.collect_artifact_ids(payload_source)
            ],
            request_ids=[
                value
                for payload_source in payload_sources
                for value in self.collect_keyed_values(payload_source, key_suffix="request_id")
            ],
            latest_activity_at_value=self.latest_of(
                raw_record.get("finished_at"),
                raw_record.get("started_at"),
            ),
            config_revision_value=_coerce_optional_string(raw_record.get("config_revision")),
        )

    def score_group(self, group: dict[str, Any]) -> tuple[int, list[str]]:
        matched_by: list[str] = []
        score = 0
        event_keys = {
            value
            for value in (self.lookup.run_id, self.lookup.event_id, self.lookup.trigger_event_id)
            if value
        }
        if group["event_id"] in event_keys:
            score += 1000
            matched_by.append("event_id")
        group_trace_ids = set(str(value) for value in group["trace_ids"])
        if self.lookup.trace_id and self.lookup.trace_id in group_trace_ids:
            score += 900
            matched_by.append("trace_id")
        if self.lookup.artifact_id and self.lookup.artifact_id in group["artifact_ids"]:
            score += 850
            matched_by.append("artifact_id")
        for session_candidate in self.lookup_session_candidates:
            if session_candidate and session_candidate in group["session_ids"]:
                score += 800
                matched_by.append("session_id")
                break
        for request_id in group["request_ids"]:
            request = self.approval_requests_by_id.get(request_id)
            if request is None:
                continue
            if self.lookup.session_id and request.session_id == self.lookup.session_id:
                score += 750
                matched_by.append("approval_request_session")
                break
        if self.lookup.session_id:
            for request in self.approval_requests_by_session.get(self.lookup.session_id, []):
                if request.request_id in group["request_ids"]:
                    score += 700
                    matched_by.append("approval_request_id")
                    break
        if self.artifact_lookup_payload is not None:
            artifact_session_id = _coerce_optional_string(
                self.artifact_lookup_payload.get("session_id")
            )
            if artifact_session_id and artifact_session_id in group["session_ids"]:
                score += 650
                matched_by.append("artifact_session_id")
        return score, matched_by

    def _record_latest_activity(self, record: dict[str, Any]) -> datetime | None:
        return self.latest_of(
            record.get("finished_at"),
            record.get("started_at"),
            record.get("delivered_at"),
            record.get("inflight_at"),
            record.get("occurred_at"),
        )

    def register_node_metadata(self, config: dict[str, Any]) -> None:
        for collection_name in ("triggers", "gates", "effects", "queries"):
            entries = config.get(collection_name)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                node_id = _coerce_optional_string(entry.get("id"))
                if not node_id:
                    continue
                self.node_metadata_by_id[node_id] = {
                    "node_type": _coerce_optional_string(entry.get("type")),
                    "display_name": _coerce_optional_string(entry.get("display_name")),
                }

    def tool_call_entry_matches_selected_run(self, entry: ToolCallAuditEntryPayload) -> bool:
        entry_run_id = _coerce_optional_string(entry.workflow_run_id)
        if entry_run_id is not None and entry_run_id in {
            self.selected_event_id,
            self.selected_trace_id,
        }:
            return True
        entry_trace_id = _coerce_optional_string(entry.trace_id)
        if entry_trace_id is not None:
            return self.selected_trace_id is not None and entry_trace_id == self.selected_trace_id
        if entry_run_id is not None:
            return False
        entry_session_id = _coerce_optional_string(entry.session_id)
        return entry_session_id is not None and entry_session_id in self.selected_session_ids

    def build_node_record_payload(
        self,
        *,
        record_kind: str,
        record: dict[str, Any],
        node_id: str | None,
        node_type: str | None,
    ) -> WorkflowRunNodeRecordPayload:
        started_at = _coerce_optional_datetime(record.get("started_at"))
        finished_at = _coerce_optional_datetime(record.get("finished_at"))
        occurred_at = _coerce_optional_datetime(record.get("occurred_at"))
        return WorkflowRunNodeRecordPayload(
            record_kind=record_kind,
            execution_id=_coerce_optional_string(record.get("execution_id")),
            event_id=_coerce_optional_string(record.get("event_id")),
            trace_id=_coerce_optional_string(record.get("trace_id")),
            node_id=node_id,
            node_type=node_type,
            status=_coerce_optional_string(record.get("status"))
            or _coerce_optional_string(record.get("delivery_state")),
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=self.duration_ms(started_at, finished_at),
            source_node_id=_coerce_optional_string(record.get("source_node_id")),
            source_trigger_type=_coerce_optional_string(record.get("source_trigger_type")),
            delivery_state=_coerce_optional_string(record.get("delivery_state")),
            occurred_at=occurred_at,
            input_payload=self.normalize_record_payload(record.get("input_payload")),
            outputs=self.normalize_record_payload(record.get("outputs")),
            resolved_config=self.normalize_record_payload(record.get("resolved_config")),
            binding_trace=self.normalize_record_payload(record.get("binding_trace")) or None,
            binding_error=_coerce_optional_string(record.get("binding_error")),
            error=_coerce_optional_string(record.get("error")),
            published_event_id=_coerce_optional_string(record.get("published_event_id")),
            completion_event_enqueued_at=_coerce_optional_datetime(
                record.get("completion_event_enqueued_at")
            ),
            config_revision=_coerce_optional_string(record.get("config_revision")),
        )

    def build_node_activity_payload(
        self,
        *,
        source_kind: str,
        record: dict[str, Any],
        node_id: str | None,
    ) -> WorkflowRunNodeActivityPayload:
        started_at = _coerce_optional_datetime(record.get("started_at"))
        finished_at = _coerce_optional_datetime(record.get("finished_at"))
        status = _coerce_optional_string(record.get("status"))
        if status is None:
            status = _coerce_optional_string(record.get("delivery_state"))
        dispatch_id = _coerce_optional_string(
            self.normalize_record_payload(record.get("outputs")).get("dispatch_id")
            if isinstance(record.get("outputs"), (dict, BaseModel))
            else None
        )
        if dispatch_id is None:
            dispatch_id = _coerce_optional_string(
                self.normalize_record_payload(record.get("input_payload")).get("dispatch_id")
            )
        return WorkflowRunNodeActivityPayload(
            node_id=node_id or "",
            status=status or "unknown",
            count=1,
            source=source_kind,
            started_at=started_at,
            finished_at=finished_at,
            dispatch_id=dispatch_id,
            error=_coerce_optional_string(record.get("error")),
        )

    def add_node_record(
        self,
        *,
        source_kind: str,
        record: dict[str, Any],
        node_id: str | None,
        node_type: str | None,
    ) -> None:
        if not node_id:
            return
        self.node_groups.setdefault(node_id, []).append((source_kind, record, node_id, node_type))

    def build_approval_link(self, request: ApprovalRequest) -> WorkflowRunApprovalLinkPayload:
        arguments = request.payload.get("arguments")
        connector_arguments = arguments if isinstance(arguments, dict) else {}
        return WorkflowRunApprovalLinkPayload(
            request_id=request.request_id,
            session_id=request.session_id,
            action_type=request.action_type,
            tool_name=request.tool_name,
            connector_id=_coerce_optional_string(connector_arguments.get("connector_id")),
            connector_operation=_coerce_optional_string(connector_arguments.get("operation")),
            directory_path=request.directory_path,
            payload_digest=request.payload_digest,
            status=request.status.value,
            approver=request.approver,
            decision_at=request.decision_at,
            decision_reason=request.decision_reason,
            expires_at=request.expires_at,
            human_summary=request.human_summary,
        )

    def build_artifact_link(
        self, artifact_payload: dict[str, Any], *, status: str | None = None
    ) -> WorkflowRunCommunicationArtifactLinkPayload:
        return WorkflowRunCommunicationArtifactLinkPayload(
            artifact_id=str(artifact_payload.get("artifact_id") or ""),
            session_id=_coerce_optional_string(artifact_payload.get("session_id")),
            provider=_coerce_optional_string(artifact_payload.get("provider")),
            source_message_id=_coerce_optional_string(artifact_payload.get("source_message_id")),
            source_attachment_id=_coerce_optional_string(
                artifact_payload.get("source_attachment_id")
            ),
            source_channel=_coerce_optional_string(artifact_payload.get("source_channel")),
            source_mailbox=_coerce_optional_string(artifact_payload.get("source_mailbox")),
            original_filename=_coerce_optional_string(artifact_payload.get("original_filename")),
            filename=_coerce_optional_string(artifact_payload.get("filename")),
            mime_type=_coerce_optional_string(artifact_payload.get("mime_type")),
            byte_count=(
                int(artifact_payload["byte_count"])
                if artifact_payload.get("byte_count") is not None
                else None
            ),
            sha256=_coerce_optional_string(artifact_payload.get("sha256")),
            created_at=_coerce_optional_datetime(artifact_payload.get("created_at")),
            expires_at=_coerce_optional_datetime(artifact_payload.get("expires_at")),
            staged_at=_coerce_optional_datetime(artifact_payload.get("staged_at")),
            status=status,
            metadata=self.normalize_record_payload(artifact_payload.get("metadata")),
        )

    def infer_agent_backend(
        self,
        session: SessionManifest | None,
        *,
        vm_name: str | None,
    ) -> str | None:
        if session is None:
            return "libvirt_vm" if vm_name else None
        metadata = dict(session.metadata) if isinstance(session.metadata, dict) else {}
        execution_backend = _coerce_optional_string(metadata.get("execution_backend"))
        if execution_backend == "container_worker_vm":
            return execution_backend
        if _coerce_optional_string(session.vm_provider) == "container_worker_vm":
            return "container_worker_vm"
        if _coerce_optional_string(session.vm_provider_id) or _coerce_optional_string(
            session.vm_id
        ):
            return "libvirt_vm"
        return "libvirt_vm" if vm_name else None

    def select_agent_record_for_link(
        self,
        *,
        session_id: str | None,
        vm_name: str | None,
        backend_hint: str | None,
    ) -> AgentRecord | None:
        candidates: list[AgentRecord] = []
        if session_id:
            candidates.extend(self.agent_records_by_session_id.get(session_id, []))
        if not candidates and vm_name:
            candidates.extend(self.agent_records_by_target_name.get(vm_name, []))
        if not candidates:
            return None
        if backend_hint is not None:
            for candidate in candidates:
                if candidate.backend == backend_hint:
                    return candidate
        return candidates[0]

    def build_tool_call_href(self, entry: ToolCallAuditEntryPayload) -> str | None:
        for candidate in (
            entry.request_id,
            entry.correlation_id,
            entry.approval_request_id,
            entry.trace_id,
            entry.session_id,
        ):
            text = str(candidate or "").strip()
            if text:
                return f"#tool-calls?q={quote(text, safe='')}"
        return None

    def compact_payload_summary(self, value: Any, *, max_length: int = 180) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
        elif isinstance(value, (dict, list)):
            if not value:
                return None
            text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        else:
            text = str(value).strip()
        if not text:
            return None
        if len(text) <= max_length:
            return text
        return f"{text[: max_length - 1].rstrip()}..."

    def build_tool_call_link(
        self,
        entry: ToolCallAuditEntryPayload,
        *,
        node_id: str | None = None,
    ) -> WorkflowRunToolCallLinkPayload:
        payload = entry.model_dump(mode="python", exclude={"related_links"})
        payload["node_id"] = node_id
        payload["href"] = self.build_tool_call_href(entry)
        filter_payload: dict[str, Any] = {}
        if entry.request_id:
            filter_payload["q"] = entry.request_id
        elif entry.correlation_id:
            filter_payload["q"] = entry.correlation_id
        elif entry.approval_request_id:
            filter_payload["q"] = entry.approval_request_id
        elif entry.trace_id:
            filter_payload["q"] = entry.trace_id
        elif entry.session_id:
            filter_payload["q"] = entry.session_id
        payload["filter"] = filter_payload or None
        payload["redacted_args"] = dict(entry.args)
        payload["redacted_result"] = dict(entry.result)
        payload["args_summary"] = self.compact_payload_summary(entry.args)
        payload["args_missing_reason"] = None if entry.args else "Args payload missing."
        payload["result_summary"] = self.compact_payload_summary(entry.result)
        payload["result_empty"] = not bool(entry.result)
        payload["result_status"] = "available" if entry.result else "recorded_empty"
        payload["result_missing_reason"] = (
            None if entry.result else "Tool call recorded an empty result."
        )
        return WorkflowRunToolCallLinkPayload.model_validate(payload)

    def build_agent_link(
        self,
        *,
        node_id: str,
        node_type: str | None,
        record: dict[str, Any],
        source_kind: str,
    ) -> WorkflowRunAgentLinkPayload | None:
        resolved_config = self.normalize_record_payload(record.get("resolved_config"))
        input_payload = self.normalize_record_payload(record.get("input_payload"))
        outputs = self.normalize_record_payload(record.get("outputs"))
        merged = {**resolved_config, **input_payload, **outputs}
        session_candidates = self.collect_keyed_values(merged, key_suffix="session_id")
        vm_name = _coerce_optional_string(merged.get("vm_name"))
        prompt = _coerce_optional_string(
            merged.get("prompt") or merged.get("content") or merged.get("message")
        )
        dispatch_id = _coerce_optional_string(
            merged.get("dispatch_id") or outputs.get("dispatch_id") or record.get("dispatch_id")
        )
        thread_id = _coerce_optional_string(
            merged.get("thread_id") or record.get("thread_id") or outputs.get("thread_id")
        )
        execution_id = _coerce_optional_string(
            merged.get("execution_id") or record.get("execution_id") or outputs.get("execution_id")
        )
        artifact_ids = self.collect_artifact_ids(merged)
        status = _coerce_optional_string(record.get("status"))
        waiting = status == "waiting"
        completed = status in {"succeeded", "failed", "completed", "dismissed"}
        if not any(
            (
                session_candidates,
                vm_name,
                prompt,
                dispatch_id,
                thread_id,
                artifact_ids,
                waiting,
            )
        ):
            return None
        session_id = session_candidates[0] if session_candidates else None
        session = (
            self.service.runtime.session_repository.get_session(session_id)
            if session_id is not None
            else None
        )
        backend_hint = self.infer_agent_backend(session, vm_name=vm_name)
        selected_agent_record = self.select_agent_record_for_link(
            session_id=session_id,
            vm_name=vm_name,
            backend_hint=backend_hint,
        )
        agent_id = (
            _coerce_optional_string(selected_agent_record.agent_id)
            if selected_agent_record is not None
            else session_id or vm_name
        )
        backend = (
            _coerce_optional_string(selected_agent_record.backend)
            if selected_agent_record is not None
            else backend_hint
        )
        target_name = (
            _coerce_optional_string(selected_agent_record.target_name)
            if selected_agent_record is not None
            else _coerce_optional_string(session.vm_id if session is not None else None) or vm_name
        )
        session_status = "available" if session is not None else "session_missing"
        session_missing_reason = None if session is not None else "Session record missing."
        agent_available = selected_agent_record is not None
        agent_status = (
            _coerce_optional_string(selected_agent_record.status)
            if selected_agent_record is not None
            else "missing"
        )
        agent_missing_reason = (
            None if selected_agent_record is not None else "No agent record matched the session."
        )
        log_status = "log_unavailable"
        log_reference_href = None
        log_missing_reason = None
        if session is None:
            log_missing_reason = session_missing_reason
        elif selected_agent_record is None:
            log_missing_reason = agent_missing_reason
        elif selected_agent_record.capabilities.get("logs"):
            log_status = "log_available"
            log_reference_href = (
                f"/api/admin/agents/{quote(selected_agent_record.agent_id, safe='')}/log"
                f"?backend={quote(selected_agent_record.backend, safe='')}"
            )
        else:
            log_missing_reason = "Agent record does not advertise log support."
        if "outputs" not in record:
            result_status = "missing"
            result_summary = None
            result_missing_reason = "Result payload missing."
            result_empty = None
        elif outputs:
            result_status = "available"
            result_summary = self.compact_payload_summary(outputs)
            result_missing_reason = None
            result_empty = False
        else:
            result_status = "recorded_empty"
            result_summary = "Empty result"
            result_missing_reason = None
            result_empty = True
        prompt_summary = prompt
        prompt_missing_reason = None if prompt_summary else "Prompt summary missing."
        return WorkflowRunAgentLinkPayload(
            node_id=node_id,
            node_type=node_type,
            session_id=session_id,
            agent_id=agent_id,
            backend=backend,
            agent_backend=backend,
            agent_available=agent_available,
            agent_status=agent_status,
            agent_missing_reason=agent_missing_reason,
            target_name=target_name,
            vm_name=vm_name,
            prompt=prompt,
            prompt_summary=prompt_summary,
            prompt_status="available" if prompt_summary else "missing",
            prompt_missing_reason=prompt_missing_reason,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            execution_id=execution_id,
            artifact_ids=artifact_ids,
            status=status,
            waiting=waiting,
            completed=completed,
            session_status=session_status,
            session_available=session is not None,
            session_missing_reason=session_missing_reason,
            session_href=f"#sessions?session_id={quote(session_id, safe='')}"
            if session_id
            else None,
            log_status=log_status,
            log_available=log_status == "log_available",
            log_href=log_reference_href,
            log_reference_href=log_reference_href,
            log_missing_reason=log_missing_reason,
            log_reference={
                "source_kind": source_kind,
                "node_id": node_id,
                "session_id": session_id,
                "vm_name": vm_name,
                "thread_id": thread_id,
                "execution_id": execution_id,
                "dispatch_id": dispatch_id,
            },
            result_summary=result_summary,
            result_status=result_status,
            result_missing_reason=result_missing_reason,
            result_empty=result_empty,
        )

    def build_node_detail(
        self, node_id: str, record_entries: list[tuple[str, dict[str, Any], str | None, str | None]]
    ) -> WorkflowRunNodeDetailPayload:
        ordered_entries = sorted(
            record_entries,
            key=lambda item: (
                self._record_latest_activity(item[1]) or datetime.min.replace(tzinfo=UTC),
                _coerce_optional_string(item[1].get("execution_id")) or "",
            ),
            reverse=True,
        )
        latest_source_kind, latest_record, _latest_node_id, latest_node_type = ordered_entries[0]
        node_metadata = self.node_metadata_by_id.get(node_id, {})
        latest_started_at = _coerce_optional_datetime(latest_record.get("started_at"))
        latest_finished_at = _coerce_optional_datetime(latest_record.get("finished_at"))
        latest_occurred_at = _coerce_optional_datetime(latest_record.get("occurred_at"))
        latest_status = _coerce_optional_string(latest_record.get("status"))
        if latest_status is None:
            latest_status = _coerce_optional_string(latest_record.get("delivery_state"))
        retry_resume_reason: str | None = None
        retry_resume_eligible = False
        if latest_status == "failed":
            resumable_record = self.service._latest_resumable_failed_workflow_record(
                event_id=self.selected_event_id,
                effect_payload={
                    key: value
                    for key, value in (
                        (
                            _coerce_optional_string(record.get("execution_id")) or node_id,
                            record,
                        )
                        for (
                            _source_kind,
                            record,
                            _record_node_id,
                            _record_node_type,
                        ) in ordered_entries
                        if _source_kind == "effect_execution"
                    )
                },
                gate_payload={
                    key: value
                    for key, value in (
                        (
                            _coerce_optional_string(record.get("execution_id")) or node_id,
                            record,
                        )
                        for (
                            _source_kind,
                            record,
                            _record_node_id,
                            _record_node_type,
                        ) in ordered_entries
                        if _source_kind == "gate_execution"
                    )
                },
            )
            if resumable_record is not None:
                retry_resume_eligible = resumable_record["node_id"] == node_id
                if retry_resume_eligible:
                    retry_resume_reason = "Failed node has a resumable input payload."
            elif latest_status == "failed":
                retry_resume_reason = (
                    "Failed node is missing source_node_id, source_trigger_type, "
                    "trace_id, or input_payload."
                )
        missing_fields: list[str] = []
        if latest_source_kind == "trigger_event":
            if not _coerce_optional_string(latest_record.get("delivery_state")):
                missing_fields.append("delivery_state")
            if not latest_record.get("payload"):
                missing_fields.append("payload")
            if latest_record.get("binding_trace") is None:
                missing_fields.append("binding_trace")
        else:
            for field_name in ("resolved_config", "input_payload", "outputs"):
                if not latest_record.get(field_name):
                    missing_fields.append(field_name)
            if latest_record.get("binding_trace") is None:
                missing_fields.append("binding_trace")
        approval_links = self.approval_links_by_node.get(node_id, [])
        agent_links = self.agent_links_by_node.get(node_id, [])
        artifact_links = self.artifact_links_by_node.get(node_id, [])
        deduped_approval_links: list[WorkflowRunApprovalLinkPayload] = []
        seen_approval_ids: set[str] = set()
        for approval_link in approval_links:
            if approval_link.request_id in seen_approval_ids:
                continue
            seen_approval_ids.add(approval_link.request_id)
            deduped_approval_links.append(approval_link)
        deduped_agent_links: list[WorkflowRunAgentLinkPayload] = []
        seen_agent_keys: set[tuple[str | None, str | None, str | None]] = set()
        for agent_link in agent_links:
            agent_key = (agent_link.session_id, agent_link.dispatch_id, agent_link.execution_id)
            if agent_key in seen_agent_keys:
                continue
            seen_agent_keys.add(agent_key)
            deduped_agent_links.append(agent_link)
        deduped_artifact_links: list[WorkflowRunCommunicationArtifactLinkPayload] = []
        seen_artifact_ids: set[str] = set()
        for artifact_link in artifact_links:
            if artifact_link.artifact_id in seen_artifact_ids:
                continue
            seen_artifact_ids.add(artifact_link.artifact_id)
            deduped_artifact_links.append(artifact_link)
        tool_call_links = self.tool_call_links_by_node.get(node_id, [])
        deduped_tool_call_links: list[WorkflowRunToolCallLinkPayload] = []
        seen_tool_call_keys: set[
            tuple[str | None, str | None, str | None, str | None, datetime]
        ] = set()
        for tool_call_link in tool_call_links:
            tool_call_key = (
                tool_call_link.tool_name,
                tool_call_link.request_id,
                tool_call_link.correlation_id,
                tool_call_link.approval_request_id,
                tool_call_link.created_at,
            )
            if tool_call_key in seen_tool_call_keys:
                continue
            seen_tool_call_keys.add(tool_call_key)
            deduped_tool_call_links.append(tool_call_link)
        return WorkflowRunNodeDetailPayload(
            node_id=node_id,
            node_type=_coerce_optional_string(latest_node_type)
            or _coerce_optional_string(node_metadata.get("node_type")),
            display_name=_coerce_optional_string(node_metadata.get("display_name")),
            source_kind=latest_source_kind,
            execution_id=_coerce_optional_string(latest_record.get("execution_id")),
            event_id=_coerce_optional_string(latest_record.get("event_id")),
            trace_id=_coerce_optional_string(latest_record.get("trace_id")),
            status=latest_status,
            started_at=latest_started_at,
            finished_at=latest_finished_at,
            duration_ms=self.duration_ms(latest_started_at, latest_finished_at),
            occurred_at=latest_occurred_at,
            delivery_state=_coerce_optional_string(latest_record.get("delivery_state")),
            resolved_config=self.normalize_record_payload(latest_record.get("resolved_config")),
            binding_trace=self.normalize_record_payload(latest_record.get("binding_trace")) or None,
            binding_error=_coerce_optional_string(latest_record.get("binding_error")),
            input_payload=self.normalize_record_payload(latest_record.get("input_payload")),
            outputs=self.normalize_record_payload(latest_record.get("outputs")),
            inputs_by_source=self.normalize_record_payload(latest_record.get("inputs_by_source")),
            input_event_ids_by_source=self.normalize_record_payload(
                latest_record.get("input_event_ids_by_source")
            ),
            input_occurred_at_by_source={
                key: _coerce_optional_datetime(value)
                for key, value in self.normalize_record_payload(
                    latest_record.get("input_occurred_at_by_source")
                ).items()
                if _coerce_optional_datetime(value) is not None
            },
            error=_coerce_optional_string(latest_record.get("error")),
            published_event_id=_coerce_optional_string(latest_record.get("published_event_id")),
            completion_event_enqueued_at=_coerce_optional_datetime(
                latest_record.get("completion_event_enqueued_at")
            ),
            config_revision=_coerce_optional_string(latest_record.get("config_revision")),
            retry_resume_eligible=retry_resume_eligible,
            retry_resume_reason=retry_resume_reason,
            missing_fields=sorted(dict.fromkeys(missing_fields)),
            approval_links=deduped_approval_links,
            agent_links=deduped_agent_links,
            communication_artifact_links=deduped_artifact_links,
            tool_call_links=deduped_tool_call_links,
            records=[
                self.build_node_record_payload(
                    record_kind=source_kind,
                    record=record,
                    node_id=node_id,
                    node_type=node_type,
                )
                for source_kind, record, _node_id, node_type in ordered_entries
            ],
        )
