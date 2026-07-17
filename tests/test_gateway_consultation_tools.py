from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import evander.gateway as gateway_module
import evander.gateway.consultation_tools as consultation_tools_module
import pytest

from evander.config import GatewayConfig
from evander.consultations import (
    CodeConsultationStore,
    consultation_store_path,
    hash_consultation_token,
    issue_consultation_token,
    utc_now,
)
from evander.directories import DirectoryService
from evander.events.internal import InternalEventBus, internal_event_store_path
from evander.gateway import create_server
from evander.models import SessionManifest, ToolName
from evander.runtime import build_runtime


def _call_tool(server, name: str, arguments: dict[str, object]):
    result = asyncio.run(server.call_tool(name, arguments))
    if isinstance(result, tuple) and len(result) == 2:
        structured = result[1]
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    return result


class _ManagedGit:
    def __init__(self):
        self.pins = []
        self.released_pins = []

    def resolve_snapshot(self, managed_repo_id, directory_path, **_kwargs):
        return {
            "managed_repo_id": managed_repo_id,
            "directory_path": directory_path,
            "source_ref": "refs/heads/main",
            "commit_sha": "a" * 40,
        }

    def pin_snapshot(self, managed_repo_id, directory_path, **kwargs):
        self.pins.append((managed_repo_id, directory_path, dict(kwargs)))
        return {
            "managed_repo_id": managed_repo_id,
            "directory_path": directory_path,
            "source_ref": "refs/heads/main" if kwargs.get("source_ref") else None,
            "commit_sha": kwargs.get("commit_sha") or "a" * 40,
            "pin_ref": f"refs/evander/consultations/{kwargs['consultation_id']}",
        }

    def release_snapshot_pin(self, managed_repo_id, directory_path, **kwargs):
        self.released_pins.append((managed_repo_id, directory_path, dict(kwargs)))
        return {"released": True}

    def validate_snapshot_evidence(self, _managed_repo_id, _directory_path, **kwargs):
        return kwargs["evidence"]


def _runtime(tmp_path, *, allowed_tools: list[ToolName]):
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(
        runtime_dir=runtime_dir,
        audit_log_path=runtime_dir / "audit.jsonl",
        session_store_path=runtime_dir / "sessions.json",
        approval_store_path=runtime_dir / "approvals.json",
        agent_consultation_profiles={
            "local-search": {
                "model": "qwen3.6:27b",
                "model_provider_mode": "local_openai",
                "timeout_seconds": 180,
                "max_concurrency": 1,
            }
        },
        agent_consultation_default_profile="local-search",
        agent_consultation_max_wait_seconds=0,
    )
    context = build_runtime(config)
    DirectoryService(context.directories).create(parent="root", name="team")
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="senior-1",
            allowed_tools=allowed_tools,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            directory_path="root/team",
        )
    )
    return context


