from __future__ import annotations

import argparse
import base64
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx


@dataclass(frozen=True)
class WorkerConfig:
    organization_url: str
    project: str
    pat_env_var_name: str
    poll_interval_seconds: float
    area_filters: tuple[str, ...]
    team_filters: tuple[str, ...]

    @classmethod
    def load_from_env(cls) -> WorkerConfig:
        organization_url = _require_env("ADO_ORGANIZATION_URL")
        project = _require_env("ADO_PROJECT")
        pat_env_var_name = os.environ.get("ADO_PAT_ENV_VAR_NAME", "AZURE_DEVOPS_PAT").strip()
        if not pat_env_var_name:
            raise ValueError("ADO_PAT_ENV_VAR_NAME must not be empty.")

        poll_interval_raw = os.environ.get("ADO_POLL_INTERVAL_SECONDS", "30")
        try:
            poll_interval_seconds = float(poll_interval_raw)
        except ValueError as exc:
            raise ValueError("ADO_POLL_INTERVAL_SECONDS must be numeric.") from exc
        if poll_interval_seconds <= 0:
            raise ValueError("ADO_POLL_INTERVAL_SECONDS must be > 0.")

        return cls(
            organization_url=organization_url.rstrip("/"),
            project=project,
            pat_env_var_name=pat_env_var_name,
            poll_interval_seconds=poll_interval_seconds,
            area_filters=_csv_env("ADO_AREA_FILTERS"),
            team_filters=_csv_env("ADO_TEAM_FILTERS"),
        )


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Environment variable {name} is required.")
    return value


def _csv_env(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    values = [part.strip() for part in raw.split(",") if part.strip()]
    return tuple(values)


def _escape_wiql_string(value: str) -> str:
    return value.replace("'", "''")


def _build_wiql(config: WorkerConfig) -> str:
    clauses = [
        "[System.TeamProject] = @project",
        "[System.State] = 'Committed'",
        "[System.Tags] CONTAINS 'true-agent'",
    ]

    if config.area_filters:
        area_clause = " OR ".join(
            f"[System.AreaPath] UNDER '{_escape_wiql_string(area)}'" for area in config.area_filters
        )
        clauses.append(f"({area_clause})")

    if config.team_filters:
        team_clause = " OR ".join(
            f"[System.IterationPath] UNDER '{_escape_wiql_string(team)}'" for team in config.team_filters
        )
        clauses.append(f"({team_clause})")

    where_clause = " AND ".join(clauses)
    return (
        "SELECT [System.Id] "
        "FROM WorkItems "
        f"WHERE {where_clause} "
        "ORDER BY [System.ChangedDate] ASC, [System.Id] ASC"
    )


def _build_auth_header(pat: str) -> str:
    token = base64.b64encode(f":{pat}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _normalize_tags(raw_tags: str | None) -> set[str]:
    if not raw_tags:
        return set()
    return {tag.strip().lower() for tag in raw_tags.split(";") if tag.strip()}


def _parse_ado_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)

    normalized = value
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def fetch_candidate_ids(client: httpx.Client, config: WorkerConfig, auth_header: str) -> list[int]:
    wiql_url = f"{config.organization_url}/{config.project}/_apis/wit/wiql"
    response = client.post(
        wiql_url,
        params={"api-version": "7.1"},
        headers={"Authorization": auth_header},
        json={"query": _build_wiql(config)},
    )
    response.raise_for_status()
    payload = response.json()
    return [item["id"] for item in payload.get("workItems", []) if isinstance(item.get("id"), int)]


def fetch_work_item_batch(
    client: httpx.Client,
    config: WorkerConfig,
    auth_header: str,
    candidate_ids: list[int],
) -> list[dict[str, Any]]:
    if not candidate_ids:
        return []

    batch_url = f"{config.organization_url}/{config.project}/_apis/wit/workitemsbatch"
    response = client.post(
        batch_url,
        params={"api-version": "7.1"},
        headers={"Authorization": auth_header},
        json={
            "ids": candidate_ids,
            "fields": [
                "System.Id",
                "System.Title",
                "System.Description",
                "System.Tags",
                "System.State",
                "System.ChangedDate",
                "System.AreaPath",
                "System.IterationPath",
            ],
            "errorPolicy": "Omit",
        },
    )
    response.raise_for_status()
    return response.json().get("value", [])


def gate_and_order_items(items: list[dict[str, Any]], config: WorkerConfig) -> tuple[list[dict[str, Any]], int]:
    matched: list[dict[str, Any]] = []
    skipped = 0

    for item in items:
        fields = item.get("fields", {})
        state = str(fields.get("System.State", "")).strip()
        normalized_tags = _normalize_tags(fields.get("System.Tags"))

        if state != "Committed" or "true-agent" not in normalized_tags:
            skipped += 1
            continue

        area_path = str(fields.get("System.AreaPath", "")).strip()
        if config.area_filters and not any(area_path.startswith(prefix) for prefix in config.area_filters):
            skipped += 1
            continue

        team_path = str(fields.get("System.IterationPath", "")).strip()
        if config.team_filters and not any(team_path.startswith(prefix) for prefix in config.team_filters):
            skipped += 1
            continue

        work_item_id = item.get("id")
        if not isinstance(work_item_id, int):
            skipped += 1
            continue

        matched.append(
            {
                "id": work_item_id,
                "title": str(fields.get("System.Title", "")).strip(),
                "description": str(fields.get("System.Description", "") or ""),
                "tags": sorted(normalized_tags),
                "state": state,
                "url": item.get("url", ""),
                "updated_date": str(fields.get("System.ChangedDate", "")),
            }
        )

    matched.sort(key=lambda work_item: (_parse_ado_datetime(work_item["updated_date"]), work_item["id"]))
    return matched, skipped


def poll_once(client: httpx.Client, config: WorkerConfig, auth_header: str) -> list[dict[str, Any]]:
    candidate_ids = fetch_candidate_ids(client=client, config=config, auth_header=auth_header)
    fetched_items = fetch_work_item_batch(
        client=client,
        config=config,
        auth_header=auth_header,
        candidate_ids=candidate_ids,
    )
    matched_items, skipped_count = gate_and_order_items(fetched_items, config)

    logging.info(
        "cycle polled=%d matched=%d skipped=%d",
        len(candidate_ids),
        len(matched_items),
        skipped_count,
    )
    return matched_items


def run_worker(config: WorkerConfig) -> None:
    pat = _require_env(config.pat_env_var_name)
    auth_header = _build_auth_header(pat)

    timeout = httpx.Timeout(connect=5.0, write=10.0, read=20.0, pool=5.0)
    with httpx.Client(timeout=timeout) as client:
        while True:
            cycle_start = time.monotonic()
            try:
                poll_once(client=client, config=config, auth_header=auth_header)
            except httpx.HTTPError as exc:
                logging.warning("cycle error=%s", exc.__class__.__name__)
            except Exception:  # pragma: no cover
                logging.exception("cycle error=unexpected")

            elapsed = time.monotonic() - cycle_start
            sleep_seconds = max(0.0, config.poll_interval_seconds - elapsed)
            time.sleep(sleep_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll Azure DevOps work items for true-agent candidates.")
    parser.add_argument("--log-level", default="INFO", help="Python logging level.")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    config = WorkerConfig.load_from_env()
    run_worker(config)


if __name__ == "__main__":
    main()
