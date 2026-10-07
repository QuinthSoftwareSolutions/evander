"""OpenAI Decisions adapter using the published POST /v1/decisions contract."""

from __future__ import annotations

import math
from typing import Any

from evander.classification.jev import JevProvider
from evander.classification.models import (
    ClassificationError,
    DecisionsProviderSettings,
    json_bytes,
    validate_answers,
)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json_bytes(value).decode("utf-8")


class DecisionsProvider(JevProvider):
    """Reuse the bounded HTTP transport with Decisions-specific schemas and errors."""

    display_name = "OpenAI Decisions"
    error_name = "OpenAI Decisions"
    available = True
    default_settings = DecisionsProviderSettings()
    reports_option_probabilities = True
    usage_provider = "openai"
    usage_endpoint_identifier = "decisions"
    endpoint = "https://api.openai.com/v1/decisions"

    def _request_body(self, request) -> bytes:
        if request.model != "gpt-6-luna":
            raise ClassificationError("OpenAI Decisions currently requires model gpt-6-luna")
        questions = []
        for name, question in request.questions.items():
            instructions = _text(question.instructions)
            wire = {"name": name, "type": question.type, "instructions": instructions}
            if question.type == "choice":
                wire["choices"] = [
                    {"value": value, "description": _text(description)}
                    for value, description in question.criteria.items()
                ]
            elif question.type == "score":
                wire["levels"] = [
                    {"label": str(index), "description": _text(description)}
                    for index, description in enumerate(question.criteria)
                ]
            else:
                wire["type"] = "predicate"
                if question.criteria:
                    wire["instructions"] += "\nBoolean criteria: " + _text(question.criteria)
            questions.append(wire)
        # JSON state is shared evidence, not a Responses conversation or tool payload.
        return json_bytes(
            {"model": request.model, "input": _text(request.state), "questions": questions}
        )

    @staticmethod
    def _normalize(request, payload):
        if not isinstance(payload.get("model"), str) or not payload["model"].strip():
            raise ClassificationError("OpenAI Decisions response is missing model identity")
        raw = payload.get("answers")
        if not isinstance(raw, list) or len(raw) != len(request.questions):
            raise ClassificationError("OpenAI Decisions answers do not match requested questions")
        answers = {}
        for (name, question), answer in zip(request.questions.items(), raw, strict=True):
            if not isinstance(answer, dict) or answer.get("name") != name:
                raise ClassificationError(
                    "OpenAI Decisions returned an incorrect question name or order"
                )
            if answer.get("type") == "refusal":
                raise ClassificationError(
                    "OpenAI Decisions refused to answer a classification question"
                )
            expected_type = "predicate" if question.type == "boolean" else question.type
            if answer.get("type") != expected_type:
                raise ClassificationError("OpenAI Decisions returned an incorrect answer type")
            if question.type == "boolean":
                answers[name] = {"type": "boolean", "probability_true": answer.get("probability")}
                continue
            if answer.get("confidence") is None:
                raise ClassificationError("OpenAI Decisions answer is missing confidence")
            probabilities = _distribution(question, answer.get("probabilities"))
            answers[name] = {
                "type": question.type,
                question.type: answer.get(question.type),
                "confidence": answer["confidence"],
                "probabilities": probabilities,
            }
        normalized = validate_answers(request.questions, answers)
        for answer in normalized.values():
            if answer["type"] == "score" and not math.isclose(
                answer["score"],
                sum(
                    int(index) * probability
                    for index, probability in answer["probabilities"].items()
                ),
                abs_tol=0.001,
            ):
                raise ClassificationError(
                    "OpenAI Decisions score does not match its level probabilities"
                )
        return {
            "answers": normalized,
            "model": payload["model"],
            "usage": _usage(payload.get("usage")),
        }


def _distribution(question, entries) -> dict[str, Any]:
    count = len(question.criteria)
    if not isinstance(entries, list) or len(entries) != count:
        raise ClassificationError("OpenAI Decisions distribution does not match supplied options")
    probabilities = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ClassificationError("OpenAI Decisions returned an invalid probability entry")
        value = entry.get("value")
        if question.type == "score":
            if type(value) is not int or not 0 <= value < count or entry.get("label") != str(value):
                raise ClassificationError("OpenAI Decisions returned an invalid score level")
            value = str(value)
        elif not isinstance(value, str) or value not in question.criteria:
            raise ClassificationError("OpenAI Decisions returned an invalid choice value")
        if value in probabilities:
            raise ClassificationError("OpenAI Decisions returned duplicate probability entries")
        probabilities[value] = entry.get("probability")
    return probabilities


def _usage(value) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ClassificationError("OpenAI Decisions response is missing valid token usage")

    def tokens(item):
        if type(item) is not int or item < 0:
            raise ClassificationError("OpenAI Decisions response is missing valid token usage")
        return item

    usage = {key: tokens(value.get(key)) for key in ("input_tokens", "output_tokens")}
    for key in ("total_tokens", "compute_units"):
        if value.get(key) is not None:
            usage[key] = tokens(value[key])
    for key, fields in (
        ("input_tokens_details", ("cached_tokens", "cache_write_tokens")),
        ("output_tokens_details", ("reasoning_tokens",)),
    ):
        if key in value:
            if not isinstance(value[key], dict):
                raise ClassificationError("OpenAI Decisions response is missing valid token usage")
            usage[key] = {field: tokens(value[key].get(field)) for field in fields}
    return usage
