from __future__ import annotations

import json
from pathlib import Path
import subprocess

from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.git_ensure_managed_repo import (
    GitEnsureManagedRepoEffectProvider,
)
from evander.effects.providers.git_promote_agent_branch import (
    GitPromoteAgentBranchEffectProvider,
)
from evander.effects.providers.git_push_branch import GitPushBranchEffectProvider
from evander.events.provider_models import (
    GitEnsureManagedRepoEffectDefinition,
    GitPrNoteQueryDefinition,
    GitPromoteAgentBranchEffectDefinition,
    GitPushBranchEffectDefinition,
    GitPushTriggerDefinition,
)
from evander.events.models import materialize_node_definition
from evander.git_pr_notes import agent_pr_notes_ref_for_session
from evander.queries.providers.base import QueryRuntimeContext
from evander.queries.providers.git_pr_note import GitPrNoteQueryProvider
from evander.triggers.providers.base import TriggerRuntimeContext
from evander.triggers.providers.git_push import (
    GitPushTriggerProvider,
    git_push_trigger_matches_repo,
    publish_git_push_event,
)
from evander.workflow.contracts import WorkflowNodeEnvelope


_MANAGED_REPO_ID = "mr_0123456789abcdefabcd"
_COMMIT_SHA = "a" * 40


class _ManagedGitPort:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def ensure_managed_git_repository(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("ensure", dict(kwargs)))
        return {
            "managed_repo_id": _MANAGED_REPO_ID,
            "internal_bare_path": "/runtime/managed-git/internal.git",
        }

    def promote_agent_branch(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("promote", dict(kwargs)))
        return {"success": True, "commit_sha": _COMMIT_SHA}

    def push_branch(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("push", dict(kwargs)))
        return {"success": True, "published_commit_sha": _COMMIT_SHA}

    def git_pr_note_query(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("note", dict(kwargs)))
        return {"has_note": True, "body": "PR body", "commit_sha": _COMMIT_SHA}


def _effect_context(port: _ManagedGitPort) -> EffectRuntimeContext:
    return EffectRuntimeContext(admin_service=port, env={})


def test_managed_git_effect_and_query_providers_call_exact_managed_id_contract() -> None:
    port = _ManagedGitPort()

    ensure = GitEnsureManagedRepoEffectProvider()
    ensure.initialize(
        GitEnsureManagedRepoEffectDefinition(
            id="ensure",
            display_name="Ensure managed repository",
            type="effect:git:ensure-managed-repo",
            directory_path=" root/team ",
            repo_source=" https://example.invalid/org/repo.git ",
        ),
        _effect_context(port),
    )
    ensure_output = ensure.execute()
    assert ensure_output["managed_repo_id"] == _MANAGED_REPO_ID
    assert "internal_bare_path" not in ensure_output

    promote = GitPromoteAgentBranchEffectProvider()
    promote.initialize(
        GitPromoteAgentBranchEffectDefinition(
            id="promote",
            display_name="Promote",
            type="effect:git:promote-agent-branch",
            managed_repo_id=f" {_MANAGED_REPO_ID} ",
            agent_branch=" agents/session-1/topic ",
            target_branch=" review/session-1/topic ",
            expected_commit_sha=f" {_COMMIT_SHA} ",
        ),
        _effect_context(port),
    )
    assert promote.execute()["success"] is True

    push = GitPushBranchEffectProvider()
    push.initialize(
        GitPushBranchEffectDefinition(
            id="push",
            display_name="Push",
            type="effect:git:push-branch",
            managed_repo_id=f" {_MANAGED_REPO_ID} ",
            source_ref=" refs/evander/operations/op-1/source ",
            expected_commit_sha=f" {_COMMIT_SHA} ",
            destination_branch=" feature/topic ",
        ),
        _effect_context(port),
    )
    assert push.execute()["success"] is True

    note = GitPrNoteQueryProvider()
    note.initialize(
        GitPrNoteQueryDefinition(
            id="note",
            display_name="Read note",
            type="query:git:pr-note",
            managed_repo_id=f" {_MANAGED_REPO_ID} ",
            commit_sha=f" {_COMMIT_SHA} ",
            notes_ref=" refs/notes/evander/session-1 ",
        ),
        QueryRuntimeContext(git_pr_note_reader=port),
    )
    assert note.execute()["body"] == "PR body"

    assert port.calls == [
        (
            "ensure",
            {
                "directory_path": "root/team",
                "repo_source": "https://example.invalid/org/repo.git",
            },
        ),
        (
            "promote",
            {
                "managed_repo_id": _MANAGED_REPO_ID,
                "agent_branch": "agents/session-1/topic",
                "target_branch": "review/session-1/topic",
                "expected_commit_sha": _COMMIT_SHA,
                "directory_path": "root",
            },
        ),
        (
            "push",
            {
                "managed_repo_id": _MANAGED_REPO_ID,
                "source_ref": "refs/evander/operations/op-1/source",
                "expected_commit_sha": _COMMIT_SHA,
                "destination_branch": "feature/topic",
                "directory_path": "root",
            },
        ),
        (
            "note",
            {
                "managed_repo_id": _MANAGED_REPO_ID,
                "commit_sha": _COMMIT_SHA,
                "notes_ref": "refs/notes/evander/session-1",
                "directory_path": "root",
            },
        ),
    ]


