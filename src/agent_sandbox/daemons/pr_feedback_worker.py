from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from agent_sandbox.audit import AuditEventType, AuditLogger
from agent_sandbox.config import GatewayConfig
from agent_sandbox.pr_feedback import (
    AzureDevOpsFeedbackAdapter,
    FeedbackEnvelope,
    FeedbackProviderAdapter,
    FeedbackProviderAuthError,
    FeedbackProviderConfigurationError,
    GitHubFeedbackAdapter,
    PrFeedbackRepository,
    PrFeedbackStore,
    ProviderPollResult,
    _latest_cursor,
    _parse_dt,
    _stream_key,
    pr_feedback_item_payload,
)
from agent_sandbox.worker_directory_context import (
    WorkerDirectoryContextResolver,
    log_shadow_context,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AzureDevOpsFeedbackAdapter",
    "FeedbackEnvelope",
    "FeedbackProviderAdapter",
    "FeedbackProviderAuthError",
    "FeedbackProviderConfigurationError",
    "GitHubFeedbackAdapter",
    "PrFeedbackRepository",
    "PrFeedbackStore",
    "ProviderPollResult",
    "PullRequestFeedbackWorker",
    "_latest_cursor",
    "_parse_dt",
    "_stream_key",
    "build_pr_feedback_worker",
    "pr_feedback_item_payload",
]


class PullRequestFeedbackWorker:
    def __init__(
        self,
        *,
        store: PrFeedbackRepository,
        adapters: dict[str, FeedbackProviderAdapter],
        audit_logger: AuditLogger,
        directory_context_resolver: WorkerDirectoryContextResolver | None = None,
    ) -> None:
        self.store = store
        self.adapters = adapters
        self.audit_logger = audit_logger
        self.directory_context_resolver = directory_context_resolver

    def poll_once(
        self,
        *,
        client: httpx.Client,
        provider: str,
        repo: str,
        pr_id: str,
        stream_bindings: list[dict[str, str | None]] | None = None,
        adapter: FeedbackProviderAdapter | None = None,
    ) -> list[FeedbackEnvelope]:
        adapter = adapter or self.adapters[provider]
        stream_key = _stream_key(provider, repo, pr_id)
        cursor = self.store.get_cursor(stream_key)
        result = adapter.poll(client=client, repo=repo, pr_id=pr_id, cursor=cursor)
        resolved_bindings = self._resolve_stream_bindings(
            provider=provider,
            repo=repo,
            pr_id=pr_id,
            stream_bindings=stream_bindings,
        )

        discovered: list[FeedbackEnvelope] = []
        for envelope in result.feedback:
            if not self.store.add_feedback_if_new(envelope):
                continue
            discovered.append(envelope)
            self._log_discovery(envelope, stream_bindings=resolved_bindings)

        self.store.set_cursor(stream_key, result.next_cursor)
        return discovered

    def _resolve_stream_bindings(
        self,
        *,
        provider: str,
        repo: str,
        pr_id: str,
        stream_bindings: list[dict[str, str | None]] | None,
    ) -> list[dict[str, Any]]:
        bindings = [dict(binding) for binding in (stream_bindings or [])]
        if self.directory_context_resolver is None:
            return bindings

        resolved: list[dict[str, Any]] = []
        for binding in bindings:
            session_id = str(binding.get("session_id") or "").strip() or None
            fallback_directory_path = str(binding.get("directory_path") or "").strip() or None
            source = (
                str(binding.get("source") or "pr_feedback_worker").strip() or "pr_feedback_worker"
            )
            context = self.directory_context_resolver.resolve_for_session(
                session_id,
                fallback_directory_path=fallback_directory_path,
                source=source,
            )
            binding["directory_path"] = context.directory_path
            binding["directory_context"] = context.as_log_dict()
            log_shadow_context(
                logger,
                prefix=f"provider={provider} repo={repo} pr={pr_id} session_id={session_id or 'unknown'} shadow_readiness",
                context=context,
            )
            resolved.append(binding)
        return resolved

    def _log_discovery(
        self,
        envelope: FeedbackEnvelope,
        *,
        stream_bindings: list[dict[str, str | None]] | None = None,
    ) -> None:
        bindings = stream_bindings or []
        logger.info(
            "Discovered new PR feedback provider=%s repo=%s pr=%s feedback_id=%s stream_bindings=%s",
            envelope.provider,
            envelope.repo,
            envelope.pr_id,
            envelope.feedback_id,
            bindings,
        )
        self.audit_logger.log_decision(
            event_type=AuditEventType.FEEDBACK_DETECTED,
            session_id=bindings[0]["session_id"] if bindings else None,
            vm_id=bindings[0]["vm_id"] if bindings else None,
            provider=envelope.provider,
            repo=envelope.repo,
            pr_id=envelope.pr_id,
            decision="detected",
            reason="new-feedback",
            args={
                "feedback": envelope.model_dump(mode="json"),
                "stream_bindings": bindings,
            },
        )


def build_pr_feedback_worker(
    runtime_dir: Path,
    *,
    adapters: dict[str, FeedbackProviderAdapter],
    gateway_config: GatewayConfig | None = None,
) -> PullRequestFeedbackWorker:
    store = PrFeedbackStore(runtime_dir / "pr-feedback-store.json")
    audit = AuditLogger(runtime_dir / "audit.log")
    directory_context_resolver = (
        WorkerDirectoryContextResolver(gateway_config)
        if gateway_config is not None and gateway_config.enable_directory_scoped_config
        else None
    )
    return PullRequestFeedbackWorker(
        store=store,
        adapters=adapters,
        audit_logger=audit,
        directory_context_resolver=directory_context_resolver,
    )
