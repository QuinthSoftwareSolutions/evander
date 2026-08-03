from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from evander.azure_devops import resolve_azure_devops_organization_url
from evander.models import PrFeedbackItem
from evander.events.models import TriggerEvent
from evander.events.provider_models import PullRequestNewFeedbackTriggerDefinition
from evander.pr_feedback.agent_directed import annotate_agent_directed_feedback
from evander.pr_feedback.providers import (
    AzureDevOpsFeedbackAdapter,
    GitHubFeedbackAdapter,
    pr_feedback_item_payload,
)
from evander.pr_feedback.store import PrFeedbackStore, _stream_key
from evander.triggers.providers._cursor_state import (
    TriggerCursorStore,
    latest_cursor_timestamp_text,
    merge_cursor_ids,
)
from evander.triggers.providers.base import TriggerProviderBase


class PullRequestNewFeedbackTriggerProvider(TriggerProviderBase):
    _RECENT_FEEDBACK_LIMIT = 512

    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        if trigger.provider == "ado":
            return []
        cursor_store = self._cursor_store()
        state = cursor_store.get(trigger.id)
        state_cursors = state.get("stream_cursors", {})
        if not isinstance(state_cursors, dict):
            state_cursors = {}
        recent_feedback_ids = state.get("recent_feedback_ids", {})
        if not isinstance(recent_feedback_ids, dict):
            recent_feedback_ids = {}
        previous_cursors = dict(state_cursors)
        pr_sessions = self._load_pr_sessions()

        streams = self._load_streams(trigger.provider)
        discovered: list[tuple[dict[str, str], PrFeedbackItem]] = []

        if trigger.provider == "local":
            feedback_store_path = self._runtime_dir() / "pr-feedback-store.json"
            feedback_store = PrFeedbackStore(feedback_store_path)
            for envelope in feedback_store.list_all_feedback(
                allowed_providers={"local", "github", "azure_devops"},
            ):
                item = self._annotate_agent_directed_feedback(
                    PrFeedbackItem.model_validate(pr_feedback_item_payload(envelope))
                )
                stream = {"provider": item.provider, "repo": item.repo, "pr_id": item.pr_id}
                discovered.append((stream, item))
        else:
            adapter = self._build_adapter(trigger.provider)
            with httpx.Client(timeout=30.0) as client:
                for stream in streams:
                    provider_name = self._provider_name_for_trigger(trigger.provider)
                    stream_key = _stream_key(provider_name, stream["repo"], stream["pr_id"])
                    cursor = state_cursors.get(stream_key)
                    result = adapter.poll(
                        client=client,
                        repo=stream["repo"],
                        pr_id=stream["pr_id"],
                        cursor=cursor,
                    )
                    for envelope in result.feedback:
                        feedback_item = self._annotate_agent_directed_feedback(
                            PrFeedbackItem.model_validate(pr_feedback_item_payload(envelope))
                        )
                        discovered.append(
                            (
                                {
                                    "provider": feedback_item.provider,
                                    "repo": feedback_item.repo,
                                    "pr_id": feedback_item.pr_id,
                                },
                                feedback_item,
                            )
                        )

        discovered.sort(key=lambda item: item[1].created_at)

        events: list[TriggerEvent] = []
        for stream, feedback in discovered:
            provider_name = stream["provider"]
            stream_key = _stream_key(provider_name, stream["repo"], stream["pr_id"])
            previous = _parse_dt(previous_cursors.get(stream_key))
            recent_ids = {
                value
                for value in recent_feedback_ids.get(stream_key, [])
                if isinstance(value, str) and value.strip()
            }
            if _feedback_is_behind_cursor(
                previous,
                created_at=feedback.created_at,
                feedback_id=feedback.feedback_id,
                recent_ids=recent_ids,
            ):
                continue
            if trigger.suppress_agent_authored_feedback and bool(
                (feedback.metadata or {}).get("agent_directed_author_self")
            ):
                cursor_store.update(
                    trigger.id,
                    lambda state, stream_key=stream_key, feedback=feedback: _acknowledge_feedback(
                        state,
                        stream_key,
                        feedback.feedback_id,
                        feedback.created_at,
                    ),
                )
                continue
            occurred_at = feedback.created_at.astimezone(UTC)
            events.append(
                TriggerEvent(
                    event_id=(
                        f"{trigger.id}:{provider_name}:{stream['repo']}:{stream['pr_id']}:{feedback.feedback_id}"
                    ),
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=occurred_at,
                    payload={
                        "pr": stream,
                        "feedback": feedback.model_dump(mode="json"),
                    }
                    | (
                        {
                            "session_id": pr_sessions.get(
                                (provider_name, stream["repo"], stream["pr_id"])
                            )
                        }
                        if pr_sessions.get((provider_name, stream["repo"], stream["pr_id"]))
                        else {}
                    ),
                )
            )
        return events

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        if event.trigger_id != trigger.id or event.trigger_type != trigger.type:
            return
        payload = event.payload.get("feedback")
        if not isinstance(payload, dict):
            return
        feedback_id = str(payload.get("feedback_id") or "").strip()
        provider = str(payload.get("provider") or "").strip()
        repo = str(payload.get("repo") or "").strip()
        pr_id = str(payload.get("pr_id") or "").strip()
        occurred_at = _parse_dt(payload.get("created_at")) or event.occurred_at
        if not feedback_id or not provider or not repo or not pr_id or occurred_at is None:
            return
        stream_key = _stream_key(provider, repo, pr_id)
        self._cursor_store().update(
            trigger.id,
            lambda state: _acknowledge_feedback(state, stream_key, feedback_id, occurred_at),
        )

    def _runtime_dir(self) -> Path:
        if self.runtime_context is None:
            raise RuntimeError("PullRequestNewFeedbackTriggerProvider has no runtime context")
        return self.runtime_context.resolved_config.runtime_dir

    def _cursor_store(self) -> TriggerCursorStore:
        return TriggerCursorStore(self._runtime_dir() / "trigger-cursors-pr-feedback.json")

    def _trigger(self) -> PullRequestNewFeedbackTriggerDefinition:
        if not isinstance(self.trigger_definition, PullRequestNewFeedbackTriggerDefinition):
            raise RuntimeError(
                "PullRequestNewFeedbackTriggerProvider initialized with wrong definition"
            )
        return self.trigger_definition

    def _build_adapter(self, provider: str):
        trigger = self._trigger()
        if provider == "github":
            token = self.runtime_context.env.get(
                self.runtime_context.resolved_config.github_token_env, ""
            ).strip()
            return GitHubFeedbackAdapter(token=token)
        if provider == "ado":
            pat_env_name = self.runtime_context.resolved_config.azure_devops_pr_pat_env
            pat = self.runtime_context.env.get(pat_env_name, "").strip()
            return AzureDevOpsFeedbackAdapter(
                organization_url=resolve_azure_devops_organization_url(
                    ado_organization=trigger.ado_organization,
                    legacy_organization_url=(
                        self.runtime_context.resolved_config.azure_devops_organization_url
                    ),
                    node_type=trigger.type,
                ),
                project=trigger.project
                or self.runtime_context.resolved_config.azure_devops_project,
                pat=pat,
                pat_env_name=pat_env_name,
            )
        raise RuntimeError(f"Unsupported PR feedback provider: {provider!r}")

    def _load_streams(self, provider: str) -> list[dict[str, str]]:
        path = self.runtime_context.resolved_config.pr_map_path
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            return []
        expected_provider = self._provider_name_for_trigger(provider)
        streams: dict[str, dict[str, str]] = {}
        directory_scope = str(self.runtime_context.metadata.get("directory_path") or "root")
        for raw in entries.values():
            if not isinstance(raw, dict):
                continue
            row_provider = str(raw.get("provider", "")).strip()
            repo = str(raw.get("repo", "")).strip()
            pr_id = str(raw.get("pr_id", "")).strip()
            directory_path = str(raw.get("directory_path", "root")).strip() or "root"
            if provider != "local" and row_provider != expected_provider:
                continue
            if not repo or not pr_id:
                continue
            if directory_scope and not (
                directory_path == directory_scope
                or directory_path.startswith(f"{directory_scope}/")
            ):
                continue
            key = f"{row_provider}:{repo}:{pr_id}"
            streams[key] = {"provider": row_provider, "repo": repo, "pr_id": pr_id}
        return list(streams.values())

    def _load_pr_sessions(self) -> dict[tuple[str, str, str], str]:
        path = self.runtime_context.resolved_config.pr_map_path
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            return {}

        sessions: dict[tuple[str, str, str], str] = {}
        directory_scope = str(self.runtime_context.metadata.get("directory_path") or "root")
        for raw in entries.values():
            if not isinstance(raw, dict):
                continue
            provider = str(raw.get("provider", "")).strip()
            repo = str(raw.get("repo", "")).strip()
            pr_id = str(raw.get("pr_id", "")).strip()
            session_id = str(raw.get("session_id", "")).strip()
            directory_path = str(raw.get("directory_path", "root")).strip() or "root"
            if not provider or not repo or not pr_id or not session_id:
                continue
            if directory_scope and not (
                directory_path == directory_scope
                or directory_path.startswith(f"{directory_scope}/")
            ):
                continue
            sessions[(provider, repo, pr_id)] = session_id
        return sessions

    def _annotate_agent_directed_feedback(self, item: PrFeedbackItem) -> PrFeedbackItem:
        trigger = self._trigger()
        return annotate_agent_directed_feedback(
            item,
            agent_author_names=trigger.agent_author_names,
            agent_mention_names=trigger.agent_mention_names,
            agent_author_ids=trigger.agent_author_ids,
            agent_mention_ids=trigger.agent_mention_ids,
        )

    @staticmethod
    def _provider_name_for_trigger(provider: str) -> str:
        if provider == "ado":
            return "azure_devops"
        return provider


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _feedback_is_behind_cursor(
    cursor: datetime | None,
    *,
    created_at: datetime,
    feedback_id: str,
    recent_ids: set[str],
) -> bool:
    if cursor is None:
        return False
    created_at = created_at.astimezone(UTC)
    if created_at < cursor:
        return True
    return created_at == cursor and feedback_id in recent_ids


def _acknowledge_feedback(
    state: dict[str, object],
    stream_key: str,
    feedback_id: str,
    occurred_at: datetime,
) -> dict[str, object]:
    stream_cursors = state.get("stream_cursors")
    if not isinstance(stream_cursors, dict):
        stream_cursors = {}
        state["stream_cursors"] = stream_cursors
    recent_feedback_ids = state.get("recent_feedback_ids")
    if not isinstance(recent_feedback_ids, dict):
        recent_feedback_ids = {}
        state["recent_feedback_ids"] = recent_feedback_ids

    stream_cursors[stream_key] = latest_cursor_timestamp_text(
        stream_cursors.get(stream_key),
        occurred_at,
    )
    recent_feedback_ids[stream_key] = merge_cursor_ids(
        recent_feedback_ids.get(stream_key),
        [feedback_id],
        limit=PullRequestNewFeedbackTriggerProvider._RECENT_FEEDBACK_LIMIT,
    )
    return state
