from pathlib import Path

import pytest

from evander.admin.services.artifact_service import ArtifactAdminDependencies, ArtifactAdminService


@pytest.mark.parametrize(
    ("recursive", "subject", "expected_spaces", "expected_keys"),
    [
        (False, "reader", ["/team"], {"root-report"}),
        (True, "reader", ["/team", "/team/public"], {"root-report", "public-report"}),
        (
            True,
            "owner",
            ["/team", "/team/private", "/team/public"],
            {"root-report", "private-report", "public-report"},
        ),
        (True, None, ["/team", "/team/public"], {"root-report", "public-report"}),
    ],
)
def test_artifact_and_space_lists_share_scope_and_read_permissions(
    tmp_path: Path,
    recursive: bool,
    subject: str | None,
    expected_spaces: list[str],
    expected_keys: set[str],
) -> None:
    service = ArtifactAdminService(ArtifactAdminDependencies(runtime_dir=tmp_path))
    for path, key in (
        ("/team", "root-report"),
        ("/team/public", "public-report"),
        ("/team/private", "private-report"),
        ("/team-other", "neighbor-report"),
    ):
        service.create_artifact_space(
            path=path,
            grants={"manage_subjects": ["owner"]} if path == "/team/private" else None,
        )
        service.upload_published_artifact(key=key, filename="report.txt", content=b"report")
        service.publish_artifact_space_version(
            space_path=path, artifact_id="report", artifact_key=key, subject="owner"
        )

    spaces = service.list_artifact_spaces(
        path="/team", recursive=recursive, subject=subject, include_versions=True
    )
    artifacts = service.list_published_artifacts(
        space_path="/team", space_recursive=recursive, subject=subject
    )

    assert [space["path"] for space in spaces["spaces"]] == expected_spaces
    assert {version["artifact_key"] for version in spaces["versions"]} == expected_keys
    assert {artifact.key for artifact in artifacts.items} == expected_keys
