from __future__ import annotations

from config_helpers import canonical_worker_config

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from evander.daemons.ado_issue_worker import (
    AdoWorkerRuntimeConfig,
    WorkerConfig,
    _ado_organization_url_for_trigger,
    _load_ado_pr_feedback_session_bindings,
    _publish_directory_pr_completion_events,
    _publish_directory_pr_feedback_events,
)
from evander.events.provider_models import (
    PullRequestCompletedTriggerDefinition,
    PullRequestNewFeedbackTriggerDefinition,
)
from evander.models import GetPullRequestResponse
from evander.pr_feedback import FeedbackEnvelope
from evander.services import PrFeedbackService
from evander.triggers.queue import SqliteTriggerEventQueue
from config_helpers import canonical_workflow_payload, write_runtime_config


def _canonicalize_workflow_pack(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(canonical_workflow_payload(payload)), encoding="utf-8")


def test_ado_worker_runtime_config_load_rejects_retired_fields(tmp_path: Path) -> None:
    config_dir = tmp_path / "config" / "ado-worker"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "runtime_dir": "runtime",
                "poll_interval_seconds": 5,
                "directories": ["root/team-a"],
                "include_comments": True,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        AdoWorkerRuntimeConfig.load(workspace_root=tmp_path)


@pytest.fixture
def worker_config(tmp_path: Path) -> WorkerConfig:
    write_runtime_config(tmp_path, runtime_dir=tmp_path)
    return WorkerConfig(
        organization_url="https://dev.azure.com/example",
        project="example",
        poll_interval_seconds=30.0,
        runtime_dir=tmp_path,
        directories=("root/team-a",),
    )


