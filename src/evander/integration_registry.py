"""Host-side access to optional integration contributions; no eager callbacks."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from evander.addons import AddonContext, LoadedAddonCatalog
from evander.credentials.store import CredentialUnavailable, scope_path
from evander.integration_contracts import (
    AddonIntegration,
    IntegrationCheckResult,
    IntegrationWorker,
    validate_integration_input,
)
from evander.workflow.packs import WorkflowPack


class IntegrationRegistry:
    def __init__(self, catalog: LoadedAddonCatalog) -> None:
        self._addons = {item.addon_id: item for item in catalog.registrations}
        if len(self._addons) != len(catalog.registrations):
            raise ValueError("duplicate add-on id in integration registry")

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "addon_id": addon.addon_id,
                "display_name": addon.display_name,
                **addon.integration.describe(),
                "dependency_issues": self._dependency_issues(addon.addon_id),
            }
            for addon in self._addons.values()
            if addon.integration is not None
        ]

    def _integration(self, addon_id: str) -> AddonIntegration:
        addon = self._addons.get(addon_id)
        if addon is None or addon.integration is None:
            raise KeyError("Integration is not enabled or has no integration contract")
        return addon.integration

    def _dependency_issues(self, addon_id: str) -> list[dict[str, str]]:
        issues: set[tuple[str, str]] = set()
        visited: set[str] = set()

        def visit(current: str, path: frozenset[str]) -> None:
            if current in path:
                issues.add(("dependency_cycle", current))
                return
            if current in visited:
                return
            addon = self._addons.get(current)
            if addon is None:
                issues.add(("dependency_not_enabled", current))
                return
            visited.add(current)
            if addon.integration is not None:
                for dependency in addon.integration.required_addons:
                    visit(dependency, path | {current})

        visit(addon_id, frozenset())
        return [{"code": code, "addon_id": owner} for code, owner in sorted(issues)]

    def _context(self, addon_id: str, context: AddonContext) -> AddonIntegration:
        if context.addon_id != addon_id:
            raise ValueError("Integration context belongs to a different add-on")
        scope_path(context.directory_path)
        return self._integration(addon_id)

    def _ready_context(self, addon_id: str, context: AddonContext) -> AddonIntegration:
        integration = self._context(addon_id, context)
        if self._dependency_issues(addon_id):
            raise ValueError("Integration dependencies are not enabled or contain a cycle")
        return integration

    @staticmethod
    def _find(items: tuple[Any, ...], item_id: str) -> Any:
        for item in items:
            if item.id == item_id:
                return item
        raise KeyError("Integration contribution was not found")

    def validate_settings(self, addon_id: str, section_id: str, values: Mapping[str, Any]) -> None:
        section = self._find(self._integration(addon_id).settings, section_id)
        validate_integration_input(section.schema, values)

    def readiness(self, addon_id: str, context: AddonContext) -> dict[str, Any]:
        integration = self._context(addon_id, context)
        issues = self._dependency_issues(addon_id)
        if issues:
            return {
                "addon_id": addon_id,
                "directory_path": context.directory_path,
                "status": "blocked",
                "dependency_issues": issues,
                "checks": [],
            }
        checks: list[dict[str, Any]] = []
        for credential in integration.credentials:
            try:
                configured = credential.is_configured(context)
                if type(configured) is not bool:
                    raise TypeError("credential status must be a boolean")
                result = IntegrationCheckResult(
                    "ready" if configured else "blocked",
                    "credential_configured" if configured else "credential_not_configured",
                )
            except Exception:
                result = IntegrationCheckResult("unknown", "credential_check_failed")
            checks.append(
                {
                    "id": credential.id,
                    "kind": "credential",
                    "label": credential.label,
                    "required": credential.required,
                    "status": result.status,
                    "code": result.code,
                }
            )
        for check in integration.readiness_checks:
            try:
                result = check.check(context)
                if not isinstance(result, IntegrationCheckResult):
                    raise TypeError("readiness check must return IntegrationCheckResult")
            except Exception:
                # Provider exceptions can contain tokens, URLs or response bodies.
                result = IntegrationCheckResult("unknown", "readiness_check_failed")
            checks.append(
                {
                    "id": check.id,
                    "kind": "readiness",
                    "label": check.label,
                    "required": check.required,
                    "status": result.status,
                    "code": result.code,
                }
            )
        required = [check for check in checks if check["required"]]
        status = "ready"
        if any(check["status"] == "blocked" for check in required):
            status = "blocked"
        elif not required or any(check["status"] == "unknown" for check in required):
            status = "unknown"
        return {
            "addon_id": addon_id,
            "directory_path": context.directory_path,
            "status": status,
            "dependency_issues": [],
            "checks": checks,
        }

    def resolve_credential(
        self, addon_id: str, provider_id: str, context: AddonContext
    ) -> Mapping[str, str]:
        """Host-only operation. Never expose this result through Admin or MCP."""
        integration = self._ready_context(addon_id, context)
        provider = self._find(integration.credentials, provider_id)
        try:
            value = provider.resolve(context)
            if (
                not isinstance(value, Mapping)
                or not value
                or any(
                    not isinstance(key, str) or not isinstance(item, str)
                    for key, item in value.items()
                )
            ):
                raise ValueError("invalid credential material")
        except Exception:
            raise CredentialUnavailable("Integration credential is unavailable") from None
        return dict(value)

    def create_worker(
        self, addon_id: str, worker_id: str, context: AddonContext
    ) -> IntegrationWorker:
        """Construct only; the caller owns the supervisor and shutdown lifecycle."""
        integration = self._ready_context(addon_id, context)
        worker = self._find(integration.workers, worker_id).create(context)
        if not callable(getattr(worker, "run", None)):
            raise TypeError("Integration worker must implement run(stop)")
        return worker

    def build_workflow_starter(
        self, addon_id: str, starter_id: str, context: AddonContext, values: Mapping[str, Any]
    ) -> WorkflowPack:
        """Return a normal, editable pack. This never saves or enables it."""
        integration = self._ready_context(addon_id, context)
        starter = self._find(integration.workflow_starters, starter_id)
        validate_integration_input(starter.input_schema, values)
        pack = WorkflowPack.model_validate(starter.build(context, deepcopy(dict(values))))
        if not pack.graphs or any(
            graph.directory_path != context.directory_path for graph in pack.graphs
        ):
            raise ValueError("Starter graphs must belong to the requested directory")
        node_ids = {node.node.id for node in pack.ir.nodes}
        graph_node_ids = {node_id for graph in pack.graphs for node_id in graph.node_ids}
        if node_ids != graph_node_ids:
            raise ValueError("Starter graphs must account for every workflow node")
        return pack
