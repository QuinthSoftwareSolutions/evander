"""DIDWW bridge protocol values, audio codecs, and event parsing."""

from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Any, Literal
from uuid import uuid4
import httpx
from evander.voice_tasks import voice_task_looks_actionable as _voice_task_looks_actionable
from evander.voice_sessions import (
    VoiceTranscriptEvent,
    sanitize_voice_metadata,
)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .didww_sip_bridge import DidwwBridgeCallRecord

logger = logging.getLogger("evander.didww_sip_bridge")

BridgeCallStatus = Literal["created", "connecting", "active", "ended", "failed"]


_RTP_STATIC_AUDIO_FORMATS = {
    "alaw": {"openai_type": "audio/pcma", "payload_type": 8, "sample_rate_hz": 8000},
    "ulaw": {"openai_type": "audio/pcmu", "payload_type": 0, "sample_rate_hz": 8000},
}


_FAILED_HANGUP_CAUSE_CODES = {
    1,
    2,
    3,
    17,
    18,
    19,
    20,
    21,
    22,
    27,
    28,
    34,
    38,
    41,
    42,
    44,
    47,
    57,
    58,
    63,
    88,
    102,
}


_FAILED_HANGUP_CAUSE_TEXTS = {
    "allocated_channel_not_available",
    "bearer_capability_not_authorized",
    "bearer_capability_not_available",
    "busy",
    "call_rejected",
    "congestion",
    "destination_out_of_order",
    "incompatible_destination",
    "invalid_number_format",
    "network_out_of_order",
    "no_answer",
    "no_answer_from_user",
    "no_route_to_destination",
    "no_route_to_specified_transit_network",
    "no_user_responding",
    "normal_circuit_congestion",
    "number_changed",
    "recovery_on_timer_expiry",
    "requested_channel_not_available",
    "resource_unavailable",
    "service_or_option_not_available",
    "subscriber_absent",
    "switching_equipment_congestion",
    "temporary_failure",
    "unallocated_number",
    "user_busy",
}


_DEBUG_SNIPPET_LIMIT = 200


_ORIGIN_QUERY_ACK_GRACE_SECONDS = 2.0


_BRIDGE_IDLE_PROMPT_DELAY_SECONDS = 30.0


_BRIDGE_IDLE_PROMPT_MAX_PER_CALL = 2


_ASSISTANT_PLAYBACK_DRAIN_SECONDS = 0.5


_AUDIO_QUALITY_RECENT_SEQUENCE_LIMIT = 256


_AUDIO_QUALITY_FRAME_GAP_TOLERANCE = 1.5


_IDLE_PROMPT_RESPONSE_KEY = "bridge_idle_prompt"


_IDLE_PROMPT_INSTRUCTIONS = "Are you still there?"


_END_CALL_TOOL_NAME = "end_call"


_ORIGIN_QUERY_IDLE_PROMPT_INSTRUCTIONS = "Still waiting on that, thanks for holding."


_PROJECTED_TASK_RESPONSE_KEY = "projection:calling_task"


_PROJECTED_TASK_RESPONSE_INSTRUCTIONS = (
    "Answer the caller from the projected calling task now. Do not say you are checking internally."
)


_MISSING_TASK_CLARIFICATION_RESPONSE_KEY = "projection:missing_task_clarification"


_MISSING_TASK_CLARIFICATION_INSTRUCTIONS = (
    "Ask exactly one concise caller-facing clarification question about the call objective."
)


_CALLER_TURN_COMPLETION_RESPONSE_KEY_PREFIX = "bridge:caller_turn_completed"


_CALLER_TURN_COMPLETION_INSTRUCTIONS = (
    "Briefly acknowledge or answer the caller's latest turn. "
    "Do not use or mention the queued lookup result yet."
)


_INTERRUPTED_ASSISTANT_RESUME_RESPONSE_KEY_PREFIX = "bridge:interrupted_assistant_resume"


_INTERRUPTED_ASSISTANT_RESUME_INSTRUCTIONS = (
    "Continue the assistant response that was interrupted. "
    "Do not restart it, do not acknowledge the interruption, and do not switch to a new "
    "caller turn."
)


_INTERRUPTED_ASSISTANT_RECOVERY_GRACE_SECONDS = 0.25


_INTERRUPTED_ASSISTANT_NO_TRANSCRIPT_RECOVERY_GRACE_SECONDS = 1.0


_ORIGIN_QUERY_ANSWER_RESPONSE_INSTRUCTIONS = (
    "Start with a natural transition. If the conversation has moved on, start with a soft "
    "callback such as 'Quick callback to your earlier question -' or "
    "'I heard back on the earlier question.' Then briefly answer from the lookup result. "
    "Do not mention internal agent handoffs, do not say the query is still pending, and do "
    "not say that you are still checking."
)


_ORIGIN_QUERY_HOLD_RESPONSE_INSTRUCTIONS = (
    'Tell the caller in one short natural sentence: "Let me look that up for you - one moment." '
    "Do not add filler, do not invite a topic shift, and do not expose internal handoffs."
)


_ORIGIN_QUERY_TIMEOUT_RESPONSE_INSTRUCTIONS = (
    "Tell the caller you cannot pull that up right now and offer to take a message, set a "
    "follow-up, or help with something else. Do not mention any internal transfer of the "
    "request, do not say the query is still pending, and do not say you are still checking."
)


