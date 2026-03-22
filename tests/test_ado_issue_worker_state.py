from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from agent_sandbox.ado_issue_worker import (
    ProcessedWorkItemStore,
    WorkerConfig,
    _build_setup_command,
    gate_and_order_items,
    process_work_item,
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
    )
    cmd = _build_setup_command(config, tmp_path / "work-item-10.yaml")
    assert cmd == [
        "scripts/setup_issue_vm.sh",
        "--dotnet",
        "--nuget",
        "--mssql",
        str(tmp_path / "work-item-10.yaml"),
        "real-target",
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
    called_yaml_paths = {Path(cmd[4]).name for cmd in calls}
    assert called_yaml_paths == {"work-item-501.yaml", "work-item-502.yaml"}
