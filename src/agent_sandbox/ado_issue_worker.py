from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import BoundedSemaphore
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
    setup_log_dir: Path
    processed_store_path: Path
    setup_script_path: Path
    prewarm_target: str
    repo_path: str | None
    session_id: str | None
    vm_user: str | None
    setup_timeout_seconds: float
    setup_concurrency_limit: int
    setup_lock_path: Path
    fetch_retry_count: int
    fetch_retry_backoff_seconds: float
    bootstrap_dotnet: bool
    bootstrap_nuget: bool
    bootstrap_mssql: bool
    include_comments: bool
    comments_max_count: int
    comment_text_max_chars: int

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
        setup_log_dir = Path(
            os.environ.get("ADO_SETUP_LOG_DIR", str(runtime_dir / "setup-logs"))
        ).expanduser()
        processed_store_path = Path(
            os.environ.get("ADO_PROCESSED_STORE_PATH", str(runtime_dir / "ado-processed-work-items.json"))
        ).expanduser()

        prewarm_target = _optional_env("ADO_PREWARM_TARGET") or _require_env("ADO_PROJECT_OR_SOLUTION")
        setup_script_path = Path(
            os.environ.get("ADO_SETUP_ISSUE_VM_SCRIPT", "scripts/setup_issue_vm.sh")
        ).expanduser()
        setup_timeout_seconds = _float_env("ADO_SETUP_TIMEOUT_SECONDS", 1800.0, minimum=1.0)
        setup_concurrency_limit = _int_env("ADO_SETUP_CONCURRENCY_LIMIT", 1, minimum=1)
        setup_lock_path = Path(
            os.environ.get("ADO_SETUP_LOCK_PATH", str(runtime_dir / "setup-issue-vm.lock"))
        ).expanduser()
        fetch_retry_count = _int_env("ADO_FETCH_RETRY_COUNT", 2, minimum=0)
        fetch_retry_backoff_seconds = _float_env("ADO_FETCH_RETRY_BACKOFF_SECONDS", 2.0, minimum=0.0)
        bootstrap_dotnet = _bool_env("ADO_BOOTSTRAP_DOTNET", True)
        bootstrap_nuget = _bool_env("ADO_BOOTSTRAP_NUGET", True)
        bootstrap_mssql = _bool_env("ADO_BOOTSTRAP_MSSQL", True)
        include_comments = _bool_env("ADO_INCLUDE_COMMENTS", True)
        comments_max_count = _int_env("ADO_COMMENTS_MAX_COUNT", 20, minimum=1)
        comment_text_max_chars = _int_env("ADO_COMMENT_TEXT_MAX_CHARS", 4000, minimum=1)

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
            setup_log_dir=setup_log_dir,
            processed_store_path=processed_store_path,
            setup_script_path=setup_script_path,
            prewarm_target=prewarm_target,
            repo_path=repo_path,
            session_id=session_id,
            vm_user=vm_user,
            setup_timeout_seconds=setup_timeout_seconds,
            setup_concurrency_limit=setup_concurrency_limit,
            setup_lock_path=setup_lock_path,
            fetch_retry_count=fetch_retry_count,
            fetch_retry_backoff_seconds=fetch_retry_backoff_seconds,
            bootstrap_dotnet=bootstrap_dotnet,
            bootstrap_nuget=bootstrap_nuget,
            bootstrap_mssql=bootstrap_mssql,
            include_comments=include_comments,
            comments_max_count=comments_max_count,
            comment_text_max_chars=comment_text_max_chars,
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


def _float_env(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric.") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}.")
    return value


def _int_env(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}.")
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value (true/false).")


def _escape_wiql_string(value: str) -> str:
    return value.replace("'", "''")


