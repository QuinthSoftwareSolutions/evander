"""Per-call realtime output, playback, caller turns, and timer lifecycle."""

from __future__ import annotations
import asyncio
import base64
import inspect
from dataclasses import dataclass
from typing import Any

from typing import TYPE_CHECKING
from .didww_bridge_protocol import (
    BridgeResponseRequest,
    OriginQueryState,
    OutboundAudioFrame,
    RealtimeFunctionCall,
    _CALLER_TURN_COMPLETION_INSTRUCTIONS,
    _CALLER_TURN_COMPLETION_RESPONSE_KEY_PREFIX,
    _END_CALL_TOOL_NAME,
    _IDLE_PROMPT_INSTRUCTIONS,
    _IDLE_PROMPT_RESPONSE_KEY,
    _INTERRUPTED_ASSISTANT_RESUME_INSTRUCTIONS,
    _ORIGIN_QUERY_IDLE_PROMPT_INSTRUCTIONS,
    _build_barge_in_fade_payload,
    _debug_snippet,
    _instruction_debug_metadata,
    _interrupted_assistant_resume_response_key,
    _is_backchannel_transcript,
    _is_goodbye_transcript,
    _is_interrupted_assistant_resume_response_key,
    _is_origin_query_hold_ack,
    _is_origin_query_response_key,
    _realtime_audio_delta,
    _realtime_audio_done,
    _realtime_audio_key,
    _realtime_audio_metadata,
    _realtime_content_index,
    _realtime_error_metadata,
    _realtime_function_call,
    _realtime_item_id,
    _realtime_lifecycle_metadata,
    _realtime_response_id,
    _realtime_text_delta,
    _realtime_transcript_metadata,
    _realtime_transcript_text,
    _realtime_vad_event,
    _realtime_vad_metadata,
    _rtp_bytes_per_sample,
    _word_count,
)

if TYPE_CHECKING:
    from .didww_sip_bridge import (
        DidwwSipBridgeService,
        RtpUdpEndpoint,
        OpenAIRealtimeSession,
        VoiceMediaControllerClient,
    )


@dataclass(frozen=True)
class RealtimeMediaTiming:
    """Timing policy captured at the start of one voice media session."""

    assistant_playback_drain_seconds: float
    audio_quality_frame_gap_tolerance: float
    bridge_idle_prompt_delay_seconds: float
    bridge_idle_prompt_max_per_call: int
    interrupted_assistant_no_transcript_recovery_grace_seconds: float
    interrupted_assistant_recovery_grace_seconds: float


