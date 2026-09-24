from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..models import TeamState
from ..repository import TeamStateRepository
from .agent_action_application import AgentActionApplication


class TeamAgentActionService:
    """Compatibility facade for applying one persistent agent action batch."""

    def __init__(
        self,
        repository: TeamStateRepository,
        *,
        human_review_eligibility_resolver: Callable[[Any], list[str]] | None = None,
        git_default_branch_resolver: Callable[..., object] | None = None,
        git_base_commit_resolver: Callable[..., object] | None = None,
        session_lookup: Any | None = None,
    ) -> None:
        self._repository = repository
        self._human_review_eligibility_resolver = human_review_eligibility_resolver
        self._git_default_branch_resolver = git_default_branch_resolver
        self._git_base_commit_resolver = git_base_commit_resolver
        self._session_lookup = session_lookup

    @property
    def repository(self) -> TeamStateRepository:
        return self._repository

    def apply_agent_action(
        self,
        *,
        action: dict[str, Any] | None,
        session_id: str | None = None,
        dispatch_id: str | None = None,
        valid: bool = True,
        error: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        application = AgentActionApplication(
            self._repository,
            human_review_eligibility_resolver=self._human_review_eligibility_resolver,
            git_default_branch_resolver=self._git_default_branch_resolver,
            git_base_commit_resolver=self._git_base_commit_resolver,
            session_lookup=self._session_lookup,
        )
        return application.apply_agent_action(
            action=action,
            session_id=session_id,
            dispatch_id=dispatch_id,
            valid=valid,
            error=error,
            expected_revision=expected_revision,
        )
