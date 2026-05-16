from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.events.provider_models import PullRequestCompletedTriggerDefinition
from agent_sandbox.pr_tracking import (
    PrMapEntry,
    PrMapStore,
    completion_event_id,
    completion_event_payload,
    completion_event_published,
    is_pr_completed,
    list_pr_map_entries,
    pr_map_provider_name,
)
from agent_sandbox.triggers.providers.base import TriggerProviderBase

logger = logging.getLogger(__name__)


class PullRequestCompletedTriggerProvider(TriggerProviderBase):
    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        if trigger.provider == "ado":
            return []
        context = self._context()
        provider = pr_map_provider_name(trigger.provider)
        directory_path = str(context.metadata.get("directory_path") or "root")
        entries = list_pr_map_entries(
            context.resolved_config.pr_map_path,
            provider=provider,
            directory_path=directory_path,
            runtime_dir=context.resolved_config.runtime_dir,
        )
        if provider == "github":
            self._refresh_github_pr_statuses(trigger=trigger, entries=entries)
            entries = list_pr_map_entries(
                context.resolved_config.pr_map_path,
                provider=provider,
                directory_path=directory_path,
                runtime_dir=context.resolved_config.runtime_dir,
            )

        events: list[TriggerEvent] = []
        for entry in entries:
            raw = dict(entry.raw)
            if not is_pr_completed(raw) or completion_event_published(raw, trigger.id):
                continue
            event_id = completion_event_id(trigger.id, raw)
            occurred_at = _occurred_at(raw)
            events.append(
                TriggerEvent(
                    event_id=event_id,
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=occurred_at,
                    payload=completion_event_payload(raw),
                )
            )
        return events

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        pr = event.payload.get("pr")
        if not isinstance(pr, dict):
            return
        provider = pr_map_provider_name(pr.get("provider"))
        repo = str(pr.get("repo", "")).strip()
        pr_id = str(pr.get("pr_id", "")).strip()
        if not provider or not repo or not pr_id:
            return
        PrMapStore(self._context().resolved_config.pr_map_path).mark_completion_event_published(
            provider=provider,
            repo=repo,
            pr_id=pr_id,
            trigger_id=event.trigger_id,
            event_id=event.event_id,
        )

    def _trigger(self) -> PullRequestCompletedTriggerDefinition:
        if not isinstance(self.trigger_definition, PullRequestCompletedTriggerDefinition):
            raise RuntimeError(
                "PullRequestCompletedTriggerProvider initialized with wrong definition"
            )
        return self.trigger_definition

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("PullRequestCompletedTriggerProvider has no runtime context")
        return self.runtime_context

    def _refresh_github_pr_statuses(
        self,
        *,
        trigger: PullRequestCompletedTriggerDefinition,
        entries: list[PrMapEntry],
    ) -> None:
        if not entries:
            return
        context = self._context()
        token_env = str(getattr(context.resolved_config, "github_token_env", "GITHUB_TOKEN") or "")
        token = context.resolve_secret(token_env) if token_env else None
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        pr_map_store = PrMapStore(context.resolved_config.pr_map_path)
        with httpx.Client(timeout=30.0) as client:
            for entry in entries:
                raw = dict(entry.raw)
                if completion_event_published(raw, trigger.id):
                    continue
                try:
                    payload = self._fetch_github_pull_request(
                        client=client,
                        repo=entry.repo,
                        pr_id=entry.pr_id,
                        headers=headers,
                    )
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code if exc.response is not None else None
                    if status_code == 404:
                        logger.warning(
                            "Skipping GitHub PR completion status after 404 "
                            "trigger_id=%s repo=%s pr_id=%s",
                            trigger.id,
                            entry.repo,
                            entry.pr_id,
                        )
                        continue
                    raise
                pr_map_store.update_status(
                    _github_status_snapshot(repo=entry.repo, pr_id=entry.pr_id, payload=payload),
                    trigger_id=trigger.id,
                )

    def _fetch_github_pull_request(
        self,
        *,
        client: httpx.Client,
        repo: str,
        pr_id: str,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        response = client.get(
            f"https://api.github.com/repos/{repo}/pulls/{pr_id}",
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}


def _occurred_at(entry: dict[str, object]) -> datetime:
    for key in ("completed_at", "pr_status_updated_at", "status_checked_at"):
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return datetime.now(UTC)


def _github_status_snapshot(*, repo: str, pr_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    head = payload.get("head") if isinstance(payload.get("head"), dict) else {}
    base = payload.get("base") if isinstance(payload.get("base"), dict) else {}
    merged = bool(payload.get("merged")) or bool(str(payload.get("merged_at") or "").strip())
    state = str(payload.get("state") or "").strip().lower() or None
    status = "merged" if merged else state
    completed_at = (
        str(payload.get("merged_at") or payload.get("closed_at") or "").strip() or None
        if merged or state == "closed"
        else None
    )
    return {
        "provider": "github",
        "repo": repo,
        "pr_id": pr_id,
        "status": status,
        "merged": merged,
        "completed_at": completed_at,
        "pr_url": str(payload.get("html_url") or payload.get("url") or "").strip() or None,
        "title": str(payload.get("title") or "").strip() or None,
        "source_branch": str(head.get("ref") or "").strip() or None,
        "target_branch": str(base.get("ref") or "").strip() or None,
        "source_commit": str(head.get("sha") or "").strip() or None,
        "target_commit": str(base.get("sha") or "").strip() or None,
    }
