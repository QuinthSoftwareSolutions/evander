"""Guided creation of editable workflow packs and evidence-based first-run checks."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from evander.admin.payloads import AdminRevisionConflict, WorkflowNodeTriggerRequestPayload
from evander.integration_contracts import IntegrationStarterVerification, validate_integration_input
from evander.storage import write_json_atomically
from evander.workflow_starters import SCHEDULED_STARTER


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class WorkflowStarterAdminService:
    def __init__(self, *, integrations, workflows, reports, sessions, runtime_dir: Path):
        self.integrations = integrations
        self.workflows = workflows
        self.reports = reports
        self.sessions = sessions
        self._receipts_path = runtime_dir / "workflow-starter-instances.json"

    def _receipts(self) -> dict[str, Any]:
        if not self._receipts_path.exists():
            return {}
        return json.loads(self._receipts_path.read_text(encoding="utf-8"))

    def catalog(self, directory_path: str) -> dict[str, Any]:
        context = self.integrations.context("workflow-starters", directory_path)
        starters = [
            {
                "id": "core/scheduled-task",
                "label": SCHEDULED_STARTER.label,
                "description": SCHEDULED_STARTER.description,
                "provider": "Evander",
                "input_schema": SCHEDULED_STARTER.input_schema,
            }
        ]
        for integration in self.integrations.registry.describe():
            for starter in integration["workflow_starters"]:
                starters.append(
                    {
                        **starter,
                        "id": f"{integration['addon_id']}/{starter['id']}",
                        "provider": integration["display_name"],
                    }
                )
        sessions = self.sessions.list_sessions(directory_path=context.directory_path)
        return {
            "directory_path": context.directory_path,
            "starters": starters,
            "agents": [
                {
                    "value": session.session_id,
                    "label": str(session.metadata.get("display_name") or session.session_id),
                }
                for session in sessions
                if session.vm_id
            ],
            "instances": [
                value
                for value in self._receipts().values()
                if value["directory_path"] == context.directory_path
            ],
        }

    def _prepare(self, starter_id: str, directory_path: str, values: dict[str, Any]):
        owner, _, contribution = starter_id.partition("/")
        context = self.integrations.context(owner, directory_path)
        checks = []
        if owner == "core" and contribution == SCHEDULED_STARTER.id:
            starter = SCHEDULED_STARTER
            validate_integration_input(starter.input_schema, values)
            pack = starter.build(context, values)
            verification = starter.verification(context, values)
        else:
            readiness = self.integrations.readiness(owner, directory_path)
            checks = readiness["checks"]
            checks.extend(
                {
                    "id": f"dependency-{item['addon_id']}",
                    "label": f"Required add-on: {item['addon_id']}",
                    "status": "blocked",
                    "required": True,
                    "code": item["code"],
                }
                for item in readiness["dependency_issues"]
            )
            if readiness["status"] != "ready":
                return None, None, checks, context
            pack = self.integrations.registry.build_workflow_starter(
                owner, contribution, context, values
            )
            verification = self.integrations.registry.workflow_starter_verification(
                owner, contribution, context, values
            )
        # Starters cannot silently broaden session scope or interpret form values as expressions.
        if any(isinstance(value, str) and "${{" in value for value in values.values()):
            raise ValueError(
                "Starter form values must be plain text. Add expressions later in the workflow editor."
            )
        for node in pack.ir.nodes:
            session_id = node.node.config.get("session_id")
            if session_id is None:
                continue
            session = self.sessions.get_session_summary(session_id)
            if (
                session.directory_path != context.directory_path
                or session.is_expired
                or not session.vm_id
            ):
                raise ValueError("Choose an active agent in the selected directory.")
        if verification is not None:
            if not isinstance(verification, IntegrationStarterVerification):
                raise ValueError("Invalid starter verification contract.")
            nodes = {node.node.id: node.node for node in pack.ir.nodes}
            if (
                verification.completion_node_id not in nodes
                or not verification.trigger_node_ids
                or any(
                    node_id not in nodes or not nodes[node_id].type.startswith("trigger:")
                    for node_id in verification.trigger_node_ids
                )
                or (
                    verification.manual_trigger_node_id is not None
                    and (
                        verification.manual_trigger_node_id not in verification.trigger_node_ids
                        or nodes[verification.manual_trigger_node_id].type != "trigger:event"
                    )
                )
            ):
                raise ValueError("Starter verification must refer to its own workflow nodes.")
        checks.append(
            {
                "id": "agent",
                "label": "Selected agent and directory",
                "status": "ready",
                "required": True,
            }
        )
        return pack, verification, checks, context

    def preview(
        self, starter_id: str, directory_path: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        with self.workflows.admin_config_mutation_lock():
            return self._preview_unlocked(starter_id, directory_path, values)

    def _preview_unlocked(
        self, starter_id: str, directory_path: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        pack, verification, checks, context = self._prepare(starter_id, directory_path, values)
        if pack is None:
            return {"ready": False, "checks": checks, "directory_path": context.directory_path}
        validated = self.workflows.preview_new_workflow_pack_unlocked(pack)
        payload = {
            "ready": True,
            "checks": checks,
            "directory_path": context.directory_path,
            "workflow_pack": validated["reference"],
            "workflow_revision": validated["revision"],
            "pack": pack.model_dump(mode="json"),
            "verification": asdict(verification) if verification else None,
            "diagnostics": validated["diagnostics"],
        }
        payload["preview_revision"] = _digest(payload)
        return payload

    def activate(self, starter_id, directory_path, values, *, preview_revision, actor=None):
        with self.workflows.admin_config_mutation_lock():
            # A repeated request returns the original installation, including after edits.
            for receipt in self._receipts().values():
                if (
                    receipt["preview_revision"] == preview_revision
                    and receipt["starter_id"] == starter_id
                    and receipt["directory_path"] == directory_path
                    and receipt["input_digest"] == _digest(values)
                ):
                    return receipt
            preview = self._preview_unlocked(starter_id, directory_path, values)
            if not preview["ready"]:
                raise ValueError("Complete the starter prerequisites before activation.")
            if preview["preview_revision"] != preview_revision:
                raise AdminRevisionConflict(
                    "The starter or its prerequisites changed. Preview it again.", details={}
                )
            from evander.workflow.packs import WorkflowPack

            document = self.workflows.install_new_workflow_pack_unlocked(
                WorkflowPack.model_validate(preview["pack"]),
                expected_revision=preview["workflow_revision"],
                actor=actor,
            )
            receipt = {
                "id": uuid4().hex,
                "starter_id": starter_id,
                "directory_path": preview["directory_path"],
                "workflow_pack": preview["workflow_pack"],
                "workflow_revision": document.revision,
                "created_at": datetime.now(UTC).isoformat(),
                "verification": preview["verification"],
                "preview_revision": preview_revision,
                "input_digest": _digest(values),
            }
            receipts = self._receipts()
            receipts[receipt["id"]] = receipt
            write_json_atomically(self._receipts_path, receipts, new_file_mode=0o600)
            return receipt

    def instance(self, instance_id: str, directory_path: str) -> dict[str, Any]:
        directory = self.integrations.context("workflow-starters", directory_path).directory_path
        receipt = self._receipts().get(instance_id)
        if receipt is None or receipt["directory_path"] != directory:
            raise KeyError("Unknown starter in this directory.")
        return receipt

    def run_test(self, instance_id, directory_path, *, request_id, source, authorize_directory):
        receipt = self.instance(instance_id, directory_path)
        verification = receipt["verification"] or {}
        node_id = verification.get("manual_trigger_node_id")
        if not node_id:
            raise ValueError("This starter is tested through its normal trigger.")
        return self.workflows.trigger_workflow_event_node(
            node_id,
            WorkflowNodeTriggerRequestPayload(
                workflow_pack=receipt["workflow_pack"],
                expected_revision=receipt["workflow_revision"],
                request_id=request_id,
                payload={},
            ),
            source=source,
            authorize_directory=authorize_directory,
        ).model_dump(mode="json")

    def verification(self, instance_id: str, directory_path: str, *, event_id: str | None = None):
        receipt = self.instance(instance_id, directory_path)
        spec = receipt["verification"]
        result = {"state": "waiting", "message": "Waiting for a workflow run.", "event_id": None}
        document = self.workflows.read_workflow_editor_document(
            workflow_pack=receipt["workflow_pack"]
        )
        if document.revision != receipt["workflow_revision"]:
            return {
                **result,
                "state": "changed",
                "message": "This workflow was edited. Use its run report to verify the current version.",
            }
        if not spec:
            return {
                **result,
                "state": "manual",
                "message": "Use the workflow run report to verify this starter.",
            }
        if not event_id:
            runs = self.reports.read_workflow_run_search(
                graph_id=document.graphs[0].id, limit=25
            ).runs
            runs = [
                run
                for run in runs
                if run.source_node_id in spec["trigger_node_ids"]
                and run.occurred_at
                and run.occurred_at >= datetime.fromisoformat(receipt["created_at"])
            ]
            if not runs:
                return result
            event_id = max(runs, key=lambda run: run.occurred_at).event_id
        run = self.reports.read_workflow_run_detail(event_id=event_id)
        if run.source_node_id not in spec["trigger_node_ids"]:
            raise ValueError("This run does not belong to the starter.")
        if run.occurred_at is not None and run.occurred_at < datetime.fromisoformat(
            receipt["created_at"]
        ):
            raise ValueError("This run predates starter activation.")
        result["event_id"] = run.event_id
        runs, evidence_complete = self._continuation_runs(run)
        completed = any(
            node.node_id == spec["completion_node_id"] and node.status == "succeeded"
            for continuation in runs
            for node in continuation.nodes
        )
        if any(
            continuation.run_state in {"errored", "error_routed", "cleared"}
            or continuation.failed_node_id
            for continuation in runs
        ):
            return {
                **result,
                "state": "failed",
                "message": "The workflow reported an error. Open the run report for details.",
            }
        if not evidence_complete:
            return {
                **result,
                "state": "needs_attention",
                "message": "Some continuation evidence is unavailable. Inspect the workflow run before verifying it.",
            }
        if not completed:
            return {
                **result,
                "state": "running",
                "message": "The run was received. Waiting for the task to complete.",
            }
        if spec["required_tool"]:
            run_ids = {continuation.event_id for continuation in runs}
            delivered = any(
                link.tool_name == spec["required_tool"]
                and link.status == "success"
                and (
                    link.workflow_run_id in run_ids
                    or (run.trace_id and link.trace_id == run.trace_id)
                )
                and all(
                    link.args.get(key) == value
                    for key, value in spec["required_tool_arguments"].items()
                )
                for link in [
                    *(link for continuation in runs for link in continuation.tool_call_links),
                    *(
                        link
                        for continuation in runs
                        for node in continuation.nodes
                        for link in node.tool_call_links
                    ),
                ]
            )
            if not delivered:
                return {
                    **result,
                    "state": "needs_attention",
                    "message": "The agent completed, but a successful reply to the selected target was not recorded. Check the run report and Teams.",
                }
        return {
            **result,
            "state": "verified",
            "message": "The task completed and its required outcome was recorded.",
        }

    def _continuation_runs(self, root) -> tuple[list[Any], bool]:
        """Follow persisted publication links, never unrelated runs sharing a session or trace."""
        runs = [root]
        seen = {root.event_id}
        for run in runs:
            for node in run.nodes:
                records = [node, *getattr(node, "records", ())]
                for record in records:
                    event_id = getattr(record, "published_event_id", None)
                    if not event_id or event_id in seen:
                        continue
                    # A starter normally has only a few events. Bound corrupted/cyclic evidence.
                    if len(seen) >= 64:
                        return runs, False
                    seen.add(event_id)
                    try:
                        continuation = self.reports.read_workflow_run_detail(event_id=event_id)
                    except ValueError:
                        return runs, False
                    if continuation.trace_id != root.trace_id:
                        return runs, False
                    runs.append(continuation)
        return runs, True
