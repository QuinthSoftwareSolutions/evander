from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import os
from pathlib import Path
from typing import Any

from evander.issues.service import IssueService
from evander.services import AzureDevOpsPrQueryProvider, PrQueryService

from .models import ADDON_ID
from .repository import AdoActivityRepository

PAGE_SIZE = 200


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
        if repo is None and any(item in scopes for item in ("pr_comments", "pr_commits")):
            raise ValueError("repo is required when syncing PR activity")

        totals = {"inserted": 0, "updated": 0, "records": 0}
        details: dict[str, Any] = {}
        sync_until = until or datetime.now(UTC)
        if "work_items" in scopes:
            result = self._sync_work_items(
                since=since,
                until=until,
                directory_path=directory_path,
                actor=actor,
                project=project,
                states=states or [],
                types=types or [],
                tags_any=tags_any or [],
            )
            details["work_items"] = result
            _merge_totals(totals, result)
            if result["complete"]:
                self.repository.update_sync_state(
                    sync_key=_sync_key(
                        directory_path=directory_path,
                        scope="work_items",
                        project=project,
                        repo=None,
                    ),
                    complete_through=sync_until,
                    metadata={"actor": actor, "states": states or [], "types": types or []},
                )
        if "pr_comments" in scopes:
            result = self._sync_pr_comments(
                since=since,
                until=until,
                directory_path=directory_path,
                actor=actor,
                project=project,
                repo=repo or "",
                statuses=statuses or [],
                include_system_comments=include_system_comments,
            )
            details["pr_comments"] = result
            _merge_totals(totals, result)
            if result["complete"]:
                self.repository.update_sync_state(
                    sync_key=_sync_key(
                        directory_path=directory_path,
                        scope="pr_comments",
                        project=project,
                        repo=repo,
                    ),
                    complete_through=sync_until,
                    metadata={"actor": actor, "statuses": statuses or []},
                )
        if "pr_commits" in scopes:
            result = self._sync_pr_commits(
                since=since,
                until=until,
                directory_path=directory_path,
                actor=actor,
                project=project,
                repo=repo or "",
                statuses=statuses or [],
            )
            details["pr_commits"] = result
            _merge_totals(totals, result)
            if result["complete"]:
                self.repository.update_sync_state(
                    sync_key=_sync_key(
                        directory_path=directory_path,
                        scope="pr_commits",
                        project=project,
                        repo=repo,
                    ),
                    complete_through=sync_until,
                    metadata={"actor": actor, "statuses": statuses or []},
                )

        return {
            "addon_id": ADDON_ID,
            "directory_path": directory_path,
            "scope": normalized_scope,
            "since": _iso(since),
            "until": _iso(sync_until),
            "inserted": totals["inserted"],
            "updated": totals["updated"],
            "records": totals["records"],
            "details": details,
        }

    def _sync_work_items(
        self,
        *,
        since: datetime,
        until: datetime | None,
        directory_path: str,
        actor: str | None,
        project: str | None,
        states: list[str],
        types: list[str],
        tags_any: list[str],
    ) -> dict[str, Any]:
        issue_service = IssueService(self.provider_config_resolver)
        skip = 0
        total = {"inserted": 0, "updated": 0, "records": 0}
        complete = True
        pages = 0
        while True:
            payload = issue_service.query_remote_work_item_activity(
                provider="azure_devops",
                directory_path=directory_path,
                project=project,
                actor=actor,
                since=since,
                until=until,
                activity_types=["created", "changed", "commented"],
                states=states,
                types=types,
                tags_any=tags_any,
                top=PAGE_SIZE,
                skip=skip,
                exhaustive=True,
                max_candidates=1000,
            )
            records = _normalize_work_item_records(payload, directory_path=directory_path)
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

    def _sync_pr_comments(
        self,
        *,
        since: datetime,
        until: datetime | None,
        directory_path: str,
        actor: str | None,
        project: str | None,
        repo: str,
        statuses: list[str],
        include_system_comments: bool,
    ) -> dict[str, Any]:
        service = self._pr_query_service(directory_path)
        skip = 0
        total = {"inserted": 0, "updated": 0, "records": 0}
        complete = True
        pages = 0
        while True:
            response = service.list_ado_pr_comment_activity(
                repo=repo,
                author=actor,
                since=since,
                until=until,
                project=project,
                statuses=statuses,
                include_system_comments=include_system_comments,
                top=PAGE_SIZE,
                skip=skip,
                exhaustive=True,
                max_candidates=1000,
            )
            payload = _model_payload(response)
            records = _normalize_pr_comment_records(payload, directory_path=directory_path)
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

    def _sync_pr_commits(
        self,
        *,
        since: datetime,
        until: datetime | None,
        directory_path: str,
        actor: str | None,
        project: str | None,
        repo: str,
        statuses: list[str],
    ) -> dict[str, Any]:
        service = self._pr_query_service(directory_path)
        skip = 0
        total = {"inserted": 0, "updated": 0, "records": 0}
        complete = True
        pages = 0
        while True:
            response = service.list_ado_pr_commit_activity(
                repo=repo,
                author=actor,
                since=since,
                until=until,
                project=project,
                statuses=statuses,
                top=PAGE_SIZE,
                skip=skip,
                exhaustive=True,
                max_candidates=1000,
            )
            payload = _model_payload(response)
            records = _normalize_pr_commit_records(payload, directory_path=directory_path)
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

    def _pr_query_service(self, directory_path: str) -> PrQueryService:
        resolve_for_directory = getattr(self.provider_config_resolver, "resolve_for_directory", None)
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


def _expand_scope(scope: str) -> tuple[str, ...]:
    if scope == "all":
        return ("work_items", "pr_comments", "pr_commits")
    if scope not in {"work_items", "pr_comments", "pr_commits"}:
        raise ValueError("scope must be one of: work_items, pr_comments, pr_commits, all")
    return (scope,)


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
