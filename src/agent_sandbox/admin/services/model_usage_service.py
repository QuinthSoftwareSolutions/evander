from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, ContextManager

from agent_sandbox.model_usage.models import (
    ModelInferenceUsageEvent,
    ModelUsageCorrelationContext,
)
from agent_sandbox.model_usage.pricing import (
    MODEL_PRICING_PATH_ENV,
    ModelPricingRule,
    clear_model_pricing_catalog_cache,
    default_model_pricing_catalog_path,
    load_model_pricing_catalog_payload,
    normalize_model_pricing_catalog_payload,
)
from agent_sandbox.model_usage.runtime import model_usage_store
from agent_sandbox.operator_steering import (
    OPERATOR_STEERING_NOTE_MAX_LENGTH,
    OperatorSteeringNote,
    operator_steering_note_store,
)
from agent_sandbox.models import SessionManifest


@dataclass(frozen=True)
class ModelUsageAdminDependencies:
    runtime_dir: Path
    session_repository: Any
    normalize_agent_record_directory_path: Callable[[Any], str]
    is_container_session: Callable[[SessionManifest], bool]
    interrupt_container_operator_steering_note: Callable[..., dict[str, Any]]
    admin_config_mutation_lock: Callable[[], ContextManager[None]]
    stable_json_revision: Callable[[object], str]
    write_json_file_atomic: Callable[[Path, dict[str, Any]], None]
    revision_conflict_factory: Callable[[str, dict[str, Any]], Exception]


