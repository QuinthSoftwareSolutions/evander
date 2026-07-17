from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import yaml

from evander.bootstrap import load_runtime_gateway_config
from evander.config import ProxyIssueSyncWorkerGlobalConfig
from evander.issues.service import IssueService
from evander.models import DirectoryPath, LocalIssueRecord, RemoteIssueReference
from evander.provider_config import ProviderConfigResolver
from evander.storage import JsonStore

_LOG = logging.getLogger(__name__)
DEFAULT_PROXY_ISSUE_SYNC_INTERVAL_SECONDS = 300.0
DEFAULT_PROXY_ISSUE_SYNC_FRESHNESS_SECONDS = 300.0
_PROXY_ISSUE_SYNC_CONFIG_RELATIVE_PATH = Path("config") / "proxy-issue-sync" / "config.json"
_URL_TRAILING_DELIMITERS = ".,;:!?)]}'\""


@dataclass(frozen=True)
class ProxyIssueSyncGroupKey:
    provider: str
    directory_path: str
    organization_url: str | None = None
    project: str | None = None


@dataclass(frozen=True)
class ProxyIssueSyncCandidate:
    issue_id: str
    work_item_id: str
    group: ProxyIssueSyncGroupKey
    last_synced_at: datetime | None = None


@dataclass
class ProxyIssueSyncResult:
    candidates: int = 0
    skipped_fresh: int = 0
    groups: int = 0
    remote_requests: int = 0
    updated: int = 0
    missing: int = 0
    failed: int = 0
    diagnostics: list[dict[str, Any]] = field(default_factory=list)


def sync_proxy_issues_once(
    *,
    runtime_dir: Path,
    issue_service: IssueService,
    freshness_window_seconds: float = DEFAULT_PROXY_ISSUE_SYNC_FRESHNESS_SECONDS,
    force: bool = False,
    now: datetime | None = None,
) -> ProxyIssueSyncResult:
    now = _utc(now) or datetime.now(UTC)
    store = JsonStore(runtime_dir / "local-issues.json", LocalIssueRecord)
    snapshot = store.read_all()
    candidates, skipped_fresh = _collect_stale_ado_proxy_candidates(
        snapshot,
        now=now,
        freshness_window=timedelta(seconds=max(0.0, freshness_window_seconds)),
        force=force,
    )
    result = ProxyIssueSyncResult(candidates=len(candidates), skipped_fresh=skipped_fresh)
    if not candidates:
        return result

    groups: dict[ProxyIssueSyncGroupKey, list[ProxyIssueSyncCandidate]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.group, []).append(candidate)
    result.groups = len(groups)

    remote_by_issue_id: dict[str, dict[str, Any]] = {}
    missing_by_issue_id: dict[str, str] = {}
    failed_by_issue_id: dict[str, str] = {}

    for group, grouped_candidates in groups.items():
        work_item_ids = [candidate.work_item_id for candidate in grouped_candidates]
        try:
            batch = issue_service.read_remote_issues_batch(
                provider=group.provider,
                remote_issue_ids=work_item_ids,
                directory_path=group.directory_path,
                project=group.project,
                organization_url=group.organization_url,
            )
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            _LOG.warning(
                "proxy_issue_sync group_failed provider=%s directory=%s project=%s count=%d error=%s",
                group.provider,
                group.directory_path,
                group.project,
                len(grouped_candidates),
                message,
            )
            for candidate in grouped_candidates:
                failed_by_issue_id[candidate.issue_id] = message
            result.failed += len(grouped_candidates)
            result.diagnostics.append(
                {
                    "group": _group_key_payload(group),
                    "status": "failed",
                    "count": len(grouped_candidates),
                    "error": message,
                }
            )
            continue

        result.remote_requests += 1
        items = batch.get("items") if isinstance(batch, dict) else {}
        items_by_remote_id = items if isinstance(items, dict) else {}
        missing_ids = {
            str(item).strip()
            for item in (batch.get("missing_ids") if isinstance(batch, dict) else []) or []
            if str(item).strip()
        }
        for candidate in grouped_candidates:
            remote_payload = items_by_remote_id.get(candidate.work_item_id)
            if isinstance(remote_payload, dict):
                remote_by_issue_id[candidate.issue_id] = remote_payload
                continue
            if candidate.work_item_id in missing_ids:
                missing_by_issue_id[candidate.issue_id] = "missing_or_inaccessible"
            else:
                missing_by_issue_id[candidate.issue_id] = "not_returned"
        result.missing += len(
            [
                candidate
                for candidate in grouped_candidates
                if candidate.issue_id in missing_by_issue_id
            ]
        )

    if not remote_by_issue_id and not missing_by_issue_id and not failed_by_issue_id:
        return result

    now_iso = _iso(now)
    refreshed_records: list[LocalIssueRecord] = []

    def _apply(items: dict[str, LocalIssueRecord]) -> None:
        for issue_id, remote_payload in remote_by_issue_id.items():
            issue = items.get(issue_id)
            if issue is None:
                continue
            changed = _refresh_issue_from_summary(issue, remote_payload, now=now)
            _record_sync_status(issue, status="ok", attempted_at=now_iso)
            if changed:
                issue.updated_at = now
                result.updated += 1
            refreshed_records.append(issue.model_copy(deep=True))
        for issue_id, reason in missing_by_issue_id.items():
            issue = items.get(issue_id)
            if issue is None:
                continue
            _record_sync_status(issue, status=reason, attempted_at=now_iso)
        for issue_id, error in failed_by_issue_id.items():
            issue = items.get(issue_id)
            if issue is None:
                continue
            _record_sync_status(issue, status="error", attempted_at=now_iso, error=error)

    store.update_all(_apply)
    for issue in refreshed_records:
        if _is_generated_ado_proxy_issue(issue):
            _write_proxy_issue_yaml(issue)
    return result


