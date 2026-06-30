from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from evander.config import GatewayConfig
from evander.runtime import RuntimeContext


class RuntimeAssemblyInterface(Protocol):
    def build_runtime(self, config: GatewayConfig) -> RuntimeContext: ...


@dataclass(frozen=True)
class PlatformServiceBundle(RuntimeAssemblyInterface):
    runtime_builder: Callable[[GatewayConfig], RuntimeContext]

    def build_runtime(self, config: GatewayConfig) -> RuntimeContext:
        return self.runtime_builder(config)
