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
        provider = self.provider_factory.build(effect)
        resolved_config = kwargs.get("resolved_config")
        normalize = getattr(provider, "normalize_resolved_config", None)
        if callable(normalize):
            return dict(normalize(**kwargs) or {})
        return dict(resolved_config) if isinstance(resolved_config, Mapping) else {}

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        effect = kwargs["effect"]
        resolved_config = kwargs.get("resolved_config")
        provider = self.provider_factory.build(effect)
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
        provider = self.provider_factory.build(query)
        return dict(provider.execute(resolved_config=dict(resolved_config or {})) or {})
