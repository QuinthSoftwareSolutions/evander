from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

QuestionType = Literal["choice", "score", "boolean"]
DECISIONS_PROVIDER_ID = "openai_decisions"


class ClassificationError(ValueError):
    """A safe, user-facing configuration, request, or provider error."""


def json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ClassificationError("Classification content must be finite JSON data") from None


def revision(value: Any) -> str:
    return hashlib.sha256(json_bytes(value)).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Question(StrictModel):
    type: QuestionType
    instructions: JsonValue
    criteria: JsonValue = None

    @model_validator(mode="after")
    def validate_question(self) -> Question:
        if not isinstance(self.instructions, (str, dict, list)) or not self.instructions:
            raise ValueError("instructions must be nonempty text, an object, or an array")
        if isinstance(self.instructions, str) and not self.instructions.strip():
            raise ValueError("instructions must not be blank")
        if self.type == "choice":
            if not isinstance(self.criteria, dict) or len(self.criteria) < 2:
                raise ValueError("choice requires at least two named options")
            if any(not key.strip() for key in self.criteria):
                raise ValueError("choice option IDs must be nonempty")
        elif self.type == "score":
            if not isinstance(self.criteria, list) or len(self.criteria) < 2:
                raise ValueError("score requires at least two ordered rubric levels")
        elif self.criteria is not None:
            if not isinstance(self.criteria, dict) or set(self.criteria) - {"true", "false"}:
                raise ValueError("boolean criteria may describe only true and false")
        json_bytes(self.model_dump())
        return self


def validate_question_ids(questions: dict[str, Question]) -> dict[str, Question]:
    if not questions or any(not key.strip() for key in questions):
        raise ValueError("questions requires nonempty question IDs")
    return questions


class EvaluationRequest(StrictModel):
    state: str | dict[str, JsonValue] | list[JsonValue]
    questions: dict[str, Question] | None = None
    profile_id: str | None = Field(default=None, min_length=1)
    provider: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_source(self) -> EvaluationRequest:
        if (self.questions is None) == (self.profile_id is None):
            raise ValueError("provide exactly one of questions or profile_id")
        if self.profile_id is not None and (self.provider is not None or self.model is not None):
            raise ValueError("profile calls cannot override provider, model, or questions")
        if self.questions is not None:
            validate_question_ids(self.questions)
        json_bytes(self.state)
        return self


class ClassificationProfile(StrictModel):
    label: str = ""
    description: str = ""
    enabled: bool = True
    provider: str = Field(default="jev", min_length=1)
    model: str | None = Field(default="jev-1.13.0", min_length=1)
    questions: dict[str, Question] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def decisions_model_default(cls, value):
        if isinstance(value, dict) and value.get("provider") == DECISIONS_PROVIDER_ID:
            # Use the configured Decisions default rather than inheriting Jev's model.
            return {"model": None, **value}
        return value

    @model_validator(mode="after")
    def validate_enabled(self) -> ClassificationProfile:
        if self.enabled:
            validate_question_ids(self.questions)
        return self


class ClassificationProviderSettings(StrictModel):
    enabled: bool = True
    default_model: str | None = Field(default="jev-1.13.0", min_length=1)
    api_key_env: str = Field(default="TYPESAFE_API_KEY", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    timeout_seconds: float = Field(default=30.0, gt=0, le=300, allow_inf_nan=False)
    max_retries: int = Field(default=2, ge=0, le=5)
    max_request_bytes: int = Field(default=1024 * 1024, ge=1024, le=20 * 1024 * 1024)
    max_response_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=20 * 1024 * 1024)


