from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

import httpx
import yaml


@dataclass(frozen=True)
class WorkerConfig:
    organization_url: str
    project: str
    pat_env_var_name: str
    poll_interval_seconds: float
    area_filters: tuple[str, ...]
    team_filters: tuple[str, ...]
    runtime_dir: Path
    yaml_output_dir: Path
    processed_store_path: Path
    setup_script_path: Path
    project_or_solution: str
    repo_path: str | None
    session_id: str | None
    vm_user: str | None

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

        runtime_dir = Path(os.environ.get("ADO_RUNTIME_DIR", "runtime")).expanduser()
        yaml_output_dir = Path(
            os.environ.get("ADO_YAML_OUTPUT_DIR", str(runtime_dir / "ado-work-items"))
        ).expanduser()
        processed_store_path = Path(
            os.environ.get("ADO_PROCESSED_STORE_PATH", str(runtime_dir / "ado-processed-work-items.json"))
        ).expanduser()

        project_or_solution = _require_env("ADO_PROJECT_OR_SOLUTION")
        setup_script_path = Path(
            os.environ.get("ADO_SETUP_ISSUE_VM_SCRIPT", "scripts/setup_issue_vm.sh")
        ).expanduser()

        repo_path = _optional_env("ADO_REPO_PATH")
        session_id = _optional_env("ADO_SESSION_ID")
        vm_user = _optional_env("ADO_VM_USER")

        return cls(
            organization_url=organization_url.rstrip("/"),
            project=project,
            pat_env_var_name=pat_env_var_name,
            poll_interval_seconds=poll_interval_seconds,
            area_filters=_csv_env("ADO_AREA_FILTERS"),
            team_filters=_csv_env("ADO_TEAM_FILTERS"),
            runtime_dir=runtime_dir,
            yaml_output_dir=yaml_output_dir,
            processed_store_path=processed_store_path,
            setup_script_path=setup_script_path,
            project_or_solution=project_or_solution,
            repo_path=repo_path,
            session_id=session_id,
            vm_user=vm_user,
        )


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Environment variable {name} is required.")
    return value


def _optional_env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


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


class ProcessedWorkItemStore:
    """Atomic JSON state keyed by Azure work item id."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def is_processed(self, work_item_id: int) -> bool:
        with self._lock:
            payload = self._read_raw()
            return str(work_item_id) in payload

    def mark_processed(
        self,
        work_item_id: int,
        yaml_path: Path,
        script_exit_code: int,
        output_snippet: str,
    ) -> None:
        with self._lock:
            payload = self._read_raw()
            payload[str(work_item_id)] = {
                "processed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "yaml_path": str(yaml_path),
                "script_exit_code": script_exit_code,
                "script_output_snippet": output_snippet,
            }
            self._atomic_write(payload)

    def forget(self, work_item_id: int) -> bool:
        with self._lock:
            payload = self._read_raw()
            existed = payload.pop(str(work_item_id), None) is not None
            self._atomic_write(payload)
            return existed

    def _read_raw(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)


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


def _build_issue_payload(work_item: dict[str, Any]) -> dict[str, Any]:
    return {
        "issue_number": work_item["id"],
        "title": work_item.get("title", ""),
        "description": work_item.get("description", ""),
        "tags": work_item.get("tags", []),
        "state": work_item.get("state", ""),
        "url": work_item.get("url", ""),
        "updated_date": work_item.get("updated_date", ""),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _atomic_write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    tmp_path.replace(path)


def _build_setup_command(config: WorkerConfig, yaml_path: Path) -> list[str]:
    cmd = [str(config.setup_script_path), str(yaml_path), config.project_or_solution]
    if config.repo_path:
        cmd.append(config.repo_path)
    if config.session_id:
        cmd.append(config.session_id)
    if config.vm_user:
        cmd.append(config.vm_user)
    return cmd


def _snippet(text: str, max_chars: int = 1000) -> str:
    compact = " ".join(text.split())
    return compact[:max_chars]


def process_work_item(
    work_item: dict[str, Any],
    *,
    config: WorkerConfig,
    store: ProcessedWorkItemStore,
) -> bool:
    work_item_id = int(work_item["id"])
    if store.is_processed(work_item_id):
        logging.info("work_item=%s skipped=already_processed", work_item_id)
        return False

    yaml_path = config.yaml_output_dir / f"work-item-{work_item_id}.yaml"
    _atomic_write_yaml(yaml_path, _build_issue_payload(work_item))

    cmd = _build_setup_command(config, yaml_path)
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    output_snippet = _snippet((result.stdout or "") + "\n" + (result.stderr or ""))
    if result.returncode != 0:
        logging.warning("work_item=%s setup_issue_vm_failed exit_code=%d", work_item_id, result.returncode)
        return False

    store.mark_processed(
        work_item_id=work_item_id,
        yaml_path=yaml_path,
        script_exit_code=result.returncode,
        output_snippet=output_snippet,
    )
    logging.info("work_item=%s processed yaml=%s", work_item_id, yaml_path)
    return True


def fetch_work_item_by_id(
    client: httpx.Client,
    config: WorkerConfig,
    auth_header: str,
    work_item_id: int,
) -> dict[str, Any] | None:
    items = fetch_work_item_batch(client, config, auth_header, [work_item_id])
    matched, _ = gate_and_order_items(items, config)
    return matched[0] if matched else None


def run_worker(config: WorkerConfig) -> None:
    pat = _require_env(config.pat_env_var_name)
    auth_header = _build_auth_header(pat)
    store = ProcessedWorkItemStore(config.processed_store_path)

    timeout = httpx.Timeout(connect=5.0, write=10.0, read=20.0, pool=5.0)
    with httpx.Client(timeout=timeout) as client:
        while True:
            cycle_start = time.monotonic()
            try:
                for item in poll_once(client=client, config=config, auth_header=auth_header):
                    process_work_item(item, config=config, store=store)
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
    parser.add_argument(
        "--reprocess",
        type=int,
        help="Force reprocess a specific work item ID by forgetting previous state first.",
    )
    parser.add_argument(
        "--forget",
        type=int,
        help="Forget a specific work item ID in the processed store, then exit.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    config = WorkerConfig.load_from_env()
    store = ProcessedWorkItemStore(config.processed_store_path)

    if args.forget is not None:
        removed = store.forget(args.forget)
        logging.info("forget work_item=%s removed=%s", args.forget, removed)
        return

    if args.reprocess is not None:
        store.forget(args.reprocess)
        pat = _require_env(config.pat_env_var_name)
        auth_header = _build_auth_header(pat)
        timeout = httpx.Timeout(connect=5.0, write=10.0, read=20.0, pool=5.0)
        with httpx.Client(timeout=timeout) as client:
            item = fetch_work_item_by_id(
                client=client,
                config=config,
                auth_header=auth_header,
                work_item_id=args.reprocess,
            )
            if item is None:
                logging.warning("reprocess work_item=%s not_found_or_not_eligible", args.reprocess)
                return
            process_work_item(item, config=config, store=store)
        return

    run_worker(config)


if __name__ == "__main__":
    main()
