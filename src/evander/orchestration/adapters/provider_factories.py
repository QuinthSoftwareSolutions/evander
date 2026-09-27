from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from evander.admin.composition import AdminRuntimeComponents, build_admin_runtime
from evander.code_consultation_service import BoundCodeConsultationServiceFactory
from evander.effects.registry import EffectProviderFactory, EffectProviderRegistry
from evander.gates.registry import GateProviderFactory, GateProviderRegistry
from evander.orchestration.effect_extensions import (
    AgentPromptDispatchPort,
    AgentRunStatusPort,
)
from evander.provider_config import ProviderConfigResolver
from evander.provider_runtime.adapters import AdminRuntimeProviderAdapter
from evander.queries.registry import QueryProviderFactory, QueryProviderRegistry
from evander.triggers.registry import TriggerProviderFactory, TriggerProviderRegistry


class _ExplicitProviderMapping(Mapping[str, object]):
    def __init__(self, values: Mapping[str, object]) -> None:
        self._values = dict(values)

    def __getitem__(self, key: str) -> object:
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __bool__(self) -> bool:
        return True


def _env_or_default(env: Mapping[str, str] | None) -> dict[str, str]:
    return dict(os.environ if env is None else env)


def _admin_runtime_adapter(
    runtime_dir: Path,
    admin_runtime: AdminRuntimeComponents | None = None,
) -> AdminRuntimeProviderAdapter:
    return AdminRuntimeProviderAdapter(admin_runtime or build_admin_runtime(runtime_dir))


def build_effect_provider_factory(
    *,
    runtime_dir: Path,
    provider_config_resolver: ProviderConfigResolver,
    effect_providers: Mapping[str, Any],
    env: Mapping[str, str] | None = None,
    admin_runtime: AdminRuntimeComponents | None = None,
) -> EffectProviderFactory:
    resolved_admin_runtime = admin_runtime or build_admin_runtime(runtime_dir)
    return EffectProviderFactory(
        provider_config_resolver,
        registry=EffectProviderRegistry(initial=_ExplicitProviderMapping(effect_providers)),
        admin_service=AdminRuntimeProviderAdapter(resolved_admin_runtime),
        code_consultation_service_factory=BoundCodeConsultationServiceFactory(
            lambda: resolved_admin_runtime.managed_git_admin,
            config=resolved_admin_runtime.config,
        ),
        env=_env_or_default(env),
    )


def build_query_provider_factory(
    *,
    runtime_dir: Path,
    provider_config_resolver: ProviderConfigResolver,
    query_providers: Mapping[str, Any],
    env: Mapping[str, str] | None = None,
    admin_runtime: AdminRuntimeComponents | None = None,
) -> QueryProviderFactory:
    return QueryProviderFactory(
        provider_config_resolver,
        registry=QueryProviderRegistry(initial=_ExplicitProviderMapping(query_providers)),
        admin_service=_admin_runtime_adapter(runtime_dir, admin_runtime),
        env=_env_or_default(env),
    )


def build_gate_provider_factory(
    *,
    runtime_dir: Path,
    provider_config_resolver: ProviderConfigResolver,
    gate_providers: Mapping[str, Any],
    env: Mapping[str, str] | None = None,
    admin_runtime: AdminRuntimeComponents | None = None,
) -> GateProviderFactory:
    return GateProviderFactory(
        provider_config_resolver,
        registry=GateProviderRegistry(initial=_ExplicitProviderMapping(gate_providers)),
        admin_service=_admin_runtime_adapter(runtime_dir, admin_runtime),
        env=_env_or_default(env),
    )


def build_trigger_provider_factory(
    *,
    runtime_dir: Path,
    provider_config_resolver: ProviderConfigResolver,
    trigger_providers: Mapping[str, Any],
    env: Mapping[str, str] | None = None,
    admin_runtime: AdminRuntimeComponents | None = None,
) -> TriggerProviderFactory:
    return TriggerProviderFactory(
        provider_config_resolver,
        registry=TriggerProviderRegistry(initial=_ExplicitProviderMapping(trigger_providers)),
        admin_service=_admin_runtime_adapter(runtime_dir, admin_runtime),
        env=_env_or_default(env),
    )


def build_agent_run_status_port(
    *,
    runtime_dir: Path,
    admin_runtime: AdminRuntimeComponents | None = None,
) -> AgentRunStatusPort:
    return _AdminRuntimeAgentRunStatusPort(admin_runtime or build_admin_runtime(runtime_dir))


def build_agent_prompt_dispatch_port(
    *,
    runtime_dir: Path,
    admin_runtime: AdminRuntimeComponents | None = None,
) -> AgentPromptDispatchPort:
    return _AdminRuntimeAgentPromptDispatchPort(admin_runtime or build_admin_runtime(runtime_dir))


class _AdminRuntimeAgentRunStatusPort:
    def __init__(self, admin_runtime: AdminRuntimeComponents) -> None:
        self._admin_runtime = admin_runtime

    def list_vm_summaries(self) -> list[Any]:
        return list(
            self._admin_runtime.vm_agent_admin.list_vms(
                include_inactive=True,
                omit_state=False,
                vm_user="agent",
                directory_path=None,
                direct_only=False,
            )
        )


class _AdminRuntimeAgentPromptDispatchPort:
    def __init__(self, admin_runtime: AdminRuntimeComponents) -> None:
        self._admin_runtime = admin_runtime

    def dispatch_agent_prompt(
        self,
        *,
        target: str,
        prompt: str,
        model: str | None = None,
        reasoning_effort: str | None = None,
        reasoning_effort_source: str | None = None,
        vm_user: str = "agent",
        dispatch_id: str | None = None,
        resume_thread_id: str | None = None,
        resume_mode: str = "new_context",
        persist_thread_id: bool = True,
        mcp_gateway_url: str | None = None,
    ) -> Any:
        return self._admin_runtime.vm_agent_admin.resume_agent(
            target=target,
            prompt=prompt,
            model=model,
            reasoning_effort=reasoning_effort,
            reasoning_effort_source=reasoning_effort_source,
            vm_user=vm_user,
            dispatch_id=dispatch_id,
            resume_thread_id=resume_thread_id,
            resume_mode=resume_mode,
            persist_thread_id=persist_thread_id,
            mcp_gateway_url=mcp_gateway_url,
        )