_DEFAULT_BACKCHANNEL_PHRASES = (
    "ok",
    "okay",
    "yep",
    "yup",
    "right",
    "sure",
    "uh-huh",
    "mhm",
    "mm-hmm",
    "yeah",
    "yes",
    "good",
    "great",
    "thanks",
    "thank you",
)


_AUDIO_QUALITY_WINDOW_LIMIT = 16


_AUDIO_QUALITY_ANOMALY_EVENT_LIMIT = 64


def _end_call_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "name": _END_CALL_TOOL_NAME,
        "description": (
            "End the phone call after a natural goodbye or closing when the caller has no "
            "further request."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Optional caller-facing reason for ending the call.",
                }
            },
        },
    }


def _openai_audio_format(media_format: str) -> dict[str, Any]:
    try:
        audio_format = _RTP_STATIC_AUDIO_FORMATS[media_format]
    except KeyError as exc:  # pragma: no cover - guarded by config validation
        raise ValueError(f"Unsupported Realtime bridge media format: {media_format}") from exc
    return {"type": str(audio_format["openai_type"])}


def _rtp_bytes_per_sample(media_format: str) -> int:
    if media_format in _RTP_STATIC_AUDIO_FORMATS:
        return 1
    raise ValueError(f"Unsupported RTP media format: {media_format}")


_G711_SILENCE_BYTES = {
    "alaw": b"\xd5",
    "ulaw": b"\xff",
}


def _decode_g711_alaw_sample(encoded_sample: int) -> int:
    value = encoded_sample ^ 0x55
    mantissa = value & 0x0F
    exponent = (value >> 4) & 0x07
    if exponent == 0:
        linear_sample = (mantissa << 4) + 8
    else:
        linear_sample = ((mantissa << 4) + 0x108) << (exponent - 1)
    return linear_sample if value & 0x80 else -linear_sample


def _decode_g711_ulaw_sample(encoded_sample: int) -> int:
    value = (~encoded_sample) & 0xFF
    mantissa = value & 0x0F
    exponent = (value >> 4) & 0x07
    linear_sample = ((mantissa << 3) + 0x84) << exponent
    linear_sample -= 0x84
    return -linear_sample if value & 0x80 else linear_sample


_G711_DECODE_TABLES = {
    "alaw": tuple(_decode_g711_alaw_sample(sample) for sample in range(256)),
    "ulaw": tuple(_decode_g711_ulaw_sample(sample) for sample in range(256)),
}


def _g711_silence_byte(media_format: str) -> bytes:
    try:
        return _G711_SILENCE_BYTES[media_format]
    except KeyError as exc:  # pragma: no cover - guarded by config validation
        raise ValueError(f"Unsupported RTP media format: {media_format}") from exc


def _g711_decode_sample(media_format: str, encoded_sample: int) -> int:
    try:
        return _G711_DECODE_TABLES[media_format][encoded_sample]
    except KeyError as exc:  # pragma: no cover - guarded by config validation
        raise ValueError(f"Unsupported RTP media format: {media_format}") from exc


@lru_cache(maxsize=32768)
def _g711_encode_sample(media_format: str, linear_sample: int) -> int:
    if linear_sample == 0:
        return _g711_silence_byte(media_format)[0]
    try:
        decode_table = _G711_DECODE_TABLES[media_format]
    except KeyError as exc:  # pragma: no cover - guarded by config validation
        raise ValueError(f"Unsupported RTP media format: {media_format}") from exc

    best_sample = 0
    best_distance: int | None = None
    for candidate, decoded_sample in enumerate(decode_table):
        distance = abs(decoded_sample - linear_sample)
        if best_distance is None or distance < best_distance:
            best_sample = candidate
            best_distance = distance
    return best_sample


def _build_barge_in_fade_payload(
    *,
    frames: list[OutboundAudioFrame],
    media_format: str,
    sample_rate_hz: int,
    fade_ms: int,
) -> bytes:
    if fade_ms <= 0 or not frames:
        return b""
    samples_to_fade = int((fade_ms * sample_rate_hz) / 1000)
    if samples_to_fade <= 0:
        return b""
    bytes_per_sample = _rtp_bytes_per_sample(media_format)
    silence_byte = _g711_silence_byte(media_format)
    source_payload = b"".join(frame.payload for frame in frames if frame.payload)
    if not source_payload:
        return b""
    fade_window = max(1, samples_to_fade - 1)
    payload = bytearray()
    for index in range(samples_to_fade):
        offset = index * bytes_per_sample
        sample_fragment = source_payload[offset : offset + bytes_per_sample]
        if not sample_fragment:
            payload.extend(silence_byte)
            continue
        linear_sample = _g711_decode_sample(media_format, sample_fragment[0])
        gain = 0.0 if samples_to_fade == 1 else 1.0 - (index / fade_window)
        faded_sample = int(linear_sample * gain)
        payload.append(_g711_encode_sample(media_format, faded_sample))
    return bytes(payload)


@dataclass(frozen=True)
class RealtimeFunctionCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class BridgeResponseRequest:
    instructions: str | None = None
    reason: str = "bridge_requested"
    key: str | None = None


