from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from agent_sandbox.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from agent_sandbox.models import DiscordMessageEnvelope, EmailMessage, LocalIssueRecord, PrFeedbackItem
from agent_sandbox.trigger_orchestrator import TriggerOrchestratorWorker
from agent_sandbox.events.models import TriggerDefinitionDiscriminated
from agent_sandbox.triggers.providers.base import TriggerRuntimeContext
from agent_sandbox.triggers.providers.cron import CronTriggerProvider
from agent_sandbox.triggers.providers.discord_new_message import DiscordNewMessageTriggerProvider
from agent_sandbox.triggers.providers.email_new_message import EmailNewMessageTriggerProvider
from agent_sandbox.triggers.providers.git_push import GitPushTriggerProvider
from agent_sandbox.triggers.providers.pull_request_new_feedback import PullRequestNewFeedbackTriggerProvider
from agent_sandbox.triggers.providers.ticket_changed import TicketChangedTriggerProvider
from agent_sandbox.triggers.providers.ticket_created import TicketCreatedTriggerProvider
from agent_sandbox.triggers.queue import TriggerEventQueue


REPORT_ENV_VAR = "AGENT_SANDBOX_TRIGGER_CONFORMANCE_REPORT"


@pytest.fixture(scope="module")
def report_collector() -> dict[str, Any]:
    return {"requirements": []}


def _record_result(
    report: dict[str, Any],
    *,
    requirement_id: str,
    trigger_type: str,
    status: str,
    coverage: list[str],
    details: str,
    remediation: str = "",
) -> None:
    report["requirements"].append(
        {
            "requirement_id": requirement_id,
            "trigger_type": trigger_type,
            "status": status,
            "coverage": coverage,
            "details": details,
            "remediation": remediation,
        }
    )


def _build_runtime_context(runtime_dir: Path, *, env: dict[str, str] | None = None) -> TriggerRuntimeContext:
    return TriggerRuntimeContext(resolved_config=GatewayConfig(runtime_dir=runtime_dir), env=env or {})


def _trigger_adapter() -> TypeAdapter[TriggerDefinitionDiscriminated]:
    return TypeAdapter(TriggerDefinitionDiscriminated)


@pytest.mark.parametrize(
    ("payload", "expected_type", "required_field"),
    [
        (
            {"id": "cron-1", "display_name": "Cron", "type": "trigger:cron", "expression": "*/5 * * * *"},
            "trigger:cron",
            "expression",
        ),
        (
            {
                "id": "ticket-created",
                "display_name": "Ticket created",
                "type": "trigger:ticket:created",
                "directories": ["root/team-a"],
            },
            "trigger:ticket:created",
            "directories",
        ),
        (
            {
                "id": "ticket-changed",
                "display_name": "Ticket changed",
                "type": "trigger:ticket:changed",
                "directories": ["root/team-a"],
            },
            "trigger:ticket:changed",
            "directories",
        ),
        (
            {
                "id": "discord",
                "display_name": "Discord",
                "type": "trigger:discord:new-message",
                "discord_bot_token_env": "DISCORD_BOT_TOKEN",
                "channel_ids": ["chan-a"],
                "user_ids": ["user-a"],
            },
            "trigger:discord:new-message",
            "discord_bot_token_env",
        ),
        (
            {
                "id": "email",
                "display_name": "Email",
                "type": "trigger:email:new-message",
                "provider": "json_store",
                "account_identifier": "ops@example.com",
                "mailbox_ids": ["default"],
                "auth_secret_refs": {"access_token": "EMAIL_TOKEN"},
            },
            "trigger:email:new-message",
            "provider",
        ),
        (
            {
                "id": "pr",
                "display_name": "PR",
                "type": "trigger:pull-request:new-feedback",
                "provider": "ado",
                "organization_url": "https://dev.azure.com/example",
                "project": "Sandbox",
            },
            "trigger:pull-request:new-feedback",
            "provider",
        ),
        (
            {
                "id": "git",
                "display_name": "Git",
                "type": "trigger:git:push",
                "path_to_repo": "/tmp/repo.git",
            },
            "trigger:git:push",
            "path_to_repo",
        ),
    ],
)
def test_conformance_schema_accepts_required_trigger_shapes(
    payload: dict[str, Any],
    expected_type: str,
    required_field: str,
    report_collector: dict[str, Any],
) -> None:
    trigger = _trigger_adapter().validate_python(payload)

    assert trigger.type == expected_type
    assert getattr(trigger, required_field) is not None
    _record_result(
        report_collector,
        requirement_id=f"schema:{expected_type}",
        trigger_type=expected_type,
        status="pass",
        coverage=["configuration/schema conformance"],
        details=f"Accepted valid config and required field `{required_field}`.",
    )