def _build_wiql(config: WorkerConfig) -> str:
    clauses = [
        "[System.TeamProject] = @project",
        "[System.State] = 'Committed'",
        "[System.Tags] CONTAINS 'truman'",
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


def update_work_item_state(
    client: httpx.Client,
    config: WorkerConfig,
    auth_header: str,
    work_item_id: int,
    state: str,
) -> None:
    work_item_url = f"{config.organization_url}/{config.project}/_apis/wit/workitems/{work_item_id}"
    response = client.patch(
        work_item_url,
        params={"api-version": "7.1"},
        headers={
            "Authorization": auth_header,
            "Content-Type": "application/json-patch+json",
        },
        json=[
            {
                "op": "add",
                "path": "/fields/System.State",
                "value": state,
            }
        ],
    )
    response.raise_for_status()


def _normalize_comment_text(raw_text: Any, *, max_chars: int) -> str:
    if raw_text is None:
        return ""
    text = str(raw_text).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def fetch_work_item_comments(
    client: httpx.Client,
    config: WorkerConfig,
    auth_header: str,
    work_item_id: int,
) -> list[dict[str, str]]:
    comments_url = (
        f"{config.organization_url}/{config.project}/_apis/wit/workItems/{work_item_id}/comments"
    )
    response = client.get(
        comments_url,
        params={
            "api-version": "7.1-preview.4",
            "$top": str(config.comments_max_count),
            "order": "asc",
        },
        headers={"Authorization": auth_header},
    )
    response.raise_for_status()
    raw_comments = response.json().get("comments", [])

    normalized: list[dict[str, str]] = []
    for comment in raw_comments:
        text = _normalize_comment_text(
            comment.get("text", ""),
            max_chars=config.comment_text_max_chars,
        )
        if not text:
            continue
        revised_by = comment.get("revisedBy", {})
        normalized.append(
            {
                "text": text,
                "author": str(revised_by.get("displayName", "")).strip(),
                "created_date": str(comment.get("createdDate", "")),
                "modified_date": str(comment.get("modifiedDate", "")),
            }
        )
    return normalized


def _attach_comments_to_item(
    *,
    client: httpx.Client,
    config: WorkerConfig,
    auth_header: str,
    item: dict[str, Any],
) -> None:
    if not config.include_comments:
        return

    work_item_id = int(item["id"])
    try:
        item["comments"] = fetch_work_item_comments(
            client=client,
            config=config,
            auth_header=auth_header,
            work_item_id=work_item_id,
        )
    except httpx.HTTPError:
        logging.warning("work_item=%s comments_fetch_failed", work_item_id)
        item["comments"] = []


def gate_and_order_items(items: list[dict[str, Any]], config: WorkerConfig) -> tuple[list[dict[str, Any]], int]:
    matched: list[dict[str, Any]] = []
    skipped = 0

    for item in items:
        fields = item.get("fields", {})
        state = str(fields.get("System.State", "")).strip()
        normalized_tags = _normalize_tags(fields.get("System.Tags"))

        if state != "Committed" or "truman" not in normalized_tags:
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
    for item in matched_items:
        _attach_comments_to_item(
            client=client,
            config=config,
            auth_header=auth_header,
            item=item,
        )

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
        "comments": work_item.get("comments", []),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _atomic_write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    tmp_path.replace(path)


def _write_setup_log(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    header = f"===== {timestamp} =====\n"
    with path.open("a", encoding="utf-8") as handle:
        if handle.tell() > 0:
            handle.write("\n")
        handle.write(header)
        handle.write(content)
        if not content.endswith("\n"):
            handle.write("\n")


def _build_setup_command(config: WorkerConfig, yaml_path: Path) -> list[str]:
    cmd = [str(config.setup_script_path)]
    if config.bootstrap_dotnet:
        cmd.append("--dotnet")
    if config.bootstrap_nuget:
        cmd.append("--nuget")
    if config.bootstrap_mssql:
        cmd.append("--mssql")
    cmd.append(str(yaml_path))
    if config.bootstrap_nuget:
        cmd.append(config.prewarm_target)
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


def _is_vm_already_exists_output(output: str) -> bool:
    return "VM already exists:" in output


def _is_transient_fetch_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        return exc.response.status_code in {408, 429, 500, 502, 503, 504}
    return False


_SETUP_LIMITERS: dict[Path, BoundedSemaphore] = {}
_SETUP_LIMITERS_LOCK = Lock()


@contextmanager
def _setup_slot(config: WorkerConfig) -> Any:
    if config.setup_concurrency_limit <= 1:
        config.setup_lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config.setup_lock_path, "a+", encoding="utf-8") as lock_file:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        return

    with _SETUP_LIMITERS_LOCK:
        limiter = _SETUP_LIMITERS.get(config.setup_lock_path)
        if limiter is None:
            limiter = BoundedSemaphore(config.setup_concurrency_limit)
            _SETUP_LIMITERS[config.setup_lock_path] = limiter
    with limiter:
        yield


def process_work_item(
    work_item: dict[str, Any],
    *,
    config: WorkerConfig,
    store: ProcessedWorkItemStore,
    client: httpx.Client | None = None,
    auth_header: str | None = None,
) -> bool:
    work_item_id = int(work_item["id"])
    if store.is_processed(work_item_id):
        logging.info("work_item=%s skipped=already_processed", work_item_id)
        return False

    yaml_path = config.yaml_output_dir / f"work-item-{work_item_id}.yaml"
    setup_log_path = config.setup_log_dir / f"work-item-{work_item_id}.log"
    _atomic_write_yaml(yaml_path, _build_issue_payload(work_item))

    cmd = _build_setup_command(config, yaml_path)
    try:
        with _setup_slot(config):
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=config.setup_timeout_seconds,
            )
    except subprocess.TimeoutExpired as exc:
        combined = f"{(exc.stdout or '')}\n{(exc.stderr or '')}"
        _write_setup_log(setup_log_path, combined)
        logging.error(
            "work_item=%s setup_issue_vm_timeout timeout_seconds=%s cmd=%s output=%s log=%s",
            work_item_id,
            config.setup_timeout_seconds,
            cmd,
            _snippet(combined),
            setup_log_path,
        )
        return False

    combined_output = (result.stdout or "") + "\n" + (result.stderr or "")
    _write_setup_log(setup_log_path, combined_output)
    output_snippet = _snippet(combined_output)
    if result.returncode != 0:
        if _is_vm_already_exists_output(combined_output):
            logging.warning(
                "work_item=%s setup_issue_vm_already_exists treating_as_success cmd=%s output=%s log=%s",
                work_item_id,
                cmd,
                output_snippet,
                setup_log_path,
            )
        else:
            logging.error(
                "work_item=%s setup_issue_vm_failed exit_code=%d cmd=%s output=%s log=%s",
                work_item_id,
                result.returncode,
                cmd,
                output_snippet,
                setup_log_path,
            )
            return False

    store.mark_processed(
        work_item_id=work_item_id,
        yaml_path=yaml_path,
        script_exit_code=result.returncode,
        output_snippet=output_snippet,
    )
    if client is not None and auth_header:
        try:
            update_work_item_state(
                client=client,
                config=config,
                auth_header=auth_header,
                work_item_id=work_item_id,
                state="In Progress",
            )
        except httpx.HTTPError as exc:
            logging.warning(
                "work_item=%s state_update_failed target_state=%s error=%s",
                work_item_id,
                "In Progress",
                exc.__class__.__name__,
            )
        else:
            logging.info("work_item=%s state_updated=%s", work_item_id, "In Progress")
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
    if not matched:
        return None
    item = matched[0]
    _attach_comments_to_item(
        client=client,
        config=config,
        auth_header=auth_header,
        item=item,
    )
    return item


