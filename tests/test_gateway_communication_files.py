import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from evander import gateway
from evander.communication_artifact_links import (
    verify_communication_artifact_download_token,
)
from evander.communication_artifacts import CommunicationArtifactStore
from evander.config import GatewayConfig
from evander.directory_config import DirectoryConfigStore
from evander.directories import DirectoryService
from evander.gateway import create_server
from evander.models import SessionManifest, ToolName
from evander.runtime import build_runtime


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


class FakeTeamsFileService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def post_file(
        self,
        message: str,
        channel_target: str,
        session_id: str,
        artifacts: list[dict[str, object]],
        thread_id: str | None = None,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "message": message,
                "channel_target": channel_target,
                "session_id": session_id,
                "artifacts": artifacts,
                "thread_id": thread_id,
            }
        )
        return {
            "status": "posted",
            "message_id": "teams-message-1",
            "channel_target": channel_target,
            "session_id": session_id,
            "artifact_count": len(artifacts),
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


def test_ado_comment_tool_schemas_describe_mentions(tmp_path: Path) -> None:
    server = create_server(make_context(tmp_path))
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    for tool_name in (
        "post_ado_backlog_comment",
        "post_pr_feedback_reply",
        "post_pr_comment",
        "post_pr_inline_comment",
        "submit_pr_review",
    ):
        mentions = tools[tool_name].parameters["properties"]["mentions"]
        assert "Azure DevOps user refs to mention" in mentions["description"]
        assert "plain @name text" in mentions["description"]


def test_create_server_registers_extracted_communication_tools(tmp_path: Path) -> None:
    server = create_server(make_context(tmp_path))
    registered_tool_names = {tool.name for tool in server._tool_manager.list_tools()}

    expected_tool_names = {
        "discord_get_commands",
        "discord_post_status",
        "teams_get_messages",
        "has_pending_discord_messages",
        "list_unread_discord_messages",
        "list_all_discord_messages",
        "add_message_reaction",
        "list_contacts",
        "make_phone_call",
        "get_phone_call_result",
        "submit_voice_query_response",
        "teams_post_status",
        "teams_meeting_get_events",
        "teams_meeting_speak",
        "teams_meeting_stop",
        "email_list_unread",
        "email_get_message",
        "email_create_draft",
        "list_inline_media",
        "download_inline_media",
        "communication_list_attachments",
        "communication_download_attachment",
        "stage_artifact",
        "communication_copy_artifact_to_workspace",
        "communication_stage_workspace_file",
        "discord_post_file",
        "teams_post_file",
        "email_create_draft_with_attachments",
        "email_send",
        "email_send_with_attachments",
        "email_send_approved_with_attachments",
        "email_send_approved",
    }

    assert expected_tool_names <= registered_tool_names


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
    monkeypatch.setattr(gateway, "AgentCopyFilesEffectProvider", FakeAgentCopyFilesEffectProvider)
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


def test_post_ado_backlog_comment_uses_staged_artifact_send_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeIssueService:
        def __init__(self, provider_config: object) -> None:
            self.provider_config = provider_config

        def post_remote_comment(self, **kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            attachments = kwargs["attachments"]
            assert isinstance(attachments, list)
            assert Path(str(attachments[0]["path"])).read_bytes() == b"png-bytes"
            return {
                "provider": "azure_devops",
                "remote_issue_id": kwargs["remote_issue_id"],
                "comment": {"id": "9", "text": kwargs["comment"], "url": ""},
                "uploaded_attachments": [{"filename": "shot.png"}],
            }

    monkeypatch.setattr(gateway, "IssueService", FakeIssueService)
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.POST_ADO_BACKLOG_COMMENT],
            communication_file_send_enabled=True,
            communication_file_max_bytes=100,
            communication_file_allowed_mime_types=["image/png"],
            communication_file_allowed_extensions=[".png"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    artifact = CommunicationArtifactStore(tmp_path).stage(
        provider="workspace",
        session_id="session-1",
        content=b"png-bytes",
        original_filename="shot.png",
        mime_type="image/png",
    )
    server = create_server(context)

    result = call_tool(
        server,
        "post_ado_backlog_comment",
        {
            "session_id": "session-1",
            "id": 123,
            "project": "Project",
            "comment": "Screenshot",
            "artifact_ids": [artifact.artifact_id],
        },
    )

    assert result["comment"]["id"] == "9"
    assert calls[0]["provider"] == "azure_devops"
    assert calls[0]["remote_issue_id"] == "123"
    assert calls[0]["project"] == "Project"
    assert calls[0]["attachments"][0]["artifact_id"] == artifact.artifact_id


def test_teams_post_file_publishes_non_image_as_signed_control_plane_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEAM_LINK_SECRET", "test-secret")
    context = make_context(
        tmp_path,
        communication_artifact_public_base_url="https://root-control.example.test",
    )
    team_directory = DirectoryService(context.directories).create(parent="root", name="team")
    DirectoryConfigStore(context.directories, runtime_dir=context.config.runtime_dir).write(
        team_directory.path,
        domain="providers",
        payload={
            "communication_artifact_public_base_url": "https://control.example.test",
            "communication_artifact_link_secret_env": "TEAM_LINK_SECRET",
        },
    )
    fake_service = FakeTeamsFileService()
    context.teams_service = fake_service
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.TEAMS_POST_FILE],
            teams_channel_allowlist=["team-a/channel-1"],
            communication_file_send_enabled=True,
            communication_file_max_bytes=100,
            communication_file_allowed_mime_types=["text/plain"],
            communication_file_allowed_extensions=[".txt"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            directory_path=team_directory.path,
        ),
    )
    artifact = CommunicationArtifactStore(tmp_path).stage(
        provider="workspace",
        session_id="session-1",
        content=b"hello",
        original_filename="hello.txt",
        mime_type="text/plain",
    )
    server = create_server(context)

    result = call_tool(
        server,
        "teams_post_file",
        {
            "session_id": "session-1",
            "channel_target": "team-a/channel-1",
            "message": "Here is the file",
            "artifact_ids": [artifact.artifact_id],
        },
    )

    assert result["status"] == "posted"
    assert result["published_links"][0]["artifact_id"] == artifact.artifact_id
    posted_artifacts = fake_service.calls[0]["artifacts"]
    assert isinstance(posted_artifacts, list)
    download_url = str(posted_artifacts[0]["download_url"])
    parsed = urlparse(download_url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "control.example.test"
    assert parsed.path == f"/public/communication-artifacts/{artifact.artifact_id}"
    token = parse_qs(parsed.query)["token"][0]
    assert (
        verify_communication_artifact_download_token(
            artifact_id=artifact.artifact_id,
            token=token,
            secret_env="TEAM_LINK_SECRET",
        )
        == artifact.artifact_id
    )
    audit_text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "published_links" in audit_text
    assert "token=" not in audit_text


def test_inline_media_pr_feedback_downloads_and_copies_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evander.pr_feedback import FeedbackEnvelope

    FakeAgentCopyFilesEffectProvider.calls = []
    monkeypatch.setattr(gateway, "AgentCopyFilesEffectProvider", FakeAgentCopyFilesEffectProvider)
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
                "pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]
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
    audit_events = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    list_event = next(event for event in audit_events if event["event_type"] == "inline_media_list")
    assert list_event["provider"] == "github"
    assert list_event["pr_id"] == "17"
    assert list_event["result"]["media"][0]["comment_id"] == "comment-1"
    assert list_event["result"]["media"][0]["mime_type"] == "image/png"
    assert list_event["result"]["media"][0]["byte_count"] == len(b"png-bytes")
    download_event = next(
        event for event in audit_events if event["event_type"] == "inline_media_download"
    )
    assert download_event["provider"] == "github"
    assert download_event["pr_id"] == "17"
    assert download_event["result"]["media"][0]["comment_id"] == "comment-1"
    assert download_event["result"]["media"][0]["mime_type"] == "image/png"
    assert download_event["result"]["media"][0]["byte_count"] == len(b"png-bytes")

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
    assert (
        FakeAgentCopyFilesEffectProvider.calls[0]
        .files[0]
        .source_path.endswith(f"{artifact['artifact_id']}.bin")
    )


def test_inline_media_ado_backlog_uses_issue_service_and_content_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeIssueService:
        def __init__(self, provider_config: object) -> None:
            self.provider_config = provider_config

        def get_remote_backlog_item_by_id(self, **kwargs: object) -> dict[str, object]:
            assert kwargs["provider"] == "azure_devops"
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

    assert listed["provider"] == "azure_devops"
    assert listed["media"][0]["source_kind"] == "ado_backlog"
    assert listed["media"][0]["provider"] == "azure_devops"
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
    assert artifact["provider"] == "azure_devops"
    assert artifact["mime_type"] == "image/jpeg"
    assert artifact["metadata"]["source_kind"] == "ado_backlog"
    staged = CommunicationArtifactStore(tmp_path).get(artifact["artifact_id"])
    assert staged.blob_path.read_bytes() == b"jpeg-bytes"


def test_ado_backlog_inline_media_does_not_duplicate_aggregate_comment_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeIssueService:
        def __init__(self, provider_config: object) -> None:
            self.provider_config = provider_config

        def get_remote_backlog_item_by_id(self, **kwargs: object) -> dict[str, object]:
            assert kwargs["include_comments"] is True
            return {
                "provider": "azure_devops",
                "id": "123",
                "project": "Project",
                "title": "Bug with screenshots",
                "inline_media": [
                    {
                        "source_kind": "ado_backlog",
                        "provider": "azure_devops",
                        "resource_id": "123",
                        "media_id": "field-image",
                        "filename": "field.png",
                        "mime_type": "image/png",
                    },
                    {
                        "source_kind": "ado_backlog",
                        "provider": "azure_devops",
                        "resource_id": "123",
                        "comment_id": "comment-7",
                        "media_id": "comment-image",
                        "filename": "comment.png",
                        "mime_type": "image/png",
                    },
                ],
                "comments": [
                    {
                        "id": "comment-7",
                        "inline_media": [
                            {
                                "media_id": "comment-image",
                                "filename": "comment.png",
                                "mime_type": "image/png",
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
            allowed_tools=[ToolName.LIST_INLINE_MEDIA],
            communication_file_receive_enabled=True,
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
    scoped = call_tool(
        server,
        "list_inline_media",
        {
            "session_id": "session-1",
            "source_kind": "ado_backlog",
            "provider": "ado",
            "resource_id": "123",
            "project": "Project",
            "comment_id": "comment-7",
        },
    )

    assert [media["media_id"] for media in listed["media"]] == [
        "field-image",
        "comment-image",
    ]
    assert [media["media_id"] for media in scoped["media"]] == ["comment-image"]


def test_pr_feedback_inline_media_rejects_mime_before_staging(tmp_path: Path) -> None:
    from evander.pr_feedback import FeedbackEnvelope

    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.DOWNLOAD_INLINE_MEDIA],
            communication_file_receive_enabled=True,
            communication_file_max_bytes=100,
            communication_file_allowed_mime_types=["image/png"],
            communication_file_allowed_extensions=[".png"],
            metadata={
                "pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]
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
                    "mime_type": "image/gif",
                    "byte_count": len(b"gif-bytes"),
                    "content_base64": base64.b64encode(b"gif-bytes").decode("ascii"),
                }
            ],
        )
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="MIME type 'image/gif' is not allowlisted"):
        call_tool(
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

    artifact_dir = tmp_path / "communication-artifacts"
    assert not artifact_dir.exists() or list(artifact_dir.glob("comm_*")) == []
    audit_events = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    failure = audit_events[-1]
    assert failure["tool_name"] == "download_inline_media"
    assert failure["decision"] == "denied"
    assert failure["args"]["source_kind"] == "pr_feedback"
    assert failure["args"]["provider"] == "github"
    assert failure["args"]["resource_id"] == "feedback-1"
    assert failure["args"]["comment_id"] == "comment-1"
    assert failure["args"]["mime_type"] == "image/gif"
    assert failure["args"]["byte_count"] == len(b"gif-bytes")


def test_ado_backlog_inline_media_rejects_oversize_before_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeIssueService:
        def __init__(self, provider_config: object) -> None:
            self.provider_config = provider_config

        def get_remote_backlog_item_by_id(self, **kwargs: object) -> dict[str, object]:
            assert kwargs["backlog_item_id"] == "123"
            return {
                "provider": "ado",
                "id": "123",
                "project": "Project",
                "title": "Bug with screenshot",
                "comments": [
                    {
                        "id": "comment-7",
                        "inline_media": [
                            {
                                "media_id": "ado-image-1",
                                "filename": "screen.png",
                                "mime_type": "image/png",
                                "byte_count": len(b"too-large"),
                                "content_base64": base64.b64encode(b"too-large").decode("ascii"),
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
            allowed_tools=[ToolName.DOWNLOAD_INLINE_MEDIA],
            communication_file_receive_enabled=True,
            communication_file_max_bytes=4,
            communication_file_allowed_mime_types=["image/png"],
            communication_file_allowed_extensions=[".png"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="exceeds max bytes"):
        call_tool(
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

    artifact_dir = tmp_path / "communication-artifacts"
    assert not artifact_dir.exists() or list(artifact_dir.glob("comm_*")) == []
    audit_events = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    failure = audit_events[-1]
    assert failure["tool_name"] == "download_inline_media"
    assert failure["decision"] == "denied"
    assert failure["args"]["source_kind"] == "ado_backlog"
    assert failure["args"]["provider"] == "azure_devops"
    assert failure["args"]["resource_id"] == "123"
    assert failure["args"]["work_item_id"] == "123"
    assert failure["args"]["comment_id"] == "comment-7"
    assert failure["args"]["mime_type"] == "image/png"
    assert failure["args"]["byte_count"] == len(b"too-large")


def test_pr_feedback_inline_media_requires_receive_permission(tmp_path: Path) -> None:
    from evander.pr_feedback import FeedbackEnvelope

    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.DOWNLOAD_INLINE_MEDIA],
            metadata={
                "pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]
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
                    "filename": "review.png",
                    "mime_type": "image/png",
                    "content_base64": base64.b64encode(b"png-bytes").decode("ascii"),
                }
            ],
        )
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="Inline media receive is disabled for pr_feedback"):
        call_tool(
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

    artifact_dir = tmp_path / "communication-artifacts"
    assert not artifact_dir.exists() or list(artifact_dir.glob("comm_*")) == []
