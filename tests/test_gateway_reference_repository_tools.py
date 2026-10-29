from __future__ import annotations

from gateway_tool_support import call_tool as _call_tool

from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

import evander.gateway as gateway_module
from mcp.server.mcpserver.exceptions import ToolError
import pytest

from evander.config import GatewayConfig
from evander.directories import DirectoryService
from evander.gateway import create_server
from evander.gateway import reference_repository_tools
from evander.models import SessionManifest, ToolName
from evander.runtime import build_runtime


_ALLOWED_REPO_ID = "mr_0123456789abcdef0123"
_DENIED_REPO_ID = "mr_fedcba9876543210fedc"


def _runtime(tmp_path):
    runtime_dir = tmp_path / "runtime"
    context = build_runtime(
        GatewayConfig(
            runtime_dir=runtime_dir,
            audit_log_path=runtime_dir / "audit.jsonl",
            session_store_path=runtime_dir / "sessions.json",
            approval_store_path=runtime_dir / "approvals.json",
        )
    )
    DirectoryService(context.directories).create(parent="root", name="example")
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="techops-agent",
            directory_path="root/example",
            allowed_tools=[ToolName.PREPARE_REFERENCE_REPOSITORY],
            tool_argument_policies={
                ToolName.PREPARE_REFERENCE_REPOSITORY.value: [
                    {
                        "id": "techops-reference-repositories",
                        "name": "TechOps reference repository allowlist",
                        "constraints": [
                            {
                                "id": "managed-repository",
                                "path": "managed_repo_id",
                                "operator": "allowlist",
                                "values": [_ALLOWED_REPO_ID],
                            }
                        ],
                    }
                ]
            },
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    return context


def _audit_events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_prepare_reference_repository_uses_calling_session_and_fresh_read_only_provider(
    tmp_path,
    monkeypatch,
) -> None:
    context = _runtime(tmp_path)
    captured: dict[str, object] = {}

    class StubProvider:
        def initialize(self, effect_definition, runtime_context) -> None:
            captured["definition"] = effect_definition
            captured["context"] = runtime_context

        def execute(self):
            return {
                "success": True,
                "session_id": "techops-agent",
                "repositories": [
                    {
                        "repo_source": "ssh://internal-bare-repository",
                        "managed_repo_id": _ALLOWED_REPO_ID,
                        "repo_name": "SampleApplication",
                        "source_ref": "refs/heads/main",
                        "commit_sha": "a" * 40,
                        "target_dir": "/home/agent/repos/SampleApplication",
                        "refresh_performed": True,
                        "checkout_updated": True,
                        "access_reused": False,
                        "trust_reconciled": False,
                    }
                ],
            }

    monkeypatch.setattr(
        reference_repository_tools,
        "AgentPrepareReferenceRepositoriesEffectProvider",
        StubProvider,
    )
    monkeypatch.setattr(
        gateway_module,
        "build_admin_runtime",
        lambda _runtime_dir: SimpleNamespace(),
    )

    result = _call_tool(
        create_server(context),
        ToolName.PREPARE_REFERENCE_REPOSITORY.value,
        {"session_id": "techops-agent", "managed_repo_id": _ALLOWED_REPO_ID},
    )

    definition = captured["definition"]
    assert definition.session_id == "techops-agent"
    assert definition.directory_path == "root/example"
    assert definition.repositories == [{"managed_repo_id": _ALLOWED_REPO_ID}]
    assert definition.refresh_upstream is True
    assert captured["context"].metadata == {"directory_path": "root/example"}
    assert result == {
        "success": True,
        "managed_repo_id": _ALLOWED_REPO_ID,
        "repo_name": "SampleApplication",
        "source_ref": "refs/heads/main",
        "commit_sha": "a" * 40,
        "target_dir": "/home/agent/repos/SampleApplication",
        "refresh_performed": True,
        "checkout_updated": True,
        "access_reused": False,
        "trust_reconciled": False,
    }
    assert "repo_source" not in result
    event = _audit_events(context.config.audit_log_path)[-1]
    assert event["tool_name"] == ToolName.PREPARE_REFERENCE_REPOSITORY.value
    assert event["decision"] == "allowed"
    assert event["result"]["managed_repo_id"] == _ALLOWED_REPO_ID


def test_prepare_reference_repository_policy_denies_unlisted_repository_before_provider(
    tmp_path,
    monkeypatch,
) -> None:
    context = _runtime(tmp_path)

    class UnexpectedProvider:
        def __init__(self) -> None:
            raise AssertionError("provider must not be constructed for denied arguments")

    monkeypatch.setattr(
        reference_repository_tools,
        "AgentPrepareReferenceRepositoriesEffectProvider",
        UnexpectedProvider,
    )

    with pytest.raises(ToolError, match="tool_argument_policy_denied"):
        _call_tool(
            create_server(context),
            ToolName.PREPARE_REFERENCE_REPOSITORY.value,
            {"session_id": "techops-agent", "managed_repo_id": _DENIED_REPO_ID},
        )

    event = _audit_events(context.config.audit_log_path)[-1]
    assert event["tool_name"] == ToolName.PREPARE_REFERENCE_REPOSITORY.value
    assert event["decision"] == "denied"
    assert event["result"]["error_class"] == "tool_argument_policy_violation"
    assert event["result"]["field_path"] == "managed_repo_id"


def test_prepare_reference_repository_requires_explicit_tool_grant(
    tmp_path,
    monkeypatch,
) -> None:
    context = _runtime(tmp_path)
    session = context.session_repository.get_session("techops-agent")
    assert session is not None
    context.session_repository.upsert_session(session.model_copy(update={"allowed_tools": []}))

    class UnexpectedProvider:
        def __init__(self) -> None:
            raise AssertionError("provider must not be constructed without a tool grant")

    monkeypatch.setattr(
        reference_repository_tools,
        "AgentPrepareReferenceRepositoriesEffectProvider",
        UnexpectedProvider,
    )

    with pytest.raises(ToolError, match="not allowed"):
        _call_tool(
            create_server(context),
            ToolName.PREPARE_REFERENCE_REPOSITORY.value,
            {"session_id": "techops-agent", "managed_repo_id": _ALLOWED_REPO_ID},
        )
