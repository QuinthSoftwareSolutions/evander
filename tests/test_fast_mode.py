from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_effect_registry import _AdminServiceStub, _ContainerResolverStub, _FakeContainerBackend

from evander.config import GatewayConfig
from evander.effects.providers.agent_session import container_prompt_command
from evander.effects.providers.schemas import (
    AgentCreateEffectDefinition,
    AgentEnqueuePromptEffectDefinition,
    AgentPromptEffectDefinition,
)
from evander.effects.registry import EffectProviderFactory
from evander.fast_mode import resolve_fast_mode
from evander.models import SessionManifest
from evander.repositories.session_manifests import JsonSessionManifestRepository


@pytest.mark.parametrize("default", [True, False, "ultrafast", None])
def test_prompt_speed_override_does_not_change_session_default(tmp_path, default) -> None:
    admin = _AdminServiceStub([], [], [], [], [])
    session = admin._sessions["session-42"]
    session.fast_mode = default
    session.model = "gpt-6.1-sol"
    session.reasoning_effort = "max"
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(GatewayConfig(runtime_dir=tmp_path)),
        admin_service=admin,
    )
    for override in [True, False, "ultrafast", None]:
        factory.build(
            AgentPromptEffectDefinition(
                id="prompt",
                display_name="Prompt",
                type="effect:agent:prompt",
                session_id="session-42",
                prompt="Continue",
                resume_thread_id="existing-thread",
                fast_mode=override,
            )
        ).execute()
    assert [(c["fast_mode"], c["fast_mode_source"]) for c in admin.resume_calls] == [
        (True, "prompt"),
        (False, "prompt"),
        ("ultrafast", "prompt"),
        (
            default if default is not None else False,
            "session" if default is not None else "default",
        ),
    ]
    assert session.fast_mode == default
    assert all(
        c["model"] == "gpt-6.1-sol" and c["reasoning_effort"] == "max" for c in admin.resume_calls
    )


@pytest.mark.parametrize(
    "schema",
    [AgentCreateEffectDefinition, AgentPromptEffectDefinition, AgentEnqueuePromptEffectDefinition],
)
def test_fast_mode_schema_preserves_false_and_accepts_bindings(schema) -> None:
    payload = {
        "id": "node",
        "display_name": "Node",
        "prompt": "Continue",
        "type": schema.model_fields["type"].annotation.__args__[0],
    }
    assert schema.model_validate(payload).fast_mode is None
    assert schema.model_validate(payload | {"fast_mode": False}).fast_mode is False
    assert schema.model_validate(payload | {"fast_mode": "ultrafast"}).fast_mode == "ultrafast"
    binding = "${{trigger.fast_mode}}"
    assert schema.model_validate(payload | {"fast_mode": binding}).fast_mode == binding
    with pytest.raises(ValidationError, match="fast_mode"):
        schema.model_validate(payload | {"fast_mode": "false"})


@pytest.mark.parametrize(
    "harness,route",
    [
        ("claude", "anthropic"),
        ("copilot", None),
        ("codex", "local_openai"),
        ("codex", "azure_openai"),
        ("codex", "openrouter"),
    ],
)
@pytest.mark.parametrize("speed", [True, "ultrafast"])
def test_unsupported_fast_dispatch_is_rejected_before_execution(harness, route, speed) -> None:
    session = SessionManifest(
        session_id="test", allowed_tools=[], expires_at=datetime.now(UTC), fast_mode=speed
    )
    with pytest.raises(ValueError, match="public OpenAI Codex"):
        resolve_fast_mode({}, session=session, harness=harness, route=route)
    assert (
        resolve_fast_mode({"fast_mode": False}, session=session, harness=harness, route=route).value
        is False
    )


def test_captured_standard_speed_does_not_inherit_a_later_fast_default() -> None:
    session = SessionManifest(
        session_id="test", allowed_tools=[], expires_at=datetime.now(UTC), fast_mode=True
    )
    captured = resolve_fast_mode(
        {"fast_mode": False, "fast_mode_source": "default"}, session=session, harness="codex"
    )
    assert (captured.value, captured.source) == (False, "default")
    assert resolve_fast_mode({}, session=session, harness="codex").value is True