@pytest.mark.parametrize(
    ("payload", "error_match", "trigger_type"),
    [
        (
            {"id": "cron-bad", "display_name": "Cron", "type": "trigger:cron"},
            "expression",
            "trigger:cron",
        ),
        (
            {
                "id": "discord-bad",
                "display_name": "Discord",
                "type": "trigger:discord:new-message",
            },
            "discord_bot_token_env",
            "trigger:discord:new-message",
        ),
        (
            {
                "id": "email-bad",
                "display_name": "Email",
                "type": "trigger:email:new-message",
                "provider": "gmail",
                "mailbox_ids": ["default"],
            },
            "account_identifier",
            "trigger:email:new-message",
        ),
        (
            {
                "id": "pr-bad",
                "display_name": "PR",
                "type": "trigger:pull-request:new-feedback",
                "provider": "ado",
            },
            "organization_url is required",
            "trigger:pull-request:new-feedback",
        ),
        (
            {
                "id": "git-bad",
                "display_name": "Git",
                "type": "trigger:git:push",
            },
            "path_to_repo",
            "trigger:git:push",
        ),
    ],
)
def test_conformance_schema_rejects_invalid_configs_with_explicit_errors(
    payload: dict[str, Any],
    error_match: str,
    trigger_type: str,
    report_collector: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match=error_match):
        _trigger_adapter().validate_python(payload)

    _record_result(
        report_collector,
        requirement_id=f"schema:invalid:{trigger_type}",
        trigger_type=trigger_type,
        status="pass",
        coverage=["configuration/schema conformance"],
        details=f"Invalid config emits explicit error containing `{error_match}`.",
    )


def test_conformance_schema_rejects_duplicate_trigger_ids(report_collector: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="Node IDs must be globally unique across triggers, gates, effects, and queries"):
        TriggerOrchestratorWorkerGlobalConfig.model_validate(
            {
                "runtime_dir": "runtime",
                "triggers": [
                    {
                        "id": "dup-id",
                        "display_name": "Cron A",
                        "type": "trigger:cron",
                        "expression": "*/5 * * * *",
                    },
                    {
                        "id": "dup-id",
                        "display_name": "Cron B",
                        "type": "trigger:cron",
                        "expression": "*/15 * * * *",
                    },
                ],
            }
        )

    _record_result(
        report_collector,
        requirement_id="schema:duplicate-ids",
        trigger_type="all",
        status="pass",
        coverage=["configuration/schema conformance"],
        details="Duplicate trigger IDs are rejected.",
    )


