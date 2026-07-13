from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from evander.config import GatewayConfig
from evander.consultations import (
    CONSULTATION_RESULT_TOKEN_PATH,
    CodeConsultationResult,
    CodeConsultationStore,
    consultation_store_path,
    hash_consultation_token,
    issue_consultation_token,
    utc_now,
)
from evander.effects.providers.agent_clone_repo import AgentCloneRepoEffectProvider
from evander.effects.providers.agent_create import AgentCreateEffectProvider
from evander.effects.providers.agent_destroy import AgentDestroyEffectProvider
from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.code_consultation import CodeConsultationEffectProvider
from evander.events.provider_models import CodeConsultationEffectDefinition


def _record(config: GatewayConfig):
    expires_at = utc_now() + timedelta(minutes=3)
    token = issue_consultation_token(
        secret="consultation-secret",
        consultation_id="consult-one",
        session_id="consult-worker-one",
        expires_at=expires_at,
    )
    store = CodeConsultationStore(consultation_store_path(config.runtime_dir))
    record, _ = store.create(
        consultation_id="consult-one",
        session_id="consult-worker-one",
        source_session_id="senior-one",
        directory_path="root",
        managed_repo_id="mr_0123456789abcdef0123",
        source_ref="refs/heads/main",
        commit_sha="a" * 40,
        profile_name="local-search",
        profile={
            "agent_provider": "codex",
            "model": "qwen3.6:27b",
            "model_provider_mode": "local_openai",
            "container_image": None,
        },
        query="Where is routing implemented?",
        idempotency_key=None,
        max_concurrency=1,
        expires_at=expires_at,
        token_hash=hash_consultation_token(token),
    )
    store.claim_available(directory_path="root", profile_name="local-search")
    return store, record, token


def _provider(config: GatewayConfig, *, operation: str, **fields):
    provider = CodeConsultationEffectProvider()
    provider.initialize(
        CodeConsultationEffectDefinition(
            id=f"consultation-{operation}",
            display_name=f"Consultation {operation}",
            type="effect:agent:code-consultation",
            consultation_id="consult-one",
            operation=operation,
            **fields,
        ),
        EffectRuntimeContext(
            resolved_config=config,
            env={"AGENT_PROMPT_TOKEN_SECRET": "consultation-secret"},
            admin_service=SimpleNamespace(
                managed_git_admin=SimpleNamespace(
                    release_snapshot_pin=lambda *_args, **_kwargs: {"released": True}
                )
            ),
        ),
    )
    return provider


def test_prepare_materializes_worker_clones_and_pins_snapshot(tmp_path, monkeypatch) -> None:
    config = GatewayConfig(runtime_dir=tmp_path)
    store, _record_before, token = _record(config)
    calls = []
    token_files = []
    provider = _provider(config, operation="prepare")

    def execute_nested(provider_type, definition):
        calls.append((provider_type, definition))
        if provider_type is AgentCreateEffectProvider:
            return {"success": True, "session_id": "consult-worker-one"}
        if provider_type is AgentCloneRepoEffectProvider:
            return {
                "success": True,
                "session_id": "consult-worker-one",
                "target_dir": "/home/agent/repos/example",
            }
        if provider_type is AgentExecEffectProvider:
            return {"success": True}
        raise AssertionError(provider_type)

    monkeypatch.setattr(provider, "_execute_nested", execute_nested)
    monkeypatch.setattr(
        provider,
        "_write_result_token",
        lambda record: token_files.append(record.session_id),
    )
    result = provider.execute()

    assert result["status"] == "running"
    assert result["workspace_path"] == "/home/agent/repos/example"
    assert token not in result["prompt"]
    assert CONSULTATION_RESULT_TOKEN_PATH in result["prompt"]
    assert token_files == ["consult-worker-one"]
    assert [provider_type for provider_type, _definition in calls] == [
        AgentCreateEffectProvider,
        AgentCloneRepoEffectProvider,
        AgentExecEffectProvider,
    ]
    assert calls[0][1].allowed_tools == ["submit_codebase_consultation_result"]
    assert calls[1][1].git_access_mode == "read-only"
    assert "git fetch --no-tags origin refs/evander/consultations/consult-one" in (
        calls[2][1].command
    )
    assert "git checkout --detach FETCH_HEAD" in calls[2][1].command
    assert store.get("consult-one").status == "running"


