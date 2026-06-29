from __future__ import annotations

from pathlib import Path

import pytest

from agent_sandbox.artifact_spaces import (
    ArtifactSpacePermissionError,
    ArtifactSpaceStore,
    normalize_space_path,
)
from agent_sandbox.effects.providers.artifact_space import (
    ArtifactSpaceCreateEffectProvider,
    ArtifactSpacePublishVersionEffectProvider,
)
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.events.provider_models import (
    ArtifactSpaceCreateEffectDefinition,
    ArtifactSpacePublishVersionEffectDefinition,
)
from agent_sandbox.published_artifacts import PublishedArtifactStore
from agent_sandbox.queries.providers.artifact_space import (
    ArtifactSpaceLatestQueryProvider,
    ArtifactSpaceListQueryProvider,
)
from agent_sandbox.queries.providers.base import QueryRuntimeContext
from agent_sandbox.queries.providers.schemas import (
    ArtifactSpaceLatestQueryDefinition,
    ArtifactSpaceListQueryDefinition,
)


def test_artifact_space_store_versions_are_hierarchical_and_immutable(tmp_path: Path) -> None:
    artifact_store = PublishedArtifactStore(tmp_path)
    artifact_store.publish_single_file(
        key="builds/backend-v1",
        session_id="session-1",
        filename="result.txt",
        content=b"first",
    )
    artifact_store.publish_single_file(
        key="builds/backend-v2",
        session_id="session-1",
        filename="result.txt",
        content=b"second",
    )

    store = ArtifactSpaceStore(tmp_path)
    state = store.ensure_space(
        path="/teams/team/projects/backend",
        grants={
            "read_subjects": ["team-addon:agent:lead"],
            "write_subjects": ["team-addon:agent:backend"],
            "manage_subjects": ["team-addon:agent:lead"],
        },
    )
    assert "/teams/team/projects/backend" in state.spaces
    assert "/teams/team" in state.spaces

    state = store.publish_version(
        space_path="/teams/team/projects/backend",
        artifact_id="backend-build",
        artifact_key="builds/backend-v1",
        created_by_subject="team-addon:agent:backend",
    )
    state = store.publish_version(
        space_path="/teams/team/projects/backend",
        artifact_id="backend-build",
        artifact_key="builds/backend-v2",
        created_by_subject="team-addon:agent:backend",
    )

    latest = state.latest_versions_for_space(
        "/teams/team/projects/backend",
        artifact_id="backend-build",
    )
    assert len(latest) == 1
    assert latest[0].version == 2
    assert latest[0].artifact_key == "builds/backend-v2"


def test_artifact_space_implicit_parents_inherit_restricted_ancestor_grants(
    tmp_path: Path,
) -> None:
    store = ArtifactSpaceStore(tmp_path)
    store.ensure_space(
        path="/teams",
        grants={
            "read_subjects": ["team-addon:agent:lead"],
            "write_subjects": ["team-addon:agent:lead"],
            "manage_subjects": ["team-addon:agent:lead"],
        },
        subject="team-addon:agent:lead",
    )

    with pytest.raises(ArtifactSpacePermissionError):
        store.ensure_space(
            path="/teams/team/projects/backend",
            subject="team-addon:agent:backend",
        )

    state = store.ensure_space(
        path="/teams/team/projects/backend",
        subject="team-addon:agent:lead",
    )

    assert state.spaces["/teams/team"].grants.manage_subjects == ["team-addon:agent:lead"]
    assert state.spaces["/teams/team/projects"].grants.manage_subjects == [
        "team-addon:agent:lead"
    ]
    with pytest.raises(ArtifactSpacePermissionError):
        store.ensure_space(
            path="/teams/team/projects/frontend",
            subject="team-addon:agent:backend",
        )


@pytest.mark.parametrize("path", ["//", "//team"])
def test_artifact_space_rejects_double_slash_roots(path: str) -> None:
    with pytest.raises(ValueError, match="normalized absolute path"):
        normalize_space_path(path)


