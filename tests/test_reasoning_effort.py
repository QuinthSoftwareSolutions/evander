from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from evander.config import GatewayConfig
from evander.effects.providers.agent_session import container_prompt_command
from evander.effects.providers.schemas import (
    AgentCreateEffectDefinition,
    AgentEnqueuePromptEffectDefinition,
    AgentPromptEffectDefinition,
)
from evander.effects.registry import EffectProviderFactory
from evander.models import SessionManifest
from evander.reasoning_effort import resolve_reasoning_effort, validate_reasoning_effort
from test_effect_registry import _AdminServiceStub, _ContainerResolverStub

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "harness,model,route,level",
    [
        ("codex", "gpt-4o", "openai_public", "high"),
        ("codex", "gpt-6-luna", "azure_openai", "ultra"),
        ("codex", "openai/gpt-6-astra", "openrouter", "ultra"),
        ("codex", "gpt-5.4", "openai_public", "max"),
        ("claude", "claude-sonnet-4-6", "anthropic", "xhigh"),
        ("claude", "claude-haiku-4-5", "anthropic", "low"),
        ("copilot", "deployment-alias", None, "minimal"),
    ],
)
def test_known_unsupported_effort_is_rejected(harness, model, route, level) -> None:
    with pytest.raises(ValueError, match="not supported"):
        validate_reasoning_effort(level, harness=harness, model=model, route=route)


@pytest.mark.parametrize(
    "harness,model,route,level",
    [
        ("codex", "gpt-6-astra", "openai_public", "ultra"),
        ("codex", "openai/gpt-6-astra", "openrouter", "max"),
        ("codex", "deployment-alias", "azure_openai", "low"),
        ("codex", "gpt-4o", "local_openai", "minimal"),
        ("claude", "custom-alias", "anthropic", "max"),
        ("copilot", "claude-opus-4.7", None, "xhigh"),
    ],
)
def test_native_effort_is_forwarded_without_remapping(harness, model, route, level) -> None:
    assert (
        validate_reasoning_effort(f" {level.upper()} ", harness=harness, model=model, route=route)
        == level
    )


@pytest.mark.parametrize("harness", ["codex", "claude", "copilot"])
def test_prompt_override_is_one_turn_and_session_default_is_restored(tmp_path, harness) -> None:
    admin = _AdminServiceStub([], [], [], [], [])
    session = admin._sessions["session-42"]
    session.model = "claude-sonnet-4-6" if harness == "claude" else "gpt-6-sol"
    session.reasoning_effort = "medium"
    session.metadata["agent_provider"] = harness
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(GatewayConfig(runtime_dir=tmp_path)),
        admin_service=admin,
    )
    for override in ["low", None]:
        factory.build(
            AgentPromptEffectDefinition(
                id="prompt",
                display_name="Prompt",
                type="effect:agent:prompt",
                session_id="session-42",
                prompt="Continue",
                resume_thread_id="thread-existing",
                reasoning_effort=override,
            )
        ).execute()
    assert [
        (call["reasoning_effort"], call["reasoning_effort_source"]) for call in admin.resume_calls
    ] == [("low", "prompt"), ("medium", "session")]
    assert admin._sessions["session-42"].reasoning_effort == "medium"
    assert all(call["resume_thread_id"] == "thread-existing" for call in admin.resume_calls)


@pytest.mark.parametrize(
    "schema",
    [AgentCreateEffectDefinition, AgentPromptEffectDefinition, AgentEnqueuePromptEffectDefinition],
)
def test_effect_effort_accepts_bindings_but_rejects_invalid_literals(schema) -> None:
    payload = {
        "id": "node",
        "display_name": "Node",
        "type": schema.model_fields["type"].annotation.__args__[0],
        "prompt": "Continue",
    }
    assert schema.model_validate(payload).reasoning_effort is None
    assert (
        schema.model_validate(payload | {"reasoning_effort": " HIGH "}).reasoning_effort == "high"
    )
    binding = "${{ trigger.level }}"
    assert (
        schema.model_validate(payload | {"reasoning_effort": binding}).reasoning_effort == binding
    )
    with pytest.raises(ValidationError, match="reasoning_effort"):
        schema.model_validate(payload | {"reasoning_effort": "fastest"})


@pytest.mark.parametrize(
    "harness,flag", [("codex", None), ("claude", "--effort"), ("copilot", "--reasoning-effort")]
)
def test_vm_runners_pass_effort_on_resumes_and_restore_implicit_default(
    tmp_path, harness, flag
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    args_log = tmp_path / "args.jsonl"
    fake = fake_bin / harness
    fake.write_text("""#!/usr/bin/env python3
import json, os, sys
if sys.argv[1:2] in (["features"], ["mcp"]):
    sys.exit(0)
with open(os.environ["ARG_LOG"], "a") as output:
    output.write(json.dumps(sys.argv[1:]) + "\\n")
if os.environ["TEST_HARNESS"] == "claude":
    sys.stdin.read()
print('{"type":"result","result":"Done"}')
""")
    fake.chmod(0o755)
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "ARG_LOG": str(args_log),
        "TEST_HARNESS": harness,
        "AGENT_WEB_SEARCH_MODE": "disabled",
        "EVANDER_MCP_GATEWAY_URL": "",
    }
    runner = ROOT / "scripts" / "lib" / f"agent_runner_{harness}.sh"
    subprocess.run(
        [
            "bash",
            "-c",
            """set -e
source "$1"
AGENT_REASONING_EFFORT=low AGENT_REASONING_EFFORT_SOURCE=prompt agent_runner_"$2"_resume_last --model custom-model Continue
AGENT_REASONING_EFFORT= AGENT_REASONING_EFFORT_SOURCE=default agent_runner_"$2"_resume_last --model custom-model Continue
""",
            "test",
            str(runner),
            harness,
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    first, second = [json.loads(line) for line in args_log.read_text().splitlines()]
    if flag:
        assert first[first.index(flag) + 1] == "low"
        assert flag not in second
    else:
        assert 'model_reasoning_effort="low"' in first
        assert "model_supports_reasoning_summaries=true" in first
        assert 'model_reasoning_effort="high"' in second
        assert 'model_reasoning_effort="low"' not in second


@pytest.mark.parametrize("harness", ["codex", "claude"])
def test_container_commands_keep_native_effort_on_new_and_resumed_turns(harness) -> None:
    session = SessionManifest(
        session_id="test",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        model="custom-alias",
        reasoning_effort="medium",
    )
    for resume in [None, "thread-existing"]:
        for override, expected in [("low", "low"), (None, "medium")]:
            command = container_prompt_command(
                prompt="Continue",
                model=session.model,
                session=session,
                agent_provider=harness,
                reasoning_effort=override,
                resume_thread_id=resume,
            )
            native = (
                f'model_reasoning_effort="{expected}"'
                if harness == "codex"
                else f"--effort {expected}"
            )
            assert native in command


def test_captured_null_default_does_not_inherit_a_later_session_setting() -> None:
    session = SessionManifest(
        session_id="test", allowed_tools=[], expires_at=datetime.now(UTC), reasoning_effort="high"
    )
    assert (
        resolve_reasoning_effort(
            {"reasoning_effort": None, "reasoning_effort_source": "default"},
            session=session,
            harness="claude",
            model="claude-sonnet-4-6",
        ).value
        is None
    )
    assert (
        resolve_reasoning_effort(
            {}, session=session, harness="claude", model="claude-sonnet-4-6"
        ).value
        == "high"
    )
