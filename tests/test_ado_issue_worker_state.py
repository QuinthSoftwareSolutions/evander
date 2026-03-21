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
            "tags": ["true-agent"],
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
        "tags": ["true-agent"],
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
            "tags": ["true-agent"],
            "state": "Committed",
            "url": "https://example/1001",
            "updated_date": "2026-03-21T00:00:00Z",
        },
        config=worker_config,
        store=store,
    )

    assert succeeded is True
    assert captured["timeout"] == worker_config.setup_timeout_seconds
