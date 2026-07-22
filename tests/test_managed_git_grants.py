from evander.managed_git_grants import (
    managed_git_grants_from_metadata,
    managed_git_key_name,
    managed_git_ssh_alias,
    normalize_git_access_lifetime,
    remove_managed_git_grant_metadata,
    upsert_managed_git_grant_metadata,
)


def test_plural_grants_preserve_unrelated_repositories_and_support_targeted_revoke() -> None:
    first = "mr_11111111111111111111"
    second = "mr_22222222222222222222"
    metadata = upsert_managed_git_grant_metadata(
        {"keep": True},
        {"managed_repo_id": first, "access_mode": "read-only", "lifetime": "session"},
    )
    metadata = upsert_managed_git_grant_metadata(
        metadata,
        {"managed_repo_id": second, "access_mode": "write", "lifetime": "session"},
    )

    assert set(managed_git_grants_from_metadata(metadata)) == {first, second}
    updated, removed = remove_managed_git_grant_metadata(metadata, first)

    assert removed is True
    assert updated["keep"] is True
    assert set(managed_git_grants_from_metadata(updated)) == {second}


def test_legacy_singular_grant_is_read_as_session_persistent() -> None:
    repo_id = "mr_11111111111111111111"
    grants = managed_git_grants_from_metadata(
        {
            "container_git_write_access": {
                "managed_repo_id": repo_id,
                "access_mode": "read-only",
            }
        }
    )

    assert grants[repo_id]["lifetime"] == "session"


def test_adding_a_second_grant_canonicalizes_legacy_singular_metadata() -> None:
    first = "mr_11111111111111111111"
    second = "mr_22222222222222222222"
    metadata = upsert_managed_git_grant_metadata(
        {
            "container_git_write_access": {
                "managed_repo_id": first,
                "access_mode": "read-only",
            }
        },
        {"managed_repo_id": second, "access_mode": "read-only"},
    )

    assert "container_git_write_access" not in metadata
    assert set(metadata["managed_git_access_grants"]) == {first, second}
    assert all(
        grant["lifetime"] == "session" for grant in metadata["managed_git_access_grants"].values()
    )


def test_repository_key_and_alias_are_stable_and_repository_specific() -> None:
    first = "mr_11111111111111111111"
    second = "mr_22222222222222222222"

    assert managed_git_key_name(first) == managed_git_key_name(first)
    assert managed_git_key_name(first) != managed_git_key_name(second)
    assert managed_git_ssh_alias("evander-git-host", first) != managed_git_ssh_alias(
        "evander-git-host", second
    )


def test_git_access_lifetime_defaults_to_persistent_session() -> None:
    assert normalize_git_access_lifetime(None) == "session"
    assert normalize_git_access_lifetime("operation") == "operation"
