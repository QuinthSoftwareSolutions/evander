from datetime import UTC, datetime

import pytest

from config_helpers import canonical_workflow_payload, write_runtime_config
from evander.admin.composition import build_admin_runtime
from evander.daemons.effect_orchestrator import EffectOrchestratorWorker
from evander.events.models import TriggerEvent
from evander.workflow.runtime_config import load_trigger_orchestrator_worker_global_config
from evander.workflow.state_store import WorkflowStateStore


@pytest.mark.parametrize(
    "payload,registered,branch",
    [
        ({"answer": "yes"}, True, "valid"),
        ({"answer": 3}, True, "invalid"),
        ({"answer": "yes"}, False, "invalid"),
    ],
)
def test_event_validator_routes_with_directory_schema_and_preserves_body(
    tmp_path, payload, registered, branch
):
    service = build_admin_runtime(
        write_runtime_config(tmp_path, enable_directory_scoped_config=True)
    )
    result = service.directory_admin.create_child(parent="root", name="child")
    assert result.ok
    if registered:
        snapshot = service.event_schema_admin.snapshot("root/child")
        service.event_schema_admin.save(
            "root/child",
            expected_revision=snapshot["revision"],
            config={
                "events": {
                    "custom.answer": {
                        "label": "Answer",
                        "payload_schema": {
                            "type": "object",
                            "properties": {"answer": {"type": "string"}},
                            "required": ["answer"],
                        },
                    }
                }
            },
        )
    config = canonical_workflow_payload(
        {
            "runtime_dir": str(service.config.runtime_dir),
            "triggers": [
                {
                    "id": "source",
                    "type": "trigger:event",
                    "event_type": "custom.answer",
                    "display_name": "Source",
                }
            ],
            "gates": [
                {
                    "id": "validate",
                    "type": "gate:event:validate",
                    "display_name": "Validate",
                    "event_type": "${{source.event_type}}",
                    "payload": "${{source.payload}}",
                }
            ],
            "effects": [
                {
                    "id": port,
                    "type": "effect:state:update",
                    "display_name": port,
                    "namespace": "validation",
                    "key": port,
                    "operation": "set",
                    "value": "${{validate.payload}}",
                }
                for port in ("valid", "invalid")
            ],
            "edges": [{"source_id": "source", "target_id": "validate"}]
            + [
                {"source_id": "validate", "source_port": port, "target_id": port}
                for port in ("valid", "invalid")
            ],
        }
    )
    graphs = [
        {
            "id": "validation-graph",
            "name": "Validation",
            "directory_path": "root/child",
            "node_ids": ["source", "validate", "valid", "invalid"],
        }
    ]
    config["workflow_graphs"] = graphs
    service.workflow_config_admin.save_workflow_editor_document(
        config=config, layout={}, graphs=graphs
    )
    worker = EffectOrchestratorWorker(
        config=service.config,
        worker_config=load_trigger_orchestrator_worker_global_config(
            service.project_dir, base_config=service.config
        ),
        runtime=None,
    )
    worker.event_queue.publish(
        TriggerEvent(
            event_id="source:event",
            trigger_id="source",
            trigger_type="trigger:event",
            occurred_at=datetime.now(UTC),
            payload={"event_type": "custom.answer", "payload": payload, "source": None},
        )
    )
    for _ in range(5):
        worker.run_once()
        worker._wait_for_inflight_completion(timeout_seconds=2)
    result = WorkflowStateStore(service.config.runtime_dir).get(
        "root/child", "validation-graph", "validation", branch
    )
    assert result.record.value == payload
    assert (
        WorkflowStateStore(service.config.runtime_dir)
        .get(
            "root/child",
            "validation-graph",
            "validation",
            "invalid" if branch == "valid" else "valid",
        )
        .record
        is None
    )
    executions = list(worker.gate_execution_store.read_all().values())
    assert executions[0].outputs["payload"] == payload
    assert executions[0].outputs["valid"] == (branch == "valid")
    assert executions[0].resolved_config["directory_path"] == "root/child"
    if not registered:
        assert executions[0].outputs["failures"][0]["code"] == "schema.not_registered"
