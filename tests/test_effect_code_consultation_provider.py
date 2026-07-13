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
    verify_consultation_token,
)
from evander.effects.providers.agent_clone_repo import AgentCloneRepoEffectProvider
from evander.effects.providers.agent_create import AgentCreateEffectProvider
from evander.effects.providers.agent_destroy import AgentDestroyEffectProvider
from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.code_consultation import CodeConsultationEffectProvider
from evander.events.provider_models import CodeConsultationEffectDefinition


def _record(config: GatewayConfig, *, directory_path: str = "root"):
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
        directory_path=directory_path,
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
    store.claim_available(directory_path=directory_path, profile_name="local-search")
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


def test_prepare_retry_retries_failed_cleanup_and_releases_capacity(
    tmp_path,
    monkeypatch,
) -> None:
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
    create_attempts = 0
    destroy_attempts = 0

    def execute_nested(provider_type, _definition):
        nonlocal create_attempts, destroy_attempts
        if provider_type is AgentCreateEffectProvider:
            create_attempts += 1
            return {"success": False, "message": "worker creation failed"}
        if provider_type is AgentDestroyEffectProvider:
            destroy_attempts += 1
            if destroy_attempts == 1:
                return {"success": False, "message": "worker cleanup failed"}
            return {"success": True, "message": "worker destroyed"}
        raise AssertionError(provider_type)

    monkeypatch.setattr(provider, "_execute_nested", execute_nested)

    with pytest.raises(RuntimeError, match="cleanup also failed"):
        provider.execute()

    failed = store.get("consult-one")
    assert failed is not None
    assert failed.status == "failed"
    assert failed.cleanup_status == "failed"
    assert store.get("consult-two").status == "queued"

    with pytest.raises(RuntimeError, match=r"already terminal \(failed\)"):
        provider.execute()

    cleaned = store.get("consult-one")
    assert cleaned is not None
    assert cleaned.cleanup_status == "completed"
    assert store.get("consult-two").status == "provisioning"
    assert create_attempts == 1
    assert destroy_attempts == 2


def test_result_token_uses_worker_session_effective_secret(
    tmp_path,
    monkeypatch,
) -> None:
    root_config = GatewayConfig(
        runtime_dir=tmp_path,
        agent_consultation_token_secret_env="ROOT_CONSULTATION_SECRET",
    )
    worker_config = root_config.model_copy(
        update={"agent_consultation_token_secret_env": "TEAM_CONSULTATION_SECRET"}
    )
    _store, record, _token = _record(root_config, directory_path="root/team-a")
    resolver_calls: list[str] = []
    backend_configs: list[GatewayConfig] = []
    written_files: list[dict[str, object]] = []

    class SessionConfigResolver:
        def resolve_for_session_id(self, session_id: str) -> GatewayConfig:
            resolver_calls.append(session_id)
            return worker_config

    backend = SimpleNamespace(
        write_files=lambda **kwargs: written_files.append(dict(kwargs)),
    )
    monkeypatch.setattr(
        "evander.effects.providers.code_consultation.container_backend",
        lambda context, config: backend_configs.append(config) or backend,
    )
    provider = CodeConsultationEffectProvider()
    provider.initialize(
        CodeConsultationEffectDefinition(
            id="consultation-prepare",
            display_name="Consultation prepare",
            type="effect:agent:code-consultation",
            consultation_id="consult-one",
            operation="prepare",
        ),
        EffectRuntimeContext(
            resolved_config=root_config,
            env={"TEAM_CONSULTATION_SECRET": "team-consultation-secret"},
            admin_service=SimpleNamespace(
                runtime=SimpleNamespace(provider_config=SessionConfigResolver()),
            ),
        ),
    )

    provider._write_result_token(record)

    assert resolver_calls == ["consult-worker-one"]
    assert backend_configs == [worker_config]
    assert written_files[0]["session_id"] == "consult-worker-one"
    token = written_files[0]["files"][0]["content"]
    claims = verify_consultation_token(
        str(token),
        secret="team-consultation-secret",
        consultation_id="consult-one",
        session_id="consult-worker-one",
    )
    assert claims["consultation_id"] == "consult-one"
