from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from agent_sandbox.admin.composition import AdminRuntimeComponents, build_admin_runtime
from agent_sandbox.admin.web.app import create_app
from agent_sandbox.admin.web.pr_integration import pr_integration_routes
from agent_sandbox.pr_merge_preflight import (
    MergePreflightCheck,
    MergePreflightParticipant,
    MergePreflightReport,
)


def write_config(tmp_path: Path) -> Path:
    runtime_dir = tmp_path / "runtime"
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text(
        "\n".join(
            [
                f'runtime_dir: "{runtime_dir}"',
                f'audit_log_path: "{runtime_dir / "audit.jsonl"}"',
                f'session_store_path: "{runtime_dir / "sessions.json"}"',
                f'approval_store_path: "{runtime_dir / "approvals.json"}"',
                f'draft_store_path: "{runtime_dir / "drafts.json"}"',
                f'email_store_path: "{runtime_dir / "emails.json"}"',
                f'discord_command_store_path: "{runtime_dir / "discord_commands.json"}"',
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def _json_request(payload: dict[str, object]) -> Request:
    body = json.dumps(payload).encode()
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/admin/pr-integration-preflight",
            "headers": [(b"content-type", b"application/json")],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        },
        receive,
    )


class _DenyWorkflowWriteAuth:
    def __init__(self) -> None:
        self.required_permission: tuple[str, str] | None = None

    def require(self, _request: Request) -> object:
        return object()

    def require_permission(
        self,
        _request: Request,
        permission: str,
        *,
        directory_path: str,
    ) -> None:
        self.required_permission = (permission, directory_path)
        raise PermissionError(f"Permission required: {permission}")


def _report() -> MergePreflightReport:
    participants = [
        MergePreflightParticipant(
            pr_id="17",
            source_branch="feature/one",
            target_branch="main",
            source_commit="1111111",
            target_commit="aaaaaaa",
            current_target_commit="aaaaaaa",
            url="https://dev.azure.com/org/proj/_git/repo/pullrequest/17",
            status="clean",
        ),
        MergePreflightParticipant(
            pr_id="18",
            source_branch="feature/two",
            target_branch="main",
            source_commit="2222222",
            target_commit="aaaaaaa",
            current_target_commit="aaaaaaa",
            url="https://dev.azure.com/org/proj/_git/repo/pullrequest/18",
            status="clean",
        ),
        MergePreflightParticipant(
            pr_id="19",
            source_branch="feature/three",
            target_branch="main",
            source_commit="3333333",
            target_commit="aaaaaaa",
            current_target_commit="aaaaaaa",
            url="https://dev.azure.com/org/proj/_git/repo/pullrequest/19",
            status="clean",
        ),
    ]
    checks = [
        MergePreflightCheck(
            index=1,
            kind="ordered",
            pr_ids=["17"],
            source_commits=["1111111"],
            left_commit="aaaaaaa",
            right_commit="1111111",
            target_commit="aaaaaaa",
            current_target_commit="aaaaaaa",
            tree_oid="tree-1",
            conflicted_paths=[],
            status="clean",
            explanation="merged cleanly",
        ),
        MergePreflightCheck(
            index=2,
            kind="ordered",
            pr_ids=["17", "18"],
            source_commits=["1111111", "2222222"],
            left_commit="synthetic-1",
            right_commit="2222222",
            target_commit="aaaaaaa",
            current_target_commit="aaaaaaa",
            tree_oid="tree-2",
            conflicted_paths=[],
            status="clean",
            explanation="merged cleanly",
        ),
        MergePreflightCheck(
            index=3,
            kind="ordered",
            pr_ids=["17", "18", "19"],
            source_commits=["1111111", "2222222", "3333333"],
            left_commit="synthetic-2",
            right_commit="3333333",
            target_commit="aaaaaaa",
            current_target_commit="aaaaaaa",
            tree_oid="tree-3",
            conflicted_paths=[],
            status="clean",
            explanation="merged cleanly",
        ),
    ]
    return MergePreflightReport(
        repo_path="/tmp/repo",
        mode="ordered",
        status="clean",
        target_branch="main",
        target_commit="aaaaaaa",
        current_target_commit="aaaaaaa",
        participants=participants,
        checks=checks,
        warnings=[],
        errors=[],
        summary_text="ordered preflight clean for PRs 17, 18, 19.",
        generated_at=None,
    )


def test_admin_web_pr_integration_preflight_parses_request_and_returns_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service = build_admin_runtime(write_config(tmp_path))
    report = _report()
    captured: dict[str, object] = {}

    def run_pr_integration_preflight(**kwargs: object) -> MergePreflightReport:
        captured.update(kwargs)
        return report

    monkeypatch.setattr(service, "run_pr_integration_preflight", run_pr_integration_preflight)

    client = TestClient(create_app(service))
    response = client.post(
        "/api/admin/pr-integration-preflight",
        json={
            "provider": "ado",
            "repo": "repo-1",
            "pr_ids": "17, 18\n19",
            "mode": "ordered",
            "directory_path": "root/team-a",
            "repo_source": "/work/repo",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "azure_devops"
    assert payload["repo"] == "repo-1"
    assert payload["directory_path"] == "root/team-a"
    assert payload["repo_path"] == "/tmp/repo"
    assert payload["mode"] == "ordered"
    assert payload["status"] == "clean"
    assert payload["summary_text"] == "ordered preflight clean for PRs 17, 18, 19."
    assert payload["generated_at"] is None
    assert [item["pr_id"] for item in payload["participants"]] == ["17", "18", "19"]
    assert [item["status"] for item in payload["checks"]] == ["clean", "clean", "clean"]
    assert captured == {
        "provider": "azure_devops",
        "repo": "repo-1",
        "pr_ids": ["17", "18", "19"],
        "mode": "ordered",
        "directory_path": "root/team-a",
        "organization_url": None,
        "project": None,
        "repo_source": "/work/repo",
    }


@pytest.mark.parametrize(
    ("body", "expected_error"),
    [
        (
            {"provider": "github", "repo": "repo-1", "pr_ids": ["17"], "mode": "single"},
            "provider 'github' is not supported",
        ),
        (
            {"repo": "repo-1", "pr_ids": ["17"], "mode": "bogus"},
            "mode must be single, pairwise, or ordered",
        ),
        (
            {"repo": "repo-1", "pr_ids": [], "mode": "single"},
            "pr_ids must include at least one PR id",
        ),
        (
            {"repo": "repo-1", "pr_ids": ["17", "18"], "mode": "single"},
            "single mode requires exactly one PR id",
        ),
    ],
)
def test_admin_web_pr_integration_preflight_rejects_invalid_requests(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    body: dict[str, object],
    expected_error: str,
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    def run_pr_integration_preflight(**kwargs: object) -> MergePreflightReport:
        raise AssertionError(f"service should not be invoked: {kwargs!r}")

    monkeypatch.setattr(service, "run_pr_integration_preflight", run_pr_integration_preflight)

    client = TestClient(create_app(service))
    response = client.post("/api/admin/pr-integration-preflight", json=body)

    assert response.status_code == 400
    assert expected_error in response.json()["error"]


def test_admin_web_pr_integration_preflight_rejects_bad_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    def run_pr_integration_preflight(**kwargs: object) -> MergePreflightReport:
        raise AssertionError(f"service should not be invoked: {kwargs!r}")

    monkeypatch.setattr(service, "run_pr_integration_preflight", run_pr_integration_preflight)

    client = TestClient(create_app(service))
    response = client.post(
        "/api/admin/pr-integration-preflight",
        content="{",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "Request body must be valid JSON"


def test_admin_web_pr_integration_preflight_requires_workflow_write_permission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service = build_admin_runtime(write_config(tmp_path))

    def run_pr_integration_preflight(**kwargs: object) -> MergePreflightReport:
        raise AssertionError(f"service should not be invoked: {kwargs!r}")

    monkeypatch.setattr(service, "run_pr_integration_preflight", run_pr_integration_preflight)
    auth = _DenyWorkflowWriteAuth()
    route = pr_integration_routes(service, auth)[0]

    response = asyncio.run(
        route.endpoint(
            _json_request(
                {
                    "provider": "ado",
                    "repo": "repo-1",
                    "pr_ids": ["17"],
                    "mode": "single",
                    "directory_path": "root/team-a",
                    "repo_source": "/work/repo",
                }
            )
        )
    )

    assert response.status_code == 403
    assert json.loads(response.body)["error"] == "Permission required: workflows.write"
    assert auth.required_permission == ("workflows.write", "root/team-a")
