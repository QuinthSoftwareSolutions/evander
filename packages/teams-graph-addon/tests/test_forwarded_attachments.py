from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from gateway_tool_support import call_tool
from http_client_support import patch_httpx_client
from evander.communication_artifacts import CommunicationArtifactStore
from evander.config import GatewayConfig
from evander.gateway import create_server
from evander.models import SessionManifest, TeamsMessageEnvelope, ToolName
from evander.runtime import build_runtime
from mcp.server.mcpserver.exceptions import ToolError


IMAGE_URL = (
    "https://graph.microsoft.com/v1.0/chats/image-source/messages/image-message/"
    "hostedContents/image-1/$value"
)
FILE_URL = "https://example.sharepoint.com/sites/shared/Report.pdf"


def context_with_forward(tmp_path, monkeypatch, body):
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    context = build_runtime(GatewayConfig(runtime_dir=tmp_path, teams_backend="graph_api"))
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[
                ToolName.TEAMS_GET_MESSAGES,
                ToolName.COMMUNICATION_LIST_ATTACHMENTS,
                ToolName.COMMUNICATION_DOWNLOAD_ATTACHMENT,
                ToolName.LIST_INLINE_MEDIA,
                ToolName.DOWNLOAD_INLINE_MEDIA,
            ],
            teams_chat_allowlist=["chat:destination"],
            communication_file_receive_enabled=True,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    context.teams_messages.put(
        "chat:destination/forward-1",
        TeamsMessageEnvelope(
            cursor="chat:destination/forward-1",
            message_id="forward-1",
            author_id="forwarder",
            chat_id="destination",
            content='<attachment id="original-1"></attachment>',
            metadata={
                "attachments": [
                    {
                        "id": "original-1",
                        "contentType": "forwardedMessageReference",
                        "content": json.dumps(
                            {
                                "originalMessageId": "original-1",
                                "originalConversationId": "forward-source",
                                "originalMessageContent": body,
                            }
                        ),
                    }
                ]
            },
        ),
    )
    return context


