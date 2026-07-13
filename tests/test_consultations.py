from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from evander.config import GatewayConfig
from evander.consultations import (
    CodeConsultationResult,
    CodeConsultationStore,
    hash_consultation_token,
    issue_consultation_token,
    utc_now,
    verify_consultation_token,
)


def _create(
    store: CodeConsultationStore,
    *,
    suffix: str,
    idempotency_key: str | None = None,
    query: str | None = None,
) -> tuple[object, bool]:
    return store.create(
        consultation_id=f"consult-{suffix}",
        session_id=f"consult-worker-{suffix}",
        source_session_id="senior-1",
        directory_path="root/team",
        managed_repo_id="mr_0123456789abcdef0123",
        source_ref="refs/heads/main",
        commit_sha="a" * 40,
        profile_name="local-search",
        profile={
            "execution_backend": "container_worker_vm",
            "agent_provider": "codex",
            "model": "qwen3.6:27b",
            "model_provider_mode": "local_openai",
            "timeout_seconds": 180,
            "max_concurrency": 1,
        },
        query=query or f"query {suffix}",
        idempotency_key=idempotency_key,
        max_concurrency=1,
        expires_at=utc_now() + timedelta(minutes=3),
        token_hash=hash_consultation_token(f"token-{suffix}"),
    )


def test_store_holds_capacity_until_terminal_worker_cleanup(tmp_path) -> None:
    store = CodeConsultationStore(tmp_path / "consultations.db")
    first, _ = _create(store, suffix="one")
    second, _ = _create(store, suffix="two")

    claimed = store.claim_available(directory_path="root/team", profile_name="local-search")
    assert [record.consultation_id for record in claimed] == [first.consultation_id]
    assert store.get(second.consultation_id).status == "queued"

    store.mark_running(first.consultation_id, workspace_path="/home/agent/repos/repo")
    store.submit_result(
        first.consultation_id,
        session_id=first.session_id,
        token_hash=hash_consultation_token("token-one"),
        result=CodeConsultationResult(
            answer="The answer",
            evidence=[],
            confidence=0.8,
        ),
    )

    assert store.claim_available(directory_path="root/team", profile_name="local-search") == []
    store.mark_cleanup(first.consultation_id, success=False, error="destroy failed")
    assert store.claim_available(directory_path="root/team", profile_name="local-search") == []
    store.mark_cleanup(first.consultation_id, success=True)
    claimed = store.claim_available(directory_path="root/team", profile_name="local-search")
    assert [record.consultation_id for record in claimed] == [second.consultation_id]


def test_late_cleanup_failure_cannot_downgrade_completed_cleanup(tmp_path) -> None:
    store = CodeConsultationStore(tmp_path / "consultations.db")
    record, _ = _create(store, suffix="one")

    store.mark_cleanup(record.consultation_id, success=True)
    updated = store.mark_cleanup(
        record.consultation_id,
        success=False,
        error="late concurrent failure",
    )

    assert updated.cleanup_status == "completed"
    assert updated.cleanup_error is None


def test_compaction_retains_terminal_records_with_incomplete_cleanup(tmp_path) -> None:
    store = CodeConsultationStore(tmp_path / "consultations.db")
    cleaned, _ = _create(store, suffix="cleaned")
    incomplete, _ = _create(store, suffix="incomplete")
    for record in (cleaned, incomplete):
        store.terminate(record.consultation_id, status="cancelled", error="cancelled")
    store.mark_cleanup(cleaned.consultation_id, success=True)
    store.mark_cleanup(incomplete.consultation_id, success=False, error="destroy failed")

    deleted = store.compact(before=utc_now() + timedelta(days=1))

    assert deleted == 1
    assert store.get(cleaned.consultation_id) is None
    assert store.get(incomplete.consultation_id).cleanup_status == "failed"


def test_store_idempotency_is_scoped_to_requesting_session(tmp_path) -> None:
    store = CodeConsultationStore(tmp_path / "consultations.db")
    first, created = _create(
        store,
        suffix="one",
        idempotency_key="same-request",
        query="same query",
    )
    repeated, repeated_created = _create(
        store,
        suffix="different-generated-id",
        idempotency_key="same-request",
        query="same query",
    )

    assert created is True
    assert repeated_created is False
    assert repeated.consultation_id == first.consultation_id


def test_consultation_token_is_bound_to_consultation_and_worker_session() -> None:
    expires_at = utc_now() + timedelta(minutes=1)
    token = issue_consultation_token(
        secret="test-secret",
        consultation_id="consult-one",
        session_id="consult-worker-one",
        expires_at=expires_at,
    )

    claims = verify_consultation_token(
        token,
        secret="test-secret",
        consultation_id="consult-one",
        session_id="consult-worker-one",
    )
    assert claims["consultation_id"] == "consult-one"
    with pytest.raises(ValueError, match="another worker session"):
        verify_consultation_token(
            token,
            secret="test-secret",
            consultation_id="consult-one",
            session_id="consult-worker-two",
        )


def test_gateway_config_requires_a_known_named_consultation_profile() -> None:
    config = GatewayConfig.model_validate(
        {
            "agent_consultation_profiles": {
                "local-search": {
                    "model": "qwen3.6:27b",
                    "model_provider_mode": "local_openai",
                    "max_concurrency": 2,
                }
            },
            "agent_consultation_default_profile": "local-search",
        }
    )

    assert config.agent_consultation_profiles["local-search"].execution_backend == (
        "container_worker_vm"
    )
    assert config.agent_consultation_profiles["local-search"].max_concurrency == 2
    with pytest.raises(ValidationError, match="must name"):
        GatewayConfig.model_validate(
            {
                "agent_consultation_profiles": {"local-search": {"model": "qwen3.6:27b"}},
                "agent_consultation_default_profile": "missing",
            }
        )
