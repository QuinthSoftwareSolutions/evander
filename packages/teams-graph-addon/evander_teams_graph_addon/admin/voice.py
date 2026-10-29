from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from evander.admin.services.voice_visual_service import VoiceVisualService
from evander.config import TeamsCallRoute
from evander.models import ToolName
from evander.policy import PolicyError
from evander.voice_sessions import VoiceProjectionService, realtime_session_config
from evander.voice_sessions import VoiceQueryDelegator
from evander.voice_visuals import VisualQuotaExceeded, snapshot_fingerprint, validate_snapshots

logger = logging.getLogger(__name__)


class InboundTeamsCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str
    caller_user_id: str
    graph_call_id: str = Field(min_length=1, max_length=256)

    @field_validator("tenant_id", "caller_user_id", mode="before")
    @classmethod
    def normalize_identity(cls, value: object) -> str:
        return str(UUID(str(value).strip()))


class TeamsVoiceService(VoiceVisualService):
    """Admission and supervision for calls bound to explicitly selected assistants."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    def admit(self, payload: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        call = InboundTeamsCall.model_validate(payload)
        store = self.runtime.voice_session_store
        request_id = f"teams-call:{call.tenant_id}:{call.graph_call_id}"
        existing = store.get_by_request_id(request_id)
        if existing is not None:
            if existing.caller_metadata.get("caller_user_id") != call.caller_user_id:
                raise ValueError("Call identity changed on retry")
            return self._admission_response(existing)
        root = self.runtime.provider_config.resolve_for_directory("root")
        if not root.teams_call_enabled:
            raise ValueError("Teams calling is disabled")
        route = next(
            (
                entry
                for entry in root.teams_call_routes
                if (entry.tenant_id, entry.caller_user_id) == (call.tenant_id, call.caller_user_id)
            ),
            None,
        )
        if route is None:
            raise ValueError("Caller has no Teams call route")
        session = self.runtime.policy.get_session(route.origin_session_id)
        if session.directory_path != route.directory_path:
            raise ValueError("Mapped assistant directory does not match the route")
        config = self.runtime.provider_config.resolve_for_directory(route.directory_path)
        if not config.teams_call_enabled:
            raise ValueError("Teams calling is disabled for the assistant directory")
        if config.voice_media_controller_token_env != root.voice_media_controller_token_env:
            raise ValueError("Teams calling requires the root voice controller credential")
        tenant = str(config.teams_delegated_tenant_id or "").strip()
        if tenant and tenant.lower() != call.tenant_id:
            raise ValueError("Teams provider tenant does not match the caller tenant")
        caller_target = self._authorize_caller(route, session)
        self.runtime.policy.require_tool(session.session_id, ToolName.SUBMIT_VOICE_QUERY_RESPONSE)
        if self.runtime.policy.approval_required_for_tool(
            session, ToolName.SUBMIT_VOICE_QUERY_RESPONSE
        ):
            raise ValueError("Voice query responses must be granted without approval")
        visuals = self._visual_capability(config, session)
        projection = VoiceProjectionService().build_projection(
            session=session,
            prompt="Talk with the caller in their language. Delegate substantive requests to your originating assistant.",
            channel="teams_call",
            metadata=call.model_dump(),
            tool_summary=self.runtime.policy.allowed_tool_summary_for_session(session),
        )
        if time.monotonic() - started >= 5:
            raise ValueError("Teams call admission timed out")
        record, created = store.prepare_session(
            origin_session_id=session.session_id,
            directory_path=route.directory_path,
            channel="teams_call",
            request_id=request_id,
            prompt_metadata={},
            caller_metadata={
                **call.model_dump(),
                "teams_user_target": caller_target,
                "route": route.model_dump(mode="json"),
                "visual_context": visuals,
            },
            projection=projection,
            max_active_sessions=root.teams_call_max_concurrency,
        )
        if created:
            record = store.attach_bridge_session(
                record.voice_session_id,
                bridge_session_id=record.voice_session_id,
                status="connecting",
                bridge_metadata={"graph_call_id": call.graph_call_id},
            )
        logger.info(
            "Teams call admitted voice_session_id=%s origin_session_id=%s duration=%.3f",
            record.voice_session_id,
            record.origin_session_id,
            time.monotonic() - started,
        )
        return self._admission_response(record)

    def _authorize_caller(self, route: TeamsCallRoute, session: Any) -> str:
        targets = self.runtime.policy_config.resolved_teams_user_targets_for_session(
            session, capability="write"
        )
        if f"user:{route.caller_user_id}" in {target.lower() for target in targets}:
            return f"user:{route.caller_user_id}"
        service = self.runtime.teams_service_for_session(session.session_id)
        resolve = getattr(service, "resolve_user_id_for_reference", None)
        if resolve is not None:
            for target in targets:
                user_id = resolve(target)
                if user_id and str(user_id).lower() == route.caller_user_id:
                    return target
        raise ValueError("Caller is not authorized for the mapped assistant")

    def _admission_response(self, record: Any) -> dict[str, Any]:
        config = self.runtime.provider_config.resolve_for_directory(record.directory_path)
        return {
            "voice_session_id": record.voice_session_id,
            "status": record.status,
            "projection": record.projection,
            "visual_context": record.caller_metadata.get(
                "visual_context",
                {"enabled": False, "reason": "Visual context was not enabled at admission"},
            ),
            "realtime": {
                **realtime_session_config(config),
                "voice": getattr(config, "openai_realtime_voice", "marin"),
                "transcription_model": getattr(
                    config, "openai_realtime_transcription_model", "gpt-live-transcribe"
                ),
            },
        }

    def _visual_capability(self, config: Any, session: Any) -> dict[str, Any]:
        if not config.teams_call_visual_context_enabled:
            return {"enabled": False, "reason": "Visual context is disabled for this assistant"}
        if not re.fullmatch(
            r"gpt-realtime(?:-2(?:\.1(?:-mini)?)?)?(?:-\d{4}-\d{2}-\d{2})?", config.openai_realtime_model
        ):
            return {
                "enabled": False,
                "reason": "Configured Realtime model is not enabled for image input",
            }
        try:
            if self.runtime.policy.approval_required_for_tool(
                session, ToolName.GET_VOICE_QUERY_VISUAL_CONTEXT
            ):
                raise ValueError("Visual inspection requires a grant without approval")
            self.runtime.policy.require_tool(
                session.session_id, ToolName.GET_VOICE_QUERY_VISUAL_CONTEXT
            )
        except (PolicyError, ValueError, PermissionError) as exc:
            return {"enabled": False, "reason": str(exc)}
        return {"enabled": True, "reason": None}

    def create_visual_query(
        self, record: Any, payload: dict[str, Any], images: list[tuple[dict[str, Any], bytes]]
    ) -> Any:
        if record.channel != "teams_call" or not record.caller_metadata.get(
            "visual_context", {}
        ).get("enabled"):
            raise ValueError("Visual context is not enabled for this admitted Teams call")
        request_id = str(payload.get("request_id") or "").strip()
        if not request_id or len(request_id) > 256:
            raise ValueError("A visual query requires a bounded request_id")
        snapshots = validate_snapshots(images)
        fingerprint = snapshot_fingerprint(snapshots)
        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("Voice query metadata must be an object")
        self.cleanup_visual_context()

        def prepare(query):
            if query.visual_context or query.visual_context_unavailable:
                return query
            if query.wake_request_id or query.status != "pending":
                raise ValueError("Cannot attach snapshots to a dispatched voice query")
            unavailable = None
            try:
                references = self.runtime.voice_visual_store.stage(query, snapshots)
            except VisualQuotaExceeded as exc:
                references = []
                unavailable = str(exc)
            return self.runtime.voice_session_store.attach_query_visual_context(
                query.query_id,
                references=references,
                fingerprint=fingerprint,
                unavailable=unavailable,
            )

        return VoiceQueryDelegator(
            store=self.runtime.voice_session_store,
            wake_store=self.runtime.wake_store,
        ).create_query_and_wake_origin(
            record.voice_session_id,
            question=str(payload.get("question") or ""),
            transcript_excerpt=str(payload.get("transcript_excerpt") or "").strip() or None,
            metadata={"reason": str(metadata.get("reason") or "")[:2048]},
            request_id=request_id,
            deadline_seconds=90,
            prepare_query=prepare,
            visual_request_fingerprint=fingerprint,
        )

    def supervise(self, *, directories: list[str] | None = None) -> tuple[int, int]:
        polled = errors = 0
        if not self.cleanup_visual_context():
            errors += 1
        for record in self.runtime.voice_session_store.list_sessions(channel="teams_call"):
            if directories and not any(
                record.directory_path == scope or record.directory_path.startswith(scope + "/")
                for scope in directories
            ):
                continue
            if record.status in {"ended", "failed"}:
                self.runtime.voice_session_reconciler.schedule_reconciliation(
                    record.voice_session_id
                )
                continue
            polled += 1
            try:
                payload = self.runtime.teams_meeting_bridge_client.get_call(record.voice_session_id)
                payload["last_poll_at"] = datetime.now(UTC).isoformat()
                updated = self.runtime.voice_session_store.update_from_bridge_payload(
                    record.voice_session_id, payload
                )
                if updated.status in {"ended", "failed"}:
                    self.runtime.voice_session_reconciler.schedule_reconciliation(
                        updated.voice_session_id
                    )
            except Exception as exc:
                errors += 1
                last_poll = record.bridge_metadata.get("last_poll_at")
                last_seen = datetime.fromisoformat(last_poll) if last_poll else record.created_at
                age = (datetime.now(UTC) - last_seen).total_seconds()
                missing = isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404
                if age >= (30 if missing else 90):
                    self.runtime.voice_session_store.update_from_bridge_payload(
                        record.voice_session_id,
                        {"status": "failed", "error": "Teams media bridge call lost"},
                    )
                    self.runtime.voice_session_reconciler.schedule_reconciliation(
                        record.voice_session_id
                    )
                logger.warning(
                    "Teams call supervision failed voice_session_id=%s error=%s",
                    record.voice_session_id,
                    type(exc).__name__,
                )
        return polled, errors