def upsert_ado_proxy_issue_from_remote_payload(
    *,
    runtime_dir: Path,
    directory_path: str,
    remote_payload: dict[str, Any],
    project: str | None = None,
    organization_url: str | None = None,
    yaml_output_dir: Path | None = None,
    managed_repo_id: str | None = None,
    source: str = "proxy_azure_devops",
    now: datetime | None = None,
) -> LocalIssueRecord:
    """Create or refresh one local Azure DevOps proxy issue from a remote payload."""

    normalized_directory_path = DirectoryPath(segments=directory_path).as_posix()
    work_item_id = _remote_work_item_id(remote_payload)
    if not work_item_id:
        raise ValueError("remote_payload must include a work item id")
    normalized_project = _optional_text(project) or _optional_text(remote_payload.get("project"))
    normalized_organization_url = _optional_text(organization_url) or _optional_text(
        remote_payload.get("organization_url")
    )
    now = _utc(now) or datetime.now(UTC)
    normalized_managed_repo_id = _optional_text(managed_repo_id)
    normalized_source = _optional_text(source) or "proxy_azure_devops"
    store = JsonStore(runtime_dir / "local-issues.json", LocalIssueRecord)
    deleted_proxy_yaml_paths: list[Path] = []

    def _apply(items: dict[str, LocalIssueRecord]) -> LocalIssueRecord:
        issue_id = _find_ado_proxy_issue_id(
            items,
            directory_path=normalized_directory_path,
            work_item_id=work_item_id,
            project=normalized_project,
            organization_url=normalized_organization_url,
            remote_payload=remote_payload,
        )
        issue = items.get(issue_id) if issue_id is not None else None
        if issue is None:
            issue_id = _scoped_proxy_issue_id(
                work_item_id,
                directory_path=normalized_directory_path,
                organization_url=normalized_organization_url,
                project=normalized_project,
            )
            yaml_path = _proxy_issue_yaml_path(
                runtime_dir=runtime_dir,
                issue_id=issue_id,
                work_item_id=work_item_id,
                yaml_output_dir=yaml_output_dir,
            )
            issue = LocalIssueRecord(
                issue_id=issue_id,
                issue_number=work_item_id,
                title=str(remote_payload.get("title") or f"ADO work item {work_item_id}"),
                body=str(remote_payload.get("description") or ""),
                description=str(remote_payload.get("description") or ""),
                yaml_path=str(yaml_path),
                directory_path=normalized_directory_path,
                managed_repo_id=normalized_managed_repo_id,
                source_type="remote_proxy",
                source=normalized_source,
                state=_optional_text(remote_payload.get("state")),
                url=_optional_text(remote_payload.get("url")),
                created_at=now,
                updated_at=now,
            )
            items[issue.issue_id] = issue
        else:
            issue.directory_path = normalized_directory_path

        if _is_generated_ado_proxy_issue(issue):
            issue.source_type = "remote_proxy"
            issue.source = normalized_source
            if yaml_output_dir is not None:
                issue.yaml_path = str(
                    _proxy_issue_yaml_path(
                        runtime_dir=runtime_dir,
                        issue_id=issue.issue_id,
                        work_item_id=work_item_id,
                        yaml_output_dir=yaml_output_dir,
                    )
                )
        if normalized_managed_repo_id is not None:
            issue.managed_repo_id = normalized_managed_repo_id
        issue.remote_reference = _updated_remote_reference(
            issue.remote_reference,
            work_item_id=work_item_id,
            project=normalized_project,
            organization_url=normalized_organization_url,
            remote_payload=remote_payload,
            now=now,
        )
        issue.metadata = dict(issue.metadata)
        _preserve_proxy_yaml_metadata(issue)
        issue.metadata["remote"] = {
            "provider": "azure_devops",
            "organization": normalized_organization_url or "",
            "project": normalized_project or "",
            "issue_id": work_item_id,
            "work_item_url": str(remote_payload.get("url") or ""),
        }
        _refresh_issue_from_summary(issue, remote_payload, now=now)
        _record_sync_status(issue, status="ok", attempted_at=_iso(now))
        issue.updated_at = now
        items[issue.issue_id] = issue
        for duplicate_id in _find_generated_ado_proxy_duplicate_ids(
            items,
            keep_issue_id=issue.issue_id,
            directory_path=normalized_directory_path,
            work_item_id=work_item_id,
            project=normalized_project,
            organization_url=normalized_organization_url,
        ):
            duplicate = items.pop(duplicate_id, None)
            if duplicate is not None and duplicate.yaml_path:
                deleted_proxy_yaml_paths.append(Path(duplicate.yaml_path))
        return issue.model_copy(deep=True)

    issue = store.update_all(_apply)
    if _is_generated_ado_proxy_issue(issue):
        _write_proxy_issue_yaml(issue)
    for yaml_path in deleted_proxy_yaml_paths:
        _delete_generated_proxy_yaml(runtime_dir=runtime_dir, yaml_path=yaml_path)
    return issue


