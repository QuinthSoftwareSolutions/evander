from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from evander.bootstrap import load_runtime_gateway_config
from evander.config import GatewayConfig, TeamsIngestWorkerGlobalConfig
from evander.messaging.teams_inbox_store import (
    TeamsInboxStore,
    TeamsNotificationQueueEntry,
)
from evander.messaging.teams_ingest import _load_teams_ingest_worker_global_config

logger = logging.getLogger(__name__)
_MAX_NOTIFICATION_REQUEST_BYTES = 256 * 1024
_CLIENT_STATE_HASH_LENGTH = 64
_LIFECYCLE_EVENTS = {"missed", "reauthorizationRequired", "subscriptionRemoved"}


class NotificationRequestTooLarge(ValueError):
    pass


class TeamsNotificationsService:
    def __init__(
        self,
        config: GatewayConfig,
        *,
        project_dir: Path,
        store: TeamsInboxStore | None = None,
        max_request_bytes: int = _MAX_NOTIFICATION_REQUEST_BYTES,
    ) -> None:
        self.config = config
        self.project_dir = Path(project_dir)
        self.store = store or TeamsInboxStore(config.runtime_dir / "teams-inbox-store.db")
        self.owner_id = self.store.notification_owner_id()
        self.max_request_bytes = max(1, int(max_request_bytes))

    def worker_config(self) -> TeamsIngestWorkerGlobalConfig:
        return _load_teams_ingest_worker_global_config(
            self.project_dir,
            base_config=self.config,
        )

    def create_app(self) -> Starlette:
        service = self

        async def health(_: Request) -> JSONResponse:
            worker_config = service.worker_config()
            return JSONResponse(
                {
                    "status": "ok",
                    "listener": "loopback",
                    "notifications": service.store.notification_status(
                        enabled=worker_config.change_notifications_enabled,
                        renewal_margin=timedelta(
                            hours=worker_config.change_notification_renewal_margin_hours
                        ),
                    ),
                }
            )

        async def messages(request: Request) -> Response:
            return await service._accept(request, notification_kind="message")

        async def lifecycle(request: Request) -> Response:
            return await service._accept(request, notification_kind="lifecycle")

        return Starlette(
            debug=False,
            routes=[
                Route("/healthz", health, methods=["GET"]),
                Route("/msgraph/teams/notifications", messages, methods=["POST"]),
                Route("/msgraph/teams/lifecycle", lifecycle, methods=["POST"]),
            ],
        )

    async def _accept(self, request: Request, *, notification_kind: str) -> Response:
        validation_token = request.query_params.get("validationToken")
        if validation_token is not None:
            return PlainTextResponse(validation_token, status_code=200)

        try:
            raw_body = await self._bounded_body(request)
        except NotificationRequestTooLarge:
            return JSONResponse({"error": "request body too large"}, status_code=413)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        try:
            payload = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JSONResponse({"error": "request body must be valid JSON"}, status_code=400)
        if not isinstance(payload, dict) or not isinstance(payload.get("value"), list):
            return JSONResponse(
                {"error": "request body must contain a notification value array"},
                status_code=400,
            )

        worker_config = self.worker_config()
        accepted = 0
        if worker_config.change_notifications_enabled:
            for item in payload["value"]:
                if not isinstance(item, dict):
                    continue
                entry = self._validated_queue_entry(item, notification_kind=notification_kind)
                if entry is not None and self.store.enqueue_notification(entry):
                    accepted += 1
        return JSONResponse({"status": "accepted", "accepted": accepted}, status_code=202)

    async def _bounded_body(self, request: Request) -> bytes:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                parsed_content_length = int(content_length)
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if parsed_content_length > self.max_request_bytes:
                raise NotificationRequestTooLarge
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > self.max_request_bytes:
                raise NotificationRequestTooLarge
        return bytes(body)

    def _validated_queue_entry(
        self,
        item: dict[str, Any],
        *,
        notification_kind: str,
    ) -> TeamsNotificationQueueEntry | None:
        subscription_id = str(item.get("subscriptionId") or "").strip()
        tenant_id = str(item.get("tenantId") or "").strip()
        client_state = str(item.get("clientState") or "")
        resource = str(item.get("resource") or "").strip()
        subscription = self.store.get_owned_subscription(subscription_id)
        received_hash = hashlib.sha256(client_state.encode("utf-8")).hexdigest()
        expected_hash = (
            subscription.client_state_sha256
            if subscription is not None
            else "0" * _CLIENT_STATE_HASH_LENGTH
        )
        client_state_valid = hmac.compare_digest(received_hash, expected_hash)
        if (
            subscription is None
            or subscription.owner_id != self.owner_id
            or not tenant_id
            or tenant_id != subscription.tenant_id
            or not client_state_valid
        ):
            logger.warning(
                "Ignored invalid Teams Graph notification kind=%s known_subscription=%s",
                notification_kind,
                subscription is not None,
            )
            return None

        change_type = str(item.get("changeType") or "").strip() or None
        lifecycle_event = str(item.get("lifecycleEvent") or "").strip() or None
        if notification_kind == "message":
            if change_type != "created" or not resource:
                return None
        elif lifecycle_event not in _LIFECYCLE_EVENTS:
            return None

        received_at = datetime.now(UTC)
        idempotency_payload = {
            "notification_id": str(item.get("id") or "").strip(),
            "kind": notification_kind,
            "subscription_id": subscription_id,
            "tenant_id": tenant_id,
            "resource": resource,
            "change_type": change_type,
            "lifecycle_event": lifecycle_event,
            "subscription_expiration": str(
                item.get("subscriptionExpirationDateTime") or ""
            ).strip(),
            "resource_id": (
                str((item.get("resourceData") or {}).get("id") or "").strip()
                if isinstance(item.get("resourceData"), dict)
                else ""
            ),
        }
        idempotency_key = hashlib.sha256(
            json.dumps(
                idempotency_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return TeamsNotificationQueueEntry(
            idempotency_key=idempotency_key,
            notification_kind=notification_kind,
            subscription_id=subscription_id,
            tenant_id=tenant_id,
            resource=resource or subscription.resource,
            change_type=change_type,
            lifecycle_event=lifecycle_event,
            received_at=received_at,
            next_attempt_at=received_at,
        )


def create_app(
    config: GatewayConfig,
    *,
    project_dir: Path,
    store: TeamsInboxStore | None = None,
    max_request_bytes: int = _MAX_NOTIFICATION_REQUEST_BYTES,
) -> Starlette:
    return TeamsNotificationsService(
        config,
        project_dir=project_dir,
        store=store,
        max_request_bytes=max_request_bytes,
    ).create_app()


def _validate_loopback_listener(host: str) -> None:
    normalized = str(host or "").strip()
    if normalized.casefold() == "localhost":
        return
    try:
        is_loopback = ip_address(normalized).is_loopback
    except ValueError as exc:
        raise SystemExit("Teams notifications listener host must be a loopback address") from exc
    if not is_loopback:
        raise SystemExit("Teams notifications listener host must be a loopback address")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Teams Graph notifications listener.")
    parser.add_argument("--runtime-dir", default="runtime")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging_level = getattr(logging, str(args.log_level).upper(), None)
    if not isinstance(logging_level, int):
        raise SystemExit(f"Invalid --log-level value: {args.log_level}")
    logging.basicConfig(
        level=logging_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    runtime_dir = Path(args.runtime_dir).expanduser().resolve()
    config = load_runtime_gateway_config(runtime_dir=runtime_dir)
    project_dir = runtime_dir.parent
    worker_config = _load_teams_ingest_worker_global_config(project_dir, base_config=config)
    _validate_loopback_listener(worker_config.change_notification_listener_host)
    uvicorn.run(
        create_app(config, project_dir=project_dir),
        host=worker_config.change_notification_listener_host,
        port=worker_config.change_notification_listener_port,
    )


if __name__ == "__main__":
    main()
