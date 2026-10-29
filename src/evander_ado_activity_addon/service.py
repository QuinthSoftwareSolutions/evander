from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
from typing import Any, Literal

from evander.issues.service import IssueService
from evander.services import AzureDevOpsPrQueryProvider, PrQueryService

from .models import ADDON_ID
from .repository import AdoActivityRepository

PAGE_SIZE = 200

type _ScopeName = Literal["work_items", "pr_comments", "pr_commits"]
type _PageFetcher = Callable[[int], dict[str, Any]]
type _PageFetcherFactory = Callable[[], _PageFetcher]
type _RecordNormalizer = Callable[[Mapping[str, Any]], list[dict[str, Any]]]

_SCOPE_NAMES: tuple[_ScopeName, ...] = ("work_items", "pr_comments", "pr_commits")


@dataclass(frozen=True)
class _SyncRequest:
    since: datetime
    until: datetime | None
    directory_path: str
    actor: str | None
    project: str | None
    repo: str | None
    states: list[str]
    types: list[str]
    tags_any: list[str]
    statuses: list[str]
    include_system_comments: bool


@dataclass(frozen=True)
class _ScopeAdapter:
    requires_repo: bool
    sync_repo: str | None
    sync_metadata: Mapping[str, Any]
    page_fetcher_factory: _PageFetcherFactory
    normalize_records: _RecordNormalizer