@pytest.mark.parametrize("resume", [None, "existing-thread"])
def test_container_turns_explicitly_restore_standard_speed_after_fast(resume) -> None:
    session = SessionManifest(
        session_id="test",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        model="gpt-6.1-sol",
    )
    commands = [
        container_prompt_command(
            prompt="Continue",
            model=session.model,
            session=session,
            fast_mode=override,
            resume_thread_id=resume,
            reasoning_effort="max",
        )
        for override in [True, "ultrafast", None]
    ]
    assert 'service_tier="fast"' in commands[0]
    assert "features.fast_mode=true" in commands[0]
    assert 'service_tier="ultrafast"' in commands[1]
    assert 'service_tier="default"' in commands[2]
    assert 'service_tier="fast"' not in commands[2]
    assert 'service_tier="ultrafast"' not in commands[2]
    assert all('model_reasoning_effort="max"' in command for command in commands)


def test_vm_runner_resets_fast_tier_on_a_followup(tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    args_log = tmp_path / "args.jsonl"
    fake = fake_bin / "codex"
    fake.write_text("""#!/usr/bin/env python3
import json, os, sys
if sys.argv[1:2] in (["features"], ["mcp"]):
    sys.exit(0)
with open(os.environ["ARG_LOG"], "a") as output:
    output.write(json.dumps(sys.argv[1:]) + "\\n")
print('{"type":"result","result":"Done"}')
""")
    fake.chmod(0o755)
    runner = Path(__file__).resolve().parents[1] / "scripts/lib/agent_runner_codex.sh"
    result = subprocess.run(
        [
            "bash",
            "-c",
            """set -e
source "$1"
AGENT_FAST_MODE=ultrafast agent_runner_codex_resume_last --model gpt-6-astra Continue
AGENT_FAST_MODE=true agent_runner_codex_resume_last --model gpt-6.1-sol Continue
AGENT_FAST_MODE=false agent_runner_codex_resume_last --model gpt-6.1-sol Continue
""",
            "test",
            str(runner),
        ],
        env=os.environ
        | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "ARG_LOG": str(args_log),
            "AGENT_WEB_SEARCH_MODE": "disabled",
            "EVANDER_MCP_GATEWAY_URL": "",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    ultrafast, fast, standard = [json.loads(line) for line in args_log.read_text().splitlines()]
    assert 'service_tier="ultrafast"' in ultrafast
    assert 'service_tier="fast"' in fast
    assert 'service_tier="default"' in standard
    assert 'service_tier="fast"' not in standard


@pytest.mark.parametrize("default", [True, False, "ultrafast", None])
def test_workflow_creation_persists_and_reuses_session_speed(
    tmp_path, monkeypatch, default
) -> None:
    config = GatewayConfig(runtime_dir=tmp_path, container_worker_base_url="http://worker.test")
    repository = JsonSessionManifestRepository(config.session_store_path)
    backend = _FakeContainerBackend()
    monkeypatch.setattr(
        "evander.effects.providers.agent_create.container_backend", lambda *_args: backend
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=SimpleNamespace(runtime=SimpleNamespace(session_repository=repository)),
    )
    fields = dict(
        id="create",
        display_name="Create",
        type="effect:agent:create",
        session_id="speed-session",
        execution_backend="container_worker_vm",
    )
    factory.build(AgentCreateEffectDefinition(**fields, fast_mode=default)).execute()
    assert repository.get_session("speed-session").fast_mode == default
    factory.build(AgentCreateEffectDefinition(**fields)).execute()
    assert repository.get_session("speed-session").fast_mode == default
    factory.build(AgentCreateEffectDefinition(**fields, fast_mode=False)).execute()
    assert repository.get_session("speed-session").fast_mode is False
    factory.build(AgentCreateEffectDefinition(**fields, fast_mode=None)).execute()
    assert repository.get_session("speed-session").fast_mode is None
