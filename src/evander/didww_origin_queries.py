from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from .didww_bridge_protocol import (
    OriginQueryState,
    RealtimeFunctionCall,
    _MISSING_TASK_CLARIFICATION_INSTRUCTIONS,
    _MISSING_TASK_CLARIFICATION_RESPONSE_KEY,
    _ORIGIN_QUERY_ANSWER_RESPONSE_INSTRUCTIONS,
    _ORIGIN_QUERY_HOLD_RESPONSE_INSTRUCTIONS,
    _ORIGIN_QUERY_TIMEOUT_RESPONSE_INSTRUCTIONS,
    _PROJECTED_TASK_RESPONSE_INSTRUCTIONS,
    _PROJECTED_TASK_RESPONSE_KEY,
    _debug_snippet,
    _instruction_debug_metadata,
    _origin_query_response_key,
    _origin_query_tool_output,
    _projected_startup_task_answer,
    _projected_task_answer,
    _voice_query_key,
)

if TYPE_CHECKING:
    from .didww_sip_bridge import OpenAIRealtimeSession, VoiceMediaControllerClient


logger = logging.getLogger("evander.didww_sip_bridge")


def _origin_query_ack_grace_seconds() -> float:
    # Retain the bridge module as the patchable timing seam used by callers and tests.
    from . import didww_sip_bridge

    return didww_sip_bridge._ORIGIN_QUERY_ACK_GRACE_SECONDS


