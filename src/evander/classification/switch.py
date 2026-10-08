"""Shared Smart switch preparation and routing for workflows and sample tests."""

from __future__ import annotations

from typing import Any

from pydantic import Field, JsonValue, ValidationError, field_validator, model_validator

from evander.classification.models import ClassificationError, PreparedEvaluation, StrictModel


def _port(value: str) -> str:
    value = value.strip()
    if not value or value == "error" or "${{" in value or "$(" in value:
        raise ValueError("Smart switch ports must be nonempty static names other than 'error'")
    return value


class ClassifierSwitchCase(StrictModel):
    port: str
    label: str | None = None
    description: str = Field(min_length=1)

    _validate_port = field_validator("port")(_port)

    @field_validator("description")
    @classmethod
    def nonblank_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Branch descriptions must not be blank")
        return value


class ClassifierSwitchConfig(StrictModel):
    state: str | dict[str, JsonValue] | list[JsonValue]
    instructions: str = Field(min_length=1)
    cases: list[ClassifierSwitchCase] = Field(min_length=1)
    default_port: str
    default_description: str = "None of the described routes clearly applies."
    provider: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1)
    min_probability: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)

    _validate_port = field_validator("default_port")(_port)

    @field_validator("instructions", "default_description")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Routing instructions and default descriptions must not be blank")
        return value

    @field_validator("min_probability", mode="before")
    @classmethod
    def probability_is_number(cls, value):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError("min_probability must be a number")
        return value

    @model_validator(mode="after")
    def unique_ports(self):
        ports = [case.port for case in self.cases] + [self.default_port]
        if len(set(ports)) != len(ports):
            raise ValueError("Smart switch branch and default ports must be unique")
        return self


class PreparedSwitch(StrictModel):
    config: ClassifierSwitchConfig
    evaluation: PreparedEvaluation


def _check_probability_support(service, prepared: PreparedSwitch) -> None:
    provider = service.providers.get(prepared.evaluation.provider)
    if (
        prepared.config.min_probability is not None
        and getattr(provider, "reports_option_probabilities", None) is False
    ):
        raise ClassificationError("Smart switch probability cutoff requires option probabilities")


def prepare_switch(service, settings, payload: Any) -> PreparedSwitch:
    try:
        config = ClassifierSwitchConfig.model_validate(payload)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
            for e in exc.errors(include_input=False, include_context=False)
        )
        raise ClassificationError(f"Invalid Smart switch configuration: {details}") from None
    criteria = {case.port: case.description for case in config.cases}
    criteria[config.default_port] = config.default_description
    evaluation = service.prepare(
        settings,
        {
            "state": config.state,
            "provider": config.provider,
            "model": config.model,
            "questions": {
                "route": {
                    "type": "choice",
                    "instructions": config.instructions,
                    "criteria": criteria,
                }
            },
        },
    )
    prepared = PreparedSwitch(config=config, evaluation=evaluation)
    _check_probability_support(service, prepared)
    return prepared


def execute_switch(service, settings, prepared: PreparedSwitch, *, attribution=None):
    _check_probability_support(service, prepared)
    result = service.execute(settings, prepared.evaluation, attribution=attribution)
    answer = result["answers"]["route"]
    choice = answer["choice"]
    port = choice
    reason = "default" if choice == prepared.config.default_port else "choice"
    minimum = prepared.config.min_probability
    if minimum is not None:
        probabilities = answer.get("probabilities")
        if probabilities is None:
            raise ClassificationError(
                "Smart switch probability cutoff requires option probabilities"
            )
        if probabilities[choice] < minimum:
            port, reason = prepared.config.default_port, "below_probability"
    return {**result, "selected_choice": choice, "selected_port": port, "routing_reason": reason}
