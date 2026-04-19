from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from agent_sandbox.admin_service import AdminService
from agent_sandbox.events.models import GateDefinitionDiscriminated


@dataclass(frozen=True)
class GateRuntimeContext:
    resolved_config: Any
    admin_service: AdminService | None = None
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    secret_resolver: Callable[[str], str | None] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class GateProviderBase:
    def __init__(self) -> None:
        self.runtime_context: GateRuntimeContext | None = None

    def initialize(self, runtime_context: GateRuntimeContext) -> None:
        self.runtime_context = runtime_context

    def execute(self, **_: Any) -> bool:
        return False

    def reconcile(self, **_: Any) -> int:
        return 0

    def handle_completion_event(self, **_: Any) -> int:
        return 0

    def force_complete(self, **_: Any) -> bool:
        return False