class DidwwOriginQueryHandling:
    """Delegated origin-query lifecycle for live DIDWW calls."""

    async def _handle_realtime_function_call(
        self,
        voice_session_id: str,
        function_call: RealtimeFunctionCall,
        realtime: OpenAIRealtimeSession,
        media_controller: VoiceMediaControllerClient,
        query_tasks: set[asyncio.Task[None]],
        stop_event: asyncio.Event,
        *,
        delegated_queries: dict[str, str] | None = None,
        origin_queries_by_question: dict[str, OriginQueryState] | None = None,
        origin_queries_by_id: dict[str, OriginQueryState] | None = None,
        request_response: Callable[..., Awaitable[None]] | None = None,
        discard_response_keys: Callable[..., Awaitable[int]] | None = None,
        cancel_goodbye_idle_timer: Callable[..., bool] | None = None,
    ) -> None:
        if request_response is None:

            async def request_response(
                instructions: str | None = None,
                **_: Any,
            ) -> None:
                await realtime.request_response(instructions)

        if origin_queries_by_question is None:
            origin_queries_by_question = {}
        if origin_queries_by_id is None:
            origin_queries_by_id = {}
        if discard_response_keys is None:

            async def discard_response_keys(
                keys: set[str],
                **_: Any,
            ) -> int:
                return 0

        if function_call.name != "query_origin_agent":
            await realtime.send_tool_output(
                call_id=function_call.call_id,
                output={"status": "failed", "error": f"Unsupported tool: {function_call.name}"},
            )
            await request_response("Tell the caller that tool is unavailable.")
            return
        question = str(
            function_call.arguments.get("question") or function_call.arguments.get("query") or ""
        ).strip()
        record = self.store.get(voice_session_id)
        projection = record.projection if record is not None else {}
        if not question:
            projection_answer = _projected_startup_task_answer(projection)
            if projection_answer is not None:
                self._append_debug_event(
                    voice_session_id,
                    "origin_query.empty_question_answered_from_projection",
                    metadata={
                        "realtime_call_id": function_call.call_id,
                        "reason": "projected_task",
                        "answer_snippet": _debug_snippet(projection_answer),
                    },
                )
                await realtime.send_tool_output(
                    call_id=function_call.call_id,
                    output={
                        "status": "answered",
                        "source": "projection",
                        "answer": projection_answer,
                    },
                )
                await request_response(
                    _PROJECTED_TASK_RESPONSE_INSTRUCTIONS,
                    reason="projected_task",
                    key=_PROJECTED_TASK_RESPONSE_KEY,
                )
                return
            await realtime.send_tool_output(
                call_id=function_call.call_id,
                output={"status": "failed", "error": "question is required"},
            )
            await request_response(
                _MISSING_TASK_CLARIFICATION_INSTRUCTIONS,
                reason="missing_task_clarification",
                key=_MISSING_TASK_CLARIFICATION_RESPONSE_KEY,
            )
            return
        projection_answer = _projected_task_answer(
            question,
            projection,
        )
        if projection_answer is not None:
            self._append_debug_event(
                voice_session_id,
                "origin_query.answered_from_projection",
                metadata={
                    "realtime_call_id": function_call.call_id,
                    "reason": "projected_task",
                    "question_snippet": _debug_snippet(question),
                    "answer_snippet": _debug_snippet(projection_answer),
                },
            )
            await realtime.send_tool_output(
                call_id=function_call.call_id,
                output={
                    "status": "answered",
                    "source": "projection",
                    "answer": projection_answer,
                },
            )
            await request_response(
                _PROJECTED_TASK_RESPONSE_INSTRUCTIONS,
                reason="projected_task",
                key=_PROJECTED_TASK_RESPONSE_KEY,
            )
            return
        query_key = _voice_query_key(question)
        argument_query_id = str(function_call.arguments.get("query_id") or "").strip()
        state = origin_queries_by_id.get(argument_query_id) if argument_query_id else None
        state = state or origin_queries_by_question.get(query_key)
        if state is not None:
            if state.status in {"failed", "timeout"} and state.failure_reused:
                origin_queries_by_question.pop(state.question_key, None)
                origin_queries_by_id.pop(state.query_id, None)
                if delegated_queries is not None:
                    delegated_queries.pop(query_key, None)
                    delegated_queries.pop(state.question_key, None)
            else:
                await realtime.send_tool_output(
                    call_id=function_call.call_id,
                    output=_origin_query_tool_output(state.query, deduplicated=True),
                )
                if state.status in {"failed", "timeout"}:
                    state.failure_reused = True
                if state.status != "pending":
                    await self._queue_terminal_origin_query_response(
                        voice_session_id,
                        state,
                        request_response=request_response,
                    )
                return
        if not media_controller.configured:
            await realtime.send_tool_output(
                call_id=function_call.call_id,
                output={"status": "failed", "error": "lookup is not configured"},
            )
            await request_response(
                "Tell the caller you cannot look that up right now. "
                "Do not mention internal agent handoffs."
            )
            return
        if delegated_queries is not None and query_key in delegated_queries:
            await realtime.send_tool_output(
                call_id=function_call.call_id,
                output={
                    "status": "pending",
                    "query_id": delegated_queries[query_key],
                    "deduplicated": True,
                },
            )
            return
        transcript_excerpt = record.transcript if record is not None else None
        try:
            query = await media_controller.create_query(
                voice_session_id,
                question=question,
                transcript_excerpt=transcript_excerpt,
                metadata={
                    "reason": function_call.arguments.get("reason"),
                    "realtime_call_id": function_call.call_id,
                },
                deadline_seconds=int(self.config.voice_query_timeout_seconds),
            )
        except Exception as exc:
            logger.warning(
                "voice_query_create_failed voice_session_id=%s call_id=%s",
                voice_session_id,
                function_call.call_id,
                exc_info=True,
            )
            self._append_debug_event(
                voice_session_id,
                "origin_query.create_failed",
                metadata={
                    "reason": type(exc).__name__,
                    "realtime_call_id": function_call.call_id,
                    "question_snippet": _debug_snippet(question),
                    "message_snippet": _debug_snippet(exc),
                },
            )
            await realtime.send_tool_output(
                call_id=function_call.call_id,
                output={"status": "failed", "error": str(exc)},
            )
            await request_response(
                "Tell the caller you cannot complete that lookup right now. "
                "Do not mention internal agent handoffs."
            )
            return
        query_id = str(query.get("query_id") or "").strip()
        if delegated_queries is not None and query_id:
            delegated_queries[query_key] = query_id
        if cancel_goodbye_idle_timer is not None:
            cancel_goodbye_idle_timer(reason="origin_query_created")
        self._append_debug_event(
            voice_session_id,
            "origin_query.created",
            metadata={
                "query_id": query_id,
                "realtime_call_id": function_call.call_id,
                "reason": function_call.arguments.get("reason"),
                "question_snippet": _debug_snippet(question),
            },
        )
        self.store.append_transcript_event(
            voice_session_id,
            speaker="assistant",
            text=f"Lookup requested: {question}",
            metadata={"query_id": query_id, "event_type": "voice_query_created"},
        )
        await realtime.send_tool_output(
            call_id=function_call.call_id,
            output=_origin_query_tool_output(query),
        )
        status = str(query.get("status") or "pending").strip() or "pending"
        state = OriginQueryState(
            query_id=query_id,
            question=question,
            question_key=query_key,
            status=status,
            query=dict(query),
            completed_event=asyncio.Event(),
        )
        origin_queries_by_question[query_key] = state
        if query_id:
            origin_queries_by_id[query_id] = state
        await discard_response_keys(
            {_PROJECTED_TASK_RESPONSE_KEY},
            reason="origin_query_created",
        )
        if status in {"answered", "failed", "timeout"}:
            await self._handle_terminal_origin_query_result(
                voice_session_id,
                state,
                query,
                realtime,
                request_response=request_response,
                event_type="origin_query.answered"
                if status != "timeout"
                else "origin_query.timeout",
                started_at=asyncio.get_running_loop().time(),
            )
            return
        ack_task = asyncio.create_task(
            self._queue_delayed_origin_query_ack(
                voice_session_id,
                state,
                stop_event,
                request_response=request_response,
            )
        )
        query_tasks.add(ack_task)
        ack_task.add_done_callback(lambda completed: query_tasks.discard(completed))
        poll_task = asyncio.create_task(
            self._poll_origin_query_result(
                voice_session_id,
                query_id,
                media_controller,
                realtime,
                stop_event,
                state=state,
                request_response=request_response,
            )
        )
        query_tasks.add(poll_task)
        poll_task.add_done_callback(lambda completed: query_tasks.discard(completed))

    async def _poll_origin_query_result(
        self,
        voice_session_id: str,
        query_id: str,
        media_controller: VoiceMediaControllerClient,
        realtime: OpenAIRealtimeSession,
        stop_event: asyncio.Event,
        *,
        state: OriginQueryState | None = None,
        request_response: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        if request_response is None:

            async def request_response(
                instructions: str | None = None,
                **_: Any,
            ) -> None:
                await realtime.request_response(instructions)

        loop = asyncio.get_running_loop()
        started_at = loop.time()
        backend_deadline = started_at + self.config.voice_query_timeout_seconds
        caller_deadline = started_at + min(
            self.config.voice_query_caller_patience_seconds,
            self.config.voice_query_timeout_seconds,
        )
        caller_patience_can_expire_first = caller_deadline < backend_deadline
        if state is None:
            state = OriginQueryState(
                query_id=query_id,
                question="",
                question_key="",
                query={"query_id": query_id, "status": "pending"},
                completed_event=asyncio.Event(),
            )
        poll_attempt = 0
        caller_patience_response_queued = False
        while not stop_event.is_set():
            now = loop.time()
            if now >= backend_deadline:
                break
            sleep_deadline = backend_deadline
            if caller_patience_can_expire_first and not caller_patience_response_queued:
                sleep_deadline = min(sleep_deadline, caller_deadline)
            await asyncio.sleep(
                min(
                    self.config.voice_query_poll_interval_seconds,
                    max(0.0, sleep_deadline - now),
                )
            )
            if stop_event.is_set():
                break
            if loop.time() >= backend_deadline:
                break
            query = await media_controller.get_query(voice_session_id, query_id)
            poll_attempt += 1
            status = str(query.get("status") or "").strip()
            if status in {"answered", "failed"}:
                await self._handle_terminal_origin_query_result(
                    voice_session_id,
                    state,
                    query,
                    realtime,
                    request_response=request_response,
                    event_type="origin_query.answered",
                    started_at=started_at,
                )
                return
            self._append_debug_event(
                voice_session_id,
                "origin_query.poll_pending",
                metadata={
                    "query_id": query_id,
                    "reason": status or "pending",
                    "status": status or "pending",
                    "poll_attempt": poll_attempt,
                    "elapsed_ms": int(max(0.0, loop.time() - started_at) * 1000),
                },
            )
            now = loop.time()
            if (
                caller_patience_can_expire_first
                and not caller_patience_response_queued
                and now >= caller_deadline
                and now < backend_deadline
            ):
                caller_patience_response_queued = True
                await self._queue_origin_query_caller_patience_response(
                    voice_session_id,
                    state,
                    request_response=request_response,
                    started_at=started_at,
                )
        if not stop_event.is_set():
            query = await media_controller.mark_query_timeout(voice_session_id, query_id)
            await self._handle_terminal_origin_query_result(
                voice_session_id,
                state,
                query,
                realtime,
                request_response=request_response,
                event_type="origin_query.timeout",
                started_at=started_at,
            )

    async def _queue_origin_query_caller_patience_response(
        self,
        voice_session_id: str,
        state: OriginQueryState,
        *,
        request_response: Callable[..., Awaitable[None]],
        started_at: float,
    ) -> None:
        if state.timeout_response_queued:
            return
        state.timeout_response_queued = True
        loop = asyncio.get_running_loop()
        self._append_debug_event(
            voice_session_id,
            "origin_query.caller_patience_expired",
            metadata={
                "query_id": state.query_id,
                "reason": "caller_patience_expired",
                "status": state.status,
                "elapsed_ms": int(max(0.0, loop.time() - started_at) * 1000),
            },
        )
        self._append_debug_event(
            voice_session_id,
            "origin_query.response_requested",
            metadata={
                "query_id": state.query_id,
                "reason": "caller_patience_expired",
                **_instruction_debug_metadata(_ORIGIN_QUERY_TIMEOUT_RESPONSE_INSTRUCTIONS),
            },
        )
        await request_response(
            _ORIGIN_QUERY_TIMEOUT_RESPONSE_INSTRUCTIONS,
            reason="caller_patience_expired",
            key=_origin_query_response_key(state.query_id, "timeout"),
            supersede_keys={
                _origin_query_response_key(state.query_id, "ack"),
            },
        )

    async def _queue_delayed_origin_query_ack(
        self,
        voice_session_id: str,
        state: OriginQueryState,
        stop_event: asyncio.Event,
        *,
        request_response: Callable[..., Awaitable[None]],
    ) -> None:
        completed = state.completed_event
        if completed is not None:
            try:
                await asyncio.wait_for(
                    completed.wait(),
                    timeout=_origin_query_ack_grace_seconds(),
                )
                return
            except TimeoutError:
                pass
        else:
            await asyncio.sleep(_origin_query_ack_grace_seconds())
        if (
            stop_event.is_set()
            or state.status != "pending"
            or state.ack_response_queued
            or state.timeout_response_queued
        ):
            return
        state.ack_response_queued = True
        response_instructions = _ORIGIN_QUERY_HOLD_RESPONSE_INSTRUCTIONS
        self._append_debug_event(
            voice_session_id,
            "origin_query.hold_offer_requested",
            metadata={
                "query_id": state.query_id,
                "reason": "query_pending_hold_offer",
                **_instruction_debug_metadata(response_instructions),
            },
        )
        await request_response(
            response_instructions,
            reason="query_pending_hold_offer",
            key=_origin_query_response_key(state.query_id, "ack"),
            supersede_keys={_PROJECTED_TASK_RESPONSE_KEY},
        )

    async def _handle_terminal_origin_query_result(
        self,
        voice_session_id: str,
        state: OriginQueryState,
        query: dict[str, Any],
        realtime: OpenAIRealtimeSession,
        *,
        request_response: Callable[..., Awaitable[None]],
        event_type: str,
        started_at: float,
    ) -> None:
        loop = asyncio.get_running_loop()
        status = str(query.get("status") or "").strip() or state.status
        if event_type == "origin_query.timeout":
            status = "timeout"
        state.status = status
        state.query = dict(query)
        if state.completed_event is not None:
            state.completed_event.set()
        answer = str(query.get("answer") or query.get("error") or "").strip()
        self._append_debug_event(
            voice_session_id,
            event_type,
            metadata={
                "query_id": state.query_id,
                "reason": status,
                "status": status,
                "elapsed_ms": int(max(0.0, loop.time() - started_at) * 1000),
                "answer_snippet": _debug_snippet(answer),
            },
        )
        if not state.result_injected:
            await realtime.inject_origin_query_result(query, request_response=False)
            state.result_injected = True
            self._append_debug_event(
                voice_session_id,
                "origin_query.result_injected",
                metadata={
                    "query_id": state.query_id,
                    "reason": status,
                    "status": status,
                    "answer_snippet": _debug_snippet(answer),
                },
            )
        await self._queue_terminal_origin_query_response(
            voice_session_id,
            state,
            request_response=request_response,
        )
        if answer and not state.transcript_recorded:
            state.transcript_recorded = True
            self.store.append_transcript_event(
                voice_session_id,
                speaker="origin_agent",
                text=answer,
                metadata={"query_id": state.query_id, "event_type": "voice_query_answered"},
            )

    async def _queue_terminal_origin_query_response(
        self,
        voice_session_id: str,
        state: OriginQueryState,
        *,
        request_response: Callable[..., Awaitable[None]],
    ) -> None:
        status = state.status
        if status == "timeout":
            if state.timeout_response_queued:
                return
            state.timeout_response_queued = True
            response_instructions = _ORIGIN_QUERY_TIMEOUT_RESPONSE_INSTRUCTIONS
            response_kind = "timeout"
            response_reason = "query_timeout"
        else:
            if state.answer_response_queued:
                return
            state.answer_response_queued = True
            if status == "failed":
                response_instructions = (
                    "Tell the caller, in one short sentence, that you could not complete the lookup "
                    "right now. "
                    "Do not mention internal agent handoffs or say the query is still pending."
                )
            else:
                response_instructions = _ORIGIN_QUERY_ANSWER_RESPONSE_INSTRUCTIONS
            response_kind = "answer"
            response_reason = "query_result"
        supersede_keys = {
            _origin_query_response_key(state.query_id, "ack"),
        }
        if response_kind == "answer":
            supersede_keys.add(_origin_query_response_key(state.query_id, "timeout"))
        self._append_debug_event(
            voice_session_id,
            "origin_query.response_requested",
            metadata={
                "query_id": state.query_id,
                "reason": response_reason,
                **_instruction_debug_metadata(response_instructions),
            },
        )
        await request_response(
            response_instructions,
            reason=response_reason,
            key=_origin_query_response_key(state.query_id, response_kind),
            supersede_keys=supersede_keys,
        )