@dataclass
class OriginQueryState:
    query_id: str
    question: str
    question_key: str
    status: str = "pending"
    query: dict[str, Any] = field(default_factory=dict)
    ack_response_queued: bool = False
    answer_response_queued: bool = False
    timeout_response_queued: bool = False
    result_injected: bool = False
    transcript_recorded: bool = False
    failure_reused: bool = False
    completed_event: asyncio.Event | None = None


@dataclass(frozen=True)
class OutboundAudioFrame:
    payload: bytes
    response_id: str | None = None
    item_id: str | None = None
    content_index: int = 0


def initial_inbound_call_instructions(
    inbound_instructions: dict[str, Any],
    projection: dict[str, Any],
) -> str:
    route_prompt = _optional_text(inbound_instructions.get("route_prompt")) or (
        _extract_projected_task_prompt(projection) or ""
    )
    startup_text = _optional_text(inbound_instructions.get("startup_text"))
    opening = startup_text or route_prompt or "<missing>"
    return (
        "Start the inbound phone call now. Greet the caller naturally using the supplied "
        "startup text when present, then continue from the route prompt. Do not use "
        "outbound startup wording, do not narrate internal handoff or context checks, "
        "and do not ask for the same script or instructions again.\n\n"
        f"Caller-facing opening:\n{opening}\n\n"
        f"Route prompt:\n{route_prompt or '<missing>'}"
    )


def projection_to_instructions(projection: dict[str, Any]) -> str:
    if _projection_is_inbound(projection):
        return _inbound_projection_to_instructions(projection)
    instructions = [
        "You are the same Evander agent represented by this projected voice session.",
        "Use the provided origin context faithfully. Do not claim to be a separate handoff agent.",
        (
            "Outbound startup contract: treat the supplied Current task as authoritative "
            "first-turn task state. If it includes a complete opening or objective, begin "
            "with it immediately. Do not ask internally for the same script, "
            "instructions, objective, expected behavior, or call context. Do not say you "
            "are doing a context check, narrate task-missing state, or ask open-ended "
            "what-to-do questions when the Current task already provides the task."
        ),
        (
            "Speak like a natural phone participant: keep normal answers to one or two short "
            "spoken paragraphs unless the caller asks for detail, avoid repeating yourself, "
            "and pause naturally after a long answer instead of immediately asking whether "
            "the caller is still there."
        ),
        (
            "Treat short acknowledgements such as ok, okay, uh-huh, good, thanks, and right "
            "as backchannels unless they include a new request. After a backchannel, do not "
            "repeat the prior answer; continue only if you were mid-thought, otherwise pause."
        ),
        (
            "Use the query_origin_agent tool only for missing origin-side files, tools, "
            "repository state, memory, or live session state that is not already in the "
            "projected task/context. While an origin query is pending, do not fill the call "
            "with generic startup scaffolding such as asking what to do, saying you are "
            "doing a context check, or narrating task-missing state. When the origin answer "
            "arrives, give the answer directly without recapping the pending state."
        ),
        (
            "If the supplied Current task is not actionable enough to start and the missing "
            "data must come from the caller, ask exactly one concise caller-facing "
            "clarification question, then wait."
        ),
        (
            "Preserve decisions and follow-up actions internally after the call, but do not "
            "narrate internal bookkeeping, transcript cleanup, or handoff notes aloud."
        ),
        (
            "After a natural goodbye or closing, and only when the caller has no further "
            "request, call end_call to end the phone call. Do not call it during active "
            "discussion or before saying goodbye."
        ),
    ]
    origin = projection.get("origin")
    if isinstance(origin, dict):
        instructions.append(
            f"Origin: {json.dumps(sanitize_voice_metadata(origin), sort_keys=True)}"
        )
    prompt = projection.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        instructions.append(f"Current task: {prompt.strip()}")
    context = projection.get("context")
    if isinstance(context, str) and context.strip():
        instructions.append(f"Context:\n{context.strip()}")
    return "\n\n".join(instructions)


def _inbound_projection_to_instructions(projection: dict[str, Any]) -> str:
    instructions = [
        "You are the same Evander agent represented by this projected voice session.",
        "Use the provided origin context faithfully. Do not claim to be a separate handoff agent.",
        (
            "Inbound startup contract: treat the supplied route prompt as the caller-facing "
            "starting context. If the route includes startup text, greet the caller with it "
            "naturally. Do not use outbound startup wording, do not ask for the same script "
            "or instructions again, and do not narrate context checks or internal handoffs."
        ),
        (
            "Speak like a natural phone participant: keep normal answers to one or two short "
            "spoken paragraphs unless the caller asks for detail, avoid repeating yourself, "
            "and pause naturally after a long answer instead of immediately asking whether "
            "the caller is still there."
        ),
        (
            "Treat short acknowledgements such as ok, okay, uh-huh, good, thanks, and right "
            "as backchannels unless they include a new request. After a backchannel, do not "
            "repeat the prior answer; continue only if you were mid-thought, otherwise pause."
        ),
        (
            "Use the query_origin_agent tool only for missing origin-side files, tools, "
            "repository state, memory, or live session state that is not already in the "
            "projected task/context. While an origin query is pending, do not fill the call "
            "with generic startup scaffolding. When the origin answer arrives, give the "
            "answer directly without recapping the pending state."
        ),
        (
            "If the supplied route prompt is not actionable enough to start and the missing "
            "data must come from the caller, ask exactly one concise caller-facing "
            "clarification question, then wait."
        ),
        (
            "Preserve decisions and follow-up actions internally after the call, but do not "
            "narrate internal bookkeeping, transcript cleanup, or handoff notes aloud."
        ),
        (
            "After a natural goodbye or closing, and only when the caller has no further "
            "request, call end_call to end the phone call. Do not call it during active "
            "discussion or before saying goodbye."
        ),
    ]
    origin = projection.get("origin")
    if isinstance(origin, dict):
        instructions.append(
            f"Origin: {json.dumps(sanitize_voice_metadata(origin), sort_keys=True)}"
        )
    caller_metadata = projection.get("caller_metadata")
    if isinstance(caller_metadata, dict):
        instructions.append(
            f"Caller metadata: {json.dumps(sanitize_voice_metadata(caller_metadata), sort_keys=True)}"
        )
    prompt = _extract_projected_task_prompt(projection)
    if isinstance(prompt, str) and prompt.strip():
        instructions.append(f"Route prompt: {prompt.strip()}")
    context = projection.get("context")
    if isinstance(context, str) and context.strip():
        instructions.append(f"Context:\n{context.strip()}")
    return "\n\n".join(instructions)