def test_conformance_provider_payload_contracts_and_secret_redaction(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    report_collector: dict[str, Any],
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True)

    now = datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc)
    issue_store = runtime_dir / "local-issues.json"
    issue_store.write_text(
        json.dumps(
            {
                "i-1": LocalIssueRecord(
                    issue_id="i-1",
                    issue_number="1",
                    title="Issue one",
                    body="Body",
                    yaml_path="root/team-a/issue-1.yaml",
                    directory_path="root/team-a",
                    created_at=now - timedelta(minutes=2),
                    updated_at=now - timedelta(minutes=1),
                ).model_dump(mode="json")
            }
        ),
        encoding="utf-8",
    )

    inbox_path = runtime_dir / "discord-inbox-store.json"
    inbox_path.write_text(
        json.dumps(
            {
                "messages": {
                    "d-1": DiscordMessageEnvelope(
                        message_id="d-1",
                        channel_id="chan-a",
                        author_id="user-a",
                        author_name="Alice",
                        content="hello",
                        created_at=now,
                    ).model_dump(mode="json")
                }
            }
        ),
        encoding="utf-8",
    )

    email_path = runtime_dir / "emails.json"
    email_path.write_text(
        json.dumps(
            {
                "m-1": EmailMessage(
                    message_id="m-1",
                    mailbox_id="default",
                    provider="json_store",
                    folder="inbox",
                    sender="ops@example.com",
                    recipients=["dev@example.com"],
                    subject="Subject",
                    body_text="Body",
                    unread=True,
                    received_at=now,
                ).model_dump(mode="json")
            }
        ),
        encoding="utf-8",
    )

    pr_feedback_path = runtime_dir / "pr-feedback-store.json"
    pr_feedback_path.write_text(
        json.dumps(
            {
                "feedback": {
                    "f-1": PrFeedbackItem(
                        feedback_id="f-1",
                        provider="local",
                        repo="org/repo",
                        pr_id="12",
                        author="reviewer",
                        body="Needs changes",
                        created_at=now,
                    ).model_dump(mode="json")
                }
            }
        ),
        encoding="utf-8",
    )

    config = GatewayConfig(runtime_dir=runtime_dir, email_store_path=email_path)

    ticket_created = TicketCreatedTriggerProvider()
    ticket_created.initialize(
        _trigger_adapter().validate_python(
            {
                "id": "ticket-created",
                "display_name": "Ticket created",
                "type": "trigger:ticket:created",
                "directories": ["root/team-a"],
            }
        ),
        _build_runtime_context(runtime_dir),
    )
    ticket_changed = TicketChangedTriggerProvider()
    ticket_changed.initialize(
        _trigger_adapter().validate_python(
            {
                "id": "ticket-changed",
                "display_name": "Ticket changed",
                "type": "trigger:ticket:changed",
                "directories": ["root/team-a"],
            }
        ),
        _build_runtime_context(runtime_dir),
    )
    discord = DiscordNewMessageTriggerProvider()
    discord.initialize(
        _trigger_adapter().validate_python(
            {
                "id": "discord",
                "display_name": "Discord",
                "type": "trigger:discord:new-message",
                "discord_bot_token_env": "DISCORD_BOT_TOKEN",
                "channel_ids": ["chan-a"],
                "user_ids": ["user-a"],
            }
        ),
        _build_runtime_context(runtime_dir),
    )
    email = EmailNewMessageTriggerProvider()
    email.initialize(
        _trigger_adapter().validate_python(
            {
                "id": "email",
                "display_name": "Email",
                "type": "trigger:email:new-message",
                "provider": "json_store",
                "account_identifier": "ops@example.com",
                "mailbox_ids": ["default"],
                "auth_secret_refs": {"access_token": "EMAIL_TOKEN"},
            }
        ),
        TriggerRuntimeContext(
            resolved_config=config,
            env={"EMAIL_TOKEN": "super-secret-value"},
        ),
    )
    pr = PullRequestNewFeedbackTriggerProvider()
    pr.initialize(
        _trigger_adapter().validate_python(
            {
                "id": "pr",
                "display_name": "PR feedback",
                "type": "trigger:pull-request:new-feedback",
                "provider": "local",
            }
        ),
        _build_runtime_context(runtime_dir),
    )

    caplog.set_level(logging.DEBUG)
    ticket_created_events = ticket_created.poll()
    ticket_changed_events = ticket_changed.poll()
    discord_events = discord.poll()
    email_events = email.poll()
    pr_events = pr.poll()

    assert list(ticket_created_events[0].payload.keys()) == ["ticket"]
    assert list(ticket_changed_events[0].payload.keys()) == ["ticket"]
    assert list(discord_events[0].payload.keys()) == ["message"]
    assert list(email_events[0].payload.keys()) == ["message"]
    assert set(pr_events[0].payload.keys()) == {"pr", "feedback"}

    for line in caplog.messages:
        assert "super-secret-value" not in line

    _record_result(
        report_collector,
        requirement_id="event-contract:provider-payload-shape",
        trigger_type="all",
        status="pass",
        coverage=["event/output contract verification", "provider behavior correctness"],
        details="Trigger payload keys match contract and no secret values leak to logs.",
    )


