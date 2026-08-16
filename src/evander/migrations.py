from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evander.config import GatewayConfig
from evander.directory_config import (
    DIRECTORY_CONFIG_METADATA_DOMAINS_KEY,
    DIRECTORY_CONFIG_METADATA_KEY,
    AdoWorkerDirectoryConfig,
    DIRECTORY_CONFIG_DOMAIN_FILENAMES,
    DirectoryConfigStore,
    EffectiveIssueSetupDirectoryConfig,
    PrTemplateDirectoryConfig,
    get_domain_base_config,
)
from evander.directories import DirectoryStore


@dataclass(frozen=True)
class OwnershipMigrationSummary:
    changed_files: int
    changed_records: int
    per_file_changes: dict[str, int]


@dataclass(frozen=True)
class WakeConfigMigrationSummary:
    scanned_files: int
    written_files: int
    skipped_files: int
    removed_legacy_files: int
    metadata_updated: int


@dataclass(frozen=True)
class DomainMigrationResult:
    domain: str
    status: str
    path: Path


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


def migrate_heartbeat_config_to_wake(
    config: GatewayConfig, *, overwrite: bool = False
) -> WakeConfigMigrationSummary:
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    config_store = DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir)
    config_store.ensure_root()

    scanned_files = 0
    written_files = 0
    skipped_files = 0
    removed_legacy_files = 0
    default_wake_payload = DirectoryConfigStore.validate_domain_payload(
        "wake", get_domain_base_config(config, "wake")
    )
    for legacy_path in sorted(config_store.root_path.rglob("heartbeat.json")):
        scanned_files += 1
        try:
            raw_payload = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            skipped_files += 1
            continue
        if not isinstance(raw_payload, dict):
            skipped_files += 1
            continue

        wake_payload = _heartbeat_payload_to_wake(raw_payload)
        try:
            normalized = DirectoryConfigStore.validate_domain_payload("wake", wake_payload)
        except ValueError:
            skipped_files += 1
            continue

        wake_path = legacy_path.with_name(DIRECTORY_CONFIG_DOMAIN_FILENAMES["wake"])
        if wake_path.exists():
            try:
                existing_payload = json.loads(wake_path.read_text(encoding="utf-8"))
                existing_normalized = DirectoryConfigStore.validate_domain_payload(
                    "wake", existing_payload
                )
            except (OSError, json.JSONDecodeError, ValueError):
                existing_normalized = None
            if existing_normalized != normalized and not overwrite:
                if existing_normalized != default_wake_payload:
                    skipped_files += 1
                    continue

        relative_directory = legacy_path.parent.relative_to(config_store.root_path)
        canonical_path = "/".join(relative_directory.parts) or "root"
        config_store.write(canonical_path, domain="wake", payload=normalized)
        written_files += 1
        legacy_path.unlink()
        removed_legacy_files += 1

    metadata_updated = _replace_directory_config_metadata_domain(
        directory_store, old_domain="heartbeat", new_domain="wake"
    )
    return WakeConfigMigrationSummary(
        scanned_files=scanned_files,
        written_files=written_files,
        skipped_files=skipped_files,
        removed_legacy_files=removed_legacy_files,
        metadata_updated=metadata_updated,
    )


def _heartbeat_payload_to_wake(payload: dict[str, Any]) -> dict[str, Any]:
    migrated: dict[str, Any] = {}
    if "pr_feedback_resume_prompt" in payload:
        migrated["pr_feedback_resume_prompt"] = payload["pr_feedback_resume_prompt"]
    elif "host_heartbeat_pr_resume_prompt" in payload:
        migrated["pr_feedback_resume_prompt"] = payload["host_heartbeat_pr_resume_prompt"]
    if "discord_resume_prompt" in payload:
        migrated["discord_resume_prompt"] = payload["discord_resume_prompt"]
    return migrated


def _replace_directory_config_metadata_domain(
    directory_store: DirectoryStore, *, old_domain: str, new_domain: str
) -> int:
    changed = 0

    def update(items):
        nonlocal changed
        for directory_id, directory in list(items.items()):
            metadata = dict(directory.metadata)
            config_metadata = metadata.get(DIRECTORY_CONFIG_METADATA_KEY)
            if not isinstance(config_metadata, dict):
                continue
            raw_domains = config_metadata.get(DIRECTORY_CONFIG_METADATA_DOMAINS_KEY, [])
            if not isinstance(raw_domains, list) or old_domain not in {
                str(value).strip() for value in raw_domains
            }:
                continue
            domains = {
                new_domain if str(value).strip() == old_domain else str(value).strip()
                for value in raw_domains
                if str(value).strip()
            }
            domains = {domain for domain in domains if domain in DIRECTORY_CONFIG_DOMAIN_FILENAMES}
            if domains:
                config_metadata[DIRECTORY_CONFIG_METADATA_DOMAINS_KEY] = sorted(domains)
                metadata[DIRECTORY_CONFIG_METADATA_KEY] = config_metadata
            else:
                metadata.pop(DIRECTORY_CONFIG_METADATA_KEY, None)
            items[directory_id] = directory.model_copy(update={"metadata": metadata})
            changed += 1

    directory_store.update_all(update)
    return changed


