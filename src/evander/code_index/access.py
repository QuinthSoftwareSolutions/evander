from __future__ import annotations

from evander.managed_git_grants import managed_git_grants_from_metadata

from .models import commit_id


def authorize_session(components, session, repo: str, sha: str | None) -> str:
    if repo not in managed_git_grants_from_metadata(session.metadata):
        raise PermissionError("Session has no current Git grant for this repository")
    components.managed_git_admin.get_repository(repo, directory_path=session.directory_path)
    from evander_team_addon.code_index import session_bindings

    bindings = session_bindings(components.runtime_dir, session.session_id)
    if bindings is None and session.metadata.get("team_addon") is not None:
        # A retired/deleted Team actor must never regain broad repository scope.
        bindings = set()
    if bindings is not None:
        candidates = {commit for repository, commit in bindings if repository == repo}
        if sha is None and len(candidates) == 1:
            sha = next(iter(candidates))
        if sha not in candidates:
            raise PermissionError("Index request is outside the active immutable Team Git binding")
    if sha is None:
        raise ValueError("An exact commit is required")
    return commit_id(sha)