@pytest.mark.parametrize(
    "download_tool", ["communication_download_attachment", "download_inline_media"]
)
def test_forwarded_image_can_be_listed_downloaded_and_staged_from_its_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    download_tool: str,
) -> None:
    context = context_with_forward(tmp_path, monkeypatch, f'<p><img src="{IMAGE_URL}"></p>')
    requests = []

    def handler(request):
        requests.append(request)
        assert str(request.url) == IMAGE_URL
        assert request.headers["authorization"] == "Bearer graph-token"
        return httpx.Response(
            200,
            content=b"forwarded-image-bytes",
            headers={"content-type": "image/jpeg"},
            request=request,
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    server = create_server(context)
    args = {
        "session_id": "session-1",
        "provider": "teams",
        "message_id": "forward-1",
        "channel_target": "chat:destination",
    }
    listed = call_tool(server, "communication_list_attachments", args)["attachments"]
    assert len(listed) == 1
    assert listed[0]["mime_type"] != "forwardedMessageReference"
    assert requests == []
    if download_tool == "download_inline_media":
        download_args = {
            "session_id": "session-1",
            "provider": "teams",
            "source_kind": "communication",
            "resource_id": "forward-1",
            "media_id": listed[0]["attachment_id"],
            "channel_target": "chat:destination",
        }
    else:
        download_args = {**args, "attachment_id": listed[0]["attachment_id"]}
    artifact = call_tool(server, download_tool, download_args)["artifact"]
    assert artifact["filename"].endswith(".jpg")
    assert artifact["mime_type"] == "image/jpeg"
    assert artifact["source_message_id"] == "forward-1"
    assert (
        CommunicationArtifactStore(tmp_path).get(artifact["artifact_id"]).blob_path.read_bytes()
        == b"forwarded-image-bytes"
    )
    assert len(requests) == 1


def test_forwarded_file_link_uses_existing_sharepoint_download_without_exporting_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = context_with_forward(tmp_path, monkeypatch, f'<a href="{FILE_URL}">Report.pdf</a>')
    requests = []
    share_id = base64.urlsafe_b64encode(FILE_URL.encode()).decode().rstrip("=")
    graph_url = f"https://graph.microsoft.com/v1.0/shares/u!{share_id}/driveItem/content"

    def handler(request):
        requests.append(request)
        if str(request.url) == graph_url:
            assert request.headers["authorization"] == "Bearer graph-token"
            return httpx.Response(
                302, headers={"location": "https://download.example/Report.pdf"}, request=request
            )
        assert str(request.url) == "https://download.example/Report.pdf"
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            content=b"%PDF-forwarded-report",
            headers={"content-type": "application/pdf"},
            request=request,
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    service = context.teams_service_for_session("session-1")
    attachments = service.list_attachments("forward-1", channel_target="chat:destination")
    assert len(attachments) == 1
    downloaded = service.download_attachment(
        "forward-1", attachments[0]["id"], channel_target="chat:destination"
    )
    assert downloaded["filename"] == "Report.pdf"
    assert downloaded["content"] == b"%PDF-forwarded-report"
    assert len(requests) == 2


@pytest.mark.parametrize(
    "url",
    [
        IMAGE_URL.replace("graph.microsoft.com", "attacker.example"),
        IMAGE_URL.replace("https:", "http:"),
        "https://graph.microsoft.com/v1.0/me",
        IMAGE_URL + "?other=resource",
        IMAGE_URL.replace("image-message", ".."),
        "../hostedContents/image-1/$value?other=resource",
        "https://[invalid-host/path",
    ],
)
def test_forwarded_media_rejects_untrusted_or_non_media_urls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    context = context_with_forward(tmp_path, monkeypatch, f'<img src="{url}">')

    def handler(request):
        raise AssertionError(f"No network request is allowed: {request.url}")

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    assert (
        context.teams_service_for_session("session-1").list_attachments(
            "forward-1", channel_target="chat:destination"
        )
        == []
    )


def test_forwarded_media_requires_the_destination_chat_grant_before_network_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = context_with_forward(tmp_path, monkeypatch, f'<img src="{IMAGE_URL}">')
    service = context.teams_service_for_session("session-1")
    with pytest.raises(ValueError, match="not allowlisted"):
        service.list_attachments("forward-1", channel_target="chat:other")


def test_relative_forwarded_image_uses_original_message_context_and_deduplicates_links(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = context_with_forward(
        tmp_path,
        monkeypatch,
        '<img src="../hostedContents/image-1/$value"><img src="../hostedContents/image-1/$value">',
    )
    requests = []

    def handler(request):
        requests.append(request)
        assert (
            str(request.url)
            == "https://graph.microsoft.com/v1.0/chats/forward-source/messages/original-1/hostedContents/image-1/$value"
        )
        return httpx.Response(
            200, content=b"relative-image", headers={"content-type": "image/png"}, request=request
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    service = context.teams_service_for_session("session-1")
    first = service.list_attachments("forward-1", channel_target="chat:destination")
    second = service.list_attachments("forward-1", channel_target="chat:destination")
    assert len(first) == 1
    assert first[0]["id"] == second[0]["id"]
    downloaded = service.download_attachment(
        "forward-1", first[0]["id"], channel_target="chat:destination"
    )
    assert downloaded["content"] == b"relative-image"
    assert downloaded["filename"].endswith(".png")
    assert len(requests) == 1


def test_forwarded_image_download_stops_when_the_stream_exceeds_the_byte_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = context_with_forward(tmp_path, monkeypatch, f'<img src="{IMAGE_URL}">')
    yielded = []

    class Chunks(httpx.SyncByteStream):
        def __iter__(self):
            for chunk in (b"1234", b"5678", b"must-not-read"):
                yielded.append(chunk)
                yield chunk

    def handler(request):
        assert str(request.url) == IMAGE_URL
        return httpx.Response(
            200, stream=Chunks(), headers={"content-type": "image/png"}, request=request
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    service = context.teams_service_for_session("session-1")
    attachment = service.list_attachments("forward-1", channel_target="chat:destination")[0]
    with pytest.raises(ValueError, match="maximum size of 5 bytes"):
        service.download_attachment(
            "forward-1", attachment["id"], channel_target="chat:destination", max_bytes=5
        )
    assert yielded == [b"1234", b"5678"]


@pytest.mark.parametrize("resource", ["image", "file"])
@pytest.mark.parametrize("allowed_response", [True, False])
def test_forwarded_inline_download_enforces_policy_after_unknown_metadata_is_resolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resource: str,
    allowed_response: bool,
) -> None:
    image = resource == "image"
    body = f'<img src="{IMAGE_URL}">' if image else f'<a href="{FILE_URL}">Report.pdf</a>'
    context = context_with_forward(tmp_path, monkeypatch, body)
    session = context.session_repository.get_session("session-1")
    context.session_repository.upsert_session(
        session.model_copy(
            update={
                "communication_file_allowed_extensions": [".jpg" if image else ".pdf"],
                "communication_file_allowed_mime_types": [
                    "image/jpeg" if image else "application/pdf"
                ],
            }
        )
    )
    requests = []

    def handler(request):
        requests.append(request)
        expected_mime = "image/jpeg" if image else "application/pdf"
        return httpx.Response(
            200,
            content=b"permitted-resource",
            headers={"content-type": expected_mime if allowed_response else "image/gif"},
            request=request,
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    server = create_server(context)
    args = {
        "session_id": "session-1",
        "provider": "teams",
        "source_kind": "communication",
        "resource_id": "forward-1",
        "channel_target": "chat:destination",
    }
    listed = call_tool(server, "list_inline_media", args)["media"]
    if allowed_response:
        artifact = call_tool(
            server, "download_inline_media", {**args, "media_id": listed[0]["media_id"]}
        )["artifact"]
        assert artifact["filename"].endswith(".jpg" if image else ".pdf")
        assert artifact["mime_type"] == ("image/jpeg" if image else "application/pdf")
    else:
        with pytest.raises(ToolError, match="not allowlisted"):
            call_tool(server, "download_inline_media", {**args, "media_id": listed[0]["media_id"]})
        assert not list((tmp_path / "communication-artifacts").rglob("*.json"))
    assert len(requests) == 1


@pytest.mark.parametrize("tool", ["communication_download_attachment", "download_inline_media"])
def test_forwarded_tools_stop_oversized_streams_before_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
) -> None:
    context = context_with_forward(tmp_path, monkeypatch, f'<img src="{IMAGE_URL}">')
    session = context.session_repository.get_session("session-1")
    context.session_repository.upsert_session(
        session.model_copy(update={"communication_file_max_bytes": 5})
    )
    yielded = []

    class Chunks(httpx.SyncByteStream):
        def __iter__(self):
            for chunk in (b"1234", b"5678", b"must-not-read"):
                yielded.append(chunk)
                yield chunk

    def handler(request):
        return httpx.Response(
            200, stream=Chunks(), headers={"content-type": "image/png"}, request=request
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    server = create_server(context)
    args = {
        "session_id": "session-1",
        "provider": "teams",
        "message_id": "forward-1",
        "channel_target": "chat:destination",
    }
    listed = call_tool(server, "communication_list_attachments", args)["attachments"]
    if tool == "download_inline_media":
        download_args = {
            "session_id": "session-1",
            "provider": "teams",
            "source_kind": "communication",
            "resource_id": "forward-1",
            "channel_target": "chat:destination",
            "media_id": listed[0]["attachment_id"],
        }
    else:
        download_args = {**args, "attachment_id": listed[0]["attachment_id"]}
    with pytest.raises(ToolError):
        call_tool(server, tool, download_args)
    assert yielded == [b"1234", b"5678"]
    assert not list((tmp_path / "communication-artifacts").rglob("*.json"))
