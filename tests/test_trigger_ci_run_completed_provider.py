from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from evander.config import GatewayConfig
from evander.events.provider_models import CiRunCompletedTriggerDefinition
from evander.triggers.providers import ci_run_completed as ci_provider_module
from evander.triggers.providers.base import TriggerRuntimeContext
from evander.triggers.providers.ci_run_completed import CiRunCompletedTriggerProvider


class _DirectoryConfigResolver:
    def __init__(self, resolved_config: GatewayConfig) -> None:
        self._resolved_config = resolved_config
        self.calls: list[str] = []

    def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
        self.calls.append(directory_path or "root")
        return self._resolved_config


def _context(
    runtime_dir: Path,
    *,
    env: dict[str, str] | None = None,
    directory_path: str = "root/team-a",
    resolver: _DirectoryConfigResolver | None = None,
    resolved_config: GatewayConfig | None = None,
) -> TriggerRuntimeContext:
    config = resolved_config or GatewayConfig(runtime_dir=runtime_dir)
    return TriggerRuntimeContext(
        resolved_config=config,
        env=env or {},
        provider_config_resolver=resolver,
        metadata={"directory_path": directory_path},
    )


def _trigger(
    *,
    provider: str,
    repo: str | None = None,
    workflow: str | None = None,
    organization_url: str | None = None,
    project: str | None = None,
    pipeline_id: int | None = None,
    pipeline_name: str | None = None,
    branches: list[str] | None = None,
    conclusions: list[str] | None = None,
    top: int = 50,
    include_existing: bool = False,
) -> CiRunCompletedTriggerDefinition:
    payload: dict[str, Any] = {
        "id": "ci-trigger",
        "display_name": "CI run completed",
        "type": "trigger:ci:run:completed",
        "provider": provider,
        "branches": branches or [],
        "top": top,
        "include_existing": include_existing,
    }
    if repo is not None:
        payload["repo"] = repo
    if workflow is not None:
        payload["workflow"] = workflow
    if organization_url is not None:
        payload["organization_url"] = organization_url
    if project is not None:
        payload["project"] = project
    if pipeline_id is not None:
        payload["pipeline_id"] = pipeline_id
    if pipeline_name is not None:
        payload["pipeline_name"] = pipeline_name
    if conclusions is not None:
        payload["conclusions"] = conclusions
    return CiRunCompletedTriggerDefinition.model_validate(payload)