def test_publish_directory_pr_feedback_events_enqueues_azure_feedback(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evander.daemons import ado_issue_worker as ado_worker

    (worker_config.runtime_dir / "sessions.json").write_text(
        json.dumps(
            {
                "session-6070": {
                    "session_id": "session-6070",
                    "directory_path": "root/team-a",
                    "allowed_tools": [],
                    "expires_at": "2026-04-09T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )
    pr_map_path = worker_config.runtime_dir / "pr-map.json"
    pr_map_path.write_text(
        json.dumps(
            {
                "entries": {
                    "azure_devops:org/repo:6070": {
                        "provider": "azure_devops",
                        "repo": "org/repo",
                        "pr_id": "6070",
                        "session_id": "session-6070",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    runtime_gateway_config = ado_worker.GatewayConfig.model_validate(
        {
            "runtime_dir": str(worker_config.runtime_dir),
            "session_store_path": str(worker_config.runtime_dir / "sessions.json"),
            "approval_store_path": str(worker_config.runtime_dir / "approvals.json"),
            "draft_store_path": str(worker_config.runtime_dir / "drafts.json"),
            "email_store_path": str(worker_config.runtime_dir / "emails.json"),
            "discord_command_store_path": str(worker_config.runtime_dir / "discord_commands.json"),
            "audit_log_path": str(worker_config.runtime_dir / "audit.jsonl"),
            "pr_map_path": str(pr_map_path),
            "azure_devops_pr_pat_env": "AZURE_DEVOPS_PR_PAT",
        }
    )
    trigger = PullRequestNewFeedbackTriggerDefinition.model_validate(
        {
            "id": "trigger-pr-feedback",
            "display_name": "PR feedback",
            "type": "trigger:pull-request:new-feedback",
            "provider": "ado",
            "organization_url": "https://dev.azure.com/example-org",
            "project": "Example Project",
            "agent_author_names": ["evander Bot"],
            "agent_mention_names": ["Truman Bot"],
            "agent_mention_ids": ["c74221cb-ee08-6a83-9055-af01c3333112"],
        }
    )

    monkeypatch.setattr(ado_worker, "_load_ado_pr_feedback_triggers", lambda _config: [trigger])
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "secret-pat")
    wake_paths: list[Path] = []
    monkeypatch.setattr(
        ado_worker,
        "notify_wake_socket_for_lock",
        lambda lock_path: wake_paths.append(Path(lock_path)) or True,
    )
    feedback_created_at = datetime.now(UTC).replace(microsecond=0) - timedelta(days=1)
    feedback_cursor = feedback_created_at.isoformat().replace("+00:00", "Z")

    def fake_poll(self, *, client, repo, pr_id, cursor):
        assert repo == "org/repo"
        assert pr_id == "6070"
        assert cursor is None
        return SimpleNamespace(
            feedback=[
                FeedbackEnvelope(
                    feedback_id="azure_devops:thread_comment:30830:1",
                    provider="azure_devops",
                    repo="org/repo",
                    pr_id="6070",
                    thread_id="30830",
                    comment_id="1",
                    author="jonathan",
                    body="@<C74221CB-EE08-6A83-9055-AF01C3333112> please fix this",
                    created_at=feedback_cursor,
                    metadata={"mentioned_ids": ["c74221cb-ee08-6a83-9055-af01c3333112"]},
                )
            ],
            next_cursor=feedback_cursor,
        )

    monkeypatch.setattr(ado_worker.AzureDevOpsFeedbackAdapter, "poll", fake_poll)

    with httpx.Client() as client:
        published = _publish_directory_pr_feedback_events(
            client=client,
            config=WorkerConfig(**{**worker_config.__dict__, "directory_path": "root/team-a"}),
            runtime_gateway_config=runtime_gateway_config,
            directory_path="root/team-a",
        )

    assert published == 1
    assert wake_paths == [worker_config.runtime_dir / "effect-orchestrator.lock"]
    queue = SqliteTriggerEventQueue(worker_config.runtime_dir / "orchestration.db")
    events = queue.list_pending(trigger_id="trigger-pr-feedback")
    assert len(events) == 1
    assert events[0].payload["session_id"] == "session-6070"
    assert events[0].payload["pr"] == {
        "provider": "azure_devops",
        "repo": "org/repo",
        "pr_id": "6070",
        "ado_organization": "example-org",
        "project": "Example Project",
    }
    assert events[0].payload["feedback"]["metadata"]["agent_directed"] is True
    assert events[0].payload["feedback"]["metadata"]["agent_directed_reason"] == "mention"
    feedback_store_payload = json.loads(
        (worker_config.runtime_dir / "pr-feedback-store.json").read_text(encoding="utf-8")
    )
    assert "azure_devops:thread_comment:30830:1" in feedback_store_payload["feedback"]
    assert (
        feedback_store_payload["feedback"]["azure_devops:thread_comment:30830:1"]["metadata"][
            "agent_directed"
        ]
        is True
    )
    service = PrFeedbackService(worker_config.runtime_dir / "pr-feedback-store.json")
    unread = service.list_unread_feedback(
        streams=[{"provider": "azure_devops", "repo": "org/repo", "pr_id": "6070"}],
        session_id="session-6070",
    )
    assert [item.feedback_id for item in unread.feedback] == ["azure_devops:thread_comment:30830:1"]
    cursor_payload = json.loads(
        (worker_config.runtime_dir / "trigger-cursors-pr-feedback.json").read_text(encoding="utf-8")
    )
    assert (
        cursor_payload["trigger-pr-feedback"]["stream_cursors"]["azure_devops:org/repo:6070"]
        == feedback_cursor
    )


def test_load_ado_pr_feedback_session_bindings_matches_worker_directory_exactly(
    worker_config: WorkerConfig,
) -> None:
    (worker_config.runtime_dir / "sessions.json").write_text(
        json.dumps(
            {
                "session-6070": {
                    "session_id": "session-6070",
                    "directory_path": "root/team-a",
                    "allowed_tools": [],
                    "expires_at": "2026-04-09T00:00:00Z",
                },
                "session-6071": {
                    "session_id": "session-6071",
                    "directory_path": "root/team-a/child",
                    "allowed_tools": [],
                    "expires_at": "2026-04-09T00:00:00Z",
                },
            }
        ),
        encoding="utf-8",
    )
    pr_map_path = worker_config.runtime_dir / "pr-map.json"
    pr_map_path.write_text(
        json.dumps(
            {
                "entries": {
                    "azure_devops:org/repo:6070": {
                        "provider": "azure_devops",
                        "repo": "org/repo",
                        "pr_id": "6070",
                        "session_id": "session-6070",
                    },
                    "azure_devops:org/repo:6071": {
                        "provider": "azure_devops",
                        "repo": "org/repo",
                        "pr_id": "6071",
                        "session_id": "session-6071",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    parent_bindings = _load_ado_pr_feedback_session_bindings(
        session_store_path=worker_config.runtime_dir / "sessions.json",
        pr_map_path=pr_map_path,
        directory_path="root/team-a",
    )
    child_bindings = _load_ado_pr_feedback_session_bindings(
        session_store_path=worker_config.runtime_dir / "sessions.json",
        pr_map_path=pr_map_path,
        directory_path="root/team-a/child",
    )

    assert sorted(parent_bindings) == [("org/repo", "6070")]
    assert sorted(child_bindings) == [("org/repo", "6071")]


def test_load_ado_pr_feedback_bindings_use_configured_store_for_legacy_directory(
    worker_config: WorkerConfig,
) -> None:
    configured_session_store_path = worker_config.runtime_dir.parent / "configured-sessions.json"
    configured_session_store_path.write_text(
        json.dumps(
            {
                "session-6070": {
                    "session_id": "session-6070",
                    "directory_path": "root/team-a",
                    "allowed_tools": [],
                    "expires_at": "2026-04-09T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )
    (worker_config.runtime_dir / "sessions.json").write_text(
        json.dumps(
            {
                "session-6070": {
                    "session_id": "session-6070",
                    "directory_path": "root/wrong",
                    "allowed_tools": [],
                    "expires_at": "2026-04-09T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )
    pr_map_path = worker_config.runtime_dir / "pr-map.json"
    pr_map_path.write_text(
        json.dumps(
            {
                "entries": {
                    "azure_devops:org/repo:6070": {
                        "provider": "azure_devops",
                        "repo": "org/repo",
                        "pr_id": "6070",
                        "session_id": "session-6070",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    root_bindings = _load_ado_pr_feedback_session_bindings(
        session_store_path=configured_session_store_path,
        pr_map_path=pr_map_path,
        directory_path="root",
    )
    session_bindings = _load_ado_pr_feedback_session_bindings(
        session_store_path=configured_session_store_path,
        pr_map_path=pr_map_path,
        directory_path="root/team-a",
    )
    wrong_bindings = _load_ado_pr_feedback_session_bindings(
        session_store_path=configured_session_store_path,
        pr_map_path=pr_map_path,
        directory_path="root/wrong",
    )

    assert root_bindings == {}
    assert sorted(session_bindings) == [("org/repo", "6070")]
    assert wrong_bindings == {}


@pytest.mark.parametrize(
    ("suppress_agent_authored_feedback", "expected_published"),
    [(False, 1), (True, 0)],
)
def test_publish_directory_pr_feedback_events_handles_agent_authored_feedback(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
    suppress_agent_authored_feedback: bool,
    expected_published: int,
) -> None:
    from evander.daemons import ado_issue_worker as ado_worker

    (worker_config.runtime_dir / "sessions.json").write_text(
        json.dumps(
            {
                "session-6070": {
                    "session_id": "session-6070",
                    "directory_path": "root/team-a",
                    "allowed_tools": [],
                    "expires_at": "2026-04-09T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )
    pr_map_path = worker_config.runtime_dir / "pr-map.json"
    pr_map_path.write_text(
        json.dumps(
            {
                "entries": {
                    "azure_devops:org/repo:6070": {
                        "provider": "azure_devops",
                        "repo": "org/repo",
                        "pr_id": "6070",
                        "session_id": "session-6070",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    runtime_gateway_config = ado_worker.GatewayConfig.model_validate(
        {
            "runtime_dir": str(worker_config.runtime_dir),
            "session_store_path": str(worker_config.runtime_dir / "sessions.json"),
            "approval_store_path": str(worker_config.runtime_dir / "approvals.json"),
            "draft_store_path": str(worker_config.runtime_dir / "drafts.json"),
            "email_store_path": str(worker_config.runtime_dir / "emails.json"),
            "discord_command_store_path": str(worker_config.runtime_dir / "discord_commands.json"),
            "audit_log_path": str(worker_config.runtime_dir / "audit.jsonl"),
            "pr_map_path": str(pr_map_path),
            "azure_devops_pr_pat_env": "AZURE_DEVOPS_PR_PAT",
        }
    )
    trigger = PullRequestNewFeedbackTriggerDefinition.model_validate(
        {
            "id": "trigger-pr-feedback",
            "display_name": "PR feedback",
            "type": "trigger:pull-request:new-feedback",
            "provider": "ado",
            "organization_url": "https://dev.azure.com/example-org",
            "project": "Example Project",
            "agent_author_names": ["evander Bot"],
            "agent_mention_names": ["Truman Bot"],
            "agent_mention_ids": ["c74221cb-ee08-6a83-9055-af01c3333112"],
            "suppress_agent_authored_feedback": suppress_agent_authored_feedback,
        }
    )

    monkeypatch.setattr(ado_worker, "_load_ado_pr_feedback_triggers", lambda _config: [trigger])
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "secret-pat")
    wake_paths: list[Path] = []
    monkeypatch.setattr(
        ado_worker,
        "notify_wake_socket_for_lock",
        lambda lock_path: wake_paths.append(Path(lock_path)) or True,
    )
    feedback_created_at = datetime.now(UTC).replace(microsecond=0) - timedelta(days=1)
    feedback_cursor = feedback_created_at.isoformat().replace("+00:00", "Z")

    def fake_poll(self, *, client, repo, pr_id, cursor):
        assert repo == "org/repo"
        assert pr_id == "6070"
        assert cursor is None
        return SimpleNamespace(
            feedback=[
                FeedbackEnvelope(
                    feedback_id="azure_devops:thread_comment:30830:5",
                    provider="azure_devops",
                    repo="org/repo",
                    pr_id="6070",
                    thread_id="30830",
                    comment_id="5",
                    author="evander Bot",
                    body="@<C74221CB-EE08-6A83-9055-AF01C3333112> already handled.",
                    created_at=feedback_cursor,
                    metadata={
                        "mentioned_ids": ["c74221cb-ee08-6a83-9055-af01c3333112"],
                        "prior_thread_comment_author_names": ["evander Bot", "jonathan"],
                    },
                )
            ],
            next_cursor=feedback_cursor,
        )

    monkeypatch.setattr(ado_worker.AzureDevOpsFeedbackAdapter, "poll", fake_poll)

    with httpx.Client() as client:
        published = _publish_directory_pr_feedback_events(
            client=client,
            config=WorkerConfig(**{**worker_config.__dict__, "directory_path": "root/team-a"}),
            runtime_gateway_config=runtime_gateway_config,
            directory_path="root/team-a",
        )

    assert published == expected_published
    assert wake_paths == (
        [worker_config.runtime_dir / "effect-orchestrator.lock"] if expected_published else []
    )
    queue = SqliteTriggerEventQueue(worker_config.runtime_dir / "orchestration.db")
    events = queue.list_pending(trigger_id="trigger-pr-feedback")
    assert len(events) == expected_published
    if expected_published:
        assert events[0].payload["feedback"]["metadata"]["agent_directed_author_self"] is True
    feedback_store_payload = json.loads(
        (worker_config.runtime_dir / "pr-feedback-store.json").read_text(encoding="utf-8")
    )
    stored = feedback_store_payload["feedback"]["azure_devops:thread_comment:30830:5"]
    assert stored["metadata"]["agent_directed"] is False
    assert stored["metadata"]["agent_directed_author_self"] is True
    assert stored["metadata"]["agent_directed_reason"] == "self_authored"


@pytest.mark.parametrize(
    ("pr_status", "expected_merged", "expected_completed_at"),
    [
        ("completed", True, "2026-05-15T10:00:00Z"),
        ("abandoned", False, "2026-05-15T10:00:00Z"),
    ],
)
def test_publish_directory_pr_completion_events_updates_pr_map_and_enqueues_event(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
    pr_status: str,
    expected_merged: bool,
    expected_completed_at: str,
) -> None:
    from evander.daemons import ado_issue_worker as ado_worker

    (worker_config.runtime_dir / "sessions.json").write_text(
        json.dumps(
            {
                "session-6070": {
                    "session_id": "session-6070",
                    "directory_path": "root/team-a",
                    "allowed_tools": [],
                    "expires_at": "2026-05-16T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )
    pr_map_path = worker_config.runtime_dir / "pr-map.json"
    pr_map_path.write_text(
        json.dumps(
            {
                "entries": {
                    "azure_devops:org/repo:6070": {
                        "provider": "azure_devops",
                        "repo": "org/repo",
                        "pr_id": "6070",
                        "session_id": "session-6070",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    runtime_gateway_config = ado_worker.GatewayConfig.model_validate(
        {
            "runtime_dir": str(worker_config.runtime_dir),
            "session_store_path": str(worker_config.runtime_dir / "sessions.json"),
            "approval_store_path": str(worker_config.runtime_dir / "approvals.json"),
            "draft_store_path": str(worker_config.runtime_dir / "drafts.json"),
            "email_store_path": str(worker_config.runtime_dir / "emails.json"),
            "discord_command_store_path": str(worker_config.runtime_dir / "discord_commands.json"),
            "audit_log_path": str(worker_config.runtime_dir / "audit.jsonl"),
            "pr_map_path": str(pr_map_path),
            "azure_devops_pr_pat_env": "AZURE_DEVOPS_PR_PAT",
        }
    )
    trigger = PullRequestCompletedTriggerDefinition.model_validate(
        {
            "id": "trigger-pr-completed",
            "display_name": "PR completed",
            "type": "trigger:pull-request:completed",
            "provider": "ado",
            "organization_url": "https://dev.azure.com/example-org",
            "project": "Example Project",
        }
    )

    monkeypatch.setattr(ado_worker, "_load_ado_pr_completed_triggers", lambda _config: [trigger])
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "secret-pat")
    calls: list[tuple[str, str, bool | None, bool | None]] = []

    def fake_get_pull_request(self, *, repo, pr_id, include_reviewers=None, include_commits=None):
        calls.append((repo, pr_id, include_reviewers, include_commits))
        return GetPullRequestResponse(
            provider="azure_devops",
            repo=repo,
            pr_id=pr_id,
            title="Feature PR",
            status=pr_status,
            url="https://dev.azure.com/example-org/Example%20Project/_git/repo/pullrequest/6070",
            source_branch="refs/heads/feature/6070",
            target_branch="refs/heads/main",
            source_commit="abc123",
            target_commit="def456",
            raw={"status": pr_status, "closedDate": "2026-05-15T10:00:00Z"},
        )

    monkeypatch.setattr(
        ado_worker.AzureDevOpsPrQueryProvider,
        "get_pull_request",
        fake_get_pull_request,
    )

    with httpx.Client() as client:
        published = _publish_directory_pr_completion_events(
            client=client,
            config=WorkerConfig(**{**worker_config.__dict__, "directory_path": "root/team-a"}),
            runtime_gateway_config=runtime_gateway_config,
            directory_path="root/team-a",
        )

    assert published == 1
    assert calls == [("org/repo", "6070", False, False)]
    queue = SqliteTriggerEventQueue(worker_config.runtime_dir / "orchestration.db")
    events = queue.list_pending(trigger_id="trigger-pr-completed")
    assert len(events) == 1
    assert events[0].payload["session_id"] == "session-6070"
    assert events[0].payload["status"] == pr_status
    assert events[0].payload["merged"] is expected_merged
    assert events[0].payload["completed_at"] == expected_completed_at
    assert events[0].payload["pr"]["target_branch"] == "refs/heads/main"

    pr_map_payload = json.loads(pr_map_path.read_text(encoding="utf-8"))
    entry = pr_map_payload["entries"]["azure_devops:org/repo:6070"]
    assert entry["status"] == pr_status
    assert entry["merged"] is expected_merged
    assert entry["completion_events"]["trigger-pr-completed"]["event_id"] == events[0].event_id
    audit_log_path = worker_config.runtime_dir / "audit.jsonl"
    audit_records = [
        json.loads(line)
        for line in audit_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert audit_records[-1]["event_type"] == "pr_completion_detected"
    assert audit_records[-1]["provider"] == "azure_devops"
    assert audit_records[-1]["repo"] == "org/repo"
    assert audit_records[-1]["pr_id"] == "6070"
    assert audit_records[-1]["result"] == {"event_id": events[0].event_id}
    assert not (worker_config.runtime_dir / "audit.log").exists()

    with httpx.Client() as client:
        republished = _publish_directory_pr_completion_events(
            client=client,
            config=WorkerConfig(**{**worker_config.__dict__, "directory_path": "root/team-a"}),
            runtime_gateway_config=runtime_gateway_config,
            directory_path="root/team-a",
        )

    assert republished == 0


def test_publish_directory_pr_feedback_events_uses_explicit_pr_query_candidates_without_pr_map(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evander.daemons import ado_issue_worker as ado_worker

    runtime_gateway_config = ado_worker.GatewayConfig.model_validate(
        {
            "runtime_dir": str(worker_config.runtime_dir),
            "session_store_path": str(worker_config.runtime_dir / "sessions.json"),
            "approval_store_path": str(worker_config.runtime_dir / "approvals.json"),
            "draft_store_path": str(worker_config.runtime_dir / "drafts.json"),
            "email_store_path": str(worker_config.runtime_dir / "emails.json"),
            "discord_command_store_path": str(worker_config.runtime_dir / "discord_commands.json"),
            "audit_log_path": str(worker_config.runtime_dir / "audit.jsonl"),
            "pr_map_path": str(worker_config.runtime_dir / "pr-map.json"),
            "azure_devops_pr_pat_env": "AZURE_DEVOPS_PR_PAT",
        }
    )
    trigger = PullRequestNewFeedbackTriggerDefinition.model_validate(
        {
            "id": "trigger-pr-feedback-explicit",
            "display_name": "PR feedback explicit",
            "type": "trigger:pull-request:new-feedback",
            "provider": "ado",
            "repo": "org/repo",
            "directory_path": "root/team-a",
            "organization_url": " https://dev.azure.com/example-org/ ",
            "project": " Example Project ",
            "status": "active",
            "creator_id": "creator-1",
            "reviewer_id": "reviewer-1",
            "source_ref_name": "refs/heads/feature",
            "target_ref_name": "refs/heads/main",
            "source_repository_id": "source-repo-1",
            "repository_id": "repository-1",
            "min_time": "2026-05-01T00:00:00Z",
            "max_time": "2026-05-02T00:00:00Z",
            "query_time_range_type": "created",
            "include_links": True,
            "max_comment_length": 5,
            "tags": "needs-review",
            "top": 1,
            "skip": 0,
            "cursor": "cursor-1",
        }
    )

    monkeypatch.setattr(ado_worker, "_load_ado_pr_feedback_triggers", lambda _config: [trigger])
    monkeypatch.setattr(
        ado_worker,
        "_load_ado_pr_feedback_session_bindings",
        lambda **_kwargs: pytest.fail("explicit PR polling should not load pr-map bindings"),
    )
    monkeypatch.setattr(
        ado_worker,
        "_resolve_directory_worker_config",
        lambda config, runtime_gateway_config, directory_path: WorkerConfig(
            **{**worker_config.__dict__, "directory_path": directory_path}
        ),
    )
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "secret-pat")

    list_pull_request_calls: list[dict[str, object]] = []

    def fake_list_pull_requests(self, **kwargs):
        list_pull_request_calls.append(
            {
                "organization_url": self.organization_url,
                "project": self.project,
                "pat": self.pat,
                "kwargs": kwargs,
            }
        )
        assert kwargs == {
            "repo": "org/repo",
            "project": "Example Project",
            "status": "active",
            "creator_id": "creator-1",
            "reviewer_id": "reviewer-1",
            "source_ref_name": "refs/heads/feature",
            "target_ref_name": "refs/heads/main",
            "source_repository_id": "source-repo-1",
            "repository_id": "repository-1",
            "min_time": "2026-05-01T00:00:00Z",
            "max_time": "2026-05-02T00:00:00Z",
            "query_time_range_type": "created",
            "include_links": True,
            "max_comment_length": 5,
            "tags": "needs-review",
            "top": 1,
            "skip": 0,
            "cursor": "cursor-1",
        }
        return SimpleNamespace(
            pull_requests=[SimpleNamespace(provider="azure_devops", repo="org/repo", pr_id="6070")]
        )

    monkeypatch.setattr(
        ado_worker.AzureDevOpsPrQueryProvider,
        "list_pull_requests",
        fake_list_pull_requests,
    )

    poll_calls: list[tuple[str, str, str, str | None]] = []

    def fake_poll(self, *, client, repo, pr_id, cursor):
        poll_calls.append((self.organization_url, self.project, repo, pr_id))
        assert repo == "org/repo"
        assert pr_id == "6070"
        assert cursor is None
        return SimpleNamespace(
            feedback=[
                FeedbackEnvelope(
                    feedback_id="azure_devops:thread_comment:30830:1",
                    provider="azure_devops",
                    repo=repo,
                    pr_id=pr_id,
                    thread_id="30830",
                    comment_id="1",
                    author="jonathan",
                    body="Please fix this",
                    created_at="2026-04-08T05:33:41.433000Z",
                )
            ],
            next_cursor="2026-04-08T05:33:41.433000Z",
        )

    monkeypatch.setattr(ado_worker.AzureDevOpsFeedbackAdapter, "poll", fake_poll)

    with httpx.Client() as client:
        published = _publish_directory_pr_feedback_events(
            client=client,
            config=WorkerConfig(**{**worker_config.__dict__, "directory_path": "root/team-a"}),
            runtime_gateway_config=runtime_gateway_config,
            directory_path="root/team-a",
        )

    assert published == 1
    assert list_pull_request_calls == [
        {
            "organization_url": "https://dev.azure.com/example-org",
            "project": "Example Project",
            "pat": "secret-pat",
            "kwargs": {
                "repo": "org/repo",
                "project": "Example Project",
                "status": "active",
                "creator_id": "creator-1",
                "reviewer_id": "reviewer-1",
                "source_ref_name": "refs/heads/feature",
                "target_ref_name": "refs/heads/main",
                "source_repository_id": "source-repo-1",
                "repository_id": "repository-1",
                "min_time": "2026-05-01T00:00:00Z",
                "max_time": "2026-05-02T00:00:00Z",
                "query_time_range_type": "created",
                "include_links": True,
                "max_comment_length": 5,
                "tags": "needs-review",
                "top": 1,
                "skip": 0,
                "cursor": "cursor-1",
            },
        }
    ]
    assert poll_calls == [
        ("https://dev.azure.com/example-org", "Example Project", "org/repo", "6070")
    ]
    assert not (worker_config.runtime_dir / "pr-map.json").exists()
    queue = SqliteTriggerEventQueue(worker_config.runtime_dir / "orchestration.db")
    events = queue.list_pending(trigger_id="trigger-pr-feedback-explicit")
    assert len(events) == 1
    assert "session_id" not in events[0].payload
    assert events[0].payload["pr"] == {
        "provider": "azure_devops",
        "repo": "org/repo",
        "pr_id": "6070",
        "ado_organization": "example-org",
        "project": "Example Project",
    }
    cursor_payload = json.loads(
        (worker_config.runtime_dir / "trigger-cursors-pr-feedback.json").read_text(encoding="utf-8")
    )
    assert (
        cursor_payload["trigger-pr-feedback-explicit"]["stream_cursors"][
            "azure_devops:org/repo:6070"
        ]
        == "2026-04-08T05:33:41.433000Z"
    )


def test_publish_directory_pr_feedback_events_keeps_explicit_stream_unbound_with_legacy_trigger(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evander.daemons import ado_issue_worker as ado_worker

    (worker_config.runtime_dir / "sessions.json").write_text(
        json.dumps(
            {
                "session-6070": {
                    "session_id": "session-6070",
                    "directory_path": "root/team-a",
                    "allowed_tools": [],
                    "expires_at": "2026-05-16T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )
    pr_map_path = worker_config.runtime_dir / "pr-map.json"
    pr_map_path.write_text(
        json.dumps(
            {
                "entries": {
                    "azure_devops:org/repo:6070": {
                        "provider": "azure_devops",
                        "repo": "org/repo",
                        "pr_id": "6070",
                        "session_id": "session-6070",
                        "directory_path": "root/team-a",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    runtime_gateway_config = ado_worker.GatewayConfig.model_validate(
        {
            "runtime_dir": str(worker_config.runtime_dir),
            "session_store_path": str(worker_config.runtime_dir / "sessions.json"),
            "approval_store_path": str(worker_config.runtime_dir / "approvals.json"),
            "draft_store_path": str(worker_config.runtime_dir / "drafts.json"),
            "email_store_path": str(worker_config.runtime_dir / "emails.json"),
            "discord_command_store_path": str(worker_config.runtime_dir / "discord_commands.json"),
            "audit_log_path": str(worker_config.runtime_dir / "audit.jsonl"),
            "pr_map_path": str(pr_map_path),
            "azure_devops_pr_pat_env": "AZURE_DEVOPS_PR_PAT",
        }
    )
    legacy_trigger = PullRequestNewFeedbackTriggerDefinition.model_validate(
        {
            "id": "trigger-pr-feedback-legacy",
            "display_name": "PR feedback legacy",
            "type": "trigger:pull-request:new-feedback",
            "provider": "ado",
            "organization_url": "https://dev.azure.com/example-org",
            "project": "Example Project",
        }
    )
    explicit_trigger = PullRequestNewFeedbackTriggerDefinition.model_validate(
        {
            "id": "trigger-pr-feedback-explicit",
            "display_name": "PR feedback explicit",
            "type": "trigger:pull-request:new-feedback",
            "provider": "ado",
            "repo": "org/repo",
            "directory_path": "root/team-a",
            "organization_url": "https://dev.azure.com/example-org",
            "project": "Example Project",
            "status": "active",
        }
    )

    monkeypatch.setattr(
        ado_worker,
        "_load_ado_pr_feedback_triggers",
        lambda _config: [legacy_trigger, explicit_trigger],
    )
    monkeypatch.setattr(
        ado_worker,
        "_resolve_directory_worker_config",
        lambda config, runtime_gateway_config, directory_path: WorkerConfig(
            **{**worker_config.__dict__, "directory_path": directory_path}
        ),
    )
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "secret-pat")

    def fake_list_pull_requests(self, **kwargs):
        assert kwargs["status"] == "active"
        return SimpleNamespace(
            pull_requests=[SimpleNamespace(provider="azure_devops", repo="org/repo", pr_id="6070")]
        )

    monkeypatch.setattr(
        ado_worker.AzureDevOpsPrQueryProvider,
        "list_pull_requests",
        fake_list_pull_requests,
    )

    def fake_poll(self, *, client, repo, pr_id, cursor):
        assert repo == "org/repo"
        assert pr_id == "6070"
        return SimpleNamespace(
            feedback=[
                FeedbackEnvelope(
                    feedback_id="azure_devops:thread_comment:30830:1",
                    provider="azure_devops",
                    repo=repo,
                    pr_id=pr_id,
                    thread_id="30830",
                    comment_id="1",
                    author="jonathan",
                    body="Please fix this",
                    created_at="2026-04-08T05:33:41.433000Z",
                )
            ],
            next_cursor="2026-04-08T05:33:41.433000Z",
        )

    monkeypatch.setattr(ado_worker.AzureDevOpsFeedbackAdapter, "poll", fake_poll)

    with httpx.Client() as client:
        published = _publish_directory_pr_feedback_events(
            client=client,
            config=WorkerConfig(**{**worker_config.__dict__, "directory_path": "root/team-a"}),
            runtime_gateway_config=runtime_gateway_config,
            directory_path="root/team-a",
        )

    assert published == 2
    queue = SqliteTriggerEventQueue(worker_config.runtime_dir / "orchestration.db")
    legacy_events = queue.list_pending(trigger_id="trigger-pr-feedback-legacy")
    explicit_events = queue.list_pending(trigger_id="trigger-pr-feedback-explicit")
    assert len(legacy_events) == 1
    assert legacy_events[0].payload["session_id"] == "session-6070"
    assert len(explicit_events) == 1
    assert "session_id" not in explicit_events[0].payload


def test_publish_directory_pr_completion_events_uses_explicit_pr_query_candidates_without_pr_map(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evander.daemons import ado_issue_worker as ado_worker

    runtime_gateway_config = ado_worker.GatewayConfig.model_validate(
        {
            "runtime_dir": str(worker_config.runtime_dir),
            "session_store_path": str(worker_config.runtime_dir / "sessions.json"),
            "approval_store_path": str(worker_config.runtime_dir / "approvals.json"),
            "draft_store_path": str(worker_config.runtime_dir / "drafts.json"),
            "email_store_path": str(worker_config.runtime_dir / "emails.json"),
            "discord_command_store_path": str(worker_config.runtime_dir / "discord_commands.json"),
            "audit_log_path": str(worker_config.runtime_dir / "audit.jsonl"),
            "pr_map_path": str(worker_config.runtime_dir / "pr-map.json"),
            "azure_devops_pr_pat_env": "AZURE_DEVOPS_PR_PAT",
        }
    )
    trigger = PullRequestCompletedTriggerDefinition.model_validate(
        {
            "id": "trigger-pr-completed-explicit",
            "display_name": "PR completed explicit",
            "type": "trigger:pull-request:completed",
            "provider": "ado",
            "repo": "org/repo",
            "directory_path": "root/team-a",
            "organization_url": " https://dev.azure.com/example-org/ ",
            "project": " Example Project ",
            "status": "completed",
            "creator_id": "creator-1",
            "reviewer_id": "reviewer-1",
            "source_ref_name": "refs/heads/feature",
            "target_ref_name": "refs/heads/main",
            "source_repository_id": "source-repo-1",
            "repository_id": "repository-1",
            "min_time": "2026-05-01T00:00:00Z",
            "max_time": "2026-05-02T00:00:00Z",
            "query_time_range_type": "completed",
            "include_links": False,
            "max_comment_length": 3,
            "tags": "needs-review",
            "top": 1,
            "skip": 0,
            "cursor": "cursor-2",
        }
    )

    monkeypatch.setattr(ado_worker, "_load_ado_pr_completed_triggers", lambda _config: [trigger])
    monkeypatch.setattr(
        ado_worker,
        "_load_ado_pr_feedback_session_bindings",
        lambda **_kwargs: pytest.fail("explicit PR polling should not load pr-map bindings"),
    )
    monkeypatch.setattr(
        ado_worker,
        "_resolve_directory_worker_config",
        lambda config, runtime_gateway_config, directory_path: WorkerConfig(
            **{**worker_config.__dict__, "directory_path": directory_path}
        ),
    )
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "secret-pat")

    list_pull_request_calls: list[dict[str, object]] = []

    def fake_list_pull_requests(self, **kwargs):
        list_pull_request_calls.append(
            {
                "organization_url": self.organization_url,
                "project": self.project,
                "pat": self.pat,
                "kwargs": kwargs,
            }
        )
        assert kwargs == {
            "repo": "org/repo",
            "project": "Example Project",
            "status": "completed",
            "creator_id": "creator-1",
            "reviewer_id": "reviewer-1",
            "source_ref_name": "refs/heads/feature",
            "target_ref_name": "refs/heads/main",
            "source_repository_id": "source-repo-1",
            "repository_id": "repository-1",
            "min_time": "2026-05-01T00:00:00Z",
            "max_time": "2026-05-02T00:00:00Z",
            "query_time_range_type": "completed",
            "include_links": False,
            "max_comment_length": 3,
            "tags": "needs-review",
            "top": 1,
            "skip": 0,
            "cursor": "cursor-2",
        }
        return SimpleNamespace(
            pull_requests=[SimpleNamespace(provider="azure_devops", repo="org/repo", pr_id="6070")]
        )

    monkeypatch.setattr(
        ado_worker.AzureDevOpsPrQueryProvider,
        "list_pull_requests",
        fake_list_pull_requests,
    )

    get_calls: list[tuple[str, str, str, str, bool | None, bool | None]] = []

    def fake_get_pull_request(self, *, repo, pr_id, include_reviewers=None, include_commits=None):
        get_calls.append(
            (self.organization_url, self.project, repo, pr_id, include_reviewers, include_commits)
        )
        assert repo == "org/repo"
        assert pr_id == "6070"
        return GetPullRequestResponse(
            provider="azure_devops",
            repo=repo,
            pr_id=pr_id,
            title="Feature PR",
            status="completed",
            url="https://dev.azure.com/example-org/Example%20Project/_git/repo/pullrequest/6070",
            source_branch="refs/heads/feature/6070",
            target_branch="refs/heads/main",
            source_commit="abc123",
            target_commit="def456",
            raw={"status": "completed", "closedDate": "2026-05-15T10:00:00Z"},
        )

    monkeypatch.setattr(
        ado_worker.AzureDevOpsPrQueryProvider,
        "get_pull_request",
        fake_get_pull_request,
    )

    with httpx.Client() as client:
        published = _publish_directory_pr_completion_events(
            client=client,
            config=WorkerConfig(**{**worker_config.__dict__, "directory_path": "root/team-a"}),
            runtime_gateway_config=runtime_gateway_config,
            directory_path="root/team-a",
        )

    assert published == 1
    assert list_pull_request_calls == [
        {
            "organization_url": "https://dev.azure.com/example-org",
            "project": "Example Project",
            "pat": "secret-pat",
            "kwargs": {
                "repo": "org/repo",
                "project": "Example Project",
                "status": "completed",
                "creator_id": "creator-1",
                "reviewer_id": "reviewer-1",
                "source_ref_name": "refs/heads/feature",
                "target_ref_name": "refs/heads/main",
                "source_repository_id": "source-repo-1",
                "repository_id": "repository-1",
                "min_time": "2026-05-01T00:00:00Z",
                "max_time": "2026-05-02T00:00:00Z",
                "query_time_range_type": "completed",
                "include_links": False,
                "max_comment_length": 3,
                "tags": "needs-review",
                "top": 1,
                "skip": 0,
                "cursor": "cursor-2",
            },
        }
    ]
    assert get_calls == [
        (
            "https://dev.azure.com/example-org",
            "Example Project",
            "org/repo",
            "6070",
            False,
            False,
        )
    ]
    assert not (worker_config.runtime_dir / "pr-map.json").exists()
    queue = SqliteTriggerEventQueue(worker_config.runtime_dir / "orchestration.db")
    events = queue.list_pending(trigger_id="trigger-pr-completed-explicit")
    assert len(events) == 1
    assert "session_id" not in events[0].payload
    assert events[0].payload["status"] == "completed"
    assert events[0].payload["merged"] is True
    assert events[0].payload["completed_at"] == "2026-05-15T10:00:00Z"
    cursor_payload = json.loads(
        (worker_config.runtime_dir / "trigger-cursors-pr-completed.json").read_text(
            encoding="utf-8"
        )
    )
    assert (
        cursor_payload["trigger-pr-completed-explicit"]["completed_streams"][
            "azure_devops:org/repo:6070"
        ]
        == events[0].event_id
    )
    assert get_calls == [
        (
            "https://dev.azure.com/example-org",
            "Example Project",
            "org/repo",
            "6070",
            False,
            False,
        )
    ]

    with httpx.Client() as client:
        republished = _publish_directory_pr_completion_events(
            client=client,
            config=WorkerConfig(**{**worker_config.__dict__, "directory_path": "root/team-a"}),
            runtime_gateway_config=runtime_gateway_config,
            directory_path="root/team-a",
        )

    assert republished == 0
    assert get_calls == [
        (
            "https://dev.azure.com/example-org",
            "Example Project",
            "org/repo",
            "6070",
            False,
            False,
        )
    ]


def test_publish_directory_pr_completion_events_defaults_explicit_query_to_terminal_statuses(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evander.daemons import ado_issue_worker as ado_worker

    runtime_gateway_config = ado_worker.GatewayConfig.model_validate(
        {
            "runtime_dir": str(worker_config.runtime_dir),
            "session_store_path": str(worker_config.runtime_dir / "sessions.json"),
            "approval_store_path": str(worker_config.runtime_dir / "approvals.json"),
            "draft_store_path": str(worker_config.runtime_dir / "drafts.json"),
            "email_store_path": str(worker_config.runtime_dir / "emails.json"),
            "discord_command_store_path": str(worker_config.runtime_dir / "discord_commands.json"),
            "audit_log_path": str(worker_config.runtime_dir / "audit.jsonl"),
            "pr_map_path": str(worker_config.runtime_dir / "pr-map.json"),
            "azure_devops_pr_pat_env": "AZURE_DEVOPS_PR_PAT",
        }
    )
    trigger = PullRequestCompletedTriggerDefinition.model_validate(
        {
            "id": "trigger-pr-completed-explicit",
            "display_name": "PR completed explicit",
            "type": "trigger:pull-request:completed",
            "provider": "ado",
            "repo": "org/repo",
            "directory_path": "root/team-a",
            "organization_url": "https://dev.azure.com/example-org",
            "project": "Example Project",
        }
    )

    monkeypatch.setattr(ado_worker, "_load_ado_pr_completed_triggers", lambda _config: [trigger])
    monkeypatch.setattr(
        ado_worker,
        "_resolve_directory_worker_config",
        lambda config, runtime_gateway_config, directory_path: WorkerConfig(
            **{**worker_config.__dict__, "directory_path": directory_path}
        ),
    )
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "secret-pat")

    list_statuses: list[str | None] = []

    def fake_list_pull_requests(self, **kwargs):
        status = kwargs["status"]
        list_statuses.append(status)
        pr_id_by_status = {"completed": "6070", "abandoned": "6071"}
        return SimpleNamespace(
            pull_requests=[
                SimpleNamespace(
                    provider="azure_devops",
                    repo="org/repo",
                    pr_id=pr_id_by_status[status],
                )
            ]
        )

    monkeypatch.setattr(
        ado_worker.AzureDevOpsPrQueryProvider,
        "list_pull_requests",
        fake_list_pull_requests,
    )

    def fake_get_pull_request(self, *, repo, pr_id, include_reviewers=None, include_commits=None):
        pr_status = "completed" if pr_id == "6070" else "abandoned"
        return GetPullRequestResponse(
            provider="azure_devops",
            repo=repo,
            pr_id=pr_id,
            title=f"Feature PR {pr_id}",
            status=pr_status,
            url=f"https://dev.azure.com/example-org/Example%20Project/_git/repo/pullrequest/{pr_id}",
            source_branch=f"refs/heads/feature/{pr_id}",
            target_branch="refs/heads/main",
            source_commit="abc123",
            target_commit="def456",
            raw={"status": pr_status, "closedDate": "2026-05-15T10:00:00Z"},
        )

    monkeypatch.setattr(
        ado_worker.AzureDevOpsPrQueryProvider,
        "get_pull_request",
        fake_get_pull_request,
    )

    with httpx.Client() as client:
        published = _publish_directory_pr_completion_events(
            client=client,
            config=WorkerConfig(**{**worker_config.__dict__, "directory_path": "root/team-a"}),
            runtime_gateway_config=runtime_gateway_config,
            directory_path="root/team-a",
        )

    assert list_statuses == ["completed", "abandoned"]
    assert published == 2
    queue = SqliteTriggerEventQueue(worker_config.runtime_dir / "orchestration.db")
    events = queue.list_pending(trigger_id="trigger-pr-completed-explicit")
    assert [event.payload["status"] for event in events] == ["completed", "abandoned"]
    assert [event.payload["merged"] for event in events] == [True, False]


def test_publish_directory_pr_feedback_events_uses_session_ado_project(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evander.daemons import ado_issue_worker as ado_worker

    (worker_config.runtime_dir / "sessions.json").write_text(
        json.dumps(
            {
                "session-6281": {
                    "session_id": "session-6281",
                    "directory_path": "root/team-a",
                    "allowed_tools": [],
                    "expires_at": "2026-04-22T00:00:00Z",
                    "metadata": {"ado_project": "Example Industry Scanspac"},
                }
            }
        ),
        encoding="utf-8",
    )
    pr_map_path = worker_config.runtime_dir / "pr-map.json"
    pr_map_path.write_text(
        json.dumps(
            {
                "entries": {
                    "azure_devops:exampleindustry/Scanspac/Scanspac:6281": {
                        "provider": "azure_devops",
                        "repo": "exampleindustry/Scanspac/Scanspac",
                        "pr_id": "6281",
                        "session_id": "session-6281",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    runtime_gateway_config = ado_worker.GatewayConfig.model_validate(
        {
            "runtime_dir": str(worker_config.runtime_dir),
            "session_store_path": str(worker_config.runtime_dir / "sessions.json"),
            "approval_store_path": str(worker_config.runtime_dir / "approvals.json"),
            "draft_store_path": str(worker_config.runtime_dir / "drafts.json"),
            "email_store_path": str(worker_config.runtime_dir / "emails.json"),
            "discord_command_store_path": str(worker_config.runtime_dir / "discord_commands.json"),
            "audit_log_path": str(worker_config.runtime_dir / "audit.jsonl"),
            "pr_map_path": str(pr_map_path),
            "azure_devops_pr_pat_env": "AZURE_DEVOPS_PR_PAT",
        }
    )
    trigger = PullRequestNewFeedbackTriggerDefinition.model_validate(
        {
            "id": "trigger-pr-feedback",
            "display_name": "PR feedback",
            "type": "trigger:pull-request:new-feedback",
            "provider": "ado",
            "organization_url": "https://dev.azure.com/example-org",
            "project": "Example Industry APP",
        }
    )

    monkeypatch.setattr(ado_worker, "_load_ado_pr_feedback_triggers", lambda _config: [trigger])
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "secret-pat")
    observed_projects: list[str] = []

    def fake_poll(self, *, client, repo, pr_id, cursor):
        observed_projects.append(self.project)
        assert repo == "exampleindustry/Scanspac/Scanspac"
        assert pr_id == "6281"
        return SimpleNamespace(feedback=[], next_cursor=cursor)

    monkeypatch.setattr(ado_worker.AzureDevOpsFeedbackAdapter, "poll", fake_poll)

    with httpx.Client() as client:
        published = _publish_directory_pr_feedback_events(
            client=client,
            config=WorkerConfig(**{**worker_config.__dict__, "directory_path": "root/team-a"}),
            runtime_gateway_config=runtime_gateway_config,
            directory_path="root/team-a",
        )

    assert published == 0
    assert observed_projects == ["Scanspac"]


def test_publish_directory_pr_feedback_events_skips_missing_pr_stream_and_continues(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from evander.daemons import ado_issue_worker as ado_worker

    (worker_config.runtime_dir / "sessions.json").write_text(
        json.dumps(
            {
                "session-100": {
                    "session_id": "session-100",
                    "directory_path": "root/team-a",
                    "allowed_tools": [],
                    "expires_at": "2026-05-13T00:00:00Z",
                },
                "session-200": {
                    "session_id": "session-200",
                    "directory_path": "root/team-a",
                    "allowed_tools": [],
                    "expires_at": "2026-05-13T00:00:00Z",
                },
            }
        ),
        encoding="utf-8",
    )
    pr_map_path = worker_config.runtime_dir / "pr-map.json"
    pr_map_path.write_text(
        json.dumps(
            {
                "entries": {
                    "azure_devops:org/repo:100": {
                        "provider": "azure_devops",
                        "repo": "org/repo",
                        "pr_id": "100",
                        "session_id": "session-100",
                    },
                    "azure_devops:org/repo:200": {
                        "provider": "azure_devops",
                        "repo": "org/repo",
                        "pr_id": "200",
                        "session_id": "session-200",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    runtime_gateway_config = ado_worker.GatewayConfig.model_validate(
        {
            "runtime_dir": str(worker_config.runtime_dir),
            "session_store_path": str(worker_config.runtime_dir / "sessions.json"),
            "approval_store_path": str(worker_config.runtime_dir / "approvals.json"),
            "draft_store_path": str(worker_config.runtime_dir / "drafts.json"),
            "email_store_path": str(worker_config.runtime_dir / "emails.json"),
            "discord_command_store_path": str(worker_config.runtime_dir / "discord_commands.json"),
            "audit_log_path": str(worker_config.runtime_dir / "audit.jsonl"),
            "pr_map_path": str(pr_map_path),
            "azure_devops_pr_pat_env": "AZURE_DEVOPS_PR_PAT",
        }
    )
    trigger = PullRequestNewFeedbackTriggerDefinition.model_validate(
        {
            "id": "trigger-pr-feedback",
            "display_name": "PR feedback",
            "type": "trigger:pull-request:new-feedback",
            "provider": "ado",
            "organization_url": "https://dev.azure.com/example-org",
            "project": "Example Project",
        }
    )

    monkeypatch.setattr(ado_worker, "_load_ado_pr_feedback_triggers", lambda _config: [trigger])
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "secret-pat")
    calls: list[tuple[str, str]] = []

    def fake_poll(self, *, client, repo, pr_id, cursor):
        calls.append((repo, pr_id))
        if pr_id == "100":
            request = httpx.Request("GET", "https://dev.azure.com/example-org/project")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("not found", request=request, response=response)
        return SimpleNamespace(
            feedback=[
                FeedbackEnvelope(
                    feedback_id="azure_devops:thread_comment:200:1",
                    provider="azure_devops",
                    repo=repo,
                    pr_id=pr_id,
                    thread_id="200",
                    comment_id="1",
                    author="jonathan",
                    body="Still needs work",
                    created_at="2026-05-13T09:00:00Z",
                )
            ],
            next_cursor="2026-05-13T09:00:00Z",
        )

    monkeypatch.setattr(ado_worker.AzureDevOpsFeedbackAdapter, "poll", fake_poll)
    caplog.set_level(logging.WARNING, logger=ado_worker.__name__)

    with httpx.Client() as client:
        published = _publish_directory_pr_feedback_events(
            client=client,
            config=WorkerConfig(**{**worker_config.__dict__, "directory_path": "root/team-a"}),
            runtime_gateway_config=runtime_gateway_config,
            directory_path="root/team-a",
        )

    assert calls == [("org/repo", "100"), ("org/repo", "200")]
    assert published == 1
    assert "Skipping Azure PR feedback stream after 404" in caplog.text
    queue = SqliteTriggerEventQueue(worker_config.runtime_dir / "orchestration.db")
    events = queue.list_pending(trigger_id="trigger-pr-feedback")
    assert len(events) == 1
    assert events[0].payload["session_id"] == "session-200"
    assert events[0].payload["pr"]["pr_id"] == "200"
    cursor_payload = json.loads(
        (worker_config.runtime_dir / "trigger-cursors-pr-feedback.json").read_text(encoding="utf-8")
    )
    assert (
        "azure_devops:org/repo:100" not in cursor_payload["trigger-pr-feedback"]["stream_cursors"]
    )
    assert (
        cursor_payload["trigger-pr-feedback"]["stream_cursors"]["azure_devops:org/repo:200"]
        == "2026-05-13T09:00:00Z"
    )


def test_publish_directory_pr_feedback_events_skips_ignored_feedback_ids(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evander.daemons import ado_issue_worker as ado_worker

    (worker_config.runtime_dir / "sessions.json").write_text(
        json.dumps(
            {
                "session-6070": {
                    "session_id": "session-6070",
                    "directory_path": "root/team-a",
                    "allowed_tools": [],
                    "expires_at": "2026-04-09T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )
    (worker_config.runtime_dir / "ignored-feedback-ids.json").write_text(
        json.dumps(
            {
                "feedback_ids": [
                    {
                        "feedback_id": "azure_devops:thread_comment:30830:2",
                        "ignored_at": "2099-01-01T00:00:00Z",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    pr_map_path = worker_config.runtime_dir / "pr-map.json"
    pr_map_path.write_text(
        json.dumps(
            {
                "entries": {
                    "azure_devops:org/repo:6070": {
                        "provider": "azure_devops",
                        "repo": "org/repo",
                        "pr_id": "6070",
                        "session_id": "session-6070",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    runtime_gateway_config = ado_worker.GatewayConfig.model_validate(
        {
            "runtime_dir": str(worker_config.runtime_dir),
            "session_store_path": str(worker_config.runtime_dir / "sessions.json"),
            "approval_store_path": str(worker_config.runtime_dir / "approvals.json"),
            "draft_store_path": str(worker_config.runtime_dir / "drafts.json"),
            "email_store_path": str(worker_config.runtime_dir / "emails.json"),
            "discord_command_store_path": str(worker_config.runtime_dir / "discord_commands.json"),
            "audit_log_path": str(worker_config.runtime_dir / "audit.jsonl"),
            "pr_map_path": str(pr_map_path),
            "azure_devops_pr_pat_env": "AZURE_DEVOPS_PR_PAT",
        }
    )
    trigger = PullRequestNewFeedbackTriggerDefinition.model_validate(
        {
            "id": "trigger-pr-feedback",
            "display_name": "PR feedback",
            "type": "trigger:pull-request:new-feedback",
            "provider": "ado",
            "organization_url": "https://dev.azure.com/example-org",
            "project": "Example Project",
        }
    )

    monkeypatch.setattr(ado_worker, "_load_ado_pr_feedback_triggers", lambda _config: [trigger])
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "secret-pat")
    feedback_created_at = datetime.now(UTC).replace(microsecond=0) - timedelta(days=1)
    feedback_cursor = feedback_created_at.isoformat().replace("+00:00", "Z")

    def fake_poll(self, *, client, repo, pr_id, cursor):
        return SimpleNamespace(
            feedback=[
                FeedbackEnvelope(
                    feedback_id="azure_devops:thread_comment:30830:2",
                    provider="azure_devops",
                    repo="org/repo",
                    pr_id="6070",
                    thread_id="30830",
                    comment_id="2",
                    author="Truman Bot",
                    body="Bot reply",
                    created_at=feedback_cursor,
                )
            ],
            next_cursor=feedback_cursor,
        )

    monkeypatch.setattr(ado_worker.AzureDevOpsFeedbackAdapter, "poll", fake_poll)

    with httpx.Client() as client:
        published = _publish_directory_pr_feedback_events(
            client=client,
            config=WorkerConfig(**{**worker_config.__dict__, "directory_path": "root/team-a"}),
            runtime_gateway_config=runtime_gateway_config,
            directory_path="root/team-a",
        )

    assert published == 0
    queue = SqliteTriggerEventQueue(worker_config.runtime_dir / "orchestration.db")
    assert queue.list_pending(trigger_id="trigger-pr-feedback") == []
    feedback_store_payload = json.loads(
        (worker_config.runtime_dir / "pr-feedback-store.json").read_text(encoding="utf-8")
    )
    assert "azure_devops:thread_comment:30830:2" in feedback_store_payload["feedback"]
    service = PrFeedbackService(
        worker_config.runtime_dir / "pr-feedback-store.json",
        ignored_feedback_store_path=worker_config.runtime_dir / "ignored-feedback-ids.json",
    )
    unread = service.list_unread_feedback(
        streams=[{"provider": "azure_devops", "repo": "org/repo", "pr_id": "6070"}],
        session_id="session-6070",
    )
    assert unread.feedback == []


def test_publish_directory_pr_feedback_events_uses_configured_orchestration_store_path(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evander.daemons import ado_issue_worker as ado_worker

    queue_path = worker_config.runtime_dir / "custom-orchestration.db"
    trigger_worker_config = canonical_worker_config(
        canonical_workflow_payload(
            {
                "runtime_dir": str(worker_config.runtime_dir),
                "orchestration_store_path": str(queue_path),
                "triggers": [
                    {
                        "id": "trigger-pr-feedback",
                        "display_name": "PR feedback",
                        "type": "trigger:pull-request:new-feedback",
                        "provider": "ado",
                        "organization_url": "https://dev.azure.com/example-org",
                        "project": "Example Project",
                    }
                ],
            }
        )
    )
    (worker_config.runtime_dir / "sessions.json").write_text(
        json.dumps(
            {
                "session-6070": {
                    "session_id": "session-6070",
                    "directory_path": "root/team-a",
                    "allowed_tools": [],
                    "expires_at": "2026-04-09T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )
    pr_map_path = worker_config.runtime_dir / "pr-map.json"
    pr_map_path.write_text(
        json.dumps(
            {
                "entries": {
                    "azure_devops:org/repo:6070": {
                        "provider": "azure_devops",
                        "repo": "org/repo",
                        "pr_id": "6070",
                        "session_id": "session-6070",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    runtime_gateway_config = ado_worker.GatewayConfig.model_validate(
        {
            "runtime_dir": str(worker_config.runtime_dir),
            "session_store_path": str(worker_config.runtime_dir / "sessions.json"),
            "approval_store_path": str(worker_config.runtime_dir / "approvals.json"),
            "draft_store_path": str(worker_config.runtime_dir / "drafts.json"),
            "email_store_path": str(worker_config.runtime_dir / "emails.json"),
            "discord_command_store_path": str(worker_config.runtime_dir / "discord_commands.json"),
            "audit_log_path": str(worker_config.runtime_dir / "audit.jsonl"),
            "pr_map_path": str(pr_map_path),
            "azure_devops_pr_pat_env": "AZURE_DEVOPS_PR_PAT",
        }
    )
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "secret-pat")
    monkeypatch.setattr(
        ado_worker,
        "_load_trigger_orchestrator_worker_config",
        lambda _config: trigger_worker_config,
    )

    def fake_poll(self, *, client, repo, pr_id, cursor):
        return SimpleNamespace(
            feedback=[
                FeedbackEnvelope(
                    feedback_id="azure_devops:thread_comment:30830:1",
                    provider="azure_devops",
                    repo="org/repo",
                    pr_id="6070",
                    thread_id="30830",
                    comment_id="1",
                    author="jonathan",
                    body="Please fix this",
                    created_at="2026-04-08T05:33:41.433000Z",
                )
            ],
            next_cursor="2026-04-08T05:33:41.433000Z",
        )

    monkeypatch.setattr(ado_worker.AzureDevOpsFeedbackAdapter, "poll", fake_poll)

    with httpx.Client() as client:
        published = _publish_directory_pr_feedback_events(
            client=client,
            config=WorkerConfig(**{**worker_config.__dict__, "directory_path": "root/team-a"}),
            runtime_gateway_config=runtime_gateway_config,
            directory_path="root/team-a",
        )

    assert published == 1
    queue = SqliteTriggerEventQueue(queue_path)
    events = queue.list_pending(trigger_id="trigger-pr-feedback")
    assert len(events) == 1


def test_load_ado_pr_feedback_triggers_resolves_workflow_packs(worker_config: WorkerConfig) -> None:
    from evander.daemons import ado_issue_worker as ado_worker

    project_dir = worker_config.runtime_dir
    (project_dir / "config" / "trigger-orchestrator").mkdir(parents=True)
    (project_dir / "config" / "workflow-packs").mkdir(parents=True)
    (project_dir / "config" / "trigger-orchestrator" / "config.json").write_text(
        json.dumps(
            {
                "runtime_dir": str(worker_config.runtime_dir),
                "workflow_packs": ["pr-feedback-only.v1.json"],
            }
        ),
        encoding="utf-8",
    )
    (project_dir / "config" / "workflow-packs" / "pr-feedback-only.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "pr-feedback-only",
                "pack_version": "v1",
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "trigger-pr-feedback",
                            "display_name": "PR feedback",
                            "type": "trigger:pull-request:new-feedback",
                            "provider": "ado",
                            "organization_url": "https://dev.azure.com/example-org",
                            "project": "Example Project",
                        },
                        {
                            "id": "trigger-pr-feedback-disabled",
                            "display_name": "Disabled PR feedback",
                            "type": "trigger:pull-request:new-feedback",
                            "provider": "ado",
                            "organization_url": "https://dev.azure.com/example-org",
                            "project": "Example Project",
                            "enabled": False,
                        },
                    ],
                    "edges": [],
                },
            }
        ),
        encoding="utf-8",
    )
    _canonicalize_workflow_pack(
        project_dir / "config" / "workflow-packs" / "pr-feedback-only.v1.json"
    )

    triggers = ado_worker._load_ado_pr_feedback_triggers(worker_config)

    assert [trigger.id for trigger in triggers] == ["trigger-pr-feedback"]
    assert triggers[0].provider == "ado"


def test_load_ado_pr_completed_triggers_resolves_workflow_packs(
    worker_config: WorkerConfig,
) -> None:
    from evander.daemons import ado_issue_worker as ado_worker

    project_dir = worker_config.runtime_dir
    (project_dir / "config" / "trigger-orchestrator").mkdir(parents=True)
    (project_dir / "config" / "workflow-packs").mkdir(parents=True)
    (project_dir / "config" / "trigger-orchestrator" / "config.json").write_text(
        json.dumps(
            {
                "runtime_dir": str(worker_config.runtime_dir),
                "workflow_packs": ["pr-completed-only.v1.json"],
            }
        ),
        encoding="utf-8",
    )
    (project_dir / "config" / "workflow-packs" / "pr-completed-only.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "pr-completed-only",
                "pack_version": "v1",
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "trigger-pr-completed",
                            "display_name": "PR completed",
                            "type": "trigger:pull-request:completed",
                            "provider": "ado",
                            "organization_url": "https://dev.azure.com/example-org",
                            "project": "Example Project",
                        },
                        {
                            "id": "trigger-pr-completed-disabled",
                            "display_name": "Disabled PR completed",
                            "type": "trigger:pull-request:completed",
                            "provider": "ado",
                            "organization_url": "https://dev.azure.com/example-org",
                            "project": "Example Project",
                            "enabled": False,
                        },
                    ],
                    "edges": [],
                },
            }
        ),
        encoding="utf-8",
    )
    _canonicalize_workflow_pack(
        project_dir / "config" / "workflow-packs" / "pr-completed-only.v1.json"
    )

    triggers = ado_worker._load_ado_pr_completed_triggers(worker_config)

    assert [trigger.id for trigger in triggers] == ["trigger-pr-completed"]
    assert triggers[0].provider == "ado"


def test_pr_feedback_worker_rejects_an_arbitrary_trigger_organization() -> None:
    trigger = PullRequestNewFeedbackTriggerDefinition.model_validate(
        {
            "id": "trigger-pr-feedback-untrusted-org",
            "display_name": "PR feedback",
            "type": "trigger:pull-request:new-feedback",
            "provider": "ado",
            "organization_url": "https://attacker.example/organization",
        }
    )

    with pytest.raises(ValueError, match=r"dev\.azure\.com or \*\.visualstudio\.com"):
        _ado_organization_url_for_trigger(
            trigger=trigger,
            legacy_organization_url="https://dev.azure.com/directory-org",
        )
