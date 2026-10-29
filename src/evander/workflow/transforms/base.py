from __future__ import annotations

from collections.abc import Callable

from evander.config import TriggerOrchestratorWorkerGlobalConfig

WorkflowTransform = Callable[
    [TriggerOrchestratorWorkerGlobalConfig], TriggerOrchestratorWorkerGlobalConfig
]
