import asyncio
import base64
import json
from io import BytesIO
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import httpx
from mcp.server.fastmcp.exceptions import ToolError
from PIL import Image

from evander import gateway
from evander.communication_artifact_links import (
    verify_communication_artifact_download_token,
)
from evander.communication_artifacts import CommunicationArtifactStore
from evander.config import GatewayConfig
from evander.directory_config import DirectoryConfigStore
from evander.directories import DirectoryService
from evander.gateway import create_server
from evander.inline_media import InlineMediaDownload
from evander.models import GetPullRequestResponse, SessionManifest, ToolName
from evander.issues.service import AzureDevOpsIssueAdapter
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
        "email_list_mailboxes",
        "email_list_unread",
        "email_list_messages",
        "email_get_message",
        "email_create_draft",
        "list_inline_media",
        "download_inline_media",
        "list_pr_feedback_attachments",
        "download_pr_feedback_attachment",
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


def test_ado_pr_feedback_attachment_tools_resolve_guid_scope_without_pr_map(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evander.pr_feedback import FeedbackEnvelope

    project_id = "70a1569b-16ac-476e-a6b5-44830b54dcc7"
    repository_id = "d30d0ddf-69c9-4a6b-98e4-1bc60506b8a1"
    other_repository_id = "11111111-2222-3333-4444-555555555555"
    attachment_url = (
        f"https://dev.azure.com/example/{project_id}/_apis/git/repositories/{repository_id}/"
        "pullRequests/9299/attachments/review%20notes.pdf"
    )
    forged_url = (
        "https://attacker.example/Project/_apis/git/repositories/repo-1/"
        "pullRequests/9299/attachments/forged.pdf"
    )
    traversal_url = (
        "https://dev.azure.com/example/Project/_apis/wit/attachments/"
        "../../git/repositories?api-version=7.1"
    )
    cross_repo_url = (
        f"https://dev.azure.com/example/{project_id}/_apis/git/repositories/"
        f"{other_repository_id}/"
        "pullRequests/9299/attachments/other.pdf"
    )
    download_calls: list[str | None] = []
    pr_lookup_calls: list[dict[str, object]] = []

    class FakePrQueryService:
        def get_pull_request(self, **kwargs: object) -> GetPullRequestResponse:
            pr_lookup_calls.append(kwargs)
            return GetPullRequestResponse(
                provider="azure_devops",
                repo="repo-1",
                pr_id="9299",
                raw={
                    "pullRequestId": 9299,
                    "repository": {
                        "id": repository_id,
                        "name": "repo-1",
                        "project": {"id": project_id, "name": "Project"},
                    },
                },
            )

    class FakeAzureDevOpsFeedbackAdapter:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def download_inline_media(
            self,
            *,
            media: object,
            raw: object = None,
            max_bytes: int | None = None,
        ) -> InlineMediaDownload:
            del raw
            url = getattr(media, "url", None)
            download_calls.append(url)
            assert url == attachment_url
            assert max_bytes == 1024
            scope = getattr(media, "metadata")["ado_pr_feedback_attachment_scope"]
            assert scope["project_identifiers"] == ["Project", project_id]
            assert scope["repository_identifiers"] == ["repo-1", repository_id]
            return InlineMediaDownload(
                content=b"%PDF-1.7 test attachment",
                filename="review notes.pdf",
                mime_type="application/pdf",
            )

    monkeypatch.setattr(
        gateway,
        "AzureDevOpsFeedbackAdapter",
        FakeAzureDevOpsFeedbackAdapter,
    )
    context = make_context(
        tmp_path,
        azure_devops_organization_url="https://dev.azure.com/example",
        azure_devops_project="Project",
        pr_map_path=tmp_path / "pr-map.json",
    )
    context.pr_query_service = FakePrQueryService()
    assert not context.config.pr_map_path.exists()
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[
                ToolName.LIST_PR_FEEDBACK_ATTACHMENTS,
                ToolName.DOWNLOAD_PR_FEEDBACK_ATTACHMENT,
                ToolName.LIST_INLINE_MEDIA,
            ],
            communication_file_receive_enabled=True,
            communication_file_max_bytes=1024,
            communication_file_allowed_mime_types=["application/json", "application/pdf"],
            communication_file_allowed_extensions=[".json", ".pdf"],
            metadata={
                "pr_feedback_streams": [
                    {"provider": "azure_devops", "repo": "repo-1", "pr_id": "9299"}
                ]
            },
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    context.pr_feedback_service.store.add_feedback_if_new(
        FeedbackEnvelope(
            feedback_id="azure_devops:thread_comment:45:93",
            provider="azure_devops",
            repo="repo-1",
            pr_id="9299",
            thread_id="45",
            comment_id="93",
            author="reviewer",
            body=(
                f"[review notes]({attachment_url}) [forged]({forged_url}) "
                f"[repos.json]({traversal_url}) [other]({cross_repo_url})"
            ),
            created_at=datetime.now(UTC),
            inline_media=[
                {
                    "source_kind": "pr_feedback",
                    "provider": "azure_devops",
                    "resource_id": "azure_devops:thread_comment:45:93",
                    "comment_id": "93",
                    "media_id": "valid-inline",
                    "filename": "review notes.pdf",
                    "mime_type": "application/pdf",
                    "url": attachment_url,
                },
                {
                    "source_kind": "pr_feedback",
                    "provider": "azure_devops",
                    "resource_id": "azure_devops:thread_comment:45:93",
                    "comment_id": "93",
                    "media_id": "traversal-inline",
                    "filename": "repos.json",
                    "mime_type": "application/json",
                    "url": traversal_url,
                },
            ],
        )
    )
    server = create_server(context)

    listed = call_tool(
        server,
        "list_pr_feedback_attachments",
        {
            "session_id": "session-1",
            "feedback_id": "azure_devops:thread_comment:45:93",
        },
    )

    assert listed["provider"] == "azure_devops"
    assert listed["repo"] == "repo-1"
    assert listed["pr_id"] == "9299"
    assert listed["thread_id"] == "45"
    assert listed["comment_id"] == "93"
    assert len(listed["attachments"]) == 1
    attachment = listed["attachments"][0]
    assert attachment["filename"] == "review notes.pdf"
    assert attachment["mime_type"] == "application/pdf"
    assert "url" not in attachment

    inline_media = call_tool(
        server,
        "list_inline_media",
        {
            "session_id": "session-1",
            "source_kind": "pr_feedback",
            "provider": "azure_devops",
            "resource_id": "azure_devops:thread_comment:45:93",
        },
    )
    assert [item["media_id"] for item in inline_media["media"]] == ["valid-inline"]

    downloaded = call_tool(
        server,
        "download_pr_feedback_attachment",
        {
            "session_id": "session-1",
            "feedback_id": "azure_devops:thread_comment:45:93",
            "attachment_id": attachment["attachment_id"],
        },
    )

    artifact = downloaded["artifact"]
    assert len(pr_lookup_calls) == 3
    assert all(
        call
        == {
            "provider": "azure_devops",
            "repo": "repo-1",
            "pr_id": "9299",
            "include_reviewers": False,
            "include_commits": False,
        }
        for call in pr_lookup_calls
    )
    assert download_calls == [attachment_url]
    assert artifact["provider"] == "azure_devops"
    assert artifact["source_message_id"] == "azure_devops:thread_comment:45:93"
    assert artifact["source_attachment_id"] == attachment["attachment_id"]
    assert artifact["filename"] == "review-notes.pdf"
    assert artifact["mime_type"] == "application/pdf"
    assert artifact["metadata"]["source_kind"] == "pr_feedback_attachment"
    assert "url" not in artifact["metadata"]["attachment"]
    staged = CommunicationArtifactStore(tmp_path).get(artifact["artifact_id"])
    assert staged.blob_path.read_bytes() == b"%PDF-1.7 test attachment"
    assert attachment_url not in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")


def test_pr_feedback_attachment_tools_require_receive_permission(tmp_path: Path) -> None:
    from evander.pr_feedback import FeedbackEnvelope

    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.LIST_PR_FEEDBACK_ATTACHMENTS],
            metadata={
                "pr_feedback_streams": [
                    {"provider": "azure_devops", "repo": "repo-1", "pr_id": "9299"}
                ]
            },
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    context.pr_feedback_service.store.add_feedback_if_new(
        FeedbackEnvelope(
            feedback_id="azure_devops:thread_comment:45:93",
            provider="azure_devops",
            repo="repo-1",
            pr_id="9299",
            comment_id="93",
            author="reviewer",
            body="attachment",
            created_at=datetime.now(UTC),
        )
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="Inbound communication file receive is disabled"):
        call_tool(
            server,
            "list_pr_feedback_attachments",
            {
                "session_id": "session-1",
                "feedback_id": "azure_devops:thread_comment:45:93",
            },
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


def test_ado_markdown_images_download_and_copy_as_viewable_workspace_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    images = {}
    for name, size in (("first", (3, 2)), ("second", (4, 3))):
        buffer = BytesIO()
        Image.new("RGB", size, "blue").save(buffer, format="PNG")
        images[name] = buffer.getvalue()
    base_url = "https://dev.azure.com/example/_apis/wit/attachments/"
    description = (
        f"Operator dialog\n![image.png]({base_url}first?fileName=image.png)\n\n"
        f"Admin report\n![image.png]({base_url}second?fileName=image.png)"
    )
    download_requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == AzureDevOpsIssueAdapter._auth_header("host-pat")
        if "/attachments/" in request.url.path:
            download_requests.append(str(request.url))
            return httpx.Response(
                200,
                content=images[request.url.path.rsplit("/", 1)[-1]],
                headers={"content-type": "image/png"},
            )
        if request.url.path.endswith("/comments"):
            return httpx.Response(200, json={"comments": []})
        assert request.url.path.endswith("/workitems/101")
        return httpx.Response(
            200,
            json={
                "id": 101,
                "fields": {"System.Description": description},
                "relations": [],
            },
        )

    client_type = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    monkeypatch.setattr(
        AzureDevOpsIssueAdapter,
        "_resolve_connection",
        lambda self, directory: ("https://dev.azure.com/example", "Project", "host-pat"),
    )

    class IssueService:
        def __init__(self, provider_config: object) -> None:
            self.adapter = AzureDevOpsIssueAdapter(provider_config)

        def get_remote_backlog_item_by_id(self, *, provider: str, **kwargs: object):
            return self.adapter.get_backlog_item_by_id(**kwargs)

    class CopyFiles(FakeAgentCopyFilesEffectProvider):
        def execute(self) -> dict[str, object]:
            for item in self.effect_definition.files:
                destination = (
                    tmp_path / "workspace" / Path(item.destination_path).relative_to("/home/agent")
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(Path(item.source_path).read_bytes())
            return super().execute()

    monkeypatch.setattr(gateway, "IssueService", IssueService)
    monkeypatch.setattr(gateway, "AgentCopyFilesEffectProvider", CopyFiles)
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[
                ToolName.GET_ADO_BACKLOG_ITEM,
                ToolName.LIST_INLINE_MEDIA,
                ToolName.DOWNLOAD_INLINE_MEDIA,
                ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE,
            ],
            communication_file_receive_enabled=True,
            communication_file_max_bytes=1024,
            communication_file_allowed_mime_types=["image/png"],
            communication_file_allowed_extensions=[".png"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    server = create_server(context)
    item = call_tool(
        server,
        "get_ado_backlog_item",
        {
            "session_id": "session-1",
            "id": "101",
            "project": "Project",
            "include_comments": False,
            "include_relations": False,
        },
    )
    assert len(item["inline_media"]) == 2
    assert item["metadata"]["inline_media_access"]["unavailable_tools"] == {}
    media_request = {
        "session_id": "session-1",
        "source_kind": "ado_backlog",
        "provider": "azure_devops",
        "resource_id": "101",
        "project": "Project",
    }
    listed = call_tool(server, "list_inline_media", media_request)
    assert [media["media_id"] for media in listed["media"]] == [
        media["media_id"] for media in item["inline_media"]
    ]
    destinations = []
    for index, media in enumerate(listed["media"]):
        downloaded = call_tool(
            server,
            "download_inline_media",
            {
                **media_request,
                "media_id": media["media_id"],
            },
        )
        copied = call_tool(
            server,
            "communication_copy_artifact_to_workspace",
            {
                "session_id": "session-1",
                "artifact_id": downloaded["artifact"]["artifact_id"],
                "destination_filename": f"work-item-101-{index}.png",
            },
        )
        destination = (
            tmp_path / "workspace" / Path(copied["destination_path"]).relative_to("/home/agent")
        )
        destinations.append(destination)
    for index, destination in enumerate(destinations):
        with Image.open(destination) as image:
            assert image.size == (3 + index, 2 + index)
            assert image.getpixel((0, 0)) == (0, 0, 255)
    assert len(download_requests) == 2
    audit = (tmp_path / "audit.jsonl").read_text()
    assert "host-pat" not in audit
    assert base64.b64encode(images["first"]).decode() not in audit


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
