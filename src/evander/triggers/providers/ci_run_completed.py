from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from evander.azure_devops import resolve_azure_devops_organization_url
from evander.events.models import TriggerEvent
from evander.events.provider_models import CiRunCompletedTriggerDefinition
from evander.triggers.providers._cursor_state import (
    TriggerCursorStore,
    latest_cursor_timestamp_text,
    merge_cursor_ids,
)
from evander.triggers.providers.base import TriggerProviderBase

_GITHUB_API_BASE_URL = "https://api.github.com"
_ADO_API_VERSION = "7.1"
_RECENT_RUN_LIMIT = 512

_GITHUB_CONCLUSION_ALIASES = {
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "failed": "failure",
    "failure": "failure",
    "skipped": "skipped",
    "success": "success",
    "succeeded": "success",
    "timed_out": "timed_out",
    "timedout": "timed_out",
    "timeout": "timed_out",
}

_ADO_CONCLUSION_ALIASES = {
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "failed": "failure",
    "failure": "failure",
    "partiallysucceeded": "failure",
    "skipped": "skipped",
    "succeeded": "success",
    "success": "success",
    "timed_out": "timed_out",
    "timedout": "timed_out",
    "timeout": "timed_out",
}


@dataclass(frozen=True)
class _NormalizedCiRun:
    provider: str
    repo: str
    run_id: str
    run_number: str | None
    attempt: int
    workflow_id: str | None
    workflow_name: str | None
    pipeline_id: int | None
    pipeline_name: str | None
    branch: str | None
    commit_sha: str | None
    status: str
    conclusion: str | None
    run_url: str | None
    started_at: datetime | None
    completed_at: datetime
    duration_seconds: float | None
    actor: str | None
    pull_requests: list[dict[str, Any]]
    metadata: dict[str, Any]

    @property
    def run_key(self) -> str:
        return f"{self.provider}:{self.repo}:{self.run_id}:{self.attempt}"

    @property
    def sort_key(self) -> tuple[datetime, str, str, str, int]:
        return (self.completed_at, self.provider, self.repo, self.run_id, self.attempt)

    def payload(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "repo": self.repo,
            "run_id": self.run_id,
            "run_number": self.run_number,
            "attempt": self.attempt,
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "pipeline_id": self.pipeline_id,
            "pipeline_name": self.pipeline_name,
            "branch": self.branch,
            "commit_sha": self.commit_sha,
            "status": self.status,
            "conclusion": self.conclusion,
            "run_url": self.run_url,
            "started_at": _format_dt(self.started_at),
            "completed_at": _format_dt(self.completed_at),
            "duration_seconds": self.duration_seconds,
            "actor": self.actor,
            "pull_requests": [dict(item) for item in self.pull_requests],
            "metadata": dict(self.metadata),
        }


