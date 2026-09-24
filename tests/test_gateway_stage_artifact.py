from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from evander import gateway
from evander.config import GatewayConfig
from evander.effects.providers.agent_session import build_container_session_manifest
from evander.events.agent_artifacts import (
    AGENT_ARTIFACT_STAGED_EVENT_SOURCE_TYPE,
    AGENT_ARTIFACT_STAGED_EVENT_TYPE,
)
from evander.gateway import create_server
from evander.models import SessionManifest, ToolName
from evander.orchestration.internal_events import build_internal_event_bus_for_runtime
from evander.runtime import build_runtime


class FakeArtifactStageEffectProvider:
    calls: list[tuple[object, object]] = []

    def initialize(self, *, effect_definition: object, runtime_context: object) -> None:
        self.effect_definition = effect_definition
        self.runtime_context = runtime_context

    def execute(self) -> dict[str, object]:
        self.calls.append((self.effect_definition, self.runtime_context))
        return {
            "success": True,
            "action": "artifact_stage",
            "message": "Staged 2 artifact file(s) to reports",
            "session_id": "session-1",
            "vm_name": "vm-1",
            "execution_backend": "container_worker_vm",
            "source_path": "/home/agent/repos/project/reports",
            "artifact_path": "reports",
            "artifacts_root": "/artifacts",
            "copied_count": 2,
            "byte_count": 42,
        }


class FailedArtifactStageEffectProvider(FakeArtifactStageEffectProvider):
    def execute(self) -> dict[str, object]:
        return {
            "success": False,
            "action": "artifact_stage",
            "message": "Artifact staging failed",
            "session_id": "session-1",
            "vm_name": "vm-1",
            "execution_backend": "container_worker_vm",
        }


def make_context(tmp_path: Path):
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
        container_worker_base_url="http://worker.example:8080",
    )
    return build_runtime(config)


def call_tool(server, name: str, arguments: dict[str, object]):
    result = asyncio.run(server.call_tool(name, arguments))
    if isinstance(result, tuple) and len(result) == 2:
        structured = result[1]
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    return result


def read_audit_events(path: Path) -> list[dict[str, object]]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


def test_stage_artifact_delegates_to_artifact_stage_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeArtifactStageEffectProvider.calls = []
    monkeypatch.setattr(gateway, "ArtifactStageEffectProvider", FakeArtifactStageEffectProvider)
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            vm_provider="container_worker_vm",
            allowed_tools=[ToolName.STAGE_ARTIFACT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            directory_path="root",
        )
    )
    server = create_server(context)

    result = call_tool(
        server,
        "stage_artifact",
        {
            "session_id": "session-1",
            "path": "repos/project/reports",
            "destination_path": "reports",
        },
    )

    assert result["success"] is True
    assert result["session_id"] == "session-1"
    assert result["vm_name"] == "vm-1"
    assert result["execution_backend"] == "container_worker_vm"
    assert result["source_path"] == "/home/agent/repos/project/reports"
    assert result["artifact_path"] == "reports"
    assert result["artifacts_root"] == "/artifacts"
    assert result["copied_count"] == 2
    assert result["byte_count"] == 42

    staged_events = build_internal_event_bus_for_runtime(
        tmp_path,
        base_config=context.config,
    ).list_events()
    assert len(staged_events) == 1
    staged_event = staged_events[0]
    assert staged_event.event_type == AGENT_ARTIFACT_STAGED_EVENT_TYPE
    assert staged_event.correlation_key == "session-1"
    assert staged_event.source is not None
    assert staged_event.source.source_type == AGENT_ARTIFACT_STAGED_EVENT_SOURCE_TYPE
    assert staged_event.payload == {
        "session_id": "session-1",
        "artifact_path": "reports",
        "source_path": "/home/agent/repos/project/reports",
        "artifacts_root": "/artifacts",
        "copied_count": 2,
        "byte_count": 42,
        "vm_name": "vm-1",
        "execution_backend": "container_worker_vm",
        "directory_path": "root",
    }

    effect, runtime_context = FakeArtifactStageEffectProvider.calls[0]
    assert effect.type == "effect:artifact:stage"
    assert effect.session_id == "session-1"
    assert effect.path == "repos/project/reports"
    assert effect.destination_path == "reports"
    assert runtime_context.metadata == {"directory_path": "root"}

    audit_events = read_audit_events(tmp_path / "audit.jsonl")
    assert audit_events[-1]["tool_name"] == ToolName.STAGE_ARTIFACT.value
    assert audit_events[-1]["result"]["artifact_path"] == "reports"
    assert audit_events[-1]["result"]["copied_count"] == 2


def test_stage_artifact_requires_allowed_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeArtifactStageEffectProvider.calls = []
    monkeypatch.setattr(gateway, "ArtifactStageEffectProvider", FakeArtifactStageEffectProvider)
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="Tool stage_artifact is not allowed"):
        call_tool(
            server,
            "stage_artifact",
            {"session_id": "session-1", "path": "reports/out.txt"},
        )

    assert FakeArtifactStageEffectProvider.calls == []


def test_stage_artifact_does_not_emit_event_when_provider_reports_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway, "ArtifactStageEffectProvider", FailedArtifactStageEffectProvider)
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            allowed_tools=[ToolName.STAGE_ARTIFACT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            directory_path="root",
        )
    )
    server = create_server(context)

    result = call_tool(
        server,
        "stage_artifact",
        {"session_id": "session-1", "path": "reports/out.txt"},
    )

    assert result["success"] is False
    assert (
        build_internal_event_bus_for_runtime(tmp_path, base_config=context.config).list_events() == []
    )


def test_container_session_manifest_allows_stage_artifact_by_default(tmp_path: Path) -> None:
    manifest = build_container_session_manifest(
        config=GatewayConfig(runtime_dir=tmp_path),
        session_id="session-1",
        directory_path="root",
        model=None,
        ttl_hours=1,
        allowed_tools=[],
        tool_argument_policies=None,
        allowed_script_names=[],
        allowed_contact_ids=[],
        discord_channels=[],
        teams_channel_allowlist=[],
        teams_chat_allowlist=[],
        teams_user_allowlist=[],
        email_mailboxes=[],
        email_folders=[],
        metadata={},
    )

    assert manifest.allowed_tools.count(ToolName.STAGE_ARTIFACT) == 1
