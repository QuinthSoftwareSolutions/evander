from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest
import yaml

from agent_sandbox.ado_issue_worker import (
    ProcessedWorkItemStore,
    WorkerConfig,
    _build_setup_command,
    _is_vm_already_exists_output,
    fetch_work_item_comments,
    filter_unprocessed_work_items,
    format_work_item_listing,
    gate_and_order_items,
    list_unprocessed_work_items,
    poll_once,
    process_work_item,
    run_worker_command,
    update_work_item_state,
)


@pytest.fixture
def worker_config(tmp_path: Path) -> WorkerConfig:
    return WorkerConfig(
        organization_url="https://dev.azure.com/example",
        project="example",
        pat_env_var_name="AZURE_DEVOPS_PAT",
        poll_interval_seconds=30.0,
        area_filters=(),
        team_filters=(),
        runtime_dir=tmp_path,
        yaml_output_dir=tmp_path / "yaml",
        setup_log_dir=tmp_path / "setup-logs",
        processed_store_path=tmp_path / "state" / "processed.json",
        setup_script_path=Path("scripts/setup_issue_vm.sh"),
        prewarm_target="My.slnx",
        repo_path=None,
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
        bootstrap_playwright=True,
        include_comments=True,
        comments_max_count=20,
        comment_text_max_chars=4000,
    )


def test_processed_store_mark_and_forget(worker_config: WorkerConfig) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)
    assert store.is_processed(123) is False

    store.mark_processed(
        work_item_id=123,
        yaml_path=worker_config.yaml_output_dir / "work-item-123.yaml",
        script_exit_code=0,
        output_snippet="ok",
    )
    assert store.is_processed(123) is True

    payload = json.loads(worker_config.processed_store_path.read_text(encoding="utf-8"))
    assert payload["123"]["script_exit_code"] == 0
    assert payload["123"]["script_output_snippet"] == "ok"

    assert store.forget(123) is True
    assert store.is_processed(123) is False


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
    )

    called = {"run": False}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        called["run"] = True
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.ado_issue_worker.subprocess.run", fake_run)

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


def test_process_work_item_marks_state_only_after_success(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)

    def failing_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args, returncode=12, stdout="nope", stderr="bad")

    monkeypatch.setattr("agent_sandbox.ado_issue_worker.subprocess.run", failing_run)

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

    monkeypatch.setattr("agent_sandbox.ado_issue_worker.subprocess.run", successful_run)

    succeeded = process_work_item(item, config=worker_config, store=store)
    assert succeeded is True
    assert store.is_processed(789) is True
    log_text = (worker_config.setup_log_dir / "work-item-789.log").read_text(encoding="utf-8")
    assert "===== " in log_text
    assert log_text.endswith("ok\n")


