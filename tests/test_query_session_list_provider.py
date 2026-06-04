from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_sandbox.effects.providers.session_update_metadata import (
    SessionUpdateMetadataEffectProvider,
)
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.events.provider_models import (
    SessionListQueryDefinition,
    SessionUpdateMetadataEffectDefinition,
)
from agent_sandbox.models import SessionManifest
from agent_sandbox.queries.providers.base import QueryRuntimeContext
from agent_sandbox.queries.providers.session_list import SessionListQueryProvider


@dataclass
class _SessionStore:
    sessions: dict[str, SessionManifest]

    def read_all_sessions(self) -> dict[str, SessionManifest]:
        return dict(self.sessions)

    def session_update_metadata(self, *, target: str, metadata: dict[str, Any]) -> dict[str, Any]:
        session = self.sessions[target]
        merged = dict(session.metadata)
        merged.update(metadata)
        self.sessions[target] = session.model_copy(update={"metadata": merged})
        return {
            "success": True,
            "session_id": target,
            "metadata": merged,
        }


def _session(
    session_id: str,
    *,
    directory_path: str = "root/team-a",
    metadata: dict[str, Any] | None = None,
    active: bool = True,
    created_offset_minutes: int = 0,
) -> SessionManifest:
    now = datetime.now(UTC)
    return SessionManifest(
        session_id=session_id,
        vm_id=f"vm-{session_id}",
        directory_path=directory_path,
        model="gpt-5.4",
        allowed_tools=[],
        created_at=now + timedelta(minutes=created_offset_minutes),
        expires_at=now + (timedelta(hours=1) if active else -timedelta(hours=1)),
        metadata=metadata or {},
    )


