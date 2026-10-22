"""Bounded read-only worker readiness, independent of Browser Sessions and SQL."""

import time
from evander.integration_contracts import IntegrationCheckResult
from evander.resources.session_owner import Peer
from .service import Service


def check_readiness(context):
    service = Service(context.runtime_dir, context.service)
    _, settings = service.store.settings(context.directory_path)
    if not settings.workers or not settings.profiles:
        return IntegrationCheckResult("unknown", "not_configured")
    if not settings.allocations_enabled:
        return IntegrationCheckResult("blocked", "allocations_disabled")
    deadline = time.monotonic() + 10
    try:
        for worker_id, worker in settings.workers.items():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return IntegrationCheckResult("unknown", "check_budget_exhausted")
            health = Peer(worker.base_url, worker.token_env, timeout=remaining).call(
                "GET", "/healthz"
            )
            if (
                health.get("worker_id") != worker_id
                or health.get("capabilities", {}).get("application_environments") != 1
            ):
                return IntegrationCheckResult("blocked", "worker_incompatible")
    except Exception:
        return IntegrationCheckResult("blocked", "worker_unavailable")
    return IntegrationCheckResult("ready", "workers_ready")