def test_artifact_space_effects_and_queries(tmp_path: Path) -> None:
    PublishedArtifactStore(tmp_path).publish_single_file(
        key="manual/spec",
        session_id="session-1",
        filename="spec.txt",
        content=b"spec",
    )
    effect_context = EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)})
    query_context = QueryRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)})

    create = ArtifactSpaceCreateEffectProvider()
    create.initialize(
        ArtifactSpaceCreateEffectDefinition(
            id="space",
            display_name="Create space",
            type="effect:artifact-space:create",
            path="/teams/team",
        ),
        effect_context,
    )
    assert create.execute()["success"] is True

    publish = ArtifactSpacePublishVersionEffectProvider()
    publish.initialize(
        ArtifactSpacePublishVersionEffectDefinition(
            id="version",
            display_name="Publish version",
            type="effect:artifact-space:publish-version",
            space_path="/teams/team",
            artifact_id="spec",
            artifact_key="manual/spec",
        ),
        effect_context,
    )
    assert publish.execute()["version"]["version"] == 1

    list_query = ArtifactSpaceListQueryProvider()
    list_query.initialize(
        ArtifactSpaceListQueryDefinition(
            id="list",
            display_name="List spaces",
            type="query:artifact-space:list",
            include_versions=True,
        ),
        query_context,
    )
    listed = list_query.execute()
    assert listed["count"] >= 1
    assert listed["versions"][0]["artifact_key"] == "manual/spec"

    latest_query = ArtifactSpaceLatestQueryProvider()
    latest_query.initialize(
        ArtifactSpaceLatestQueryDefinition(
            id="latest",
            display_name="Latest",
            type="query:artifact-space:latest",
            space_path="/teams/team",
            artifact_id="spec",
        ),
        query_context,
    )
    assert latest_query.execute()["artifact_key"] == "manual/spec"


def test_artifact_space_admin_api(tmp_path: Path) -> None:
    from starlette.testclient import TestClient

    from agent_sandbox.admin.composition import AdminRuntimeComponents, build_admin_runtime
    from agent_sandbox.admin.web.app import create_app

    config_path = tmp_path / "gateway.yaml"
    runtime_dir = tmp_path / "runtime"
    config_path.write_text(
        "\n".join(
            [
                f'runtime_dir: "{runtime_dir}"',
                f'audit_log_path: "{runtime_dir / "audit.jsonl"}"',
                f'session_store_path: "{runtime_dir / "sessions.json"}"',
                f'approval_store_path: "{runtime_dir / "approvals.json"}"',
            ]
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app(build_admin_runtime(config_path)))
    upload = client.post(
        "/api/admin/artifacts/uploads",
        data={"key": "uploads/spec"},
        files={"file": ("spec.txt", b"spec")},
    )
    assert upload.status_code == 201

    response = client.post(
        "/api/admin/artifact-spaces",
        json={"path": "/teams/team"},
    )
    assert response.status_code == 201
    initial_revision = response.json()["revision"]

    stale_create = client.post(
        "/api/admin/artifact-spaces",
        json={"path": "/teams/team/projects/backend", "expected_revision": 0},
    )
    assert stale_create.status_code == 409
    assert stale_create.json()["details"] == {
        "expected_revision": 0,
        "current_revision": initial_revision,
    }

    response = client.post(
        "/api/admin/artifact-space-versions",
        json={
            "space_path": "/teams/team",
            "artifact_id": "spec",
            "artifact_key": "uploads/spec",
        },
    )
    assert response.status_code == 201
    published_revision = response.json()["revision"]

    stale_publish = client.post(
        "/api/admin/artifact-space-versions",
        json={
            "space_path": "/teams/team",
            "artifact_id": "spec",
            "artifact_key": "uploads/spec",
            "expected_revision": initial_revision,
        },
    )
    assert stale_publish.status_code == 409
    assert stale_publish.json()["details"] == {
        "expected_revision": initial_revision,
        "current_revision": published_revision,
    }

    latest = client.get(
        "/api/admin/artifact-space-versions/latest",
        params={"space_path": "/teams/team", "artifact_id": "spec"},
    )
    assert latest.status_code == 200
    assert latest.json()["versions"][0]["artifact_key"] == "uploads/spec"