def test_conformance_cron_due_and_cursor_persistence(tmp_path: Path, report_collector: dict[str, Any]) -> None:
    runtime_dir = tmp_path / "runtime"
    provider = CronTriggerProvider()
    provider.initialize(
        _trigger_adapter().validate_python(
            {
                "id": "cron-every-minute",
                "display_name": "Cron",
                "type": "trigger:cron",
                "expression": "* * * * *",
            }
        ),
        _build_runtime_context(runtime_dir),
    )

    first_events = provider.poll()
    second_events = provider.poll()
    cursor_payload = json.loads((runtime_dir / "trigger-cursors-cron.json").read_text(encoding="utf-8"))

    assert first_events
    assert second_events == []
    assert "last_fired_at" in cursor_payload["cron-every-minute"]
    _record_result(
        report_collector,
        requirement_id="provider:cron:cursor",
        trigger_type="trigger:cron",
        status="pass",
        coverage=["provider behavior correctness"],
        details="Cron emits when due and persists last_fired_at cursor.",
    )


def test_conformance_directory_and_filter_scoping(tmp_path: Path, report_collector: dict[str, Any]) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True)
    now = datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc)

    (runtime_dir / "local-issues.json").write_text(
        json.dumps(
            {
                "i-a": LocalIssueRecord(
                    issue_id="i-a",
                    issue_number="1",
                    title="A",
                    body="A",
                    yaml_path="root/team-a/a.yaml",
                    directory_path="root/team-a",
                    created_at=now,
                    updated_at=now,
                ).model_dump(mode="json"),
                "i-b": LocalIssueRecord(
                    issue_id="i-b",
                    issue_number="2",
                    title="B",
                    body="B",
                    yaml_path="root/team-b/b.yaml",
                    directory_path="root/team-b",
                    created_at=now,
                    updated_at=now,
                ).model_dump(mode="json"),
            }
        ),
        encoding="utf-8",
    )

    (runtime_dir / "discord-inbox-store.json").write_text(
        json.dumps(
            {
                "messages": {
                    "a": DiscordMessageEnvelope(
                        message_id="a",
                        channel_id="chan-a",
                        author_id="user-a",
                        author_name="A",
                        content="a",
                        created_at=now,
                    ).model_dump(mode="json"),
                    "b": DiscordMessageEnvelope(
                        message_id="b",
                        channel_id="chan-b",
                        author_id="user-b",
                        author_name="B",
                        content="b",
                        created_at=now + timedelta(seconds=1),
                    ).model_dump(mode="json"),
                }
            }
        ),
        encoding="utf-8",
    )

    ticket_provider = TicketCreatedTriggerProvider()
    ticket_provider.initialize(
        _trigger_adapter().validate_python(
            {
                "id": "ticket-filtered",
                "display_name": "Ticket",
                "type": "trigger:ticket:created",
                "directories": ["root/team-a"],
            }
        ),
        _build_runtime_context(runtime_dir),
    )
    discord_provider = DiscordNewMessageTriggerProvider()
    discord_provider.initialize(
        _trigger_adapter().validate_python(
            {
                "id": "discord-filtered",
                "display_name": "Discord",
                "type": "trigger:discord:new-message",
                "discord_bot_token_env": "DISCORD_BOT_TOKEN",
                "channel_ids": ["chan-a"],
                "user_ids": ["user-a"],
            }
        ),
        _build_runtime_context(runtime_dir),
    )

    ticket_events = ticket_provider.poll()
    discord_events = discord_provider.poll()

    assert len(ticket_events) == 1
    assert ticket_events[0].payload["ticket"]["directory_path"] == "root/team-a"
    assert len(discord_events) == 1
    assert discord_events[0].payload["message"]["channel_id"] == "chan-a"

    _record_result(
        report_collector,
        requirement_id="provider:directory-and-filter-scoping",
        trigger_type="trigger:ticket:created,trigger:discord:new-message",
        status="pass",
        coverage=["provider behavior correctness"],
        details="Ticket directory scope and Discord channel/user filters are enforced.",
    )