def _patch_httpx_client(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(ci_provider_module.httpx, "Client", factory)
    return transport


def _assert_payload_keys(payload: dict[str, Any]) -> None:
    assert set(payload) == {
        "provider",
        "repo",
        "run_id",
        "run_number",
        "attempt",
        "workflow_id",
        "workflow_name",
        "pipeline_id",
        "pipeline_name",
        "branch",
        "commit_sha",
        "status",
        "conclusion",
        "run_url",
        "started_at",
        "completed_at",
        "duration_seconds",
        "actor",
        "pull_requests",
        "metadata",
    }


def test_github_ci_runs_use_workflow_endpoint_and_normalize_payloads(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    requests: list[httpx.Request] = []
    token = "github-secret-token"

    runs_by_branch = {
        "main": [
            {
                "id": 101,
                "run_number": 11,
                "run_attempt": 1,
                "workflow_id": 555,
                "name": "Nightly Build",
                "path": ".github/workflows/build.yml",
                "head_branch": "main",
                "head_sha": "sha-success",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/owner/repo/actions/runs/101",
                "run_started_at": "2026-04-03T11:50:00Z",
                "updated_at": "2026-04-03T12:00:00Z",
                "actor": {"login": "octo-success"},
                "pull_requests": [{"number": 17, "url": "https://github.com/owner/repo/pull/17"}],
            },
            {
                "id": 102,
                "run_number": 12,
                "run_attempt": 2,
                "workflow_id": 555,
                "name": "Nightly Build",
                "path": ".github/workflows/build.yml",
                "head_branch": "main",
                "head_sha": "sha-cancel",
                "status": "completed",
                "conclusion": "cancelled",
                "html_url": "https://github.com/owner/repo/actions/runs/102",
                "run_started_at": "2026-04-03T11:55:00Z",
                "updated_at": "2026-04-03T12:02:00Z",
                "actor": {"login": "octo-cancel"},
                "pull_requests": [],
            },
        ],
        "release/v1": [
            {
                "id": 103,
                "run_number": 13,
                "run_attempt": 1,
                "workflow_id": 555,
                "name": "Nightly Build",
                "path": ".github/workflows/build.yml",
                "head_branch": "release/v1",
                "head_sha": "sha-failure",
                "status": "completed",
                "conclusion": "failure",
                "html_url": "https://github.com/owner/repo/actions/runs/103",
                "run_started_at": "2026-04-03T11:52:00Z",
                "updated_at": "2026-04-03T12:01:00Z",
                "actor": {"login": "octo-failure"},
                "pull_requests": [],
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/repos/owner/repo/actions/workflows/build.yml/runs"
        assert request.headers["authorization"] == f"Bearer {token}"
        assert request.url.params["status"] == "completed"
        assert request.url.params["per_page"] == "10"
        branch = request.url.params["branch"]
        assert branch in runs_by_branch
        return httpx.Response(200, json={"workflow_runs": runs_by_branch[branch]}, request=request)

    _patch_httpx_client(monkeypatch, handler)
    trigger = _trigger(
        provider="github_actions",
        repo="owner/repo",
        workflow="build.yml",
        branches=["main", "release/v1"],
        top=10,
        include_existing=True,
    )
    provider = CiRunCompletedTriggerProvider()
    provider.initialize(trigger, _context(runtime_dir, env={"GITHUB_TOKEN": token}))

    events = provider.poll()
    assert [event.event_id for event in events] == [
        "ci-trigger:github_actions:owner/repo:101:1",
        "ci-trigger:github_actions:owner/repo:103:1",
        "ci-trigger:github_actions:owner/repo:102:2",
    ]
    assert [event.occurred_at.isoformat() for event in events] == [
        "2026-04-03T12:00:00+00:00",
        "2026-04-03T12:01:00+00:00",
        "2026-04-03T12:02:00+00:00",
    ]
    assert [event.payload["conclusion"] for event in events] == [
        "success",
        "failure",
        "cancelled",
    ]
    assert [event.payload["branch"] for event in events] == [
        "main",
        "release/v1",
        "main",
    ]
    assert [event.payload["workflow_name"] for event in events] == ["Nightly Build"] * 3
    assert [event.payload["workflow_id"] for event in events] == ["555"] * 3
    assert [event.payload["actor"] for event in events] == [
        "octo-success",
        "octo-failure",
        "octo-cancel",
    ]
    assert events[0].payload["pull_requests"] == [
        {"number": 17, "url": "https://github.com/owner/repo/pull/17"}
    ]
    _assert_payload_keys(events[0].payload)
    assert token not in json.dumps([event.payload for event in events], sort_keys=True)

    cursor_path = runtime_dir / "trigger-cursors-ci-runs.json"
    assert json.loads(cursor_path.read_text(encoding="utf-8")) == {}

    for event in events:
        provider.acknowledge_published_event(event)

    assert provider.poll() == []
    assert len(requests) == 4


def test_github_ci_runs_skip_unknown_conclusions_when_filtering(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    runs = [
        {
            "id": 151,
            "run_number": 31,
            "run_attempt": 1,
            "workflow_id": 777,
            "name": "CI",
            "head_branch": "main",
            "head_sha": "sha-failure",
            "status": "completed",
            "conclusion": "failure",
            "updated_at": "2026-04-03T12:00:00Z",
            "actor": {"login": "octo-failure"},
            "pull_requests": [],
        },
        {
            "id": 152,
            "run_number": 32,
            "run_attempt": 1,
            "workflow_id": 777,
            "name": "CI",
            "head_branch": "main",
            "head_sha": "sha-neutral",
            "status": "completed",
            "conclusion": "neutral",
            "updated_at": "2026-04-03T12:01:00Z",
            "actor": {"login": "octo-neutral"},
            "pull_requests": [],
        },
        {
            "id": 153,
            "run_number": 33,
            "run_attempt": 1,
            "workflow_id": 777,
            "name": "CI",
            "head_branch": "main",
            "head_sha": "sha-action-required",
            "status": "completed",
            "conclusion": "action_required",
            "updated_at": "2026-04-03T12:02:00Z",
            "actor": {"login": "octo-action-required"},
            "pull_requests": [],
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/repos/owner/repo/actions/runs"
        return httpx.Response(200, json={"workflow_runs": runs}, request=request)

    _patch_httpx_client(monkeypatch, handler)
    trigger = _trigger(
        provider="github_actions",
        repo="owner/repo",
        conclusions=["failure"],
        include_existing=True,
    )
    provider = CiRunCompletedTriggerProvider()
    provider.initialize(trigger, _context(runtime_dir))

    events = provider.poll()
    assert [event.event_id for event in events] == ["ci-trigger:github_actions:owner/repo:151:1"]
    assert events[0].payload["conclusion"] == "failure"


def test_github_ci_runs_suppress_initial_history_and_retry_until_acknowledged(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    token = "github-secret-token"
    requests: list[httpx.Request] = []
    historical_runs = [
        {
            "id": 201,
            "run_number": 21,
            "run_attempt": 1,
            "workflow_id": 777,
            "name": "CI",
            "path": ".github/workflows/ci.yml",
            "head_branch": "main",
            "head_sha": "sha-old-1",
            "status": "completed",
            "conclusion": "success",
            "html_url": "https://github.com/owner/repo/actions/runs/201",
            "run_started_at": "2026-04-03T11:50:00Z",
            "updated_at": "2026-04-03T12:00:00Z",
            "actor": {"login": "octo-old"},
            "pull_requests": [],
        },
        {
            "id": 202,
            "run_number": 22,
            "run_attempt": 1,
            "workflow_id": 777,
            "name": "CI",
            "path": ".github/workflows/ci.yml",
            "head_branch": "main",
            "head_sha": "sha-old-2",
            "status": "completed",
            "conclusion": "failure",
            "html_url": "https://github.com/owner/repo/actions/runs/202",
            "run_started_at": "2026-04-03T11:55:00Z",
            "updated_at": "2026-04-03T12:01:00Z",
            "actor": {"login": "octo-old"},
            "pull_requests": [],
        },
    ]
    current_runs = historical_runs + [
        {
            "id": 202,
            "run_number": 22,
            "run_attempt": 2,
            "workflow_id": 777,
            "name": "CI",
            "path": ".github/workflows/ci.yml",
            "head_branch": "main",
            "head_sha": "sha-current",
            "status": "completed",
            "conclusion": "failure",
            "html_url": "https://github.com/owner/repo/actions/runs/202",
            "run_started_at": "2026-04-03T11:57:00Z",
            "updated_at": "2026-04-03T12:01:00Z",
            "actor": {"login": "octo-current"},
            "pull_requests": [],
        }
    ]
    requested_runs = historical_runs

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/repos/owner/repo/actions/runs"
        assert request.headers["authorization"] == f"Bearer {token}"
        assert request.url.params["status"] == "completed"
        assert request.url.params["per_page"] == "50"
        assert request.url.params["branch"] == "main"
        return httpx.Response(200, json={"workflow_runs": requested_runs}, request=request)

    _patch_httpx_client(monkeypatch, handler)
    trigger = _trigger(
        provider="github_actions",
        repo="owner/repo",
        branches=["main"],
        include_existing=False,
    )
    provider = CiRunCompletedTriggerProvider()
    provider.initialize(trigger, _context(runtime_dir, env={"GITHUB_TOKEN": token}))

    assert provider.poll() == []
    cursor_path = runtime_dir / "trigger-cursors-ci-runs.json"
    cursor_payload = json.loads(cursor_path.read_text(encoding="utf-8"))
    stream_state = next(iter(cursor_payload["ci-trigger"]["streams"].values()))
    assert stream_state["initialized"] is True
    assert stream_state["last_completed_at"] == "2026-04-03T12:01:00Z"
    assert stream_state["recent_run_keys"] == [
        "github_actions:owner/repo:201:1",
        "github_actions:owner/repo:202:1",
    ]

    requested_runs = current_runs
    second = provider.poll()
    assert [event.event_id for event in second] == ["ci-trigger:github_actions:owner/repo:202:2"]
    assert second[0].payload["attempt"] == 2
    assert second[0].payload["completed_at"] == "2026-04-03T12:01:00Z"

    assert provider.poll() == second
    provider.acknowledge_published_event(second[0])
    assert provider.poll() == []
    assert token not in json.dumps(second[0].payload, sort_keys=True)


def test_github_ci_runs_initialize_empty_first_poll_before_emitting_new_runs(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    requested_runs: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/repos/owner/repo/actions/runs"
        assert request.url.params["status"] == "completed"
        return httpx.Response(200, json={"workflow_runs": requested_runs}, request=request)

    _patch_httpx_client(monkeypatch, handler)
    trigger = _trigger(
        provider="github_actions",
        repo="owner/repo",
        include_existing=False,
    )
    provider = CiRunCompletedTriggerProvider()
    provider.initialize(trigger, _context(runtime_dir))

    assert provider.poll() == []
    cursor_path = runtime_dir / "trigger-cursors-ci-runs.json"
    cursor_payload = json.loads(cursor_path.read_text(encoding="utf-8"))
    stream_state = next(iter(cursor_payload["ci-trigger"]["streams"].values()))
    assert stream_state == {
        "initialized": True,
        "last_completed_at": None,
        "recent_run_keys": [],
    }

    requested_runs = [
        {
            "id": 251,
            "run_number": 41,
            "run_attempt": 1,
            "workflow_id": 777,
            "name": "CI",
            "head_branch": "main",
            "head_sha": "sha-new",
            "status": "completed",
            "conclusion": "success",
            "html_url": "https://github.com/owner/repo/actions/runs/251",
            "run_started_at": "2026-04-03T12:05:00Z",
            "updated_at": "2026-04-03T12:10:00Z",
            "actor": {"login": "octo-new"},
            "pull_requests": [],
        }
    ]

    events = provider.poll()
    assert [event.event_id for event in events] == ["ci-trigger:github_actions:owner/repo:251:1"]
    assert events[0].payload["completed_at"] == "2026-04-03T12:10:00Z"


def test_ado_ci_runs_use_directory_config_fallback_and_normalize_results(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    requests: list[httpx.Request] = []
    resolved_config = GatewayConfig(
        runtime_dir=runtime_dir,
        azure_devops_organization_url="https://dev.azure.com/example",
        azure_devops_project="Sandbox",
        azure_devops_pr_pat_env="ADO_PAT",
    )
    resolver = _DirectoryConfigResolver(resolved_config)

    builds = [
        {
            "id": 301,
            "buildNumber": "2026.04.03.1",
            "status": "completed",
            "result": "succeeded",
            "queueTime": "2026-04-03T11:45:00Z",
            "startTime": "2026-04-03T11:50:00Z",
            "finishTime": "2026-04-03T12:00:00Z",
            "sourceBranch": "refs/heads/release/v1",
            "sourceVersion": "commit-success",
            "definition": {"id": 9001, "name": "nightly"},
            "requestedFor": {"displayName": "Ada"},
            "repository": {
                "id": "repo-id",
                "name": "repo",
                "url": "https://dev.azure.com/example/Sandbox/_git/repo",
            },
            "webUrl": "https://dev.azure.com/example/Sandbox/_build/results?buildId=301",
            "triggerInfo": {
                "pr.number": "17",
                "pr.url": "https://dev.azure.com/example/Sandbox/_git/repo/pullrequest/17",
            },
        },
        {
            "id": 302,
            "buildNumber": "2026.04.03.2",
            "status": "completed",
            "result": "failed",
            "queueTime": "2026-04-03T11:46:00Z",
            "startTime": "2026-04-03T11:51:00Z",
            "finishTime": "2026-04-03T12:01:00Z",
            "sourceBranch": "refs/heads/release/v1",
            "sourceVersion": "commit-failure",
            "definition": {"id": 9001, "name": "nightly"},
            "requestedFor": {"displayName": "Bob"},
            "repository": {
                "id": "repo-id",
                "name": "repo",
                "url": "https://dev.azure.com/example/Sandbox/_git/repo",
            },
            "webUrl": "https://dev.azure.com/example/Sandbox/_build/results?buildId=302",
        },
        {
            "id": 303,
            "buildNumber": "2026.04.03.3",
            "status": "completed",
            "result": "canceled",
            "queueTime": "2026-04-03T11:47:00Z",
            "startTime": "2026-04-03T11:52:00Z",
            "finishTime": "2026-04-03T12:02:00Z",
            "sourceBranch": "refs/heads/release/v1",
            "sourceVersion": "commit-cancelled",
            "definition": {"id": 9001, "name": "nightly"},
            "requestedBy": {"displayName": "Carol"},
            "repository": {
                "id": "repo-id",
                "name": "repo",
                "url": "https://dev.azure.com/example/Sandbox/_git/repo",
            },
            "webUrl": "https://dev.azure.com/example/Sandbox/_build/results?buildId=303",
        },
        {
            "id": 304,
            "buildNumber": "2026.04.03.4",
            "status": "completed",
            "result": "timedOut",
            "queueTime": "2026-04-03T11:48:00Z",
            "startTime": "2026-04-03T11:53:00Z",
            "finishTime": "2026-04-03T12:03:00Z",
            "sourceBranch": "refs/heads/release/v1",
            "sourceVersion": "commit-timeout",
            "definition": {"id": 9001, "name": "nightly"},
            "requestedFor": {"displayName": "Dana"},
            "repository": {
                "id": "repo-id",
                "name": "repo",
                "url": "https://dev.azure.com/example/Sandbox/_git/repo",
            },
            "webUrl": "https://dev.azure.com/example/Sandbox/_build/results?buildId=304",
        },
        {
            "id": 305,
            "buildNumber": "2026.04.03.5",
            "status": "completed",
            "result": "succeeded",
            "queueTime": "2026-04-03T11:49:00Z",
            "startTime": "2026-04-03T11:54:00Z",
            "finishTime": "2026-04-03T12:04:00Z",
            "sourceBranch": "refs/heads/release/v1",
            "sourceVersion": "commit-other",
            "definition": {"id": 9002, "name": "other"},
            "requestedFor": {"displayName": "Eve"},
            "repository": {
                "id": "repo-id",
                "name": "repo",
                "url": "https://dev.azure.com/example/Sandbox/_git/repo",
            },
            "webUrl": "https://dev.azure.com/example/Sandbox/_build/results?buildId=305",
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/example/Sandbox/_apis/build/builds"
        assert request.url.params["statusFilter"] == "completed"
        assert request.url.params["$top"] == "50"
        assert request.url.params["queryOrder"] == "finishTimeDescending"
        assert request.url.params["branchName"] == "refs/heads/release/v1"
        assert request.url.params["api-version"] == "7.1"
        assert "definitions" not in request.url.params
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(200, json={"value": builds}, request=request)

    _patch_httpx_client(monkeypatch, handler)
    trigger = _trigger(
        provider="ado_pipelines",
        repo="repo",
        project=None,
        pipeline_name="nightly",
        branches=["release/v1"],
        include_existing=True,
    )
    provider = CiRunCompletedTriggerProvider()
    provider.initialize(
        trigger, _context(runtime_dir, env={"ADO_PAT": "ado-secret"}, resolver=resolver)
    )

    events = provider.poll()
    assert resolver.calls == ["root/team-a", "root/team-a"]
    assert [event.event_id for event in events] == [
        "ci-trigger:ado_pipelines:repo:301:1",
        "ci-trigger:ado_pipelines:repo:302:1",
        "ci-trigger:ado_pipelines:repo:303:1",
        "ci-trigger:ado_pipelines:repo:304:1",
    ]
    assert [event.payload["conclusion"] for event in events] == [
        "success",
        "failure",
        "cancelled",
        "timed_out",
    ]
    assert [event.payload["pipeline_name"] for event in events] == ["nightly"] * 4
    assert [event.payload["workflow_id"] for event in events] == [None] * 4
    assert [event.payload["workflow_name"] for event in events] == [None] * 4
    assert [event.payload["branch"] for event in events] == ["release/v1"] * 4
    assert [event.payload["actor"] for event in events] == ["Ada", "Bob", "Carol", "Dana"]
    assert events[0].payload["pull_requests"] == [
        {
            "number": "17",
            "url": "https://dev.azure.com/example/Sandbox/_git/repo/pullrequest/17",
        }
    ]
    _assert_payload_keys(events[0].payload)
    assert "ado-secret" not in json.dumps([event.payload for event in events], sort_keys=True)

    for event in events:
        provider.acknowledge_published_event(event)

    assert provider.poll() == []
    assert len(requests) == 2
