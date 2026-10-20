from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import time

from evander.security_scans import SCAN_EFFECT_TYPE, SecurityScanRequest, TERMINAL_SCAN_STATUSES
from evander.effects.providers.schemas import SecurityScanEffectDefinition
from evander.workflow.validation import NodeCapability

from .base import EffectProviderBase, EffectProviderExecutionError, EffectLifecycleHookBase


class SecurityScanEffectProvider(EffectProviderBase):
    supports_waiting_slices = True
    capability = NodeCapability(
        min_inbound=1, max_inbound=1, allowed_source_ports=frozenset({"completed", "error"})
    )

    def execute(self, *, resolved_config=None, execution_context=None):
        definition = self.effect_definition
        payload = {key: getattr(definition, key) for key in SecurityScanRequest.model_fields}
        payload.update(dict(resolved_config or {}))
        request = SecurityScanRequest.model_validate(
            {key: payload[key] for key in SecurityScanRequest.model_fields}
        )
        context = dict(execution_context or {})
        key = str(context.get("idempotency_key") or "")
        execution_id = str(context.get("execution_id") or key)
        if not key:
            raise ValueError("Security scans require a durable workflow execution identity")
        runtime = self.runtime_context
        service = self._service(runtime)
        record = service.start(
            request,
            directory_path=str(runtime.metadata.get("directory_path") or "root"),
            execution_id=execution_id,
            idempotency_key=key,
            runtime=runtime,
        )
        output = record.outputs()
        if (
            record.result
            and record.result.status in TERMINAL_SCAN_STATUSES
            and not record.result.success
        ):
            raise EffectProviderExecutionError(
                record.result.error or "Security scan failed", outputs=output
            )
        return output

    @staticmethod
    def _service(runtime):
        service = getattr(runtime, "security_scan_service", None)
        if service is None:
            raise RuntimeError("Security scan service is not available")
        return service

    @classmethod
    def on_execution_started(cls, **kwargs):
        return kwargs.get("outputs", {}).get("status") in {"running", "completed"}

    @classmethod
    def reconcile_waiting(cls, *, worker, records=None, **kwargs):
        records = (
            records
            if records is not None
            else worker.execution_store.list_waiting_records(SCAN_EFFECT_TYPE)
        )
        count = 0
        for record in records:
            if not isinstance(record.outputs, Mapping) or not record.outputs.get("scan_id"):
                continue
            definition = SecurityScanEffectDefinition(
                id=record.effect_id,
                display_name="Security scan",
                type=SCAN_EFFECT_TYPE,
                **record.resolved_config,
            )
            provider = worker.provider_factory.build(
                definition, directory_path=record.outputs["directory_path"]
            )
            runtime = provider.runtime_context
            scope = str(runtime.metadata.get("directory_path") or "root")
            scan = cls._service(runtime).poll(
                record.outputs["scan_id"], directory_path=scope, runtime=runtime
            )
            if not scan.result or scan.result.status not in TERMINAL_SCAN_STATUSES:
                outputs = scan.outputs()
                if outputs != record.outputs:
                    record.outputs = outputs
                    worker.execution_store.put(record)
                continue
            outputs = scan.outputs()
            success = scan.result.success
            port = "completed" if success else "error"
            finished_at = datetime.now(UTC)
            if not success:
                from evander.daemons.effect_orchestrator import _effect_error_outbound_edge_exists

                if not _effect_error_outbound_edge_exists(
                    graph=worker._build_execution_graph(), effect_id=record.effect_id
                ):
                    record.outputs = outputs
                    record.status = "failed"
                    record.error = scan.result.error or "Security scan failed"
                    record.finished_at = finished_at
                    worker.execution_store.put(record)
                    worker._update_event_delivery_state(record.event_id)
                    count += 1
                    continue
            event_id = worker._publish_effect_completion_event_or_mark_pending(
                record=record,
                outputs=outputs,
                branch_port=port,
                pending_status="succeeded" if success else "error_routed",
                finished_at=finished_at,
            )
            if event_id is not None:
                worker._finalize_effect_completion_publication(
                    record=record,
                    outputs=outputs,
                    published_event_id=event_id,
                    finished_at=finished_at,
                    status="succeeded" if success else "error_routed",
                    branch_port=port,
                )
                count += 1
        return count

    @classmethod
    def node_type(cls):
        return SCAN_EFFECT_TYPE

    @classmethod
    def lifecycle_hooks(cls):
        return (SecurityScanCleanupHook,)


class SecurityScanCleanupHook(EffectLifecycleHookBase):
    def on_waiting_reconciliation(self, *, worker):
        service = getattr(
            getattr(worker.provider_factory, "_provider_runtime_ports", None),
            "security_scan_service",
            None,
        )
        if service is None or not service.store.path.exists():
            return 0
        changed = 0
        for scan in service.store.active():
            owner = worker.execution_store.find_execution_by_idempotency_key(
                scan.owner_idempotency_key
            )
            if owner is None or owner.status in {"running", "waiting", "completion_pending"}:
                continue
            definition = SecurityScanEffectDefinition(
                id="scan-cleanup",
                display_name="Security scan cleanup",
                type=SCAN_EFFECT_TYPE,
                **scan.request.model_dump(),
            )
            provider = worker.provider_factory.build(definition, directory_path=scan.directory_path)
            service.cancel(
                scan.scan_id, directory_path=scan.directory_path, runtime=provider.runtime_context
            )
            changed += 1
        if time.monotonic() >= getattr(worker, "_security_scan_maintenance_at", 0):
            worker._security_scan_maintenance_at = time.monotonic() + 300
            for provider in service.providers.values():
                candidates = getattr(provider, "maintenance_candidates", None)
                if not callable(candidates):
                    continue
                for scan_id, directory in candidates(service.store.path.parent):
                    scan = service.store.get(scan_id, directory)
                    definition = SecurityScanEffectDefinition(
                        id="scan-retention",
                        display_name="Security scan retention",
                        type=SCAN_EFFECT_TYPE,
                        **scan.request.model_dump(),
                    )
                    instance = worker.provider_factory.build(definition, directory_path=directory)
                    changed += provider.maintain(instance.runtime_context)
        return changed
