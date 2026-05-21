from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from agent_sandbox.config import GatewayConfig
from agent_sandbox.directory_config import (
    DIRECTORY_CONFIG_METADATA_DOMAINS_KEY,
    DIRECTORY_CONFIG_METADATA_KEY,
    AdoWorkerDirectoryConfig,
    DIRECTORY_CONFIG_DOMAIN_FILENAMES,
    DirectoryConfigStore,
    EffectiveIssueSetupDirectoryConfig,
    PrTemplateDirectoryConfig,
    get_domain_base_config,
)
from agent_sandbox.directories import DirectoryStore
from agent_sandbox.host_config import HostConfig, save_host_config


@dataclass(frozen=True)
class OwnershipMigrationSummary:
    changed_files: int
    changed_records: int
    per_file_changes: dict[str, int]


@dataclass(frozen=True)
class RootConfigMigrationSummary:
    host_config_written: bool
    written_domains: list[str]
    skipped_domains: list[str]
    unchanged_domains: list[str]


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


@dataclass(frozen=True)
class LocalIssueSchemaMigrationSummary:
    version: str
    already_applied: bool
    scanned_records: int
    updated_records: int
    imported_ado_artifacts: int
    counter_rewritten: bool
    marker_path: Path


GLOBAL_DIRECTORY_CONFIG_DOMAINS = (
    "policy",
    "providers",
    "email",
    "wake",
    "vm",
    "ado_worker",
    "issue_setup",
)
LOCAL_ISSUE_SCHEMA_VERSION = "2026-03-28-local-issues-v2"
LOCAL_ISSUE_MIGRATION_MARKER_RELATIVE_PATH = Path("migrations/local-issues-schema-v2.json")


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


