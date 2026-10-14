from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx

from evander.directories import DirectoryService
from evander.models import SessionManifest, ToolName
from evander.voice_sessions import (
    VoiceProjectionService,
    realtime_session_config,
    sanitize_voice_metadata,
)


from evander.teams_meeting import (
    TeamsMeetingBridgeEvent,
    TeamsMeetingBinding,
    TeamsMeetingStartResult,
    TeamsMeetingStore,
    voice_session_transcript_from_teams_events,
    _normalize_base_url,
    _json_object_response,
    _sha256,
    _redact_bridge_response,
)


class TeamsMeetingBridgeClient:
    def __init__(
        self,
        *,
        base_url: str | None,
        token_env: str | None,
        timeout_seconds: float,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = _normalize_base_url(base_url)
        self.token_env = str(token_env or "").strip() or None
        self.timeout_seconds = timeout_seconds
        self._http_client = http_client

    def join(
        self,
        *,
        join_url: str,
        external_meeting_id: str,
        default_voice: str | None = None,
        metadata: dict[str, Any] | None = None,
        realtime: dict[str, Any] | None = None,
        projection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "join_url": join_url,
            "external_meeting_id": external_meeting_id,
            "default_voice": default_voice,
            "metadata": dict(metadata or {}),
        }
        if realtime is not None:
            payload["realtime"] = sanitize_voice_metadata(realtime)
        if projection is not None:
            payload["projection"] = sanitize_voice_metadata(projection)
        return self._request("POST", "/meetings/join", json=payload)

    def get_events(
        self, *, meeting_id: str, after_sequence: int = 0
    ) -> list[TeamsMeetingBridgeEvent]:
        payload = self._request(
            "GET",
            f"/meetings/{quote(meeting_id, safe='')}/events",
            params={"after": after_sequence},
        )
        raw_events = payload.get("events") if isinstance(payload, dict) else payload
        if not isinstance(raw_events, list):
            raise ValueError("media bridge events response must contain an events list")
        return [
            TeamsMeetingBridgeEvent.model_validate(raw_event)
            for raw_event in raw_events
            if isinstance(raw_event, dict)
        ]

    def speak(
        self,
        *,
        meeting_id: str,
        text: str,
        voice: str | None,
        interrupt: bool,
    ) -> dict[str, Any]:
        payload = {"text": text, "voice": voice, "interrupt": interrupt}
        return self._request(
            "POST",
            f"/meetings/{quote(meeting_id, safe='')}/speak",
            json=payload,
        )

    def leave(self, *, meeting_id: str) -> dict[str, Any]:
        return self._request("POST", f"/meetings/{quote(meeting_id, safe='')}/leave")

    def get_call(self, voice_session_id: str) -> dict[str, Any]:
        return self._request("GET", f"/calls/{quote(voice_session_id, safe='')}")

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if self.base_url is None:
            raise RuntimeError("teams_meeting_media_bridge_url must be configured")
        headers = dict(kwargs.pop("headers", {}) or {})
        if self.token_env is not None:
            token = os.environ.get(self.token_env, "").strip()
            if not token:
                raise RuntimeError(
                    f"Teams meeting media bridge token env var {self.token_env!r} is not set"
                )
            headers["Authorization"] = f"Bearer {token}"
        url = f"{self.base_url}{path}"
        client = self._http_client
        if client is not None:
            response = client.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            return _json_object_response(response)
        with httpx.Client(timeout=self.timeout_seconds) as scoped_client:
            response = scoped_client.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            return _json_object_response(response)


class TeamsMeetingService:
    def __init__(
        self,
        *,
        config: Any,
        runtime: Any,
        store: TeamsMeetingStore,
        bridge_client: TeamsMeetingBridgeClient,
    ) -> None:
        self.config = config
        self.runtime = runtime
        self.store = store
        self.bridge_client = bridge_client

    def start_meeting(
        self,
        *,
        directory_path: str,
        join_url: str,
        model: str | None,
        session_id: str | None = None,
        ttl_hours: int | None = None,
        origin_session_id: str | None = None,
        prompt: str | None = None,
    ) -> TeamsMeetingStartResult:
        normalized_join_url = str(join_url or "").strip()
        if not normalized_join_url:
            raise ValueError("join_url is required")
        max_active = int(getattr(self.config, "teams_meeting_max_active_meetings", 1))
        active_bindings = self.store.list_active_bindings()
        if len(active_bindings) >= max_active:
            active_ids = ", ".join(binding.meeting_id for binding in active_bindings)
            raise ValueError(
                f"Teams meeting MVP allows {max_active} active meeting(s); active={active_ids}"
            )

        directory_service = DirectoryService(self.runtime.directories)
        normalized_directory = directory_service.show(directory_path or "root").directory.path
        policy = self.runtime.policy_config.resolve_for_directory(normalized_directory)
        resolved_session_id = (
            str(session_id or "").strip()
            or f"teams-meeting-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
        )
        if self.runtime.session_repository.get_session(resolved_session_id) is not None:
            raise ValueError(f"Session already exists: {resolved_session_id}")

        external_meeting_id = f"meeting-{uuid4().hex}"
        default_voice = str(getattr(self.config, "teams_meeting_default_voice", "") or "").strip()
        origin_session = self._resolve_origin_session(origin_session_id)
        voice_record = None
        voice_projection: dict[str, Any] | None = None
        if origin_session is not None:
            voice_prompt = (
                str(prompt or "").strip()
                or "Join this Teams meeting as a projected live voice session for the originating Evander agent."
            )
            tool_summary = None
            tool_policy = getattr(self.runtime, "policy", None)
            if tool_policy is not None:
                try:
                    tool_summary = tool_policy.allowed_tool_summary_for_session(origin_session)
                except Exception:
                    tool_summary = None
            voice_projection = VoiceProjectionService().build_projection(
                session=origin_session,
                prompt=voice_prompt,
                channel="teams_meeting",
                metadata={
                    "meeting_session_id": resolved_session_id,
                    "join_url_sha256": _sha256(normalized_join_url),
                },
                tool_summary=tool_summary,
            )
            voice_record, _created = self.runtime.voice_session_store.prepare_session(
                origin_session_id=origin_session.session_id,
                directory_path=origin_session.directory_path,
                channel="teams_meeting",
                request_id=f"teams-meeting:{resolved_session_id}",
                prompt_metadata={
                    "sha256": _sha256(voice_prompt),
                    "length": len(voice_prompt),
                },
                projection=voice_projection,
                caller_metadata={
                    "meeting_session_id": resolved_session_id,
                    "join_url_sha256": _sha256(normalized_join_url),
                },
            )
        bridge_metadata = {
            "session_id": resolved_session_id,
            "directory_path": normalized_directory,
        }
        if origin_session is not None and voice_record is not None:
            bridge_metadata.update(
                {
                    "origin_session_id": origin_session.session_id,
                    "voice_session_id": voice_record.voice_session_id,
                }
            )
        try:
            bridge_response = self.bridge_client.join(
                join_url=normalized_join_url,
                external_meeting_id=external_meeting_id,
                default_voice=default_voice or None,
                metadata=bridge_metadata,
                realtime=realtime_session_config(self.config) if voice_record is not None else None,
                projection=voice_projection,
            )
        except Exception as exc:
            if voice_record is not None:
                self.runtime.voice_session_store.complete_session(
                    voice_record.voice_session_id,
                    status="failed",
                    error=str(exc),
                )
            raise
        meeting_id = str(bridge_response.get("meeting_id") or external_meeting_id).strip()
        if not meeting_id:
            raise ValueError("media bridge join response did not include a meeting_id")
        if voice_record is not None:
            voice_record = self.runtime.voice_session_store.attach_bridge_session(
                voice_record.voice_session_id,
                bridge_session_id=meeting_id,
                status=str(bridge_response.get("status") or "active"),
                bridge_metadata=_redact_bridge_response(bridge_response),
                realtime_metadata=bridge_response.get("realtime")
                if isinstance(bridge_response.get("realtime"), dict)
                else {},
                meeting_id=meeting_id,
            )

        ttl_seconds = (
            int(ttl_hours) * 3600
            if ttl_hours is not None
            else int(policy.default_session_ttl_seconds)
        )
        manifest = SessionManifest(
            session_id=resolved_session_id,
            directory_path=normalized_directory,
            allowed_tools=[
                ToolName.TEAMS_MEETING_GET_EVENTS,
                ToolName.TEAMS_MEETING_SPEAK,
                ToolName.TEAMS_MEETING_STOP,
            ],
            email_mailbox_allowlist=policy.email_mailbox_allowlist,
            email_folder_allowlist_by_mailbox=policy.email_folder_allowlist_by_mailbox,
            email_draft_recipient_allowlist=policy.email_draft_recipient_allowlist,
            email_draft_domain_allowlist=policy.email_draft_domain_allowlist,
            email_send_enabled=policy.email_send_enabled,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
            model=str(model or "").strip() or None,
            metadata={
                "teams_meeting": {
                    "meeting_id": meeting_id,
                    "status": "active",
                    "started_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "join_url_sha256": _sha256(normalized_join_url),
                    **(
                        {
                            "origin_session_id": origin_session.session_id,
                            "voice_session_id": voice_record.voice_session_id,
                        }
                        if origin_session is not None and voice_record is not None
                        else {}
                    ),
                }
            },
        )
        binding = TeamsMeetingBinding(
            meeting_id=meeting_id,
            session_id=resolved_session_id,
            directory_path=normalized_directory,
            join_url_sha256=_sha256(normalized_join_url),
            default_voice=default_voice or None,
            metadata={
                "bridge": _redact_bridge_response(bridge_response),
                **(
                    {
                        "origin_session_id": origin_session.session_id,
                        "voice_session_id": voice_record.voice_session_id,
                    }
                    if origin_session is not None and voice_record is not None
                    else {}
                ),
            },
        )
        self.runtime.session_repository.upsert_session(manifest)
        self.store.upsert_binding(binding)
        self.store.append_local_event(
            meeting_id=meeting_id,
            event_type="meeting_status",
            speech_status="joined",
            metadata={"bridge": _redact_bridge_response(bridge_response)},
        )
        return TeamsMeetingStartResult(
            meeting_id=meeting_id,
            session_id=resolved_session_id,
            directory_path=normalized_directory,
            status="active",
            model=manifest.model,
            bridge=_redact_bridge_response(bridge_response),
        )

    def stop_meeting(self, *, session_id: str) -> dict[str, Any]:
        binding = self.store.get_binding_for_session(session_id)
        if binding is None:
            raise ValueError(f"Unknown Teams meeting session: {session_id}")
        response = self.bridge_client.leave(meeting_id=binding.meeting_id)
        self.store.close_binding(binding.meeting_id, status="stopped")
        self.store.append_local_event(
            meeting_id=binding.meeting_id,
            event_type="meeting_status",
            speech_status="stopped",
            metadata={"bridge": _redact_bridge_response(response)},
        )
        self._complete_bound_voice_session(binding)
        manifest = self.runtime.session_repository.get_session(session_id)
        if manifest is not None:
            metadata = dict(manifest.metadata or {})
            teams_metadata = dict(metadata.get("teams_meeting") or {})
            teams_metadata["status"] = "stopped"
            teams_metadata["stopped_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            metadata["teams_meeting"] = teams_metadata
            self.runtime.session_repository.upsert_session(
                manifest.model_copy(update={"metadata": metadata})
            )
        return {"status": "stopped", "meeting_id": binding.meeting_id, "bridge": response}

    def _resolve_origin_session(self, origin_session_id: str | None) -> SessionManifest | None:
        normalized = str(origin_session_id or "").strip()
        if not normalized:
            return None
        session = self.runtime.session_repository.get_session(normalized)
        if session is None:
            raise ValueError(f"Origin session does not exist: {normalized}")
        return session

    def _complete_bound_voice_session(self, binding: TeamsMeetingBinding) -> None:
        voice_session_id = str(binding.metadata.get("voice_session_id") or "").strip()
        if not voice_session_id or not hasattr(self.runtime, "voice_session_store"):
            return
        events = self.store.list_events_for_session(
            binding.session_id, since_sequence=0, limit=10_000
        )
        transcript = voice_session_transcript_from_teams_events(events)
        self.runtime.voice_session_store.complete_session(
            voice_session_id,
            status="ended",
            transcript=transcript,
            summary="Teams meeting ended; review the transcript for decisions and follow-up.",
        )


def build_teams_meeting_bridge_client(config: Any) -> TeamsMeetingBridgeClient:
    return TeamsMeetingBridgeClient(
        base_url=getattr(config, "teams_meeting_media_bridge_url", None),
        token_env=getattr(config, "teams_meeting_media_bridge_token_env", None),
        timeout_seconds=float(getattr(config, "teams_meeting_request_timeout_seconds", 15.0)),
    )
