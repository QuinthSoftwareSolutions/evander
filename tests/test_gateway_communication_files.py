import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from agent_sandbox import gateway
from agent_sandbox.communication_artifacts import CommunicationArtifactStore
from agent_sandbox.config import GatewayConfig
from agent_sandbox.gateway import create_server
from agent_sandbox.models import SessionManifest, ToolName
from agent_sandbox.runtime import build_runtime


class FakeAgentExecEffectProvider:
    stdout: str = ""
    calls: list[object] = []

    def initialize(self, *, effect_definition: object, runtime_context: object) -> None:
        self.effect_definition = effect_definition
        self.runtime_context = runtime_context

    def execute(self) -> dict[str, object]:
        self.calls.append(self.effect_definition)
        return {
            "success": True,
            "stdout": self.stdout,
            "stderr": "",
            "exit_code": 0,
        }


class FakeAgentCopyFilesEffectProvider:
    calls: list[object] = []

    def initialize(self, *, effect_definition: object, runtime_context: object) -> None:
        self.effect_definition = effect_definition
        self.runtime_context = runtime_context

    def execute(self) -> dict[str, object]:
        self.calls.append(self.effect_definition)
        copied_paths = [
            item.destination_path for item in getattr(self.effect_definition, "files", [])
        ]
        return {
            "copied_count": len(copied_paths),
            "copied_paths": copied_paths,
        }


def make_context(tmp_path: Path, **kwargs: object):
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
        **kwargs,
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


def workspace_file_stdout(
    *,
    source_path: str,
    filename: str,
    mime_type: str,
    content: bytes,
) -> str:
    payload = {
        "source_path": source_path,
        "filename": filename,
        "mime_type": mime_type,
        "byte_count": len(content),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }
    return "\n".join(
        [
            gateway._WORKSPACE_FILE_PAYLOAD_BEGIN,
            json.dumps(payload),
            gateway._WORKSPACE_FILE_PAYLOAD_END,
            "",
        ]
    )


def test_communication_stage_workspace_file_stages_agent_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAgentExecEffectProvider.calls = []
    FakeAgentExecEffectProvider.stdout = workspace_file_stdout(
        source_path="/home/agent/outgoing_hello.txt",
        filename="outgoing_hello.txt",
        mime_type="text/plain",
        content=b"hello from the agent\n",
    )
    monkeypatch.setattr(gateway, "AgentExecEffectProvider", FakeAgentExecEffectProvider)
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.COMMUNICATION_STAGE_WORKSPACE_FILE],
            communication_file_send_enabled=True,
            communication_file_max_bytes=100,
            communication_file_allowed_extensions=[".txt"],
            communication_file_allowed_mime_types=["text/plain"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    result = call_tool(
        server,
        "communication_stage_workspace_file",
        {
            "session_id": "session-1",
            "source_path": "/home/agent/outgoing_hello.txt",
            "filename": "hello.txt",
            "mime_type": "text/plain",
        },
    )

    artifact = result["artifact"]
    assert artifact["provider"] == "workspace"
    assert artifact["filename"] == "hello.txt"
    assert artifact["mime_type"] == "text/plain"
    assert artifact["byte_count"] == len(b"hello from the agent\n")
    assert artifact["metadata"]["source"] == "agent_workspace"
    assert artifact["metadata"]["source_path"] == "/home/agent/outgoing_hello.txt"
    staged = CommunicationArtifactStore(tmp_path).get(artifact["artifact_id"])
    assert staged.blob_path.read_bytes() == b"hello from the agent\n"
    effect = FakeAgentExecEffectProvider.calls[0]
    assert effect.command[-2:] == ["/home/agent/outgoing_hello.txt", "100"]


def test_communication_copy_artifact_to_workspace_uses_agent_copy_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAgentCopyFilesEffectProvider.calls = []
    monkeypatch.setattr(
        gateway, "AgentCopyFilesEffectProvider", FakeAgentCopyFilesEffectProvider
    )
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE],
            communication_file_receive_enabled=True,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    record = CommunicationArtifactStore(tmp_path).stage(
        provider="teams",
        session_id="session-1",
        content=b"image-bytes",
        original_filename="Picture.png",
        mime_type="image/png",
        source_message_id="message-1",
        source_attachment_id="attachment-1",
        source_channel="19:channel@thread.skype",
    )
    server = create_server(context)

    result = call_tool(
        server,
        "communication_copy_artifact_to_workspace",
        {
            "session_id": "session-1",
            "artifact_id": record.artifact_id,
            "destination_filename": "Picture.png",
            "overwrite": True,
        },
    )

    assert result["artifact"]["artifact_id"] == record.artifact_id
    assert result["destination_path"] == "/home/agent/communication-artifacts/Picture.png"
    assert result["copied_count"] == 1
    assert result["copied_paths"] == [
        "/home/agent/communication-artifacts/Picture.png",
    ]
    effect = FakeAgentCopyFilesEffectProvider.calls[0]
    assert effect.display_name == "Copy communication artifact to workspace"
    assert effect.files[0].source_path.endswith(f"{record.artifact_id}.bin")
    assert effect.files[0].destination_path == result["destination_path"]


def test_communication_stage_workspace_file_rejects_non_workspace_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAgentExecEffectProvider.calls = []
    monkeypatch.setattr(gateway, "AgentExecEffectProvider", FakeAgentExecEffectProvider)
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.COMMUNICATION_STAGE_WORKSPACE_FILE],
            communication_file_send_enabled=True,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="source_path must be under /home/agent"):
        call_tool(
            server,
            "communication_stage_workspace_file",
            {"session_id": "session-1", "source_path": "/etc/passwd"},
        )

    assert FakeAgentExecEffectProvider.calls == []


