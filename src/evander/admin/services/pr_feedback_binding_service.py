from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from evander.models import ToolName
from evander.pr_tracking import PrMapStore


@dataclass(frozen=True, slots=True)
class PrFeedbackBindingDependencies:
    pr_map_store: Callable[[], PrMapStore]
    resolve_session: Callable[[str], tuple[Any, Any] | None]
    github_slug: Callable[[str], str]
    azure_repository_parts: Callable[[str], tuple[str, str, str]]


class PrFeedbackBindingService:
    """Maintains PR feedback subscriptions and their session tool permissions."""

    def __init__(self, dependencies: PrFeedbackBindingDependencies) -> None:
        self._deps = dependencies

    def bind(
        self,
        record: Mapping[str, Any],
        *,
        pr: Mapping[str, Any],
        source_branch: str,
        target_branch: str,
        session_id: str,
        directory_path: str,
        tracking_session: tuple[Any, Any] | None = None,
    ) -> None:
        provider = str(pr.get("provider") or record.get("provider") or "").strip()
        pr_id = str(pr.get("pr_id") or "").strip()
        if not provider or not pr_id:
            raise RuntimeError("Pull request provider did not return tracking identity")
        canonical_url = str(record.get("canonical_repository_url") or "")
        if provider == "github":
            repo = self._deps.github_slug(canonical_url)
        elif provider == "azure_devops":
            repo = "/".join(self._deps.azure_repository_parts(canonical_url))
        else:
            raise ValueError(f"Unsupported PR tracking provider: {provider}")
        if session_id and tracking_session is None:
            tracking_session = self._deps.resolve_session(session_id)
        tracking_directory_path = directory_path
        if session_id:
            assert tracking_session is not None
            tracking_directory_path = (
                str(tracking_session[1].directory_path or "").strip() or "root"
            )
        self._deps.pr_map_store().upsert_binding(
            provider=provider,
            repo=repo,
            pr_id=pr_id,
            pr_url=str(pr.get("pr_url") or "").strip() or None,
            source_branch=source_branch,
            target_branch=target_branch,
            session_id=session_id,
            directory_path=tracking_directory_path,
        )
        if not session_id:
            return
        assert tracking_session is not None
        repository, manifest = tracking_session
        stream = {"provider": provider, "repo": repo, "pr_id": pr_id}
        metadata = dict(manifest.metadata)
        streams = metadata.get("pr_feedback_streams")
        streams = list(streams) if isinstance(streams, list) else []
        if stream not in streams:
            streams.append(stream)
        metadata["pr_feedback_streams"] = streams
        allowed_tools = list(manifest.allowed_tools)
        for tool in (
            ToolName.HAS_PENDING_FEEDBACK,
            ToolName.LIST_PULL_REQUESTS,
            ToolName.GET_PULL_REQUEST,
            ToolName.LIST_UNREAD_FEEDBACK,
            ToolName.LIST_ALL_FEEDBACK,
            ToolName.LIST_PR_FEEDBACK_ATTACHMENTS,
            ToolName.DOWNLOAD_PR_FEEDBACK_ATTACHMENT,
            ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE,
            ToolName.POST_PR_FEEDBACK_REPLY,
        ):
            if tool not in allowed_tools:
                allowed_tools.append(tool)
        repository.upsert_session(
            manifest.model_copy(update={"metadata": metadata, "allowed_tools": allowed_tools})
        )
