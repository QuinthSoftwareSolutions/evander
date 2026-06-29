"""Shared dependency wiring for MCP gateway tool registration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import sys

from agent_sandbox.admin.composition import AdminRuntimeComponents, build_admin_runtime
from agent_sandbox.admin.web.chat import ChatStore, admin_chat_store_path
from agent_sandbox.communication_artifacts import CommunicationArtifactStore
from agent_sandbox.issues.service import IssueService
from agent_sandbox.runtime import RuntimeContext
from agent_sandbox.semantic.gateway_adapter import GatewaySemanticAdapter


def _compat_gateway_attr(name: str, default: object) -> object:
    gateway_module = sys.modules.get("agent_sandbox.gateway")
    if gateway_module is None:
        return default
    return getattr(gateway_module, name, default)


@dataclass(frozen=True)
class GatewayToolDependencies:
    """Common services shared by gateway tool implementations.

    Factories are used for services that may hold request-local or mutable state. Shared
    instances are kept for dependencies that are safe to construct once for the gateway server.
    """

    admin_runtime_factory: Callable[[], AdminRuntimeComponents]
    issue_service_factory: Callable[[], IssueService]
    chat_store_factory: Callable[[], ChatStore]
    semantic_adapter: GatewaySemanticAdapter
    communication_artifact_store_factory: Callable[[], CommunicationArtifactStore]

    @classmethod
    def from_context(cls, context: RuntimeContext) -> "GatewayToolDependencies":
        """Build gateway dependencies from a runtime context."""

        admin_runtime_builder = _compat_gateway_attr("build_admin_runtime", build_admin_runtime)
        issue_service_cls = _compat_gateway_attr("IssueService", IssueService)
        communication_artifact_store_cls = _compat_gateway_attr(
            "CommunicationArtifactStore",
            CommunicationArtifactStore,
        )

        def communication_artifact_store_factory() -> CommunicationArtifactStore:
            store = communication_artifact_store_cls(context.config.runtime_dir)
            store.cleanup_expired()
            return store

        return cls(
            admin_runtime_factory=lambda: admin_runtime_builder(context.config.runtime_dir),
            issue_service_factory=lambda: issue_service_cls(context.provider_config),
            chat_store_factory=lambda: ChatStore(admin_chat_store_path(context.config.runtime_dir)),
            semantic_adapter=GatewaySemanticAdapter(context),
            communication_artifact_store_factory=communication_artifact_store_factory,
        )
