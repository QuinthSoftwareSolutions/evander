from __future__ import annotations

import json
from pathlib import Path

from agent_sandbox.bootstrap import load_host_bootstrap_config, load_runtime_gateway_config
from agent_sandbox.host_config import build_runtime_gateway_config
from agent_sandbox.issue_service import IssueService
from agent_sandbox.migrations import (
    LOCAL_ISSUE_MIGRATION_MARKER_RELATIVE_PATH,
    migrate_local_issues_to_unified_schema,
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
                "enable_directory_scoped_config: false",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def test_local_issue_migration_normalizes_records_imports_ado_and_preserves_counter(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    (runtime_dir / "local-issues.json").write_text(
        json.dumps(
            {
                "5": {
                    "issue_number": "5",
                    "title": "Legacy local",
                    "body": "",
                    "yaml_path": str(runtime_dir / "issues" / "5.yaml"),
                    "source": "local_gui",
                },
                "42": {
                    "issue_number": "42",
                    "title": "Proxy placeholder",
                    "body": "",
                    "yaml_path": str(runtime_dir / "issues" / "42.yaml"),
                    "source": "local_gui",
                    "metadata": {},
                },
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "local-issue-counter.txt").write_text("2\n", encoding="utf-8")

    ado_dir = runtime_dir / "ado-work-items"
    ado_dir.mkdir(parents=True, exist_ok=True)
    (ado_dir / "work-item-42.yaml").write_text(
        "\n".join(
            [
                "issue_number: 42",
                "title: Imported proxy issue",
                "description: Imported description",
                "state: Active",
                "url: https://dev.azure.com/org/proj/_workitems/edit/42",
                "source:",
                "  organization: org",
                "  project: proj",
                "  work_item_url: https://dev.azure.com/org/proj/_workitems/edit/42",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    config = build_runtime_gateway_config(
        load_host_bootstrap_config(runtime_dir=runtime_dir, legacy_config_path=config_path)
    )
    summary = migrate_local_issues_to_unified_schema(config)

    assert summary.scanned_records == 2
    assert summary.updated_records >= 1
    assert summary.imported_ado_artifacts == 1
    assert summary.marker_path.exists()

    payload = json.loads((runtime_dir / "local-issues.json").read_text(encoding="utf-8"))
    assert payload["5"]["directory_path"] == "root"
    assert payload["42"]["source"] == "proxy_azure_devops"
    assert payload["42"]["metadata"]["remote"]["provider"] == "azure_devops"
    assert payload["42"]["metadata"]["remote"]["issue_id"] == "42"
    assert payload["42"]["metadata"]["proxy_cache"]["state"] == "Active"

    # Counter is preserved for manual local issue creation and does not jump to proxy IDs.
    assert (runtime_dir / "local-issue-counter.txt").read_text(encoding="utf-8").strip() == "5"


def test_local_issue_migration_is_idempotent_with_marker(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    config_path = write_config(tmp_path)
    config = load_runtime_gateway_config(runtime_dir=runtime_dir, legacy_config_path=config_path)

    marker_path = runtime_dir / LOCAL_ISSUE_MIGRATION_MARKER_RELATIVE_PATH
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text("{}", encoding="utf-8")

    summary = migrate_local_issues_to_unified_schema(config)

    assert summary.already_applied is True
    assert summary.updated_records == 0
    assert summary.imported_ado_artifacts == 0


def test_issue_service_reads_legacy_proxy_metadata_shape() -> None:
    service = IssueService(config_resolver=object())

    resolved = service.resolve_proxy_target(
        issue_number="101",
        issue_source="local_gui",
        metadata={
            "provider": "azure_devops",
            "issue_id": "9001",
        },
    )

    assert resolved == ("azure_devops", "9001")


def test_local_issue_migration_upgrades_legacy_proxy_metadata_shape(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "local-issues.json").write_text(
        json.dumps(
            {
                "9001": {
                    "issue_number": "9001",
                    "title": "Legacy proxy issue",
                    "body": "",
                    "yaml_path": str(runtime_dir / "issues" / "9001.yaml"),
                    "source": "azure_devops",
                    "metadata": {
                        "provider": "azure_devops",
                        "issue_id": "9001",
                        "work_item_url": "https://dev.azure.com/example/sample/_workitems/edit/9001",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "local-issue-counter.txt").write_text("12\n", encoding="utf-8")

    config = build_runtime_gateway_config(
        load_host_bootstrap_config(runtime_dir=runtime_dir, legacy_config_path=config_path)
    )
    summary = migrate_local_issues_to_unified_schema(config, import_recent_ado_artifacts=False)

    assert summary.already_applied is False
    assert summary.updated_records == 1
    payload = json.loads((runtime_dir / "local-issues.json").read_text(encoding="utf-8"))
    normalized = payload["9001"]
    assert normalized["source"] == "proxy_azure_devops"
    assert normalized["metadata"]["remote"]["provider"] == "azure_devops"
    assert normalized["metadata"]["remote"]["issue_id"] == "9001"
    assert normalized["metadata"]["remote"]["work_item_url"].endswith("/9001")