def _collect_stale_ado_proxy_candidates(
    items: dict[str, LocalIssueRecord],
    *,
    now: datetime,
    freshness_window: timedelta,
    force: bool,
) -> tuple[list[ProxyIssueSyncCandidate], int]:
    candidates: list[ProxyIssueSyncCandidate] = []
    skipped_fresh = 0
    cutoff = now - freshness_window
    for issue_id, issue in items.items():
        remote = issue.remote_reference
        if remote is None:
            continue
        provider = str(remote.provider or "").strip()
        if provider != "azure_devops":
            continue
        work_item_id = str(remote.work_item_id or "").strip()
        if not work_item_id:
            continue
        last_synced_at = _utc(remote.last_synced_at)
        if not force and last_synced_at is not None and last_synced_at >= cutoff:
            skipped_fresh += 1
            continue
        candidates.append(
            ProxyIssueSyncCandidate(
                issue_id=issue_id,
                work_item_id=work_item_id,
                last_synced_at=last_synced_at,
                group=ProxyIssueSyncGroupKey(
                    provider=provider,
                    directory_path=issue.directory_path,
                    organization_url=_optional_text(remote.organization),
                    project=_optional_text(remote.project),
                ),
            )
        )
    return candidates, skipped_fresh


def _remote_work_item_id(remote_payload: dict[str, Any]) -> str:
    for key in ("remote_issue_id", "id", "work_item_id"):
        text = _optional_text(remote_payload.get(key))
        if text:
            return text
    fields = remote_payload.get("fields") if isinstance(remote_payload.get("fields"), dict) else {}
    return _optional_text(fields.get("System.Id")) or ""


