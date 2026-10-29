from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path

import pytest

from evander.artifact_spaces import ArtifactSpaceStore
from evander.effects.providers.base import EffectRuntimeContext
from evander.published_artifacts import PublishedArtifactStore
from evander_team_addon import provider_artifacts
from evander_team_addon.models import (
    AgentInboxItem,
    GitWorkContext,
    GitWorkContextState,
    TaskDeliverableContract,
    TeamBlocker,
    TeamBlockerRoute,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamState,
    TeamTaskState,
)
from evander_team_addon.services.domain.task_assessment import (
    TaskAssessmentBoundaryError,
    require_deliverable_submission,
)
from evander_team_addon.sqlite_store import TeamStateSqliteCommandTransaction
from team_repository_support import (
    _command_service,
    _lease_agent_dispatch,
    _seed_hierarchy,
    _seed_task,
)


def _blocked_evidence_request(tmp_path: Path, *, owner: str = "backend"):
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task", owner_agent_id=owner)

    def configure(state: TeamState) -> TeamState:
        state.tasks["task"].state = TeamTaskState.BLOCKED
        state.tasks["task"].deliverable_contract = TaskDeliverableContract(
            require_git_provenance=True
        )
        state.blockers["test-failure"] = TeamBlocker(
            id="test-failure",
            team_id="team",
            project_id="project",
            task_id="task",
            source_kind=TeamBlockerSourceKind.TASK,
            source_id="task",
            reason_code="verification_failed",
            summary="Tests failed",
            owner_agent_id="manager",
            route=TeamBlockerRoute.OWNED,
        )
        state.git_work_contexts["retained"] = GitWorkContext(
            id="retained",
            agent_id=owner,
            task_id="task",
            agent_branch="agents/session-backend/task",
            state=GitWorkContextState.ABANDONED,
        )
        state.inbox["evidence-request"] = AgentInboxItem(
            id="evidence-request",
            recipient_agent_id="backend",
            sender_agent_id="lead",
            task_id="task",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            response_requested=True,
            payload={"question": "Share the retained failure logs."},
        )
        return state

    repo.update(configure)
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="evidence-dispatch")
    assert repo.read().dispatches["evidence-dispatch"].task_ids == []
    return repo


def _submit(repo, publisher, **overrides):
    arguments = {
        "session_id": "session-backend",
        "task_id": "task",
        "summary": "Retained failed verification, awaiting product correction.",
        "request_id": "retained-failure-1",
        "evidence_only": True,
        "artifact_paths": ["results/tests.log"],
        "verification": {"status": "failed"},
        "artifact_publisher": publisher,
    }
    arguments.update(overrides)
    return _command_service(repo).submit_deliverable_command(**arguments)


@pytest.fixture
def retained_publisher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    artifacts = tmp_path / "agent-artifacts"
    (artifacts / "results").mkdir(parents=True)
    log = artifacts / "results/tests.log"
    log.write_text("Tests: 7 passed, 2 failed, 0 skipped.\n")
    publications = []

    class ExportRetainedFiles:
        def initialize(self, definition, context):
            self.definition, self.context = definition, context

        def execute(self):
            publications.append(self.definition.key)
            archive = tmp_path / "export.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                for relative in self.definition.source_paths:
                    tar.add(artifacts / relative, arcname=relative)
            metadata = PublishedArtifactStore(tmp_path).publish_archive(
                key=self.definition.key,
                session_id=self.definition.session_id,
                archive_path=archive,
                source_file_count=1,
                source_byte_count=log.stat().st_size,
            )
            return metadata.model_dump(mode="json")

    monkeypatch.setattr(
        provider_artifacts, "ArtifactPublishArtifactsEffectProvider", ExportRetainedFiles
    )

    def publish(**kwargs):
        return provider_artifacts._publish_deliverable_artifact_bundle(
            EffectRuntimeContext(provider_config={"runtime_dir": str(tmp_path)}),
            agent=kwargs["agent"].model_dump(mode="json"),
            team_id=kwargs["team_id"],
            project_id=kwargs["project_id"],
            deliverable_id=kwargs["deliverable_id"],
            dispatch_id=kwargs["dispatch_id"],
            source_paths=kwargs["source_paths"],
            evidence_metadata=kwargs["evidence_metadata"],
        )

    return publish, publications, log


