"""Live ownership validation for core Git submission bindings owned by Team."""

from .repository import TeamStateRepository
from .services.domain.preparation_failures import task_git_work_context_generation


def validate_submission_checkout(context, binding):
    # Preparation fingerprints include dependency/recovery and human-review state.
    state = TeamStateRepository(context.runtime_dir).read()
    work = state.git_work_contexts.get(binding["owner_id"])
    if work is None or work.state.value != "active":
        return False
    agent = state.agents.get(work.agent_id)
    task = state.tasks.get(work.task_id)
    if (
        agent is None
        or agent.session_id != binding["session_id"]
        or task is None
        or task.owner_agent_id != work.agent_id
    ):
        return False
    if (
        work.worktree_path != binding["guest_path"]
        or work.agent_branch != binding["fixed_branch"]
        or work.preparation_generation != binding["generation"]
        or work.base_commit != binding.get("provenance_base")
    ):
        return False
    if (
        work.preparation_generation
        and work.preparation_generation != task_git_work_context_generation(state, task.id)
    ):
        return False
    attempt_id = binding.get("preparation_attempt_id")
    if attempt_id:
        attempt = state.git_preparation_attempts.get(attempt_id)
        if (
            attempt is None
            or attempt.state.value not in {"prepared", "committed"}
            or attempt.base_commit_sha != binding["prepared_base"]
            or attempt.worktree_path != binding["guest_path"]
        ):
            return False
    return True
