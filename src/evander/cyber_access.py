"""Explicit Cyber program selection; account and model authorization stays upstream."""

from typing import Any, Literal

from evander.reasoning_effort import session_agent_harness

CyberAccessProgram = Literal["standard", "daybreak_blue", "daybreak_red"]
CYBER_ACCESS_PROGRAM_UNSET = object()
CYBER_ACCESS_PROGRAM_RPC = {
    "standard": "standard",
    "daybreak_blue": "daybreakBlue",
    "daybreak_red": "daybreakRed",
}


def normalize_cyber_access_program(value: Any) -> CyberAccessProgram | None:
    if value is None or isinstance(value, str) and value in CYBER_ACCESS_PROGRAM_RPC:
        return value
    raise ValueError("cyber_access_program must be standard, daybreak_blue, daybreak_red, or null")


def session_cyber_access_program(session: Any, config: Any = None) -> CyberAccessProgram | None:
    program = normalize_cyber_access_program(getattr(session, "cyber_access_program", None))
    metadata = getattr(session, "metadata", {}) or {}
    route = metadata.get("model_provider_mode") or getattr(config, "model_provider_mode", None)
    if program is not None and (
        session_agent_harness(session, config) != "codex" or route not in (None, "openai_public")
    ):
        raise ValueError("cyber_access_program is supported only for public OpenAI Codex agents")
    return program
