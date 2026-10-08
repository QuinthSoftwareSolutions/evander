from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import ValidationError

from evander.classification.models import (
    ClassificationError,
    ClassificationProviderSettings,
    ClassificationSettings,
    DECISIONS_PROVIDER_ID,
    EvaluationRequest,
    PreparedEvaluation,
    json_bytes,
    revision,
    validate_answers,
)
from evander.model_usage.models import ModelInferenceUsageEvent
from evander.model_usage.runtime import model_usage_store
from evander.privacy.providers import (
    ProviderDispatchGuard,
    provider_dispatch_scope,
    runtime_provider_guard,
)

logger = logging.getLogger(__name__)


class ClassificationProvider(Protocol):
    supported_question_types: frozenset[str]

    def evaluate(
        self,
        request: PreparedEvaluation,
        settings: ClassificationProviderSettings,
        api_key: str,
        record_attempt: Callable[..., None],
    ) -> dict[str, Any]: ...


def parse_request(payload: Any) -> EvaluationRequest:
    try:
        return EvaluationRequest.model_validate(payload)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
            for e in exc.errors(include_input=False, include_context=False)
        )
        raise ClassificationError(f"Invalid classification request: {details}") from None


class ClassificationService:
    def __init__(
        self,
        runtime_dir: Path | None = None,
        *,
        providers: Mapping[str, ClassificationProvider] | None = None,
        secret_resolver: Callable[[str], str | None] | None = None,
        privacy_guard: ProviderDispatchGuard | None = None,
    ):
        if providers is None:
            from evander.classification.decisions import DecisionsProvider
            from evander.classification.jev import JevProvider

            providers = {"jev": JevProvider(), DECISIONS_PROVIDER_ID: DecisionsProvider()}
        self.providers = dict(providers)
        self.runtime_dir = runtime_dir
        self.secret_resolver = secret_resolver or os.environ.get
        self.privacy_guard = privacy_guard or (
            runtime_provider_guard(runtime_dir) if runtime_dir is not None else None
        )

    def _provider(self, settings: ClassificationSettings, name: str):
        if not settings.enabled:
            raise ClassificationError("Classification is disabled in this directory")
        config = settings.providers.get(name)
        if config is None or not config.enabled:
            raise ClassificationError("Classification provider is not enabled in this directory")
        provider = self.providers.get(name)
        if provider is None:
            raise ClassificationError(f"Unsupported classification provider: {name}")
        if not getattr(provider, "available", True):
            raise ClassificationError(provider.unavailable_reason)
        return provider, config

    def prepare(self, settings: ClassificationSettings, payload: Any) -> PreparedEvaluation:
        request = parse_request(payload)
        profile = None
        if request.profile_id is not None:
            profile = settings.profiles.get(request.profile_id)
            if profile is None or not profile.enabled:
                raise ClassificationError("Classification profile is unavailable in this directory")
        name = profile.provider if profile else request.provider or settings.default_provider
        provider, config = self._provider(settings, name)
        questions = profile.questions if profile else request.questions
        if any(q.type not in provider.supported_question_types for q in questions.values()):
            raise ClassificationError(
                "Provider does not support one or more requested question types"
            )
        model = (profile.model if profile else request.model) or config.default_model
        if model is None:
            raise ClassificationError("Classification provider model is not configured")
        prepared = PreparedEvaluation(
            state=request.state,
            questions=questions,
            provider=name,
            model=model,
            profile_id=request.profile_id,
            profile_revision=revision(profile.model_dump(mode="json")) if profile else None,
        )
        if len(json_bytes(prepared.model_dump(mode="json"))) > config.max_request_bytes:
            raise ClassificationError("Classification request exceeds max_request_bytes")
        return prepared.model_copy(deep=True)

    def evaluate(
        self,
        settings: ClassificationSettings,
        payload: Any,
        *,
        attribution: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.execute(settings, self.prepare(settings, payload), attribution=attribution)

    def execute(
        self,
        settings: ClassificationSettings,
        prepared: PreparedEvaluation,
        *,
        attribution: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        provider, config = self._provider(settings, prepared.provider)
        if any(
            q.type not in provider.supported_question_types for q in prepared.questions.values()
        ):
            raise ClassificationError("Provider does not support the prepared question types")
        api_key = self.secret_resolver(config.api_key_env)
        if not api_key:
            raise ClassificationError("Classification provider credential is not configured")

        def check_privacy() -> None:
            if self.privacy_guard is not None:
                self.privacy_guard.require(
                    provider=prepared.provider,
                    endpoint=getattr(provider, "endpoint", ""),
                    connection_id=f"classifier:{prepared.provider}:{config.api_key_env}",
                    directory_path=(attribution or {}).get("directory_path"),
                    capability="classification",
                )

        check_privacy()
        usage_attribution = dict(attribution or {})
        usage_attribution.update(
            api_key_name=config.api_key_env, billing_kind="api", cost_attribution_recorded=True
        )
        if self.runtime_dir is not None and usage_attribution.get("session_id"):
            try:
                context = model_usage_store(self.runtime_dir).get_active_correlation_context(
                    session_id=str(usage_attribution["session_id"])
                )
                if context is not None and context.cost_attribution_recorded:
                    usage_attribution["cost_category"] = context.cost_category
            except Exception:
                logger.warning("Unable to read classification cost attribution", exc_info=True)
        started = time.monotonic()
        with provider_dispatch_scope(check_privacy):
            response = provider.evaluate(
                prepared,
                config,
                api_key,
                lambda **attempt: self._record_attempt(prepared, usage_attribution, **attempt),
            )
        answers = validate_answers(prepared.questions, response.get("answers"))
        return {
            "success": True,
            "answers": answers,
            "provider": prepared.provider,
            "model": response.get("model") or prepared.model,
            "usage": response.get("usage", {}),
            "duration_ms": round((time.monotonic() - started) * 1000),
            "profile_id": prepared.profile_id,
            "profile_revision": prepared.profile_revision,
        }

    def list_profiles(self, settings: ClassificationSettings) -> dict[str, Any]:
        if not settings.enabled:
            return {"profiles": []}
        return {
            "profiles": [
                {
                    "id": key,
                    "label": profile.label or key,
                    "description": profile.description,
                    "provider": profile.provider,
                    "model": profile.model,
                    "revision": revision(profile.model_dump(mode="json")),
                    "questions": {
                        name: {"type": question.type}
                        for name, question in profile.questions.items()
                    },
                }
                for key, profile in sorted(settings.profiles.items())
                if profile.enabled
                and profile.provider in self.providers
                and getattr(self.providers[profile.provider], "available", True)
                and profile.provider in settings.providers
                and settings.providers[profile.provider].enabled
            ]
        }

    def _record_attempt(
        self,
        prepared,
        attribution,
        *,
        status,
        payload,
        duration_ms,
        request_body,
        response_body=b"",
        http_status=None,
    ):
        if self.runtime_dir is None:
            return
        provider = self.providers[prepared.provider]
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        tokens = {
            key: value
            for key, value in usage.items()
            if key in {"input_tokens", "output_tokens"} and type(value) is int and value >= 0
        }
        usage_metadata = dict(tokens)
        for key in ("total_tokens", "compute_units"):
            if type(usage.get(key)) is int and usage[key] >= 0:
                usage_metadata[key] = usage[key]
        for key, fields in (
            ("input_tokens_details", ("cached_tokens", "cache_write_tokens")),
            ("output_tokens_details", ("reasoning_tokens",)),
        ):
            details = usage.get(key)
            if isinstance(details, dict):
                usage_metadata[key] = {
                    field: details[field]
                    for field in fields
                    if type(details.get(field)) is int and details[field] >= 0
                }
        event = ModelInferenceUsageEvent(
            event_id=f"classification-{uuid4().hex}",
            provider=getattr(provider, "usage_provider", prepared.provider),
            cost_category=attribution.get("cost_category"),
            api_key_name=attribution.get("api_key_name"),
            billing_kind=attribution.get("billing_kind"),
            cost_attribution_recorded=bool(attribution.get("cost_attribution_recorded")),
            provider_endpoint_identifier=getattr(provider, "usage_endpoint_identifier", "evaluate"),
            model=payload["model"]
            if isinstance(payload.get("model"), str) and payload["model"].strip()
            else prepared.model,
            request_status=status,
            duration_ms=duration_ms,
            normalized_usage_status="available" if tokens else "usage_unavailable",
            **tokens,
            total_tokens=sum(tokens.values()) if len(tokens) == 2 else None,
            request_body_bytes=len(request_body),
            request_body_sha256=revision_bytes(request_body),
            response_body_bytes=len(response_body),
            response_body_sha256=revision_bytes(response_body),
            raw_usage_metadata={
                "classification_provider": prepared.provider,
                "usage": usage_metadata,
                "http_status": http_status,
                "profile_id": prepared.profile_id,
                "profile_revision": prepared.profile_revision,
            },
            **{
                key: str(value)
                for key, value in attribution.items()
                if value is not None
                and key
                in {
                    "directory_path",
                    "session_id",
                    "dispatch_id",
                    "workflow_run_id",
                    "node_id",
                    "effect_id",
                    "trace_id",
                    "agent_harness",
                    "execution_backend",
                }
            },
        )
        try:
            model_usage_store(self.runtime_dir).upsert_event(event)
        except Exception:
            logger.warning("Classification usage recording failed", exc_info=False)


def revision_bytes(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()
