"""Durable gate calls on the worker executor; finalization stays on the event loop."""

from __future__ import annotations

from concurrent.futures import Future
from types import SimpleNamespace

from evander.classification.models import ClassificationError
from evander.gate_runtime import GateRuntimeError
from evander.workflow.binding import binding_trace_to_payload, resolve_workflow_bindings
from evander.workflow.contracts import NODE_OUTPUTS_CONTEXT_KEY


def dispatch_async_gate(
    *,
    worker,
    queue_record,
    gate,
    graph,
    started_at,
    config_revision,
    upstream_payload,
    upstream_output_names,
    inputs_by_source,
    input_event_ids_by_source,
    input_occurred_at_by_source,
):
    key = f"{queue_record.event_id}::{gate.id}"
    existing = worker.gate_execution_store.find_execution_by_idempotency_key(key)
    if existing is not None and existing.status != "running":
        return True
    with worker._inflight_lock:
        if key in worker._inflight:
            return True
    if existing is None:
        metadata = graph.node_graph_metadata_by_id.get(gate.id)
        scope = metadata.directory_path if metadata else "root"
        attribution = {
            "directory_path": scope,
            "workflow_run_id": queue_record.trace_id,
            "trace_id": queue_record.trace_id,
            "node_id": gate.id,
            "graph_id": metadata.graph_id if metadata else None,
        }
        raw = gate.model_dump(exclude={"id", "type", "display_name", "outputs", "config"})
        resolved = resolve_workflow_bindings(
            raw,
            upstream_payload,
            upstream_output_names,
            system_value_resolver=worker._system_value_resolver_for(
                started_at=started_at,
                queue_record=queue_record,
                node_id=gate.id,
            ),
            strict_validation=True,
        )
        try:
            provider = worker.gate_provider_factory.build(gate.type, directory_path=scope)
            prepared = provider.prepare(resolved.resolved_config)
        except ClassificationError as exc:
            raise GateRuntimeError(str(exc)) from None
        snapshot = {"prepared": prepared, "attribution": attribution}
        worker._write_gate_record(
            queue_record=queue_record,
            gate=gate,
            status="running",
            started_at=started_at,
            resolved_config=snapshot,
            outputs={},
            inputs_by_source=inputs_by_source,
            input_event_ids_by_source=input_event_ids_by_source,
            input_occurred_at_by_source=input_occurred_at_by_source,
            config_revision=config_revision,
            binding_trace=binding_trace_to_payload(resolved.trace),
        )
        existing = worker.gate_execution_store.find_execution_by_idempotency_key(key)

    # Only the saved graph directory can select credentials/settings, including on restart.
    snapshot = existing.resolved_config

    def evaluate():
        provider = worker.gate_provider_factory.build(
            existing.gate_type,
            directory_path=snapshot["attribution"]["directory_path"],
        )
        return provider.evaluate_prepared(snapshot["prepared"], snapshot["attribution"])

    try:
        future = worker._submit_provider(evaluate)
    except Exception:
        future = Future()
        future.set_exception(GateRuntimeError("Could not schedule gate evaluation"))
    worker._register_inflight_future(
        idempotency_key=key,
        future=future,
        config_revision=existing.config_revision,
        kind="gate",
    )
    return True


def finalize_async_gate(worker, key, future):
    record = worker.gate_execution_store.find_execution_by_idempotency_key(key)
    if record is None or record.status != "running":
        return 0
    snapshot = dict(record.resolved_config)
    error = None
    try:
        result = dict(future.result())
        branch = result["selected_port"]
        snapshot["decision"] = result
        outputs = {
            k: v
            for k, v in record.input_payload.items()
            if k
            not in {
                NODE_OUTPUTS_CONTEXT_KEY,
                "__branch_port",
                "__for_execution_ids",
            }
        }
        status, publish = "succeeded", True
    except Exception as exc:
        error = (
            str(exc)
            if isinstance(exc, (ClassificationError, GateRuntimeError))
            else "Gate evaluation failed"
        )
        graph = worker._build_execution_graph()
        publish = any(
            getattr(edge, "source_port", None) == "error"
            for edge in graph.outbound_index.get(record.gate_id, ())
        )
        branch = "error" if publish else None
        status = "error_routed" if publish else "failed"
        outputs = {"success": False, "error_message": error, "error_class": exc.__class__.__name__}
    worker._write_gate_record(
        queue_record=SimpleNamespace(
            event_id=record.event_id,
            trace_id=record.trace_id,
            trigger_id=record.source_node_id,
            trigger_type=record.source_trigger_type,
            payload=record.input_payload,
        ),
        gate=SimpleNamespace(id=record.gate_id, type=record.gate_type),
        status=status,
        started_at=record.started_at,
        resolved_config=snapshot,
        outputs=outputs,
        inputs_by_source=record.inputs_by_source,
        input_event_ids_by_source=record.input_event_ids_by_source,
        input_occurred_at_by_source=record.input_occurred_at_by_source,
        config_revision=record.config_revision,
        binding_trace=record.binding_trace,
        error=error,
        branch_port=branch,
        publish_completion_event=publish,
        replace_running=True,
    )
    worker._update_event_delivery_state(record.event_id)
    updated = worker.gate_execution_store.find_execution_by_idempotency_key(key)
    return int(bool(updated.completion_event_enqueued_at))