def test_ensure_managed_repo_effect_materializes_through_builtin_resolver() -> None:
    definition = materialize_node_definition(
        WorkflowNodeEnvelope(
            id="ensure",
            display_name="Ensure managed repository",
            type="effect:git:ensure-managed-repo",
            config={
                "directory_path": "root/team",
                "repo_source": "https://example.invalid/org/repo.git",
            },
        )
    )

    assert isinstance(definition, GitEnsureManagedRepoEffectDefinition)


def test_git_push_trigger_selects_and_emits_only_managed_repository_identity(
    tmp_path: Path,
) -> None:
    repo_path = tmp_path / "managed.git"
    subprocess.run(
        ["git", "init", "--bare", str(repo_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    for key, value in (
        ("evander.managedRepoId", _MANAGED_REPO_ID),
        ("evander.baseBranch", "main"),
        ("evander.directoryPath", "root"),
    ):
        subprocess.run(
            ["git", f"--git-dir={repo_path}", "config", key, value],
            check=True,
        )

    trigger = GitPushTriggerDefinition(
        id="managed-push",
        display_name="Managed push",
        type="trigger:git:push",
        managed_repo_id=_MANAGED_REPO_ID,
    )
    assert git_push_trigger_matches_repo(trigger, _MANAGED_REPO_ID)
    assert git_push_trigger_matches_repo(
        {
            "managed_repo_id_glob": "mr_012345*",
        },
        str(repo_path),
    )

    provider = GitPushTriggerProvider()
    provider.initialize(trigger, TriggerRuntimeContext())
    event = provider.handle_ref_update(
        repo_path=str(repo_path),
        old_rev="0" * 40,
        new_rev=_COMMIT_SHA,
        ref_name="refs/heads/agents/session-1/topic",
    )

    assert event is not None
    assert event.payload == {
        "managed_repo_id": _MANAGED_REPO_ID,
        "branch_name": "agents/session-1/topic",
        "commit_hash": _COMMIT_SHA,
        "new_commit_hash": _COMMIT_SHA,
        "event_hash": _COMMIT_SHA,
        "old_commit_hash": "0" * 40,
        "ref_name": "refs/heads/agents/session-1/topic",
        "default_branch": "main",
        "session_id": "session-1",
        "review_branch": "review/session-1/topic",
        "notes_ref": agent_pr_notes_ref_for_session("session-1"),
    }
    assert not any("path" in key for key in event.payload)

    runtime_dir = tmp_path / "runtime"
    publish_git_push_event(
        runtime_dir=runtime_dir,
        repo_path=str(repo_path),
        old_rev="0" * 40,
        new_rev=_COMMIT_SHA,
        ref_name="refs/heads/agents/session-1/topic",
    )
    persisted = json.loads(
        (runtime_dir / "trigger-git-push-events.jsonl").read_text(encoding="utf-8")
    )
    assert persisted["managed_repo_id"] == _MANAGED_REPO_ID
    assert not any("path" in key for key in persisted)