def _projection_is_inbound(projection: dict[str, Any]) -> bool:
    caller_metadata = projection.get("caller_metadata")
    if (
        isinstance(caller_metadata, dict)
        and str(caller_metadata.get("direction") or "").strip() == "inbound"
    ):
        return True
    metadata = projection.get("metadata")
    if isinstance(metadata, dict) and str(metadata.get("direction") or "").strip() == "inbound":
        return True
    return str(projection.get("direction") or "").strip() == "inbound"


def initial_outbound_call_instructions(projection: dict[str, Any]) -> str:
    task_prompt = _extract_projected_task_prompt(projection) or ""
    if _projection_has_complete_startup_objective(projection, task_prompt):
        return (
            "Start the outbound phone call now using the supplied Current task as "
            "authoritative first-turn task state. The first audible assistant turn must "
            "begin from the task-specific opening/objective below. Do not ask for the "
            "script, instructions, expected behavior, or call context; do not say you are "
            "doing a context check or narrate task-missing state; do not ask open-ended "
            "what-to-do questions.\n\n"
            f"Caller-facing opening/objective:\n{task_prompt}"
        )
    supplied = task_prompt or "<missing>"
    return (
        "Start the outbound phone call now. The supplied Current task is not actionable "
        "enough to start, so ask exactly one concise caller-facing clarification question "
        "about the call objective, then wait. Do not mention origin context, missing task "
        "state, internal handoff, or context-check narration.\n\n"
        f"Supplied task:\n{supplied}"
    )


def transcript_from_events(events: list[VoiceTranscriptEvent]) -> str | None:
    lines = []
    for event in events:
        speaker = event.speaker or "speaker"
        lines.append(f"{speaker}: {event.text}")
    return "\n".join(lines) if lines else None


def _sanitize_debug_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    jsonable = _debug_jsonable(metadata)
    sanitized = sanitize_voice_metadata(jsonable)
    truncated = _truncate_debug_value(sanitized)
    if not isinstance(truncated, dict):
        return {}
    _restore_debug_hashes(jsonable, truncated)
    return truncated


def _debug_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _debug_jsonable(raw_value) for key, raw_value in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_debug_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _truncate_debug_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _truncate_debug_value(raw_value) for key, raw_value in value.items()}
    if isinstance(value, list):
        return [_truncate_debug_value(item) for item in value[:20]]
    if isinstance(value, str):
        return _debug_snippet(value, limit=_DEBUG_SNIPPET_LIMIT)
    return value


def _restore_debug_hashes(original: Any, sanitized: Any) -> None:
    if not isinstance(original, dict) or not isinstance(sanitized, dict):
        return
    for key, raw_value in original.items():
        text_key = str(key)
        if text_key.endswith("_sha256") and isinstance(raw_value, str):
            normalized = raw_value.strip().lower()
            if re.fullmatch(r"[0-9a-f]{64}", normalized):
                sanitized[text_key] = normalized
                continue
        _restore_debug_hashes(raw_value, sanitized.get(text_key))