def test_blocked_handoff_publishes_inspectable_evidence_without_product_transition(
    tmp_path: Path,
    retained_publisher,
) -> None:
    repo = _blocked_evidence_request(tmp_path)
    publish, publications, log = retained_publisher
    before = repo.read()
    git_refs = {"branch_context_id": "retained", "commit_sha": "a" * 40}
    state, result = _submit(
        repo, publish, git_refs=git_refs, handled_inbox_item_ids=["evidence-request"]
    )
    assert result["success"] is True, result
    assert result["evidence_only"] is True
    assert result["deliverable_id"] is None
    assert state.tasks["task"] == before.tasks["task"]
    assert state.blockers["test-failure"] == before.blockers["test-failure"]
    assert state.blockers["test-failure"].state == TeamBlockerState.OPEN
    assert state.git_work_contexts == before.git_work_contexts
    assert state.deliverables == state.review_requests == state.release_packages == {}
    assert state.inbox["evidence-request"].state == TeamInboxItemState.HANDLED
    assert len(result["created_inbox_item_ids"]) == 1
    notice = state.inbox[result["created_inbox_item_ids"][0]]
    assert notice.recipient_agent_id == "lead"
    assert notice.item_type == TeamInboxItemType.PROGRESS_REPORT
    assert notice.response_requested is False
    assert notice.payload["kind"] == "task_evidence_published"
    assert "available_decisions" not in notice.payload
    record = result["published_evidence_artifacts"][0]
    stored = PublishedArtifactStore(tmp_path).get(record["artifact_key"])
    assert (
        record["publish_result"]["sha256"]
        == hashlib.sha256(stored.blob_path.read_bytes()).hexdigest()
    )
    with tarfile.open(stored.blob_path) as tar:
        assert tar.extractfile("results/tests.log").read() == log.read_bytes()
    space_state = ArtifactSpaceStore(tmp_path).read()
    version = space_state.latest_versions_for_space(record["artifact_space_path"])[0]
    assert version.id == record["artifact_space_version_id"]
    assert version.metadata["verification"]["status"] == "failed"
    assert version.metadata["reported_git_refs"]["commit_sha"] == "a" * 40
    assert version.metadata["git_provenance"] == "agent_reported"
    assert version.metadata["task_id"] == "task"
    assert "deliverable_id" not in version.metadata
    provider_artifacts._require_team_artifact_space_read(
        space_state,
        artifact_space_path=record["artifact_space_path"],
        team_id="team",
        subject="team-agent:lead",
    )
    assert len(repo.list_outbox_records(intent_type="team.agent.wake_requested")) == 1
    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="after-context-rotation")
    _, replay = _submit(
        repo, publish, git_refs=git_refs, handled_inbox_item_ids=["evidence-request"]
    )
    assert replay == result
    assert len(publications) == 1
    assert len(ArtifactSpaceStore(tmp_path).read().versions) == 1
    assert len(repo.read().inbox) == 2
    _, conflict = _submit(
        repo,
        publish,
        git_refs=git_refs,
        handled_inbox_item_ids=["evidence-request"],
        summary="A different payload must not reuse this request identity.",
    )
    assert conflict["errors"][0]["code"] == "idempotency_conflict"
    assert len(publications) == 1
    with pytest.raises(TaskAssessmentBoundaryError) as raised:
        require_deliverable_submission(repo.read(), task_id="task", source_deliverable_ids=[])
    assert raised.value.code == "task_boundary_blocked"


def test_evidence_handoff_recovers_archive_after_team_transaction_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retained_publisher,
) -> None:
    repo = _blocked_evidence_request(tmp_path)
    publish, publications, _log = retained_publisher

    def fail_outbox(self, record):
        raise OSError("Team transaction interrupted after artifact publication")

    with monkeypatch.context() as scoped:
        scoped.setattr(TeamStateSqliteCommandTransaction, "enqueue_outbox_record", fail_outbox)
        with pytest.raises(OSError, match="interrupted after artifact publication"):
            _submit(repo, publish, handled_inbox_item_ids=["evidence-request"])
    assert repo.read().inbox["evidence-request"].state == TeamInboxItemState.PENDING
    assert len(repo.read().inbox) == 1
    assert repo.list_outbox_records() == []
    assert len(ArtifactSpaceStore(tmp_path).read().versions) == 1

    _lease_agent_dispatch(repo, agent_id="backend", dispatch_id="after-crash")
    state, result = _submit(repo, publish, handled_inbox_item_ids=["evidence-request"])
    assert result["success"] is True, result
    assert result["published_evidence_artifacts"][0]["publish_result"]["reused"] is True
    assert len(publications) == 1
    assert len(ArtifactSpaceStore(tmp_path).read().versions) == 1
    assert len(repo.list_outbox_records(intent_type="team.agent.wake_requested")) == 1
    assert state.tasks["task"].state == TeamTaskState.BLOCKED
    assert state.blockers["test-failure"].state == TeamBlockerState.OPEN


