from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderPluginProfile:
    name: str
    include_builtin: bool
    provider_modules: tuple[str, ...]


PROVIDER_PLUGIN_PROFILES: dict[str, ProviderPluginProfile] = {
    "profiles/core-minimal": ProviderPluginProfile(
        name="profiles/core-minimal",
        include_builtin=False,
        provider_modules=(),
    ),
    "profiles/agent-default": ProviderPluginProfile(
        name="profiles/agent-default",
        include_builtin=True,
        provider_modules=(),
    ),
}

DEFAULT_PROVIDER_PROFILE_NAME = "profiles/agent-default"


def resolve_provider_profile(profile_name: str) -> ProviderPluginProfile:
    normalized = str(profile_name).strip()
    if not normalized:
        raise ValueError("provider_loader.provider_profile must not be empty")
    profile = PROVIDER_PLUGIN_PROFILES.get(normalized)
    if profile is None:
        allowed = ", ".join(sorted(PROVIDER_PLUGIN_PROFILES))
        raise ValueError(
            f"Unknown provider loader profile {normalized!r}. Supported profiles: {allowed}"
        )
    return profile


def resolve_provider_module_names(*, profile_name: str, extra_modules: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    profile = resolve_provider_profile(profile_name)
    merged: list[str] = []
    for module_name in (*profile.provider_modules, *tuple(extra_modules)):
        normalized = str(module_name).strip()
        if normalized:
            merged.append(normalized)
    return tuple(merged)
