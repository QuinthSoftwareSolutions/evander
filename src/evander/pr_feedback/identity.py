from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

from evander.pr_tracking import list_pr_map_entries, pr_map_provider_name


def pr_feedback_stream_aliases(
    stream: dict[str, str],
    *,
    pr_map_path: Path | None,
) -> list[dict[str, str]]:
    """Return safely equivalent persisted identities for one feedback stream.

    Azure DevOps accepts both a short repository name and a scoped
    organization/project/repository identifier. Persisted feedback historically
    retained whichever form the polling workflow supplied. The PR map is the
    authority used to relate those forms; a short name is never expanded when it
    could refer to more than one scoped repository for the same pull-request ID.
    """

    normalized = {
        "provider": str(stream.get("provider", "")).strip(),
        "repo": str(stream.get("repo", "")).strip(),
        "pr_id": str(stream.get("pr_id", "")).strip(),
    }
    aliases = [normalized]
    if (
        pr_map_provider_name(normalized["provider"]) != "azure_devops"
        or not normalized["repo"]
        or not normalized["pr_id"]
        or pr_map_path is None
    ):
        return aliases

    requested_name = _azure_devops_repository_name(normalized["repo"])
    requested_scope = _azure_devops_repository_scope(normalized["repo"])
    if requested_scope is None and not _azure_devops_repository_is_short(normalized["repo"]):
        return aliases
    matching_entries = [
        entry
        for entry in list_pr_map_entries(pr_map_path, provider="azure_devops")
        if entry.pr_id == normalized["pr_id"]
        and _azure_devops_repository_name(entry.repo).casefold() == requested_name.casefold()
    ]
    scoped_entries = [
        (entry, scope)
        for entry in matching_entries
        if (scope := _azure_devops_repository_scope(entry.repo)) is not None
    ]
    known_scopes = {scope for _, scope in scoped_entries}
    has_unscoped_binding = len(scoped_entries) != len(matching_entries)

    if requested_scope is None:
        if len(known_scopes) != 1 or has_unscoped_binding:
            return aliases
        selected_scope = next(iter(known_scopes))
    else:
        selected_scope = requested_scope
        if not any(scope == selected_scope for _, scope in scoped_entries):
            return aliases

    for entry, scope in scoped_entries:
        if scope == selected_scope:
            _append_repo_alias(aliases, normalized, entry.repo)

    # A short persisted identity cannot distinguish two repositories with the
    # same name and PR number, so include it only when the PR map is unambiguous.
    if known_scopes == {selected_scope} and not has_unscoped_binding:
        for entry, _scope in scoped_entries:
            _append_repo_alias(
                aliases,
                normalized,
                _azure_devops_repository_name(entry.repo),
            )

    return aliases


def _append_repo_alias(
    aliases: list[dict[str, str]],
    stream: dict[str, str],
    repo: str,
) -> None:
    candidate = {
        "provider": stream["provider"],
        "repo": str(repo).strip(),
        "pr_id": stream["pr_id"],
    }
    if candidate["repo"] and candidate not in aliases:
        aliases.append(candidate)


def _azure_devops_repository_name(repo: str) -> str:
    normalized = unquote(str(repo or "").strip()).rstrip("/")
    if not normalized:
        return normalized
    if "/_git/" in normalized:
        return normalized.split("/_git/", 1)[1].split("/", 1)[0]
    if "/_apis/git/repositories/" in normalized:
        return normalized.split("/_apis/git/repositories/", 1)[1].split("/", 1)[0]
    if "ssh.dev.azure.com:v3/" in normalized:
        return normalized.split("ssh.dev.azure.com:v3/", 1)[1].rsplit("/", 1)[-1]
    if normalized.count("/") >= 2:
        return normalized.rsplit("/", 1)[-1]
    return normalized


def _azure_devops_repository_scope(repo: str) -> tuple[str, str, str] | None:
    normalized = unquote(str(repo or "").strip()).rstrip("/")
    if not normalized:
        return None

    ssh_marker = "ssh.dev.azure.com:v3/"
    if ssh_marker in normalized:
        parts = _nonempty_path_parts(normalized.split(ssh_marker, 1)[1])
        return _scope_from_parts(parts)

    parsed = urlparse(normalized)
    if parsed.scheme and parsed.netloc:
        parts = _nonempty_path_parts(parsed.path)
        if (
            parsed.scheme.casefold() == "ssh"
            and parsed.hostname
            and parsed.hostname.casefold() == "ssh.dev.azure.com"
            and len(parts) == 4
            and parts[0].casefold() == "v3"
        ):
            return _normalized_scope(parts[1], parts[2], parts[3])

        git_index = _part_index(parts, "_git")
        if git_index is not None and git_index >= 1 and len(parts) > git_index + 1:
            if parsed.hostname and parsed.hostname.casefold().endswith(".visualstudio.com"):
                organization = parsed.hostname.split(".", 1)[0]
            elif git_index >= 2:
                organization = parts[0]
            else:
                return None
            return _normalized_scope(organization, parts[git_index - 1], parts[git_index + 1])

        api_index = _part_index(parts, "_apis")
        repositories_index = _part_index(parts, "repositories")
        if (
            api_index is not None
            and repositories_index is not None
            and api_index >= 2
            and len(parts) > repositories_index + 1
        ):
            return _normalized_scope(
                parts[0],
                parts[api_index - 1],
                parts[repositories_index + 1],
            )
        return None

    return _scope_from_parts(_nonempty_path_parts(normalized))


def _azure_devops_repository_is_short(repo: str) -> bool:
    normalized = unquote(str(repo or "").strip())
    if not normalized:
        return False
    parsed = urlparse(normalized)
    return (
        not parsed.scheme and not parsed.netloc and "/" not in normalized and "\\" not in normalized
    )


def _nonempty_path_parts(value: str) -> list[str]:
    return [part for part in value.split("/") if part]


def _part_index(parts: list[str], expected: str) -> int | None:
    expected_casefold = expected.casefold()
    return next(
        (index for index, part in enumerate(parts) if part.casefold() == expected_casefold),
        None,
    )


def _scope_from_parts(parts: list[str]) -> tuple[str, str, str] | None:
    if len(parts) < 3:
        return None
    return _normalized_scope(*parts[-3:])


def _normalized_scope(organization: str, project: str, repo: str) -> tuple[str, str, str]:
    return organization.casefold(), project.casefold(), repo.casefold()