def test_conformance_pr_provider_modes_and_git_push_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    report_collector: dict[str, Any],
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True)

    pr_provider = PullRequestNewFeedbackTriggerProvider()
    pr_provider.initialize(
        _trigger_adapter().validate_python(
            {
                "id": "pr-local",
                "display_name": "PR Local",
                "type": "trigger:pull-request:new-feedback",
                "provider": "local",
            }
        ),
        _build_runtime_context(runtime_dir),
    )

    (runtime_dir / "pr-feedback-store.json").write_text(
        json.dumps(
            {
                "feedback": {
                    "fb-1": PrFeedbackItem(
                        feedback_id="fb-1",
                        provider="local",
                        repo="org/repo",
                        pr_id="10",
                        author="dev",
                        body="nit",
                        created_at=datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc),
                    ).model_dump(mode="json")
                }
            }
        ),
        encoding="utf-8",
    )

    events = pr_provider.poll()
    assert len(events) == 1
    assert set(events[0].payload.keys()) == {"pr", "feedback"}

    repo = tmp_path / "repo.git"
    repo.mkdir(parents=True)
    git_provider = GitPushTriggerProvider()
    git_provider.initialize(
        _trigger_adapter().validate_python(
            {
                "id": "git-trigger",
                "display_name": "Git",
                "type": "trigger:git:push",
                "path_to_repo": str(repo),
            }
        ),
        _build_runtime_context(runtime_dir),
    )

    git_event = git_provider.handle_ref_update(
        repo_path=str(repo),
        old_rev="0" * 40,
        new_rev="a" * 40,
        ref_name="refs/heads/main",
    )
    assert git_event is not None
    assert git_event.payload["branch_name"] == "main"
    assert git_event.payload["commit_hash"] == "a" * 40

    # Non-local provider mode should use adapter path; patch adapter/streams to avoid network.
    pr_remote = PullRequestNewFeedbackTriggerProvider()
    pr_remote.initialize(
        _trigger_adapter().validate_python(
            {
                "id": "pr-gh",
                "display_name": "PR GitHub",
                "type": "trigger:pull-request:new-feedback",
                "provider": "github",
            }
        ),
        _build_runtime_context(runtime_dir),
    )

    class _PollResult:
        def __init__(self) -> None:
            self.feedback = []
            self.next_cursor = "cursor-1"

    class _Adapter:
        def poll(self, **_kwargs):
            return _PollResult()

    monkeypatch.setattr(pr_remote, "_build_adapter", lambda _provider: _Adapter())
    monkeypatch.setattr(
        pr_remote,
        "_load_streams",
        lambda _provider: [{"provider": "github", "repo": "org/repo", "pr_id": "11"}],
    )
    assert pr_remote.poll() == []

    _record_result(
        report_collector,
        requirement_id="provider:pr-modes-and-git-push",
        trigger_type="trigger:pull-request:new-feedback,trigger:git:push",
        status="pass",
        coverage=["provider behavior correctness", "event/output contract verification"],
        details="PR feedback trigger exercises local and github modes; git push emits branch/hash contract.",
    )


