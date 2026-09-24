from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from fnmatch import fnmatchcase
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from evander.model_usage.models import ModelInferenceUsageEvent

MODEL_PRICING_PATH_ENV = "EVANDER_MODEL_PRICING_PATH"
DEFAULT_MODEL_PRICING_FILENAME = "model-pricing.openai.json"
DEFAULT_MODEL_PRICING_SEED_FILENAME = "model-pricing.openai.json.example"
LEGACY_DEFAULT_MODEL_PRICING_SHA256 = (
    "f6933434fe83aa8bafcc9fe92fa2add02f04c25d5a58e2329eae2cc83e0bdb36"
)
TOKENS_PER_MILLION = Decimal("1000000")

ModelCostStatus = Literal[
    "estimated",
    "reported",
    "pricing_unavailable",
    "usage_unavailable",
    "unsupported_provider",
    "insufficient_token_breakdown",
]


@dataclass(frozen=True)
class ModelPricingRule:
    id: str
    provider: str
    models: tuple[str, ...]
    model_patterns: tuple[str, ...]
    effective_from: datetime | None
    effective_to: datetime | None
    currency: str
    input_per_1m_tokens: Decimal
    cached_input_per_1m_tokens: Decimal | None
    cache_write_5m_per_1m_tokens: Decimal | None
    cache_write_1h_per_1m_tokens: Decimal | None
    output_per_1m_tokens: Decimal
    batch_multiplier: Decimal | None = None
    service_tier: str | None = None
    long_context_input_tokens_over: int | None = None
    long_context_input_multiplier: Decimal | None = None
    long_context_output_multiplier: Decimal | None = None

    def matches(
        self,
        *,
        provider: str,
        model: str,
        occurred_at: datetime,
        service_tier: str | None = None,
    ) -> bool:
        if self.provider != provider:
            return False
        if self.service_tier is not None and self.service_tier != service_tier:
            return False
        if self.effective_from is not None and occurred_at < self.effective_from:
            return False
        if self.effective_to is not None and occurred_at >= self.effective_to:
            return False
        if model in self.models:
            return True
        return any(fnmatchcase(model, pattern) for pattern in self.model_patterns)


@dataclass(frozen=True)
class ModelPricingCatalog:
    version: str
    currency: str
    rules: tuple[ModelPricingRule, ...]
    source_path: Path | None = None


@dataclass(frozen=True)
class ModelCostEstimate:
    currency: str | None
    estimated_total: Decimal | None
    input_cost: Decimal | None
    cached_input_cost: Decimal | None
    cache_write_cost: Decimal | None
    output_cost: Decimal | None
    status: ModelCostStatus
    pricing_rule_id: str | None = None
    pricing_version: str | None = None
    explanation: str | None = None
    cached_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    reported_total: Decimal | None = None

    @property
    def total(self) -> Decimal | None:
        return self.reported_total if self.reported_total is not None else self.estimated_total

    def model_dump(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "estimated_total": _decimal_to_float(self.estimated_total),
            "reported_total": _decimal_to_float(self.reported_total),
            "total": _decimal_to_float(self.total),
            "cost_source": (
                "provider_reported"
                if self.reported_total is not None
                else "catalog_estimate"
                if self.estimated_total is not None
                else None
            ),
            "input_cost": _decimal_to_float(self.input_cost),
            "cached_input_cost": _decimal_to_float(self.cached_input_cost),
            "cache_write_cost": _decimal_to_float(self.cache_write_cost),
            "output_cost": _decimal_to_float(self.output_cost),
            "status": self.status,
            "pricing_rule_id": self.pricing_rule_id,
            "pricing_version": self.pricing_version,
            "explanation": self.explanation,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_input_tokens": self.cache_write_input_tokens,
        }


