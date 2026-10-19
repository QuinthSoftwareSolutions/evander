from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import subprocess

import pytest
from starlette.testclient import TestClient
from config_helpers import write_runtime_config

from evander.admin.composition import build_admin_runtime
from evander.admin.web.app import create_app
from evander.codex_auth import CodexAuthMode, CodexTurnSpec
from evander.config import GatewayConfig
from evander.effects.providers.agent_session import (
    container_codex_turn_spec,
    container_prompt_command,
)
from evander.models import SessionManifest
from evander.worker.codex_auth_broker import CodexExternalAuthTokens
from evander.worker.codex_subscription import CodexSubscriptionTurnRunner
from test_codex_subscription import FakeBroker, FakeTurnProcess
from test_effect_registry import _ContainerResolverStub, _FakeContainerBackend
from types import SimpleNamespace
from evander.effects.providers.schemas import AgentCreateEffectDefinition
from evander.effects.registry import EffectProviderFactory
from evander.repositories.session_manifests import JsonSessionManifestRepository


@pytest.fixture
def agent(tmp_path):
    service = build_admin_runtime(write_runtime_config(tmp_path))
    session = SessionManifest(
        session_id="program-agent",
        vm_id="program-vm",
        model="gpt-6-sol",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata={"agent_provider": "codex", "model_provider_mode": "openai_public"},
    )
    service.runtime.session_repository.upsert_session(session)
    with TestClient(create_app(service)) as client:
        assert client.post("/api/admin/privileged/unlock").status_code == 200
        yield service, client


@pytest.mark.parametrize(
    "path,method",
    [
        ("/api/admin/sessions/program-agent/model", "put"),
        ("/api/admin/agents/program-agent?backend=libvirt_vm", "patch"),
    ],
)
def test_operator_program_persists_applies_to_next_turn_and_can_be_reset(
    agent, tmp_path, path, method
):
    service, client = agent
    request = getattr(client, method)
    assert request(path, json={"cyber_access_program": "daybreak_blue"}).status_code == 200
    saved = service.runtime.session_repository.get_session("program-agent")
    config = GatewayConfig(
        runtime_dir=tmp_path, container_worker_codex_auth_mode="chatgpt_subscription"
    )
    current = container_codex_turn_spec(
        prompt="Continue",
        model=saved.model,
        resume_thread_id="existing-thread",
        session=saved,
        config=config,
    )
    assert current.cyber_access_program == "daybreak_blue"
    assert "--cyber-access-program daybreak_blue" in container_prompt_command(
        prompt="Continue",
        model=saved.model,
        resume_thread_id="existing-thread",
        session=saved,
    )
    # Invalid input must not mutate the saved selection or require a new session.
    assert request(path, json={"cyber_access_program": "unknown_program"}).status_code == 400
    assert (
        service.runtime.session_repository.get_session("program-agent").cyber_access_program
        == "daybreak_blue"
    )
    assert request(path, json={"model": "gpt-6-astra"}).status_code == 200
    assert (
        service.runtime.session_repository.get_session("program-agent").cyber_access_program
        == "daybreak_blue"
    )
    assert request(path, json={"cyber_access_program": None}).status_code == 200
    reset = service.runtime.session_repository.get_session("program-agent")
    assert reset.cyber_access_program is None
    assert reset.metadata == saved.metadata and reset.session_id == saved.session_id
    assert current.cyber_access_program == "daybreak_blue"  # Dispatch snapshot remains unchanged.
    assert "--cyber-access-program" not in container_prompt_command(
        prompt="Continue",
        model=reset.model,
        resume_thread_id="existing-thread",
        session=reset,
    )


@pytest.mark.parametrize(
    "harness,route",
    [
        ("claude", "anthropic"),
        ("copilot", "openai_public"),
        ("codex", "azure_openai"),
        ("codex", "local_openai"),
        ("codex", "openrouter"),
    ],
)
def test_program_for_unsupported_agent_is_rejected_before_persistence(agent, harness, route):
    service, client = agent
    original = service.runtime.session_repository.get_session("program-agent")
    original.metadata.update(agent_provider=harness, model_provider_mode=route)
    service.runtime.session_repository.upsert_session(original)
    result = client.put(
        "/api/admin/sessions/program-agent/model", json={"cyber_access_program": "daybreak_blue"}
    )
    assert result.status_code == 400
    assert (
        service.runtime.session_repository.get_session(original.session_id).cyber_access_program
        is None
    )


