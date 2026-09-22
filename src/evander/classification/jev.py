from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from evander.classification.models import ClassificationError, json_bytes, validate_answers
from evander.privacy.providers import check_provider_dispatch


class JevProvider:
    supported_question_types = frozenset({"choice", "score", "boolean"})
    endpoint = "https://api.typesafe.ai/v1/systemone"

    def __init__(self, *, transport=None, clock=time.monotonic, sleep=None):
        self.transport = transport
        self.clock = clock
        self.sleep = sleep

    def evaluate(self, request, settings, api_key, record_attempt) -> dict[str, Any]:
        return asyncio.run(self._evaluate_with_deadline(request, settings, api_key, record_attempt))

    async def _evaluate_with_deadline(self, request, settings, api_key, record_attempt):
        try:
            # HTTPX timeouts are per I/O operation; this also bounds slow streams and retries.
            async with asyncio.timeout(settings.timeout_seconds):
                return await self._evaluate(request, settings, api_key, record_attempt)
        except TimeoutError:
            raise ClassificationError("Classification evaluation deadline exceeded") from None

    async def _evaluate(self, request, settings, api_key, record_attempt):
        questions = {}
        for key, question in request.questions.items():
            data = question.model_dump(mode="json", exclude_none=True)
            if question.type == "boolean":
                data["type"] = "noul"
            questions[key] = data
        body = json_bytes({"state": request.state, "model": request.model, "questions": questions})
        if len(body) > settings.max_request_bytes:
            raise ClassificationError("Classification request exceeds max_request_bytes")
        deadline = self.clock() + settings.timeout_seconds
        async with httpx.AsyncClient(
            transport=self.transport, follow_redirects=False, trust_env=False
        ) as client:
            for attempt in range(settings.max_retries + 1):
                check_provider_dispatch()
                started = self.clock()
                remaining = deadline - started
                if remaining <= 0:
                    raise ClassificationError("Classification evaluation deadline exceeded")
                payload, content, status, http_status = {}, bytearray(), "error", None
                retryable, delay = False, min(0.5 * 2**attempt, 5.0)
                try:
                    async with client.stream(
                        "POST",
                        self.endpoint,
                        content=body,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                            "Accept-Encoding": "identity",
                        },
                        timeout=httpx.Timeout(remaining, connect=min(5.0, remaining)),
                    ) as response:
                        http_status = response.status_code
                        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                            content.extend(chunk)
                            if len(content) > settings.max_response_bytes:
                                raise ClassificationError(
                                    "Classification response exceeds max_response_bytes"
                                )
                            if self.clock() >= deadline:
                                raise ClassificationError(
                                    "Classification evaluation deadline exceeded"
                                )
                        retryable = http_status == 429 or http_status >= 500
                        delay = _retry_delay(response.headers.get("retry-after"), delay)
                    if http_status != 200:
                        status = "failed"
                        raise ClassificationError(f"Jev returned HTTP {http_status}")
                    try:
                        payload = json.loads(content)
                    except (ValueError, UnicodeError):
                        raise ClassificationError("Jev returned invalid JSON") from None
                    if not isinstance(payload, dict):
                        payload = {}
                        raise ClassificationError("Jev returned an invalid response")
                    normalized = self._normalize(request, payload)
                    status = "success"
                    return normalized
                except httpx.RequestError:
                    retryable = True
                    error = ClassificationError("Jev connection failed or timed out")
                except ClassificationError as exc:
                    error = exc
                finally:
                    record_attempt(
                        status=status,
                        payload=payload,
                        duration_ms=round((self.clock() - started) * 1000),
                        request_body=body,
                        response_body=bytes(content),
                        http_status=http_status,
                    )
                if not retryable or attempt == settings.max_retries:
                    raise error from None
                if delay >= deadline - self.clock():
                    raise ClassificationError(
                        "Classification retry exceeds evaluation deadline"
                    ) from None
                if self.sleep is None:
                    await asyncio.sleep(delay)
                else:
                    self.sleep(delay)
        raise ClassificationError("Classification evaluation failed")

    @staticmethod
    def _normalize(request, payload):
        if not isinstance(payload.get("model"), str) or not payload["model"].strip():
            raise ClassificationError("Jev response is missing model identity")
        raw = payload.get("answers")
        if not isinstance(raw, dict) or set(raw) != set(request.questions):
            raise ClassificationError("Jev answers do not match requested question IDs")
        answers = {}
        for key, question in request.questions.items():
            answer = raw[key]
            expected_type = "noul" if question.type == "boolean" else question.type
            if not isinstance(answer, dict) or answer.get("type") != expected_type:
                raise ClassificationError("Jev returned an incorrect answer type")
            if question.type == "boolean":
                answers[key] = {"type": "boolean", "probability_true": answer.get("noul")}
            else:
                if answer.get("probabilities") is None or answer.get("confidence") is None:
                    raise ClassificationError("Jev answer is missing probabilities or confidence")
                answers[key] = answer
        usage = payload.get("usage")
        if not isinstance(usage, dict) or any(
            type(usage.get(k)) is not int or usage[k] < 0 for k in ("input_tokens", "output_tokens")
        ):
            raise ClassificationError("Jev response is missing valid token usage")
        return {
            "answers": validate_answers(request.questions, answers),
            "model": payload["model"],
            "usage": {key: usage[key] for key in ("input_tokens", "output_tokens")},
        }


def _retry_delay(value: str | None, fallback: float) -> float:
    if value:
        try:
            delay = float(value)
            if 0 <= delay < float("inf"):
                return delay
        except ValueError:
            try:
                return max(0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
    return fallback
