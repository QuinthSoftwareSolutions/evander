from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import GatewayConfig


@dataclass(frozen=True)
class OwnershipMigrationSummary:
    changed_files: int
    changed_records: int
    per_file_changes: dict[str, int]


def migrate_directory_ownership_defaults(config: GatewayConfig) -> OwnershipMigrationSummary:
    per_file_changes: dict[str, int] = {}

    sessions_changed = _backfill_top_level_directory_path(config.session_store_path)
    if sessions_changed:
        per_file_changes[str(config.session_store_path)] = sessions_changed

    local_issues_path = config.runtime_dir / "local-issues.json"
    local_issues_changed = _backfill_top_level_directory_path(local_issues_path)
    if local_issues_changed:
        per_file_changes[str(local_issues_path)] = local_issues_changed

    pr_map_changed = _backfill_nested_directory_path(config.pr_map_path, container_key="entries")
    if pr_map_changed:
        per_file_changes[str(config.pr_map_path)] = pr_map_changed

    ado_processed_path = config.runtime_dir / "ado-processed-work-items.json"
    ado_processed_changed = _backfill_top_level_directory_path(ado_processed_path)
    if ado_processed_changed:
        per_file_changes[str(ado_processed_path)] = ado_processed_changed

    return OwnershipMigrationSummary(
        changed_files=len(per_file_changes),
        changed_records=sum(per_file_changes.values()),
        per_file_changes=per_file_changes,
    )


def _backfill_top_level_directory_path(path: Path) -> int:
    return _backfill_records(path, records_key=None)


def _backfill_nested_directory_path(path: Path, *, container_key: str) -> int:
    return _backfill_records(path, records_key=container_key)


def _backfill_records(path: Path, *, records_key: str | None) -> int:
    if not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(payload, dict):
        return 0

    records: Any = payload if records_key is None else payload.get(records_key)
    if not isinstance(records, dict):
        return 0

    changed = 0
    for raw_record in records.values():
        if not isinstance(raw_record, dict):
            continue
        directory_path = raw_record.get("directory_path")
        if isinstance(directory_path, str) and directory_path.strip():
            continue
        raw_record["directory_path"] = "root"
        changed += 1

    if changed:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return changed