class AdoActivityIndexService:
    def __init__(
        self,
        *,
        addon_runtime_dir: str | Path,
        provider_config_resolver: Any | None,
    ) -> None:
        self.repository = AdoActivityRepository(addon_runtime_dir)
        self.provider_config_resolver = provider_config_resolver

    def query(
        self,
        *,
        since: datetime,
        until: datetime | None = None,
        actor: str | None = None,
        activity_kinds: list[str] | None = None,
        directory_path: str | None = None,
        project: str | None = None,
        repo: str | None = None,
        work_item_id: str | None = None,
        pr_id: str | None = None,
        top: int = 100,
        cursor: str | None = None,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        return self.repository.query(
            since=since,
            until=until,
            actor=actor,
            activity_kinds=activity_kinds,
            directory_path=directory_path,
            project=project,
            repo=repo,
            work_item_id=work_item_id,
            pr_id=pr_id,
            top=top,
            cursor=cursor,
            include_raw=include_raw,
        )

    def sync(
        self,
        *,
        since: datetime,
        until: datetime | None = None,
        directory_path: str = "root",
        scope: str = "all",
        actor: str | None = None,
        project: str | None = None,
        repo: str | None = None,
        states: list[str] | None = None,
        types: list[str] | None = None,
        tags_any: list[str] | None = None,
        statuses: list[str] | None = None,
        include_system_comments: bool = False,
    ) -> dict[str, Any]:
        if self.provider_config_resolver is None:
            raise RuntimeError("ADO activity sync requires provider_config_resolver")
        normalized_scope = str(scope or "all").strip() or "all"
        scopes = _expand_scope(normalized_scope)
        request = _SyncRequest(
            since=since,
            until=until,
            directory_path=directory_path,
            actor=actor,
            project=project,
            repo=repo,
            states=states or [],
            types=types or [],
            tags_any=tags_any or [],
            statuses=statuses or [],
            include_system_comments=include_system_comments,
        )
        adapters = self._scope_adapters(request)
        if request.repo is None and any(adapters[item].requires_repo for item in scopes):
            raise ValueError("repo is required when syncing PR activity")

        totals = {"inserted": 0, "updated": 0, "records": 0}
        details: dict[str, Any] = {}
        sync_until = until or datetime.now(UTC)
        for scope_name in scopes:
            adapter = adapters[scope_name]
            result = self._consume_pages(adapter)
            details[scope_name] = result
            _merge_totals(totals, result)
            if result["complete"]:
                self.repository.update_sync_state(
                    sync_key=_sync_key(
                        directory_path=request.directory_path,
                        scope=scope_name,
                        project=request.project,
                        repo=adapter.sync_repo,
                    ),
                    directory_path=request.directory_path,
                    complete_through=sync_until,
                    metadata=adapter.sync_metadata,
                )

        return {
            "addon_id": ADDON_ID,
            "directory_path": request.directory_path,
            "scope": normalized_scope,
            "since": _iso(request.since),
            "until": _iso(sync_until),
            "inserted": totals["inserted"],
            "updated": totals["updated"],
            "records": totals["records"],
            "details": details,
        }

    def _scope_adapters(self, request: _SyncRequest) -> dict[_ScopeName, _ScopeAdapter]:
        return {
            "work_items": _ScopeAdapter(
                requires_repo=False,
                sync_repo=None,
                sync_metadata={
                    "actor": request.actor,
                    "states": request.states,
                    "types": request.types,
                },
                page_fetcher_factory=lambda: self._work_item_page_fetcher(request),
                normalize_records=lambda payload: _normalize_work_item_records(
                    payload,
                    directory_path=request.directory_path,
                ),
            ),
            "pr_comments": _ScopeAdapter(
                requires_repo=True,
                sync_repo=request.repo,
                sync_metadata={"actor": request.actor, "statuses": request.statuses},
                page_fetcher_factory=lambda: self._pr_comment_page_fetcher(request),
                normalize_records=lambda payload: _normalize_pr_comment_records(
                    payload,
                    directory_path=request.directory_path,
                ),
            ),
            "pr_commits": _ScopeAdapter(
                requires_repo=True,
                sync_repo=request.repo,
                sync_metadata={"actor": request.actor, "statuses": request.statuses},
                page_fetcher_factory=lambda: self._pr_commit_page_fetcher(request),
                normalize_records=lambda payload: _normalize_pr_commit_records(
                    payload,
                    directory_path=request.directory_path,
                ),
            ),
        }

    def _consume_pages(self, adapter: _ScopeAdapter) -> dict[str, Any]:
        fetch_page = adapter.page_fetcher_factory()
        skip = 0
        total = {"inserted": 0, "updated": 0, "records": 0}
        complete = True
        pages = 0
        while True:
            payload = fetch_page(skip)
            records = adapter.normalize_records(payload)
            counts = self.repository.upsert_records(records)
            _merge_counts(total, counts, len(records))
            pages += 1
            paging = _mapping(payload.get("paging"))
            next_skip = _next_skip(paging)
            if next_skip is None:
                complete = not bool(paging.get("has_more"))
                break
            skip = next_skip
        return {**total, "pages": pages, "complete": complete}

    def _work_item_page_fetcher(self, request: _SyncRequest) -> _PageFetcher:
        issue_service = IssueService(self.provider_config_resolver)

        def fetch_page(skip: int) -> dict[str, Any]:
            return issue_service.query_remote_work_item_activity(
                provider="azure_devops",
                directory_path=request.directory_path,
                project=request.project,
                actor=request.actor,
                since=request.since,
                until=request.until,
                activity_types=["created", "changed", "commented"],
                states=request.states,
                types=request.types,
                tags_any=request.tags_any,
                top=PAGE_SIZE,
                skip=skip,
                exhaustive=True,
                max_candidates=1000,
            )

        return fetch_page

    def _pr_comment_page_fetcher(self, request: _SyncRequest) -> _PageFetcher:
        service = self._pr_query_service(request.directory_path)

        def fetch_page(skip: int) -> dict[str, Any]:
            response = service.list_ado_pr_comment_activity(
                repo=request.repo or "",
                author=request.actor,
                since=request.since,
                until=request.until,
                project=request.project,
                statuses=request.statuses,
                include_system_comments=request.include_system_comments,
                top=PAGE_SIZE,
                skip=skip,
                exhaustive=True,
                max_candidates=1000,
            )
            return _model_payload(response)

        return fetch_page

    def _pr_commit_page_fetcher(self, request: _SyncRequest) -> _PageFetcher:
        service = self._pr_query_service(request.directory_path)

        def fetch_page(skip: int) -> dict[str, Any]:
            response = service.list_ado_pr_commit_activity(
                repo=request.repo or "",
                author=request.actor,
                since=request.since,
                until=request.until,
                project=request.project,
                statuses=request.statuses,
                top=PAGE_SIZE,
                skip=skip,
                exhaustive=True,
                max_candidates=1000,
            )
            return _model_payload(response)

        return fetch_page

    def _pr_query_service(self, directory_path: str) -> PrQueryService:
        resolve_for_directory = getattr(
            self.provider_config_resolver, "resolve_for_directory", None
        )
        if not callable(resolve_for_directory):
            raise RuntimeError("ADO PR activity sync requires resolve_for_directory")
        config = resolve_for_directory(directory_path)
        pat_env = str(getattr(config, "azure_devops_pr_pat_env", "") or "").strip()
        pat = os.environ.get(pat_env, "").strip() if pat_env else ""
        if not pat:
            raise RuntimeError(f"Azure DevOps PR PAT environment variable is not set: {pat_env}")
        provider = AzureDevOpsPrQueryProvider(
            organization_url=getattr(config, "azure_devops_organization_url"),
            project=getattr(config, "azure_devops_project"),
            pat=pat,
            timeout_seconds=getattr(config, "email_provider_timeout_seconds", 30),
        )
        return PrQueryService(providers={"azure_devops": provider})


def _normalize_work_item_records(
    payload: Mapping[str, Any],
    *,
    directory_path: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in payload.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        fields = _mapping(item.get("fields"))
        work_item_id = _text(item.get("id"))
        if work_item_id is None:
            continue
        project = _text(item.get("project")) or _text(fields.get("System.TeamProject"))
        title = _text(item.get("title")) or _text(fields.get("System.Title"))
        state = _text(item.get("state")) or _text(fields.get("System.State"))
        work_item_type = _text(item.get("type")) or _text(fields.get("System.WorkItemType"))
        url = _text(item.get("web_url")) or _text(item.get("url"))
        for activity in item.get("activities") or []:
            if not isinstance(activity, Mapping):
                continue
            occurred_at = _datetime(activity.get("occurred_at"))
            if occurred_at is None:
                continue
            identity = _identity(_mapping(activity.get("actor_identity")), activity.get("actor"))
            activity_type = _text(activity.get("activity_type")) or "changed"
            provenance = _text(activity.get("provenance")) or "activity"
            source_revision = _source_revision(activity)
            records.append(
                {
                    "activity_id": (
                        f"work-item:{activity_type}:{work_item_id}:{provenance}:"
                        f"{source_revision or _iso(occurred_at)}"
                    ),
                    "activity_kind": _work_item_kind(activity_type),
                    "occurred_at": occurred_at,
                    "actor_display_name": identity["display_name"],
                    "actor_unique_name": identity["unique_name"],
                    "actor_email": identity["email"],
                    "directory_path": directory_path,
                    "project": project,
                    "work_item_id": work_item_id,
                    "title": title,
                    "state": state,
                    "work_item_type": work_item_type,
                    "url": url,
                    "summary": _text(activity.get("summary")),
                    "changed_fields": _mapping(activity.get("fields")),
                    "raw": activity.get("raw") or {},
                    "source_revision": source_revision,
                    "source_updated_at": occurred_at,
                }
            )
    return records


def _normalize_pr_comment_records(
    payload: Mapping[str, Any],
    *,
    directory_path: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    repo = _text(payload.get("repo"))
    project = _text(payload.get("project"))
    for comment in payload.get("comments") or []:
        if not isinstance(comment, Mapping):
            continue
        occurred_at = _datetime(comment.get("created_at"))
        pr_id = _text(comment.get("pr_id"))
        if occurred_at is None or repo is None or pr_id is None:
            continue
        identity = _identity(_mapping(comment.get("author_identity")), comment.get("author"))
        thread_id = _text(comment.get("thread_id")) or "thread"
        comment_id = _text(comment.get("comment_id")) or _iso(occurred_at)
        records.append(
            {
                "activity_id": f"pr:comment:{repo}:{pr_id}:{thread_id}:{comment_id}",
                "activity_kind": "pr_commented",
                "occurred_at": occurred_at,
                "actor_display_name": identity["display_name"],
                "actor_unique_name": identity["unique_name"],
                "actor_email": identity["email"],
                "directory_path": directory_path,
                "project": _text(comment.get("project")) or project,
                "repo": repo,
                "pr_id": pr_id,
                "title": _text(comment.get("pr_title")),
                "state": _text(comment.get("pr_status")),
                "url": _text(comment.get("url")) or _text(comment.get("pr_url")),
                "summary": _text(comment.get("body_snippet")) or _text(comment.get("body")),
                "raw": comment.get("raw") or {},
                "source_revision": comment_id,
                "source_updated_at": occurred_at,
            }
        )
    return records


def _normalize_pr_commit_records(
    payload: Mapping[str, Any],
    *,
    directory_path: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    repo = _text(payload.get("repo"))
    project = _text(payload.get("project"))
    for commit in payload.get("commits") or []:
        if not isinstance(commit, Mapping):
            continue
        occurred_at = _datetime(commit.get("authored_at")) or _datetime(commit.get("committed_at"))
        pr_id = _text(commit.get("pr_id"))
        commit_id = _text(commit.get("commit_id"))
        if occurred_at is None or repo is None or pr_id is None or commit_id is None:
            continue
        author_identity = _mapping(commit.get("author")) or _mapping(commit.get("committer"))
        identity = _identity(author_identity, None)
        records.append(
            {
                "activity_id": f"pr:commit:{repo}:{pr_id}:{commit_id}",
                "activity_kind": "pr_committed",
                "occurred_at": occurred_at,
                "actor_display_name": identity["display_name"],
                "actor_unique_name": identity["unique_name"],
                "actor_email": identity["email"],
                "directory_path": directory_path,
                "project": _text(commit.get("project")) or project,
                "repo": repo,
                "pr_id": pr_id,
                "title": _text(commit.get("pr_title")),
                "state": _text(commit.get("pr_status")),
                "url": _text(commit.get("url")) or _text(commit.get("pr_url")),
                "summary": _text(commit.get("comment")),
                "raw": commit.get("raw") or {},
                "source_revision": commit_id,
                "source_updated_at": occurred_at,
            }
        )
    return records


def _expand_scope(scope: str) -> tuple[_ScopeName, ...]:
    if scope == "all":
        return _SCOPE_NAMES
    for scope_name in _SCOPE_NAMES:
        if scope == scope_name:
            return (scope_name,)
    raise ValueError("scope must be one of: work_items, pr_comments, pr_commits, all")


def _work_item_kind(activity_type: str) -> str:
    normalized = str(activity_type or "").strip().casefold()
    if normalized == "created":
        return "work_item_created"
    if normalized == "commented":
        return "work_item_commented"
    return "work_item_changed"


def _source_revision(activity: Mapping[str, Any]) -> str | None:
    raw = _mapping(activity.get("raw"))
    for key in ("id", "rev", "revision", "comment_id"):
        value = _text(raw.get(key) or activity.get(key))
        if value is not None:
            return value
    return None


def _identity(identity: Mapping[str, Any], fallback: object) -> dict[str, str | None]:
    display_name = _first_text(
        identity,
        "displayName",
        "display_name",
        "name",
        "uniqueName",
        "unique_name",
        "mailAddress",
        "email",
    ) or _text(fallback)
    unique_name = _first_text(identity, "uniqueName", "unique_name", "mailAddress", "email")
    email = _first_text(identity, "email", "mailAddress", "uniqueName", "unique_name")
    return {
        "display_name": display_name,
        "unique_name": unique_name,
        "email": email if email and "@" in email else None,
    }


def _first_text(mapping: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _text(mapping.get(key))
        if value is not None:
            return value
    return None


def _mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="python")
    return {}


def _model_payload(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="python")
    raise TypeError(f"Expected mapping-like response, got {type(value).__name__}")


def _next_skip(paging: Mapping[str, Any]) -> int | None:
    value = paging.get("next_skip")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _merge_counts(total: dict[str, int], counts: Mapping[str, int], records: int) -> None:
    total["inserted"] += int(counts.get("inserted", 0))
    total["updated"] += int(counts.get("updated", 0))
    total["records"] += records


def _merge_totals(total: dict[str, int], result: Mapping[str, Any]) -> None:
    total["inserted"] += int(result.get("inserted", 0))
    total["updated"] += int(result.get("updated", 0))
    total["records"] += int(result.get("records", 0))


def _sync_key(
    *,
    directory_path: str,
    scope: str,
    project: str | None,
    repo: str | None,
) -> str:
    return ":".join(
        [
            directory_path or "root",
            scope,
            project or "*",
            repo or "*",
        ]
    )


def _datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
