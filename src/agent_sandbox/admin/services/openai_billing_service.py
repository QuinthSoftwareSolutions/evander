from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from typing import Callable, Literal

import httpx
from pydantic import BaseModel

from agent_sandbox.config import GatewayConfig
from agent_sandbox.provider_config import ProviderConfigResolver

OPENAI_COST_LOOKBACK_DAYS = 30
OPENAI_COSTS_PATH = "/organization/costs"
OPENAI_USAGE_DASHBOARD_URL = "https://platform.openai.com/usage"


class OpenAICostStatus(BaseModel):
    provider: Literal["openai"] = "openai"
    status: Literal["ok", "not_configured", "unavailable", "error"]
    cost_usd: float | None = None
    currency: str | None = "usd"
    period_days: int = OPENAI_COST_LOOKBACK_DAYS
    period_start: datetime | None = None
    period_end: datetime | None = None
    api_key_env: str
    checked_at: datetime
    source: str | None = None
    message: str | None = None
    dashboard_url: str = OPENAI_USAGE_DASHBOARD_URL


class OpenAIBillingService:
    def __init__(
        self,
        config: GatewayConfig,
        *,
        provider_config_resolver: ProviderConfigResolver | None = None,
        transport: httpx.BaseTransport | None = None,
        cache_ttl_seconds: float = 300.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self.provider_config_resolver = provider_config_resolver or ProviderConfigResolver(config)
        self.transport = transport
        self.cache_ttl_seconds = cache_ttl_seconds
        self._clock = clock or time.time
        self._cache: dict[str, tuple[float, OpenAICostStatus]] = {}

    def fetch_status(
        self, directory_path: str | None = None, *, force_refresh: bool = False
    ) -> OpenAICostStatus:
        resolved_config = self.provider_config_resolver.resolve_for_directory(directory_path)
        api_key_env, api_key = _resolve_costs_api_key(resolved_config)
        checked_at = datetime.now(UTC)
        if not api_key:
            return OpenAICostStatus(
                status="not_configured",
                api_key_env=api_key_env,
                checked_at=checked_at,
                message=_missing_key_message(resolved_config),
            )

        cache_key = f"{resolved_config.openai_api_base_url}|{api_key_env}"
        cached = self._cache.get(cache_key)
        now = self._clock()
        if (
            not force_refresh
            and cached is not None
            and now - cached[0] < self.cache_ttl_seconds
        ):
            return cached[1]

        status = self._fetch_cost_status(
            resolved_config=resolved_config,
            api_key_env=api_key_env,
            api_key=api_key,
            checked_at=checked_at,
        )
        self._cache[cache_key] = (now, status)
        return status

    def _fetch_cost_status(
        self,
        *,
        resolved_config: GatewayConfig,
        api_key_env: str,
        api_key: str,
        checked_at: datetime,
    ) -> OpenAICostStatus:
        try:
            url = _costs_url(resolved_config.openai_api_base_url)
        except ValueError as exc:
            return OpenAICostStatus(
                status="unavailable",
                api_key_env=api_key_env,
                checked_at=checked_at,
                message=str(exc),
            )

        period_end = checked_at
        period_start = period_end - timedelta(days=OPENAI_COST_LOOKBACK_DAYS)
        try:
            with httpx.Client(
                timeout=min(resolved_config.openai_relay_timeout_seconds, 30.0),
                transport=self.transport,
            ) as client:
                response = client.get(
                    url,
                    params={
                        "start_time": int(period_start.timestamp()),
                        "end_time": int(period_end.timestamp()),
                        "bucket_width": "1d",
                        "limit": OPENAI_COST_LOOKBACK_DAYS + 1,
                    },
                    headers={
                        "authorization": f"Bearer {api_key}",
                        "accept": "application/json",
                    },
                )
        except httpx.HTTPError as exc:
            return OpenAICostStatus(
                status="error",
                api_key_env=api_key_env,
                checked_at=checked_at,
                message=f"OpenAI cost check failed: {exc.__class__.__name__}.",
            )

        if response.status_code in {401, 403}:
            return OpenAICostStatus(
                status="unavailable",
                api_key_env=api_key_env,
                checked_at=checked_at,
                message=_rejected_key_message(api_key_env, api_key),
            )
        if response.status_code == 404:
            return OpenAICostStatus(
                status="unavailable",
                api_key_env=api_key_env,
                checked_at=checked_at,
                message="OpenAI Costs API endpoint is unavailable.",
            )
        if response.status_code >= 400:
            return OpenAICostStatus(
                status="error",
                api_key_env=api_key_env,
                checked_at=checked_at,
                message=f"OpenAI cost check failed with HTTP {response.status_code}.",
            )

        try:
            payload = response.json()
        except ValueError:
            return OpenAICostStatus(
                status="error",
                api_key_env=api_key_env,
                checked_at=checked_at,
                message="OpenAI cost response was not valid JSON.",
            )
        cost_total = _extract_cost_total(payload)
        if cost_total is None:
            return OpenAICostStatus(
                status="unavailable",
                api_key_env=api_key_env,
                checked_at=checked_at,
                message="OpenAI cost response did not include bucket amount values.",
            )
        cost, currency = cost_total
        return OpenAICostStatus(
            status="ok",
            cost_usd=cost,
            currency=currency,
            period_start=period_start,
            period_end=period_end,
            api_key_env=api_key_env,
            checked_at=checked_at,
            source="organization_costs",
        )


def _costs_url(openai_api_base_url: str) -> str:
    base = httpx.URL(openai_api_base_url)
    if base.scheme not in {"http", "https"} or not base.host:
        raise ValueError("OpenAI API base URL is not an HTTP URL.")
    origin = f"{base.scheme}://{base.host}"
    if base.port is not None:
        origin = f"{origin}:{base.port}"
    if base.host.lower() != "api.openai.com":
        raise ValueError("OpenAI Costs API checks are only supported for api.openai.com.")
    version_path = base.path.rstrip("/") or "/v1"
    if version_path == "/":
        version_path = "/v1"
    return f"{origin}{version_path}{OPENAI_COSTS_PATH}"


def _resolve_costs_api_key(resolved_config: GatewayConfig) -> tuple[str, str | None]:
    candidates = _costs_api_key_env_candidates(resolved_config)
    for api_key_env in candidates:
        api_key = os.environ.get(api_key_env)
        if api_key:
            return api_key_env, api_key
    return candidates[0], None


def _costs_api_key_env_candidates(resolved_config: GatewayConfig) -> list[str]:
    costs_api_key_env = _clean_env_name(
        getattr(resolved_config, "openai_costs_api_key_env", None)
    )
    relay_api_key_env = _clean_env_name(resolved_config.openai_api_key_env) or "OPENAI_API_KEY"
    candidates: list[str] = []
    for api_key_env in (costs_api_key_env, relay_api_key_env):
        if api_key_env and api_key_env not in candidates:
            candidates.append(api_key_env)
    return candidates or ["OPENAI_ADMIN_API_KEY"]


def _clean_env_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _missing_key_message(resolved_config: GatewayConfig) -> str:
    candidates = _costs_api_key_env_candidates(resolved_config)
    primary = candidates[0]
    if len(candidates) == 1:
        return (
            f"Host environment variable {primary} is not set. Set it to an OpenAI "
            "Admin API key with organization usage/cost access."
        )
    fallback = candidates[1]
    return (
        f"Host environment variable {primary} is not set. Fallback {fallback} is "
        "also unset. Set the costs key to an OpenAI Admin API key with organization "
        "usage/cost access."
    )


def _rejected_key_message(api_key_env: str, api_key: str) -> str:
    key_kind = _api_key_kind(api_key)
    if key_kind == "project":
        return (
            f"OpenAI Costs API rejected {api_key_env} (project key). Use an OpenAI "
            "Admin API key for organization costs, typically via OPENAI_ADMIN_API_KEY; "
            "project key permissions do not grant organization usage/cost access."
        )
    return (
        f"OpenAI Costs API rejected {api_key_env}. Grant organization usage/cost "
        "access, including api.usage.read for restricted keys, and verify the key "
        "owner has the correct organization role."
    )


def _api_key_kind(api_key: str) -> str:
    if api_key.startswith("sk-admin-"):
        return "admin"
    if api_key.startswith("sk-proj-"):
        return "project"
    if api_key.startswith("sk-"):
        return "legacy_or_user"
    return "unknown"


def _extract_cost_total(payload: object) -> tuple[float, str] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    total = 0.0
    currency = "usd"
    for bucket in data:
        if not isinstance(bucket, dict):
            return None
        results = bucket.get("results", [])
        if not isinstance(results, list):
            return None
        for result in results:
            if not isinstance(result, dict):
                return None
            amount = result.get("amount")
            if not isinstance(amount, dict):
                return None
            value = amount.get("value")
            if not isinstance(value, int | float):
                return None
            result_currency = amount.get("currency")
            if isinstance(result_currency, str) and result_currency:
                currency = result_currency
            total += float(value)
    return total, currency
