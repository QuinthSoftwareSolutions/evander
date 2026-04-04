from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from agent_sandbox.models import PrFeedbackItem
from agent_sandbox.pr_feedback_worker import AzureDevOpsFeedbackAdapter, GitHubFeedbackAdapter, _stream_key
from agent_sandbox.events.models import PullRequestNewFeedbackTriggerDefinition, TriggerEvent
from agent_sandbox.triggers.providers._cursor_state import TriggerCursorStore
from agent_sandbox.triggers.providers.base import TriggerProviderBase


class PullRequestNewFeedbackTriggerProvider(TriggerProviderBase):
    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        cursor_store = self._cursor_store()
        state = cursor_store.get(trigger.id)
        state_cursors = state.get("stream_cursors", {})
        if not isinstance(state_cursors, dict):
            state_cursors = {}

        streams = self._load_streams(trigger.provider)
        discovered: list[tuple[dict[str, str], PrFeedbackItem]] = []

        if trigger.provider == "local":
            feedback_store_path = self._runtime_dir() / "pr-feedback-store.json"
            if feedback_store_path.exists():
                payload = json.loads(feedback_store_path.read_text(encoding="utf-8"))
                for raw in payload.get("feedback", {}).values():
                    if not isinstance(raw, dict):
                        continue
                    item = PrFeedbackItem.model_validate(raw)
                    if item.provider not in {"local", "github", "azure_devops"}:
                        continue
                    stream = {"provider": item.provider, "repo": item.repo, "pr_id": item.pr_id}
                    key = _stream_key(item.provider, item.repo, item.pr_id)
                    cursor = _parse_dt(state_cursors.get(key))
                    if cursor is not None and item.created_at <= cursor:
                        continue
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
                        discovered.append(
                            (
                                {
                                    "provider": envelope.provider,
                                    "repo": envelope.repo,
                                    "pr_id": envelope.pr_id,
                                },
                                PrFeedbackItem.model_validate(envelope.model_dump(mode="json")),
                            )
                        )
                    if result.next_cursor:
                        state_cursors[stream_key] = result.next_cursor

        discovered.sort(key=lambda item: item[1].created_at)

        events: list[TriggerEvent] = []
        for stream, feedback in discovered:
            provider_name = stream["provider"]
            stream_key = _stream_key(provider_name, stream["repo"], stream["pr_id"])
            previous = _parse_dt(state_cursors.get(stream_key))
            if previous is not None and feedback.created_at <= previous:
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
                    },
                )
            )
            state_cursors[stream_key] = occurred_at.isoformat().replace("+00:00", "Z")

        cursor_store.put(trigger.id, {"stream_cursors": state_cursors})
        return events

    def _runtime_dir(self) -> Path:
        if self.runtime_context is None:
            raise RuntimeError("PullRequestNewFeedbackTriggerProvider has no runtime context")
        return self.runtime_context.resolved_config.runtime_dir

    def _cursor_store(self) -> TriggerCursorStore:
        return TriggerCursorStore(self._runtime_dir() / "trigger-cursors-pr-feedback.json")

    def _trigger(self) -> PullRequestNewFeedbackTriggerDefinition:
        if not isinstance(self.trigger_definition, PullRequestNewFeedbackTriggerDefinition):
            raise RuntimeError("PullRequestNewFeedbackTriggerProvider initialized with wrong definition")
        return self.trigger_definition

    def _build_adapter(self, provider: str):
        if provider == "github":
            token = self.runtime_context.env.get(self.runtime_context.resolved_config.github_token_env, "").strip()
            return GitHubFeedbackAdapter(token=token)
        if provider == "ado":
            pat_env_name = self.runtime_context.resolved_config.azure_devops_pr_pat_env
            pat = self.runtime_context.env.get(pat_env_name, "").strip()
            return AzureDevOpsFeedbackAdapter(
                organization_url=self.runtime_context.resolved_config.azure_devops_organization_url,
                project=self.runtime_context.resolved_config.azure_devops_project,
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
            if directory_scope and not (directory_path == directory_scope or directory_path.startswith(f"{directory_scope}/")):
                continue
            key = f"{row_provider}:{repo}:{pr_id}"
            streams[key] = {"provider": row_provider, "repo": repo, "pr_id": pr_id}
        return list(streams.values())

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
