from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any, Callable, Protocol, TypeVar

TNode = TypeVar("TNode")
TResult = TypeVar("TResult")


class NodeExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[str, Any] = {}

    def register(self, node_type: str, executor: Any) -> None:
        self._executors[node_type] = executor

    def get(self, node_type: str) -> Any | None:
        return self._executors.get(node_type)

    def resolve(self, node_type: str) -> Any:
        executor = self.get(node_type)
        if executor is None:
            raise KeyError(f"No node executor registered for type {node_type!r}")
        return executor


class GateNodeExecutor(Protocol):
    def execute(self, **kwargs: Any) -> bool: ...


class EffectNodeExecutor(Protocol):
    def prepare_resolved_config(self, **kwargs: Any) -> dict[str, Any]: ...

    def execute(self, **kwargs: Any) -> dict[str, Any]: ...


class QueryNodeExecutor(Protocol):
    def execute(self, **kwargs: Any) -> dict[str, Any]: ...


class ProviderEffectNodeExecutor:
    def __init__(
        self,
        provider_factory: Any | None = None,
        *,
        provider_factory_getter: Callable[[], Any] | None = None,
    ) -> None:
        if provider_factory is None and provider_factory_getter is None:
            raise ValueError(
                "ProviderEffectNodeExecutor requires a provider factory or provider factory getter"
            )
        self._provider_factory = provider_factory
        self._provider_factory_getter = provider_factory_getter

    @property
    def provider_factory(self) -> Any:
        if self._provider_factory_getter is not None:
            return self._provider_factory_getter()
        return self._provider_factory

    def prepare_resolved_config(self, **kwargs: Any) -> dict[str, Any]:
        effect = kwargs["effect"]
        provider = _build_provider_with_graph_metadata(
            self.provider_factory,
            effect,
            graph_metadata=kwargs.get("graph_metadata"),
            execution_context=kwargs.get("execution_context"),
        )
        resolved_config = kwargs.get("resolved_config")
        normalize = getattr(provider, "normalize_resolved_config", None)
        if callable(normalize):
            return dict(normalize(**kwargs) or {})
        return dict(resolved_config) if isinstance(resolved_config, Mapping) else {}

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        effect = kwargs["effect"]
        resolved_config = kwargs.get("resolved_config")
        provider = _build_provider_with_graph_metadata(
            self.provider_factory,
            effect,
            graph_metadata=kwargs.get("graph_metadata"),
            execution_context=kwargs.get("execution_context"),
        )
        execute_kwargs: dict[str, Any] = {"resolved_config": dict(resolved_config or {})}
        if "execution_context" in inspect.signature(provider.execute).parameters:
            execute_kwargs["execution_context"] = dict(kwargs.get("execution_context") or {})
        return dict(provider.execute(**execute_kwargs) or {})


class ProviderQueryNodeExecutor:
    def __init__(
        self,
        provider_factory: Any | None = None,
        *,
        provider_factory_getter: Callable[[], Any] | None = None,
    ) -> None:
        if provider_factory is None and provider_factory_getter is None:
            raise ValueError(
                "ProviderQueryNodeExecutor requires a provider factory or provider factory getter"
            )
        self._provider_factory = provider_factory
        self._provider_factory_getter = provider_factory_getter

    @property
    def provider_factory(self) -> Any:
        if self._provider_factory_getter is not None:
            return self._provider_factory_getter()
        return self._provider_factory

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        query = kwargs["query"]
        resolved_config = kwargs.get("resolved_config")
        provider = _build_provider_with_graph_metadata(
            self.provider_factory,
            query,
            graph_metadata=kwargs.get("graph_metadata"),
            execution_context=kwargs.get("execution_context"),
        )
        return dict(provider.execute(resolved_config=dict(resolved_config or {})) or {})


def _build_provider_with_graph_metadata(
    provider_factory: Any,
    node_definition: Any,
    *,
    graph_metadata: Any | None = None,
    execution_context: Mapping[str, Any] | None = None,
) -> Any:
    build = provider_factory.build
    graph_id, directory_path = _resolve_graph_metadata_values(
        graph_metadata=graph_metadata, execution_context=execution_context
    )
    build_kwargs = _build_provider_kwargs(build, directory_path=directory_path, graph_id=graph_id)
    try:
        return build(node_definition, **build_kwargs)
    except TypeError as exc:
        if build_kwargs and _is_unexpected_keyword_type_error(exc):
            return build(node_definition)
        raise


def _resolve_graph_metadata_values(
    *,
    graph_metadata: Any | None,
    execution_context: Mapping[str, Any] | None,
) -> tuple[str | None, str | None]:
    graph_id = _graph_metadata_value(graph_metadata, "graph_id")
    directory_path = _graph_metadata_value(graph_metadata, "directory_path")
    if isinstance(execution_context, Mapping):
        graph_id = graph_id or _normalize_text(execution_context.get("graph_id"))
        directory_path = directory_path or _normalize_text(execution_context.get("directory_path"))
        nested_metadata = execution_context.get("graph_metadata")
        if graph_id is None:
            graph_id = _graph_metadata_value(nested_metadata, "graph_id")
        if directory_path is None:
            directory_path = _graph_metadata_value(nested_metadata, "directory_path")
    return graph_id, directory_path


def _graph_metadata_value(value: Any, key: str) -> str | None:
    if isinstance(value, Mapping):
        return _normalize_text(value.get(key))
    return _normalize_text(getattr(value, key, None))


def _build_provider_kwargs(
    build: Any, *, directory_path: str | None, graph_id: str | None
) -> dict[str, Any]:
    try:
        signature = inspect.signature(build)
    except (TypeError, ValueError):
        return {}
    accepts_var_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()
    )
    kwargs: dict[str, Any] = {}
    if directory_path is not None and (
        "directory_path" in signature.parameters or accepts_var_kwargs
    ):
        kwargs["directory_path"] = directory_path
    if graph_id is not None and ("graph_id" in signature.parameters or accepts_var_kwargs):
        kwargs["graph_id"] = graph_id
    return kwargs


def _is_unexpected_keyword_type_error(error: TypeError) -> bool:
    message = str(error)
    return "unexpected keyword argument" in message or "got an unexpected keyword argument" in message


def _normalize_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
