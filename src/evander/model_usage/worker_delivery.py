"""Deliver newly captured receipts independently of execution completion."""

from __future__ import annotations

import logging

from evander.effects.providers.base import EffectLifecycleHookBase
from evander.execution.container_worker_backend import ContainerWorkerVmExecutionBackend
from evander.execution.container_worker_repository import ContainerWorkerExecutionRepository
from evander.orchestration.maintenance import MaintenanceBudget, MaintenanceResult

logger = logging.getLogger(__name__)


class WorkerUsageDeliveryHook(EffectLifecycleHookBase):
    # Worker HTTP must run outside the foreground orchestration loop.
    isolated_maintenance = True

    def initialize(self, *, worker) -> None:
        pass

    def on_prompt_execution_changed(self, *, worker, record) -> None:
        pass

    def validate_queued_prompt(self, *, worker, record) -> bool:
        return True

    def on_post_completion_dispatch_slice(self, *, worker, budget) -> MaintenanceResult:
        return MaintenanceResult(examined=0)

    def on_waiting_reconciliation(self, *, worker, **kwargs) -> int:
        return self.on_waiting_reconciliation_slice(
            worker=worker,
            budget=MaintenanceBudget(),
        ).changes

    def on_waiting_reconciliation_slice(self, *, worker, budget) -> MaintenanceResult:
        repository = ContainerWorkerExecutionRepository(
            worker.config.runtime_dir / "container-worker-executions.db"
        )
        mapping = repository.claim_model_usage_delivery(
            retry_seconds=max(30, worker.config.container_worker_request_timeout_seconds * 2),
        )
        if mapping is None:
            return MaintenanceResult(examined=0)
        try:
            from evander.admin.services.container_worker_assignment_service import (
                ContainerWorkerAssignmentService,
            )

            config = ContainerWorkerAssignmentService(worker.config).resolve(
                mapping.session_id,
                directory_path=mapping.directory_path,
                worker_url=mapping.worker_url,
            )
            backend = ContainerWorkerVmExecutionBackend(config=config, repository=repository)
            backend.sync_model_usage(mapping, max_pages=1)
        except Exception as exc:
            logger.warning("worker_usage_delivery_failed exception_class=%s", type(exc).__name__)
        return MaintenanceResult(examined=1)