def _admin_datetime_payload(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _admin_decimal_payload(value: object | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _empty_model_pricing_catalog_payload() -> dict[str, Any]:
    return {
        "version": "",
        "currency": "usd",
        "description": None,
        "sources": [],
        "rules": [],
    }


class ModelUsageAdminService:
    """Model usage and operator-steering operations."""

    def __init__(self, dependencies: ModelUsageAdminDependencies) -> None:
        self._deps = dependencies

    @property
    def _runtime_dir(self) -> Path:
        return self._deps.runtime_dir

    def list_model_usage_events(
        self,
        *,
        session_id: str | None = None,
        workflow_run_id: str | None = None,
        effect_id: str | None = None,
        dispatch_id: str | None = None,
        directory_path: str | None = None,
        include_directory_descendants: bool = False,
        provider: str | None = None,
        model: str | None = None,
        agent_harness: str | None = None,
        execution_backend: str | None = None,
        normalized_usage_status: str | None = None,
        request_status: str | None = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        limit: int | None = None,
    ) -> list[ModelInferenceUsageEvent]:
        return model_usage_store(self._runtime_dir).list_events(
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            effect_id=effect_id,
            dispatch_id=dispatch_id,
            directory_path=directory_path,
            include_directory_descendants=include_directory_descendants,
            provider=provider,
            model=model,
            agent_harness=agent_harness,
            execution_backend=execution_backend,
            normalized_usage_status=normalized_usage_status,
            request_status=request_status,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
            limit=limit,
        )

    def read_model_pricing_catalog_status(self) -> dict[str, Any]:
        return self._read_model_pricing_catalog_status_unlocked()

    def _read_model_pricing_catalog_status_unlocked(self) -> dict[str, Any]:
        configured_path = str(os.environ.get(MODEL_PRICING_PATH_ENV) or "").strip()
        path = default_model_pricing_catalog_path()
        path_source = "env" if configured_path else "default"
        base_payload: dict[str, Any] = {
            "status": "error",
            "path": str(path) if path is not None else None,
            "path_source": path_source,
            "env_var": MODEL_PRICING_PATH_ENV,
            "version": None,
            "currency": None,
            "description": None,
            "sources": [],
            "rule_count": 0,
            "rules": [],
            "revision": None,
            "catalog": _empty_model_pricing_catalog_payload(),
            "editable": path is not None,
            "write_requires_privileged_unlock": True,
            "error": None,
            "error_class": None,
        }
        if path is None:
            base_payload["error"] = "No model pricing catalog path is configured."
            base_payload["error_class"] = "FileNotFoundError"
            return base_payload

        try:
            raw_payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw_payload, dict):
                raise ValueError("model pricing catalog must be a JSON object")
            catalog = load_model_pricing_catalog_payload(raw_payload, source_path=path)
            normalized_payload = normalize_model_pricing_catalog_payload(raw_payload)
        except Exception as exc:
            base_payload["error"] = str(exc)
            base_payload["error_class"] = exc.__class__.__name__
            base_payload["revision"] = self._model_pricing_error_revision(path)
            return base_payload

        return {
            **base_payload,
            "status": "ok",
            "version": catalog.version,
            "currency": catalog.currency,
            "description": normalized_payload["description"],
            "sources": normalized_payload["sources"],
            "rule_count": len(catalog.rules),
            "rules": [self._model_pricing_rule_summary(rule) for rule in catalog.rules],
            "revision": self._deps.stable_json_revision(normalized_payload),
            "catalog": normalized_payload,
            "error": None,
            "error_class": None,
        }

    def write_model_pricing_catalog(
        self, payload: dict[str, Any], *, expected_revision: str
    ) -> dict[str, Any]:
        path = default_model_pricing_catalog_path()
        if path is None:
            raise ValueError("No model pricing catalog path is configured.")
        normalized_payload = normalize_model_pricing_catalog_payload(payload)
        with self._deps.admin_config_mutation_lock():
            current = self._read_model_pricing_catalog_status_unlocked()
            if current.get("revision") != expected_revision:
                raise self._deps.revision_conflict_factory(
                    "Model pricing catalog changed before save. Reload the catalog before saving again.",
                    {
                        "current_revision": current.get("revision"),
                        "current": current,
                    },
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            self._deps.write_json_file_atomic(path, normalized_payload)
            clear_model_pricing_catalog_cache()
            return self._read_model_pricing_catalog_status_unlocked()

    def _model_pricing_error_revision(self, path: Path) -> str:
        try:
            raw_value = path.read_text(encoding="utf-8")
            revision_payload = {"path": str(path), "raw": raw_value}
        except Exception as exc:
            revision_payload = {
                "path": str(path),
                "missing_or_unreadable": True,
                "error_class": exc.__class__.__name__,
            }
        return self._deps.stable_json_revision(revision_payload)

    @staticmethod
    def _model_pricing_rule_summary(rule: ModelPricingRule) -> dict[str, Any]:
        long_context = None
        if rule.long_context_input_tokens_over is not None:
            long_context = {
                "input_tokens_over": rule.long_context_input_tokens_over,
                "input_multiplier": _admin_decimal_payload(rule.long_context_input_multiplier),
                "output_multiplier": _admin_decimal_payload(rule.long_context_output_multiplier),
            }
        return {
            "id": rule.id,
            "provider": rule.provider,
            "models": list(rule.models),
            "model_patterns": list(rule.model_patterns),
            "effective_from": _admin_datetime_payload(rule.effective_from),
            "effective_to": _admin_datetime_payload(rule.effective_to),
            "currency": rule.currency,
            "rates_per_1m_tokens": {
                "input": _admin_decimal_payload(rule.input_per_1m_tokens),
                "cached_input": _admin_decimal_payload(rule.cached_input_per_1m_tokens),
                "output": _admin_decimal_payload(rule.output_per_1m_tokens),
            },
            "batch_multiplier": _admin_decimal_payload(rule.batch_multiplier),
            "service_tier": rule.service_tier,
            "long_context": long_context,
        }

    def operator_steering_directory_path(
        self, *, session_id: str, dispatch_id: str | None = None
    ) -> str:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        normalized_dispatch_id = str(dispatch_id or "").strip() or None
        context = model_usage_store(self._runtime_dir).get_active_correlation_context(
            session_id=normalized_session_id,
            dispatch_id=normalized_dispatch_id,
        )
        if context is not None and context.directory_path:
            return self._deps.normalize_agent_record_directory_path(context.directory_path)
        session = self._deps.session_repository.get_session(normalized_session_id)
        if session is None:
            raise ValueError(f"unknown session: {normalized_session_id}")
        return self._deps.normalize_agent_record_directory_path(session.directory_path)

    def active_operator_steering_context(
        self, *, session_id: str
    ) -> ModelUsageCorrelationContext | None:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        return model_usage_store(self._runtime_dir).get_active_correlation_context(
            session_id=normalized_session_id,
        )

    def create_operator_steering_note(
        self,
        *,
        session_id: str,
        dispatch_id: str,
        message: str,
        worker_execution_id: str | None = None,
        created_by: str | None = None,
    ) -> OperatorSteeringNote:
        normalized_session_id = str(session_id or "").strip()
        normalized_dispatch_id = str(dispatch_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        if not normalized_dispatch_id:
            raise ValueError("dispatch_id is required")
        context = model_usage_store(self._runtime_dir).get_active_correlation_context(
            session_id=normalized_session_id,
            dispatch_id=normalized_dispatch_id,
        )
        if context is None:
            raise ValueError(
                "No active model-correlation context found for this session and dispatch."
            )
        return operator_steering_note_store(self._runtime_dir).create_note(
            session_id=normalized_session_id,
            dispatch_id=normalized_dispatch_id,
            worker_execution_id=worker_execution_id,
            message=message,
            created_by=created_by,
        )

    def submit_operator_steering_note(
        self,
        *,
        session_id: str,
        dispatch_id: str,
        message: str,
        worker_execution_id: str | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        normalized_session_id = str(session_id or "").strip()
        normalized_dispatch_id = str(dispatch_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        if not normalized_dispatch_id:
            raise ValueError("dispatch_id is required")
        normalized_message = self._validate_operator_steering_message(message)
        context = model_usage_store(self._runtime_dir).get_active_correlation_context(
            session_id=normalized_session_id,
            dispatch_id=normalized_dispatch_id,
        )
        if context is None:
            raise ValueError(
                "No active model-correlation context found for this session and dispatch."
            )
        session = self._deps.session_repository.get_session(normalized_session_id)
        if context.execution_backend == "container_worker_vm":
            if session is None:
                raise ValueError(f"unknown session: {normalized_session_id}")
            return self._deps.interrupt_container_operator_steering_note(
                session=session,
                context=context,
                message=normalized_message,
                worker_execution_id=worker_execution_id,
                created_by=created_by,
            )
        if session is not None and self._deps.is_container_session(session):
            return self._deps.interrupt_container_operator_steering_note(
                session=session,
                context=context,
                message=normalized_message,
                worker_execution_id=worker_execution_id,
                created_by=created_by,
            )
        note = self.create_operator_steering_note(
            session_id=normalized_session_id,
            dispatch_id=normalized_dispatch_id,
            worker_execution_id=worker_execution_id,
            message=normalized_message,
            created_by=created_by,
        )
        return {"mode": "queued_note", "note": note}

    @staticmethod
    def _validate_operator_steering_message(message: str) -> str:
        text = str(message or "").strip()
        if not text:
            raise ValueError("message is required")
        if len(text) > OPERATOR_STEERING_NOTE_MAX_LENGTH:
            raise ValueError(
                f"message must be at most {OPERATOR_STEERING_NOTE_MAX_LENGTH} characters"
            )
        return text
