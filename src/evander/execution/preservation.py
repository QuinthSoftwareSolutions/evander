"""Protect session data retained by a durable recovery before backend teardown."""

from pathlib import Path


def require_session_data_disposable(runtime_dir, session_id: str) -> None:
    # An absent Team store is the ordinary non-Team execution path. Avoid
    # initializing addon state merely to delete such a session.
    path = Path(runtime_dir) / "addons" / "team-addon" / "state.db"
    if not path.exists():
        return
    from evander_team_addon.repository import TeamStateRepository
    from evander_team_addon.services.domain.git_recovery import preservation_hold

    state = TeamStateRepository(runtime_dir).read_projection(
        collections=["git_recovery_incidents", "git_preparation_attempts", "git_work_contexts"]
    )
    hold = preservation_hold(state, session_id=session_id)
    if hold:
        raise ValueError(
            f"Session {session_id} retains work for Git recovery {hold}; archive its retained data before teardown"
        )
