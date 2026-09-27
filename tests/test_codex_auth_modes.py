from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from evander.codex_auth import CodexAuthMode, CodexTurnSpec, effective_codex_auth_mode
from evander.config import GatewayConfig
from evander.effects.providers.agent_session import container_codex_turn_spec
from evander.models import SessionManifest
from evander.worker.models import WorkerJobRequest


def test_session_override_wins_over_directory_codex_auth_default(tmp_path: Path) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        container_worker_codex_auth_mode="chatgpt_subscription",
    )
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    inherited = SessionManifest(
        session_id="inherited",
        allowed_tools=[],
        expires_at=expires_at,
    )
    metered = SessionManifest(
        session_id="metered",
        allowed_tools=[],
        expires_at=expires_at,
        codex_auth_mode=CodexAuthMode.METERED_API,
    )

    assert effective_codex_auth_mode(session=inherited, config=config) == (
        CodexAuthMode.CHATGPT_SUBSCRIPTION
    )
    assert effective_codex_auth_mode(session=metered, config=config) == CodexAuthMode.METERED_API


def test_worker_job_uses_structured_subscription_turn_without_token_fields() -> None:
    request = WorkerJobRequest(
        session_id="session-1",
        image="evander-runner:latest",
        command=["ignored-by-subscription-runner"],
        codex_turn=CodexTurnSpec(
            auth_mode="chatgpt_subscription",
            prompt="Continue",
            model="gpt-5.4",
            resume_thread_id="thread-1",
        ),
    )

    payload = request.model_dump(mode="json", exclude_none=True)
    assert payload["codex_turn"]["auth_mode"] == "chatgpt_subscription"
    assert payload["codex_turn"]["resume_thread_id"] == "thread-1"
    assert "token" not in str(payload).casefold()


def test_invalid_codex_auth_mode_is_rejected() -> None:
    with pytest.raises(ValidationError, match="codex_auth_mode"):
        GatewayConfig(container_worker_codex_auth_mode="free_magic")


@pytest.mark.parametrize("model_provider_mode", ["local_openai", "azure_openai", "openrouter"])
def test_private_model_routes_fall_back_to_metered_api_mode(
    model_provider_mode: str,
) -> None:
    config = SimpleNamespace(
        container_worker_codex_auth_mode="chatgpt_subscription",
        model_provider_mode=model_provider_mode,
    )
    session = SessionManifest(
        session_id="codex-private",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    turn = container_codex_turn_spec(
        prompt="Continue",
        model="gpt-5.4",
        resume_thread_id=None,
        session=session,
        config=config,  # type: ignore[arg-type]
        agent_provider="codex",
    )

    assert turn is not None
    assert turn.auth_mode is CodexAuthMode.METERED_API


@pytest.mark.parametrize("model_provider_mode", ["local_openai", "openrouter"])
def test_session_private_route_override_also_prevents_subscription_dispatch(
    tmp_path: Path, model_provider_mode: str
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        container_worker_codex_auth_mode="chatgpt_subscription",
        model_provider_mode="openai_public",
    )
    session = SessionManifest(
        session_id="codex-private",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata={"model_provider_mode": model_provider_mode},
    )

    turn = container_codex_turn_spec(
        prompt="Continue",
        model="gpt-5.4",
        resume_thread_id=None,
        session=session,
        config=config,
        agent_provider="codex",
    )

    assert turn is not None
    assert turn.auth_mode is CodexAuthMode.METERED_API


def test_non_codex_agent_ignores_directory_codex_auth_default(tmp_path: Path) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        container_worker_codex_auth_mode="chatgpt_subscription",
        model_provider_mode="anthropic",
    )
    session = SessionManifest(
        session_id="claude-agent",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata={"agent_provider": "claude"},
    )

    assert (
        container_codex_turn_spec(
            prompt="Continue",
            model="claude-sonnet-4-6",
            resume_thread_id=None,
            session=session,
            config=config,
            agent_provider="claude",
        )
        is None
    )


def test_non_codex_agent_still_rejects_explicit_subscription_override(tmp_path: Path) -> None:
    config = GatewayConfig(runtime_dir=tmp_path, model_provider_mode="anthropic")
    session = SessionManifest(
        session_id="claude-agent",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        codex_auth_mode=CodexAuthMode.CHATGPT_SUBSCRIPTION,
        metadata={"agent_provider": "claude"},
    )

    with pytest.raises(ValueError, match="supported only for Codex agents"):
        container_codex_turn_spec(
            prompt="Continue",
            model="claude-sonnet-4-6",
            resume_thread_id=None,
            session=session,
            config=config,
            agent_provider="claude",
        )
