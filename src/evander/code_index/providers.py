from __future__ import annotations

from typing import Literal

from pydantic import TypeAdapter

from evander.effects.providers.base import EffectProviderBase, scoped_effect_directory_path
from evander.events.models import (
    ConfigValue,
    EffectDefinition,
    QueryDefinition,
    WorkflowNodeEnvelope,
)
from evander.queries.providers.base import QueryProviderBase, scoped_query_directory_path


class CodeIndexEnsureDefinition(EffectDefinition):
    type: Literal["effect:code-index:ensure"]
    managed_repo_id: ConfigValue
    commit_sha: ConfigValue | None = None
    directory_path: ConfigValue | None = None


class CodeIndexReconcileDefinition(EffectDefinition):
    type: Literal["effect:code-index:reconcile"]
    directory_path: ConfigValue | None = None
    limit: ConfigValue | None = None


class CodeIndexStatusDefinition(QueryDefinition):
    type: Literal["query:code-index:status"]
    managed_repo_id: ConfigValue
    commit_sha: ConfigValue | None = None
    directory_path: ConfigValue | None = None


class CodeIndexSearchDefinition(QueryDefinition):
    type: Literal["query:code-index:search"]
    managed_repo_id: ConfigValue
    commit_sha: ConfigValue
    query: ConfigValue
    mode: ConfigValue | None = None
    allow_baseline: ConfigValue | None = None
    limit: ConfigValue | None = None
    offset: ConfigValue | None = None
    directory_path: ConfigValue | None = None


class CodeIndexNavigateDefinition(QueryDefinition):
    type: Literal["query:code-index:navigate"]
    managed_repo_id: ConfigValue
    commit_sha: ConfigValue
    symbol: ConfigValue
    kind: ConfigValue | None = None
    allow_baseline: ConfigValue | None = None
    limit: ConfigValue | None = None
    offset: ConfigValue | None = None
    directory_path: ConfigValue | None = None


def _service(context):
    service = getattr(context.admin_service, "repository_index_admin", None)
    if service is None:
        raise RuntimeError("Repository indexing service is unavailable")
    return service


class CodeIndexEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, definition):
        if isinstance(definition, WorkflowNodeEnvelope):
            model = (
                CodeIndexEnsureDefinition
                if definition.type == "effect:code-index:ensure"
                else CodeIndexReconcileDefinition
            )
            return model.model_validate(
                {
                    "id": definition.id,
                    "display_name": definition.display_name,
                    "type": definition.type,
                    "outputs": definition.outputs,
                    **definition.config,
                }
            )
        return definition

    def execute(self, *, resolved_config=None) -> dict:
        context = self.runtime_context
        payload = self.effect_definition.model_dump(exclude_none=True)
        payload.update(resolved_config or {})
        directory = scoped_effect_directory_path(context, payload.get("directory_path"))
        service = _service(context)
        if self.effect_definition.type == "effect:code-index:reconcile":
            return service.reconcile(directory, limit=int(payload.get("limit", 4)))
        return service.ensure(str(payload["managed_repo_id"]), directory, payload.get("commit_sha"))


class CodeIndexQueryProvider(QueryProviderBase):
    @classmethod
    def validate_definition(cls, definition):
        if isinstance(definition, WorkflowNodeEnvelope):
            model = {
                "query:code-index:status": CodeIndexStatusDefinition,
                "query:code-index:search": CodeIndexSearchDefinition,
                "query:code-index:navigate": CodeIndexNavigateDefinition,
            }[definition.type]
            return model.model_validate(
                {
                    "id": definition.id,
                    "display_name": definition.display_name,
                    "type": definition.type,
                    "outputs": definition.outputs,
                    **definition.config,
                }
            )
        return definition

    def execute(self, *, resolved_config=None) -> dict:
        context = self.runtime_context
        payload = self.query_definition.model_dump(exclude_none=True)
        payload.update(resolved_config or {})
        directory = scoped_query_directory_path(context, payload.get("directory_path"))
        service = _service(context)
        repo, sha = str(payload["managed_repo_id"]), payload.get("commit_sha")
        node_type = self.query_definition.type
        if node_type == "query:code-index:status":
            return service.status(repo, directory, sha)
        page = {"limit": int(payload.get("limit", 10)), "offset": int(payload.get("offset", 0))}
        allow_baseline = TypeAdapter(bool).validate_python(payload.get("allow_baseline", False))
        if node_type == "query:code-index:search":
            return service.search(
                repo,
                directory,
                sha,
                str(payload["query"]),
                mode=str(payload.get("mode", "hybrid")),
                allow_baseline=allow_baseline,
                **page,
            )
        return service.navigate(
            repo,
            directory,
            sha,
            str(payload["symbol"]),
            kind=str(payload.get("kind", "definitions")),
            allow_baseline=allow_baseline,
            **page,
        )
