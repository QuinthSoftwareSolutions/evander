from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from evander.admin.composition import AdminRuntimeComponents
from evander.events.models import materialize_node_definition
from evander.admin.web.shared import json_error
from evander.workflow.runtime_config import load_trigger_orchestrator_worker_global_config

_WEBHOOK_SIGNATURE_RE = re.compile(r"^sha256=([0-9a-fA-F]{64})$")
_WEBHOOK_TIMESTAMP_TOLERANCE = timedelta(minutes=5)


def workflow_webhook_routes(service: AdminRuntimeComponents) -> list[Route]:
    async def publish_webhook(request: Request) -> JSONResponse:
        slug = str(request.path_params.get("slug") or "").strip()
        if not slug:
            return json_error("Webhook slug is required", status_code=404)

        signature_header = request.headers.get("x-evander-signature-256")
        signature_value = str(signature_header or "").strip()
        signature_match = _WEBHOOK_SIGNATURE_RE.fullmatch(signature_value)
        if signature_match is None:
            return json_error("Webhook signature is invalid or missing", status_code=401)

        raw_body = await request.body()
        try:
            body = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON")
        if not isinstance(body, dict):
            return json_error("Request body must be a JSON object")

        try:
            worker_config = load_trigger_orchestrator_worker_global_config(
                service.project_dir,
                base_config=service.config,
            )
        except ValueError as exc:
            message = str(exc)
            if "Webhook trigger slugs must be globally unique across active config" in message:
                return json_error(message, status_code=409)
            return json_error(message, status_code=500)

        matching_triggers = [
            trigger
            for trigger in worker_config.triggers
            if str(trigger.type).strip() == "trigger:http:webhook"
            and str(getattr(trigger, "slug", "")).strip() == slug
        ]
        if not matching_triggers:
            return json_error(f"Webhook trigger not found for slug: {slug}", status_code=404)
        if len(matching_triggers) > 1:
            return json_error(f"Webhook trigger slug is duplicated: {slug}", status_code=409)
        trigger = materialize_node_definition(matching_triggers[0])

        secret_env = str(getattr(trigger, "secret_env", "") or "").strip()
        secret = os.environ.get(secret_env, "").strip() if secret_env else ""
        if not secret:
            return json_error(
                f"Webhook trigger secret env var {secret_env!r} is not set",
                status_code=500,
            )

        timestamp_header = str(request.headers.get("x-evander-timestamp") or "").strip()
        try:
            webhook_timestamp = _parse_webhook_timestamp(timestamp_header)
        except ValueError:
            return json_error("Webhook timestamp is invalid or stale", status_code=401)
        now = datetime.now(UTC)
        if abs(now - webhook_timestamp) > _WEBHOOK_TIMESTAMP_TOLERANCE:
            return json_error("Webhook timestamp is invalid or stale", status_code=401)

        query = _json_object_from_pairs(request.query_params.multi_items())
        signature_digest = signature_match.group(1).lower()
        expected_signature = hmac.new(
            secret.encode("utf-8"),
            _webhook_signature_material(timestamp_header, query, raw_body),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected_signature, signature_digest):
            return json_error("Webhook signature is invalid or missing", status_code=401)

        received_at = now.isoformat().replace("+00:00", "Z")
        envelope = {
            "slug": trigger.slug,
            "headers": _json_object_from_pairs(request.headers.items()),
            "query": query,
            "body": body,
            "received_at": received_at,
            "remote_addr": _remote_addr(request),
            "signature_header": signature_header or "",
            "timestamp_header": timestamp_header,
        }

        try:
            event = service.operations_admin.publish_external_workflow_event(
                event_type=trigger.event_type,
                payload=envelope,
                correlation_key=f"webhook:{trigger.slug}",
                idempotency_key=(
                    f"webhook:{trigger.id}:{trigger.slug}:sha256:{signature_digest}:"
                    f"query-sha256:{_json_sha256(query)}"
                ),
                source={
                    "source_type": "webhook",
                    "source_name": trigger.slug,
                    "node_id": trigger.id,
                    "node_type": "trigger:http:webhook",
                },
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=500)
        return JSONResponse(event, status_code=202)

    return [Route("/api/workflow-webhooks/{slug}", publish_webhook, methods=["POST"])]


def _json_object_from_pairs(items: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        normalized_key = str(key)
        if normalized_key in result:
            existing = result[normalized_key]
            if isinstance(existing, list):
                existing.append(value)
            else:
                result[normalized_key] = [existing, value]
            continue
        result[normalized_key] = value
    return result


def _json_sha256(value: Any) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _webhook_signature_material(timestamp_header: str, query: dict[str, Any], body: bytes) -> bytes:
    query_material = json.dumps(query, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return timestamp_header.encode("ascii") + b"." + query_material.encode("utf-8") + b"." + body


def _parse_webhook_timestamp(value: str) -> datetime:
    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError("webhook timestamp must be epoch seconds")
    try:
        return datetime.fromtimestamp(int(value), UTC)
    except (OverflowError, OSError) as exc:
        raise ValueError("webhook timestamp is out of range") from exc


def _remote_addr(request: Request) -> str:
    client = request.client
    if client is None:
        return ""
    return str(client.host or "")
