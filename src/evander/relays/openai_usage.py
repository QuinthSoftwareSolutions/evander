from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

import httpx

from evander.model_usage.adapters import (
    MappingModelUsageExtractor,
    ModelUsageExtractionRequest,
)
from evander.model_usage.models import ModelUsageCorrelationContext
from evander.model_usage.runtime import MODEL_USAGE_AGENT_HARNESS


class OpenAIUsageAccounting:
    """Model-usage capture and provider request correlation for the OpenAI relay."""

    def _active_model_usage_correlation_context(
        self,
        *,
        session_id: str,
    ) -> ModelUsageCorrelationContext | None:
        correlation_context = self.model_usage_store.get_active_correlation_context(
            session_id=session_id,
            agent_harness=MODEL_USAGE_AGENT_HARNESS,
        )
        if correlation_context is None:
            correlation_context = self.model_usage_store.get_active_correlation_context(
                session_id=session_id,
            )
        return correlation_context

    def _record_model_usage_event(
        self,
        *,
        session_id: str,
        upstream_path: str,
        request_body: bytes | None,
        upstream_response: httpx.Response,
        duration_ms: int | None = None,
        provider_name: str = "openai",
        request_body_bytes: int | None = None,
        request_body_sha256: str | None = None,
        request_model: str | None = None,
    ) -> None:
        if not self._is_model_usage_endpoint(upstream_path):
            return

        response_payload = self._json_payload_from_response(upstream_response)
        response_body = upstream_response.content
        self._record_model_usage_payload(
            session_id=session_id,
            upstream_path=upstream_path,
            request_status=self._request_status_for_response(upstream_response.status_code),
            request_body=request_body,
            response_body_bytes=len(response_body) if response_body else None,
            response_body_sha256=sha256(response_body).hexdigest() if response_body else None,
            response_payload=response_payload,
            request_metadata={
                "method": "POST",
                "path": f"/v1/{upstream_path}",
            },
            response_metadata=self._response_metadata_with_provider_request_id(
                {
                    "status_code": upstream_response.status_code,
                    "content_type": upstream_response.headers.get("content-type"),
                },
                provider_request_id=self._provider_request_id_from_response(
                    upstream_response=upstream_response,
                    response_payload=response_payload,
                ),
            ),
            duration_ms=duration_ms,
            provider_name=provider_name,
            request_body_bytes=request_body_bytes,
            request_body_sha256=request_body_sha256,
            request_model=request_model,
        )

    def _record_stream_model_usage_event(
        self,
        *,
        session_id: str,
        upstream_path: str,
        request_body: bytes | None,
        upstream_response: httpx.Response,
        response_body_bytes: int,
        response_body_sha256: str | None,
        response_payload: dict[str, Any],
        duration_ms: int | None,
        provider_name: str = "openai",
        request_body_bytes: int | None = None,
        request_body_sha256: str | None = None,
        request_model: str | None = None,
    ) -> None:
        if not self._is_model_usage_endpoint(upstream_path):
            return

        self._record_model_usage_payload(
            session_id=session_id,
            upstream_path=upstream_path,
            request_status=self._request_status_for_response(upstream_response.status_code),
            request_body=request_body,
            response_body_bytes=response_body_bytes or None,
            response_body_sha256=response_body_sha256,
            response_payload=response_payload,
            request_metadata={
                "method": "POST",
                "path": f"/v1/{upstream_path}",
                "stream": True,
            },
            response_metadata=self._response_metadata_with_provider_request_id(
                {
                    "status_code": upstream_response.status_code,
                    "content_type": upstream_response.headers.get("content-type"),
                    "stream": True,
                },
                provider_request_id=self._provider_request_id_from_response(
                    upstream_response=upstream_response,
                    response_payload=response_payload,
                ),
            ),
            duration_ms=duration_ms,
            provider_name=provider_name,
            request_body_bytes=request_body_bytes,
            request_body_sha256=request_body_sha256,
            request_model=request_model,
        )

    def _record_websocket_model_usage_event(
        self,
        *,
        session_id: str,
        upstream_path: str,
        message: str | bytes,
        response_payload: dict[str, Any],
        event_type: str | None,
        duration_ms: int | None,
        provider_name: str = "openai",
    ) -> None:
        if not self._is_model_usage_endpoint(upstream_path):
            return

        if isinstance(message, bytes):
            message_body = message
        else:
            message_body = message.encode("utf-8")
        self._record_model_usage_payload(
            session_id=session_id,
            upstream_path=upstream_path,
            request_status="success",
            request_body=None,
            response_body_bytes=len(message_body) if message_body else None,
            response_body_sha256=sha256(message_body).hexdigest() if message_body else None,
            response_payload=response_payload,
            request_metadata={
                "method": "WEBSOCKET",
                "path": f"/v1/{upstream_path}",
                "websocket": True,
            },
            response_metadata=self._response_metadata_with_provider_request_id(
                {
                    "websocket": True,
                    "event_type": event_type,
                    "message_bytes": len(message_body),
                },
                provider_request_id=self._provider_request_id_from_payload(response_payload),
            ),
            duration_ms=duration_ms,
            provider_name=provider_name,
        )

    def _record_model_usage_payload(
        self,
        *,
        session_id: str,
        upstream_path: str,
        request_status: str,
        request_body: bytes | None,
        response_body_bytes: int | None,
        response_body_sha256: str | None,
        response_payload: dict[str, Any],
        request_metadata: dict[str, Any],
        response_metadata: dict[str, Any],
        duration_ms: int | None,
        provider_name: str = "openai",
        request_body_bytes: int | None = None,
        request_body_sha256: str | None = None,
        request_model: str | None = None,
    ) -> None:
        if not self._is_model_usage_endpoint(upstream_path):
            return

        correlation_context = self.model_usage_store.get_active_correlation_context(
            session_id=session_id,
            agent_harness=MODEL_USAGE_AGENT_HARNESS,
        )
        if correlation_context is None:
            correlation_context = self.model_usage_store.get_active_correlation_context(
                session_id=session_id,
            )
        resolved_request_body_bytes = (
            request_body_bytes
            if request_body_bytes is not None
            else len(request_body)
            if request_body
            else None
        )
        resolved_request_body_sha256 = (
            request_body_sha256
            if request_body_sha256 is not None
            else sha256(request_body).hexdigest()
            if request_body
            else None
        )
        extractor = MappingModelUsageExtractor(provider_name=provider_name)
        result = extractor.extract(
            ModelUsageExtractionRequest(
                provider=provider_name,
                provider_endpoint_identifier=upstream_path,
                request_status=request_status,
                model=(
                    response_payload.get("model")
                    or request_model
                    or self._model_from_request_body(request_body)
                ),
                duration_ms=duration_ms,
                request_body_bytes=resolved_request_body_bytes,
                request_body_sha256=resolved_request_body_sha256,
                response_body_bytes=response_body_bytes,
                response_body_sha256=response_body_sha256,
                response_payload=response_payload,
                request_metadata=request_metadata,
                response_metadata=response_metadata,
                correlation_context=correlation_context,
                request_id=self._provider_request_id_from_metadata(
                    response_metadata=response_metadata,
                    response_payload=response_payload,
                ),
            )
        )
        self.model_usage_store.upsert_event(result.event)

    @classmethod
    def _provider_request_id_from_response(
        cls,
        *,
        upstream_response: httpx.Response,
        response_payload: dict[str, Any],
    ) -> str | None:
        for header_name in (
            "request-id",
            "x-request-id",
            "openai-request-id",
            "anthropic-request-id",
        ):
            value = upstream_response.headers.get(header_name)
            if value:
                text = value.strip()
                if text:
                    return text
        return cls._provider_request_id_from_payload(response_payload)

    @staticmethod
    def _response_metadata_with_provider_request_id(
        response_metadata: dict[str, Any], *, provider_request_id: str | None
    ) -> dict[str, Any]:
        if provider_request_id is not None:
            response_metadata["provider_request_id"] = provider_request_id
        return response_metadata

    @staticmethod
    def _provider_request_id_from_payload(response_payload: dict[str, Any]) -> str | None:
        for key in (
            "request_id",
            "requestId",
            "provider_request_id",
            "providerRequestId",
        ):
            value = response_payload.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None

    @classmethod
    def _provider_request_id_from_metadata(
        cls,
        *,
        response_metadata: dict[str, Any],
        response_payload: dict[str, Any],
    ) -> str | None:
        value = response_metadata.get("provider_request_id")
        if value is not None:
            text = str(value).strip()
            if text:
                return text
        return cls._provider_request_id_from_payload(response_payload)

    @classmethod
    def _capture_stream_usage_payload(
        cls, *, buffer: str, chunk: bytes, payload: dict[str, Any]
    ) -> str:
        try:
            buffer += chunk.decode("utf-8")
        except UnicodeDecodeError:
            buffer += chunk.decode("utf-8", errors="ignore")
        while "\n\n" in buffer:
            event_text, buffer = buffer.split("\n\n", 1)
            cls._merge_sse_event_payload(event_text, payload)
        return buffer

    @classmethod
    def _merge_sse_event_payload(cls, event_text: str, payload: dict[str, Any]) -> None:
        for raw_line in event_text.splitlines():
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data = line.removeprefix("data:").strip()
            if not data or data == "[DONE]":
                continue
            try:
                event_payload = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(event_payload, dict):
                cls._merge_stream_event_dict(event_payload, payload)

    @staticmethod
    def _merge_stream_event_dict(event_payload: dict[str, Any], payload: dict[str, Any]) -> None:
        candidate = event_payload.get("response")
        if not isinstance(candidate, dict):
            candidate = event_payload.get("message")
        if not isinstance(candidate, dict):
            candidate = event_payload
        for key in (
            "id",
            "model",
            "deployment",
            "request_id",
            "requestId",
            "provider_request_id",
            "providerRequestId",
        ):
            value = candidate.get(key)
            if value is not None:
                payload[key] = value
        event_type = candidate.get("type") or event_payload.get("type")
        if event_type is not None:
            payload["type"] = event_type
        for usage in (candidate.get("usage"), event_payload.get("usage")):
            if isinstance(usage, dict):
                existing_usage = payload.get("usage")
                merged_usage = dict(existing_usage) if isinstance(existing_usage, dict) else {}
                merged_usage.update(usage)
                payload["usage"] = merged_usage

    @staticmethod
    def _is_model_usage_endpoint(upstream_path: str) -> bool:
        normalized = upstream_path.lstrip("/")
        return normalized in {
            "responses",
            "responses/compact",
            "chat/completions",
            "completions",
            "messages",
            "images/generations",
            "images/edits",
        }