def migrate_ado_worker_env_to_root(
    config: GatewayConfig,
    *,
    env_file: Path | None = None,
    overwrite: bool = False,
) -> DomainMigrationResult:
    env_values = _load_optional_env_file(env_file) if env_file is not None else {}
    payload = AdoWorkerDirectoryConfig(
        ado_area_filters=_csv_values(_merged_env_value("ADO_AREA_FILTERS", env_values)),
        ado_team_filters=_csv_values(_merged_env_value("ADO_TEAM_FILTERS", env_values)),
        ado_repo_source=_merged_env_value("ADO_REPO_PATH", env_values) or None,
    ).model_dump(mode="json", exclude_none=True)
    return _migrate_domain_payload_to_root(
        config, domain="ado_worker", payload=payload, overwrite=overwrite
    )


def migrate_issue_setup_env_to_root(
    config: GatewayConfig,
    *,
    env_file: Path | None = None,
    overwrite: bool = False,
) -> DomainMigrationResult:
    env_values = _load_optional_env_file(env_file) if env_file is not None else {}
    payload = EffectiveIssueSetupDirectoryConfig(
        git_host=_merged_env_value("EVANDER_GIT_HOST", env_values) or "192.168.250.1",
        git_user=_merged_env_value("EVANDER_GIT_USER", env_values)
        or os.environ.get("USER", "agent"),
        git_ssh_alias=_merged_env_value("EVANDER_GIT_SSH_ALIAS", env_values) or "evander-git-host",
        default_model=_merged_env_value("AGENT_DEFAULT_MODEL", env_values) or None,
        post_exec_verification_prompt=_merged_env_value(
            "AGENT_POST_EXEC_VERIFICATION_PROMPT", env_values
        )
        or None,
        enable_guest_heartbeat=_bool_value(
            _merged_env_value("ENABLE_GUEST_HEARTBEAT", env_values), default=False
        ),
        guest_heartbeat_prompt=(
            _merged_env_value("GUEST_HEARTBEAT_PROMPT", env_values)
            or "Resume work for this session. Check for pending feedback first, then continue the most recent task."
        ),
        issue_setup_azure_devops_pr_pat_env=(
            _merged_env_value("EVANDER_AZURE_DEVOPS_PR_PAT_ENV", env_values)
            or "AZURE_DEVOPS_PR_PAT"
        ),
    ).model_dump(mode="json", exclude_none=True)
    return _migrate_domain_payload_to_root(
        config, domain="issue_setup", payload=payload, overwrite=overwrite
    )


def migrate_pr_template_to_root(
    config: GatewayConfig,
    *,
    template_file: Path | None = None,
    overwrite: bool = False,
) -> DomainMigrationResult:
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    config_store = DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir)
    config_store.ensure_root()
    path = config_store.root_path / "root" / DIRECTORY_CONFIG_DOMAIN_FILENAMES["pr_template"]
    if template_file is None or not template_file.exists():
        try:
            existing_payload = config_store.read("root", domain="pr_template")
        except ValueError as exc:
            if "directory config file is missing" not in str(exc):
                raise
            existing_payload = None
        if existing_payload is None:
            return DomainMigrationResult(domain="pr_template", status="skipped", path=path)
        return DomainMigrationResult(domain="pr_template", status="unchanged", path=path)

    payload = PrTemplateDirectoryConfig(
        pr_template_markdown=template_file.read_text(encoding="utf-8")
    ).model_dump(mode="json", exclude_none=True)
    return _migrate_domain_payload_to_root(
        config, domain="pr_template", payload=payload, overwrite=overwrite
    )


def _read_root_domain_file_without_metadata(
    config_store: DirectoryConfigStore, *, domain: str
) -> dict[str, Any] | None:
    path = config_store.root_path / "root" / DIRECTORY_CONFIG_DOMAIN_FILENAMES[domain]
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    return config_store.validate_domain_payload(domain, payload)


def _migrate_domain_payload_to_root(
    config: GatewayConfig,
    *,
    domain: str,
    payload: dict[str, Any],
    overwrite: bool,
) -> DomainMigrationResult:
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    config_store = DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir)
    config_store.ensure_root()
    path = config_store.root_path / "root" / DIRECTORY_CONFIG_DOMAIN_FILENAMES[domain]
    try:
        existing_payload = config_store.read("root", domain=domain)
    except ValueError as exc:
        if "directory config file is missing" not in str(exc):
            raise
        existing_payload = _read_root_domain_file_without_metadata(config_store, domain=domain)
    if existing_payload is None:
        config_store.write("root", domain=domain, payload=payload)
        return DomainMigrationResult(domain=domain, status="written", path=path)
    if existing_payload == payload:
        return DomainMigrationResult(domain=domain, status="unchanged", path=path)
    if overwrite:
        config_store.write("root", domain=domain, payload=payload)
        return DomainMigrationResult(domain=domain, status="written", path=path)
    return DomainMigrationResult(domain=domain, status="skipped", path=path)


def _load_optional_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


def _merged_env_value(name: str, env_values: dict[str, str]) -> str:
    if name in os.environ:
        return os.environ.get(name, "").strip()
    return env_values.get(name, "").strip()


def _bool_value(raw: str, *, default: bool) -> bool:
    normalized = raw.strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "on"}


def _csv_values(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _resolve_present_secret_env_name(
    env_values: dict[str, str],
    *,
    preferred: str,
    fallback: str | None,
) -> str | None:
    if preferred in env_values:
        return preferred
    if fallback is not None and fallback in env_values:
        return fallback
    if _merged_env_value(preferred, env_values):
        return preferred
    if fallback is not None and _merged_env_value(fallback, env_values):
        return fallback
    return None


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
