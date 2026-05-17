from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from agent_sandbox.daemons.ado_issue_worker import (
    ProcessedWorkItemStore,
    WorkerConfig,
    _build_setup_command,
    _is_vm_already_exists_output,
    _publish_directory_pr_completion_events,
    _publish_directory_pr_feedback_events,
    fetch_work_item_batch,
    fetch_work_item_comments,
    filter_unprocessed_work_items,
    format_work_item_listing,
    gate_and_order_items,
    list_unprocessed_work_items,
    poll_once,
    process_work_item,
    run_worker,
    run_worker_command,
    update_work_item_state,
)
from agent_sandbox.events.provider_models import (
    PullRequestCompletedTriggerDefinition,
    PullRequestNewFeedbackTriggerDefinition,
)
from agent_sandbox.models import GetPullRequestResponse, LocalIssueRecord
from agent_sandbox.pr_feedback import FeedbackEnvelope
from agent_sandbox.services import PrFeedbackService
from agent_sandbox.storage import JsonStore
from agent_sandbox.triggers.queue import TriggerEventQueue
from agent_sandbox.worker_directory_context import ShadowDomainContext, WorkerDirectoryContext


@pytest.fixture
def worker_config(tmp_path: Path) -> WorkerConfig:
    gateway_config_path = tmp_path / "gateway.yaml"
    gateway_config_path.write_text(
        json.dumps(
            {
                "runtime_dir": str(tmp_path),
                "session_store_path": str(tmp_path / "sessions.json"),
                "approval_store_path": str(tmp_path / "approvals.json"),
                "draft_store_path": str(tmp_path / "drafts.json"),
                "email_store_path": str(tmp_path / "emails.json"),
                "discord_command_store_path": str(tmp_path / "discord_commands.json"),
                "audit_log_path": str(tmp_path / "audit.jsonl"),
            }
        ),
        encoding="utf-8",
    )
    return WorkerConfig(
        organization_url="https://dev.azure.com/example",
        project="example",
        pat_env_var_name="ADO_WORK_ITEMS_AZURE_DEVOPS_PAT",
        poll_interval_seconds=30.0,
        area_filters=(),
        team_filters=(),
        runtime_dir=tmp_path,
        yaml_output_dir=tmp_path / "yaml",
        setup_log_dir=tmp_path / "setup-logs",
        processed_store_path=tmp_path / "state" / "processed.json",
        setup_script_path=Path("scripts/setup_vm.sh"),
        prewarm_target="My.slnx",
        repo_path=None,
        setup_vms=True,
        session_id=None,
        vm_user=None,
        setup_timeout_seconds=30.0,
        setup_concurrency_limit=1,
        setup_lock_path=tmp_path / "setup.lock",
        fetch_retry_count=2,
        fetch_retry_backoff_seconds=0.1,
        bootstrap_dotnet=True,
        bootstrap_nuget=True,
        bootstrap_mssql=True,
        mssql_sa_password="SecretPassw0rd!",
        mssql_init_root=None,
        bootstrap_playwright=True,
        bootstrap_frontend_deps=False,
        frontend_host_checkout=None,
        frontend_install_roots=(),
        include_comments=True,
        comments_max_count=20,
        comment_text_max_chars=4000,
        gateway_config_path=gateway_config_path,
        session_ttl_hours=8,
    )


def test_processed_store_mark_and_forget(worker_config: WorkerConfig) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)
    assert store.is_processed(123) is False

    store.mark_processed(
        work_item_id=123,
        yaml_path=worker_config.yaml_output_dir / "work-item-123.yaml",
        script_exit_code=0,
        output_snippet="ok",
        provider="azure_devops",
        organization=worker_config.organization_url,
        project=worker_config.project,
    )
    assert (
        store.is_processed(
            123,
            provider="azure_devops",
            organization=worker_config.organization_url,
            project=worker_config.project,
        )
        is True
    )

    payload = json.loads(worker_config.processed_store_path.read_text(encoding="utf-8"))
    expected_key = "root::azure_devops::https://dev.azure.com/example::example::123"
    assert payload[expected_key]["script_exit_code"] == 0
    assert payload[expected_key]["script_output_snippet"] == "ok"

    assert (
        store.forget(
            123,
            provider="azure_devops",
            organization=worker_config.organization_url,
            project=worker_config.project,
        )
        is True
    )
    assert store.is_processed(123) is False


def test_processed_store_tracks_directory_scoped_entries_separately(
    worker_config: WorkerConfig,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)

    store.mark_processed(
        work_item_id=777,
        yaml_path=worker_config.yaml_output_dir / "work-item-777.yaml",
        script_exit_code=0,
        output_snippet="root",
        directory_path="root",
        provider="azure_devops",
        organization=worker_config.organization_url,
        project=worker_config.project,
    )
    store.mark_processed(
        work_item_id=777,
        yaml_path=worker_config.yaml_output_dir / "team-a" / "work-item-777.yaml",
        script_exit_code=0,
        output_snippet="team-a",
        directory_path="root/team-a",
        provider="azure_devops",
        organization=worker_config.organization_url,
        project=worker_config.project,
    )

    payload = json.loads(worker_config.processed_store_path.read_text(encoding="utf-8"))
    root_key = "root::azure_devops::https://dev.azure.com/example::example::777"
    team_key = "root/team-a::azure_devops::https://dev.azure.com/example::example::777"
    assert payload[root_key]["script_output_snippet"] == "root"
    assert payload[team_key]["script_output_snippet"] == "team-a"
    assert (
        store.is_processed(
            777,
            directory_path="root",
            provider="azure_devops",
            organization=worker_config.organization_url,
            project=worker_config.project,
        )
        is True
    )
    assert (
        store.is_processed(
            777,
            directory_path="root/team-a",
            provider="azure_devops",
            organization=worker_config.organization_url,
            project=worker_config.project,
        )
        is True
    )


def test_process_work_item_skips_when_already_processed(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)
    store.mark_processed(
        work_item_id=456,
        yaml_path=worker_config.yaml_output_dir / "work-item-456.yaml",
        script_exit_code=0,
        output_snippet="done",
        provider="azure_devops",
        organization=worker_config.organization_url,
        project=worker_config.project,
    )

    called = {"run": False}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        called["run"] = True
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.daemons.ado_issue_worker.subprocess.run", fake_run)

    processed = process_work_item(
        {
            "id": 456,
            "title": "Already handled",
            "description": "",
            "tags": ["truman"],
            "state": "Committed",
            "url": "",
            "updated_date": "2026-03-21T00:00:00Z",
        },
        config=worker_config,
        store=store,
    )

    assert processed is False
    assert called["run"] is False