def _execute(
    query: SessionListQueryDefinition,
    store: _SessionStore,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider = SessionListQueryProvider()
    provider.initialize(
        query,
        QueryRuntimeContext(
            provider_config={},
            session_lookup=store,
            metadata=metadata or {},
        ),
    )
    return provider.execute()


def test_session_list_query_uses_context_directory_when_omitted() -> None:
    store = _SessionStore(
        {
            "owner": _session("owner", directory_path="root/team-a"),
            "reviewer": _session("reviewer", directory_path="root/team-a/reviews"),
            "other": _session("other", directory_path="root/team-b"),
        }
    )

    output = _execute(
        SessionListQueryDefinition(
            id="query-sessions",
            display_name="Sessions",
            type="query:session:list",
        ),
        store,
        metadata={"directory_path": "root/team-a"},
    )

    assert [item["session_id"] for item in output["sessions"]] == ["owner", "reviewer"]


def test_session_list_query_explicit_directory_overrides_context_directory() -> None:
    store = _SessionStore(
        {
            "owner": _session("owner", directory_path="root/team-a"),
            "other": _session("other", directory_path="root/team-b"),
        }
    )

    output = _execute(
        SessionListQueryDefinition(
            id="query-sessions",
            display_name="Sessions",
            type="query:session:list",
            directory_path="root/team-b",
        ),
        store,
        metadata={"directory_path": "root/team-a"},
    )

    assert [item["session_id"] for item in output["sessions"]] == ["other"]


def test_session_list_query_defaults_to_root_without_context_directory() -> None:
    store = _SessionStore(
        {
            "owner": _session("owner", directory_path="root/team-a"),
            "other": _session("other", directory_path="root/team-b"),
        }
    )

    output = _execute(
        SessionListQueryDefinition(
            id="query-sessions",
            display_name="Sessions",
            type="query:session:list",
        ),
        store,
    )

    assert [item["session_id"] for item in output["sessions"]] == ["owner", "other"]


def test_session_list_query_matches_arbitrary_metadata_and_directory_scope() -> None:
    store = _SessionStore(
        {
            "owner": _session(
                "owner",
                metadata={
                    "owns_pr": "17",
                    "pr_provider": "azure_devops",
                    "pr_repo": "org/project/repo",
                },
            ),
            "reviewer": _session(
                "reviewer",
                directory_path="root/team-a/reviews",
                metadata={
                    "reviewing_pr": "17",
                    "pr_provider": "azure_devops",
                    "pr_repo": "org/project/repo",
                },
            ),
            "other": _session(
                "other",
                directory_path="root/team-b",
                metadata={"reviewing_pr": "17", "pr_provider": "azure_devops"},
            ),
        }
    )

    output = _execute(
        SessionListQueryDefinition(
            id="query-sessions",
            display_name="Sessions",
            type="query:session:list",
            directory_path="root/team-a",
            metadata_equals={"pr_provider": "azure_devops"},
            metadata_exists=["owns_pr"],
            active_only=True,
        ),
        store,
    )

    assert output["count"] == 1
    assert output["sessions"][0]["session_id"] == "owner"
    assert output["sessions"][0]["metadata"]["owns_pr"] == "17"

    missing_key_output = _execute(
        SessionListQueryDefinition(
            id="query-missing",
            display_name="Missing metadata",
            type="query:session:list",
            directory_path="root/team-a",
            metadata_exists={"reviewing_pr": "false"},
            active_only=True,
        ),
        store,
    )
    assert [item["session_id"] for item in missing_key_output["sessions"]] == ["owner"]


def test_session_list_query_supports_metadata_prefix_state_and_limit() -> None:
    store = _SessionStore(
        {
            "first": _session(
                "first",
                metadata={"custom_key": "review:17:first"},
                created_offset_minutes=1,
            ),
            "second": _session(
                "second",
                metadata={"custom_key": "review:17:second"},
                created_offset_minutes=2,
            ),
            "expired": _session(
                "expired",
                metadata={"custom_key": "review:17:expired"},
                active=False,
                created_offset_minutes=3,
            ),
        }
    )

    active_output = _execute(
        SessionListQueryDefinition(
            id="query-active",
            display_name="Active sessions",
            type="query:session:list",
            metadata_prefix={"custom_key": "review:17:"},
            state="active",
            limit=1,
        ),
        store,
    )
    expired_output = _execute(
        SessionListQueryDefinition(
            id="query-expired",
            display_name="Expired sessions",
            type="query:session:list",
            metadata_prefix={"custom_key": "review:17:"},
            state="expired",
        ),
        store,
    )

    assert active_output["count"] == 1
    assert active_output["sessions"][0]["session_id"] == "first"
    assert [item["session_id"] for item in expired_output["sessions"]] == ["expired"]


def test_session_metadata_update_plus_session_list_can_route_pr_feedback_fanout() -> None:
    store = _SessionStore(
        {
            "owner": _session("owner", metadata={"agent_thread_id": "thread-owner"}),
            "reviewer": _session("reviewer", metadata={"agent_thread_id": "thread-reviewer"}),
        }
    )
    for session_id, metadata in {
        "owner": {
            "owns_pr": "17",
            "pr_provider": "azure_devops",
            "pr_repo": "org/project/repo",
        },
        "reviewer": {
            "reviewing_pr": "17",
            "pr_provider": "azure_devops",
            "pr_repo": "org/project/repo",
            "pr_feedback_streams": [
                {"provider": "azure_devops", "repo": "org/project/repo", "pr_id": "17"}
            ],
        },
    }.items():
        provider = SessionUpdateMetadataEffectProvider()
        provider.initialize(
            SessionUpdateMetadataEffectDefinition(
                id=f"update-{session_id}",
                display_name="Update",
                type="effect:session:update-metadata",
                target=session_id,
                metadata=metadata,
            ),
            EffectRuntimeContext(provider_config={}, session_metadata_updater=store),
        )
        assert provider.execute()["success"] is True

    owner_sessions = _execute(
        SessionListQueryDefinition(
            id="query-owners",
            display_name="Owners",
            type="query:session:list",
            metadata_equals={
                "owns_pr": "17",
                "pr_provider": "azure_devops",
                "pr_repo": "org/project/repo",
            },
            active_only=True,
        ),
        store,
    )
    reviewer_sessions = _execute(
        SessionListQueryDefinition(
            id="query-reviewers",
            display_name="Reviewers",
            type="query:session:list",
            metadata_equals={
                "reviewing_pr": "17",
                "pr_provider": "azure_devops",
                "pr_repo": "org/project/repo",
            },
            active_only=True,
        ),
        store,
    )

    targets = {
        session["session_id"]
        for session in owner_sessions["sessions"] + reviewer_sessions["sessions"]
    }
    assert targets == {"owner", "reviewer"}
    assert store.sessions["reviewer"].metadata["pr_feedback_streams"] == [
        {"provider": "azure_devops", "repo": "org/project/repo", "pr_id": "17"}
    ]
