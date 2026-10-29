from __future__ import annotations

from evander.provider_profiles import (
    DEFAULT_PROVIDER_PROFILE_NAME,
    is_pure_orchestration_core_profile,
    resolve_provider_profile,
)


def test_agent_default_profile_remains_default_domain_convenience_mode() -> None:
    profile = resolve_provider_profile(DEFAULT_PROVIDER_PROFILE_NAME)

    assert profile.name == "profiles/agent-default"
    assert profile.include_builtin is True
    assert is_pure_orchestration_core_profile(profile.name) is False


def test_core_minimal_profile_is_supported_for_production_orchestration_only_mode() -> None:
    profile = resolve_provider_profile("profiles/core-minimal")

    assert profile.include_builtin is False
    assert profile.provider_modules == ()
    assert is_pure_orchestration_core_profile(profile.name) is True
    assert "Pure orchestration-core runtime" in profile.description