def test_process_work_item_uses_root_vm_defaults_overlay_for_setup_env(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_payload = json.loads(worker_config.gateway_config_path.read_text(encoding="utf-8"))
    config_payload["enable_directory_scoped_config"] = True
    worker_config.gateway_config_path.write_text(json.dumps(config_payload), encoding="utf-8")

    runtime_dir = worker_config.runtime_dir
    (runtime_dir / "directory-config" / "root").mkdir(parents=True, exist_ok=True)
    (runtime_dir / "directory-config" / "root" / "vm-defaults.json").write_text(
        json.dumps(
            {
                "gateway_url": "http://10.0.0.10:8000/mcp",
                "openai_relay_port": 12000,
                "nuget_relay_port": 13000,
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "directories.json").write_text(
        json.dumps(
            {
                "root": {
                    "id": "root",
                    "name": "root",
                    "parent_id": None,
                    "created_at": "2026-03-27T00:00:00Z",
                    "metadata": {"directory_config": {"domains": ["vm-defaults"]}},
                }
            }
        ),
        encoding="utf-8",
    )

    captured_env: dict[str, str] = {}

    def successful_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_env.update(kwargs.get("env", {}))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.daemons.ado_issue_worker.subprocess.run", successful_run)

    processed = process_work_item(
        {
            "id": 123,
            "title": "Needs setup",
            "description": "",
            "tags": ["truman"],
            "state": "Committed",
            "url": "",
            "updated_date": "2026-03-21T00:00:00Z",
        },
        config=worker_config,
        store=ProcessedWorkItemStore(worker_config.processed_store_path),
    )

    assert processed is True
    assert captured_env["GATEWAY_URL"] == "http://10.0.0.10:8000/mcp"
    assert captured_env["LLM_BASE_URL"] == "http://10.0.0.10:12000/v1"
    assert captured_env["NUGET_RELAY_BASE_URL"] == "http://10.0.0.10:13000"


def test_process_work_item_marks_state_only_after_success(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)

    def failing_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=12, stdout="nope", stderr="bad")

    monkeypatch.setattr("agent_sandbox.daemons.ado_issue_worker.subprocess.run", failing_run)

    item = {
        "id": 789,
        "title": "Do thing",
        "description": "Details",
        "tags": ["truman"],
        "state": "Committed",
        "url": "https://example/789",
        "updated_date": "2026-03-21T00:00:00Z",
    }

    failed = process_work_item(item, config=worker_config, store=store)
    assert failed is False
    assert store.is_processed(789) is False

    yaml_path = worker_config.yaml_output_dir / "work-item-789.yaml"
    assert yaml_path.exists()
    parsed = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert parsed["issue_number"] == 789

    def successful_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.daemons.ado_issue_worker.subprocess.run", successful_run)

    succeeded = process_work_item(item, config=worker_config, store=store)
    assert succeeded is True
    assert (
        store.is_processed(
            789,
            provider="azure_devops",
            organization=worker_config.organization_url,
            project=worker_config.project,
        )
        is True
    )
    log_text = (worker_config.setup_log_dir / "work-item-789.log").read_text(encoding="utf-8")
    assert "===== " in log_text
    assert log_text.endswith("ok\n")


def test_process_work_item_upserts_unified_issue_and_refreshes_yaml(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)

    def successful_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.daemons.ado_issue_worker.subprocess.run", successful_run)

    first = {
        "id": 990,
        "title": "Initial title",
        "description": "First description",
        "tags": ["truman"],
        "state": "Committed",
        "url": "https://example/990",
        "updated_date": "2026-03-21T00:00:00Z",
        "comments": [{"text": "First", "author": "Ann", "created_date": "", "modified_date": ""}],
    }
    assert process_work_item(first, config=worker_config, store=store) is True

    second = {
        **first,
        "title": "Updated title",
        "description": "Second description",
        "tags": ["truman", "backend"],
        "state": "In Progress",
        "comments": [{"text": "Second", "author": "Bob", "created_date": "", "modified_date": ""}],
        "updated_date": "2026-03-21T01:00:00Z",
    }
    assert process_work_item(second, config=worker_config, store=store) is False

    local_issues_store = JsonStore(
        worker_config.runtime_dir / "local-issues.json", LocalIssueRecord
    )
    records = list(local_issues_store.read_all().values())
    assert len(records) == 1
    assert records[0].title == "Updated title"
    assert records[0].description == "Second description"
    assert records[0].state == "In Progress"
    assert records[0].tags == ["truman", "backend"]
    assert records[0].comments == [
        {"text": "Second", "author": "Bob", "created_date": "", "modified_date": ""}
    ]
    assert records[0].metadata["proxy_cache"]["title"] == "Updated title"
    assert records[0].metadata["proxy_cache"]["state"] == "In Progress"
    assert records[0].metadata["proxy_cache"]["comments"] == [
        {"text": "Second", "author": "Bob", "created_date": "", "modified_date": ""}
    ]
    assert records[0].remote_reference is not None
    assert records[0].remote_reference.work_item_id == "990"

    yaml_path = worker_config.yaml_output_dir / "work-item-990.yaml"
    parsed = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert parsed["issue_id"] == records[0].issue_id
    assert parsed["title"] == "Updated title"
    assert parsed["description"] == "Second description"
    assert parsed["state"] == "In Progress"
    assert parsed["tags"] == ["truman", "backend"]


def test_process_work_item_refresh_preserves_local_issue_plan_metadata(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)

    def successful_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.daemons.ado_issue_worker.subprocess.run", successful_run)

    item = {
        "id": 14758,
        "title": "Initial title",
        "description": "First description",
        "tags": ["truman"],
        "state": "Committed",
        "url": "https://example/14758",
        "updated_date": "2026-04-13T12:07:37.687000Z",
        "comments": [],
    }
    assert process_work_item(item, config=worker_config, store=store) is True

    local_issues_store = JsonStore(
        worker_config.runtime_dir / "local-issues.json", LocalIssueRecord
    )
    record = next(iter(local_issues_store.read_all().values()))
    record.metadata = {
        **record.metadata,
        "steps": ["Inspect code", "Write patch", "Run tests"],
        "plan_summary": "Fix the issue and verify it.",
    }
    local_issues_store.put(record.issue_id, record)
    yaml_path = worker_config.yaml_output_dir / "work-item-14758.yaml"
    parsed = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    parsed["steps"] = ["Inspect code", "Write patch", "Run tests"]
    parsed["plan_summary"] = "Fix the issue and verify it."
    yaml_path.write_text(yaml.safe_dump(parsed, sort_keys=False), encoding="utf-8")

    refreshed = {
        **item,
        "title": "Updated title",
        "description": "Second description",
        "updated_date": "2026-04-20T13:21:59.140483Z",
    }
    assert process_work_item(refreshed, config=worker_config, store=store) is False

    reread_record = next(iter(local_issues_store.read_all().values()))
    reread_yaml = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert reread_record.metadata["steps"] == ["Inspect code", "Write patch", "Run tests"]
    assert reread_record.metadata["plan_summary"] == "Fix the issue and verify it."
    assert reread_yaml["steps"] == ["Inspect code", "Write patch", "Run tests"]
    assert reread_yaml["plan_summary"] == "Fix the issue and verify it."
    assert reread_yaml["title"] == "Updated title"


def test_process_work_item_creates_session_manifest_before_setup(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)
    captured: dict[str, object] = {}

    def successful_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        cmd = args[0]
        assert isinstance(cmd, list)
        session_id = next(
            part for part in cmd if isinstance(part, str) and part.startswith("issue-14084-")
        )
        payload = json.loads(
            (worker_config.runtime_dir / "sessions.json").read_text(encoding="utf-8")
        )
        manifest = payload[session_id]
        captured["session_id"] = session_id
        captured["manifest"] = manifest
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.daemons.ado_issue_worker.subprocess.run", successful_run)

    item = {
        "id": 14084,
        "title": "Do thing",
        "description": "Details",
        "tags": ["truman"],
        "state": "Committed",
        "url": "https://example/14084",
        "updated_date": "2026-03-21T00:00:00Z",
    }

    succeeded = process_work_item(item, config=worker_config, store=store)

    assert succeeded is True
    assert (
        store.is_processed(
            14084,
            provider="azure_devops",
            organization=worker_config.organization_url,
            project=worker_config.project,
        )
        is True
    )
    assert captured["manifest"]["session_id"] == captured["session_id"]
    assert captured["manifest"]["vm_id"] == "vm-14084"
    assert captured["manifest"]["directory_path"] == "root"


def test_process_work_item_uses_configured_directory_for_session_manifest(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory_config = WorkerConfig(
        **{
            **worker_config.__dict__,
            "directory_path": "root/team-a",
        }
    )
    store = ProcessedWorkItemStore(directory_config.processed_store_path)
    captured: dict[str, object] = {}

    def successful_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        cmd = args[0]
        assert isinstance(cmd, list)
        session_id = next(
            part
            for part in cmd
            if isinstance(part, str) and part.startswith("issue-14086-root-team-a-")
        )
        payload = json.loads(
            (directory_config.runtime_dir / "sessions.json").read_text(encoding="utf-8")
        )
        manifest = payload[session_id]
        captured["session_id"] = session_id
        captured["manifest"] = manifest
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.daemons.ado_issue_worker.subprocess.run", successful_run)

    item = {
        "id": 14086,
        "title": "Do thing",
        "description": "Details",
        "tags": ["truman"],
        "state": "Committed",
        "url": "https://example/14086",
        "updated_date": "2026-03-21T00:00:00Z",
    }

    succeeded = process_work_item(item, config=directory_config, store=store)

    assert succeeded is True
    assert (
        store.is_processed(
            14086,
            directory_path="root/team-a",
            provider="azure_devops",
            organization=worker_config.organization_url,
            project=worker_config.project,
        )
        is True
    )
    assert captured["manifest"]["directory_path"] == "root/team-a"


def test_process_work_item_attaches_directory_shadow_context(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)

    class StubResolver:
        def resolve_for_session(
            self, session_id: str | None, *, fallback_directory_path: str | None = None, source: str
        ):
            return WorkerDirectoryContext(
                directory_path="root/team-a",
                source=source,
                session_id=session_id,
                domains=(ShadowDomainContext(domain="workers", diff_paths=("pool.max",)),),
            )

    gateway_payload = json.loads(worker_config.gateway_config_path.read_text(encoding="utf-8"))
    gateway_payload["enable_directory_scoped_config"] = True
    worker_config.gateway_config_path.write_text(json.dumps(gateway_payload), encoding="utf-8")
    monkeypatch.setattr(
        "agent_sandbox.daemons.ado_issue_worker.WorkerDirectoryContextResolver",
        lambda cfg: StubResolver(),
    )
    monkeypatch.setattr(
        "agent_sandbox.daemons.ado_issue_worker.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args, returncode=0, stdout="ok", stderr=""
        ),
    )

    item = {
        "id": 14085,
        "title": "Do thing with directory config",
        "description": "Details",
        "tags": ["truman"],
        "state": "Committed",
        "url": "https://example/14085",
        "updated_date": "2026-03-21T00:00:00Z",
    }

    succeeded = process_work_item(item, config=worker_config, store=store)

    assert succeeded is True
    assert item["directory_context"]["directory_path"] == "root/team-a"
    assert item["directory_context"]["source"] == "ado_issue_worker"
    assert item["directory_context"]["session_id"].startswith("issue-14085-")
    assert item["directory_context"]["domains"] == [
        {"domain": "workers", "diff_paths": ["pool.max"], "validation_errors": []}
    ]


def test_process_work_item_keeps_global_behavior_when_directory_scoped_config_disabled(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)
    monkeypatch.setattr(
        "agent_sandbox.daemons.ado_issue_worker.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args, returncode=0, stdout="ok", stderr=""
        ),
    )

    item = {
        "id": 14086,
        "title": "Do thing without scoped config",
        "description": "Details",
        "tags": ["truman"],
        "state": "Committed",
        "url": "https://example/14086",
        "updated_date": "2026-03-21T00:00:00Z",
    }

    succeeded = process_work_item(item, config=worker_config, store=store)

    assert succeeded is True
    assert "directory_context" not in item


def test_update_work_item_state_uses_json_patch(
    worker_config: WorkerConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    def fake_patch(_self: object, url: str, **kwargs: object) -> FakeResponse:
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        captured["headers"] = kwargs.get("headers")
        captured["json"] = kwargs.get("json")
        return FakeResponse()

    monkeypatch.setattr("httpx.Client.patch", fake_patch)
    with httpx.Client() as client:
        update_work_item_state(
            client=client,
            config=worker_config,
            auth_header="Basic token",
            work_item_id=14084,
            state="In Progress",
        )

    assert str(captured["url"]).endswith("/_apis/wit/workitems/14084")
    assert captured["params"] == {"api-version": "7.1"}
    assert captured["headers"] == {
        "Authorization": "Basic token",
        "Content-Type": "application/json-patch+json",
    }
    assert captured["json"] == [
        {
            "op": "add",
            "path": "/fields/System.State",
            "value": "In Progress",
        }
    ]


def test_process_work_item_updates_state_after_success(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)
    state_updates: list[tuple[int, str]] = []

    def successful_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    def fake_update_work_item_state(
        *,
        client: httpx.Client,
        config: WorkerConfig,
        auth_header: str,
        work_item_id: int,
        state: str,
    ) -> None:
        state_updates.append((work_item_id, state))

    monkeypatch.setattr("agent_sandbox.daemons.ado_issue_worker.subprocess.run", successful_run)
    monkeypatch.setattr(
        "agent_sandbox.daemons.ado_issue_worker.update_work_item_state", fake_update_work_item_state
    )

    item = {
        "id": 790,
        "title": "Do thing",
        "description": "Details",
        "tags": ["truman"],
        "state": "Committed",
        "url": "https://example/790",
        "updated_date": "2026-03-21T00:00:00Z",
    }

    with httpx.Client() as client:
        succeeded = process_work_item(
            item,
            config=worker_config,
            store=store,
            client=client,
            auth_header="Basic token",
        )

    assert succeeded is True
    assert (
        store.is_processed(
            790,
            provider="azure_devops",
            organization=worker_config.organization_url,
            project=worker_config.project,
        )
        is True
    )
    assert state_updates == [(790, "In Progress")]


def test_process_work_item_refreshes_local_proxy_state_after_success(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)

    def successful_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    def fake_update_work_item_state(
        *,
        client: httpx.Client,
        config: WorkerConfig,
        auth_header: str,
        work_item_id: int,
        state: str,
    ) -> None:
        return None

    monkeypatch.setattr("agent_sandbox.daemons.ado_issue_worker.subprocess.run", successful_run)
    monkeypatch.setattr(
        "agent_sandbox.daemons.ado_issue_worker.update_work_item_state", fake_update_work_item_state
    )

    item = {
        "id": 14382,
        "title": "Do thing",
        "description": "Details",
        "tags": ["truman"],
        "state": "Committed",
        "url": "https://example/14382",
        "updated_date": "2026-03-21T00:00:00Z",
    }

    with httpx.Client() as client:
        succeeded = process_work_item(
            item,
            config=worker_config,
            store=store,
            client=client,
            auth_header="Basic token",
        )

    assert succeeded is True

    local_issues_store = JsonStore(
        worker_config.runtime_dir / "local-issues.json", LocalIssueRecord
    )
    records = list(local_issues_store.read_all().values())
    assert len(records) == 1
    assert records[0].issue_number == "14382"
    assert records[0].state == "In Progress"
    assert records[0].metadata["proxy_cache"]["state"] == "In Progress"
    assert records[0].remote_reference is not None
    assert records[0].remote_reference.last_known_remote_state == "In Progress"
    assert records[0].remote_reference.last_remote_updated_at is not None

    yaml_payload = yaml.safe_load(
        (worker_config.yaml_output_dir / "work-item-14382.yaml").read_text(encoding="utf-8")
    )
    assert yaml_payload["state"] == "In Progress"


def test_process_work_item_does_not_update_state_when_setup_fails(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)
    state_updates: list[tuple[int, str]] = []

    def failing_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=12, stdout="nope", stderr="bad")

    def fake_update_work_item_state(
        *,
        client: httpx.Client,
        config: WorkerConfig,
        auth_header: str,
        work_item_id: int,
        state: str,
    ) -> None:
        state_updates.append((work_item_id, state))

    monkeypatch.setattr("agent_sandbox.daemons.ado_issue_worker.subprocess.run", failing_run)
    monkeypatch.setattr(
        "agent_sandbox.daemons.ado_issue_worker.update_work_item_state", fake_update_work_item_state
    )

    item = {
        "id": 791,
        "title": "Do thing",
        "description": "Details",
        "tags": ["truman"],
        "state": "Committed",
        "url": "https://example/791",
        "updated_date": "2026-03-21T00:00:00Z",
    }

    with httpx.Client() as client:
        succeeded = process_work_item(
            item,
            config=worker_config,
            store=store,
            client=client,
            auth_header="Basic token",
        )

    assert succeeded is False
    assert store.is_processed(791) is False
    assert state_updates == []
    log_text = (worker_config.setup_log_dir / "work-item-791.log").read_text(encoding="utf-8")
    assert "===== " in log_text
    assert log_text.endswith("nope\nbad\n")


def test_process_work_item_writes_full_setup_log_on_failure(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)

    def failing_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args, returncode=7, stdout="hello", stderr="goodbye"
        )

    monkeypatch.setattr("agent_sandbox.daemons.ado_issue_worker.subprocess.run", failing_run)

    item = {
        "id": 792,
        "title": "Failing setup",
        "description": "Details",
        "tags": ["truman"],
        "state": "Committed",
        "url": "https://example/792",
        "updated_date": "2026-03-21T00:00:00Z",
    }

    assert process_work_item(item, config=worker_config, store=store) is False
    log_text = (worker_config.setup_log_dir / "work-item-792.log").read_text(encoding="utf-8")
    assert "===== " in log_text
    assert log_text.endswith("hello\ngoodbye\n")


