from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Callable, Literal

import httpx
from pydantic import BaseModel

from agent_sandbox.config import GatewayConfig
from agent_sandbox.provider_config import ProviderConfigResolver

OPENAI_LOW_BALANCE_THRESHOLD_USD = 5.0
OPENAI_CREDIT_GRANTS_PATH = "/dashboard/billing/credit_grants"
OPENAI_CREDIT_GRANTS_DASHBOARD_URL = (
    "https://platform.openai.com/settings/organization/billing/credit-grants"
)


class OpenAIBalanceStatus(BaseModel):
    provider: Literal["openai"] = "openai"
    status: Literal["ok", "not_configured", "unavailable", "error"]
    balance_usd: float | None = None
    currency: str | None = "usd"
    low_balance_threshold_usd: float = OPENAI_LOW_BALANCE_THRESHOLD_USD
    low_balance: bool = False
    api_key_env: str
    checked_at: datetime
    source: str | None = None
    message: str | None = None
    dashboard_url: str = OPENAI_CREDIT_GRANTS_DASHBOARD_URL


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
        self._cache: dict[str, tuple[float, OpenAIBalanceStatus]] = {}

    def fetch_status(
        self, directory_path: str | None = None, *, force_refresh: bool = False
    ) -> OpenAIBalanceStatus:
        resolved_config = self.provider_config_resolver.resolve_for_directory(directory_path)
        api_key_env = resolved_config.openai_api_key_env
        checked_at = datetime.now(UTC)
        api_key = os.environ.get(api_key_env)
        if not api_key:
            return OpenAIBalanceStatus(
                status="not_configured",
                api_key_env=api_key_env,
                checked_at=checked_at,
                message=f"Host environment variable {api_key_env} is not set.",
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

        status = self._fetch_credit_grants_status(
            resolved_config=resolved_config,
            api_key=api_key,
            checked_at=checked_at,
        )
        self._cache[cache_key] = (now, status)
        return status

    def _fetch_credit_grants_status(
        self, *, resolved_config: GatewayConfig, api_key: str, checked_at: datetime
    ) -> OpenAIBalanceStatus:
        api_key_env = resolved_config.openai_api_key_env
        try:
            url = _credit_grants_url(resolved_config.openai_api_base_url)
        except ValueError as exc:
            return OpenAIBalanceStatus(
                status="unavailable",
                api_key_env=api_key_env,
                checked_at=checked_at,
                message=str(exc),
            )

        try:
            with httpx.Client(
                timeout=min(resolved_config.openai_relay_timeout_seconds, 30.0),
                transport=self.transport,
            ) as client:
                response = client.get(
                    url,
                    headers={
                        "authorization": f"Bearer {api_key}",
                        "accept": "application/json",
                    },
                )
        except httpx.HTTPError as exc:
            return OpenAIBalanceStatus(
                status="error",
                api_key_env=api_key_env,
                checked_at=checked_at,
                message=f"OpenAI balance check failed: {exc.__class__.__name__}.",
            )

        if response.status_code in {401, 403}:
            return OpenAIBalanceStatus(
                status="unavailable",
                api_key_env=api_key_env,
                checked_at=checked_at,
                message=(
                    "OpenAI rejected the billing request; the configured key may not "
                    "have billing access."
                ),
            )
        if response.status_code == 404:
            return OpenAIBalanceStatus(
                status="unavailable",
                api_key_env=api_key_env,
                checked_at=checked_at,
                message="OpenAI credit-grants balance endpoint is unavailable.",
            )
        if response.status_code >= 400:
            return OpenAIBalanceStatus(
                status="error",
                api_key_env=api_key_env,
                checked_at=checked_at,
                message=f"OpenAI balance check failed with HTTP {response.status_code}.",
            )

        try:
            payload = response.json()
        except ValueError:
            return OpenAIBalanceStatus(
                status="error",
                api_key_env=api_key_env,
                checked_at=checked_at,
                message="OpenAI balance response was not valid JSON.",
            )
        balance = _extract_total_available(payload)
        if balance is None:
            return OpenAIBalanceStatus(
                status="unavailable",
                api_key_env=api_key_env,
                checked_at=checked_at,
                message="OpenAI balance response did not include total_available.",
            )
        low_balance = balance < OPENAI_LOW_BALANCE_THRESHOLD_USD
        return OpenAIBalanceStatus(
            status="ok",
            balance_usd=balance,
            currency="usd",
            low_balance=low_balance,
            api_key_env=api_key_env,
            checked_at=checked_at,
            source="credit_grants",
            message=(
                f"OpenAI balance is below ${OPENAI_LOW_BALANCE_THRESHOLD_USD:.0f}."
                if low_balance
                else None
            ),
        )


def _credit_grants_url(openai_api_base_url: str) -> str:
    base = httpx.URL(openai_api_base_url)
    if base.scheme not in {"http", "https"} or not base.host:
        raise ValueError("OpenAI API base URL is not an HTTP URL.")
    origin = f"{base.scheme}://{base.host}"
    if base.port is not None:
        origin = f"{origin}:{base.port}"
    if base.host.lower() != "api.openai.com":
        raise ValueError("OpenAI balance checks are only supported for api.openai.com.")
    return f"{origin}{OPENAI_CREDIT_GRANTS_PATH}"


def _extract_total_available(payload: object) -> float | None:
    if not isinstance(payload, dict):
        return None
    for key in ("total_available", "balance", "credit_balance"):
        value = payload.get(key)
        if isinstance(value, int | float):
            return float(value)
    amount = payload.get("amount_available")
    if isinstance(amount, dict):
        value = amount.get("value")
        if isinstance(value, int | float):
            return float(value)
    return None