def _find_ado_proxy_issue_id(
    items: dict[str, LocalIssueRecord],
    *,
    directory_path: str,
    work_item_id: str,
    project: str | None,
    organization_url: str | None,
    remote_payload: dict[str, Any] | None = None,
) -> str | None:
    proxy_match: str | None = None
    text_link_match: str | None = None
    for issue_id, issue in items.items():
        if issue.directory_path != directory_path:
            continue
        remote = issue.remote_reference
        if remote is not None:
            if remote.provider != "azure_devops" or str(remote.work_item_id or "") != work_item_id:
                continue
            if project and _optional_text(remote.project) not in {None, project}:
                continue
            if organization_url and _normalize_url(remote.organization) not in {
                None,
                _normalize_url(organization_url),
            }:
                continue
            if not _is_generated_ado_proxy_issue(issue):
                return issue_id
            proxy_match = proxy_match or issue_id
            continue
        metadata_remote = (
            issue.metadata.get("remote") if isinstance(issue.metadata.get("remote"), dict) else {}
        )
        provider = str(metadata_remote.get("provider") or "").strip()
        metadata_issue_id = str(metadata_remote.get("issue_id") or "").strip()
        if provider == "azure_devops" and metadata_issue_id == work_item_id:
            if not _is_generated_ado_proxy_issue(issue):
                return issue_id
            proxy_match = proxy_match or issue_id
            continue
        if remote_payload is not None and _local_issue_mentions_ado_work_item(
            issue, work_item_id=work_item_id, remote_payload=remote_payload
        ):
            text_link_match = text_link_match or issue_id
    return text_link_match or proxy_match


