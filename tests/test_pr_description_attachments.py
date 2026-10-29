from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from gateway_tool_support import call_tool
from http_client_support import patch_httpx_client

from evander.communication_artifacts import CommunicationArtifactStore
from evander.config import GatewayConfig
from evander.gateway import create_server
from evander.models import ApprovalStatus, SessionManifest, ToolName
from evander.runtime import build_runtime


@pytest.fixture
def attachment_pr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Exercise the MCP tool through the real service and Azure DevOps HTTP adapter."""
    requests: list[httpx.Request] = []
    state = {
        "description": "Author's description.\n\nKeep this paragraph.",
        "status": "active",
        "upload_status": 201,
        "patch_status": 200,
        "omit_upload_url": False,
    }
    pr_path = "/example/Selected/_apis/git/repositories/repo-1/pullRequests/17"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.params["api-version"] == "7.1"
        if request.method == "GET" and request.url.path == pr_path:
            is_refresh = sum(
                r.method == "GET" and r.url.path == pr_path for r in requests
            ) > 1
            return httpx.Response(
                state.get("refresh_status", 200) if is_refresh else 200,
                json={
                    "pullRequestId": 17,
                    "title": "Author's title",
                    "description": state["description"],
                    "status": state["status"],
                },
            )
        if request.method == "GET" and request.url.path == f"{pr_path}/labels":
            return httpx.Response(200, json={"value": []})
        if request.method == "POST" and request.url.path.startswith(f"{pr_path}/attachments/"):
            if "description_during_upload" in state:
                state["description"] = state["description_during_upload"]
            return httpx.Response(
                state["upload_status"],
                json={
                    "id": 42,
                    **(
                        {}
                        if state["omit_upload_url"]
                        else {"url": str(request.url.copy_with(query=None))}
                    ),
                },
            )
        if request.method == "PATCH" and request.url.path == pr_path.replace(
            "pullRequests", "pullrequests"
        ):
            payload = json.loads(request.content)
            if state["patch_status"] == 200:
                state.update(payload)
            return httpx.Response(
                state["patch_status"],
                json={"pullRequestId": 17, "title": "Author's title", **payload},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    monkeypatch.setenv("TEST_PR_ATTACHMENT_PAT", "test-pat")
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            azure_devops_organization_url="https://dev.azure.com/example",
            azure_devops_project="Default",
            azure_devops_pr_pat_env="TEST_PR_ATTACHMENT_PAT",
        )
    )
    session = SessionManifest(
        session_id="review-17",
        allowed_tools=[ToolName.UPDATE_PULL_REQUEST],
        communication_file_send_enabled=True,
        communication_file_max_bytes=1024,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    context.session_repository.upsert_session(session)
    store = CommunicationArtifactStore(tmp_path)
    csv = store.stage(
        provider="workspace",
        session_id=session.session_id,
        content=b"name,value\nalpha,1\n",
        original_filename="review data.csv",
        mime_type="text/csv",
    )
    server = create_server(context)
    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    return SimpleNamespace(
        context=context,
        session=session,
        store=store,
        csv=csv,
        server=server,
        requests=requests,
        state=state,
        arguments={
            "session_id": session.session_id,
            "provider": "ado",
            "repo": "repo-1",
            "pr_id": "17",
            "project": "Selected",
            "expected_statuses": ["active"],
            "artifact_ids": [csv.artifact_id],
        },
    )


@pytest.mark.parametrize("description", [None, "Replacement description.", ""])
def test_description_attachments_upload_csv_and_image_without_a_comment(
    attachment_pr, description: str | None
) -> None:
    pr = attachment_pr
    image = pr.store.stage(
        provider="workspace",
        session_id=pr.session.session_id,
        content=b"image-bytes",
        original_filename="chart.png",
        mime_type="image/png",
    )
    arguments = {**pr.arguments, "artifact_ids": [pr.csv.artifact_id, image.artifact_id]}
    original_description = pr.state["description"]
    if description is not None:
        arguments["description"] = description

    result = call_tool(pr.server, "update_pull_request", arguments)

    csv_url = (
        "https://dev.azure.com/example/Selected/_apis/git/repositories/repo-1/"
        "pullRequests/17/attachments/review-data.csv"
    )
    image_url = (
        "https://dev.azure.com/example/Selected/_apis/git/repositories/repo-1/"
        "pullRequests/17/attachments/chart.png"
    )
    expected_description = (
        f"{original_description if description is None else description}\n\n"
        f"[review-data.csv]({csv_url})\n![chart.png]({image_url})"
    )
    assert pr.state["description"] == expected_description
    assert result["description"] == expected_description.strip()
    assert result["updated"] is True
    expected_requests = [
        ("GET", "17"),
        ("GET", "labels"),
        ("POST", "review-data.csv"),
        ("POST", "chart.png"),
    ]
    if description is None:
        expected_requests.append(("GET", "17"))
    expected_requests.append(("PATCH", "17"))
    assert [(r.method, r.url.path.rsplit("/", 1)[-1]) for r in pr.requests] == expected_requests
    assert pr.requests[2].content == b"name,value\nalpha,1\n"
    assert pr.requests[2].headers["content-type"] == "application/octet-stream"
    assert pr.requests[3].content == b"image-bytes"
    assert json.loads(pr.requests[-1].content) == {"description": expected_description}
    assert [a["url"] for a in result["uploaded_attachments"]] == [csv_url, image_url]
    assert [a["artifact_id"] for a in result["uploaded_attachments"]] == [
        pr.csv.artifact_id,
        image.artifact_id,
    ]


def test_description_attachments_preserve_edits_during_upload(attachment_pr) -> None:
    pr = attachment_pr
    pr.state["description_during_upload"] = "Author's revised description.\n\nNew review caveat."

    result = call_tool(pr.server, "update_pull_request", pr.arguments)

    expected_description = (
        "Author's revised description.\n\nNew review caveat.\n\n"
        "[review-data.csv](https://dev.azure.com/example/Selected/_apis/git/"
        "repositories/repo-1/pullRequests/17/attachments/review-data.csv)"
    )
    assert pr.state["description"] == expected_description
    assert result["description"] == expected_description
    assert result["updated"] is True


def test_description_attachment_refresh_failure_does_not_save_stale_text(attachment_pr) -> None:
    pr = attachment_pr
    pr.state["description_during_upload"] = "Author's revised description."
    pr.state["refresh_status"] = 503

    with pytest.raises(ToolError, match="Error executing tool update_pull_request"):
        call_tool(pr.server, "update_pull_request", pr.arguments)

    assert pr.state["description"] == "Author's revised description."
    assert [r.method for r in pr.requests] == ["GET", "GET", "POST", "GET"]


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"communication_file_send_enabled": False}, "file send is disabled"),
        ({"communication_file_max_bytes": 1}, "exceeds max bytes"),
        ({"communication_file_allowed_extensions": [".pdf"]}, "not allowlisted"),
        ({"communication_file_blocked_extensions": [".csv"]}, "is blocked"),
        ({"communication_file_allowed_mime_types": ["image/png"]}, "MIME type.*not allowlisted"),
    ],
)
def test_description_attachment_file_policy_denies_before_http(
    attachment_pr, changes: dict, error: str
) -> None:
    pr = attachment_pr
    pr.context.session_repository.upsert_session(pr.session.model_copy(update=changes))

    with pytest.raises(ToolError, match=error):
        call_tool(pr.server, "update_pull_request", pr.arguments)

    assert pr.requests == []


def test_description_attachments_require_the_callers_own_artifact(attachment_pr) -> None:
    pr = attachment_pr
    other = pr.store.stage(
        provider="workspace",
        session_id="another-reviewer",
        content=b"private report",
        original_filename="private.csv",
        mime_type="text/csv",
    )

    with pytest.raises(ToolError, match="artifact is not owned by session"):
        call_tool(
            pr.server, "update_pull_request", {**pr.arguments, "artifact_ids": [other.artifact_id]}
        )

    assert pr.requests == []


def test_description_attachments_keep_the_bound_pr_argument_policy(attachment_pr) -> None:
    pr = attachment_pr
    session = pr.session.model_dump(mode="json")
    session["tool_argument_policies"] = {
        "update_pull_request": [
            {
                "id": "assigned-pr",
                "constraints": [{"id": "pr", "path": "pr_id", "operator": "fixed", "value": "17"}],
            }
        ]
    }
    pr.context.session_repository.upsert_session(SessionManifest.model_validate(session))

    with pytest.raises(ToolError, match="tool_argument_policy_denied"):
        call_tool(pr.server, "update_pull_request", {**pr.arguments, "pr_id": "18"})

    assert pr.requests == []


def test_description_attachment_status_guard_skips_upload(attachment_pr) -> None:
    pr = attachment_pr
    pr.state["status"] = "completed"

    result = call_tool(pr.server, "update_pull_request", pr.arguments)

    assert result["skipped"] is True
    assert result["skipped_reason"] == "unexpected_status"
    assert result["updated"] is False
    assert result["uploaded_attachments"] == []
    assert [r.method for r in pr.requests] == ["GET", "GET"]


@pytest.mark.parametrize("failure", ["upload_status", "patch_status"])
def test_description_attachment_http_failures_do_not_report_an_update(
    attachment_pr, failure: str
) -> None:
    pr = attachment_pr
    original_description = pr.state["description"]
    pr.state[failure] = 503

    with pytest.raises(ToolError, match="Error executing tool update_pull_request"):
        call_tool(pr.server, "update_pull_request", pr.arguments)

    assert pr.state["description"] == original_description
    assert [r.method for r in pr.requests] == (
        ["GET", "GET", "POST"]
        if failure == "upload_status"
        else ["GET", "GET", "POST", "GET", "PATCH"]
    )


def test_description_attachment_file_send_approval_is_exact_payload_bound(attachment_pr) -> None:
    pr = attachment_pr
    pr.context.session_repository.upsert_session(
        pr.session.model_copy(update={"communication_file_require_approval_for_send": True})
    )
    with pytest.raises(ToolError, match="file send requires approval"):
        call_tool(pr.server, "update_pull_request", pr.arguments)
    assert pr.requests == []

    approval = pr.context.policy.create_approval(
        pr.session.session_id,
        "update_pull_request",
        {
            "session_id": pr.session.session_id,
            "provider": "azure_devops",
            "repo": "repo-1",
            "pr_id": "17",
            "project": "Selected",
            "add_tags": [],
            "remove_tags": [],
            "expected_statuses": ["active"],
            "artifact_ids": [pr.csv.artifact_id],
        },
        "Attach CSV to the assigned PR description",
    )
    approval.status = ApprovalStatus.APPROVED
    pr.context.approvals.put(approval.request_id, approval)
    approved_arguments = {**pr.arguments, "request_id": approval.request_id}
    with pytest.raises(ToolError, match="payload"):
        call_tool(
            pr.server, "update_pull_request", {**approved_arguments, "description": "Changed text"}
        )
    assert pr.requests == []

    result = call_tool(pr.server, "update_pull_request", approved_arguments)

    assert result["updated"] is True
    assert result["uploaded_attachments"][0]["artifact_id"] == pr.csv.artifact_id


def test_description_attachment_requires_a_provider_download_url(attachment_pr) -> None:
    pr = attachment_pr
    pr.state["omit_upload_url"] = True

    with pytest.raises(ToolError, match="Error executing tool update_pull_request"):
        call_tool(pr.server, "update_pull_request", pr.arguments)

    assert [r.method for r in pr.requests] == ["GET", "GET", "POST"]
