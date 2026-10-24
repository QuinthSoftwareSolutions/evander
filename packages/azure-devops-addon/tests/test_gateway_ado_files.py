from gateway_tool_support import call_tool
import base64
import json
from io import BytesIO
from datetime import UTC, datetime, timedelta
from pathlib import Path
import pytest
import httpx
from mcp.server.mcpserver.exceptions import ToolError
from PIL import Image
from evander import gateway
from evander.communication_artifacts import CommunicationArtifactStore
from evander.gateway import create_server
from evander.inline_media import InlineMediaDownload
from evander.models import GetPullRequestResponse, SessionManifest, ToolName
from evander_azure_devops_addon.issues import AzureDevOpsIssueAdapter

from test_gateway_communication_files import FakeAgentCopyFilesEffectProvider, make_context


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