class CiRunCompletedTriggerProvider(TriggerProviderBase):
    _CURSOR_FILENAME = "trigger-cursors-ci-runs.json"

    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        stream_key = self._stream_key(trigger)
        cursor_state = self._cursor_store().get(trigger.id)
        streams_state = _normalize_streams_state(cursor_state.get("streams"))
        stream_state = streams_state.get(stream_key)

        runs = self._discover_runs(trigger)
        if stream_state is None and not trigger.include_existing:
            baseline_state = _baseline_stream_state(runs)
            self._cursor_store().update(
                trigger.id,
                lambda state: _seed_stream_state(state, stream_key, baseline_state),
            )
            return []

        recent_run_keys = {
            value
            for value in _normalize_text_list(
                stream_state.get("recent_run_keys") if stream_state else []
            )
        }
        last_completed_at = _parse_dt(
            stream_state.get("last_completed_at") if stream_state else None
        )

        events: list[TriggerEvent] = []
        for run in runs:
            if _run_is_behind_cursor(
                last_completed_at, run.completed_at, run.run_key, recent_run_keys
            ):
                continue
            events.append(
                TriggerEvent(
                    event_id=f"{trigger.id}:{run.run_key}",
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=run.completed_at.astimezone(UTC),
                    payload=run.payload(),
                )
            )
        return events

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        if event.trigger_id != trigger.id or event.trigger_type != trigger.type:
            return
        payload = event.payload
        run_id = _normalize_text(payload.get("run_id"))
        repo = _normalize_text(payload.get("repo"))
        provider = _normalize_text(payload.get("provider"))
        attempt = _coerce_int(payload.get("attempt"), default=1)
        completed_at = _parse_dt(payload.get("completed_at")) or event.occurred_at
        if not run_id or not repo or not provider or attempt is None or completed_at is None:
            return
        run_key = f"{provider}:{repo}:{run_id}:{attempt}"
        stream_key = self._stream_key(trigger)
        self._cursor_store().update(
            trigger.id,
            lambda state: _acknowledge_run(state, stream_key, run_key, completed_at),
        )

    def _discover_runs(self, trigger: CiRunCompletedTriggerDefinition) -> list[_NormalizedCiRun]:
        if trigger.provider == "github_actions":
            return self._discover_github_runs(trigger)
        if trigger.provider == "ado_pipelines":
            return self._discover_ado_runs(trigger)
        raise RuntimeError(f"Unsupported CI provider: {trigger.provider!r}")

    def _discover_github_runs(
        self, trigger: CiRunCompletedTriggerDefinition
    ) -> list[_NormalizedCiRun]:
        context = self._context()
        token_env = _normalize_text(getattr(context.resolved_config, "github_token_env", None))
        token = context.resolve_secret(token_env) if token_env else None
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        repo = _normalize_text(trigger.repo)
        if not repo:
            return []
        workflow = _normalize_text(trigger.workflow)
        branches = _github_branch_queries(trigger.branches)
        include_direct = self._github_workflow_selector_is_direct(workflow)
        selected_runs: dict[str, _NormalizedCiRun] = {}

        with httpx.Client(timeout=30.0) as client:
            for branch in branches:
                raw_runs = self._github_fetch_runs(
                    client=client,
                    repo=repo,
                    headers=headers,
                    branch=branch,
                    top=trigger.top,
                    workflow=workflow,
                    use_workflow_endpoint=include_direct,
                )
                for raw in raw_runs:
                    run = self._normalize_github_run(trigger=trigger, raw=raw, branch=branch)
                    if run is None:
                        continue
                    if (
                        workflow
                        and not include_direct
                        and not _github_run_matches_workflow(raw, workflow)
                    ):
                        continue
                    if run.conclusion not in trigger.conclusions:
                        continue
                    selected_runs.setdefault(run.run_key, run)

        return sorted(selected_runs.values(), key=lambda run: run.sort_key)

    def _discover_ado_runs(
        self, trigger: CiRunCompletedTriggerDefinition
    ) -> list[_NormalizedCiRun]:
        context = self._context()
        config = self._resolve_directory_config()
        organization_url = resolve_azure_devops_organization_url(
            ado_organization=trigger.ado_organization,
            legacy_organization_url=getattr(config, "azure_devops_organization_url", None),
            node_type=trigger.type,
        )
        project = _normalize_text(trigger.project) or _normalize_text(
            getattr(config, "azure_devops_project", None)
        )
        pat_env = _normalize_text(getattr(config, "azure_devops_pr_pat_env", None))
        if not project:
            raise ValueError("trigger:ci:run:completed requires project for Azure DevOps")
        if not pat_env:
            raise ValueError("trigger:ci:run:completed requires azure_devops_pr_pat_env")
        pat = context.resolve_secret(pat_env)
        if not pat:
            raise RuntimeError(
                f"trigger:ci:run:completed Azure DevOps PAT is not configured in {pat_env}"
            )

        headers = {"Accept": "application/json"}
        auth = ("", pat)
        repo_filter = _normalize_text(trigger.repo)
        branches = _ado_branch_queries(trigger.branches)
        selected_runs: dict[str, _NormalizedCiRun] = {}

        with httpx.Client(timeout=30.0) as client:
            for branch in branches:
                raw_builds = self._ado_fetch_builds(
                    client=client,
                    organization_url=organization_url,
                    project=project,
                    headers=headers,
                    auth=auth,
                    branch=branch,
                    top=trigger.top,
                    pipeline_id=trigger.pipeline_id,
                )
                for raw in raw_builds:
                    run = self._normalize_ado_build(
                        trigger=trigger,
                        raw=raw,
                        organization_url=organization_url,
                        project=project,
                        branch_query=branch,
                    )
                    if run is None:
                        continue
                    if repo_filter and not _repo_matches_filter(repo_filter, run.repo, raw):
                        continue
                    if trigger.pipeline_name and run.pipeline_name != trigger.pipeline_name:
                        continue
                    if run.conclusion not in trigger.conclusions:
                        continue
                    selected_runs.setdefault(run.run_key, run)

        return sorted(selected_runs.values(), key=lambda run: run.sort_key)

    def _github_fetch_runs(
        self,
        *,
        client: httpx.Client,
        repo: str,
        headers: dict[str, str],
        branch: str | None,
        top: int,
        workflow: str | None,
        use_workflow_endpoint: bool,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {
            "status": "completed",
            "per_page": str(top),
        }
        if branch:
            params["branch"] = branch

        if workflow and use_workflow_endpoint:
            workflow_segment = quote(workflow, safe="")
            url = f"{_GITHUB_API_BASE_URL}/repos/{repo}/actions/workflows/{workflow_segment}/runs"
        else:
            url = f"{_GITHUB_API_BASE_URL}/repos/{repo}/actions/runs"

        response = client.get(url, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return []
        runs = payload.get("workflow_runs")
        if not isinstance(runs, list):
            return []
        return [item for item in runs if isinstance(item, dict)]

    def _ado_fetch_builds(
        self,
        *,
        client: httpx.Client,
        organization_url: str,
        project: str,
        headers: dict[str, str],
        auth: tuple[str, str],
        branch: str | None,
        top: int,
        pipeline_id: int | None,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {
            "statusFilter": "completed",
            "$top": str(top),
            "queryOrder": "finishTimeDescending",
            "api-version": _ADO_API_VERSION,
        }
        if branch:
            params["branchName"] = f"refs/heads/{branch}"
        if pipeline_id is not None:
            params["definitions"] = str(pipeline_id)

        url = f"{organization_url.rstrip('/')}/{project.strip()}/_apis/build/builds"
        response = client.get(url, params=params, headers=headers, auth=auth)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return []
        builds = payload.get("value")
        if not isinstance(builds, list):
            return []
        return [item for item in builds if isinstance(item, dict)]

    def _normalize_github_run(
        self,
        *,
        trigger: CiRunCompletedTriggerDefinition,
        raw: dict[str, Any],
        branch: str | None,
    ) -> _NormalizedCiRun | None:
        repo = _normalize_text(trigger.repo)
        run_id = _normalize_text(raw.get("id"))
        if not repo or not run_id:
            return None
        run_number = _normalize_text(raw.get("run_number"))
        attempt = _coerce_int(raw.get("run_attempt"), default=1)
        if attempt is None:
            attempt = 1
        workflow_id = _normalize_text(raw.get("workflow_id"))
        workflow_name = _normalize_text(raw.get("name"))
        branch_name = _normalize_branch_name(raw.get("head_branch")) or branch
        commit_sha = _normalize_text(raw.get("head_sha"))
        status = _normalize_text(raw.get("status")) or "completed"
        conclusion = _normalize_conclusion(raw.get("conclusion"), provider="github_actions")
        run_url = _normalize_text(raw.get("html_url") or raw.get("url"))
        started_at = _parse_dt(raw.get("run_started_at") or raw.get("created_at"))
        completed_at = _parse_dt(raw.get("updated_at") or raw.get("run_started_at"))
        if completed_at is None:
            completed_at = started_at or datetime.now(UTC)
        duration_seconds = _duration_seconds(started_at, completed_at)
        actor = _normalize_actor(raw.get("triggering_actor") or raw.get("actor"))
        pull_requests = _normalize_pull_requests(raw.get("pull_requests"))
        metadata = {
            "raw": raw,
            "branch_query": branch,
            "workflow_selector": trigger.workflow,
            "query_provider": trigger.provider,
        }
        return _NormalizedCiRun(
            provider="github_actions",
            repo=repo,
            run_id=run_id,
            run_number=run_number,
            attempt=attempt,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            pipeline_id=None,
            pipeline_name=None,
            branch=branch_name,
            commit_sha=commit_sha,
            status=status,
            conclusion=conclusion,
            run_url=run_url,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration_seconds,
            actor=actor,
            pull_requests=pull_requests,
            metadata=metadata,
        )

    def _normalize_ado_build(
        self,
        *,
        trigger: CiRunCompletedTriggerDefinition,
        raw: dict[str, Any],
        organization_url: str,
        project: str,
        branch_query: str | None,
    ) -> _NormalizedCiRun | None:
        run_id = _normalize_text(raw.get("id"))
        if not run_id:
            return None
        repository = raw.get("repository") if isinstance(raw.get("repository"), dict) else {}
        raw_repo = repository if isinstance(repository, dict) else {}
        repo = _normalize_text(
            raw_repo.get("name") or raw_repo.get("id") or raw_repo.get("url") or trigger.repo or ""
        )
        if not repo:
            return None
        definition = raw.get("definition") if isinstance(raw.get("definition"), dict) else {}
        raw_definition = definition if isinstance(definition, dict) else {}
        pipeline_id = _coerce_int(raw_definition.get("id"), default=None)
        pipeline_name = _normalize_text(raw_definition.get("name"))
        run_number = _normalize_text(raw.get("buildNumber"))
        attempt = 1
        branch = _normalize_branch_name(raw.get("sourceBranch")) or branch_query
        commit_sha = _normalize_text(raw.get("sourceVersion"))
        status = _normalize_text(raw.get("status")) or "completed"
        conclusion = _normalize_conclusion(raw.get("result"), provider="ado_pipelines")
        run_url = _normalize_text(raw.get("webUrl") or raw.get("url"))
        started_at = _parse_dt(raw.get("startTime") or raw.get("queueTime"))
        completed_at = _parse_dt(raw.get("finishTime") or raw.get("completedTime"))
        if completed_at is None:
            completed_at = started_at or datetime.now(UTC)
        duration_seconds = _duration_seconds(started_at, completed_at)
        actor = _normalize_actor(raw.get("requestedFor") or raw.get("requestedBy"))
        pull_requests = _normalize_ado_pull_requests(raw)
        metadata = {
            "raw": raw,
            "organization_url": organization_url,
            "project": project,
            "branch_query": branch_query,
            "query_provider": trigger.provider,
        }
        return _NormalizedCiRun(
            provider="ado_pipelines",
            repo=repo,
            run_id=run_id,
            run_number=run_number,
            attempt=attempt,
            workflow_id=None,
            workflow_name=None,
            pipeline_id=pipeline_id,
            pipeline_name=pipeline_name,
            branch=branch,
            commit_sha=commit_sha,
            status=status,
            conclusion=conclusion,
            run_url=run_url,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration_seconds,
            actor=actor,
            pull_requests=pull_requests,
            metadata=metadata,
        )

    def _cursor_store(self) -> TriggerCursorStore:
        return TriggerCursorStore(self._runtime_dir() / self._CURSOR_FILENAME)

    def _runtime_dir(self) -> Path:
        return self._context().resolved_config.runtime_dir

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("CiRunCompletedTriggerProvider has no runtime context")
        return self.runtime_context

    def _trigger(self) -> CiRunCompletedTriggerDefinition:
        if not isinstance(self.trigger_definition, CiRunCompletedTriggerDefinition):
            raise RuntimeError("CiRunCompletedTriggerProvider initialized with wrong definition")
        return self.trigger_definition

    def _resolve_directory_config(self):
        context = self._context()
        directory_path = _normalize_text(context.metadata.get("directory_path")) or "root"
        resolver = getattr(context, "provider_config_resolver", None)
        resolve_for_directory = getattr(resolver, "resolve_for_directory", None)
        if callable(resolve_for_directory):
            return resolve_for_directory(directory_path)
        return context.resolved_config

    def _stream_key(self, trigger: CiRunCompletedTriggerDefinition) -> str:
        config = self._resolve_directory_config()
        repo = _normalize_text(trigger.repo) or "*"
        branches = ",".join(sorted(_normalize_text_list(trigger.branches))) or "*"
        conclusions = ",".join(sorted(_normalize_text_list(trigger.conclusions))) or "*"
        if trigger.provider == "github_actions":
            selector = f"workflow={_normalize_text(trigger.workflow) or '*'}"
        else:
            organization_url = resolve_azure_devops_organization_url(
                ado_organization=trigger.ado_organization,
                legacy_organization_url=getattr(config, "azure_devops_organization_url", None),
                node_type=trigger.type,
            )
            project = _normalize_text(trigger.project) or _normalize_text(
                getattr(config, "azure_devops_project", None)
            )
            selector = "|".join(
                [
                    f"organization_url={organization_url or '*'}",
                    f"project={project or '*'}",
                    f"pipeline_id={trigger.pipeline_id if trigger.pipeline_id is not None else '*'}",
                    f"pipeline_name={_normalize_text(trigger.pipeline_name) or '*'}",
                ]
            )
        return "|".join(
            [
                f"provider={trigger.provider}",
                f"repo={repo}",
                selector,
                f"branches={branches}",
                f"conclusions={conclusions}",
            ]
        )

    @staticmethod
    def _github_workflow_selector_is_direct(workflow: str | None) -> bool:
        if not workflow:
            return False
        normalized = workflow.strip()
        return (
            normalized.isdigit()
            or "/" in normalized
            or normalized.lower().endswith(".yml")
            or normalized.lower().endswith(".yaml")
        )


def _seed_stream_state(
    state: dict[str, Any],
    stream_key: str,
    baseline_state: dict[str, Any],
) -> dict[str, Any]:
    streams = _normalize_streams_state(state.get("streams"))
    streams[stream_key] = baseline_state
    state["streams"] = streams
    return state


def _acknowledge_run(
    state: dict[str, Any],
    stream_key: str,
    run_key: str,
    completed_at: datetime,
) -> dict[str, Any]:
    streams = _normalize_streams_state(state.get("streams"))
    stream_state = streams.get(stream_key, {})
    if not isinstance(stream_state, dict):
        stream_state = {}
    stream_state["initialized"] = True
    stream_state["last_completed_at"] = latest_cursor_timestamp_text(
        stream_state.get("last_completed_at"),
        completed_at,
    )
    stream_state["recent_run_keys"] = merge_cursor_ids(
        stream_state.get("recent_run_keys"),
        [run_key],
        limit=_RECENT_RUN_LIMIT,
    )
    streams[stream_key] = stream_state
    state["streams"] = streams
    return state


def _baseline_stream_state(runs: list[_NormalizedCiRun]) -> dict[str, Any]:
    return {
        "initialized": True,
        "last_completed_at": _format_dt(max((run.completed_at for run in runs), default=None)),
        "recent_run_keys": merge_cursor_ids(
            [],
            [run.run_key for run in runs],
            limit=_RECENT_RUN_LIMIT,
        ),
    }


def _normalize_streams_state(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    streams: dict[str, dict[str, Any]] = {}
    for key, item in value.items():
        if isinstance(item, dict):
            streams[str(key)] = dict(item)
    return streams


def _run_is_behind_cursor(
    last_completed_at: datetime | None,
    completed_at: datetime,
    run_key: str,
    recent_run_keys: set[str],
) -> bool:
    if last_completed_at is None:
        return False
    completed_at = completed_at.astimezone(UTC)
    if completed_at < last_completed_at:
        return True
    return completed_at == last_completed_at and run_key in recent_run_keys


def _github_run_matches_workflow(raw: dict[str, Any], workflow: str) -> bool:
    candidate = _normalize_text(workflow)
    if not candidate:
        return True
    workflow_id = _normalize_text(raw.get("workflow_id"))
    workflow_name = _normalize_text(raw.get("name"))
    workflow_path = _normalize_text(raw.get("path"))
    return candidate in {workflow_id, workflow_name, workflow_path}


def _github_branch_queries(branches: list[str]) -> list[str | None]:
    if not branches:
        return [None]
    normalized = [_normalize_branch_name(branch) for branch in branches]
    return [value for value in normalized if value]


def _ado_branch_queries(branches: list[str]) -> list[str | None]:
    if not branches:
        return [None]
    normalized = [_normalize_branch_name(branch) for branch in branches]
    return [value for value in normalized if value]


def _normalize_conclusion(value: object, *, provider: str) -> str | None:
    token = _normalize_conclusion_token(value)
    if token is None:
        return None
    alias_map = (
        _GITHUB_CONCLUSION_ALIASES if provider == "github_actions" else _ADO_CONCLUSION_ALIASES
    )
    return alias_map.get(token)


def _normalize_conclusion_token(value: object) -> str | None:
    text = _normalize_text(value)
    if text is None:
        return None
    return text.casefold().replace("-", "").replace("_", "").replace(" ", "")


def _normalize_optional_text(value: object) -> str | None:
    return _normalize_text(value)


def _normalize_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_text_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _normalize_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _normalize_branch_name(value: object) -> str | None:
    text = _normalize_text(value)
    if text is None:
        return None
    prefix = "refs/heads/"
    if text.startswith(prefix):
        text = text.removeprefix(prefix)
    return text or None


def _coerce_int(value: object, *, default: int | None) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _parse_dt(value: object) -> datetime | None:
    text = _normalize_text(value)
    if text is None:
        return None
    normalized = text
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _format_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _duration_seconds(started_at: datetime | None, completed_at: datetime) -> float | None:
    if started_at is None:
        return None
    return max(0.0, (completed_at.astimezone(UTC) - started_at.astimezone(UTC)).total_seconds())


def _normalize_actor(value: object) -> str | None:
    if not isinstance(value, dict):
        return _normalize_text(value)
    for key in ("login", "displayName", "display_name", "uniqueName", "email", "name", "id"):
        actor = _normalize_text(value.get(key))
        if actor:
            return actor
    return None


def _normalize_pull_requests(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    pull_requests: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            pull_requests.append(dict(item))
    return pull_requests


def _normalize_ado_pull_requests(raw: dict[str, Any]) -> list[dict[str, Any]]:
    value = raw.get("pull_requests")
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    trigger_info = raw.get("triggerInfo")
    if not isinstance(trigger_info, dict):
        return []
    pr_number = (
        _normalize_text(trigger_info.get("pr.number"))
        or _normalize_text(trigger_info.get("pullRequestId"))
        or _normalize_text(raw.get("pullRequestId"))
    )
    if not pr_number:
        return []
    pr_url = _normalize_text(trigger_info.get("pr.url") or trigger_info.get("pullRequestUrl"))
    payload: dict[str, Any] = {"number": pr_number}
    if pr_url:
        payload["url"] = pr_url
    return [payload]


def _repo_matches_filter(repo_filter: str, normalized_repo: str, raw: dict[str, Any]) -> bool:
    raw_repository = raw.get("repository") if isinstance(raw.get("repository"), dict) else {}
    candidates = [
        _normalize_text(normalized_repo),
        _normalize_text(raw_repository.get("name") if isinstance(raw_repository, dict) else None),
        _normalize_text(raw_repository.get("id") if isinstance(raw_repository, dict) else None),
        _normalize_text(raw_repository.get("url") if isinstance(raw_repository, dict) else None),
    ]
    normalized_filter = _normalize_text(repo_filter)
    if not normalized_filter:
        return True
    for candidate in candidates:
        if not candidate:
            continue
        if candidate == normalized_filter:
            return True
        if candidate.endswith(f"/{normalized_filter}"):
            return True
        if normalized_filter.endswith(f"/{candidate}"):
            return True
    return False


def _run_behind_cursor(
    last_completed_at: datetime | None,
    completed_at: datetime,
    run_key: str,
    recent_run_keys: set[str],
) -> bool:
    return _run_is_behind_cursor(last_completed_at, completed_at, run_key, recent_run_keys)