def test_consult_codebase_pins_snapshot_and_publishes_durable_request(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_PROMPT_TOKEN_SECRET", "consultation-secret")
    managed_git = _ManagedGit()
    monkeypatch.setattr(
        gateway_module,
        "build_admin_runtime",
        lambda _runtime_dir: SimpleNamespace(managed_git_admin=managed_git),
    )
    context = _runtime(
        tmp_path,
        allowed_tools=[
            ToolName.CONSULT_CODEBASE,
            ToolName.GET_CODEBASE_CONSULTATION,
            ToolName.CANCEL_CODEBASE_CONSULTATION,
        ],
    )
    server = create_server(context)

    created = _call_tool(
        server,
        "consult_codebase",
        {
            "session_id": "senior-1",
            "managed_repo_id": "mr_0123456789abcdef0123",
            "source_ref": "main",
            "query": "Where is request routing implemented?",
            "wait_seconds": 0,
        },
    )

    assert created["status"] == "provisioning"
    assert created["provenance"] == {
        "managed_repo_id": "mr_0123456789abcdef0123",
        "source_ref": "refs/heads/main",
        "commit_sha": "a" * 40,
    }
    assert managed_git.pins[0][2]["source_ref"] == "main"
    assert managed_git.pins[-1][2]["commit_sha"] == "a" * 40
    events = InternalEventBus(internal_event_store_path(context.config.runtime_dir)).list_events()
    assert [(event.event_type, event.payload["consultation_id"]) for event in events] == [
        ("agent.consultation.requested", created["consultation_id"])
    ]

    fetched = _call_tool(
        server,
        "get_codebase_consultation",
        {
            "session_id": "senior-1",
            "consultation_id": created["consultation_id"],
        },
    )
    assert fetched["consultation_id"] == created["consultation_id"]
    cancelled = _call_tool(
        server,
        "cancel_codebase_consultation",
        {
            "session_id": "senior-1",
            "consultation_id": created["consultation_id"],
        },
    )
    assert cancelled["cancellation_requested"] is True
    assert [
        event.event_type
        for event in InternalEventBus(
            internal_event_store_path(context.config.runtime_dir)
        ).list_events()
    ] == [
        "agent.consultation.requested",
        "agent.consultation.terminate",
    ]


def test_worker_submission_is_session_bound_and_persists_structured_result(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_PROMPT_TOKEN_SECRET", "consultation-secret")
    managed_git = _ManagedGit()
    monkeypatch.setattr(
        gateway_module,
        "build_admin_runtime",
        lambda _runtime_dir: SimpleNamespace(managed_git_admin=managed_git),
    )
    context = _runtime(tmp_path, allowed_tools=[])
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="consult-worker-one",
            allowed_tools=[ToolName.SUBMIT_CODEBASE_CONSULTATION_RESULT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            directory_path="root/team",
        )
    )
    expires_at = utc_now() + timedelta(minutes=3)
    token = issue_consultation_token(
        secret="consultation-secret",
        consultation_id="consult-one",
        session_id="consult-worker-one",
        expires_at=expires_at,
    )
    store = CodeConsultationStore(consultation_store_path(context.config.runtime_dir))
    store.create(
        consultation_id="consult-one",
        session_id="consult-worker-one",
        source_session_id="senior-1",
        directory_path="root/team",
        managed_repo_id="mr_0123456789abcdef0123",
        source_ref="refs/heads/main",
        commit_sha="a" * 40,
        profile_name="local-search",
        profile={"model": "qwen3.6:27b"},
        query="Where is routing?",
        idempotency_key=None,
        max_concurrency=1,
        expires_at=expires_at,
        token_hash=hash_consultation_token(token),
    )
    store.claim_available(directory_path="root/team", profile_name="local-search")
    store.mark_running("consult-one", workspace_path="/home/agent/repos/repo")
    server = create_server(context)

    response = _call_tool(
        server,
        "submit_codebase_consultation_result",
        {
            "session_id": "consult-worker-one",
            "consultation_id": "consult-one",
            "result_token": token,
            "answer": "Routing starts in gateway/server.py.",
            "evidence": [
                {
                    "path": "src/evander/gateway/server.py",
                    "line_start": 1,
                    "line_end": 2,
                    "explanation": "Gateway entry point.",
                }
            ],
            "confidence": 0.9,
            "limitations": ["Only the primary path was inspected."],
        },
    )

    assert response == {
        "accepted": True,
        "consultation_id": "consult-one",
        "status": "completed",
        "commit_sha": "a" * 40,
    }
    record = store.get("consult-one")
    assert record is not None
    assert record.result.answer == "Routing starts in gateway/server.py."
    assert record.result.evidence[0].line_start == 1


def test_consult_codebase_idempotent_retry_reuses_the_original_snapshot(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_PROMPT_TOKEN_SECRET", "consultation-secret")
    managed_git = _ManagedGit()
    monkeypatch.setattr(
        gateway_module,
        "build_admin_runtime",
        lambda _runtime_dir: SimpleNamespace(managed_git_admin=managed_git),
    )
    context = _runtime(tmp_path, allowed_tools=[ToolName.CONSULT_CODEBASE])
    server = create_server(context)
    arguments = {
        "session_id": "senior-1",
        "managed_repo_id": "mr_0123456789abcdef0123",
        "source_ref": "main",
        "query": "Where is request routing implemented?",
        "wait_seconds": 0,
        "idempotency_key": "routing-search",
    }

    first = _call_tool(server, "consult_codebase", arguments)
    repeated = _call_tool(server, "consult_codebase", arguments)

    assert repeated["created"] is False
    assert repeated["consultation_id"] == first["consultation_id"]
    assert repeated["provenance"] == first["provenance"]
    assert sum(1 for _repo, _directory, kwargs in managed_git.pins if kwargs.get("source_ref")) == 1


def test_cancelling_a_queued_consultation_publishes_snapshot_cleanup(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_PROMPT_TOKEN_SECRET", "consultation-secret")
    managed_git = _ManagedGit()
    monkeypatch.setattr(
        gateway_module,
        "build_admin_runtime",
        lambda _runtime_dir: SimpleNamespace(managed_git_admin=managed_git),
    )
    context = _runtime(
        tmp_path,
        allowed_tools=[ToolName.CONSULT_CODEBASE, ToolName.CANCEL_CODEBASE_CONSULTATION],
    )
    server = create_server(context)
    common = {
        "session_id": "senior-1",
        "managed_repo_id": "mr_0123456789abcdef0123",
        "source_ref": "main",
        "wait_seconds": 0,
    }

    first = _call_tool(
        server,
        "consult_codebase",
        {**common, "query": "first query"},
    )
    second = _call_tool(
        server,
        "consult_codebase",
        {**common, "query": "second query"},
    )
    assert first["status"] == "provisioning"
    assert second["status"] == "queued"

    cancelled = _call_tool(
        server,
        "cancel_codebase_consultation",
        {
            "session_id": "senior-1",
            "consultation_id": second["consultation_id"],
        },
    )

    assert cancelled["status"] == "cancelled"
    assert cancelled["cancellation_requested"] is True
    events = InternalEventBus(internal_event_store_path(context.config.runtime_dir)).list_events()
    assert [(event.event_type, event.payload["consultation_id"]) for event in events] == [
        ("agent.consultation.requested", first["consultation_id"]),
        ("agent.consultation.terminate", second["consultation_id"]),
    ]


def test_consult_codebase_releases_snapshot_when_timeout_setup_fails(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_PROMPT_TOKEN_SECRET", "consultation-secret")
    managed_git = _ManagedGit()
    monkeypatch.setattr(
        gateway_module,
        "build_admin_runtime",
        lambda _runtime_dir: SimpleNamespace(managed_git_admin=managed_git),
    )
    monkeypatch.setattr(
        consultation_tools_module,
        "_schedule_timeout",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("schedule unavailable")),
    )
    context = _runtime(tmp_path, allowed_tools=[ToolName.CONSULT_CODEBASE])
    server = create_server(context)

    with pytest.raises(Exception, match="schedule unavailable"):
        _call_tool(
            server,
            "consult_codebase",
            {
                "session_id": "senior-1",
                "managed_repo_id": "mr_0123456789abcdef0123",
                "source_ref": "main",
                "query": "Where is request routing implemented?",
                "wait_seconds": 0,
            },
        )

    consultation_id = managed_git.pins[0][2]["consultation_id"]
    record = CodeConsultationStore(consultation_store_path(context.config.runtime_dir)).get(
        consultation_id
    )
    assert record is not None
    assert record.status == "failed"
    assert record.cleanup_status == "completed"
    assert managed_git.released_pins[-1][2]["consultation_id"] == consultation_id


def test_consult_codebase_enqueues_cleanup_when_dispatch_publication_fails(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_PROMPT_TOKEN_SECRET", "consultation-secret")
    managed_git = _ManagedGit()
    monkeypatch.setattr(
        gateway_module,
        "build_admin_runtime",
        lambda _runtime_dir: SimpleNamespace(managed_git_admin=managed_git),
    )
    monkeypatch.setattr(
        consultation_tools_module,
        "_publish_claimed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("event publication unavailable")
        ),
    )
    context = _runtime(tmp_path, allowed_tools=[ToolName.CONSULT_CODEBASE])
    server = create_server(context)

    with pytest.raises(Exception, match="event publication unavailable"):
        _call_tool(
            server,
            "consult_codebase",
            {
                "session_id": "senior-1",
                "managed_repo_id": "mr_0123456789abcdef0123",
                "source_ref": "main",
                "query": "Where is request routing implemented?",
                "wait_seconds": 0,
            },
        )

    consultation_id = managed_git.pins[0][2]["consultation_id"]
    record = CodeConsultationStore(consultation_store_path(context.config.runtime_dir)).get(
        consultation_id
    )
    assert record is not None
    assert record.status == "failed"
    assert record.started_at is not None
    assert record.cleanup_status == "pending"
    events = InternalEventBus(internal_event_store_path(context.config.runtime_dir)).list_events()
    assert [(event.event_type, event.payload["consultation_id"]) for event in events] == [
        ("agent.consultation.terminate", consultation_id)
    ]
