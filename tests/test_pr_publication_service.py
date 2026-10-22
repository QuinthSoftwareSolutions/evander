from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from evander.admin.services.pr_publication_service import (
    PrPublicationDependencies,
    PrPublicationService,
)


@pytest.mark.parametrize("push_succeeds", [False, True])
def test_personal_publication_checks_identity_before_push_and_binds_only_after_creation(
    push_succeeds: bool,
) -> None:
    events: list[str] = []
    commit = "a" * 40

    def resolve_publication(context_id: str, **kwargs: Any) -> None:
        events.append("resolve-identity")
        assert kwargs["commit_sha"] == commit

    @contextmanager
    def target_lock(repo_id: str, target: str):
        events.append("lock")
        assert target == "upstream:refs/heads/feature"
        try:
            yield
        finally:
            events.append("unlock")

    def push(*args: Any, **kwargs: Any) -> dict[str, Any]:
        events.append("push")
        assert args[2] == commit
        assert kwargs["_target_lock_held"] is True
        return {"success": push_succeeds}

    def publish(record: Any, **kwargs: Any) -> dict[str, Any]:
        events.append("preflight" if kwargs.get("preflight_only") else "create")
        assert kwargs["expected_commit_sha"] == commit
        return {"provider": "azure_devops", "pr_id": "42"}

    def bind(record: Any, **kwargs: Any) -> None:
        events.append("bind-feedback")
        assert kwargs["pr"]["pr_id"] == "42"

    service = PrPublicationService(
        PrPublicationDependencies(
            require_scoped_record=lambda repo_id, directory: {
                "provider": "azure_devops",
                "default_branch": "main",
                "canonical_repository_url": "https://dev.azure.com/org/project/_git/repo",
            },
            normalize_branch=lambda branch, **kwargs: branch,
            resolve_publication=resolve_publication,
            azure_repository_parts=lambda url: ("org", "project", "repo"),
            resolve_pr_tracking_session=lambda sid: None,
            azure_work_item_id=lambda explicit, sid: explicit,
            target_lock=target_lock,
            push_branch=push,
            create_or_reuse_github_pr=lambda *args, **kwargs: pytest.fail("Wrong provider"),
            create_or_reuse_azure_pr=publish,
            persist_pr_tracking=bind,
        )
    )

    result = service.create(
        managed_repo_id="repo",
        source_ref="refs/heads/candidate",
        expected_commit_sha=commit,
        destination_branch="feature",
        directory_path="root",
        publication_context_id="personal-publication",
        publication_policy="require_personal",
    )

    assert result["success"] is push_succeeds
    assert events == (
        ["resolve-identity", "lock", "preflight", "push", "create", "bind-feedback", "unlock"]
        if push_succeeds
        else ["resolve-identity", "lock", "preflight", "push", "unlock"]
    )