def test_update_work_item_state_uses_json_patch(worker_config: WorkerConfig, monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr("agent_sandbox.ado_issue_worker.subprocess.run", successful_run)
    monkeypatch.setattr("agent_sandbox.ado_issue_worker.update_work_item_state", fake_update_work_item_state)

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
    assert store.is_processed(790) is True
    assert state_updates == [(790, "In Progress")]


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

    monkeypatch.setattr("agent_sandbox.ado_issue_worker.subprocess.run", failing_run)
    monkeypatch.setattr("agent_sandbox.ado_issue_worker.update_work_item_state", fake_update_work_item_state)

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
        return subprocess.CompletedProcess(args=args, returncode=7, stdout="hello", stderr="goodbye")

    monkeypatch.setattr("agent_sandbox.ado_issue_worker.subprocess.run", failing_run)

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

    monkeypatch.setattr("agent_sandbox.ado_issue_worker.subprocess.run", already_exists_run)
    monkeypatch.setattr("agent_sandbox.ado_issue_worker.update_work_item_state", fake_update_work_item_state)

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
    assert store.is_processed(793) is True
    assert state_updates == [(793, "In Progress")]


def test_build_setup_command_includes_optional_arguments(tmp_path: Path) -> None:
    config = WorkerConfig(
        organization_url="https://dev.azure.com/example",
        project="example",
        pat_env_var_name="AZURE_DEVOPS_PAT",
        poll_interval_seconds=30.0,
        area_filters=(),
        team_filters=(),
        runtime_dir=tmp_path,
        yaml_output_dir=tmp_path / "yaml",
        setup_log_dir=tmp_path / "setup-logs",
        processed_store_path=tmp_path / "state" / "processed.json",
        setup_script_path=Path("scripts/setup_issue_vm.sh"),
        prewarm_target="real-target",
        repo_path="/tmp/repo",
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
        bootstrap_playwright=True,
        include_comments=True,
        comments_max_count=20,
        comment_text_max_chars=4000,
    )
    cmd = _build_setup_command(config, tmp_path / "work-item-10.yaml")
    assert cmd == [
        "scripts/setup_issue_vm.sh",
        "--dotnet",
        "--nuget",
        "--mssql",
        "--playwright",
        str(tmp_path / "work-item-10.yaml"),
        "real-target",
        "/tmp/repo",
        "sess-1",
        "agent",
    ]


def test_build_setup_command_omits_disabled_bootstraps_and_nuget_prewarm_target(tmp_path: Path) -> None:
    config = WorkerConfig(
        organization_url="https://dev.azure.com/example",
        project="example",
        pat_env_var_name="AZURE_DEVOPS_PAT",
        poll_interval_seconds=30.0,
        area_filters=(),
        team_filters=(),
        runtime_dir=tmp_path,
        yaml_output_dir=tmp_path / "yaml",
        setup_log_dir=tmp_path / "setup-logs",
        processed_store_path=tmp_path / "state" / "processed.json",
        setup_script_path=Path("scripts/setup_issue_vm.sh"),
        prewarm_target="real-target",
        repo_path="/tmp/repo",
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
        bootstrap_playwright=False,
        include_comments=True,
        comments_max_count=20,
        comment_text_max_chars=4000,
    )

    cmd = _build_setup_command(config, tmp_path / "work-item-10.yaml")
    assert cmd == [
        "scripts/setup_issue_vm.sh",
        "--mssql",
        str(tmp_path / "work-item-10.yaml"),
        "/tmp/repo",
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
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.ado_issue_worker.subprocess.run", successful_run)

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
    assert captured["timeout"] == worker_config.setup_timeout_seconds


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


def test_process_work_item_skips_previously_processed_id_on_subsequent_attempt(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)
    call_count = {"run": 0}

    def successful_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        call_count["run"] += 1
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.ado_issue_worker.subprocess.run", successful_run)

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

    monkeypatch.setattr("agent_sandbox.ado_issue_worker.subprocess.run", successful_run)

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


def test_setup_issue_vm_invocation_once_per_newly_eligible_item(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)
    calls: list[list[str]] = []

    def successful_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(args=[cmd], returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent_sandbox.ado_issue_worker.subprocess.run", successful_run)

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
    called_yaml_paths = {Path(next(arg for arg in cmd if arg.endswith(".yaml"))).name for cmd in calls}
    assert called_yaml_paths == {"work-item-501.yaml", "work-item-502.yaml"}


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
            "text": "abcde",
            "author": "Ada",
            "created_date": "2026-03-21T00:00:00Z",
            "modified_date": "2026-03-21T01:00:00Z",
        }
    ]


def test_poll_once_attaches_comments(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_sandbox.ado_issue_worker.fetch_candidate_ids",
        lambda **kwargs: [10],
    )
    monkeypatch.setattr(
        "agent_sandbox.ado_issue_worker.fetch_work_item_batch",
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
        "agent_sandbox.ado_issue_worker.fetch_work_item_comments",
        lambda **kwargs: [{"text": "Needs x", "author": "Ann", "created_date": "", "modified_date": ""}],
    )

    with httpx.Client() as client:
        items = poll_once(client=client, config=worker_config, auth_header="Basic token")

    assert len(items) == 1
    assert items[0]["comments"] == [{"text": "Needs x", "author": "Ann", "created_date": "", "modified_date": ""}]


def test_fetch_work_item_by_id_attaches_comments(
    worker_config: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_sandbox.ado_issue_worker.fetch_work_item_batch",
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
        "agent_sandbox.ado_issue_worker.fetch_work_item_comments",
        lambda **kwargs: [{"text": "Needs x", "author": "Ann", "created_date": "", "modified_date": ""}],
    )

    from agent_sandbox.ado_issue_worker import fetch_work_item_by_id

    with httpx.Client() as client:
        item = fetch_work_item_by_id(
            client=client,
            config=worker_config,
            auth_header="Basic token",
            work_item_id=10,
        )

    assert item is not None
    assert item["comments"] == [{"text": "Needs x", "author": "Ann", "created_date": "", "modified_date": ""}]


def test_filter_unprocessed_work_items_excludes_processed(worker_config: WorkerConfig) -> None:
    store = ProcessedWorkItemStore(worker_config.processed_store_path)
    store.mark_processed(
        work_item_id=10,
        yaml_path=worker_config.yaml_output_dir / "work-item-10.yaml",
        script_exit_code=0,
        output_snippet="done",
    )
    items = [
        {"id": 10, "title": "Picked up"},
        {"id": 11, "title": "Still pending"},
    ]

    filtered = filter_unprocessed_work_items(items, store)

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
    )
    monkeypatch.setattr(
        "agent_sandbox.ado_issue_worker.poll_once",
        lambda **kwargs: [
            {"id": 10, "title": "Picked up", "state": "Committed", "updated_date": "2026-03-21T00:00:00Z"},
            {"id": 11, "title": "Still pending", "state": "Committed", "updated_date": "2026-03-21T00:01:00Z"},
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
        {"id": 11, "title": "Still pending", "state": "Committed", "updated_date": "2026-03-21T00:01:00Z"}
    ]


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
    monkeypatch.setattr("agent_sandbox.ado_issue_worker.WorkerConfig.load_from_env", lambda: worker_config)
    monkeypatch.setattr("agent_sandbox.ado_issue_worker._require_env", lambda _name: "pat")
    monkeypatch.setattr(
        "agent_sandbox.ado_issue_worker.list_unprocessed_work_items",
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
