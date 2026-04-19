from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_sandbox.provider_runtime.contracts import GateVmInventory, ProviderRuntimeBase


@dataclass(frozen=True)
class GateRuntimeContext(ProviderRuntimeBase):
    vm_inventory: GateVmInventory | None = None


class GateProviderBase:
    def __init__(self) -> None:
        self.runtime_context: GateRuntimeContext | None = None

    def initialize(self, runtime_context: GateRuntimeContext) -> None:
        self.runtime_context = runtime_context

    def execute(self, **_: Any) -> bool:
        return False

    def load_state(self, **_: Any) -> Any:
        return None

    def save_state(self, **_: Any) -> None:
        return None

    def cleanup(self, **_: Any) -> None:
        return None

    def reconcile(self, **_: Any) -> int:
        return 0

    def handle_completion_event(self, **_: Any) -> int:
        return 0

    def force_complete(self, **_: Any) -> bool:
        return False