def test_inline_media_pr_feedback_downloads_and_copies_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_sandbox.daemons.pr_feedback_worker import FeedbackEnvelope

    FakeAgentCopyFilesEffectProvider.calls = []
    monkeypatch.setattr(
        gateway, "AgentCopyFilesEffectProvider", FakeAgentCopyFilesEffectProvider
    )
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[
                ToolName.LIST_INLINE_MEDIA,
                ToolName.DOWNLOAD_INLINE_MEDIA,
                ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE,
            ],
            communication_file_receive_enabled=True,
            communication_file_max_bytes=100,
            communication_file_allowed_mime_types=["image/png"],
            communication_file_allowed_extensions=[".png"],
            metadata={
                "pr_feedback_streams": [
                    {"provider": "github", "repo": "org/repo", "pr_id": "17"}
                ]
            },
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    context.pr_feedback_service.store.add_feedback_if_new(
        FeedbackEnvelope(
            feedback_id="feedback-1",
            provider="github",
            repo="org/repo",
            pr_id="17",
            comment_id="comment-1",
            author="reviewer",
            body="See inline image",
            created_at=datetime.now(UTC),
            inline_media=[
                {
                    "media_id": "media-1",
                    "comment_id": "comment-1",
                    "filename": "review.png",
                    "mime_type": "image/png",
                    "byte_count": len(b"png-bytes"),
                    "content_base64": base64.b64encode(b"png-bytes").decode("ascii"),
                    "provider_metadata": {"provider_file_id": "gh-file-1"},
                }
            ],
        )
    )
    server = create_server(context)

    listed = call_tool(
        server,
        "list_inline_media",
        {
            "session_id": "session-1",
            "source_kind": "pr_feedback",
            "provider": "github",
            "resource_id": "feedback-1",
        },
    )

    assert listed["source_kind"] == "pr_feedback"
    assert listed["media"][0]["media_id"] == "media-1"
    assert listed["media"][0]["filename"] == "review.png"
    assert "content_base64" not in listed["media"][0]["metadata"]

    downloaded = call_tool(
        server,
        "download_inline_media",
        {
            "session_id": "session-1",
            "source_kind": "pr_feedback",
            "provider": "github",
            "resource_id": "feedback-1",
            "media_id": "media-1",
        },
    )

    artifact = downloaded["artifact"]
    assert artifact["artifact_id"].startswith("comm_")
    assert artifact["provider"] == "github"
    assert artifact["source_message_id"] == "feedback-1"
    assert artifact["source_attachment_id"] == "media-1"
    assert artifact["metadata"]["source_kind"] == "pr_feedback"
    staged = CommunicationArtifactStore(tmp_path).get(artifact["artifact_id"])
    assert staged.blob_path.read_bytes() == b"png-bytes"

    copied = call_tool(
        server,
        "communication_copy_artifact_to_workspace",
        {
            "session_id": "session-1",
            "artifact_id": artifact["artifact_id"],
            "destination_filename": "review.png",
        },
    )

    assert copied["destination_path"] == "/home/agent/communication-artifacts/review.png"
    assert FakeAgentCopyFilesEffectProvider.calls[0].files[0].source_path.endswith(
        f"{artifact['artifact_id']}.bin"
    )


def test_inline_media_ado_backlog_uses_issue_service_and_content_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeIssueService:
        def __init__(self, provider_config: object) -> None:
            self.provider_config = provider_config

        def get_remote_backlog_item_by_id(self, **kwargs: object) -> dict[str, object]:
            assert kwargs["provider"] == "ado"
            assert kwargs["backlog_item_id"] == "123"
            return {
                "provider": "ado",
                "id": "123",
                "project": "Project",
                "title": "Bug with screenshot",
                "comments": [
                    {
                        "id": "comment-7",
                        "text": "Screenshot",
                        "inline_media": [
                            {
                                "media_id": "ado-image-1",
                                "filename": "screen.jpeg",
                                "mime_type": "image/jpeg",
                                "byte_count": len(b"jpeg-bytes"),
                                "content_base64": base64.b64encode(b"jpeg-bytes").decode("ascii"),
                            }
                        ],
                    }
                ],
            }

    monkeypatch.setattr(gateway, "IssueService", FakeIssueService)
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.LIST_INLINE_MEDIA, ToolName.DOWNLOAD_INLINE_MEDIA],
            communication_file_receive_enabled=True,
            communication_file_max_bytes=100,
            communication_file_allowed_mime_types=["image/jpeg"],
            communication_file_allowed_extensions=[".jpeg"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    listed = call_tool(
        server,
        "list_inline_media",
        {
            "session_id": "session-1",
            "source_kind": "ado_backlog",
            "provider": "ado",
            "resource_id": "123",
            "project": "Project",
        },
    )

    assert listed["media"][0]["source_kind"] == "ado_backlog"
    assert listed["media"][0]["comment_id"] == "comment-7"
    assert listed["media"][0]["media_id"] == "ado-image-1"

    downloaded = call_tool(
        server,
        "download_inline_media",
        {
            "session_id": "session-1",
            "source_kind": "ado_backlog",
            "provider": "ado",
            "resource_id": "123",
            "media_id": "ado-image-1",
            "project": "Project",
        },
    )

    artifact = downloaded["artifact"]
    assert artifact["artifact_id"].startswith("comm_")
    assert artifact["provider"] == "ado"
    assert artifact["mime_type"] == "image/jpeg"
    assert artifact["metadata"]["source_kind"] == "ado_backlog"
    staged = CommunicationArtifactStore(tmp_path).get(artifact["artifact_id"])
    assert staged.blob_path.read_bytes() == b"jpeg-bytes"
