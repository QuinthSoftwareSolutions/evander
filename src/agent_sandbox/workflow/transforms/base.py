from __future__ import annotations

from collections.abc import Callable

from agent_sandbox.config import TriggerOrchestratorWorkerGlobalConfig

WorkflowTransform = Callable[
    [TriggerOrchestratorWorkerGlobalConfig], TriggerOrchestratorWorkerGlobalConfig
]