def test_scoped_blocked_handoff_still_requires_normal_deliverable_readiness(tmp_path: Path) -> None:
    repo = _blocked_evidence_request(tmp_path)

    def authorize_owner_task(state):
        state.dispatches["evidence-dispatch"].task_ids = ["task"]
        return state

    repo.update(authorize_owner_task)
    calls = []
    _, result = _submit(repo, lambda **kwargs: calls.append(kwargs), evidence_only=False)
    assert result["success"] is False
    assert result["errors"][0]["code"] == "task_boundary_blocked"
    assert calls == []


@pytest.mark.parametrize("status", ["partial", "not_run", "unknown"])
def test_evidence_handoff_accepts_incomplete_verification(tmp_path: Path, status: str) -> None:
    repo = _blocked_evidence_request(tmp_path)
    _, result = _submit(
        repo,
        lambda **_: {"artifact_ref": "artifact-space:/teams/team/evidence/logs"},
        verification={"status": status},
    )
    assert result["success"] is True, result
    assert repo.read().tasks["task"].state == TeamTaskState.BLOCKED


@pytest.mark.parametrize(
    "change",
    [
        "foreign_owner",
        "unselected_inbox",
        "different_task",
        "handled_request",
        "wrong_type",
        "missing_project",
    ],
)
def test_evidence_handoff_requires_exact_owned_dispatch_authority(
    tmp_path: Path, change: str
) -> None:
    repo = _blocked_evidence_request(
        tmp_path, owner="worker" if change == "foreign_owner" else "backend"
    )

    def change_scope(state):
        if change == "unselected_inbox":
            state.dispatches["evidence-dispatch"].inbox_item_ids = []
        elif change == "different_task":
            state.inbox["evidence-request"].task_id = None
        elif change == "handled_request":
            state.inbox["evidence-request"].state = TeamInboxItemState.HANDLED
        elif change == "wrong_type":
            state.inbox["evidence-request"].item_type = TeamInboxItemType.PROGRESS_REPORT
        elif change == "missing_project":
            state.dispatches["evidence-dispatch"].project_ids = []
        return state

    repo.update(change_scope)
    calls = []
    state, result = _submit(repo, lambda **kwargs: calls.append(kwargs))
    assert result["success"] is False
    assert result["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert calls == []
    assert state.deliverables == {}


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"verification": {"status": "passed"}}, "evidence_verification_required"),
        ({"verification": None}, "evidence_verification_required"),
        ({"artifact_paths": []}, "evidence_artifact_paths_required"),
        ({"artifact_paths": ["../outside.log"]}, "unsafe_artifact_path"),
        ({"supersedes_deliverable_id": "old"}, "evidence_product_fields_forbidden"),
        ({"branch_context_id": "another-task"}, "evidence_git_context_forbidden"),
    ],
)
def test_invalid_evidence_handoff_fails_before_publication(
    tmp_path: Path, overrides, code: str
) -> None:
    repo = _blocked_evidence_request(tmp_path)
    calls = []
    _, result = _submit(repo, lambda **kwargs: calls.append(kwargs), **overrides)
    assert result["success"] is False
    assert result["errors"][0]["code"] == code
    assert calls == []


def test_evidence_authority_does_not_authorize_deliverable_or_task_mutation(tmp_path: Path) -> None:
    repo = _blocked_evidence_request(tmp_path)
    calls = []
    _, result = _submit(repo, lambda **kwargs: calls.append(kwargs), evidence_only=False)
    assert result["errors"][0]["code"] == "dispatch_scope_forbidden"
    _, result = _command_service(repo).update_task_command(
        session_id="session-backend", task_id="task", state="finished", request_id="finish-task"
    )
    assert result["success"] is False
    assert result["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert calls == []


def test_evidence_export_failure_leaves_task_and_request_untouched(tmp_path: Path) -> None:
    repo = _blocked_evidence_request(tmp_path)
    before = repo.read()

    def fail(**kwargs):
        raise RuntimeError("retained file unavailable")

    state, result = _submit(repo, fail, handled_inbox_item_ids=["evidence-request"])
    assert result["success"] is False
    assert result["errors"][0]["code"] == "artifact_publication_failed"
    assert state.revision == before.revision
    assert state.inbox == before.inbox
    assert state.tasks == before.tasks
    assert repo.list_outbox_records() == []