def run_worker(config: WorkerConfig) -> None:
    pat = _require_env(config.pat_env_var_name)
    auth_header = _build_auth_header(pat)
    store = ProcessedWorkItemStore(config.processed_store_path)

    timeout = httpx.Timeout(connect=5.0, write=10.0, read=20.0, pool=5.0)
    with httpx.Client(timeout=timeout) as client:
        while True:
            cycle_start = time.monotonic()
            try:
                attempts = 0
                while True:
                    try:
                        items = poll_once(client=client, config=config, auth_header=auth_header)
                        break
                    except httpx.HTTPError as exc:
                        attempts += 1
                        if attempts > config.fetch_retry_count or not _is_transient_fetch_error(exc):
                            raise
                        sleep_seconds = config.fetch_retry_backoff_seconds * attempts
                        logging.warning(
                            "cycle transient_fetch_error=%s retry=%d/%d sleep=%.1fs",
                            exc.__class__.__name__,
                            attempts,
                            config.fetch_retry_count,
                            sleep_seconds,
                        )
                        time.sleep(sleep_seconds)

                for item in items:
                    process_work_item(
                        item,
                        config=config,
                        store=store,
                        client=client,
                        auth_header=auth_header,
                    )
            except httpx.HTTPError as exc:
                logging.warning("cycle error=%s", exc.__class__.__name__)
            except Exception:  # pragma: no cover
                logging.exception("cycle error=unexpected")

            elapsed = time.monotonic() - cycle_start
            sleep_seconds = max(0.0, config.poll_interval_seconds - elapsed)
            time.sleep(sleep_seconds)


def run_worker_command(*, log_level: str = "INFO", reprocess: int | None = None, forget: int | None = None) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    config = WorkerConfig.load_from_env()
    store = ProcessedWorkItemStore(config.processed_store_path)

    if forget is not None:
        removed = store.forget(forget)
        logging.info("forget work_item=%s removed=%s", forget, removed)
        return

    if reprocess is not None:
        store.forget(reprocess)
        pat = _require_env(config.pat_env_var_name)
        auth_header = _build_auth_header(pat)
        timeout = httpx.Timeout(connect=5.0, write=10.0, read=20.0, pool=5.0)
        with httpx.Client(timeout=timeout) as client:
            item = fetch_work_item_by_id(
                client=client,
                config=config,
                auth_header=auth_header,
                work_item_id=reprocess,
            )
            if item is None:
                logging.warning("reprocess work_item=%s not_found_or_not_eligible", reprocess)
                return
            process_work_item(item, config=config, store=store, client=client, auth_header=auth_header)
        return

    run_worker(config)


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll Azure DevOps work items for truman-tagged candidates.")
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

    run_worker_command(log_level=args.log_level, reprocess=args.reprocess, forget=args.forget)


if __name__ == "__main__":
    main()