def default_model_pricing_catalog_path(*, runtime_dir: Path | str | None = None) -> Path:
    configured = str(os.environ.get(MODEL_PRICING_PATH_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser()

    runtime_root = (
        Path(runtime_dir).expanduser() if runtime_dir is not None else Path.cwd() / "runtime"
    )
    return runtime_root / DEFAULT_MODEL_PRICING_FILENAME


def default_model_pricing_catalog_seed_path() -> Path:
    candidates = [
        Path.cwd() / "config" / DEFAULT_MODEL_PRICING_SEED_FILENAME,
        Path(__file__).resolve().parents[3] / "config" / DEFAULT_MODEL_PRICING_SEED_FILENAME,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[-1]


def seed_default_model_pricing_catalog(
    runtime_dir: Path | str,
    *,
    legacy_catalog_path: Path | str | None = None,
) -> Path:
    target = default_model_pricing_catalog_path(runtime_dir=runtime_dir)
    if target.exists():
        return target

    payload = default_model_pricing_catalog_seed_path().read_bytes()
    if legacy_catalog_path is not None:
        legacy_path = Path(legacy_catalog_path).expanduser()
        if legacy_path.exists():
            legacy_payload = legacy_path.read_bytes()
            if sha256(legacy_payload).hexdigest() != LEGACY_DEFAULT_MODEL_PRICING_SHA256:
                payload = legacy_payload
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o664,
        )
    except FileExistsError:
        return target
    with os.fdopen(descriptor, "wb") as target_file:
        target_file.write(payload)
    return target


@lru_cache(maxsize=16)
def _load_model_pricing_catalog_cached(
    path_key: str,
    _mtime_ns: int,
    _size: int,
    _inode: int,
    _device: int,
) -> ModelPricingCatalog:
    return load_model_pricing_catalog(Path(path_key))


def default_model_pricing_catalog(*, runtime_dir: Path | str | None = None) -> ModelPricingCatalog:
    path = default_model_pricing_catalog_path(runtime_dir=runtime_dir)
    resolved_path = path.resolve()
    stat = resolved_path.stat()
    return _load_model_pricing_catalog_cached(
        str(resolved_path),
        stat.st_mtime_ns,
        stat.st_size,
        stat.st_ino,
        stat.st_dev,
    )


def clear_model_pricing_catalog_cache() -> None:
    _load_model_pricing_catalog_cached.cache_clear()


def load_model_pricing_catalog(path: Path | str) -> ModelPricingCatalog:
    catalog_path = Path(path)
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    return load_model_pricing_catalog_payload(payload, source_path=catalog_path)


def load_model_pricing_catalog_payload(
    payload: Any, *, source_path: Path | None = None
) -> ModelPricingCatalog:
    if not isinstance(payload, dict):
        raise ValueError("model pricing catalog must be a JSON object")

    version = _required_text(payload.get("version"), field_name="version")
    currency = _required_text(payload.get("currency"), field_name="currency").lower()
    if currency != "usd":
        raise ValueError("model pricing catalog currency must be usd")
    rules_payload = payload.get("rules")
    if not isinstance(rules_payload, list) or not rules_payload:
        raise ValueError("model pricing catalog requires a non-empty rules array")

    rules = tuple(_parse_pricing_rule(entry, default_currency=currency) for entry in rules_payload)
    for rule in rules:
        if rule.currency != currency:
            raise ValueError("model pricing rule currency must match catalog currency")
    return ModelPricingCatalog(
        version=version,
        currency=currency,
        rules=rules,
        source_path=source_path,
    )


def normalize_model_pricing_catalog_payload(payload: Any) -> dict[str, Any]:
    catalog = load_model_pricing_catalog_payload(payload)
    raw_payload = payload if isinstance(payload, dict) else {}
    description = _optional_text(raw_payload.get("description"))
    sources_payload = raw_payload.get("sources")
    sources = (
        [str(source).strip() for source in sources_payload if str(source).strip()]
        if isinstance(sources_payload, list)
        else []
    )
    return {
        "version": catalog.version,
        "currency": catalog.currency,
        "description": description,
        "sources": sources,
        "rules": [_pricing_rule_payload(rule) for rule in catalog.rules],
    }


def model_pricing_catalog_payload(catalog: ModelPricingCatalog) -> dict[str, Any]:
    """Return the exact normalized pricing snapshot used for cost accounting."""
    return {
        "version": catalog.version,
        "currency": catalog.currency,
        "rules": [_pricing_rule_payload(rule) for rule in catalog.rules],
    }


def model_pricing_catalog_fingerprint(catalog: ModelPricingCatalog) -> str:
    """Identify pricing by content so same-version edits invalidate cached balances."""
    payload = json.dumps(
        model_pricing_catalog_payload(catalog),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def estimate_model_usage_cost(
    event: ModelInferenceUsageEvent,
    catalog: ModelPricingCatalog | None,
    *,
    currency: str = "usd",
) -> ModelCostEstimate:
    requested_currency = str(currency or "").strip().lower() or "usd"
    provider = str(event.provider or "").strip().lower()
    if provider == "openrouter":
        usage = event.raw_usage_metadata.get("usage")
        if isinstance(usage, dict) and "cost" in usage:
            reported_cost = _optional_non_negative_decimal(usage.get("cost"))
            if reported_cost is None:
                return _unavailable_estimate(
                    "pricing_unavailable",
                    "OpenRouter reported an invalid usage.cost value.",
                    requested_currency,
                )
            if requested_currency != "usd":
                return _unavailable_estimate(
                    "pricing_unavailable",
                    "OpenRouter usage.cost is reported in USD.",
                    requested_currency,
                )
            return ModelCostEstimate(
                currency="usd",
                estimated_total=None,
                input_cost=None,
                cached_input_cost=None,
                cache_write_cost=None,
                output_cost=None,
                status="reported",
                explanation="Provider-reported OpenRouter usage.cost; no catalog estimate used.",
                cached_input_tokens=extract_cached_input_tokens(event),
                cache_write_input_tokens=extract_cache_write_input_tokens(event).total,
                reported_total=reported_cost,
            )
    if event.normalized_usage_status != "available":
        return _unavailable_estimate(
            "usage_unavailable",
            "Model usage was recorded without token usage.",
            requested_currency,
        )
    if catalog is None or not catalog.rules:
        return _unavailable_estimate(
            "pricing_unavailable",
            "No model pricing catalog is available.",
            requested_currency,
        )
    if catalog.currency != requested_currency:
        explanation = (
            f"Pricing catalog currency {catalog.currency!r} does not match requested "
            f"currency {requested_currency!r}."
        )
        return _unavailable_estimate(
            "pricing_unavailable",
            explanation,
            requested_currency,
        )

    model = str(event.model or "").strip()
    if not model:
        return _unavailable_estimate(
            "pricing_unavailable",
            "Model usage event does not include a model.",
            requested_currency,
        )
    if event.input_tokens is None or event.output_tokens is None:
        return _unavailable_estimate(
            "insufficient_token_breakdown",
            "Cost inference requires input and output token counts.",
            requested_currency,
        )

    occurred_at = _as_utc(event.occurred_at)
    service_tier = _service_tier_from_event(event)
    rule = _match_rule(
        catalog,
        provider=provider,
        model=model,
        occurred_at=occurred_at,
        service_tier=service_tier,
    )
    if rule is None:
        occurred_value = occurred_at.isoformat().replace("+00:00", "Z")
        catalog_providers = {pricing_rule.provider for pricing_rule in catalog.rules}
        if provider and provider not in catalog_providers:
            return _unavailable_estimate(
                "pricing_unavailable",
                f"Pricing catalog does not include provider {event.provider!r}.",
                requested_currency,
            )
        return _unavailable_estimate(
            "pricing_unavailable",
            f"No pricing rule matched model {model!r} at {occurred_value}.",
            requested_currency,
        )

    cached_input_tokens = extract_cached_input_tokens(event)
    cache_write_tokens = extract_cache_write_input_tokens(event)
    billable_cached_input_tokens = cached_input_tokens or 0
    if _uses_separate_prompt_cache_counters(event):
        billable_input_tokens = int(event.input_tokens)
    else:
        billable_input_tokens = max(
            int(event.input_tokens) - billable_cached_input_tokens - cache_write_tokens.total,
            0,
        )
    output_tokens = int(event.output_tokens)

    input_rate = rule.input_per_1m_tokens
    cached_input_rate = (
        rule.cached_input_per_1m_tokens
        if rule.cached_input_per_1m_tokens is not None
        else rule.input_per_1m_tokens
    )
    cache_write_5m_rate = (
        rule.cache_write_5m_per_1m_tokens
        if rule.cache_write_5m_per_1m_tokens is not None
        else rule.input_per_1m_tokens
    )
    cache_write_1h_rate = (
        rule.cache_write_1h_per_1m_tokens
        if rule.cache_write_1h_per_1m_tokens is not None
        else rule.input_per_1m_tokens
    )
    output_rate = rule.output_per_1m_tokens

    if (
        rule.long_context_input_tokens_over is not None
        and event.input_tokens > rule.long_context_input_tokens_over
    ):
        if rule.long_context_input_multiplier is not None:
            input_rate *= rule.long_context_input_multiplier
            cached_input_rate *= rule.long_context_input_multiplier
            cache_write_5m_rate *= rule.long_context_input_multiplier
            cache_write_1h_rate *= rule.long_context_input_multiplier
        if rule.long_context_output_multiplier is not None:
            output_rate *= rule.long_context_output_multiplier

    if _is_batch_event(event) and rule.batch_multiplier is not None:
        input_rate *= rule.batch_multiplier
        cached_input_rate *= rule.batch_multiplier
        cache_write_5m_rate *= rule.batch_multiplier
        cache_write_1h_rate *= rule.batch_multiplier
        output_rate *= rule.batch_multiplier

    input_cost = _token_cost(billable_input_tokens, input_rate)
    cached_input_cost = _token_cost(billable_cached_input_tokens, cached_input_rate)
    cache_write_cost = _token_cost(cache_write_tokens.ephemeral_5m, cache_write_5m_rate)
    cache_write_cost += _token_cost(cache_write_tokens.ephemeral_1h, cache_write_1h_rate)
    output_cost = _token_cost(output_tokens, output_rate)
    total = input_cost + cached_input_cost + cache_write_cost + output_cost

    explanation = None
    if cached_input_tokens is None and not cache_write_tokens.total:
        explanation = (
            "No cached input token breakdown recorded; all input tokens priced at the "
            "standard input rate."
        )

    return ModelCostEstimate(
        currency=rule.currency,
        estimated_total=total,
        input_cost=input_cost,
        cached_input_cost=cached_input_cost,
        cache_write_cost=cache_write_cost,
        output_cost=output_cost,
        status="estimated",
        pricing_rule_id=rule.id,
        pricing_version=catalog.version,
        explanation=explanation,
        cached_input_tokens=cached_input_tokens,
        cache_write_input_tokens=cache_write_tokens.total,
    )


def extract_cached_input_tokens(event: ModelInferenceUsageEvent) -> int | None:
    usage = event.raw_usage_metadata.get("usage")
    if not isinstance(usage, dict):
        return None
    for path in (
        ("input_tokens_details", "cached_tokens"),
        ("prompt_tokens_details", "cached_tokens"),
        ("cached_input_tokens",),
        ("cache_read_input_tokens",),
    ):
        value = _nested_value(usage, path)
        if value is None:
            continue
        number = _optional_non_negative_int(value)
        if number is not None:
            return number
    return None


@dataclass(frozen=True)
class CacheWriteInputTokens:
    ephemeral_5m: int = 0
    ephemeral_1h: int = 0

    @property
    def total(self) -> int:
        return self.ephemeral_5m + self.ephemeral_1h


def extract_cache_write_input_tokens(event: ModelInferenceUsageEvent) -> CacheWriteInputTokens:
    usage = event.raw_usage_metadata.get("usage")
    if not isinstance(usage, dict):
        return CacheWriteInputTokens()
    for path in (
        ("input_tokens_details", "cache_write_tokens"),
        ("prompt_tokens_details", "cache_write_tokens"),
    ):
        value = _nested_value(usage, path)
        if value is None:
            continue
        number = _optional_non_negative_int(value)
        if number is not None:
            return CacheWriteInputTokens(ephemeral_5m=number)
    cache_creation = usage.get("cache_creation")
    cache_creation_payload = cache_creation if isinstance(cache_creation, dict) else {}
    ephemeral_5m = _optional_non_negative_int(
        cache_creation_payload.get("ephemeral_5m_input_tokens")
    )
    ephemeral_1h = _optional_non_negative_int(
        cache_creation_payload.get("ephemeral_1h_input_tokens")
    )
    total = _optional_non_negative_int(usage.get("cache_creation_input_tokens"))
    billable_5m = ephemeral_5m or 0
    billable_1h = ephemeral_1h or 0
    if total is not None and total > billable_5m + billable_1h:
        billable_5m += total - billable_5m - billable_1h
    return CacheWriteInputTokens(ephemeral_5m=billable_5m, ephemeral_1h=billable_1h)


def _uses_separate_prompt_cache_counters(event: ModelInferenceUsageEvent) -> bool:
    usage = event.raw_usage_metadata.get("usage")
    if not isinstance(usage, dict):
        return False
    if _optional_non_negative_int(usage.get("cache_read_input_tokens")) is not None:
        return True
    if _optional_non_negative_int(usage.get("cache_creation_input_tokens")) is not None:
        return True
    return isinstance(usage.get("cache_creation"), dict)


def _parse_pricing_rule(payload: Any, *, default_currency: str) -> ModelPricingRule:
    if not isinstance(payload, dict):
        raise ValueError("model pricing rules must be JSON objects")
    rates = payload.get("rates_per_1m_tokens")
    if not isinstance(rates, dict):
        raise ValueError("model pricing rule requires rates_per_1m_tokens")
    models = _text_tuple(payload.get("models"))
    patterns = _text_tuple(payload.get("model_patterns"))
    if not models and not patterns:
        raise ValueError("model pricing rule requires models or model_patterns")

    long_context = (
        payload.get("long_context") if isinstance(payload.get("long_context"), dict) else {}
    )
    return ModelPricingRule(
        id=_required_text(payload.get("id"), field_name="id"),
        provider=_required_text(payload.get("provider"), field_name="provider").lower(),
        models=models,
        model_patterns=patterns,
        effective_from=_optional_datetime(payload.get("effective_from")),
        effective_to=_optional_datetime(payload.get("effective_to")),
        currency=(_optional_text(payload.get("currency")) or default_currency).lower(),
        input_per_1m_tokens=_required_decimal(rates.get("input"), field_name="input"),
        cached_input_per_1m_tokens=_optional_decimal(rates.get("cached_input")),
        cache_write_5m_per_1m_tokens=_optional_decimal(rates.get("cache_write_5m")),
        cache_write_1h_per_1m_tokens=_optional_decimal(rates.get("cache_write_1h")),
        output_per_1m_tokens=_required_decimal(rates.get("output"), field_name="output"),
        batch_multiplier=_optional_decimal(payload.get("batch_multiplier")),
        service_tier=_optional_text(payload.get("service_tier")),
        long_context_input_tokens_over=_optional_non_negative_int(
            long_context.get("input_tokens_over")
        ),
        long_context_input_multiplier=_optional_decimal(long_context.get("input_multiplier")),
        long_context_output_multiplier=_optional_decimal(long_context.get("output_multiplier")),
    )


def _match_rule(
    catalog: ModelPricingCatalog,
    *,
    provider: str,
    model: str,
    occurred_at: datetime,
    service_tier: str | None,
) -> ModelPricingRule | None:
    matches = [
        (rule, model in rule.models)
        for rule in catalog.rules
        if rule.matches(
            provider=provider,
            model=model,
            occurred_at=occurred_at,
            service_tier=service_tier,
        )
    ]
    if not matches:
        return None
    return sorted(
        matches,
        key=lambda match: (
            1 if match[1] else 0,
            match[0].effective_from or datetime.min.replace(tzinfo=UTC),
            1 if match[0].service_tier is not None else 0,
            match[0].id,
        ),
        reverse=True,
    )[0][0]


def _token_cost(tokens: int, rate_per_1m: Decimal) -> Decimal:
    return (Decimal(tokens) * rate_per_1m) / TOKENS_PER_MILLION


def _pricing_rule_payload(rule: ModelPricingRule) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": rule.id,
        "provider": rule.provider,
        "models": list(rule.models),
        "model_patterns": list(rule.model_patterns),
        "effective_from": _datetime_payload(rule.effective_from),
        "effective_to": _datetime_payload(rule.effective_to),
        "currency": rule.currency,
        "rates_per_1m_tokens": {
            "input": str(rule.input_per_1m_tokens),
            "cached_input": (
                str(rule.cached_input_per_1m_tokens)
                if rule.cached_input_per_1m_tokens is not None
                else None
            ),
            "cache_write_5m": (
                str(rule.cache_write_5m_per_1m_tokens)
                if rule.cache_write_5m_per_1m_tokens is not None
                else None
            ),
            "cache_write_1h": (
                str(rule.cache_write_1h_per_1m_tokens)
                if rule.cache_write_1h_per_1m_tokens is not None
                else None
            ),
            "output": str(rule.output_per_1m_tokens),
        },
        "batch_multiplier": (
            str(rule.batch_multiplier) if rule.batch_multiplier is not None else None
        ),
        "service_tier": rule.service_tier,
        "long_context": None,
    }
    if rule.long_context_input_tokens_over is not None:
        payload["long_context"] = {
            "input_tokens_over": rule.long_context_input_tokens_over,
            "input_multiplier": (
                str(rule.long_context_input_multiplier)
                if rule.long_context_input_multiplier is not None
                else None
            ),
            "output_multiplier": (
                str(rule.long_context_output_multiplier)
                if rule.long_context_output_multiplier is not None
                else None
            ),
        }
    return payload


def _is_batch_event(event: ModelInferenceUsageEvent) -> bool:
    metadata = event.raw_usage_metadata.get("request_metadata")
    if not isinstance(metadata, dict):
        return False
    if metadata.get("batch") is True:
        return True
    path = str(metadata.get("path") or "").lower()
    return "/batch" in path


def _service_tier_from_event(event: ModelInferenceUsageEvent) -> str | None:
    for metadata_key in ("request_metadata", "response_metadata"):
        metadata = event.raw_usage_metadata.get(metadata_key)
        if not isinstance(metadata, dict):
            continue
        value = _optional_text(metadata.get("service_tier"))
        if value:
            return value
    return None


def _unavailable_estimate(
    status: ModelCostStatus,
    explanation: str,
    currency: str | None,
) -> ModelCostEstimate:
    return ModelCostEstimate(
        currency=currency,
        estimated_total=None,
        input_cost=None,
        cached_input_cost=None,
        cache_write_cost=None,
        output_cost=None,
        status=status,
        explanation=explanation,
    )


def _optional_non_negative_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0:
        return None
    return number


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _datetime_payload(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _optional_datetime(value: Any) -> datetime | None:
    text = _optional_text(value)
    if text is None:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _required_text(value: Any, *, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"model pricing catalog field {field_name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _text_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("model pricing catalog model fields must be arrays")
    items = tuple(str(item).strip() for item in value if str(item).strip())
    return items


def _required_decimal(value: Any, *, field_name: str) -> Decimal:
    result = _optional_decimal(value)
    if result is None:
        raise ValueError(f"model pricing catalog rate {field_name} is required")
    return result


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal value {value!r}") from exc
    if result < 0:
        raise ValueError("decimal values must be greater than or equal to 0")
    return result


def _optional_non_negative_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _nested_value(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


__all__ = [
    "DEFAULT_MODEL_PRICING_FILENAME",
    "DEFAULT_MODEL_PRICING_SEED_FILENAME",
    "LEGACY_DEFAULT_MODEL_PRICING_SHA256",
    "MODEL_PRICING_PATH_ENV",
    "ModelCostEstimate",
    "ModelCostStatus",
    "ModelPricingCatalog",
    "ModelPricingRule",
    "clear_model_pricing_catalog_cache",
    "default_model_pricing_catalog",
    "default_model_pricing_catalog_path",
    "default_model_pricing_catalog_seed_path",
    "estimate_model_usage_cost",
    "extract_cache_write_input_tokens",
    "extract_cached_input_tokens",
    "load_model_pricing_catalog",
    "load_model_pricing_catalog_payload",
    "model_pricing_catalog_fingerprint",
    "model_pricing_catalog_payload",
    "normalize_model_pricing_catalog_payload",
    "seed_default_model_pricing_catalog",
]