def migrate_local_issues_to_unified_schema(
    config: GatewayConfig,
    *,
    import_recent_ado_artifacts: bool = True,
    ado_artifact_scan_limit: int = 200,
) -> LocalIssueSchemaMigrationSummary:
    marker_path = config.runtime_dir / LOCAL_ISSUE_MIGRATION_MARKER_RELATIVE_PATH
    if marker_path.exists():
        return LocalIssueSchemaMigrationSummary(
            version=LOCAL_ISSUE_SCHEMA_VERSION,
            already_applied=True,
            scanned_records=0,
            updated_records=0,
            imported_ado_artifacts=0,
            counter_rewritten=False,
            marker_path=marker_path,
        )

    local_issues_path = config.runtime_dir / "local-issues.json"
    if not local_issues_path.exists():
        _write_local_issue_migration_marker(
            marker_path, scanned_records=0, updated_records=0, imported_ado_artifacts=0
        )
        return LocalIssueSchemaMigrationSummary(
            version=LOCAL_ISSUE_SCHEMA_VERSION,
            already_applied=False,
            scanned_records=0,
            updated_records=0,
            imported_ado_artifacts=0,
            counter_rewritten=_reconcile_local_issue_counter(config, records={}),
            marker_path=marker_path,
        )

    try:
        payload = json.loads(local_issues_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return LocalIssueSchemaMigrationSummary(
            version=LOCAL_ISSUE_SCHEMA_VERSION,
            already_applied=False,
            scanned_records=0,
            updated_records=0,
            imported_ado_artifacts=0,
            counter_rewritten=False,
            marker_path=marker_path,
        )
    if not isinstance(payload, dict):
        return LocalIssueSchemaMigrationSummary(
            version=LOCAL_ISSUE_SCHEMA_VERSION,
            already_applied=False,
            scanned_records=0,
            updated_records=0,
            imported_ado_artifacts=0,
            counter_rewritten=False,
            marker_path=marker_path,
        )

    scanned_records = 0
    updated_records = 0
    normalized_records: dict[str, dict[str, Any]] = {}
    for key, raw_record in payload.items():
        if not isinstance(raw_record, dict):
            normalized_records[key] = raw_record
            continue
        scanned_records += 1
        normalized = _normalize_local_issue_record(raw_record)
        if normalized != raw_record:
            updated_records += 1
        normalized_records[key] = normalized

    imported_ado_artifacts = 0
    if import_recent_ado_artifacts:
        imported_ado_artifacts = _import_recent_ado_yaml_into_proxy_records(
            config.runtime_dir,
            records=normalized_records,
            limit=ado_artifact_scan_limit,
        )
        updated_records += imported_ado_artifacts

    if normalized_records != payload:
        local_issues_path.write_text(
            json.dumps(normalized_records, indent=2, sort_keys=True), encoding="utf-8"
        )

    counter_rewritten = _reconcile_local_issue_counter(config, records=normalized_records)
    _write_local_issue_migration_marker(
        marker_path,
        scanned_records=scanned_records,
        updated_records=updated_records,
        imported_ado_artifacts=imported_ado_artifacts,
    )
    return LocalIssueSchemaMigrationSummary(
        version=LOCAL_ISSUE_SCHEMA_VERSION,
        already_applied=False,
        scanned_records=scanned_records,
        updated_records=updated_records,
        imported_ado_artifacts=imported_ado_artifacts,
        counter_rewritten=counter_rewritten,
        marker_path=marker_path,
    )


def migrate_global_config_to_root(
    config: GatewayConfig, *, overwrite: bool = False
) -> RootConfigMigrationSummary:
    host_config = HostConfig.from_legacy_gateway_config(config)
    host_config_path = save_host_config(host_config)
    written_domains: list[str] = []
    skipped_domains: list[str] = []
    unchanged_domains: list[str] = []

    for domain in GLOBAL_DIRECTORY_CONFIG_DOMAINS:
        desired_payload = get_domain_base_config(config, domain)
        summary = migrate_root_domain_payload(
            config, domain=domain, desired_payload=desired_payload, overwrite=overwrite
        )
        written_domains.extend(summary.written_domains)
        skipped_domains.extend(summary.skipped_domains)
        unchanged_domains.extend(summary.unchanged_domains)

    return RootConfigMigrationSummary(
        host_config_written=host_config_path.exists(),
        written_domains=written_domains,
        skipped_domains=skipped_domains,
        unchanged_domains=unchanged_domains,
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
        git_root=_merged_env_value("AGENT_SANDBOX_GIT_ROOT", env_values) or "git-remotes",
        git_host=_merged_env_value("AGENT_SANDBOX_GIT_HOST", env_values) or "192.168.250.1",
        git_user=_merged_env_value("AGENT_SANDBOX_GIT_USER", env_values)
        or os.environ.get("USER", "agent"),
        git_ssh_alias=_merged_env_value("AGENT_SANDBOX_GIT_SSH_ALIAS", env_values)
        or "agent-sandbox-git-host",
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
            _merged_env_value("AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV", env_values)
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


def migrate_root_domain_payload(
    config: GatewayConfig,
    *,
    domain: str,
    desired_payload: dict[str, Any],
    overwrite: bool = False,
) -> RootConfigMigrationSummary:
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    config_store = DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir)
    config_store.ensure_root()

    discovered_root_payload = False
    try:
        existing_payload = config_store.read("root", domain=domain)
    except ValueError as exc:
        if "directory config file is missing" not in str(exc):
            raise
        existing_payload = _read_root_domain_file_without_metadata(config_store, domain=domain)
        discovered_root_payload = existing_payload is not None
    if existing_payload is None:
        existing_payload = _read_root_domain_file_without_metadata(config_store, domain=domain)
        discovered_root_payload = existing_payload is not None
    if discovered_root_payload and existing_payload is not None:
        config_store.write("root", domain=domain, payload=existing_payload)
    if existing_payload is None:
        config_store.write("root", domain=domain, payload=desired_payload)
        return RootConfigMigrationSummary(False, [domain], [], [])
    if existing_payload == desired_payload:
        return RootConfigMigrationSummary(False, [], [], [domain])
    if overwrite:
        config_store.write("root", domain=domain, payload=desired_payload)
        return RootConfigMigrationSummary(False, [domain], [], [])
    return RootConfigMigrationSummary(False, [], [domain], [])


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


def _normalize_local_issue_record(raw_record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(raw_record)
    issue_number = str(normalized.get("issue_number") or "").strip()
    normalized["issue_number"] = issue_number
    normalized["directory_path"] = str(normalized.get("directory_path") or "root").strip() or "root"

    metadata = normalized.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata = dict(metadata)

    yaml_payload = _load_yaml_payload(normalized.get("yaml_path"))
    inferred_remote = _infer_remote_reference(issue_number, metadata, yaml_payload)
    source = str(normalized.get("source") or "").strip()
    if inferred_remote is not None:
        metadata["remote"] = inferred_remote
        if source in {"", "local_gui", "local", "azure_devops", "ado_proxy"}:
            source = "proxy_azure_devops"
    if not source:
        source = "local_gui"
    normalized["source"] = source

    existing_proxy_cache = metadata.get("proxy_cache")
    if not isinstance(existing_proxy_cache, dict):
        existing_proxy_cache = {}
    inferred_proxy_cache = _infer_proxy_cache(normalized, yaml_payload)
    merged_proxy_cache = {**existing_proxy_cache, **inferred_proxy_cache}
    if _remote_issue_id(metadata) and merged_proxy_cache:
        metadata["proxy_cache"] = merged_proxy_cache

    normalized["metadata"] = metadata
    return normalized


def _load_yaml_payload(yaml_path: Any) -> dict[str, Any]:
    if not isinstance(yaml_path, str) or not yaml_path.strip():
        return {}
    path = Path(yaml_path)
    if not path.exists():
        return {}
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _infer_remote_reference(
    issue_number: str,
    metadata: dict[str, Any],
    yaml_payload: dict[str, Any],
) -> dict[str, str] | None:
    existing_remote = metadata.get("remote")
    if isinstance(existing_remote, dict):
        provider = str(existing_remote.get("provider") or "").strip() or "azure_devops"
        remote_issue_id = str(existing_remote.get("issue_id") or issue_number).strip()
        if remote_issue_id:
            return {
                "provider": provider,
                "issue_id": remote_issue_id,
                "organization": str(existing_remote.get("organization") or "").strip(),
                "project": str(existing_remote.get("project") or "").strip(),
                "work_item_url": str(existing_remote.get("work_item_url") or "").strip(),
            }

    provider = str(metadata.get("provider") or "").strip()
    legacy_remote_issue_id = str(metadata.get("issue_id") or "").strip()
    if provider and legacy_remote_issue_id:
        return {
            "provider": provider,
            "issue_id": legacy_remote_issue_id,
            "work_item_url": str(metadata.get("work_item_url") or "").strip(),
        }

    source_payload = yaml_payload.get("source")
    if isinstance(source_payload, dict):
        work_item_url = str(source_payload.get("work_item_url") or "").strip()
        inferred_issue_id = _extract_ado_work_item_id(work_item_url) or issue_number
        if inferred_issue_id:
            return {
                "provider": "azure_devops",
                "issue_id": inferred_issue_id,
                "organization": str(source_payload.get("organization") or "").strip(),
                "project": str(source_payload.get("project") or "").strip(),
                "work_item_url": work_item_url,
            }

    for url_key in ("work_item_url", "url"):
        inferred_issue_id = _extract_ado_work_item_id(str(yaml_payload.get(url_key) or "").strip())
        if inferred_issue_id:
            return {"provider": "azure_devops", "issue_id": inferred_issue_id}
    return None


def _infer_proxy_cache(record: dict[str, Any], yaml_payload: dict[str, Any]) -> dict[str, Any]:
    proxy_cache: dict[str, Any] = {}
    title = str(yaml_payload.get("title") or record.get("title") or "").strip()
    description = str(
        yaml_payload.get("description") or yaml_payload.get("body") or record.get("body") or ""
    ).strip()
    state = str(yaml_payload.get("state") or "").strip()
    url = str(yaml_payload.get("url") or "").strip()
    updated_at = str(
        yaml_payload.get("updated_date") or yaml_payload.get("changed_date") or ""
    ).strip()
    comments = yaml_payload.get("comments")

    if title:
        proxy_cache["title"] = title
    if description:
        proxy_cache["description"] = description
    if state:
        proxy_cache["state"] = state
    if url:
        proxy_cache["url"] = url
    if updated_at:
        proxy_cache["updated_at"] = updated_at
    if isinstance(comments, list):
        proxy_cache["comments"] = comments
    return proxy_cache


def _import_recent_ado_yaml_into_proxy_records(
    runtime_dir: Path, *, records: dict[str, Any], limit: int
) -> int:
    ado_dir = runtime_dir / "ado-work-items"
    if not ado_dir.exists():
        return 0
    yaml_paths = sorted(ado_dir.glob("**/*.yaml"), key=_safe_mtime, reverse=True)[: max(limit, 0)]
    if not yaml_paths:
        return 0

    changed = 0
    by_issue_number: dict[str, dict[str, Any]] = {}
    by_remote_issue_id: dict[str, dict[str, Any]] = {}
    for raw_record in records.values():
        if not isinstance(raw_record, dict):
            continue
        issue_number = str(raw_record.get("issue_number") or "").strip()
        if issue_number:
            by_issue_number[issue_number] = raw_record
        remote_issue_id = _remote_issue_id(raw_record.get("metadata"))
        if remote_issue_id:
            by_remote_issue_id[remote_issue_id] = raw_record

    for yaml_path in yaml_paths:
        payload = _load_yaml_payload(str(yaml_path))
        if not payload:
            continue
        issue_id = str(payload.get("issue_number") or "").strip() or _extract_ado_work_item_id(
            yaml_path.name
        )
        if not issue_id:
            continue
        target = by_issue_number.get(issue_id) or by_remote_issue_id.get(issue_id)
        if target is None:
            continue
        before = json.dumps(target, sort_keys=True, default=str)
        normalized_target = _normalize_local_issue_record(target)
        metadata = dict(
            normalized_target.get("metadata")
            if isinstance(normalized_target.get("metadata"), dict)
            else {}
        )
        metadata["remote"] = {
            **(metadata.get("remote") if isinstance(metadata.get("remote"), dict) else {}),
            **(
                _infer_remote_reference(issue_id, metadata, payload)
                or {"provider": "azure_devops", "issue_id": issue_id}
            ),
        }
        if normalized_target.get("source") in {"", "local_gui", "local"}:
            normalized_target["source"] = "proxy_azure_devops"
        proxy_cache = dict(
            metadata.get("proxy_cache") if isinstance(metadata.get("proxy_cache"), dict) else {}
        )
        proxy_cache.update(_infer_proxy_cache(normalized_target, payload))
        metadata["proxy_cache"] = proxy_cache
        normalized_target["metadata"] = metadata
        target.clear()
        target.update(normalized_target)
        by_remote_issue_id[issue_id] = target
        after = json.dumps(target, sort_keys=True, default=str)
        if before != after:
            changed += 1
    return changed


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _remote_issue_id(metadata: Any) -> str:
    if not isinstance(metadata, dict):
        return ""
    remote = metadata.get("remote")
    if not isinstance(remote, dict):
        return ""
    return str(remote.get("issue_id") or "").strip()


def _extract_ado_work_item_id(raw: str) -> str:
    candidate = str(raw).strip()
    if not candidate:
        return ""
    if candidate.isdigit():
        return candidate
    patterns = (
        r"/workItems/edit/([0-9]+)",
        r"\bwork-item-([0-9]+)\.ya?ml\b",
        r"\b([0-9]{2,})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, candidate, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _reconcile_local_issue_counter(config: GatewayConfig, *, records: dict[str, Any]) -> bool:
    counter_path = config.runtime_dir / "local-issue-counter.txt"
    existing = 0
    if counter_path.exists():
        try:
            existing = int(counter_path.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            existing = 0
    max_manual_issue_number = 0
    for raw_record in records.values():
        if not isinstance(raw_record, dict):
            continue
        source = str(raw_record.get("source") or "").strip()
        if source in {"proxy_azure_devops", "azure_devops", "ado_proxy"}:
            continue
        issue_number = str(raw_record.get("issue_number") or "").strip()
        if issue_number.isdigit():
            max_manual_issue_number = max(max_manual_issue_number, int(issue_number))
    desired = max(existing, max_manual_issue_number)
    if existing == desired and counter_path.exists():
        return False
    counter_path.parent.mkdir(parents=True, exist_ok=True)
    counter_path.write_text(f"{desired}\n", encoding="utf-8")
    return True


def _write_local_issue_migration_marker(
    marker_path: Path,
    *,
    scanned_records: int,
    updated_records: int,
    imported_ado_artifacts: int,
) -> None:
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_payload = {
        "migration": "local_issues_unified_schema",
        "version": LOCAL_ISSUE_SCHEMA_VERSION,
        "applied_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "scanned_records": scanned_records,
        "updated_records": updated_records,
        "imported_ado_artifacts": imported_ado_artifacts,
    }
    marker_path.write_text(json.dumps(marker_payload, indent=2, sort_keys=True), encoding="utf-8")


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