def test_finalize_preserves_submitted_result_and_destroys_worker(tmp_path, monkeypatch) -> None:
    config = GatewayConfig(runtime_dir=tmp_path)
    store, record, token = _record(config)
    store.mark_running("consult-one", workspace_path="/home/agent/repos/example")
    store.submit_result(
        "consult-one",
        session_id=record.session_id,
        token_hash=hash_consultation_token(token),
        result=CodeConsultationResult(answer="Answer", confidence=0.9),
    )
    provider = _provider(config, operation="finalize", outcome="completed")
    destroyed = []

    def execute_nested(provider_type, definition):
        assert provider_type is AgentDestroyEffectProvider
        destroyed.append(definition.session_id)
        return {"success": True, "message": "destroyed"}

    monkeypatch.setattr(provider, "_execute_nested", execute_nested)
    result = provider.execute()

    assert result["status"] == "completed"
    assert result["cleanup_status"] == "completed"
    assert destroyed == ["consult-worker-one"]
    assert store.get("consult-one").result.answer == "Answer"


def test_timeout_cleans_up_worker_even_when_result_was_already_submitted(
    tmp_path, monkeypatch
) -> None:
    config = GatewayConfig(runtime_dir=tmp_path)
    store, record, token = _record(config)
    store.mark_running("consult-one", workspace_path="/home/agent/repos/example")
    store.submit_result(
        "consult-one",
        session_id=record.session_id,
        token_hash=hash_consultation_token(token),
        result=CodeConsultationResult(answer="Answer", confidence=0.9),
    )
    provider = _provider(
        config,
        operation="terminate",
        status="timed_out",
        reason="deadline exceeded",
    )
    destroyed = []

    def execute_nested(provider_type, definition):
        assert provider_type is AgentDestroyEffectProvider
        destroyed.append(definition.session_id)
        return {"success": True, "message": "destroyed"}

    monkeypatch.setattr(provider, "_execute_nested", execute_nested)
    result = provider.execute()

    assert result["status"] == "completed"
    assert result["cleanup_status"] == "completed"
    assert destroyed == ["consult-worker-one"]


def test_prepare_failure_cleans_up_and_releases_capacity(tmp_path, monkeypatch) -> None:
    config = GatewayConfig(runtime_dir=tmp_path)
    store, first, _token = _record(config)
    store.create(
        consultation_id="consult-two",
        session_id="consult-worker-two",
        source_session_id="senior-two",
        directory_path="root",
        managed_repo_id=first.managed_repo_id,
        source_ref="refs/heads/main",
        commit_sha="a" * 40,
        profile_name="local-search",
        profile=first.profile,
        query="Where is the second path?",
        idempotency_key=None,
        max_concurrency=1,
        expires_at=utc_now() + timedelta(minutes=3),
        token_hash=hash_consultation_token("second-token"),
    )
    provider = _provider(config, operation="prepare")

    def execute_nested(provider_type, _definition):
        if provider_type is AgentCreateEffectProvider:
            return {"success": False, "message": "worker creation failed"}
        if provider_type is AgentDestroyEffectProvider:
            return {"success": True, "message": "already absent"}
        raise AssertionError(provider_type)

    monkeypatch.setattr(provider, "_execute_nested", execute_nested)

    with pytest.raises(RuntimeError, match="worker creation failed"):
        provider.execute()

    assert store.get("consult-one").cleanup_status == "completed"
    assert store.get("consult-two").status == "provisioning"
