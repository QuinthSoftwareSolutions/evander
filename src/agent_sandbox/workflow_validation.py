from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel


@dataclass(frozen=True)
class NodeCapability:
    min_inbound: int | None = None
    max_inbound: int | None = None
    non_cyclic_inbound_exact: int | None = None
    allowed_source_ports: frozenset[str] | None = None
    required_source_ports: frozenset[str] = field(default_factory=frozenset)
    completion_semantics: str | None = None


class WorkflowValidationDiagnostic(BaseModel):
    code: str
    message: str
    node_id: str | None = None
    node_type: str | None = None
