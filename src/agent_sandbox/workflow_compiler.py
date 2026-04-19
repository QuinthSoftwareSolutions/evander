from __future__ import annotations

from collections.abc import Iterable

from .config import TriggerOrchestratorWorkerGlobalConfig
from .workflow_transforms import WorkflowTransform


def compile_trigger_orchestrator_structure(
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
) -> TriggerOrchestratorWorkerGlobalConfig:
    """Validate and normalize workflow structure without domain-specific rewrites."""
    return TriggerOrchestratorWorkerGlobalConfig.model_validate(worker_config.model_dump(mode="json"))


def apply_workflow_transforms(
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
    *,
    transforms: Iterable[WorkflowTransform] | None = None,
) -> TriggerOrchestratorWorkerGlobalConfig:
    compiled = worker_config
    for transform in transforms or ():
        compiled = compile_trigger_orchestrator_structure(transform(compiled))
    return compiled


def compile_trigger_orchestrator_config(
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
    *,
    transforms: Iterable[WorkflowTransform] | None = None,
) -> TriggerOrchestratorWorkerGlobalConfig:
    compiled = compile_trigger_orchestrator_structure(worker_config)
    return apply_workflow_transforms(compiled, transforms=transforms)