@pytest.mark.parametrize(
    "program,expected",
    [
        (None, None),
        ("standard", "standard"),
        ("daybreak_blue", "daybreakBlue"),
        ("daybreak_red", "daybreakRed"),
    ],
)
@pytest.mark.parametrize("resumed", [False, True])
def test_subscription_turn_requests_selected_program_without_changing_credentials(
    tmp_path, program, expected, resumed
):
    FakeTurnProcess.instances.clear()
    runner = CodexSubscriptionTurnRunner(
        command=("synthetic-codex", "app-server"),
        turn=CodexTurnSpec(
            auth_mode=CodexAuthMode.CHATGPT_SUBSCRIPTION,
            prompt="Continue",
            model="gpt-6-sol",
            cyber_access_program=program,
            resume_thread_id="existing-thread" if resumed else None,
        ),
        cwd="/home/agent",
        broker=FakeBroker(),
        initial_tokens=CodexExternalAuthTokens("access-secret", "account-1", "plus"),
        log_path=tmp_path / "run.log",
        finished_marker=tmp_path / "run.exit",
        process_factory=FakeTurnProcess,
    )
    runner.run()
    assert (tmp_path / "run.exit").read_text() == "0"
    turn = next(
        params for method, params in FakeTurnProcess.instances[0].requests if method == "turn/start"
    )
    assert turn.get("cyberAccessProgram") == expected
    assert "access-secret" not in (tmp_path / "run.log").read_text()


def test_vm_resumes_select_program_and_clear_it_without_stale_flags(tmp_path):
    runner = Path(__file__).resolve().parents[1] / "scripts/lib/agent_runner_codex.sh"
    result = subprocess.run(
        [
            "bash",
            "-c",
            """set -e
source "$1"
for choice in daybreak_blue standard ''; do
  AGENT_CYBER_ACCESS_PROGRAM="$choice" agent_runner_codex_config_args
  printf '\\n'
done
""",
            "test",
            str(runner),
        ],
        env={**os.environ, "AGENT_WEB_SEARCH_MODE": "disabled"},
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode()
    rows = [row.split(b"\0") for row in result.stdout.splitlines()]
    assert rows[0][:2] == [b"--cyber-access-program", b"daybreak_blue"]
    assert rows[1][:2] == [b"--cyber-access-program", b"standard"]
    assert b"--cyber-access-program" not in rows[2]


@pytest.mark.parametrize("program", [None, "standard", "daybreak_blue", "daybreak_red"])
def test_workflow_creation_preserves_session_program_unless_explicitly_changed(
    tmp_path, monkeypatch, program
):
    config = GatewayConfig(runtime_dir=tmp_path, container_worker_base_url="http://worker.test")
    repository = JsonSessionManifestRepository(config.session_store_path)
    backend = _FakeContainerBackend()
    monkeypatch.setattr(
        "evander.effects.providers.agent_create.container_backend", lambda *_: backend
    )
    factory = EffectProviderFactory(
        provider_config_resolver=_ContainerResolverStub(config),
        admin_service=SimpleNamespace(runtime=SimpleNamespace(session_repository=repository)),
    )
    fields = dict(
        id="create",
        display_name="Create",
        type="effect:agent:create",
        session_id="program-workflow",
        execution_backend="container_worker_vm",
    )
    factory.build(AgentCreateEffectDefinition(**fields, cyber_access_program=program)).execute()
    assert repository.get_session("program-workflow").cyber_access_program == program
    factory.build(AgentCreateEffectDefinition(**fields)).execute()
    assert repository.get_session("program-workflow").cyber_access_program == program
    factory.build(AgentCreateEffectDefinition(**fields, cyber_access_program="standard")).execute()
    assert repository.get_session("program-workflow").cyber_access_program == "standard"
    factory.build(AgentCreateEffectDefinition(**fields, cyber_access_program=None)).execute()
    assert repository.get_session("program-workflow").cyber_access_program is None


def test_workflow_program_validates_literals_and_allows_bindings():
    fields = dict(id="create", display_name="Create", type="effect:agent:create")
    binding = "${{ trigger.cyber_access_program }}"
    assert (
        AgentCreateEffectDefinition(**fields, cyber_access_program=binding).cyber_access_program
        == binding
    )
    for invalid in (True, "unknown_program", ""):
        with pytest.raises(ValueError, match="cyber_access_program"):
            AgentCreateEffectDefinition(**fields, cyber_access_program=invalid)
