from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class WorkItemSource:
    organization: str
    project: str
    work_item_url: str


@dataclass(frozen=True, slots=True)
class WorkItemTask:
    issue_number: int
    name: str
    description: str
    source: WorkItemSource
    tags: tuple[str, ...] = ()
    state: str | None = None
    changed_date: str | None = None


class TaskYamlSerializer:
    """Serialize one Azure work item task per YAML file in a host queue directory."""

    def __init__(self, queue_dir: Path) -> None:
        self.queue_dir = queue_dir
        self.queue_dir.mkdir(parents=True, exist_ok=True)

    def serialize(self, task: WorkItemTask) -> Path:
        file_path = self.queue_dir / f"{task.issue_number}.yaml"
        payload = _build_payload(task)
        _atomic_write_yaml(file_path, payload)
        return file_path


def _build_payload(task: WorkItemTask) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "issue_number": task.issue_number,
        "name": task.name,
        "description": task.description,
        "source": {
            "organization": task.source.organization,
            "project": task.source.project,
            "work_item_url": task.source.work_item_url,
        },
    }

    if task.tags:
        payload["tags"] = sorted(task.tags)
    if task.state:
        payload["state"] = task.state
    if task.changed_date:
        payload["changed_date"] = _normalize_changed_date(task.changed_date)

    return payload


def _normalize_changed_date(value: str) -> str:
    parsed = _maybe_parse_iso_datetime(value)
    return parsed if parsed is not None else value


def _maybe_parse_iso_datetime(value: str) -> str | None:
    normalized = value.strip()
    if not normalized:
        return None

    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    return parsed.isoformat()


def _atomic_write_yaml(path: Path, payload: dict[str, Any]) -> None:
    rendered = yaml.safe_dump(
        payload,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )

    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temp_file:
        temp_file.write(rendered)
        temp_file.flush()
        os.fsync(temp_file.fileno())
        temp_path = Path(temp_file.name)

    temp_path.replace(path)
