from __future__ import annotations

from pathlib import Path

import pytest

from evander.artifact_spaces import (
    ArtifactSpacePermissionError,
    ArtifactSpaceStore,
    normalize_space_path,
)
from evander.effects.providers.artifact_space import (
    ArtifactSpaceCreateEffectProvider,
    ArtifactSpacePublishVersionEffectProvider,
)
from evander.effects.providers.base import EffectRuntimeContext
from evander.events.provider_models import (
    ArtifactSpaceCreateEffectDefinition,
    ArtifactSpacePublishVersionEffectDefinition,
)
from evander.published_artifacts import PublishedArtifactStore
from config_helpers import write_runtime_config
from evander.queries.providers.artifact_space import (
    ArtifactSpaceLatestQueryProvider,
    ArtifactSpaceListQueryProvider,
)
from evander.queries.providers.base import QueryRuntimeContext
from evander.queries.providers.schemas import (
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
    assert state.spaces["/teams/team/projects"].grants.manage_subjects == ["team-addon:agent:lead"]
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

    from evander.admin.composition import build_admin_runtime
    from evander.admin.web.app import create_app

    config_path = write_runtime_config(tmp_path)
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


def test_artifact_space_admin_api_uses_session_subject_for_grants(tmp_path: Path) -> None:
    from starlette.testclient import TestClient

    from evander.admin.composition import build_admin_runtime
    from evander.admin.web.app import create_app

    runtime_dir = tmp_path / "runtime"
    config_path = write_runtime_config(tmp_path)
    PublishedArtifactStore(runtime_dir).publish_single_file(
        key="uploads/secret",
        session_id="session-1",
        filename="secret.txt",
        content=b"secret",
    )
    store = ArtifactSpaceStore(runtime_dir)
    store.ensure_space(
        path="/teams/secret",
        grants={
            "read_subjects": ["allowed-subject"],
            "write_subjects": ["allowed-subject"],
            "manage_subjects": ["allowed-subject"],
        },
    )
    store.publish_version(
        space_path="/teams/secret",
        artifact_id="secret",
        artifact_key="uploads/secret",
        created_by_subject="allowed-subject",
    )
    client = TestClient(create_app(build_admin_runtime(config_path)))

    listed_spaces = client.get(
        "/api/admin/artifact-spaces",
        params={
            "path": "/teams/secret",
            "recursive": "true",
            "include_versions": "true",
            "subject": "allowed-subject",
        },
    )
    listed_artifacts = client.get(
        "/api/admin/artifacts",
        params={"space_path": "/teams/secret", "subject": "allowed-subject"},
    )
    latest = client.get(
        "/api/admin/artifact-space-versions/latest",
        params={
            "space_path": "/teams/secret",
            "artifact_id": "secret",
            "subject": "allowed-subject",
        },
    )
    publish = client.post(
        "/api/admin/artifact-space-versions",
        json={
            "space_path": "/teams/secret",
            "artifact_id": "secret-copy",
            "artifact_key": "uploads/secret",
            "subject": "allowed-subject",
        },
    )
    create_child = client.post(
        "/api/admin/artifact-spaces",
        json={"path": "/teams/secret/child", "subject": "allowed-subject"},
    )

    assert listed_spaces.status_code == 200
    assert listed_spaces.json()["spaces"] == []
    assert listed_spaces.json()["versions"] == []
    assert listed_artifacts.status_code == 200
    assert listed_artifacts.json()["total"] == 0
    assert latest.status_code == 403
    assert publish.status_code == 403
    assert create_child.status_code == 403
    state = store.read()
    assert "/teams/secret/child" not in state.spaces
    assert len(state.versions_for_space("/teams/secret")) == 1


def test_artifact_space_admin_api_rejects_subject_spoofing_for_authenticated_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from starlette.testclient import TestClient

    from evander.admin.composition import build_admin_runtime
    from evander.admin.web.app import create_app

    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "bob")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "secret")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")

    runtime_dir = tmp_path / "runtime"
    config_path = write_runtime_config(tmp_path)
    PublishedArtifactStore(runtime_dir).publish_single_file(
        key="uploads/alice-secret",
        session_id="session-1",
        filename="secret.txt",
        content=b"secret",
    )
    store = ArtifactSpaceStore(runtime_dir)
    store.ensure_space(
        path="/teams/alice-private",
        grants={
            "read_subjects": ["alice"],
            "write_subjects": ["alice"],
            "manage_subjects": ["alice"],
        },
    )
    store.publish_version(
        space_path="/teams/alice-private",
        artifact_id="secret",
        artifact_key="uploads/alice-secret",
        created_by_subject="alice",
    )
    client = TestClient(create_app(build_admin_runtime(config_path)))
    login = client.post("/api/auth/login", json={"username": "bob", "password": "secret"})
    assert login.status_code == 200
    assert login.json()["username"] == "bob"

    listed_artifacts = client.get(
        "/api/admin/artifacts",
        params={"space_path": "/teams/alice-private", "subject": "alice"},
    )
    latest = client.get(
        "/api/admin/artifact-space-versions/latest",
        params={
            "space_path": "/teams/alice-private",
            "artifact_id": "secret",
            "subject": "alice",
        },
    )
    publish = client.post(
        "/api/admin/artifact-space-versions",
        json={
            "space_path": "/teams/alice-private",
            "artifact_id": "secret-copy",
            "artifact_key": "uploads/alice-secret",
            "subject": "alice",
        },
    )

    assert listed_artifacts.status_code == 200
    assert listed_artifacts.json()["total"] == 0
    assert listed_artifacts.json()["items"] == []
    assert latest.status_code == 403
    assert "subject bob cannot read artifact space /teams/alice-private" in latest.json()["error"]
    assert publish.status_code == 403
    assert "subject bob cannot write artifact space /teams/alice-private" in publish.json()["error"]
    assert len(store.read().versions_for_space("/teams/alice-private")) == 1