def test_is_vm_already_exists_output_detects_expected_marker() -> None:
    assert _is_vm_already_exists_output("VM already exists: vm-14084") is True
    assert _is_vm_already_exists_output("some other failure") is False


def test_process_work_item_treats_existing_vm_as_success(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)
    state_updates: list[tuple[int, str]] = []

    def already_exists_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args,
            returncode=1,
            stdout="VM already exists: vm-14084\nDestroy it first with: sudo ./scripts/destroy_agent_vm.sh vm-14084\n",
            stderr="",
        )

    def fake_update_work_item_state(
        *,
        client: httpx.Client,
        config: WorkerConfig,
        auth_header: str,
        work_item_id: int,
        state: str,
    ) -> None:
        state_updates.append((work_item_id, state))

    monkeypatch.setattr("agent_sandbox.daemons.ado_issue_worker.subprocess.run", already_exists_run)
    monkeypatch.setattr(
        "agent_sandbox.daemons.ado_issue_worker.update_work_item_state", fake_update_work_item_state
    )

    item = {
        "id": 793,
        "title": "Existing VM",
        "description": "Details",
        "tags": ["truman"],
        "state": "Committed",
        "url": "https://example/793",
        "updated_date": "2026-03-21T00:00:00Z",
    }

    with httpx.Client() as client:
        succeeded = process_work_item(
            item,
            config=worker_config,
            store=store,
            client=client,
            auth_header="Basic token",
        )

    assert succeeded is True
    assert (
        store.is_processed(
            793,
            provider="azure_devops",
            organization=worker_config.organization_url,
            project=worker_config.project,
        )
        is True
    )
    assert state_updates == [(793, "In Progress")]


def test_build_setup_command_includes_optional_arguments(tmp_path: Path) -> None:
    gateway_config_path = tmp_path / "gateway.yaml"
    config = WorkerConfig(
        organization_url="https://dev.azure.com/example",
        project="example",
        pat_env_var_name="ADO_WORK_ITEMS_AZURE_DEVOPS_PAT",
        poll_interval_seconds=30.0,
        area_filters=(),
        team_filters=(),
        runtime_dir=tmp_path,
        yaml_output_dir=tmp_path / "yaml",
        setup_log_dir=tmp_path / "setup-logs",
        processed_store_path=tmp_path / "state" / "processed.json",
        setup_script_path=Path("scripts/setup_vm.sh"),
        prewarm_target="real-target",
        repo_path="/tmp/repo",
        setup_vms=True,
        session_id="sess-1",
        vm_user="agent",
        setup_timeout_seconds=30.0,
        setup_concurrency_limit=1,
        setup_lock_path=tmp_path / "setup.lock",
        fetch_retry_count=2,
        fetch_retry_backoff_seconds=0.1,
        bootstrap_dotnet=True,
        bootstrap_nuget=True,
        bootstrap_mssql=True,
        mssql_sa_password="SecretPassw0rd!",
        mssql_init_root="/tmp/mssql-init",
        bootstrap_playwright=True,
        bootstrap_frontend_deps=True,
        frontend_host_checkout="/tmp/repo",
        frontend_install_roots=("/tmp/repo/Src/Clients", "/tmp/repo/Src/Clients/AdminClient"),
        include_comments=True,
        comments_max_count=20,
        comment_text_max_chars=4000,
        gateway_config_path=gateway_config_path,
        session_ttl_hours=8,
    )
    cmd = _build_setup_command(
        config, tmp_path / "work-item-10.yaml", session_id="sess-1", work_item_id=10
    )
    assert cmd == [
        "scripts/setup_vm.sh",
        "--dotnet",
        "--nuget",
        "--mssql",
        "--mssql-init-root",
        "/tmp/mssql-init",
        "--playwright",
        "real-target",
        "vm-10",
        "sess-1",
        "agent",
    ]


def test_build_setup_command_omits_disabled_bootstraps_and_nuget_prewarm_target(
    tmp_path: Path,
) -> None:
    gateway_config_path = tmp_path / "gateway.yaml"
    config = WorkerConfig(
        organization_url="https://dev.azure.com/example",
        project="example",
        pat_env_var_name="ADO_WORK_ITEMS_AZURE_DEVOPS_PAT",
        poll_interval_seconds=30.0,
        area_filters=(),
        team_filters=(),
        runtime_dir=tmp_path,
        yaml_output_dir=tmp_path / "yaml",
        setup_log_dir=tmp_path / "setup-logs",
        processed_store_path=tmp_path / "state" / "processed.json",
        setup_script_path=Path("scripts/setup_vm.sh"),
        prewarm_target="real-target",
        repo_path="/tmp/repo",
        setup_vms=True,
        session_id="sess-1",
        vm_user="agent",
        setup_timeout_seconds=30.0,
        setup_concurrency_limit=1,
        setup_lock_path=tmp_path / "setup.lock",
        fetch_retry_count=2,
        fetch_retry_backoff_seconds=0.1,
        bootstrap_dotnet=False,
        bootstrap_nuget=False,
        bootstrap_mssql=True,
        mssql_sa_password="SecretPassw0rd!",
        mssql_init_root=None,
        bootstrap_playwright=False,
        bootstrap_frontend_deps=False,
        frontend_host_checkout=None,
        frontend_install_roots=(),
        include_comments=True,
        comments_max_count=20,
        comment_text_max_chars=4000,
        gateway_config_path=gateway_config_path,
        session_ttl_hours=8,
    )

    cmd = _build_setup_command(
        config, tmp_path / "work-item-10.yaml", session_id="sess-1", work_item_id=10
    )
    assert cmd == [
        "scripts/setup_vm.sh",
        "--mssql",
        "vm-10",
        "sess-1",
        "agent",
    ]


