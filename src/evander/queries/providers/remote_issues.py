from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.ado_query_mode import validate_ado_query_mode
from evander.issues.service import IssueService

from .base import QueryProviderBase


class RemoteIssuesQueryProvider(QueryProviderBase):
    """Query remote issue/work-item providers from workflow nodes."""

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._query_payload()
        if resolved_config:
            payload.update({key: value for key, value in dict(resolved_config).items()})
        payload = {key: value for key, value in payload.items() if value is not None}

        provider = _normalize_provider(payload.pop("provider", None))
        if provider not in {"azure_devops", "github"}:
            raise ValueError("query:remote-issues provider must be azure_devops or github")

        directory_path = _normalize_optional_string(payload.pop("directory_path", None))
        if directory_path is None:
            directory_path = self._context_directory_path()

        current_iteration = _coerce_bool(payload.pop("current_iteration", None), default=False)
        wiql = _normalize_optional_string(payload.get("wiql"))
        query_mode = _normalize_optional_string(payload.get("query_mode"))
        if provider == "github" and current_iteration:
            raise ValueError("query:remote-issues current_iteration is only valid for Azure DevOps")
        if provider == "github" and wiql:
            raise ValueError("query:remote-issues wiql is only valid for Azure DevOps")
        if provider == "github" and query_mode:
            raise ValueError("query:remote-issues query_mode is only valid for Azure DevOps")
        if provider == "azure_devops":
            query_mode = validate_ado_query_mode(
                query_mode=query_mode,
                wiql=wiql,
                current_iteration=current_iteration,
                context="query:remote-issues",
            )

        context = self._context()
        if context.provider_config_resolver is None:
            raise RuntimeError("query:remote-issues provider_config_resolver is not available")
        issue_service = self._issue_service(context.provider_config_resolver)
        request_kwargs: dict[str, Any] = {
            "provider": provider,
            "directory_path": directory_path,
            "project": _normalize_optional_string(payload.get("project")),
            "repo": _normalize_optional_string(payload.get("repo")),
            "wiql": wiql,
            "current_iteration": current_iteration,
            "team": _normalize_optional_string(payload.get("team")),
            "iteration": _normalize_optional_string(payload.get("iteration")),
            "area_path": _normalize_optional_string(payload.get("area_path")),
            "states": _normalize_string_list(payload.get("states")),
            "types": _normalize_string_list(payload.get("types")),
            "tags_any": _normalize_string_list(payload.get("tags_any")),
            "text_search": _normalize_optional_string(payload.get("text_search")),
            "state": _normalize_optional_string(payload.get("state")),
            "labels_any": _normalize_string_list(payload.get("labels_any")),
            "labels_all": _normalize_string_list(payload.get("labels_all")),
            "assignee": _normalize_optional_string(payload.get("assignee")),
            "author": _normalize_optional_string(payload.get("author")),
            "mentioned": _normalize_optional_string(payload.get("mentioned")),
            "milestone": _normalize_optional_string(payload.get("milestone")),
            "since": _normalize_optional_string(payload.get("since")),
            "top": _coerce_int(payload.get("top"), default=50),
            "skip": _coerce_int(payload.get("skip"), default=0),
        }
        if provider == "azure_devops":
            request_kwargs["query_mode"] = query_mode
        return _coerce_result(issue_service.query_remote_issues(**request_kwargs))

    def _query_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        query = self.query_definition
        for field_name in (
            "provider",
            "directory_path",
            "project",
            "repo",
            "query_mode",
            "wiql",
            "current_iteration",
            "team",
            "iteration",
            "area_path",
            "states",
            "types",
            "tags_any",
            "text_search",
            "state",
            "labels_any",
            "labels_all",
            "assignee",
            "author",
            "mentioned",
            "milestone",
            "since",
            "top",
            "skip",
        ):
            if query is not None and hasattr(query, field_name):
                payload[field_name] = getattr(query, field_name)
        return payload

    def _issue_service(self, provider_config_resolver: object) -> IssueService:
        return IssueService(provider_config_resolver)  # type: ignore[arg-type]

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("RemoteIssuesQueryProvider has no runtime context")
        return self.runtime_context

    def _context_directory_path(self) -> str:
        metadata = self._context().metadata
        return _normalize_optional_string(metadata.get("directory_path")) or "root"


def _normalize_provider(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"ado", "azure_devops", "azure-devops"}:
        return "azure_devops"
    return text


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    if isinstance(value, tuple):
        return [str(part).strip() for part in value if str(part).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError("query:remote-issues current_iteration must resolve to a boolean")


def _coerce_int(value: Any, *, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("query:remote-issues numeric fields must resolve to integers") from exc


def _coerce_result(result: Any) -> dict[str, Any]:
    if result is None:
        return {}
    if isinstance(result, Mapping):
        return dict(result)
    model_dump = getattr(result, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dict(dumped)
    if hasattr(result, "__dict__"):
        return dict(vars(result))
    return {"result": result}