def _local_issue_mentions_ado_work_item(
    issue: LocalIssueRecord, *, work_item_id: str, remote_payload: dict[str, Any]
) -> bool:
    if _is_generated_ado_proxy_issue(issue):
        return False
    title = _optional_text(remote_payload.get("title"))
    if title and issue.title.strip() != title:
        return False

    reason_parts: list[str] = []
    if issue.reason:
        reason_parts.append(issue.reason)
    fields = issue.metadata.get("fields") if isinstance(issue.metadata, dict) else None
    if isinstance(fields, dict):
        reason = _optional_text(fields.get("Reason"))
        if reason:
            reason_parts.append(reason)
    if not reason_parts:
        return False

    text = "\n".join(reason_parts)
    remote_url = _optional_text(remote_payload.get("url"))
    if remote_url and _text_mentions_exact_url(text, remote_url):
        return True
    return bool(
        re.search(
            rf"\b(?:ADO|Azure DevOps)\b[^\n]{{0,120}}\b{re.escape(work_item_id)}\b",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(
            rf"\b{re.escape(work_item_id)}\b[^\n]{{0,120}}\b(?:ADO|Azure DevOps)\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def _text_mentions_exact_url(text: str, url: str) -> bool:
    normalized_url = url.rstrip("/")
    if not normalized_url:
        return False
    for match in re.finditer(r"https?://[^\s<>\"]+", text):
        token = match.group(0).rstrip(_URL_TRAILING_DELIMITERS).rstrip("/")
        if token == normalized_url:
            return True
    return False


def _find_generated_ado_proxy_duplicate_ids(
    items: dict[str, LocalIssueRecord],
    *,
    keep_issue_id: str,
    directory_path: str,
    work_item_id: str,
    project: str | None,
    organization_url: str | None,
) -> list[str]:
    duplicate_ids: list[str] = []
    for issue_id, issue in items.items():
        if issue_id == keep_issue_id:
            continue
        if issue.directory_path != directory_path:
            continue
        if not _is_generated_ado_proxy_issue(issue):
            continue
        if _ado_issue_matches_remote(
            issue,
            work_item_id=work_item_id,
            project=project,
            organization_url=organization_url,
        ):
            duplicate_ids.append(issue_id)
    return duplicate_ids


def _ado_issue_matches_remote(
    issue: LocalIssueRecord,
    *,
    work_item_id: str,
    project: str | None,
    organization_url: str | None,
) -> bool:
    remote = issue.remote_reference
    if remote is not None:
        if remote.provider != "azure_devops" or str(remote.work_item_id or "") != work_item_id:
            return False
        if project and _optional_text(remote.project) not in {None, project}:
            return False
        if organization_url and _normalize_url(remote.organization) not in {
            None,
            _normalize_url(organization_url),
        }:
            return False
        return True

    metadata_remote = (
        issue.metadata.get("remote") if isinstance(issue.metadata.get("remote"), dict) else {}
    )
    provider = str(metadata_remote.get("provider") or "").strip()
    metadata_issue_id = str(metadata_remote.get("issue_id") or "").strip()
    if provider != "azure_devops" or metadata_issue_id != work_item_id:
        return False
    if project and _optional_text(metadata_remote.get("project")) not in {None, project}:
        return False
    if organization_url and _normalize_url(metadata_remote.get("organization")) not in {
        None,
        _normalize_url(organization_url),
    }:
        return False
    return True


def _is_generated_ado_proxy_issue(issue: LocalIssueRecord) -> bool:
    return issue.source_type == "remote_proxy" or issue.source == "proxy_azure_devops"


def _scoped_proxy_issue_id(
    work_item_id: str,
    *,
    directory_path: str,
    organization_url: str | None,
    project: str | None,
) -> str:
    parts = [directory_path, "azure_devops"]
    if organization_url and project:
        parts.extend([organization_url.rstrip("/"), project])
    parts.append(work_item_id)
    return "::".join(parts)


def _proxy_issue_yaml_path(
    *,
    runtime_dir: Path,
    issue_id: str,
    work_item_id: str,
    yaml_output_dir: Path | None = None,
) -> Path:
    if yaml_output_dir is not None:
        return yaml_output_dir / f"work-item-{work_item_id}.yaml"
    digest = sha256(issue_id.encode("utf-8")).hexdigest()[:12]
    return runtime_dir / "issues" / f"proxy-azure-devops-{work_item_id}-{digest}.yaml"


def _updated_remote_reference(
    existing: RemoteIssueReference | None,
    *,
    work_item_id: str,
    project: str | None,
    organization_url: str | None,
    remote_payload: dict[str, Any],
    now: datetime,
) -> RemoteIssueReference:
    updated_at = _parse_dt(
        remote_payload.get("updated_at")
        or remote_payload.get("changed_at")
        or remote_payload.get("updated_date")
    )
    state = _optional_text(remote_payload.get("state"))
    url = _optional_text(remote_payload.get("url"))
    if existing is None:
        return RemoteIssueReference(
            provider="azure_devops",
            organization=organization_url,
            project=project,
            work_item_id=work_item_id,
            work_item_url=url,
            last_synced_at=now,
            last_remote_updated_at=updated_at,
            last_known_remote_state=state,
        )
    existing.provider = "azure_devops"
    existing.organization = organization_url or existing.organization
    existing.project = project or existing.project
    existing.work_item_id = work_item_id
    existing.work_item_url = url or existing.work_item_url
    existing.last_synced_at = now
    existing.last_remote_updated_at = updated_at or existing.last_remote_updated_at
    existing.last_known_remote_state = state or existing.last_known_remote_state
    return existing


def _refresh_issue_from_summary(
    issue: LocalIssueRecord,
    remote_payload: dict[str, Any],
    *,
    now: datetime,
) -> bool:
    changed = False
    issue.metadata = dict(issue.metadata)
    proxy_cache = (
        dict(issue.metadata.get("proxy_cache"))
        if isinstance(issue.metadata.get("proxy_cache"), dict)
        else {}
    )

    title = _optional_text(remote_payload.get("title"))
    if title is not None and issue.title != title:
        issue.title = title
        changed = True

    if "description" in remote_payload:
        description = str(remote_payload.get("description") or "")
        if issue.body != description or issue.description != description:
            issue.body = description
            issue.description = description
            changed = True
    else:
        description = issue.description or issue.body

    state = _optional_text(remote_payload.get("state"))
    if state is not None and issue.state != state:
        issue.state = state
        changed = True

    tags = _remote_payload_tags(remote_payload)
    if tags is not None and tags != issue.tags:
        issue.tags = tags
        changed = True

    url = _optional_text(remote_payload.get("url"))
    if url is not None and issue.url != url:
        issue.url = url
        changed = True

    if isinstance(remote_payload.get("comments"), list):
        comments = [
            dict(comment) for comment in remote_payload["comments"] if isinstance(comment, dict)
        ]
        if comments != issue.comments:
            issue.comments = comments
            changed = True

    updated_at = _parse_dt(
        remote_payload.get("updated_at")
        or remote_payload.get("changed_at")
        or remote_payload.get("updated_date")
    )
    remote = issue.remote_reference
    if remote is not None:
        remote.last_synced_at = now
        if updated_at is not None and remote.last_remote_updated_at != updated_at:
            remote.last_remote_updated_at = updated_at
            changed = True
        if state is not None and remote.last_known_remote_state != state:
            remote.last_known_remote_state = state
            changed = True
        if url is not None and remote.work_item_url != url:
            remote.work_item_url = url
            changed = True

    proxy_cache.update(
        {
            "title": title or issue.title,
            "description": description,
            "state": state or issue.state or "",
            "url": url or issue.url or "",
            "updated_at": _iso(updated_at) if updated_at is not None else "",
            "type": _optional_text(remote_payload.get("type")),
            "area": _optional_text(remote_payload.get("area")),
            "iteration": _optional_text(remote_payload.get("iteration")),
            "assigned_to": _optional_text(remote_payload.get("assigned_to")),
            "tags": list(issue.tags),
            "fields": dict(remote_payload.get("fields") or {}),
        }
    )
    proxy_cache["comments"] = [dict(comment) for comment in issue.comments]
    issue.metadata["proxy_cache"] = proxy_cache
    issue.sync_derived_fields()
    return changed


def _remote_payload_tags(remote_payload: dict[str, Any]) -> list[str] | None:
    if "tags" in remote_payload:
        return _normalize_tags(remote_payload.get("tags"))
    fields = remote_payload.get("fields") if isinstance(remote_payload.get("fields"), dict) else {}
    if "System.Tags" in fields:
        return _normalize_tags(fields.get("System.Tags"))
    return None


def _normalize_tags(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items: object = value.split(";")
    else:
        raw_items = value
    if not isinstance(raw_items, (list, tuple, set)):
        raw_items = [raw_items]
    return [str(tag).strip() for tag in raw_items if str(tag).strip()]


def _preserve_proxy_yaml_metadata(issue: LocalIssueRecord) -> None:
    yaml_path = Path(issue.yaml_path)
    if not yaml_path.exists():
        return
    try:
        loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(loaded, dict):
        return
    yaml_steps = loaded.get("steps")
    if (
        "steps" not in issue.metadata
        and isinstance(yaml_steps, list)
        and all(isinstance(step, str) for step in yaml_steps)
    ):
        issue.metadata["steps"] = list(yaml_steps)
    yaml_plan_summary = loaded.get("plan_summary")
    if (
        "plan_summary" not in issue.metadata
        and isinstance(yaml_plan_summary, str)
        and yaml_plan_summary.strip()
    ):
        issue.metadata["plan_summary"] = yaml_plan_summary


def _write_proxy_issue_yaml(issue: LocalIssueRecord) -> None:
    path = Path(issue.yaml_path)
    _atomic_write_yaml(path, _build_proxy_issue_yaml_payload(issue))


def _delete_generated_proxy_yaml(*, runtime_dir: Path, yaml_path: Path) -> None:
    try:
        resolved_runtime = runtime_dir.resolve()
        resolved_yaml = yaml_path.resolve()
    except OSError:
        return
    if resolved_runtime not in resolved_yaml.parents:
        return
    if not resolved_yaml.name.startswith("proxy-azure-devops-"):
        return
    try:
        resolved_yaml.unlink(missing_ok=True)
    except OSError:
        _LOG.warning("proxy_issue_sync failed to delete duplicate proxy yaml path=%s", yaml_path)


def _build_proxy_issue_yaml_payload(issue: LocalIssueRecord) -> dict[str, Any]:
    remote = issue.remote_reference
    work_item_url = (remote.work_item_url if remote is not None else None) or issue.url or ""
    payload: dict[str, Any] = {
        "issue_number": int(issue.issue_number)
        if str(issue.issue_number).isdigit()
        else issue.issue_number,
        "issue_id": issue.issue_id,
        "title": issue.title,
        "body": issue.body,
        "description": issue.description or issue.body,
        "tags": list(issue.tags),
        "state": issue.state or "",
        "url": work_item_url,
        "comments": [dict(comment) for comment in issue.comments],
        "directory_path": issue.directory_path,
        "managed_repo_id": issue.managed_repo_id,
        "remote_reference": remote.model_dump(mode="json") if remote is not None else None,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    if remote is not None and remote.last_remote_updated_at is not None:
        payload["updated_date"] = _iso(remote.last_remote_updated_at)
    else:
        proxy_cache = (
            issue.metadata.get("proxy_cache") if isinstance(issue.metadata, dict) else None
        )
        if isinstance(proxy_cache, dict):
            payload["updated_date"] = str(proxy_cache.get("updated_at") or "")
    if remote is not None and remote.provider == "azure_devops":
        payload["source"] = {
            "organization": remote.organization or "",
            "project": remote.project or "",
            "work_item_url": work_item_url,
        }
    else:
        payload["source"] = issue.source or "proxy_azure_devops"
    metadata_steps = issue.metadata.get("steps") if isinstance(issue.metadata, dict) else None
    if isinstance(metadata_steps, list) and all(isinstance(step, str) for step in metadata_steps):
        payload["steps"] = list(metadata_steps)
    metadata_plan_summary = (
        issue.metadata.get("plan_summary") if isinstance(issue.metadata, dict) else None
    )
    if isinstance(metadata_plan_summary, str) and metadata_plan_summary.strip():
        payload["plan_summary"] = metadata_plan_summary
    return payload


def _atomic_write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    tmp_path.replace(path)


def _record_sync_status(
    issue: LocalIssueRecord,
    *,
    status: str,
    attempted_at: str,
    error: str | None = None,
) -> None:
    issue.metadata = dict(issue.metadata)
    diagnostics = {
        "status": status,
        "last_attempted_at": attempted_at,
    }
    if error:
        diagnostics["error"] = error
    issue.metadata["proxy_sync"] = diagnostics


def run_proxy_issue_sync_worker(
    *,
    runtime_dir: Path,
    interval_seconds: float = DEFAULT_PROXY_ISSUE_SYNC_INTERVAL_SECONDS,
    freshness_window_seconds: float = DEFAULT_PROXY_ISSUE_SYNC_FRESHNESS_SECONDS,
    force: bool = False,
) -> None:
    while True:
        cycle_start = time.monotonic()
        try:
            result = _sync_from_runtime_config(
                runtime_dir=runtime_dir,
                freshness_window_seconds=freshness_window_seconds,
                force=force,
            )
            _LOG.info(
                "cycle candidates=%d skipped_fresh=%d groups=%d remote_requests=%d "
                "updated=%d missing=%d failed=%d",
                result.candidates,
                result.skipped_fresh,
                result.groups,
                result.remote_requests,
                result.updated,
                result.missing,
                result.failed,
            )
        except Exception:
            _LOG.exception("cycle error=unexpected")
        elapsed = time.monotonic() - cycle_start
        time.sleep(max(0.0, interval_seconds - elapsed))


def _sync_from_runtime_config(
    *,
    runtime_dir: Path,
    freshness_window_seconds: float,
    force: bool,
) -> ProxyIssueSyncResult:
    config = load_runtime_gateway_config(runtime_dir=runtime_dir)
    issue_service = IssueService(ProviderConfigResolver(config))
    return sync_proxy_issues_once(
        runtime_dir=config.runtime_dir,
        issue_service=issue_service,
        freshness_window_seconds=freshness_window_seconds,
        force=force,
    )


def run_once_from_runtime(
    *,
    runtime_dir: Path,
    freshness_window_seconds: float = DEFAULT_PROXY_ISSUE_SYNC_FRESHNESS_SECONDS,
    force: bool = False,
) -> ProxyIssueSyncResult:
    return _sync_from_runtime_config(
        runtime_dir=runtime_dir,
        freshness_window_seconds=freshness_window_seconds,
        force=force,
    )


def load_proxy_issue_sync_worker_config(
    *,
    workspace_root: Path,
    runtime_dir: Path | None = None,
    interval_seconds: float | None = None,
    freshness_window_seconds: float | None = None,
) -> ProxyIssueSyncWorkerGlobalConfig:
    config_path = workspace_root / _PROXY_ISSUE_SYNC_CONFIG_RELATIVE_PATH
    raw = _load_json_object(config_path)
    updates: dict[str, Any] = {}
    if runtime_dir is not None:
        updates["runtime_dir"] = runtime_dir
    if interval_seconds is not None:
        updates["poll_interval_seconds"] = interval_seconds
    if freshness_window_seconds is not None:
        updates["freshness_window_seconds"] = freshness_window_seconds
    if updates:
        raw.update(updates)
    config = ProxyIssueSyncWorkerGlobalConfig.model_validate(raw)
    if not config.runtime_dir.is_absolute():
        config.runtime_dir = workspace_root / config.runtime_dir
    return config


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_url(value: object) -> str | None:
    text = _optional_text(value)
    return text.rstrip("/") if text else None


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return _utc(parsed)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    return _utc(value).isoformat().replace("+00:00", "Z")


def _group_key_payload(group: ProxyIssueSyncGroupKey) -> dict[str, str | None]:
    return {
        "provider": group.provider,
        "directory_path": group.directory_path,
        "organization_url": group.organization_url,
        "project": group.project,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Periodically sync local Azure DevOps proxy issues from remote state."
    )
    parser.add_argument("--runtime-dir", default=None, help="Runtime directory root.")
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=None,
        help="Daemon polling interval in seconds.",
    )
    parser.add_argument(
        "--freshness-window-seconds",
        type=float,
        default=None,
        help="Skip proxy records synced more recently than this many seconds unless --force is set.",
    )
    parser.add_argument("--once", action="store_true", help="Run one sync cycle and exit.")
    parser.add_argument(
        "--force", action="store_true", help="Sync records regardless of freshness."
    )
    parser.add_argument("--log-level", default="INFO", help="Python logging level.")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    runtime_dir_override = Path(args.runtime_dir).expanduser() if args.runtime_dir else None
    config = load_proxy_issue_sync_worker_config(
        workspace_root=Path.cwd(),
        runtime_dir=runtime_dir_override,
        interval_seconds=args.interval_seconds,
        freshness_window_seconds=args.freshness_window_seconds,
    )
    if args.once:
        result = run_once_from_runtime(
            runtime_dir=config.runtime_dir,
            freshness_window_seconds=config.freshness_window_seconds,
            force=args.force,
        )
        _LOG.info(
            "cycle candidates=%d skipped_fresh=%d groups=%d remote_requests=%d "
            "updated=%d missing=%d failed=%d",
            result.candidates,
            result.skipped_fresh,
            result.groups,
            result.remote_requests,
            result.updated,
            result.missing,
            result.failed,
        )
        return

    run_proxy_issue_sync_worker(
        runtime_dir=config.runtime_dir,
        interval_seconds=config.poll_interval_seconds,
        freshness_window_seconds=config.freshness_window_seconds,
        force=args.force,
    )


if __name__ == "__main__":
    main()
