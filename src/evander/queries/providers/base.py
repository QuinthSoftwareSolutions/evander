from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from evander.events.models import (
    QueryDefinitionDiscriminated,
    WorkflowNodeEnvelope,
    materialize_node_definition,
)
from evander.models import DirectoryPath
from evander.provider_runtime.contracts import (
    GitMergeBaseReader,
    GitPrNoteReader,
    IssueReader,
    ProviderRuntimeBase,
    SessionLookup,
    SessionArtifactReader,
    SessionContextReader,
    VmInventory,
)
from evander.workflow.validation import NodeCapability, WorkflowValidationDiagnostic


def scoped_query_directory_path(
    context: "QueryRuntimeContext",
    requested: object | None = None,
) -> str:
    """Resolve a query directory without allowing a child to claim an ancestor/sibling."""

    scope = DirectoryPath(segments=str(context.metadata.get("directory_path") or "root")).as_posix()
    target = DirectoryPath(
        segments=str(requested).strip() if requested is not None else scope
    ).as_posix()
    if target != scope and not target.startswith(f"{scope}/"):
        raise ValueError(f"directory_path {target!r} is outside workflow directory scope {scope!r}")
    return target


@dataclass(frozen=True)
class QueryRuntimeContext(ProviderRuntimeBase):
    """Host-side runtime inputs used by query providers."""

    vm_inventory: VmInventory | None = None
    git_merge_base_reader: GitMergeBaseReader | None = None
    git_pr_note_reader: GitPrNoteReader | None = None
    issue_reader: IssueReader | None = None
    session_lookup: SessionLookup | None = None
    session_context_reader: SessionContextReader | None = None
    session_artifact_reader: SessionArtifactReader | None = None
    admin_service: Any | None = None


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