def test_conformance_orchestrator_integration_locking_jitter_and_idempotency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    report_collector: dict[str, Any],
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 2,
            "jitter_seconds": 3,
            "triggers": [
                {
                    "id": "cron-1",
                    "display_name": "Cron",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
        }
    )
    worker = TriggerOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]

    frozen_event_time = datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc)
    event_payload = {
        "event_id": "cron-1:2026-04-03T12:00:00+00:00",
        "trigger_id": "cron-1",
        "trigger_type": "trigger:cron",
        "occurred_at": frozen_event_time,
        "payload": {"scheduled_for": "2026-04-03T12:00:00Z"},
    }

    class _Provider:
        def poll(self):
            from agent_sandbox.events.models import TriggerEvent

            return [TriggerEvent.model_validate(event_payload)]

    worker.provider_factory.build = lambda _trigger: _Provider()  # type: ignore[assignment]

    first = worker.run_once()
    second = worker.run_once()

    assert first.events_published == 1
    assert second.events_published == 0

    sleep_calls: list[float] = []

    def _fake_sleep(value: float) -> None:
        sleep_calls.append(value)
        raise RuntimeError("stop-loop")

    monkeypatch.setattr("agent_sandbox.trigger_orchestrator.time.sleep", _fake_sleep)
    monkeypatch.setattr("agent_sandbox.trigger_orchestrator.random.uniform", lambda _a, _b: 1.25)

    with pytest.raises(RuntimeError, match="stop-loop"):
        worker.run_forever()

    assert sleep_calls
    assert abs(sleep_calls[0] - 3.25) < 1e-6

    queue = TriggerEventQueue(worker_config.event_queue_store_path)
    assert len(queue.list_pending()) == 1

    _record_result(
        report_collector,
        requirement_id="orchestrator:integration-idempotency-loop",
        trigger_type="all",
        status="pass",
        coverage=["orchestrator integration checks"],
        details="Worker loads trigger providers, dedupes duplicate events, and applies poll+jitter sleep.",
    )


def test_conformance_traceability_matrix_and_report(
    report_collector: dict[str, Any],
    tmp_path: Path,
) -> None:
    matrix = {
        "task_trigger_schema": [
            "schema:trigger:cron",
            "schema:trigger:ticket:created",
            "schema:trigger:ticket:changed",
            "schema:trigger:discord:new-message",
            "schema:trigger:email:new-message",
            "schema:trigger:pull-request:new-feedback",
            "schema:trigger:git:push",
            "schema:duplicate-ids",
        ],
        "task_trigger_event_contract": [
            "event-contract:provider-payload-shape",
            "provider:pr-modes-and-git-push",
        ],
        "task_provider_behavior": [
            "provider:cron:cursor",
            "provider:directory-and-filter-scoping",
            "provider:pr-modes-and-git-push",
        ],
        "task_orchestrator_integration": ["orchestrator:integration-idempotency-loop"],
    }

    seen = {entry["requirement_id"] for entry in report_collector["requirements"]}
    missing: dict[str, list[str]] = {}
    for task_name, required_requirements in matrix.items():
        uncovered = [requirement_id for requirement_id in required_requirements if requirement_id not in seen]
        if uncovered:
            missing[task_name] = uncovered

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "pass" if not missing else "fail",
        "requirements": sorted(report_collector["requirements"], key=lambda item: item["requirement_id"]),
        "traceability_matrix": matrix,
        "missing_coverage": missing,
    }

    report_path = Path(os.environ.get(REPORT_ENV_VAR, "runtime/trigger-conformance-report.json"))
    if not report_path.is_absolute():
        report_path = Path.cwd() / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    local_copy = tmp_path / "trigger-conformance-report.json"
    local_copy.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    assert not missing, f"Traceability matrix has missing coverage: {missing}"
    assert summary["status"] == "pass"
    assert report_path.exists()