class RealtimeRtpSession:
    """Own one call's playback, response, interruption, and timer state."""

    def __init__(self, service: DidwwSipBridgeService, timing: RealtimeMediaTiming) -> None:
        self.service = service
        self.timing = timing

    async def run(
        self,
        voice_session_id: str,
        rtp: RtpUdpEndpoint,
        realtime: OpenAIRealtimeSession,
        media_controller: VoiceMediaControllerClient,
        stop_event: asyncio.Event,
    ) -> None:
        self._initialize(voice_session_id, rtp, realtime, media_controller, stop_event)
        self.start_outbound_sender()
        try:
            while not self.stop_event.is_set():
                try:
                    event = await asyncio.wait_for(self.realtime.recv_json(), timeout=0.5)
                except TimeoutError:
                    continue
                await self._handle_event(event)
        finally:
            await self._close_background_tasks()

    def _initialize(
        self,
        voice_session_id: str,
        rtp: RtpUdpEndpoint,
        realtime: OpenAIRealtimeSession,
        media_controller: VoiceMediaControllerClient,
        stop_event: asyncio.Event,
    ) -> None:
        self.media_controller = media_controller
        self.voice_session_id = voice_session_id
        self.rtp = rtp
        self.realtime = realtime
        self.stop_event = stop_event
        self.summary_parts: list[str] = []
        self.query_tasks: set[asyncio.Task[None]] = set()
        self.delegated_queries: dict[str, str] = {}
        self.origin_queries_by_question: dict[str, OriginQueryState] = {}
        self.origin_queries_by_id: dict[str, OriginQueryState] = {}
        self.assistant_speaking = True
        self.response_active = True
        self.active_response_id: str | None = None
        self.last_caller_turn: str | None = None
        self.pending_responses: list[BridgeResponseRequest] = []
        self.pending_response_keys: set[str] = set()
        self.last_response_request: BridgeResponseRequest | None = None
        self.response_lock = asyncio.Lock()
        self.audio_response_started_at: dict[str, float] = {}
        self.audio_delta_counts: dict[str, int] = {}
        self.audio_response_finished: set[str] = set()
        self.audio_item_played_samples: dict[str, int] = {}
        self.audio_item_content_index: dict[str, int] = {}
        self.audio_item_response_id: dict[str, str] = {}
        self.truncated_audio_items: set[str] = set()
        self.outbound_audio_queue: asyncio.Queue[OutboundAudioFrame] = asyncio.Queue()
        self.outbound_sender_active = False
        self.outbound_sender_task: asyncio.Task[None] | None = None
        self.barge_fade_active = False
        self.barge_fade_task: asyncio.Task[None] | None = None
        self.last_played_audio_item_id: str | None = None
        self.last_assistant_audio_at: float | None = None
        self.current_caller_utterance_id = 0
        self.caller_speech_active = False
        self.caller_turn_incomplete = False
        self.caller_speech_started_at: float | None = None
        self.caller_speech_started_during_assistant = False
        self.barge_cancelled_utterance_id: int | None = None
        self.barge_cancelled_response_id: str | None = None
        self.barge_cancelled_audio_truncated = False
        self.barge_cancel_recovery_requested = False
        self.barge_cancel_recovery_task: asyncio.Task[None] | None = None
        self.interruption_recovery_cancel_requested_key: str | None = None
        self.barge_delay_tasks: set[asyncio.Task[None]] = set()
        self.end_call_requested = False
        self.end_call_completed = False
        self.end_call_task: asyncio.Task[None] | None = None
        self.end_call_call_id: str | None = None
        self.end_call_reason: str | None = None
        self.end_call_wait_logged = False
        self.idle_prompt_task: asyncio.Task[None] | None = None
        self.idle_prompt_sent_this_period = False
        self.idle_prompt_sent_count = 0
        self.idle_generation = 0
        self.goodbye_idle_task: asyncio.Task[None] | None = None
        self.goodbye_idle_pending = False
        self.goodbye_idle_wait_logged = False
        self.goodbye_idle_generation = 0
        self.loop = asyncio.get_running_loop()
        self.bytes_per_sample = _rtp_bytes_per_sample(self.service.config.media_format)

        self.rtp_send_supports_sample_callback = self.send_payload_accepts_sample_callback()

    async def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        if event_type in {"response.created", "response.done", "response.cancelled"}:
            self.service._append_debug_event(
                self.voice_session_id,
                event_type,
                metadata=_realtime_lifecycle_metadata(event),
            )
        if event_type == "response.created":
            await self.mark_response_created(event)
        if _realtime_vad_event(event):
            self.service._append_debug_event(
                self.voice_session_id,
                "realtime.vad",
                metadata=_realtime_vad_metadata(event),
            )
        if event_type == "input_audio_buffer.speech_started":
            await self.handle_speech_started(event)
        if event_type == "input_audio_buffer.speech_stopped":
            await self.handle_speech_stopped()
        function_call = _realtime_function_call(event)
        if function_call is not None:
            if function_call.name == _END_CALL_TOOL_NAME:
                await self.request_end_call(function_call)
                return
            await self.service._handle_realtime_function_call(
                self.voice_session_id,
                function_call,
                self.realtime,
                self.media_controller,
                self.query_tasks,
                self.stop_event,
                delegated_queries=self.delegated_queries,
                origin_queries_by_question=self.origin_queries_by_question,
                origin_queries_by_id=self.origin_queries_by_id,
                request_response=self.request_bridge_response,
                discard_response_keys=self.discard_bridge_response_keys,
                cancel_goodbye_idle_timer=self.cancel_goodbye_idle_timer,
            )
            return
        audio_delta = _realtime_audio_delta(event)
        if audio_delta:
            await self.mark_assistant_speaking()
            self.mark_audio_delta(event)
            try:
                decoded_audio_delta = base64.b64decode(audio_delta)
            except Exception as exc:
                tracker = self.service._audio_quality_trackers.get(self.voice_session_id)
                if tracker is not None and hasattr(tracker, "record_decode_error"):
                    tracker.record_decode_error(reason=str(exc))
                raise
            await self.outbound_audio_queue.put(
                OutboundAudioFrame(
                    payload=decoded_audio_delta,
                    response_id=_realtime_response_id(event),
                    item_id=_realtime_item_id(event),
                    content_index=_realtime_content_index(event),
                )
            )
            tracker = self.service._audio_quality_trackers.get(self.voice_session_id)
            if tracker is not None and hasattr(tracker, "record_queue_depth"):
                tracker.record_queue_depth(self.outbound_audio_queue.qsize())
            await asyncio.sleep(0)
            return
        if _realtime_audio_done(event):
            self.mark_audio_finished(event, reason=event_type)
        transcript_text = _realtime_transcript_text(event)
        if transcript_text:
            speaker = "assistant" if event_type.startswith("response.") else "caller"
            transcript_item_id = _realtime_item_id(event)
            if speaker == "assistant" and transcript_item_id in self.truncated_audio_items:
                self.service._append_debug_event(
                    self.voice_session_id,
                    "assistant_transcript.truncated_ignored",
                    metadata=_realtime_transcript_metadata(event, transcript_text),
                )
            else:
                self.service.store.append_transcript_event(
                    self.voice_session_id,
                    speaker=speaker,
                    text=transcript_text,
                    metadata={"event_type": event_type},
                )
                self.service._append_debug_event(
                    self.voice_session_id,
                    (
                        "assistant_transcript.completed"
                        if speaker == "assistant"
                        else "caller_transcript.completed"
                    ),
                    metadata=_realtime_transcript_metadata(event, transcript_text),
                )
                if speaker == "assistant" and _is_goodbye_transcript(transcript_text):
                    await self.cancel_idle_prompt(
                        reason="assistant_goodbye",
                        reset_period=False,
                    )
                    self.schedule_goodbye_idle_after_playback(reason="assistant_goodbye")
            if event_type == "conversation.item.input_audio_transcription.completed":
                await self.mark_caller_turn_completed(transcript_text)
        text_delta = _realtime_text_delta(event)
        if text_delta:
            self.summary_parts.append(text_delta)
        if event_type == "response.done" and self.summary_parts:
            self.service.store.complete(
                self.voice_session_id,
                status="ended",
                summary="".join(self.summary_parts).strip(),
            )
        if event_type == "response.done":
            self.mark_audio_finished(event, reason="response.done")
            self.mark_response_inactive(event)
            flushed_response = await self.mark_response_done_and_flush()
            if not flushed_response and not self.end_call_requested:
                self.schedule_idle_prompt_after_playback(reason="response_done")
        if event_type == "response.cancelled":
            self.mark_audio_finished(event, reason="response.cancelled")
            self.mark_response_inactive(event)
            flushed_response = await self.mark_response_done_and_flush()
            if not flushed_response:
                self.schedule_barge_cancel_recovery(reason="response_cancelled")
        if event_type == "error":
            error = event.get("error") if isinstance(event.get("error"), dict) else event
            self.service._append_debug_event(
                self.voice_session_id,
                "error",
                metadata=_realtime_error_metadata(event),
            )
            fallback_handler = getattr(self.realtime, "apply_session_config_fallback", None)
            fallback_metadata = (
                await fallback_handler(event) if callable(fallback_handler) else None
            )
            if fallback_metadata is not None:
                self.service._append_debug_event(
                    self.voice_session_id,
                    "realtime.session_config_fallback",
                    metadata=fallback_metadata,
                )
                return
            if (
                isinstance(error, dict)
                and error.get("code") == "conversation_already_has_active_response"
            ):
                await self.requeue_last_response_request()
                return
            if isinstance(error, dict) and error.get("code") == "response_cancel_not_active":
                cancel_requested_key = self.interruption_recovery_cancel_requested_key
                if cancel_requested_key:
                    flushed_response = await self.mark_response_done_and_flush()
                    self.service._append_debug_event(
                        self.voice_session_id,
                        "bridge.interruption_recovery.cancel_not_active",
                        metadata={
                            "reason": "response_cancel_not_active",
                            "request_key": cancel_requested_key,
                            "flushed_response": flushed_response,
                            **_realtime_error_metadata(event),
                        },
                    )
                    return
                self.service._append_debug_event(
                    self.voice_session_id,
                    "response_cancel_not_active_ignored",
                    metadata={
                        "reason": "cancel_race",
                        **_realtime_error_metadata(event),
                    },
                )
                return
            raise RuntimeError(str(event.get("error") or event))

    async def _close_background_tasks(self) -> None:
        if self.idle_prompt_task is not None:
            self.idle_prompt_task.cancel()
        if self.barge_fade_task is not None:
            self.barge_fade_task.cancel()
        if self.outbound_sender_task is not None:
            self.outbound_sender_task.cancel()
        if self.goodbye_idle_task is not None:
            self.goodbye_idle_task.cancel()
        self.cancel_barge_cancel_recovery_timer()
        if self.end_call_task is not None:
            if self.end_call_completed:
                await asyncio.gather(self.end_call_task, return_exceptions=True)
            else:
                self.end_call_task.cancel()
        for task in self.barge_delay_tasks:
            task.cancel()
        for task in self.query_tasks:
            task.cancel()
        if self.idle_prompt_task is not None:
            await asyncio.gather(self.idle_prompt_task, return_exceptions=True)
        if self.barge_fade_task is not None:
            await asyncio.gather(self.barge_fade_task, return_exceptions=True)
        if self.outbound_sender_task is not None:
            await asyncio.gather(self.outbound_sender_task, return_exceptions=True)
        if self.goodbye_idle_task is not None:
            await asyncio.gather(self.goodbye_idle_task, return_exceptions=True)
        if self.barge_cancel_recovery_task is not None:
            await asyncio.gather(self.barge_cancel_recovery_task, return_exceptions=True)
        if self.end_call_task is not None and not self.end_call_completed:
            await asyncio.gather(self.end_call_task, return_exceptions=True)
        await asyncio.gather(*self.barge_delay_tasks, return_exceptions=True)
        await asyncio.gather(*self.query_tasks, return_exceptions=True)

    def payload_sample_count(self, payload: bytes) -> int:
        if not payload:
            return 0
        return max(1, (len(payload) + self.bytes_per_sample - 1) // self.bytes_per_sample)

    def played_audio_end_ms(self, item_id: str) -> int:
        samples = max(0, self.audio_item_played_samples.get(item_id, 0))
        return int((samples * 1000) // self.service.config.sample_rate_hz)

    def send_payload_accepts_sample_callback(self) -> bool:
        send_payload = getattr(self.rtp, "send_payload", None)
        if not callable(send_payload):
            return False
        try:
            parameters = inspect.signature(send_payload).parameters
        except (TypeError, ValueError):
            return False
        return "on_samples_sent" in parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
        )

    def mark_frame_samples_sent(self, frame: OutboundAudioFrame, samples: int) -> None:

        sent_samples = max(0, samples)
        if not frame.item_id or sent_samples <= 0:
            return
        self.last_played_audio_item_id = frame.item_id
        self.audio_item_played_samples[frame.item_id] = (
            self.audio_item_played_samples.get(frame.item_id, 0) + sent_samples
        )
        self.audio_item_content_index.setdefault(frame.item_id, frame.content_index)
        if frame.response_id:
            self.audio_item_response_id.setdefault(frame.item_id, frame.response_id)

    def fallback_sent_sample_count(self, result: object, payload: bytes) -> int:
        if result is None:
            return self.payload_sample_count(payload)
        try:
            return max(0, int(result))
        except (TypeError, ValueError):
            return 0

    async def send_outbound_audio(self) -> None:

        while True:
            wait_started = self.loop.time()
            frame = await self.outbound_audio_queue.get()
            waited_seconds = self.loop.time() - wait_started
            tracker = self.service._audio_quality_trackers.get(self.voice_session_id)
            if (
                tracker is not None
                and self.last_assistant_audio_at is not None
                and any(
                    key not in self.audio_response_finished
                    for key in self.audio_response_started_at
                )
                and waited_seconds
                > (self.service.config.frame_duration_ms / 1000.0)
                * self.timing.audio_quality_frame_gap_tolerance
                and hasattr(tracker, "record_buffer_underrun")
            ):
                tracker.record_buffer_underrun(wait_ms=waited_seconds * 1000.0)
            self.outbound_sender_active = True
            try:
                if self.rtp_send_supports_sample_callback:
                    await self.rtp.send_payload(
                        frame.payload,
                        on_samples_sent=lambda samples: self.mark_frame_samples_sent(
                            frame, samples
                        ),
                    )
                else:
                    result = await self.rtp.send_payload(frame.payload)
                    self.mark_frame_samples_sent(
                        frame,
                        self.fallback_sent_sample_count(result, frame.payload),
                    )
            finally:
                self.last_assistant_audio_at = self.loop.time()
                self.outbound_sender_active = False

    def start_outbound_sender(self) -> None:

        self.outbound_sender_task = asyncio.create_task(self.send_outbound_audio())

    def drain_outbound_audio_queue(self) -> list[OutboundAudioFrame]:
        drained: list[OutboundAudioFrame] = []
        while True:
            try:
                drained.append(self.outbound_audio_queue.get_nowait())
            except asyncio.QueueEmpty:
                return drained

    async def restart_outbound_sender_after_cancel(self, *, wait_for_fade: bool) -> None:

        if self.outbound_sender_task is not None and not self.outbound_sender_task.done():
            self.outbound_sender_task.cancel()
            await asyncio.gather(self.outbound_sender_task, return_exceptions=True)
        self.outbound_sender_active = False
        if not wait_for_fade:
            self.start_outbound_sender()

    def assistant_audio_active(self) -> bool:
        if (
            self.outbound_sender_active
            or self.barge_fade_active
            or not self.outbound_audio_queue.empty()
        ):
            return True
        if any(key not in self.audio_response_finished for key in self.audio_response_started_at):
            return True
        if self.last_assistant_audio_at is not None:
            return (
                self.loop.time() - self.last_assistant_audio_at
            ) < self.timing.assistant_playback_drain_seconds
        return False

    def interruptible_assistant_turn_active(self) -> bool:
        return self.assistant_audio_active() or self.response_active or self.assistant_speaking

    def safe_to_end_call(self) -> bool:
        return not (
            self.assistant_audio_active()
            or self.response_active
            or self.assistant_speaking
            or self.caller_speech_active
            or self.caller_turn_incomplete
            or self.pending_responses
        )

    def origin_query_turn_active(self) -> bool:
        if any(state.status == "pending" for state in self.origin_queries_by_id.values()):
            return True
        if any(state.status == "pending" for state in self.origin_queries_by_question.values()):
            return True
        if self.last_response_request is not None and _is_origin_query_response_key(
            self.last_response_request.key
        ):
            return True
        return any(_is_origin_query_response_key(request.key) for request in self.pending_responses)

    def origin_query_pending(self) -> bool:
        return any(
            state.status == "pending" for state in self.origin_queries_by_id.values()
        ) or any(state.status == "pending" for state in self.origin_queries_by_question.values())

    def response_blocked_by_caller_turn(self, request: BridgeResponseRequest) -> bool:
        # Caller speech can still barge in, but origin-query results must stay queued
        # until the caller turn has a final transcript.
        return self.caller_turn_incomplete and _is_origin_query_response_key(request.key)

    def first_blocked_origin_response_index(self) -> int | None:
        for index, pending in enumerate(self.pending_responses):
            if self.response_blocked_by_caller_turn(pending):
                return index
        return None

    def discard_pending_response_key(self, key: str) -> int:
        before = len(self.pending_responses)
        self.pending_responses[:] = [
            pending for pending in self.pending_responses if pending.key != key
        ]
        removed = before - len(self.pending_responses)
        if removed:
            self.pending_response_keys.discard(key)
        return removed

    def cancel_idle_prompt_timer(self) -> bool:

        task = self.idle_prompt_task
        self.idle_prompt_task = None
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    async def cancel_idle_prompt(
        self,
        *,
        reason: str,
        reset_period: bool = False,
    ) -> None:

        if reset_period:
            self.idle_generation += 1
            self.idle_prompt_sent_this_period = False
        timer_cancelled = self.cancel_idle_prompt_timer()
        async with self.response_lock:
            removed = self.discard_pending_response_key(_IDLE_PROMPT_RESPONSE_KEY)
            active_idle = (
                self.last_response_request is not None
                and self.last_response_request.key == _IDLE_PROMPT_RESPONSE_KEY
                and not self.assistant_audio_active()
            )
        if timer_cancelled or removed or active_idle:
            self.service._append_debug_event(
                self.voice_session_id,
                "bridge.idle_prompt.cancelled",
                metadata={
                    "reason": reason,
                    "timer_cancelled": timer_cancelled,
                    "removed_queued_count": removed,
                    "active_idle_response": active_idle,
                },
            )
        if active_idle:
            await self.realtime.cancel_response(self.active_response_id)

    def cancel_goodbye_idle_timer(self, *, reason: str) -> bool:

        task = self.goodbye_idle_task
        was_pending = self.goodbye_idle_pending
        self.goodbye_idle_generation += 1
        self.goodbye_idle_pending = False
        self.goodbye_idle_wait_logged = False
        self.goodbye_idle_task = None
        task_cancelled = task is not None and not task.done()
        if task_cancelled:
            task.cancel()
        if task_cancelled or was_pending:
            self.service._append_debug_event(
                self.voice_session_id,
                "bridge.goodbye_idle.cancelled",
                metadata={
                    "reason": reason,
                    "timer_cancelled": task_cancelled,
                },
            )
        return task_cancelled

    async def perform_end_call(self, reason: str | None, call_id: str | None) -> None:

        if self.end_call_completed:
            return
        self.goodbye_idle_pending = False
        record = self.service.store.get(self.voice_session_id)
        if record is None or record.status in {"ended", "failed"}:
            self.end_call_completed = True
            self.stop_event.set()
            if call_id:
                await self.realtime.send_tool_output(
                    call_id=call_id,
                    output={"status": "ended"},
                )
            return
        self.service._append_debug_event(
            self.voice_session_id,
            "bridge.end_call.executing",
            metadata={
                "reason": reason or "unknown",
                "realtime_call_id": call_id,
                "assistant_audio_active": self.assistant_audio_active(),
                "response_active": self.response_active,
                "assistant_speaking": self.assistant_speaking,
                "caller_speech_active": self.caller_speech_active,
                "caller_turn_incomplete": self.caller_turn_incomplete,
                "pending_response_count": len(self.pending_responses),
            },
        )
        try:
            await self.service._cleanup_asterisk(record)
            self.service.store.complete(
                self.voice_session_id,
                status="ended",
                summary="Call ended by voice agent.",
            )
            self.service._append_debug_event(
                self.voice_session_id,
                "bridge.end_call.completed",
                metadata={
                    "reason": reason or "unknown",
                    "realtime_call_id": call_id,
                },
            )
        finally:
            self.end_call_completed = True
            self.stop_event.set()
        if call_id:
            await self.realtime.send_tool_output(
                call_id=call_id,
                output={"status": "ended"},
            )

    async def goodbye_idle_after_playback(self, generation: int, *, reason: str) -> None:

        try:
            while (
                not self.stop_event.is_set()
                and generation == self.goodbye_idle_generation
                and self.goodbye_idle_pending
                and self.assistant_audio_active()
            ):
                await asyncio.sleep(0.05)
            if (
                self.stop_event.is_set()
                or generation != self.goodbye_idle_generation
                or not self.goodbye_idle_pending
            ):
                return
            if not self.goodbye_idle_wait_logged:
                self.goodbye_idle_wait_logged = True
                self.service._append_debug_event(
                    self.voice_session_id,
                    "bridge.goodbye_idle.waiting",
                    metadata={
                        "reason": reason,
                        "timeout_ms": int(self.service.config.goodbye_idle_hangup_seconds * 1000),
                        "assistant_audio_active": self.assistant_audio_active(),
                        "response_active": self.response_active,
                        "assistant_speaking": self.assistant_speaking,
                        "caller_speech_active": self.caller_speech_active,
                        "caller_turn_incomplete": self.caller_turn_incomplete,
                        "pending_response_count": len(self.pending_responses),
                    },
                )
            await asyncio.sleep(self.service.config.goodbye_idle_hangup_seconds)
            while (
                not self.stop_event.is_set()
                and generation == self.goodbye_idle_generation
                and self.goodbye_idle_pending
            ):
                if self.safe_to_end_call():
                    self.service._append_debug_event(
                        self.voice_session_id,
                        "bridge.goodbye_idle.executing",
                        metadata={
                            "reason": reason,
                            "timeout_ms": int(
                                self.service.config.goodbye_idle_hangup_seconds * 1000
                            ),
                        },
                    )
                    await self.perform_end_call(reason, None)
                    self.service._append_debug_event(
                        self.voice_session_id,
                        "bridge.goodbye_idle.completed",
                        metadata={
                            "reason": reason,
                            "timeout_ms": int(
                                self.service.config.goodbye_idle_hangup_seconds * 1000
                            ),
                        },
                    )
                    return
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise

    def schedule_goodbye_idle_after_playback(self, *, reason: str) -> None:

        if self.service.config.goodbye_idle_hangup_seconds <= 0:
            return
        self.cancel_goodbye_idle_timer(reason=reason)
        self.goodbye_idle_pending = True
        self.goodbye_idle_wait_logged = False
        self.service._append_debug_event(
            self.voice_session_id,
            "bridge.goodbye_idle.scheduled",
            metadata={
                "reason": reason,
                "timeout_ms": int(self.service.config.goodbye_idle_hangup_seconds * 1000),
            },
        )
        self.goodbye_idle_task = asyncio.create_task(
            self.goodbye_idle_after_playback(self.goodbye_idle_generation, reason=reason)
        )

    async def wait_for_end_call(self) -> None:

        try:
            while not self.stop_event.is_set():
                if self.end_call_completed:
                    return
                if self.safe_to_end_call():
                    await self.perform_end_call(self.end_call_reason, self.end_call_call_id)
                    return
                if not self.end_call_wait_logged:
                    self.end_call_wait_logged = True
                    self.service._append_debug_event(
                        self.voice_session_id,
                        "bridge.end_call.waiting",
                        metadata={
                            "reason": self.end_call_reason or "unknown",
                            "realtime_call_id": self.end_call_call_id,
                            "assistant_audio_active": self.assistant_audio_active(),
                            "response_active": self.response_active,
                            "assistant_speaking": self.assistant_speaking,
                            "caller_speech_active": self.caller_speech_active,
                            "caller_turn_incomplete": self.caller_turn_incomplete,
                            "pending_response_count": len(self.pending_responses),
                        },
                    )
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise

    async def request_end_call(self, function_call: RealtimeFunctionCall) -> None:

        reason = str(function_call.arguments.get("reason") or "").strip() or None
        if self.end_call_requested:
            self.service._append_debug_event(
                self.voice_session_id,
                "bridge.end_call.duplicate",
                metadata={
                    "reason": reason or "unknown",
                    "realtime_call_id": function_call.call_id,
                    "pending_realtime_call_id": self.end_call_call_id,
                },
            )
            return
        self.end_call_requested = True
        self.end_call_call_id = function_call.call_id
        self.end_call_reason = reason
        self.cancel_goodbye_idle_timer(reason="end_call_requested")
        self.cancel_barge_cancel_recovery_timer()
        self.service._append_debug_event(
            self.voice_session_id,
            "bridge.end_call.requested",
            metadata={
                "reason": reason or "unknown",
                "realtime_call_id": function_call.call_id,
            },
        )
        timer_cancelled = self.cancel_idle_prompt_timer()
        async with self.response_lock:
            removed_idle = self.discard_pending_response_key(_IDLE_PROMPT_RESPONSE_KEY)
        if timer_cancelled or removed_idle:
            self.service._append_debug_event(
                self.voice_session_id,
                "bridge.idle_prompt.cancelled",
                metadata={
                    "reason": "end_call_requested",
                    "timer_cancelled": timer_cancelled,
                    "removed_queued_count": removed_idle,
                },
            )
        self.end_call_task = asyncio.create_task(self.wait_for_end_call())

    async def idle_prompt_after_playback(self, generation: int, *, reason: str) -> None:

        try:
            while not self.stop_event.is_set() and self.assistant_audio_active():
                await asyncio.sleep(0.05)
            if (
                self.stop_event.is_set()
                or generation != self.idle_generation
                or self.idle_prompt_sent_this_period
                or self.idle_prompt_sent_count >= self.timing.bridge_idle_prompt_max_per_call
            ):
                return
            self.service._append_debug_event(
                self.voice_session_id,
                "bridge.idle_countdown.started",
                metadata={
                    "reason": reason,
                    "delay_ms": int(self.timing.bridge_idle_prompt_delay_seconds * 1000),
                    "playback_drain_ms": int(self.timing.assistant_playback_drain_seconds * 1000),
                },
            )
            await asyncio.sleep(self.timing.bridge_idle_prompt_delay_seconds)
            if (
                self.stop_event.is_set()
                or generation != self.idle_generation
                or self.idle_prompt_sent_this_period
                or self.goodbye_idle_pending
                or self.caller_speech_active
                or self.response_active
                or self.assistant_speaking
                or self.pending_responses
                or self.idle_prompt_sent_count >= self.timing.bridge_idle_prompt_max_per_call
            ):
                return
            self.idle_prompt_sent_this_period = True
            self.idle_prompt_sent_count += 1
            response_instructions = (
                _ORIGIN_QUERY_IDLE_PROMPT_INSTRUCTIONS
                if self.origin_query_pending()
                else _IDLE_PROMPT_INSTRUCTIONS
            )
            await self.request_bridge_response(
                response_instructions,
                reason="idle_prompt",
                key=_IDLE_PROMPT_RESPONSE_KEY,
            )
        except asyncio.CancelledError:
            raise

    def schedule_idle_prompt_after_playback(self, *, reason: str) -> None:

        if (
            self.idle_prompt_sent_this_period
            or self.idle_prompt_sent_count >= self.timing.bridge_idle_prompt_max_per_call
        ):
            return
        if self.goodbye_idle_pending:
            return
        if not self.audio_response_started_at and self.last_assistant_audio_at is None:
            return
        self.cancel_idle_prompt_timer()
        self.idle_prompt_task = asyncio.create_task(
            self.idle_prompt_after_playback(self.idle_generation, reason=reason)
        )

    def cancel_barge_cancel_recovery_timer(self) -> bool:

        task = self.barge_cancel_recovery_task
        self.barge_cancel_recovery_task = None
        if task is asyncio.current_task():
            return False
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    def mark_barge_cancel_followup_requested(self, *, cancel_timer: bool = True) -> None:

        self.barge_cancel_recovery_requested = True
        if cancel_timer:
            self.cancel_barge_cancel_recovery_timer()

    async def cancel_active_interruption_recovery(
        self,
        *,
        reason: str,
        utterance_id: int,
        transcript_text: str | None = None,
    ) -> bool:

        async with self.response_lock:
            request = self.last_response_request
            if (
                not self.response_active
                or request is None
                or not _is_interrupted_assistant_resume_response_key(request.key)
            ):
                return False
            self.interruption_recovery_cancel_requested_key = request.key
            cancel_response_id = self.active_response_id
            queued_count = len(self.pending_responses)
        self.service._append_debug_event(
            self.voice_session_id,
            "bridge.interruption_recovery.cancel_requested",
            metadata={
                "reason": reason,
                "utterance_id": utterance_id,
                "request_key": self.interruption_recovery_cancel_requested_key,
                "response_id": cancel_response_id,
                "queued_count": queued_count,
                "transcript_snippet": _debug_snippet(transcript_text),
            },
        )
        await self.realtime.cancel_response(cancel_response_id)
        return True

    async def request_barge_cancel_recovery(
        self,
        *,
        reason: str,
        utterance_id: int,
    ) -> None:

        task = asyncio.current_task()
        recovery_started_at = self.loop.time()
        try:
            self.service._append_debug_event(
                self.voice_session_id,
                "bridge.interruption_recovery.started",
                metadata={
                    "reason": reason,
                    "utterance_id": utterance_id,
                },
            )
            await asyncio.sleep(self.timing.interrupted_assistant_recovery_grace_seconds)
            while not self.stop_event.is_set() and self.caller_turn_incomplete:
                elapsed = self.loop.time() - recovery_started_at
                remaining = (
                    self.timing.interrupted_assistant_no_transcript_recovery_grace_seconds - elapsed
                )
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.05, remaining))
            if self.stop_event.is_set():
                return
            if self.barge_cancelled_utterance_id != utterance_id:
                return
            async with self.response_lock:
                if (
                    self.barge_cancel_recovery_requested
                    or self.caller_speech_active
                    or self.response_active
                    or self.assistant_speaking
                    or self.pending_responses
                    or self.last_response_request is not None
                    or self.end_call_requested
                ):
                    return
                self.barge_cancel_recovery_requested = True
                queued_count = len(self.pending_responses)
                active_state = self.response_active
                assistant_state = self.assistant_speaking
            self.service._append_debug_event(
                self.voice_session_id,
                "bridge.interruption_recovery.requested",
                metadata={
                    "reason": reason,
                    "utterance_id": utterance_id,
                    "response_active": active_state,
                    "assistant_speaking": assistant_state,
                    "queued_count": queued_count,
                    "caller_turn_incomplete": self.caller_turn_incomplete,
                },
            )
            await self.request_bridge_response(
                _INTERRUPTED_ASSISTANT_RESUME_INSTRUCTIONS,
                reason=reason,
                key=_interrupted_assistant_resume_response_key(utterance_id),
            )
        except asyncio.CancelledError:
            raise
        finally:
            if self.barge_cancel_recovery_task is task:
                self.barge_cancel_recovery_task = None

    def schedule_barge_cancel_recovery(self, *, reason: str) -> None:

        if self.barge_cancel_recovery_requested or self.barge_cancel_recovery_task is not None:
            return
        if self.barge_cancelled_utterance_id != self.current_caller_utterance_id:
            return
        if self.caller_speech_active:
            return
        self.service._append_debug_event(
            self.voice_session_id,
            "bridge.interruption_recovery.scheduled",
            metadata={
                "reason": reason,
                "utterance_id": self.current_caller_utterance_id,
                "due_in_seconds": self.timing.interrupted_assistant_recovery_grace_seconds,
            },
        )
        self.barge_cancel_recovery_task = asyncio.create_task(
            self.request_barge_cancel_recovery(
                reason="interruption_recovery",
                utterance_id=self.current_caller_utterance_id,
            )
        )

    async def send_barge_in_cancel(
        self,
        *,
        reason: str,
        utterance_id: int,
        transcript_text: str | None = None,
    ) -> None:

        if self.barge_cancelled_utterance_id == utterance_id:
            return
        self.barge_cancelled_utterance_id = utterance_id
        self.barge_cancelled_response_id = None
        self.barge_cancelled_audio_truncated = False
        self.barge_cancel_recovery_requested = False
        self.cancel_barge_cancel_recovery_timer()
        elapsed_ms = int(
            max(
                0.0,
                self.loop.time() - (self.caller_speech_started_at or self.loop.time()),
            )
            * 1000
        )
        truncate_item_id = self.last_played_audio_item_id
        truncate_audio_end_ms = (
            self.played_audio_end_ms(truncate_item_id) if truncate_item_id else 0
        )
        cancel_response_id = (
            self.active_response_id if self.active_response_id and self.response_active else None
        )
        self.barge_cancelled_response_id = cancel_response_id
        dropped_frames = self.drain_outbound_audio_queue()
        dropped_audio_bytes = sum(len(frame.payload) for frame in dropped_frames)
        fade_payload = _build_barge_in_fade_payload(
            frames=dropped_frames,
            media_format=self.service.config.media_format,
            sample_rate_hz=self.service.config.sample_rate_hz,
            fade_ms=self.service.config.barge_in_fade_ms,
        )
        await self.restart_outbound_sender_after_cancel(wait_for_fade=bool(fade_payload))
        if fade_payload:
            self.barge_fade_active = True

            async def send_barge_in_fade() -> None:

                cancelled = False
                try:
                    await self.rtp.send_payload(fade_payload)
                except asyncio.CancelledError:
                    cancelled = True
                    raise
                finally:
                    self.barge_fade_active = False
                    if self.barge_fade_task is asyncio.current_task():
                        self.barge_fade_task = None
                    if not cancelled and not self.stop_event.is_set():
                        self.start_outbound_sender()

            self.barge_fade_task = asyncio.create_task(send_barge_in_fade())
        self.service._append_debug_event(
            self.voice_session_id,
            "barge_in_cancel_sent",
            metadata={
                "reason": reason,
                "response_id": self.active_response_id,
                "response_active": self.response_active,
                "cancel_response_id": cancel_response_id,
                "cancel_response_truncated": self.barge_cancelled_audio_truncated,
                "cancel_recovery_requested": self.barge_cancel_recovery_requested,
                "utterance_id": utterance_id,
                "elapsed_ms": elapsed_ms,
                "dropped_audio_frames": len(dropped_frames),
                "dropped_audio_bytes": dropped_audio_bytes,
                "fade_ms": self.service.config.barge_in_fade_ms,
                "fade_audio_samples": len(fade_payload),
                "fade_audio_bytes": len(fade_payload),
                "truncate_item_id": truncate_item_id,
                "truncate_audio_end_ms": truncate_audio_end_ms,
                "cancelled_response_id": self.barge_cancelled_response_id,
                "transcript_snippet": _debug_snippet(transcript_text),
            },
        )
        if cancel_response_id:
            await self.realtime.cancel_response(cancel_response_id)
        else:
            self.service._append_debug_event(
                self.voice_session_id,
                "barge_in_cancel_response_skipped",
                metadata={
                    "reason": "no_active_response",
                    "trigger_reason": reason,
                    "utterance_id": utterance_id,
                    "truncate_item_id": truncate_item_id,
                    "truncate_audio_end_ms": truncate_audio_end_ms,
                },
            )
        if (
            truncate_item_id
            and truncate_audio_end_ms > 0
            and truncate_item_id not in self.truncated_audio_items
        ):
            content_index = self.audio_item_content_index.get(truncate_item_id, 0)
            await self.realtime.truncate_assistant_audio(
                item_id=truncate_item_id,
                content_index=content_index,
                audio_end_ms=truncate_audio_end_ms,
            )
            self.truncated_audio_items.add(truncate_item_id)
            self.barge_cancelled_audio_truncated = True
            self.service._append_debug_event(
                self.voice_session_id,
                "assistant_audio_truncated",
                metadata={
                    "reason": reason,
                    "response_id": self.audio_item_response_id.get(
                        truncate_item_id, self.active_response_id
                    ),
                    "item_id": truncate_item_id,
                    "content_index": content_index,
                    "audio_end_ms": truncate_audio_end_ms,
                    "utterance_id": utterance_id,
                    "dropped_audio_frames": dropped_frames,
                },
            )

    async def delayed_barge_in_check(self, utterance_id: int) -> None:
        delay_seconds = self.service.config.barge_in_delay_ms / 1000
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        if self.stop_event.is_set():
            return
        if utterance_id != self.current_caller_utterance_id:
            return
        if not self.caller_speech_active or not self.caller_speech_started_during_assistant:
            return
        if not self.interruptible_assistant_turn_active():
            return
        await self.send_barge_in_cancel(
            reason="speech_delay_elapsed",
            utterance_id=utterance_id,
        )

    def track_barge_delay_task(self, task: asyncio.Task[None]) -> None:
        self.barge_delay_tasks.add(task)
        task.add_done_callback(lambda completed: self.barge_delay_tasks.discard(completed))

    async def handle_speech_started(self, event: dict[str, Any]) -> None:

        await self.cancel_idle_prompt(reason="caller_speech_started", reset_period=True)
        self.cancel_goodbye_idle_timer(reason="caller_speech_started")
        self.cancel_barge_cancel_recovery_timer()
        self.current_caller_utterance_id += 1
        self.caller_speech_active = True
        self.caller_turn_incomplete = True
        self.caller_speech_started_at = self.loop.time()
        audio_active = self.assistant_audio_active()
        self.caller_speech_started_during_assistant = (
            audio_active or self.response_active or self.assistant_speaking
        )
        if not self.caller_speech_started_during_assistant:
            return
        self.service._append_debug_event(
            self.voice_session_id,
            "barge_in_delay_started",
            metadata={
                "reason": "caller_speech_started",
                "response_id": self.active_response_id,
                "utterance_id": self.current_caller_utterance_id,
                "delay_ms": self.service.config.barge_in_delay_ms,
                "assistant_audio_active": audio_active,
                "response_active": self.response_active,
                "assistant_speaking": self.assistant_speaking,
                **_realtime_vad_metadata(event),
            },
        )
        self.track_barge_delay_task(
            asyncio.create_task(self.delayed_barge_in_check(self.current_caller_utterance_id))
        )

    async def handle_speech_stopped(self) -> None:

        self.caller_speech_active = False
        self.schedule_barge_cancel_recovery(reason="caller_speech_stopped")

    async def request_bridge_response(
        self,
        instructions: str | None = None,
        *,
        reason: str = "bridge_requested",
        key: str | None = None,
        supersede_keys: set[str] | None = None,
    ) -> None:

        self.cancel_goodbye_idle_timer(reason=reason)
        self.cancel_barge_cancel_recovery_timer()
        request = BridgeResponseRequest(instructions=instructions, reason=reason, key=key)
        async with self.response_lock:
            if request.key != _IDLE_PROMPT_RESPONSE_KEY:
                timer_cancelled = self.cancel_idle_prompt_timer()
                removed_idle = self.discard_pending_response_key(_IDLE_PROMPT_RESPONSE_KEY)
                if timer_cancelled or removed_idle:
                    self.service._append_debug_event(
                        self.voice_session_id,
                        "bridge.idle_prompt.cancelled",
                        metadata={
                            "reason": request.reason,
                            "timer_cancelled": timer_cancelled,
                            "removed_queued_count": removed_idle,
                        },
                    )
            if supersede_keys:
                self.pending_responses[:] = [
                    pending
                    for pending in self.pending_responses
                    if pending.key not in supersede_keys
                ]
                self.pending_response_keys.difference_update(supersede_keys)
            if key and (
                key in self.pending_response_keys
                or (
                    self.last_response_request is not None and self.last_response_request.key == key
                )
            ):
                self.service._append_debug_event(
                    self.voice_session_id,
                    "bridge.response.skipped_duplicate",
                    metadata={
                        "reason": reason,
                        "request_key": key,
                        "queued_count": len(self.pending_responses),
                        "last_caller_turn_snippet": _debug_snippet(self.last_caller_turn),
                        **_instruction_debug_metadata(instructions),
                    },
                )
                return
            if self.response_blocked_by_caller_turn(request):
                self.pending_responses.append(request)
                if key:
                    self.pending_response_keys.add(key)
                self.service._append_debug_event(
                    self.voice_session_id,
                    "bridge.response.queued",
                    metadata={
                        "reason": "caller_turn_incomplete",
                        "request_reason": request.reason,
                        "request_key": request.key,
                        "queued_count": len(self.pending_responses),
                        "last_caller_turn_snippet": _debug_snippet(self.last_caller_turn),
                        **_instruction_debug_metadata(instructions),
                    },
                )
                return
            if self.response_active or self.assistant_speaking:
                insert_at = len(self.pending_responses)
                if request.reason == "caller_turn_completed":
                    blocked_index = self.first_blocked_origin_response_index()
                    if blocked_index is not None:
                        insert_at = blocked_index
                self.pending_responses.insert(insert_at, request)
                if key:
                    self.pending_response_keys.add(key)
                self.service._append_debug_event(
                    self.voice_session_id,
                    "bridge.response.queued",
                    metadata={
                        "reason": "active_response",
                        "request_reason": request.reason,
                        "request_key": request.key,
                        "queued_count": len(self.pending_responses),
                        "last_caller_turn_snippet": _debug_snippet(self.last_caller_turn),
                        **_instruction_debug_metadata(instructions),
                    },
                )
                return
            self.response_active = True
            self.last_response_request = request
            self.service._append_debug_event(
                self.voice_session_id,
                "bridge.response.requested",
                metadata={
                    "reason": request.reason,
                    "request_key": request.key,
                    "queued_count": len(self.pending_responses),
                    "last_caller_turn_snippet": _debug_snippet(self.last_caller_turn),
                    **_instruction_debug_metadata(instructions),
                },
            )
        await self.realtime.request_response(request.instructions)

    async def discard_bridge_response_keys(
        self,
        keys: set[str],
        *,
        reason: str,
    ) -> int:
        removed = 0
        async with self.response_lock:
            for key in keys:
                removed += self.discard_pending_response_key(key)
        if removed:
            self.service._append_debug_event(
                self.voice_session_id,
                "bridge.response.discarded",
                metadata={
                    "reason": reason,
                    "removed_count": removed,
                    "request_keys": sorted(keys),
                    "last_caller_turn_snippet": _debug_snippet(self.last_caller_turn),
                },
            )
        return removed

    async def mark_caller_turn_completed(self, transcript_text: str) -> None:

        blocked_origin_response_index = self.first_blocked_origin_response_index()
        await self.cancel_idle_prompt(reason="caller_transcript_completed", reset_period=True)
        self.last_caller_turn = transcript_text
        self.caller_speech_active = False
        self.caller_turn_incomplete = False
        is_backchannel = _is_backchannel_transcript(
            transcript_text,
            phrases=self.service.config.backchannel_phrases,
            max_words=self.service.config.backchannel_max_words,
            fuzzy_threshold=self.service.config.backchannel_fuzzy_threshold,
        )
        if self.origin_query_turn_active() and _is_origin_query_hold_ack(transcript_text):
            self.caller_speech_started_during_assistant = False
            self.service._append_debug_event(
                self.voice_session_id,
                "backchannel_ignored",
                metadata={
                    "reason": "origin_query_hold_ack",
                    "utterance_id": self.current_caller_utterance_id,
                    "transcript_snippet": _debug_snippet(transcript_text),
                },
            )
            if (
                blocked_origin_response_index is not None
                and not self.response_active
                and not self.assistant_speaking
            ):
                await self.mark_response_done_and_flush()
            return
        if not self.caller_speech_started_during_assistant:
            if is_backchannel:
                self.service._append_debug_event(
                    self.voice_session_id,
                    "backchannel_ignored",
                    metadata={
                        "reason": "standalone_backchannel",
                        "utterance_id": self.current_caller_utterance_id,
                        "transcript_snippet": _debug_snippet(transcript_text),
                    },
                )
                if (
                    blocked_origin_response_index is not None
                    and not self.response_active
                    and not self.assistant_speaking
                ):
                    await self.mark_response_done_and_flush()
                return
            request_instructions = (
                _CALLER_TURN_COMPLETION_INSTRUCTIONS
                if blocked_origin_response_index is not None
                else None
            )
            request_key = (
                f"{_CALLER_TURN_COMPLETION_RESPONSE_KEY_PREFIX}:{self.current_caller_utterance_id}"
                if blocked_origin_response_index is not None
                else None
            )
            await self.request_bridge_response(
                request_instructions,
                reason="caller_turn_completed",
                key=request_key,
            )
            return
        elapsed_ms = int(
            max(
                0.0,
                self.loop.time() - (self.caller_speech_started_at or self.loop.time()),
            )
            * 1000
        )
        self.service._append_debug_event(
            self.voice_session_id,
            "barge_in_transcript_classified",
            metadata={
                "reason": "backchannel" if is_backchannel else "substantive",
                "response_id": self.active_response_id,
                "utterance_id": self.current_caller_utterance_id,
                "elapsed_ms": elapsed_ms,
                "transcript_snippet": _debug_snippet(transcript_text),
                "word_count": _word_count(transcript_text),
            },
        )
        if is_backchannel:
            self.caller_speech_started_during_assistant = False
            self.service._append_debug_event(
                self.voice_session_id,
                "backchannel_ignored",
                metadata={
                    "reason": "classified_backchannel",
                    "response_id": self.active_response_id,
                    "utterance_id": self.current_caller_utterance_id,
                    "elapsed_ms": elapsed_ms,
                    "transcript_snippet": _debug_snippet(transcript_text),
                },
            )
            if self.barge_cancelled_utterance_id == self.current_caller_utterance_id:
                self.mark_barge_cancel_followup_requested()
                await self.request_bridge_response(
                    _INTERRUPTED_ASSISTANT_RESUME_INSTRUCTIONS,
                    reason="backchannel_resume",
                    key=_interrupted_assistant_resume_response_key(
                        self.current_caller_utterance_id
                    ),
                )
            if (
                blocked_origin_response_index is not None
                and not self.response_active
                and not self.assistant_speaking
            ):
                await self.mark_response_done_and_flush()
            return
        self.caller_speech_started_during_assistant = False
        resume_response_key = _interrupted_assistant_resume_response_key(
            self.current_caller_utterance_id
        )
        if self.barge_cancelled_utterance_id == self.current_caller_utterance_id:
            self.mark_barge_cancel_followup_requested()
            await self.discard_bridge_response_keys(
                {resume_response_key},
                reason="substantive_transcript",
            )
            await self.cancel_active_interruption_recovery(
                reason="substantive_transcript",
                utterance_id=self.current_caller_utterance_id,
                transcript_text=transcript_text,
            )
        request_instructions = (
            _CALLER_TURN_COMPLETION_INSTRUCTIONS
            if blocked_origin_response_index is not None
            else None
        )
        request_key = (
            f"{_CALLER_TURN_COMPLETION_RESPONSE_KEY_PREFIX}:{self.current_caller_utterance_id}"
            if blocked_origin_response_index is not None
            else None
        )
        await self.request_bridge_response(
            request_instructions,
            reason="caller_turn_completed",
            key=request_key,
        )
        if self.barge_cancelled_utterance_id == self.current_caller_utterance_id:
            return
        if self.assistant_audio_active():
            await self.send_barge_in_cancel(
                reason="substantive_transcript",
                utterance_id=self.current_caller_utterance_id,
                transcript_text=transcript_text,
            )

    async def mark_response_created(self, event: dict[str, Any]) -> None:

        self.cancel_barge_cancel_recovery_timer()
        async with self.response_lock:
            self.active_response_id = _realtime_response_id(event) or self.active_response_id
            self.assistant_speaking = True
            self.response_active = True
            cancel_recovery_key = (
                self.interruption_recovery_cancel_requested_key
                if (
                    self.interruption_recovery_cancel_requested_key
                    and self.last_response_request is not None
                    and self.last_response_request.key
                    == self.interruption_recovery_cancel_requested_key
                )
                else None
            )
            cancel_response_id = self.active_response_id
        if cancel_recovery_key:
            self.service._append_debug_event(
                self.voice_session_id,
                "bridge.interruption_recovery.cancel_retried",
                metadata={
                    "reason": "response_created",
                    "request_key": cancel_recovery_key,
                    "response_id": cancel_response_id,
                },
            )
            await self.realtime.cancel_response(cancel_response_id)

    async def mark_assistant_speaking(self) -> None:

        async with self.response_lock:
            self.assistant_speaking = True

    async def mark_response_done_and_flush(self) -> bool:

        request: BridgeResponseRequest | None = None
        async with self.response_lock:
            self.assistant_speaking = False
            self.response_active = False
            if (
                self.interruption_recovery_cancel_requested_key
                and self.last_response_request is not None
                and self.last_response_request.key
                == self.interruption_recovery_cancel_requested_key
            ):
                self.interruption_recovery_cancel_requested_key = None
            self.last_response_request = None
            if not self.pending_responses:
                return False
            if self.response_blocked_by_caller_turn(self.pending_responses[0]):
                self.service._append_debug_event(
                    self.voice_session_id,
                    "bridge.response.blocked",
                    metadata={
                        "reason": "caller_turn_incomplete",
                        "request_reason": self.pending_responses[0].reason,
                        "request_key": self.pending_responses[0].key,
                        "queued_count": len(self.pending_responses),
                        "last_caller_turn_snippet": _debug_snippet(self.last_caller_turn),
                        **_instruction_debug_metadata(self.pending_responses[0].instructions),
                    },
                )
                return False
            request = self.pending_responses.pop(0)
            if request.key:
                self.pending_response_keys.discard(request.key)
            self.response_active = True
            self.last_response_request = request
            self.service._append_debug_event(
                self.voice_session_id,
                "bridge.response.flushed",
                metadata={
                    "reason": "response_done",
                    "request_reason": request.reason,
                    "request_key": request.key,
                    "queued_count": len(self.pending_responses),
                    "last_caller_turn_snippet": _debug_snippet(self.last_caller_turn),
                    **_instruction_debug_metadata(request.instructions),
                },
            )
        await self.realtime.request_response(request.instructions)
        return True

    async def requeue_last_response_request(self) -> None:

        async with self.response_lock:
            self.assistant_speaking = True
            self.response_active = True
            request = self.last_response_request
            self.last_response_request = None
            if request is None:
                return
            if request.key and request.key in self.pending_response_keys:
                return
            self.pending_responses.insert(0, request)
            if request.key:
                self.pending_response_keys.add(request.key)
            self.service._append_debug_event(
                self.voice_session_id,
                "bridge.response.requeued",
                metadata={
                    "reason": "conversation_already_has_active_response",
                    "request_reason": request.reason,
                    "request_key": request.key,
                    "queued_count": len(self.pending_responses),
                    "last_caller_turn_snippet": _debug_snippet(self.last_caller_turn),
                    **_instruction_debug_metadata(request.instructions),
                },
            )

    def mark_audio_delta(self, event: dict[str, Any]) -> None:

        self.active_response_id = _realtime_response_id(event) or self.active_response_id
        self.last_assistant_audio_at = self.loop.time()
        key = _realtime_audio_key(event)
        self.audio_delta_counts[key] = self.audio_delta_counts.get(key, 0) + 1
        if key in self.audio_response_started_at:
            return
        self.audio_response_started_at[key] = self.loop.time()
        self.service._append_debug_event(
            self.voice_session_id,
            "response.audio_delta.started",
            metadata={
                **_realtime_audio_metadata(event),
                "reason": "audio_delta",
            },
        )

    def mark_audio_finished(self, event: dict[str, Any], *, reason: str) -> None:
        key = _realtime_audio_key(event)
        keys = [key] if key in self.audio_response_started_at else []
        if not keys and reason in {"response.done", "response.cancelled"}:
            keys = [
                started_key
                for started_key in self.audio_response_started_at
                if started_key not in self.audio_response_finished
            ]
        for finished_key in keys:
            if finished_key in self.audio_response_finished:
                continue
            self.audio_response_finished.add(finished_key)
            started_at = self.audio_response_started_at.get(finished_key, self.loop.time())
            metadata = {
                **_realtime_audio_metadata(event),
                "reason": reason,
                "audio_delta_count": self.audio_delta_counts.get(finished_key, 0),
                "duration_ms": int(max(0.0, self.loop.time() - started_at) * 1000),
            }
            self.service._append_debug_event(
                self.voice_session_id,
                "response.audio_delta.finished",
                metadata=metadata,
            )

    def mark_response_inactive(self, event: dict[str, Any]) -> None:

        response_id = _realtime_response_id(event)
        if response_id is None or response_id == self.active_response_id:
            self.active_response_id = None
