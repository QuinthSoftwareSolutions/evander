from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evander.daemons.proxy_issue_sync import upsert_ado_proxy_issue_from_remote_payload
from evander.models import DirectoryPath, LocalIssueRecord
from evander.storage import JsonStore


@dataclass(frozen=True)
class AdoProxyIssueUpsertResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    tickets: list[LocalIssueRecord] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return not self.errors

    def model_dump(self) -> dict[str, Any]:
        tickets = [ticket.model_dump(mode="json") for ticket in self.tickets]
        payload: dict[str, Any] = {
            "success": self.success,
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "tickets": tickets,
            "count": len(tickets),
            "errors": [dict(error) for error in self.errors],
        }
        if len(tickets) == 1:
            payload["ticket"] = tickets[0]
        return payload


def upsert_ado_proxy_issue_items(
    *,
    runtime_dir: Path,
    directory_path: str,
    items: list[dict[str, Any]],
    project: str | None = None,
    organization_url: str | None = None,
    repo_source: str | None = None,
    yaml_output_dir: Path | None = None,
    source: str = "ado_proxy_upsert",
    now: datetime | None = None,
) -> AdoProxyIssueUpsertResult:
    normalized_directory_path = DirectoryPath(segments=directory_path).as_posix()
    output_dir = yaml_output_dir or runtime_dir / "ado-work-items"
    timestamp = _utc(now) or datetime.now(UTC)
    tickets: list[LocalIssueRecord] = []
    errors: list[dict[str, str]] = []
    created = 0
    updated = 0
    skipped = 0

    for index, raw_item in enumerate(items):
        try:
            remote_payload = normalize_ado_work_item_payload(raw_item)
        except ValueError as exc:
            skipped += 1
            errors.append({"index": str(index), "error": str(exc)})
            continue

        before = _local_issue_ids(runtime_dir)
        ticket = upsert_ado_proxy_issue_from_remote_payload(
            runtime_dir=runtime_dir,
            directory_path=normalized_directory_path,
            project=project,
            organization_url=organization_url,
            yaml_output_dir=output_dir,
            repo_source=repo_source,
            source=source,
            remote_payload=remote_payload,
            now=timestamp,
        )
        if ticket.issue_id in before:
            updated += 1
        else:
            created += 1
        tickets.append(ticket)

    return AdoProxyIssueUpsertResult(
        created=created,
        updated=updated,
        skipped=skipped,
        tickets=tickets,
        errors=errors,
    )


def normalize_ado_work_item_payload(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("ADO work item must be an object")
    fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
    work_item_id = _first_text(
        item.get("remote_issue_id"),
        item.get("work_item_id"),
        item.get("id"),
        fields.get("System.Id"),
    )
    if not work_item_id:
        raise ValueError("ADO work item is missing id")

    description = _first_present_text(item, "description")
    if description is None:
        description = _combine_description_with_ado_sections(
            str(fields.get("System.Description") or ""),
            repro_steps=str(fields.get("Microsoft.VSTS.TCM.ReproSteps") or ""),
            system_info=str(fields.get("Microsoft.VSTS.TCM.SystemInfo") or ""),
            acceptance_criteria=str(
                fields.get("Microsoft.VSTS.Common.AcceptanceCriteria") or ""
            ),
        )

    payload: dict[str, Any] = {
        "remote_issue_id": work_item_id,
        "id": work_item_id,
        "title": _first_text(item.get("title"), fields.get("System.Title"))
        or f"ADO work item {work_item_id}",
        "description": description,
        "state": _first_text(item.get("state"), fields.get("System.State")),
        "type": _first_text(item.get("type"), fields.get("System.WorkItemType")),
        "iteration": _first_text(item.get("iteration"), fields.get("System.IterationPath")),
        "area": _first_text(item.get("area"), fields.get("System.AreaPath")),
        "assigned_to": item.get("assigned_to") or fields.get("System.AssignedTo"),
        "url": _first_text(item.get("url"), _web_link(item), item.get("web_url")),
        "updated_at": _first_text(
            item.get("updated_at"),
            item.get("changed_at"),
            item.get("updated_date"),
            fields.get("System.ChangedDate"),
        ),
        "fields": {str(key): value for key, value in fields.items()},
    }
    if "tags" in item:
        payload["tags"] = item.get("tags")
    elif "System.Tags" in fields:
        payload["tags"] = fields.get("System.Tags")
    if isinstance(item.get("comments"), list):
        payload["comments"] = [
            dict(comment) for comment in item["comments"] if isinstance(comment, dict)
        ]
    return payload


def _local_issue_ids(runtime_dir: Path) -> set[str]:
    return set(JsonStore(runtime_dir / "local-issues.json", LocalIssueRecord).read_all())


def _combine_description_with_ado_sections(
    description: str,
    *,
    repro_steps: str = "",
    system_info: str = "",
    acceptance_criteria: str = "",
) -> str:
    parts: list[str] = []
    if description.strip():
        parts.append(description.strip())
    for header, content in (
        ("Repro Steps", repro_steps),
        ("System Info", system_info),
        ("Acceptance Criteria", acceptance_criteria),
    ):
        if content.strip():
            parts.append(f"{header}\n\n{content.strip()}")
    return "\n\n".join(parts)


def _first_present_text(item: dict[str, Any], key: str) -> str | None:
    if key not in item:
        return None
    value = item.get(key)
    return "" if value is None else str(value)


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _web_link(item: dict[str, Any]) -> str | None:
    links = item.get("_links")
    if not isinstance(links, dict):
        return None
    web = links.get("web")
    if not isinstance(web, dict):
        return None
    return _first_text(web.get("href"))


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
