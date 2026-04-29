from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agent_sandbox.events.models import (
    QueryDefinitionDiscriminated,
    WorkflowNodeEnvelope,
    materialize_node_definition,
)
from agent_sandbox.provider_runtime.contracts import (
    GitPrNoteReader,
    IssueReader,
    ProviderRuntimeBase,
    SessionContextReader,
    VmInventory,
)
from agent_sandbox.workflow.validation import NodeCapability, WorkflowValidationDiagnostic


@dataclass(frozen=True)
class QueryRuntimeContext(ProviderRuntimeBase):
    """Host-side runtime inputs used by query providers."""

    vm_inventory: VmInventory | None = None
    git_pr_note_reader: GitPrNoteReader | None = None
    issue_reader: IssueReader | None = None
    session_context_reader: SessionContextReader | None = None


class QueryProvider(Protocol):
    @classmethod
    def validate_definition(
        cls, query_definition: QueryDefinitionDiscriminated
    ) -> QueryDefinitionDiscriminated: ...

    def initialize(
        self,
        query_definition: QueryDefinitionDiscriminated,
        runtime_context: QueryRuntimeContext,
    ) -> None: ...

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]: ...


class QueryProviderBase:
    """Convenience base class for query provider implementations."""

    def __init__(self) -> None:
        self.query_definition: QueryDefinitionDiscriminated | None = None
        self.runtime_context: QueryRuntimeContext | None = None

    def initialize(
        self,
        query_definition: QueryDefinitionDiscriminated,
        runtime_context: QueryRuntimeContext,
    ) -> None:
        if type(query_definition) is WorkflowNodeEnvelope:
            query_definition = self.validate_definition(query_definition)
        self.query_definition = query_definition
        self.runtime_context = runtime_context

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        del resolved_config
        return {}

    @classmethod
    def validate_definition(
        cls, query_definition: QueryDefinitionDiscriminated
    ) -> QueryDefinitionDiscriminated:
        if type(query_definition) is not WorkflowNodeEnvelope:
            return query_definition
        return materialize_node_definition(query_definition)

    @classmethod
    def node_capability(cls) -> NodeCapability:
        return cls.capability

    @classmethod
    def validate_graph_node(
        cls,
        *,
        node: Any,
        **_: Any,
    ) -> list[WorkflowValidationDiagnostic]:
        del node
        return []

    capability = NodeCapability(min_inbound=1, max_inbound=1)