class DecisionsProviderSettings(ClassificationProviderSettings):
    enabled: bool = False
    default_model: str | None = Field(default="gpt-6-luna", min_length=1)
    api_key_env: str = Field(default="OPENAI_API_KEY", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")


class ClassificationSettings(StrictModel):
    enabled: bool = False
    default_provider: str = Field(default="jev", min_length=1)
    providers: dict[str, ClassificationProviderSettings] = Field(
        default_factory=lambda: {
            "jev": ClassificationProviderSettings(),
            DECISIONS_PROVIDER_ID: DecisionsProviderSettings(),
        }
    )
    profiles: dict[str, ClassificationProfile] = Field(default_factory=dict)

    @field_validator("providers", mode="before")
    @classmethod
    def provider_defaults(cls, value):
        if isinstance(value, dict):
            return {
                name: DecisionsProviderSettings.model_validate(settings)
                if name == DECISIONS_PROVIDER_ID
                else settings
                for name, settings in value.items()
            }
        return value

    @field_validator("providers", "profiles")
    @classmethod
    def validate_ids(cls, value):
        if any(not key.strip() for key in value):
            raise ValueError("provider and profile IDs must be nonempty")
        return value


class PreparedEvaluation(StrictModel):
    state: str | dict[str, JsonValue] | list[JsonValue]
    questions: dict[str, Question]
    provider: str
    model: str
    profile_id: str | None = None
    profile_revision: str | None = None


def _number(value: Any, *, minimum: float, maximum: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ClassificationError("Provider returned an invalid numeric answer")
    return float(value)


def validate_answers(questions: dict[str, Question], answers: Any) -> dict[str, Any]:
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise ClassificationError("Provider answers do not match requested question IDs")
    result = {}
    for key, question in questions.items():
        answer = answers[key]
        if not isinstance(answer, dict) or answer.get("type") != question.type:
            raise ClassificationError("Provider returned an incorrect answer type")
        normalized: dict[str, Any] = {"type": question.type}
        options = None
        if question.type == "choice":
            options = set(question.criteria)
            if not isinstance(answer.get("choice"), str) or answer["choice"] not in options:
                raise ClassificationError("Provider returned a choice outside the supplied options")
            normalized["choice"] = answer["choice"]
        elif question.type == "score":
            options = {str(i) for i in range(len(question.criteria))}
            normalized["score"] = _number(answer.get("score"), minimum=0, maximum=len(options) - 1)
            normalized["legend"] = {str(i): value for i, value in enumerate(question.criteria)}
        else:
            if "value" in answer:
                if type(answer["value"]) is not bool:
                    raise ClassificationError("Provider returned an invalid boolean answer")
                normalized["value"] = answer["value"]
            if answer.get("probability_true") is not None:
                normalized["probability_true"] = _number(
                    answer["probability_true"], minimum=0, maximum=1
                )
            if not {"value", "probability_true"} & normalized.keys():
                raise ClassificationError(
                    "Provider boolean answer requires value or probability_true"
                )
        if answer.get("confidence") is not None:
            normalized["confidence"] = _number(answer["confidence"], minimum=0, maximum=1)
        if answer.get("probabilities") is not None:
            probabilities = answer["probabilities"]
            if (
                options is None
                or not isinstance(probabilities, dict)
                or set(probabilities) != options
            ):
                raise ClassificationError(
                    "Provider distribution does not match the supplied options"
                )
            normalized["probabilities"] = {
                option: _number(value, minimum=0, maximum=1)
                for option, value in probabilities.items()
            }
            if not math.isclose(sum(normalized["probabilities"].values()), 1, abs_tol=0.001):
                raise ClassificationError("Provider probabilities do not sum to one")
        result[key] = normalized
    return result


def answer_schema(questions: dict[str, Any]) -> dict[str, Any]:
    """Expose known answer fields to workflow binding validation and editors."""
    properties = {}
    for key, question in questions.items():
        question_type = (
            question.get("type") if isinstance(question, dict) else getattr(question, "type", None)
        )
        if not isinstance(question_type, str) or question_type not in {
            "choice",
            "score",
            "boolean",
        }:
            continue
        fields = {"type": {"type": "string"}}
        if question_type == "boolean":
            fields["value"] = {"type": "boolean"}
            fields["probability_true"] = {"type": "number"}
        else:
            fields[question_type] = {"type": "string" if question_type == "choice" else "number"}
            fields["confidence"] = {"type": "number"}
            fields["probabilities"] = {"type": "object"}
            if question_type == "score":
                fields["legend"] = {"type": "object"}
        properties[key] = {"type": "object", "properties": fields}
    return {"type": "object", "properties": properties, "additionalProperties": {"type": "object"}}
