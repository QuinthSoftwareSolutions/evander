from __future__ import annotations

from collections.abc import Iterable

from .config import TriggerOrchestratorWorkerGlobalConfig
from .workflow_graph import build_inbound_edge_index
from .workflow_plugins import load_provider_plugins
from .workflow_transforms import WorkflowTransform
from .workflow_validation import WorkflowValidationDiagnostic


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


def compile_trigger_orchestrator_config_with_diagnostics(
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
    *,
    transforms: Iterable[WorkflowTransform] | None = None,
) -> tuple[TriggerOrchestratorWorkerGlobalConfig, list[WorkflowValidationDiagnostic]]:
    compiled = compile_trigger_orchestrator_config(worker_config, transforms=transforms)
    return compiled, collect_domain_validation_diagnostics(compiled)


def collect_domain_validation_diagnostics(
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
) -> list[WorkflowValidationDiagnostic]:
    nodes_by_id = {node.id: node for node in (*worker_config.triggers, *worker_config.gates, *worker_config.effects, *worker_config.queries)}
    inbound_edges = {k: list(v) for k, v in build_inbound_edge_index(worker_config.edges).items()}
    diagnostics: list[WorkflowValidationDiagnostic] = []
    provider_catalog = load_provider_plugins(
        module_names=worker_config.provider_loader.provider_modules,
        include_entry_points=worker_config.provider_loader.provider_entry_points_enabled,
    )
    for gate in worker_config.gates:
        provider = provider_catalog.gate_providers.get(gate.type)
        if provider is None:
            continue
        diagnostics.extend(provider.validate_graph_node(node=gate, nodes_by_id=nodes_by_id, inbound_edges=inbound_edges))
    for effect in worker_config.effects:
        provider = provider_catalog.effect_providers.get(effect.type)
        if provider is None:
            continue
        diagnostics.extend(provider.validate_graph_node(node=effect, nodes_by_id=nodes_by_id, inbound_edges=inbound_edges))
    for query in worker_config.queries:
        provider = provider_catalog.query_providers.get(query.type)
        if provider is None:
            continue
        diagnostics.extend(provider.validate_graph_node(node=query, nodes_by_id=nodes_by_id, inbound_edges=inbound_edges))
    return diagnostics
