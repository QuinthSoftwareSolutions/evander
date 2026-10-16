"""Explicit read-only integration readiness checks, separate from resource readiness."""

import time

from evander.integration_contracts import IntegrationCheckResult


def check(context, service_factory):
    domain = service_factory(context)
    _, settings = domain.store.settings(context.directory_path)
    if not settings.connections and not settings.profiles:
        return IntegrationCheckResult("unknown", "not_configured")
    deadline = time.monotonic() + 10
    try:
        for connection_id in settings.connections:
            if time.monotonic() >= deadline:
                return IntegrationCheckResult("unknown", "check_budget_exhausted")
            domain.readiness(context.directory_path, connection_id=connection_id)
        for profile_id, profile in settings.profiles.items():
            if not profile.connection_id:
                if time.monotonic() >= deadline:
                    return IntegrationCheckResult("unknown", "check_budget_exhausted")
                domain.readiness(context.directory_path, profile_id=profile_id)
    except Exception:
        return IntegrationCheckResult("blocked", "provider_unavailable")
    return IntegrationCheckResult("ready", "connections_ready")
