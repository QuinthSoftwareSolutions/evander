"""Shared dependency wiring for MCP gateway tool registration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.chat import ChatStore, admin_chat_store_path
from agent_sandbox.communication_artifacts import CommunicationArtifactStore
from agent_sandbox.issues.service import IssueService
from agent_sandbox.runtime import RuntimeContext
from agent_sandbox.semantic.gateway_adapter import GatewaySemanticAdapter


@dataclass(frozen=True)
class GatewayToolDependencies:
    """Common services shared by gateway tool implementations.

    Factories are used for services that may hold request-local or mutable state. Shared
    instances are kept for dependencies that are safe to construct once for the gateway server.
    """

    admin_service_factory: Callable[[], AdminService]
    issue_service_factory: Callable[[], IssueService]
    chat_store_factory: Callable[[], ChatStore]
    semantic_adapter: GatewaySemanticAdapter
    communication_artifact_store_factory: Callable[[], CommunicationArtifactStore]

    @classmethod
    def from_context(cls, context: RuntimeContext) -> "GatewayToolDependencies":
        """Build gateway dependencies from a runtime context."""

        def communication_artifact_store_factory() -> CommunicationArtifactStore:
            store = CommunicationArtifactStore(context.config.runtime_dir)
            store.cleanup_expired()
            return store

        return cls(
            admin_service_factory=lambda: AdminService(context.config.runtime_dir),
            issue_service_factory=lambda: IssueService(context.provider_config),
            chat_store_factory=lambda: ChatStore(admin_chat_store_path(context.config.runtime_dir)),
            semantic_adapter=GatewaySemanticAdapter(context),
            communication_artifact_store_factory=communication_artifact_store_factory,
        )