def test_process_work_item_passes_timeout(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)
    captured: dict[str, object] = {}

    def successful_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["timeout"] = kwargs.get("timeout")
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.daemons.ado_issue_worker.subprocess.run", successful_run)

    succeeded = process_work_item(
        {
            "id": 1001,
            "title": "Do thing",
            "description": "Details",
            "tags": ["truman"],
            "state": "Committed",
            "url": "https://example/1001",
            "updated_date": "2026-03-21T00:00:00Z",
        },
        config=worker_config,
        store=store,
    )

    assert succeeded is True
    assert isinstance(captured["env"], dict)
    assert (
        captured["env"]["AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV"]
        == "ADO_WORK_ITEMS_AZURE_DEVOPS_PAT"
    )
    assert captured["env"]["MSSQL_SA_PASSWORD"] == "SecretPassw0rd!"
    assert captured["timeout"] == worker_config.setup_timeout_seconds


def test_worker_config_load_from_env_accepts_legacy_mssql_password_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    gateway_config_path = tmp_path / "gateway.yaml"
    gateway_config_path.write_text(
        yaml.safe_dump(
            {
                "runtime_dir": str(tmp_path),
                "azure_devops_organization_url": "https://dev.azure.com/example",
                "azure_devops_project": "example",
                "azure_devops_pat_env": "AZURE_DEVOPS_PAT",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ADO_GATEWAY_CONFIG", str(gateway_config_path))
    monkeypatch.setenv("ADO_PROJECT_OR_SOLUTION", "/tmp/Example.slnx")
    monkeypatch.setenv("ADO_BOOTSTRAP_MSSQL", "true")
    monkeypatch.delenv("ADO_MSSQL_SA_PASSWORD", raising=False)
    monkeypatch.setenv("MSSQL_SA_PASSWORD", "SecretPassw0rd!")

    config = WorkerConfig.load_from_env()

    assert config.bootstrap_mssql is True
    assert config.pat_env_var_name == "ADO_WORK_ITEMS_AZURE_DEVOPS_PAT"
    assert config.mssql_sa_password == "SecretPassw0rd!"


def test_worker_config_load_from_env_reads_ado_worker_global_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    gateway_config_path = tmp_path / "gateway.yaml"
    gateway_config_path.write_text(
        yaml.safe_dump(
            {
                "runtime_dir": str(tmp_path / "runtime"),
                "azure_devops_organization_url": "https://dev.azure.com/example",
                "azure_devops_project": "example",
                "azure_devops_pat_env": "AZURE_DEVOPS_PAT",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    worker_config_path = tmp_path / "config" / "ado-worker" / "config.json"
    worker_config_path.parent.mkdir(parents=True, exist_ok=True)
    worker_config_path.write_text(
        json.dumps(
            {
                "runtime_dir": str(tmp_path / "runtime-custom"),
                "poll_interval_seconds": 12.5,
                "directories": ["root/team-a", "root/team-b"],
                "yaml_output_dir": str(tmp_path / "yaml"),
                "setup_log_dir": str(tmp_path / "logs"),
                "processed_store_path": str(tmp_path / "processed.json"),
                "setup_script_path": "scripts/custom-setup.sh",
                "ado_work_items_azure_devops_pat_env": "JSON_WORK_ITEM_PAT",
                "setup_timeout_seconds": 42.0,
                "setup_concurrency_limit": 3,
                "setup_lock_path": str(tmp_path / "locks" / "setup.lock"),
                "fetch_retry_count": 4,
                "fetch_retry_backoff_seconds": 1.5,
                "include_comments": False,
                "comments_max_count": 11,
                "comment_text_max_chars": 777,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ADO_GATEWAY_CONFIG", str(gateway_config_path))
    monkeypatch.setenv("ADO_PROJECT_OR_SOLUTION", "/tmp/Example.slnx")
    monkeypatch.setenv("ADO_BOOTSTRAP_MSSQL", "false")

    config = WorkerConfig.load_from_env()

    assert config.runtime_dir == tmp_path / "runtime-custom"
    assert config.poll_interval_seconds == 12.5
    assert config.directories == ("root/team-a", "root/team-b")
    assert config.yaml_output_dir == tmp_path / "yaml"
    assert config.setup_log_dir == tmp_path / "logs"
    assert config.processed_store_path == tmp_path / "processed.json"
    assert config.setup_script_path == tmp_path / "scripts" / "custom-setup.sh"
    assert config.pat_env_var_name == "JSON_WORK_ITEM_PAT"
    assert config.setup_timeout_seconds == 42.0
    assert config.setup_concurrency_limit == 3
    assert config.setup_lock_path == tmp_path / "locks" / "setup.lock"
    assert config.fetch_retry_count == 4
    assert config.fetch_retry_backoff_seconds == 1.5
    assert config.include_comments is False
    assert config.comments_max_count == 11
    assert config.comment_text_max_chars == 777


def test_worker_config_load_from_env_idles_without_configured_directories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    gateway_config_path = tmp_path / "gateway.yaml"
    gateway_config_path.write_text(
        yaml.safe_dump(
            {
                "runtime_dir": str(tmp_path / "runtime"),
                "azure_devops_organization_url": "https://dev.azure.com/example",
                "azure_devops_project": "example",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    worker_config_path = tmp_path / "config" / "ado-worker" / "config.json"
    worker_config_path.parent.mkdir(parents=True, exist_ok=True)
    worker_config_path.write_text(
        json.dumps(
            {
                "runtime_dir": str(tmp_path / "runtime"),
                "poll_interval_seconds": 5,
                "directories": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ADO_GATEWAY_CONFIG", str(gateway_config_path))
    monkeypatch.delenv("ADO_PROJECT_OR_SOLUTION", raising=False)
    monkeypatch.delenv("ADO_WORK_ITEMS_AZURE_DEVOPS_PAT", raising=False)

    config = WorkerConfig.load_from_env()

    assert config.directories == ()
    assert config.directory_path == "root"
    assert config.prewarm_target == ""
    assert config.setup_vms is False
    assert config.bootstrap_dotnet is False
    assert config.bootstrap_nuget is False
    assert config.bootstrap_mssql is False


def test_run_worker_idles_without_directories_or_pat(
    monkeypatch: pytest.MonkeyPatch,
    worker_config: WorkerConfig,
) -> None:
    idle_config = WorkerConfig(**{**worker_config.__dict__, "directories": ()})
    monkeypatch.delenv(idle_config.pat_env_var_name, raising=False)

    def stop_after_first_sleep(seconds: float) -> None:
        assert seconds == idle_config.poll_interval_seconds
        raise KeyboardInterrupt

    monkeypatch.setattr("agent_sandbox.daemons.ado_issue_worker.time.sleep", stop_after_first_sleep)

    with pytest.raises(KeyboardInterrupt):
        run_worker(idle_config)


def test_process_work_item_skips_vm_setup_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    worker_config: WorkerConfig,
) -> None:
    item = {
        "id": 123,
        "title": "Example",
        "fields": {
            "System.State": "New",
            "System.ChangedDate": "2026-04-07T12:00:00Z",
        },
    }
    store = ProcessedWorkItemStore(worker_config.processed_store_path)
    disabled_config = WorkerConfig(**{**worker_config.__dict__, "setup_vms": False})
    called = False

    def should_not_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("subprocess.run should not be called when ado_setup_vms is false")

    monkeypatch.setattr("agent_sandbox.daemons.ado_issue_worker.subprocess.run", should_not_run)

    assert process_work_item(item, config=disabled_config, store=store) is True
    assert called is False
    assert (
        store.is_processed(
            123,
            directory_path=disabled_config.directory_path,
            provider="azure_devops",
            organization=disabled_config.organization_url,
            project=disabled_config.project,
        )
        is True
    )


def test_worker_config_load_from_env_requires_any_mssql_password_when_bootstrap_enabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    gateway_config_path = tmp_path / "gateway.yaml"
    gateway_config_path.write_text(
        yaml.safe_dump(
            {
                "runtime_dir": str(tmp_path),
                "azure_devops_organization_url": "https://dev.azure.com/example",
                "azure_devops_project": "example",
                "azure_devops_pat_env": "AZURE_DEVOPS_PAT",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ADO_GATEWAY_CONFIG", str(gateway_config_path))
    monkeypatch.setenv("ADO_PROJECT_OR_SOLUTION", "/tmp/Example.slnx")
    monkeypatch.setenv("ADO_BOOTSTRAP_MSSQL", "true")
    monkeypatch.delenv("ADO_MSSQL_SA_PASSWORD", raising=False)
    monkeypatch.delenv("MSSQL_SA_PASSWORD", raising=False)

    with pytest.raises(ValueError, match="Environment variable MSSQL_SA_PASSWORD is required"):
        WorkerConfig.load_from_env()


def test_worker_config_load_from_env_uses_ado_worker_mssql_env_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    gateway_config_path = tmp_path / "gateway.yaml"
    gateway_config_path.write_text(
        yaml.safe_dump(
            {
                "runtime_dir": str(tmp_path),
                "azure_devops_organization_url": "https://dev.azure.com/example",
                "azure_devops_project": "example",
                "azure_devops_pat_env": "AZURE_DEVOPS_PAT",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    directory_root = tmp_path / "directory-config" / "root"
    directory_root.mkdir(parents=True, exist_ok=True)
    (directory_root / "ado-worker.json").write_text(
        json.dumps(
            {
                "ado_project_or_solution": "/tmp/Example.slnx",
                "ado_repo_source": "/tmp/repo",
                "ado_bootstrap_mssql": True,
                "ado_mssql_sa_password_env": "TEAM_MSSQL_PASSWORD",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ADO_GATEWAY_CONFIG", str(gateway_config_path))
    monkeypatch.setenv("TEAM_MSSQL_PASSWORD", "TopSecret!")

    config = WorkerConfig.load_from_env()

    assert config.bootstrap_mssql is True
    assert config.mssql_sa_password == "TopSecret!"
    assert config.repo_path == "/tmp/repo"


def test_worker_config_load_from_env_uses_dedicated_worker_pat_env_setting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    gateway_config_path = tmp_path / "gateway.yaml"
    gateway_config_path.write_text(
        yaml.safe_dump(
            {
                "runtime_dir": str(tmp_path),
                "azure_devops_organization_url": "https://dev.azure.com/example-org",
                "azure_devops_project": "example-project",
                "azure_devops_pat_env": "ADO_DIRECTORY_PAT",
                "ado_project_or_solution": "/tmp/FromGateway.slnx",
                "ado_area_filters": ["Root\\Area"],
                "ado_bootstrap_mssql": False,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    worker_config_dir = tmp_path / "config" / "ado-worker"
    worker_config_dir.mkdir(parents=True, exist_ok=True)
    (worker_config_dir / "config.json").write_text(
        json.dumps({"ado_work_items_azure_devops_pat_env": "JSON_WORK_ITEM_PAT"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ADO_GATEWAY_CONFIG", str(gateway_config_path))
    monkeypatch.setenv("ADO_PROJECT_OR_SOLUTION", "/tmp/Example.slnx")
    monkeypatch.setenv("ADO_BOOTSTRAP_MSSQL", "false")

    with caplog.at_level("WARNING"):
        config = WorkerConfig.load_from_env()

    assert config.organization_url == "https://dev.azure.com/example-org"
    assert config.project == "example-project"
    assert config.pat_env_var_name == "JSON_WORK_ITEM_PAT"
    assert config.prewarm_target == "/tmp/Example.slnx"
    assert config.area_filters == ("Root\\Area",)
    assert config.bootstrap_mssql is False
    assert "Deprecated ADO worker env override in use for ado_project_or_solution" in caplog.text


def test_worker_config_load_from_env_bootstraps_from_first_configured_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    gateway_config_path = tmp_path / "gateway.yaml"
    gateway_config_path.write_text(
        yaml.safe_dump(
            {
                "runtime_dir": str(tmp_path),
                "azure_devops_organization_url": "https://dev.azure.com/example-org",
                "azure_devops_project": "example-project",
                "azure_devops_pat_env": "ADO_DIRECTORY_PAT",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    directories_path = tmp_path / "directories.json"
    directories_path.write_text(
        json.dumps(
            {
                "root": {
                    "id": "root",
                    "name": "root",
                    "parent_id": None,
                    "created_at": "2026-03-28T00:00:00Z",
                    "metadata": {},
                },
                "example": {
                    "id": "example",
                    "name": "example-industry",
                    "parent_id": "root",
                    "created_at": "2026-03-28T00:00:00Z",
                    "metadata": {},
                },
                "app": {
                    "id": "app",
                    "name": "app",
                    "parent_id": "example",
                    "created_at": "2026-03-28T00:00:00Z",
                    "metadata": {
                        "directory_config": {
                            "domains": ["ado_worker"],
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    root_directory = tmp_path / "directory-config" / "root"
    app_directory = root_directory / "example-industry" / "app"
    app_directory.mkdir(parents=True, exist_ok=True)
    (root_directory / "ado-worker.json").write_text(
        json.dumps(
            {
                "ado_area_filters": [],
                "ado_team_filters": [],
            }
        ),
        encoding="utf-8",
    )
    (app_directory / "ado-worker.json").write_text(
        json.dumps(
            {
                "ado_project_or_solution": "/tmp/Example.slnx",
                "ado_repo_source": "/tmp/repo",
                "ado_bootstrap_mssql": False,
            }
        ),
        encoding="utf-8",
    )
    worker_config_dir = tmp_path / "config" / "ado-worker"
    worker_config_dir.mkdir(parents=True, exist_ok=True)
    (worker_config_dir / "config.json").write_text(
        json.dumps({"directories": ["root/example-industry/app"]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ADO_GATEWAY_CONFIG", str(gateway_config_path))

    config = WorkerConfig.load_from_env()

    assert config.directory_path == "root/example-industry/app"
    assert config.directories == ("root/example-industry/app",)
    assert config.prewarm_target == "/tmp/Example.slnx"
    assert config.repo_path == "/tmp/repo"


def test_worker_config_load_from_env_prefers_worker_json_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    gateway_config_path = tmp_path / "gateway.yaml"
    gateway_config_path.write_text(
        yaml.safe_dump(
            {
                "runtime_dir": str(tmp_path),
                "azure_devops_organization_url": "https://dev.azure.com/example-org",
                "azure_devops_project": "example-project",
                "azure_devops_pat_env": "ADO_DIRECTORY_PAT",
                "ado_project_or_solution": "/tmp/FromGateway.slnx",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    directories_path = tmp_path / "directories.json"
    directories_path.write_text(
        json.dumps(
            {
                "root": {
                    "id": "root",
                    "name": "root",
                    "parent_id": None,
                    "created_at": "2026-03-28T00:00:00Z",
                    "metadata": {},
                },
                "team-a": {
                    "id": "team-a",
                    "name": "team-a",
                    "parent_id": "root",
                    "created_at": "2026-03-28T00:00:00Z",
                    "metadata": {},
                },
            }
        ),
        encoding="utf-8",
    )
    worker_config_dir = tmp_path / "config" / "ado-worker"
    worker_config_dir.mkdir(parents=True, exist_ok=True)
    (worker_config_dir / "config.json").write_text(
        json.dumps(
            {
                "poll_interval_seconds": 45,
                "directories": ["root/team-a"],
                "runtime_dir": "json-runtime",
                "yaml_output_dir": "json-runtime/yaml",
                "setup_log_dir": "json-runtime/logs",
                "processed_store_path": "json-runtime/state/processed.json",
                "setup_script_path": "scripts/from-json.sh",
                "ado_work_items_azure_devops_pat_env": "JSON_WORK_ITEM_PAT",
                "setup_timeout_seconds": 999,
                "setup_concurrency_limit": 3,
                "setup_lock_path": "json-runtime/locks/setup.lock",
                "fetch_retry_count": 7,
                "fetch_retry_backoff_seconds": 9.5,
                "include_comments": False,
                "comments_max_count": 3,
                "comment_text_max_chars": 1234,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ADO_GATEWAY_CONFIG", str(gateway_config_path))
    monkeypatch.setenv("ADO_PROJECT_OR_SOLUTION", "/tmp/Example.slnx")
    monkeypatch.setenv("ADO_BOOTSTRAP_MSSQL", "false")
    monkeypatch.setenv("ADO_POLL_INTERVAL_SECONDS", "10")
    monkeypatch.setenv("ADO_RUNTIME_DIR", "env-runtime")
    monkeypatch.setenv("ADO_YAML_OUTPUT_DIR", "env-runtime/yaml")
    monkeypatch.setenv("ADO_SETUP_LOG_DIR", "env-runtime/logs")
    monkeypatch.setenv("ADO_PROCESSED_STORE_PATH", "env-runtime/state/processed.json")
    monkeypatch.setenv("ADO_SETUP_ISSUE_VM_SCRIPT", "scripts/from-env.sh")
    monkeypatch.setenv("ADO_SETUP_TIMEOUT_SECONDS", "111")
    monkeypatch.setenv("ADO_SETUP_CONCURRENCY_LIMIT", "9")
    monkeypatch.setenv("ADO_SETUP_LOCK_PATH", "env-runtime/locks/setup.lock")
    monkeypatch.setenv("ADO_FETCH_RETRY_COUNT", "2")
    monkeypatch.setenv("ADO_FETCH_RETRY_BACKOFF_SECONDS", "1.0")
    monkeypatch.setenv("ADO_INCLUDE_COMMENTS", "true")
    monkeypatch.setenv("ADO_COMMENTS_MAX_COUNT", "8")
    monkeypatch.setenv("ADO_COMMENT_TEXT_MAX_CHARS", "456")

    config = WorkerConfig.load_from_env()

    assert config.poll_interval_seconds == 45
    assert config.directories == ("root/team-a",)
    assert config.runtime_dir == tmp_path / "json-runtime"
    assert config.yaml_output_dir == tmp_path / "json-runtime" / "yaml"
    assert config.setup_log_dir == tmp_path / "json-runtime" / "logs"
    assert config.processed_store_path == tmp_path / "json-runtime" / "state" / "processed.json"
    assert config.setup_script_path == tmp_path / "scripts" / "from-json.sh"
    assert config.pat_env_var_name == "JSON_WORK_ITEM_PAT"
    assert config.setup_timeout_seconds == 999
    assert config.setup_concurrency_limit == 3
    assert config.setup_lock_path == tmp_path / "json-runtime" / "locks" / "setup.lock"
    assert config.fetch_retry_count == 7
    assert config.fetch_retry_backoff_seconds == 9.5
    assert config.include_comments is False
    assert config.comments_max_count == 3
    assert config.comment_text_max_chars == 1234


def test_worker_config_load_from_env_warns_on_deprecated_env_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    gateway_config_path = tmp_path / "gateway.yaml"
    gateway_config_path.write_text(
        yaml.safe_dump(
            {
                "runtime_dir": str(tmp_path),
                "azure_devops_organization_url": "https://dev.azure.com/example-org",
                "azure_devops_project": "example-project",
                "azure_devops_pat_env": "ADO_DIRECTORY_PAT",
                "ado_project_or_solution": "/tmp/FromGateway.slnx",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ADO_GATEWAY_CONFIG", str(gateway_config_path))
    monkeypatch.setenv("ADO_PROJECT_OR_SOLUTION", "/tmp/FromEnv.slnx")
    monkeypatch.setenv("ADO_REPO_PATH", "/tmp/from-env-repo")
    monkeypatch.setenv("ADO_BOOTSTRAP_MSSQL", "false")

    with caplog.at_level("WARNING"):
        config = WorkerConfig.load_from_env()

    assert config.prewarm_target == "/tmp/FromEnv.slnx"
    assert config.repo_path == "/tmp/from-env-repo"
    assert "Deprecated ADO worker env override in use for ado_project_or_solution" in caplog.text
    assert "Deprecated ADO worker env override in use for ado_repo_source" in caplog.text


def test_run_worker_command_forget_does_not_require_mssql_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    gateway_config_path = tmp_path / "gateway.yaml"
    gateway_config_path.write_text(
        yaml.safe_dump(
            {
                "runtime_dir": str(tmp_path),
                "azure_devops_organization_url": "https://dev.azure.com/example-org",
                "azure_devops_project": "example-project",
                "azure_devops_pat_env": "ADO_DIRECTORY_PAT",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    directory_root = tmp_path / "directory-config" / "root"
    directory_root.mkdir(parents=True, exist_ok=True)
    (directory_root / "ado-worker.json").write_text(
        json.dumps(
            {
                "ado_project_or_solution": "/tmp/Example.slnx",
                "ado_bootstrap_mssql": True,
            }
        ),
        encoding="utf-8",
    )
    processed_store_path = tmp_path / "processed.json"
    processed_store_path.write_text(
        json.dumps(
            {
                "14382": {
                    "work_item_id": 14382,
                    "yaml_path": str(tmp_path / "ado-work-items" / "work-item-14382.yaml"),
                    "script_exit_code": 0,
                    "script_output_snippet": "ok",
                    "processed_at": "2026-03-28T12:00:00+00:00",
                }
            }
        ),
        encoding="utf-8",
    )
    worker_config_dir = tmp_path / "config" / "ado-worker"
    worker_config_dir.mkdir(parents=True, exist_ok=True)
    (worker_config_dir / "config.json").write_text(
        json.dumps({"processed_store_path": str(processed_store_path)}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ADO_GATEWAY_CONFIG", str(gateway_config_path))
    monkeypatch.delenv("ADO_MSSQL_SA_PASSWORD", raising=False)
    monkeypatch.delenv("MSSQL_SA_PASSWORD", raising=False)

    run_worker_command(log_level="INFO", forget=14382)

    payload = json.loads(processed_store_path.read_text(encoding="utf-8"))
    assert "14382" not in payload


def test_run_worker_command_forget_does_not_require_root_ado_project_or_solution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    gateway_config_path = tmp_path / "gateway.yaml"
    gateway_config_path.write_text(
        yaml.safe_dump(
            {
                "runtime_dir": str(tmp_path),
                "azure_devops_organization_url": "https://dev.azure.com/example-org",
                "azure_devops_project": "example-project",
                "azure_devops_pat_env": "ADO_DIRECTORY_PAT",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    directory_root = tmp_path / "directory-config" / "root"
    directory_root.mkdir(parents=True, exist_ok=True)
    (directory_root / "ado-worker.json").write_text(
        json.dumps(
            {
                "ado_area_filters": [],
                "ado_team_filters": [],
            }
        ),
        encoding="utf-8",
    )
    processed_store_path = tmp_path / "processed.json"
    processed_store_path.write_text(
        json.dumps(
            {
                "root/example-industry/app::azure_devops::https://dev.azure.com/example-org::example-project::14382": {
                    "directory_path": "root/example-industry/app",
                    "organization": "https://dev.azure.com/example-org",
                    "project": "example-project",
                    "provider": "azure_devops",
                    "remote_work_item_id": "14382",
                    "yaml_path": str(tmp_path / "ado-work-items" / "work-item-14382.yaml"),
                    "script_exit_code": 0,
                    "script_output_snippet": "ok",
                    "processed_at": "2026-03-28T12:00:00+00:00",
                }
            }
        ),
        encoding="utf-8",
    )
    worker_config_dir = tmp_path / "config" / "ado-worker"
    worker_config_dir.mkdir(parents=True, exist_ok=True)
    (worker_config_dir / "config.json").write_text(
        json.dumps({"processed_store_path": str(processed_store_path)}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ADO_GATEWAY_CONFIG", str(gateway_config_path))

    run_worker_command(log_level="INFO", forget=14382)

    payload = json.loads(processed_store_path.read_text(encoding="utf-8"))
    assert payload == {}


@pytest.mark.parametrize(
    ("raw_tags", "expected_match"),
    [
        ("truman", True),
        (" TRUMAN ", True),
        ("foo; truman ;bar", True),
        ("foo;TrUmAn;bar", True),
        ("agent-truman", False),
        ("", False),
    ],
)
def test_gate_and_order_items_requires_truman_tag_with_case_and_spacing_variants(
    worker_config: WorkerConfig,
    raw_tags: str,
    expected_match: bool,
) -> None:
    matched, skipped = gate_and_order_items(
        [
            {
                "id": 101,
                "url": "https://example/101",
                "fields": {
                    "System.Title": "Tag candidate",
                    "System.Description": "desc",
                    "System.Tags": raw_tags,
                    "System.State": "Committed",
                    "System.ChangedDate": "2026-03-21T00:00:00Z",
                    "System.AreaPath": "Root",
                    "System.IterationPath": "Team",
                },
            }
        ],
        worker_config,
    )

    if expected_match:
        assert [item["id"] for item in matched] == [101]
        assert skipped == 0
    else:
        assert matched == []
        assert skipped == 1


def test_gate_and_order_items_ignores_non_committed_states(worker_config: WorkerConfig) -> None:
    matched, skipped = gate_and_order_items(
        [
            {
                "id": 201,
                "fields": {
                    "System.Title": "todo",
                    "System.Tags": "truman",
                    "System.State": "New",
                    "System.ChangedDate": "2026-03-21T00:00:00Z",
                },
            },
            {
                "id": 202,
                "fields": {
                    "System.Title": "active",
                    "System.Tags": "truman",
                    "System.State": "Active",
                    "System.ChangedDate": "2026-03-21T00:00:01Z",
                },
            },
            {
                "id": 203,
                "fields": {
                    "System.Title": "resolved",
                    "System.Tags": "truman",
                    "System.State": "Resolved",
                    "System.ChangedDate": "2026-03-21T00:00:02Z",
                },
            },
        ],
        worker_config,
    )

    assert matched == []
    assert skipped == 3


def test_gate_and_order_items_appends_bug_sections_to_description(
    worker_config: WorkerConfig,
) -> None:
    matched, skipped = gate_and_order_items(
        [
            {
                "id": 204,
                "url": "https://example/204",
                "fields": {
                    "System.Title": "criteria",
                    "System.Description": "Implement the change",
                    "Microsoft.VSTS.TCM.ReproSteps": "1. Launch app\n2. Click save",
                    "Microsoft.VSTS.TCM.SystemInfo": "Browser: Edge",
                    "Microsoft.VSTS.Common.AcceptanceCriteria": "It passes validation",
                    "System.Tags": "truman",
                    "System.State": "Committed",
                    "System.ChangedDate": "2026-03-21T00:00:03Z",
                    "System.AreaPath": "Root",
                    "System.IterationPath": "Team",
                },
            }
        ],
        worker_config,
    )

    assert skipped == 0
    assert matched[0]["description"] == (
        "Implement the change\n\n"
        "Repro Steps\n\n1. Launch app\n2. Click save\n\n"
        "System Info\n\nBrowser: Edge\n\n"
        "Acceptance Criteria\n\nIt passes validation"
    )


def test_process_work_item_skips_previously_processed_id_on_subsequent_attempt(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)
    call_count = {"run": 0}

    def successful_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        call_count["run"] += 1
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.daemons.ado_issue_worker.subprocess.run", successful_run)

    item = {
        "id": 301,
        "title": "Process once",
        "description": "Details",
        "tags": ["truman"],
        "state": "Committed",
        "url": "https://example/301",
        "updated_date": "2026-03-21T00:00:00Z",
    }

    first = process_work_item(item, config=worker_config, store=store)
    second = process_work_item(item, config=worker_config, store=store)

    assert first is True
    assert second is False
    assert call_count["run"] == 1


def test_process_work_item_writes_yaml_with_required_issue_number_and_metadata(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)

    def successful_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.daemons.ado_issue_worker.subprocess.run", successful_run)

    item = {
        "id": 401,
        "title": "YAML metadata",
        "description": "Contains details",
        "tags": ["backend", "truman"],
        "state": "Committed",
        "url": "https://example/401",
        "updated_date": "2026-03-21T00:00:00Z",
    }

    assert process_work_item(item, config=worker_config, store=store) is True

    yaml_path = worker_config.yaml_output_dir / "work-item-401.yaml"
    parsed = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert parsed["issue_number"] == 401
    assert parsed["title"] == "YAML metadata"
    assert parsed["description"] == "Contains details"
    assert parsed["tags"] == ["backend", "truman"]
    assert parsed["state"] == "Committed"
    assert parsed["url"] == "https://example/401"
    assert parsed["updated_date"] == "2026-03-21T00:00:00Z"
    assert parsed["generated_at"]


def test_process_work_item_writes_yaml_with_bug_sections_appended_to_description(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)

    def successful_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.daemons.ado_issue_worker.subprocess.run", successful_run)

    item = {
        "id": 402,
        "title": "YAML bug sections",
        "description": (
            "Contains details\n\n"
            "Repro Steps\n\nOpen the dialog\n\n"
            "System Info\n\nOS: Ubuntu\n\n"
            "Acceptance Criteria\n\nMeets the acceptance bar"
        ),
        "tags": ["backend", "truman"],
        "state": "Committed",
        "url": "https://example/402",
        "updated_date": "2026-03-21T00:00:00Z",
    }

    assert process_work_item(item, config=worker_config, store=store) is True

    yaml_path = worker_config.yaml_output_dir / "work-item-402.yaml"
    parsed = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert parsed["description"] == (
        "Contains details\n\n"
        "Repro Steps\n\nOpen the dialog\n\n"
        "System Info\n\nOS: Ubuntu\n\n"
        "Acceptance Criteria\n\nMeets the acceptance bar"
    )


def test_setup_issue_vm_invocation_once_per_newly_eligible_item(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)
    calls: list[list[str]] = []

    def successful_run(
        cmd: list[str], *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(args=[cmd], returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.daemons.ado_issue_worker.subprocess.run", successful_run)

    eligible_items = [
        {
            "id": 501,
            "title": "first",
            "description": "",
            "tags": ["truman"],
            "state": "Committed",
            "url": "https://example/501",
            "updated_date": "2026-03-21T00:00:00Z",
        },
        {
            "id": 502,
            "title": "second",
            "description": "",
            "tags": ["truman"],
            "state": "Committed",
            "url": "https://example/502",
            "updated_date": "2026-03-21T00:00:01Z",
        },
    ]

    for item in eligible_items:
        assert process_work_item(item, config=worker_config, store=store) is True
        assert process_work_item(item, config=worker_config, store=store) is False

    assert len(calls) == 2
    assert calls[0][-2] == "vm-501"
    assert calls[1][-2] == "vm-502"
    assert calls[0][-1].startswith("issue-501-")
    assert calls[1][-1].startswith("issue-502-")


def test_fetch_work_item_comments_normalizes_and_truncates(
    worker_config: WorkerConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_config = WorkerConfig(
        **{
            **worker_config.__dict__,
            "comment_text_max_chars": 5,
            "comments_max_count": 3,
        }
    )

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "comments": [
                    {
                        "text": "  abcdef  ",
                        "createdDate": "2026-03-21T00:00:00Z",
                        "modifiedDate": "2026-03-21T01:00:00Z",
                        "revisedBy": {"displayName": "Ada"},
                    },
                    {"text": "   "},
                    {"text": None},
                ]
            }

    captured: dict[str, object] = {}

    def fake_get(_self: object, url: str, **kwargs: object) -> FakeResponse:
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return FakeResponse()

    monkeypatch.setattr("httpx.Client.get", fake_get)
    with httpx.Client() as client:
        comments = fetch_work_item_comments(
            client=client,
            config=worker_config,
            auth_header="Basic token",
            work_item_id=77,
        )

    assert str(captured["url"]).endswith("/_apis/wit/workItems/77/comments")
    assert captured["params"] == {"api-version": "7.1-preview.4", "$top": "3", "order": "asc"}
    assert comments == [
        {
            "id": "",
            "text": "abcde",
            "author": "Ada",
            "created_date": "2026-03-21T00:00:00Z",
            "modified_date": "2026-03-21T01:00:00Z",
            "url": "",
        }
    ]


def test_fetch_work_item_batch_requests_acceptance_criteria_field(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"value": []}

    captured: dict[str, object] = {}

    def fake_post(_self: object, url: str, **kwargs: object) -> FakeResponse:
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        captured["json"] = kwargs.get("json")
        return FakeResponse()

    monkeypatch.setattr("httpx.Client.post", fake_post)
    with httpx.Client() as client:
        items = fetch_work_item_batch(
            client=client,
            config=worker_config,
            auth_header="Basic token",
            candidate_ids=[77],
        )

    assert items == []
    assert str(captured["url"]).endswith("/_apis/wit/workitemsbatch")
    assert captured["params"] == {"api-version": "7.1"}
    assert "Microsoft.VSTS.TCM.ReproSteps" in captured["json"]["fields"]
    assert "Microsoft.VSTS.TCM.SystemInfo" in captured["json"]["fields"]
    assert "Microsoft.VSTS.Common.AcceptanceCriteria" in captured["json"]["fields"]


def test_poll_once_attaches_comments(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_sandbox.daemons.ado_issue_worker.fetch_candidate_ids",
        lambda **kwargs: [10],
    )
    monkeypatch.setattr(
        "agent_sandbox.daemons.ado_issue_worker.fetch_work_item_batch",
        lambda **kwargs: [
            {
                "id": 10,
                "url": "https://example/10",
                "fields": {
                    "System.Title": "Item 10",
                    "System.Description": "desc",
                    "System.Tags": "truman",
                    "System.State": "Committed",
                    "System.ChangedDate": "2026-03-21T00:00:00Z",
                    "System.AreaPath": "Root",
                    "System.IterationPath": "Team",
                },
            }
        ],
    )
    monkeypatch.setattr(
        "agent_sandbox.daemons.ado_issue_worker.fetch_work_item_comments",
        lambda **kwargs: [
            {"text": "Needs x", "author": "Ann", "created_date": "", "modified_date": ""}
        ],
    )

    with httpx.Client() as client:
        items = poll_once(client=client, config=worker_config, auth_header="Basic token")

    assert len(items) == 1
    assert items[0]["comments"] == [
        {"text": "Needs x", "author": "Ann", "created_date": "", "modified_date": ""}
    ]


def test_fetch_work_item_by_id_attaches_comments(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_sandbox.daemons.ado_issue_worker.fetch_work_item_batch",
        lambda *args, **kwargs: [
            {
                "id": 10,
                "url": "https://example/10",
                "fields": {
                    "System.Title": "Item 10",
                    "System.Description": "desc",
                    "System.Tags": "truman",
                    "System.State": "Committed",
                    "System.ChangedDate": "2026-03-21T00:00:00Z",
                    "System.AreaPath": "Root",
                    "System.IterationPath": "Team",
                },
            }
        ],
    )
    monkeypatch.setattr(
        "agent_sandbox.daemons.ado_issue_worker.fetch_work_item_comments",
        lambda **kwargs: [
            {"text": "Needs x", "author": "Ann", "created_date": "", "modified_date": ""}
        ],
    )

    from agent_sandbox.daemons.ado_issue_worker import fetch_work_item_by_id

    with httpx.Client() as client:
        item = fetch_work_item_by_id(
            client=client,
            config=worker_config,
            auth_header="Basic token",
            work_item_id=10,
        )

    assert item is not None
    assert item["comments"] == [
        {"text": "Needs x", "author": "Ann", "created_date": "", "modified_date": ""}
    ]


def test_filter_unprocessed_work_items_excludes_processed(worker_config: WorkerConfig) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)
    store.mark_processed(
        work_item_id=10,
        yaml_path=worker_config.yaml_output_dir / "work-item-10.yaml",
        script_exit_code=0,
        output_snippet="done",
        provider="azure_devops",
        organization=worker_config.organization_url,
        project=worker_config.project,
    )
    items = [
        {"id": 10, "title": "Picked up"},
        {"id": 11, "title": "Still pending"},
    ]

    filtered = filter_unprocessed_work_items(items, store, config=worker_config)

    assert filtered == [{"id": 11, "title": "Still pending"}]


def test_list_unprocessed_work_items_filters_processed(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)
    store.mark_processed(
        work_item_id=10,
        yaml_path=worker_config.yaml_output_dir / "work-item-10.yaml",
        script_exit_code=0,
        output_snippet="done",
        provider="azure_devops",
        organization=worker_config.organization_url,
        project=worker_config.project,
    )
    monkeypatch.setattr(
        "agent_sandbox.daemons.ado_issue_worker.poll_once",
        lambda **kwargs: [
            {
                "id": 10,
                "title": "Picked up",
                "state": "Committed",
                "updated_date": "2026-03-21T00:00:00Z",
            },
            {
                "id": 11,
                "title": "Still pending",
                "state": "Committed",
                "updated_date": "2026-03-21T00:01:00Z",
            },
        ],
    )

    with httpx.Client() as client:
        items = list_unprocessed_work_items(
            client=client,
            config=worker_config,
            auth_header="Basic token",
            store=store,
        )

    assert items == [
        {
            "id": 11,
            "title": "Still pending",
            "state": "Committed",
            "updated_date": "2026-03-21T00:01:00Z",
        }
    ]


def test_unprocessed_items_for_worker_iterates_configured_directories(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_sandbox.daemons import ado_issue_worker as ado_worker

    runtime_gateway_config = yaml.safe_load(
        json.dumps(
            {
                "runtime_dir": str(worker_config.runtime_dir),
                "session_store_path": str(worker_config.runtime_dir / "sessions.json"),
                "approval_store_path": str(worker_config.runtime_dir / "approvals.json"),
                "draft_store_path": str(worker_config.runtime_dir / "drafts.json"),
                "email_store_path": str(worker_config.runtime_dir / "emails.json"),
                "discord_command_store_path": str(
                    worker_config.runtime_dir / "discord_commands.json"
                ),
                "audit_log_path": str(worker_config.runtime_dir / "audit.jsonl"),
                "directories_store_path": str(worker_config.runtime_dir / "directories.json"),
            }
        )
    )
    gateway_config = ado_worker.GatewayConfig.model_validate(runtime_gateway_config)
    configured = WorkerConfig(
        **{
            **worker_config.__dict__,
            "directories": ("root/team-a", "root/team-b"),
        }
    )
    seen: list[str] = []

    def fake_resolve(
        config: WorkerConfig, runtime_gateway_config: object, directory_path: str
    ) -> WorkerConfig:
        return WorkerConfig(
            **{
                **config.__dict__,
                "directory_path": directory_path,
            }
        )

    def fake_list_unprocessed_work_items(*, client, config, auth_header, store):
        seen.append(config.directory_path)
        return [{"id": len(seen), "title": config.directory_path}]

    monkeypatch.setattr(ado_worker, "_resolve_directory_worker_config", fake_resolve)
    monkeypatch.setattr(ado_worker, "list_unprocessed_work_items", fake_list_unprocessed_work_items)

    with httpx.Client() as client:
        items = ado_worker._unprocessed_items_for_worker(
            client=client,
            config=configured,
            auth_header="Basic token",
            store=ProcessedWorkItemStore(configured.processed_store_path),
            runtime_gateway_config=gateway_config,
        )

    assert seen == ["root/team-a", "root/team-b"]
    assert items == [{"id": 1, "title": "root/team-a"}, {"id": 2, "title": "root/team-b"}]


def test_poll_and_process_directory_uses_directory_specific_config(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_sandbox.daemons import ado_issue_worker as ado_worker

    runtime_gateway_config = ado_worker.GatewayConfig.model_validate(
        {
            "runtime_dir": str(worker_config.runtime_dir),
            "session_store_path": str(worker_config.runtime_dir / "sessions.json"),
            "approval_store_path": str(worker_config.runtime_dir / "approvals.json"),
            "draft_store_path": str(worker_config.runtime_dir / "drafts.json"),
            "email_store_path": str(worker_config.runtime_dir / "emails.json"),
            "discord_command_store_path": str(worker_config.runtime_dir / "discord_commands.json"),
            "audit_log_path": str(worker_config.runtime_dir / "audit.jsonl"),
            "directories_store_path": str(worker_config.runtime_dir / "directories.json"),
        }
    )
    configured = WorkerConfig(
        **{
            **worker_config.__dict__,
            "directories": ("root/team-a",),
        }
    )
    calls: list[tuple[str, str, int | None]] = []

    def fake_resolve(
        config: WorkerConfig, runtime_gateway_config: object, directory_path: str
    ) -> WorkerConfig:
        return WorkerConfig(
            **{
                **config.__dict__,
                "directory_path": directory_path,
            }
        )

    def fake_poll_once(*, client, config, auth_header):
        calls.append(("poll", config.directory_path, None))
        return [{"id": 99, "title": config.directory_path}]

    def fake_process_work_item(item, *, config, store, client=None, auth_header=None):
        calls.append(("process", config.directory_path, int(item["id"])))
        return True

    monkeypatch.setattr(ado_worker, "_resolve_directory_worker_config", fake_resolve)
    monkeypatch.setattr(ado_worker, "poll_once", fake_poll_once)
    monkeypatch.setattr(ado_worker, "process_work_item", fake_process_work_item)

    with httpx.Client() as client:
        ado_worker._poll_and_process_directory(
            client=client,
            config=configured,
            auth_header="Basic token",
            store=ProcessedWorkItemStore(configured.processed_store_path),
            runtime_gateway_config=runtime_gateway_config,
            directory_path="root/team-a",
        )

    assert calls == [("poll", "root/team-a", None), ("process", "root/team-a", 99)]


def test_publish_directory_pr_feedback_events_enqueues_azure_feedback(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_sandbox.daemons import ado_issue_worker as ado_worker

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
        }
    )

    monkeypatch.setattr(ado_worker, "_load_ado_pr_feedback_triggers", lambda _config: [trigger])
    monkeypatch.setenv("AZURE_DEVOPS_PR_PAT", "secret-pat")

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
    queue = TriggerEventQueue(worker_config.runtime_dir / "trigger-orchestrator-events.json")
    events = queue.list_pending(trigger_id="trigger-pr-feedback")
    assert len(events) == 1
    assert events[0].payload["session_id"] == "session-6070"
    assert events[0].payload["pr"] == {
        "provider": "azure_devops",
        "repo": "org/repo",
        "pr_id": "6070",
    }
    feedback_store_payload = json.loads(
        (worker_config.runtime_dir / "pr-feedback-store.json").read_text(encoding="utf-8")
    )
    assert "azure_devops:thread_comment:30830:1" in feedback_store_payload["feedback"]
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
        == "2026-04-08T05:33:41.433000Z"
    )


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
    from agent_sandbox.daemons import ado_issue_worker as ado_worker

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
    queue = TriggerEventQueue(worker_config.runtime_dir / "trigger-orchestrator-events.json")
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


def test_publish_directory_pr_feedback_events_uses_session_ado_project(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_sandbox.daemons import ado_issue_worker as ado_worker

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
    from agent_sandbox.daemons import ado_issue_worker as ado_worker

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
    queue = TriggerEventQueue(worker_config.runtime_dir / "trigger-orchestrator-events.json")
    events = queue.list_pending(trigger_id="trigger-pr-feedback")
    assert len(events) == 1
    assert events[0].payload["session_id"] == "session-200"
    assert events[0].payload["pr"]["pr_id"] == "200"
    cursor_payload = json.loads(
        (worker_config.runtime_dir / "trigger-cursors-pr-feedback.json").read_text(encoding="utf-8")
    )
    assert "azure_devops:org/repo:100" not in cursor_payload["trigger-pr-feedback"][
        "stream_cursors"
    ]
    assert (
        cursor_payload["trigger-pr-feedback"]["stream_cursors"]["azure_devops:org/repo:200"]
        == "2026-05-13T09:00:00Z"
    )


def test_publish_directory_pr_feedback_events_skips_ignored_feedback_ids(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_sandbox.daemons import ado_issue_worker as ado_worker

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
                    created_at="2026-04-08T05:35:01.273000Z",
                )
            ],
            next_cursor="2026-04-08T05:35:01.273000Z",
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
    queue = TriggerEventQueue(worker_config.runtime_dir / "trigger-orchestrator-events.json")
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


def test_publish_directory_pr_feedback_events_uses_configured_trigger_queue_path(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_sandbox.daemons import ado_issue_worker as ado_worker

    queue_path = worker_config.runtime_dir / "custom-trigger-events.json"
    trigger_worker_config = ado_worker.TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": str(worker_config.runtime_dir),
            "event_queue_store_path": str(queue_path),
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
    queue = TriggerEventQueue(queue_path)
    events = queue.list_pending(trigger_id="trigger-pr-feedback")
    assert len(events) == 1


def test_load_ado_pr_feedback_triggers_resolves_workflow_packs(worker_config: WorkerConfig) -> None:
    from agent_sandbox.daemons import ado_issue_worker as ado_worker

    project_dir = worker_config.gateway_config_path.parent
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
                        }
                    ],
                    "edges": [],
                },
            }
        ),
        encoding="utf-8",
    )

    triggers = ado_worker._load_ado_pr_feedback_triggers(worker_config)

    assert [trigger.id for trigger in triggers] == ["trigger-pr-feedback"]
    assert triggers[0].provider == "ado"


def test_load_ado_pr_completed_triggers_resolves_workflow_packs(
    worker_config: WorkerConfig,
) -> None:
    from agent_sandbox.daemons import ado_issue_worker as ado_worker

    project_dir = worker_config.gateway_config_path.parent
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
                        }
                    ],
                    "edges": [],
                },
            }
        ),
        encoding="utf-8",
    )

    triggers = ado_worker._load_ado_pr_completed_triggers(worker_config)

    assert [trigger.id for trigger in triggers] == ["trigger-pr-completed"]
    assert triggers[0].provider == "ado"


def test_resolve_directory_worker_config_uses_each_directorys_own_project_or_solution(
    worker_config: WorkerConfig,
) -> None:
    from agent_sandbox.daemons import ado_issue_worker as ado_worker

    directories_path = worker_config.runtime_dir / "directories.json"
    directories_path.write_text(
        json.dumps(
            {
                "root": {
                    "id": "root",
                    "name": "root",
                    "parent_id": None,
                    "created_at": "2026-03-28T00:00:00Z",
                    "metadata": {},
                },
                "example": {
                    "id": "example",
                    "name": "example-industry",
                    "parent_id": "root",
                    "created_at": "2026-03-28T00:00:00Z",
                    "metadata": {},
                },
                "app": {
                    "id": "app",
                    "name": "app",
                    "parent_id": "example",
                    "created_at": "2026-03-28T00:00:00Z",
                    "metadata": {"directory_config": {"domains": ["ado_worker"]}},
                },
                "other": {
                    "id": "other",
                    "name": "other-team",
                    "parent_id": "root",
                    "created_at": "2026-03-28T00:00:00Z",
                    "metadata": {},
                },
                "app": {
                    "id": "app",
                    "name": "app",
                    "parent_id": "other",
                    "created_at": "2026-03-28T00:00:00Z",
                    "metadata": {"directory_config": {"domains": ["ado_worker"]}},
                },
            }
        ),
        encoding="utf-8",
    )
    app_ado_dir = worker_config.runtime_dir / "directory-config" / "root" / "example-industry" / "app"
    app_ado_dir = worker_config.runtime_dir / "directory-config" / "root" / "other-team" / "app"
    app_ado_dir.mkdir(parents=True, exist_ok=True)
    app_ado_dir.mkdir(parents=True, exist_ok=True)
    (app_ado_dir / "ado-worker.json").write_text(
        json.dumps(
            {
                "ado_project_or_solution": "/repos/app/App.slnx",
                "ado_repo_source": "/repos/app",
                "ado_bootstrap_mssql": False,
            }
        ),
        encoding="utf-8",
    )
    (app_ado_dir / "ado-worker.json").write_text(
        json.dumps(
            {
                "ado_project_or_solution": "/repos/app/App.slnx",
                "ado_repo_source": "/repos/app",
                "ado_bootstrap_mssql": False,
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
            "directories_store_path": str(directories_path),
            "azure_devops_organization_url": "https://dev.azure.com/example",
            "azure_devops_project": "example",
            "azure_devops_pat_env": "ADO_WORK_ITEMS_AZURE_DEVOPS_PAT",
        }
    )
    configured = WorkerConfig(
        **{
            **worker_config.__dict__,
            "directories": ("root/example-industry/app", "root/other-team/app"),
        }
    )

    app_config = ado_worker._resolve_directory_worker_config(
        configured,
        runtime_gateway_config,
        "root/example-industry/app",
    )
    app_config = ado_worker._resolve_directory_worker_config(
        configured,
        runtime_gateway_config,
        "root/other-team/app",
    )

    assert app_config.directory_path == "root/example-industry/app"
    assert app_config.prewarm_target == "/repos/app/App.slnx"
    assert app_config.repo_path == "/repos/app"
    assert app_config.directory_path == "root/other-team/app"
    assert app_config.prewarm_target == "/repos/app/App.slnx"
    assert app_config.repo_path == "/repos/app"


def test_format_work_item_listing_uses_tab_separated_fields() -> None:
    line = format_work_item_listing(
        {
            "id": 42,
            "updated_date": "2026-03-21T00:01:00Z",
            "state": "Committed",
            "title": "Still pending",
        }
    )

    assert line == "42\t2026-03-21T00:01:00Z\tCommitted\tStill pending"


def test_run_worker_command_lists_unprocessed_items(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "agent_sandbox.daemons.ado_issue_worker.WorkerConfig.load_from_env", lambda: worker_config
    )
    monkeypatch.setattr("agent_sandbox.daemons.ado_issue_worker._require_env", lambda _name: "pat")
    monkeypatch.setattr(
        "agent_sandbox.daemons.ado_issue_worker._unprocessed_items_for_worker",
        lambda **kwargs: [
            {
                "id": 42,
                "updated_date": "2026-03-21T00:01:00Z",
                "state": "Committed",
                "title": "Still pending",
            }
        ],
    )

    run_worker_command(list_unprocessed=True)

    assert capsys.readouterr().out == "42\t2026-03-21T00:01:00Z\tCommitted\tStill pending\n"
