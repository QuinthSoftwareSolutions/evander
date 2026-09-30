"""Internal finite-answer mapping, independent of any provider's HTTP contract."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from pydantic import JsonValue

from evander.classification.models import ClassificationError, Question, validate_answers


@dataclass(frozen=True)
class FiniteQuestion:
    instructions: JsonValue
    options: dict[str, JsonValue]


def finite_questions(questions: dict[str, Question]) -> dict[str, FiniteQuestion]:
    """Keep choice IDs, enumerate rubric levels, and expose explicit boolean options.

    These objects are an internal adapter contract, not a Decisions API request schema.
    A future transport must translate them using OpenAI's published contract.
    """
    result = {}
    for key, question in questions.items():
        if question.type == "choice":
            options = question.criteria
        elif question.type == "score":
            options = {str(i): level for i, level in enumerate(question.criteria)}
        else:
            options = {
                "true": "The proposition is true.",
                "false": "The proposition is false.",
                **(question.criteria or {}),
            }
        result[key] = FiniteQuestion(deepcopy(question.instructions), deepcopy(options))
    return result


def normalize_selections(questions: dict[str, Question], selections: Any) -> dict[str, Any]:
    """Validate finite selections without inventing confidence or probabilities."""
    if not isinstance(selections, dict) or set(selections) != set(questions):
        raise ClassificationError("Provider selections do not match requested question IDs")
    answers = {}
    for key, finite in finite_questions(questions).items():
        selection = selections[key]
        if not isinstance(selection, str) or selection not in finite.options:
            raise ClassificationError("Provider selected an answer outside the supplied options")
        question_type = questions[key].type
        if question_type == "choice":
            answer = {"choice": selection}
        elif question_type == "score":
            answer = {"score": int(selection)}
        else:
            answer = {"value": selection == "true"}
        answers[key] = {"type": question_type, **answer}
    return validate_answers(questions, answers)