def _debug_snippet(value: object, *, limit: int = _DEBUG_SNIPPET_LIMIT) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return None
    sanitized = sanitize_voice_metadata(text)
    text = str(sanitized or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."


def _instruction_debug_metadata(instructions: str | None) -> dict[str, Any]:
    normalized = str(instructions or "").strip()
    if not normalized:
        return {"instruction_present": False}
    return {
        "instruction_present": True,
        "instruction_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "instruction_length": len(normalized),
        "instruction_snippet": _debug_snippet(normalized),
    }


def _log_debug_event(
    record: DidwwBridgeCallRecord,
    event_type: str,
    metadata: dict[str, Any],
) -> None:
    logger.info(
        "didww_bridge_debug_event voice_session_id=%s call_id=%s event_type=%s "
        "response_id=%s query_id=%s reason=%s elapsed_ms=%s duration_ms=%s metadata=%s",
        record.voice_session_id,
        record.call_id,
        event_type,
        _debug_log_value(metadata.get("response_id")),
        _debug_log_value(metadata.get("query_id")),
        _debug_log_value(metadata.get("reason")),
        _debug_log_value(metadata.get("elapsed_ms")),
        _debug_log_value(metadata.get("duration_ms")),
        json.dumps(metadata, sort_keys=True, ensure_ascii=True),
    )


def _debug_log_value(value: Any) -> str:
    if value is None:
        return "-"
    return str(value)


def _normalize_backchannel_phrase(value: object) -> str:
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    text = text.replace("'", "")
    text = re.sub(r"[-_]+", " ", text)
    text = re.sub(r"[^a-z0-9\s]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _word_count(value: object) -> int:
    normalized = _normalize_backchannel_phrase(value)
    if not normalized:
        return 0
    return len(normalized.split())


def _backchannel_similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0
    left_len = len(left)
    right_len = len(right)
    previous = list(range(right_len + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            insertion = current[right_index - 1] + 1
            deletion = previous[right_index] + 1
            substitution = previous[right_index - 1] + (left_char != right_char)
            current.append(min(insertion, deletion, substitution))
        previous = current
    distance = previous[right_len]
    return 1.0 - (distance / float(left_len + right_len))


def _is_backchannel_transcript(
    transcript_text: object,
    *,
    phrases: list[str],
    max_words: int,
    fuzzy_threshold: float = 0.84,
) -> bool:
    normalized = _normalize_backchannel_phrase(transcript_text)
    if not normalized or _word_count(normalized) > max_words:
        return False
    normalized_phrases = {
        phrase for phrase in (_normalize_backchannel_phrase(item) for item in phrases) if phrase
    }
    if normalized in normalized_phrases:
        return True
    if all(word in normalized_phrases for word in normalized.split()):
        return True
    return any(
        _backchannel_similarity(normalized, phrase) >= fuzzy_threshold
        for phrase in normalized_phrases
    )


def _is_origin_query_hold_ack(transcript_text: object) -> bool:
    normalized = _normalize_backchannel_phrase(transcript_text)
    if not normalized or _word_count(normalized) > 6:
        return False
    hold_ack_phrases = {
        "ill wait",
        "i will wait",
        "ill just wait",
        "i will just wait",
        "yes",
        "yes please",
        "yeah",
        "yeah please",
        "yep",
        "yep please",
        "sure",
        "sure please",
        "ok",
        "okay",
        "nah ill wait",
        "nah i will wait",
        "nah ill just wait",
        "nah i will just wait",
        "no ill wait",
        "no i will wait",
        "no ill just wait",
        "no i will just wait",
        "no thanks ill wait",
        "no thanks i will wait",
    }
    return normalized in hold_ack_phrases


def _is_goodbye_transcript(transcript_text: object) -> bool:
    normalized = _normalize_backchannel_phrase(transcript_text)
    if not normalized:
        return False
    standalone_call_thanks = {
        "thanks for calling",
        "thank you for calling",
    }
    if normalized in standalone_call_thanks:
        return True
    closing_phrases = (
        "goodbye",
        "good bye",
        "bye",
        "bye for now",
        "have a good day",
        "talk to you soon",
    )
    return any(
        re.search(rf"(?:^| ){re.escape(phrase)}(?: |$)", normalized) is not None
        for phrase in closing_phrases
    )


def _is_origin_query_response_key(key: object) -> bool:
    return str(key or "").startswith("query:")


def _interrupted_assistant_resume_response_key(utterance_id: int) -> str:
    return f"{_INTERRUPTED_ASSISTANT_RESUME_RESPONSE_KEY_PREFIX}:{utterance_id}"


def _is_interrupted_assistant_resume_response_key(key: object) -> bool:
    return str(key or "").startswith(f"{_INTERRUPTED_ASSISTANT_RESUME_RESPONSE_KEY_PREFIX}:")


def _origin_query_response_key(query_id: str, kind: str) -> str:
    return f"query:{query_id}:{kind}"


def _origin_query_tool_output(
    query: dict[str, Any],
    *,
    deduplicated: bool = False,
) -> dict[str, Any]:
    status = str(query.get("status") or "pending").strip() or "pending"
    output: dict[str, Any] = {"status": status}
    query_id = str(query.get("query_id") or "").strip()
    if query_id:
        output["query_id"] = query_id
    answer = str(query.get("answer") or "").strip()
    error = str(query.get("error") or "").strip()
    if answer:
        output["answer"] = answer
    if error:
        output["error"] = error
    citations = query.get("citations")
    if isinstance(citations, list) and citations:
        output["citations"] = citations
    if deduplicated:
        output["deduplicated"] = True
    return output


def _projected_task_answer(question: str, projection: dict[str, Any]) -> str | None:
    if not _question_asks_projected_task(question):
        return None
    return _projected_startup_task_answer(projection)


def _projected_startup_task_answer(projection: dict[str, Any]) -> str | None:
    task_prompt = _extract_projected_task_prompt(projection)
    if not task_prompt:
        return None
    if not _projection_has_complete_startup_objective(projection, task_prompt):
        return None
    return f"The projected calling task is already authoritative: {task_prompt}"


def _projection_has_complete_startup_objective(
    projection: dict[str, Any],
    task_prompt: str,
) -> bool:
    contract = projection.get("startup_contract")
    if isinstance(contract, dict) and "complete_objective" in contract:
        return bool(contract.get("complete_objective"))
    return _voice_task_looks_actionable(task_prompt)


def _question_asks_projected_task(question: str) -> bool:
    normalized = _voice_query_key(question)
    if not normalized:
        return False
    task_terms = (
        "calling task",
        "call task",
        "call objective",
        "current task",
        "voice task",
        "task prompt",
        "task details",
        "why am i calling",
        "why are we calling",
        "what am i calling",
        "what are we calling",
        "what is this call about",
        "what's this call about",
        "what am i supposed to do",
        "what are we supposed to do",
        "what should i do",
        "what do you want me to do",
        "what would you like me to do",
    )
    if any(term in normalized for term in task_terms):
        return True
    standalone_startup_terms = (
        "what are my instructions",
        "what are the instructions",
        "what instructions should i follow",
        "what exact instructions",
        "exact instructions",
        "same exact instructions",
        "what is the expected behavior",
        "what behavior is expected",
        "what opening should i use",
        "what script should i use",
    )
    if any(term in normalized for term in standalone_startup_terms):
        return True
    script_terms = (
        "script",
        "opening",
        "instructions",
        "expected behavior",
        "what should i say",
        "what do i say",
        "how should i start",
        "how do i start",
    )
    call_terms = ("call", "caller", "phone", "voice", "task", "objective", "prompt")
    return any(term in normalized for term in script_terms) and any(
        term in normalized for term in call_terms
    )


def _extract_projected_task_prompt(projection: dict[str, Any]) -> str | None:
    task_prompt = projection.get("task_prompt")
    if isinstance(task_prompt, str) and task_prompt.strip():
        return task_prompt.strip()
    prompt = projection.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return None
    marker = "Current voice task:"
    if marker in prompt:
        return prompt.split(marker, 1)[1].strip()
    return prompt.strip()


def _voice_query_key(question: str) -> str:
    return re.sub(r"\s+", " ", str(question or "").strip()).casefold()


def redact_phone_number(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    digits = [char for char in text if char.isdigit()]
    if len(digits) <= 4:
        return "***"
    return f"***{''.join(digits[-4:])}"


def _ari_id(prefix: str, call_id: str) -> str:
    allowed = "".join(ch for ch in call_id if ch.isalnum() or ch in {"-", "_"})
    return f"{prefix}-{allowed}"[:80]


def _sanitize_call_id(value: object) -> str:
    text = "".join(ch for ch in str(value or "").strip() if ch.isalnum() or ch in {"-", "_"})
    return text or uuid4().hex


def _normalize_inbound_phone_number(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = "".join(ch for ch in text if ch.isdigit() or ch == "+")
    return normalized or None


def _hash_identity(value: object) -> str | None:
    normalized = _normalize_inbound_phone_number(value) or _optional_text(value)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _ari_event_channel_id(event: dict[str, Any]) -> str | None:
    channel = event.get("channel")
    if isinstance(channel, dict):
        value = channel.get("id")
        return str(value) if value is not None else None
    return None


def _ari_event_args(event: dict[str, Any]) -> list[str]:
    raw_args = event.get("args")
    if isinstance(raw_args, (list, tuple)):
        return [str(item).strip() for item in raw_args if str(item).strip()]
    if isinstance(raw_args, str):
        return [part.strip() for part in raw_args.split(",") if part.strip()]
    raw_app_args = event.get("appArgs")
    if isinstance(raw_app_args, str):
        return [part.strip() for part in raw_app_args.split(",") if part.strip()]
    return []


def _extract_sip_headers(channelvars: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    for candidate in (
        channelvars.get("sip_headers"),
        channelvars.get("SIP_HEADERS"),
        event.get("sip_headers"),
        event.get("SIP_HEADERS"),
    ):
        if isinstance(candidate, dict):
            return sanitize_voice_metadata(candidate)
    return {}


def _extract_inbound_stasis_context(event: dict[str, Any]) -> dict[str, Any] | None:
    if str(event.get("type") or "") != "StasisStart":
        return None
    channel = event.get("channel")
    if not isinstance(channel, dict):
        return None
    channel_id = _optional_text(channel.get("id")) or _optional_text(event.get("channel_id"))
    if not channel_id:
        return None
    channelvars_candidate = (
        channel.get("channelvars") or channel.get("variables") or event.get("channelvars")
    )
    channelvars = channelvars_candidate if isinstance(channelvars_candidate, dict) else {}
    args = _ari_event_args(event)
    inbound_marker = False
    did: str | None = None
    route_id: str | None = None
    for arg in args:
        lowered = arg.casefold()
        if lowered in {"inbound", "inbound-phone", "inbound_phone"}:
            inbound_marker = True
            continue
        if not lowered.startswith("inbound:"):
            continue
        inbound_marker = True
        descriptor = arg.split(":", 1)[1].strip()
        if not descriptor:
            continue
        first, _, second = descriptor.partition(":")
        maybe_did = _normalize_inbound_phone_number(first)
        if maybe_did is not None:
            did = maybe_did
            if second:
                route_id = _optional_text(second) or route_id
        else:
            route_id = _optional_text(first) or route_id
            if second:
                maybe_did = _normalize_inbound_phone_number(second)
                if maybe_did is not None:
                    did = maybe_did
    for key in ("INBOUND_PHONE_CALL", "INBOUND_CALL", "EVANDER_INBOUND", "INBOUND"):
        marker = _optional_text(channelvars.get(key))
        if marker and marker.casefold() in {"1", "true", "yes", "inbound"}:
            inbound_marker = True
    if not inbound_marker:
        return None
    if did is None:
        for key in ("DID", "CALLED_NUMBER", "CALLED_DID", "EXTEN", "called_number", "did"):
            did = _normalize_inbound_phone_number(channelvars.get(key))
            if did:
                break
    if route_id is None:
        for key in ("INBOUND_ROUTE_ID", "ROUTE_ID", "route_id"):
            route_id = _optional_text(channelvars.get(key))
            if route_id:
                break
    if did is not None or route_id is not None:
        inbound_marker = True
    if did is None and route_id is None:
        return None
    caller_number: str | None = None
    caller = channel.get("caller")
    if isinstance(caller, dict):
        caller_number = _normalize_inbound_phone_number(caller.get("number") or caller.get("name"))
    if caller_number is None:
        for key in ("CALLERID(num)", "CALLER_NUMBER", "caller_number", "callerid_num"):
            caller_number = _normalize_inbound_phone_number(channelvars.get(key))
            if caller_number:
                break
    bridge_call_id = (
        _optional_text(channelvars.get("BRIDGE_CALL_ID"))
        or _optional_text(event.get("bridge_call_id"))
        or _optional_text(event.get("bridgeCallId"))
        or channel_id
    )
    return {
        "asterisk_channel_id": channel_id,
        "did": did,
        "route_id": route_id,
        "caller_number": caller_number,
        "bridge_call_id": bridge_call_id,
        "sip_headers": _extract_sip_headers(channelvars, event),
    }


def _ari_channel_state(event: dict[str, Any]) -> str | None:
    channel = event.get("channel")
    if isinstance(channel, dict):
        state = channel.get("state")
        return str(state) if state is not None else None
    return None


def _asterisk_debug_event_type(ari_event_type: str) -> str:
    if ari_event_type == "ChannelHangupRequest":
        return "asterisk.hangup"
    if ari_event_type == "ChannelDestroyed":
        return "asterisk.channel_destroyed"
    if ari_event_type == "StasisEnd":
        return "asterisk.stasis_end"
    return "asterisk.call_event"


def _unicast_rtp_local_addr(channel: dict[str, Any]) -> tuple[str, int] | None:
    raw_vars = channel.get("channelvars")
    channelvars = raw_vars if isinstance(raw_vars, dict) else {}
    address = str(channelvars.get("UNICASTRTP_LOCAL_ADDRESS") or "").strip()
    port_text = str(channelvars.get("UNICASTRTP_LOCAL_PORT") or "").strip()
    if not address or not port_text:
        return None
    try:
        port = int(port_text)
    except ValueError:
        return None
    if port <= 0 or port > 65535:
        return None
    return (address, port)


def _ari_hangup_status(event: dict[str, Any]) -> BridgeCallStatus:
    if _ari_hangup_cause_is_failed(event.get("cause_txt")):
        return "failed"
    if _ari_hangup_cause_is_failed(event.get("cause")):
        return "failed"
    return "ended"


def _ari_hangup_cause_is_failed(value: object) -> bool:
    code = _ari_hangup_cause_code(value)
    if code in _FAILED_HANGUP_CAUSE_CODES:
        return True
    return _normalize_hangup_cause_text(value) in _FAILED_HANGUP_CAUSE_TEXTS


def _ari_hangup_cause_code(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    return int(text) if text.isdecimal() else None


def _normalize_hangup_cause_text(value: object) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _realtime_audio_delta(event: dict[str, Any]) -> str | None:
    event_type = str(event.get("type") or "")
    if event_type in {"response.output_audio.delta", "response.audio.delta"}:
        value = event.get("delta") or event.get("audio")
        return str(value) if value else None
    return None


def _realtime_audio_done(event: dict[str, Any]) -> bool:
    return str(event.get("type") or "") in {
        "response.output_audio.done",
        "response.audio.done",
    }


def _realtime_response_id(event: dict[str, Any]) -> str | None:
    response = event.get("response")
    if isinstance(response, dict):
        value = response.get("id")
        if value:
            return str(value)
    for key in ("response_id", "id"):
        value = event.get(key)
        if value:
            return str(value)
    return None


def _realtime_item_id(event: dict[str, Any]) -> str | None:
    item = event.get("item")
    if isinstance(item, dict):
        value = item.get("id") or item.get("item_id")
        if value:
            return str(value)
    value = event.get("item_id")
    return str(value) if value else None


def _realtime_content_index(event: dict[str, Any]) -> int:
    value = event.get("content_index")
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _realtime_audio_key(event: dict[str, Any]) -> str:
    return _realtime_response_id(event) or _realtime_item_id(event) or "unknown"


def _realtime_lifecycle_metadata(event: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "realtime_event_type": str(event.get("type") or ""),
    }
    response_id = _realtime_response_id(event)
    if response_id:
        metadata["response_id"] = response_id
    response = event.get("response")
    if isinstance(response, dict):
        status = response.get("status")
        if status:
            metadata["status"] = str(status)
        status_details = response.get("status_details")
        if isinstance(status_details, dict):
            reason = status_details.get("reason") or status_details.get("type")
            if reason:
                metadata["reason"] = str(reason)
    return metadata


def _realtime_audio_metadata(event: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "realtime_event_type": str(event.get("type") or ""),
    }
    for key, value in {
        "response_id": _realtime_response_id(event),
        "item_id": _realtime_item_id(event),
        "output_index": event.get("output_index"),
        "content_index": event.get("content_index"),
    }.items():
        if value is not None:
            metadata[key] = value
    return metadata


def _realtime_transcript_metadata(event: dict[str, Any], transcript_text: str) -> dict[str, Any]:
    metadata = _realtime_audio_metadata(event)
    metadata["transcript_snippet"] = _debug_snippet(transcript_text)
    return metadata


def _realtime_vad_event(event: dict[str, Any]) -> bool:
    event_type = str(event.get("type") or "")
    lowered = event_type.lower()
    return (
        event_type
        in {
            "input_audio_buffer.speech_started",
            "input_audio_buffer.speech_stopped",
            "input_audio_buffer.timeout_triggered",
        }
        or "vad" in lowered
    )


def _realtime_vad_metadata(event: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {"realtime_event_type": str(event.get("type") or "")}
    for key in ("audio_start_ms", "audio_end_ms", "item_id"):
        value = event.get(key)
        if value is not None:
            metadata[key] = value
    return metadata


def _realtime_error_metadata(event: dict[str, Any]) -> dict[str, Any]:
    error = event.get("error") if isinstance(event.get("error"), dict) else event
    metadata: dict[str, Any] = {
        "realtime_event_type": str(event.get("type") or "error"),
    }
    if isinstance(error, dict):
        code = error.get("code")
        error_type = error.get("type")
        message = error.get("message")
        event_id = error.get("event_id") or event.get("event_id")
        response_id = error.get("response_id") or _realtime_response_id(event)
        if code:
            metadata["error_code"] = str(code)
            metadata["reason"] = str(code)
        if error_type:
            metadata["error_type"] = str(error_type)
        if message:
            metadata["message_snippet"] = _debug_snippet(message)
        if event_id:
            metadata["realtime_event_id"] = str(event_id)
        if response_id:
            metadata["response_id"] = str(response_id)
    else:
        metadata["message_snippet"] = _debug_snippet(error)
    return metadata


def _realtime_function_call(event: dict[str, Any]) -> RealtimeFunctionCall | None:
    event_type = str(event.get("type") or "")
    raw_item = event.get("item")
    item = raw_item if isinstance(raw_item, dict) else event
    item_type = str(item.get("type") or "")
    if event_type == "response.output_item.done" and item_type != "function_call":
        return None
    if (
        event_type
        not in {
            "response.output_item.done",
            "response.function_call_arguments.done",
        }
        and item_type != "function_call"
    ):
        return None
    name = str(item.get("name") or event.get("name") or "").strip()
    call_id = str(item.get("call_id") or event.get("call_id") or item.get("id") or "").strip()
    raw_arguments = item.get("arguments") if "arguments" in item else event.get("arguments")
    if not name or not call_id:
        return None
    arguments: dict[str, Any] = {}
    if isinstance(raw_arguments, dict):
        arguments = raw_arguments
    elif isinstance(raw_arguments, str) and raw_arguments.strip():
        try:
            parsed = json.loads(raw_arguments)
            if isinstance(parsed, dict):
                arguments = parsed
        except json.JSONDecodeError:
            arguments = {"question": raw_arguments}
    return RealtimeFunctionCall(call_id=call_id, name=name, arguments=arguments)


def _realtime_transcript_text(event: dict[str, Any]) -> str | None:
    event_type = str(event.get("type") or "")
    if event_type in {
        "conversation.item.input_audio_transcription.completed",
        "response.output_audio_transcript.done",
        "response.audio_transcript.done",
    }:
        value = event.get("transcript") or event.get("text")
        return str(value).strip() if value else None
    return None


def _realtime_text_delta(event: dict[str, Any]) -> str | None:
    event_type = str(event.get("type") or "")
    if event_type in {"response.output_text.delta", "response.text.delta"}:
        value = event.get("delta")
        return str(value) if value else None
    return None


def _json_response(response: httpx.Response) -> dict[str, Any]:
    if not response.content:
        return {}
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        value = {}
        payload[key] = value
    return value


def _audio_quality_tag(record: DidwwBridgeCallRecord, key: str) -> str | None:
    normalized_key = str(key or "").strip()
    if not normalized_key:
        return None
    for candidate in (
        record.metadata,
        record.contact,
        record.realtime,
        record.projection,
    ):
        if not isinstance(candidate, dict):
            continue
        value = candidate.get(normalized_key)
        nested_metadata = candidate.get("metadata")
        if value is None and isinstance(nested_metadata, dict):
            value = nested_metadata.get(normalized_key)
        if value is not None:
            return _debug_snippet(value)
    return None


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_list_item(value: object) -> str:
    return str(value or "").strip()


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def _env_optional(name: str, default: str | None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value or None


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    return int(value)


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    return float(value)


def _env_list(name: str, default: list[str]) -> list[str]:
    value = os.environ.get(name, "").strip()
    if not value:
        return list(default)
    return [part.strip() for part in value.split(",") if part.strip()]


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value for {name}: {value!r}")
