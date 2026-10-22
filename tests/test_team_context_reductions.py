from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from evander.gateway import create_server
from evander_team_addon import mcp as team_mcp
from evander_team_addon.models import (
    GitWorkContext,
    TeamBlocker,
    TeamDispatch,
    TeamDispatchStatus,
    TeamTaskState,
)
from evander_team_addon.providers import _build_prompt, _team_agent_guidance_content
from evander_team_addon.services.read_service import TeamReadService
from evander_team_addon.services.runtime_service import TeamRuntimeService
from evander_team_addon.services.wake_prompt_context import (
    MAX_SESSION_HEALTH,
    RECORD_READS,
    build_compact_wake_context,
    compact_context_json,
    work_lineage_resume_thread_id,
)
from test_team_addon_mcp_read_tools import (
    EXPECTED_READ_TOOL_NAMES,
    _call_tool,
    _item,
    _runtime,
    _seed_read_tool_state,
    _upsert_session,
)
from test_team_external_source_evidence import _create_snapshot, _seed_repository
from test_team_work_lineage_runtime_regressions import (
    NOW,
    _create_task,
    _seed_repo,
    _set_work_lineage_policy,
)


def _overflow_lease() -> dict:
    lease = {
        "agent": {
            "id": "manager",
            "role_description": "Supervise every current obligation.",
            "effective_role_permissions": ["create_root_task"],
        },
        "reason": "scheduled",
        "operator_chat_participants": [],
        "dispatch": {"id": "dispatch", "agent_id": "manager", "work_lineage_id": "family"},
    }
    for key, ids_key in (
        ("tasks", "task_ids"),
        ("inbox", "inbox_item_ids"),
        ("reviews", "review_request_ids"),
        ("blockers", "blocker_ids"),
        ("release_packages", None),
    ):
        rows = []
        for index in range(24):
            row = {
                "id": f"{key}-{index:02d}",
                "state": "pending",
                "priority": "normal",
                "title": f"Work {index}",
                "task_id": f"tasks-{index:02d}",
            }
            if key == "tasks":
                row.update(
                    description=f"Implement requirement {index} and preserve review gates.",
                    owner_agent_id="manager",
                    dependency_status="satisfied",
                )
            if key == "inbox":
                row.update(
                    item_type="question",
                    response_requested=True,
                    payload={"question": f"Respond to question {index}."},
                )
            if key == "reviews":
                row.update(
                    deliverable_id=f"deliverable-{index}",
                    reviewer_agent_id="manager",
                    instructions=f"Independently verify requirement {index}.",
                    candidate_commit_sha=f"{index:040x}",
                    repository_snapshot={
                        "managed_repo_id": "repo", "commit_sha": f"{index:040x}",
                        "pinned_ref": f"refs/reviews/{index}",
                    },
                )
            if key == "blockers":
                row.update(
                    state="open",
                    owner_agent_id="manager",
                    summary=f"Resolve failure {index}.",
                    follow_up_at="2026-09-09T12:00:00Z",
                    route="owner",
                )
            if key == "release_packages":
                row.update(
                    state="blocked",
                    next_action="retry_release_gate",
                    candidate_commit_sha=f"{index:040x}",
                    approved_commit_sha=f"{index + 100:040x}",
                )
            rows.append(row)
        lease[key] = rows
        if ids_key:
            lease["dispatch"][ids_key] = [row["id"] for row in rows]
    return lease


@pytest.mark.parametrize("fresh", [True, False])
def test_every_wake_recovers_all_overflow_obligations_without_prior_messages(fresh: bool) -> None:
    lease = _overflow_lease()
    context = build_compact_wake_context(lease, fresh_context=fresh)
    assert context["agent"]["role_description"] == lease["agent"]["role_description"]
    for collection in ("tasks", "reviews", "inbox", "blockers", "release_packages"):
        included = context[collection]
        if isinstance(included, dict):
            included = list(included.values())
        overflow = context["overflow"][collection]
        assert {row["id"] for row in included + overflow} == {
            row["id"] for row in lease[collection]
        }
        assert context["retrieval"][collection]["count"] == 24
        assert context["retrieval"][collection]["tool"] == RECORD_READS[collection]["tool"]
        assert context["omitted_counts"][collection] == len(overflow)
        assert all(row["read_required"] for row in overflow)
    assert all(row["response_requested"] for row in context["overflow"]["inbox"])
    assert all(
        row["next_action"] == "retry_release_gate"
        for row in context["overflow"]["release_packages"]
    )
    assert all(
        len(row["approved_commit_sha"]) == 40 for row in context["overflow"]["release_packages"]
    )
    for review in context["overflow"]["reviews"]:
        snapshot = context["review_repository_snapshots"][review["repository_snapshot_ref"]]
        assert snapshot["commit_sha"] == review["candidate_commit_sha"]
    # Simulated compaction: the new wake must be sufficient even if no old
    # messages, history fingerprints, or previous prompt data are retained.
    fresh_payload = build_compact_wake_context(lease, fresh_context=True)
    assert {k: v for k, v in context.items() if k != "context_mode"} == {
        k: v for k, v in fresh_payload.items() if k != "context_mode"
    }


def test_urgent_directives_are_complete_even_when_the_list_overflows() -> None:
    lease = _overflow_lease()
    for row in lease["inbox"]:
        row.update(
            item_type="operator_directive",
            priority="high",
            payload={"message": "Urgent requirement. " * 400, "steps": ["first", "last"]},
        )
    context = build_compact_wake_context(lease, fresh_context=False)
    assert len(context["inbox"]) == 24
    assert all(row["payload"] == lease["inbox"][0]["payload"] for row in context["inbox"])
    assert context["operator_chat_participants"] == []
    assert context["operator_chat_participants_replaces_prior"] is True
    prompt = _build_prompt(lease, effective_resume_mode="same_context")
    assert "an empty list means no proactive Team Chat recipient is authorized" in prompt
    guidance = _team_agent_guidance_content(agent=lease["agent"])
    assert "Do not mark a multi-step directive handled before its final required action" in guidance


@pytest.mark.parametrize("fresh", [True, False])
@pytest.mark.parametrize(
    "health",
    [
        {"agent_execution_status": "idle", "container_lifecycle_state": "exited"},
        {"agent_execution_status": "failed", "container_lifecycle_state": "running"},
        {"session_present": False, "status": "missing"},
        {"is_expired": True, "status": "expired"},
    ],
)
def test_overflow_session_health_keeps_actionable_details_without_an_unusable_read(fresh, health):
    rows = [
        {
            "agent_id": f"agent-{index:02d}",
            "session_id": f"session-{index:02d}",
            "session_present": True,
            "status": "active",
            "is_expired": False,
            "expires_at": "2026-09-10T12:00:00Z",
            "execution_backend": "container_worker_vm",
            "agent_execution_status": "idle",
            "container_lifecycle_state": "running",
            "agent_active_execution_count": 0,
            "agent_latest_execution_id": f"execution-{index:02d}",
            **health,
        }
        for index in range(MAX_SESSION_HEALTH + 3)
    ]
    context = build_compact_wake_context({"session_health": rows}, fresh_context=fresh)
    assert len(context["session_health"]) == MAX_SESSION_HEALTH
    overflow = context["overflow"]["session_health"]
    assert len(overflow) == context["omitted_counts"]["session_health"] == 3
    assert context["session_health_summary"]["attention_required_count"] == len(rows)
    by_agent = {row["agent_id"]: row for row in rows}
    for row in context["session_health"] + overflow:
        source = by_agent[row["agent"]]
        for field in (
            "session_id", "session_present", "status", "is_expired", "expires_at",
            "execution_backend", "agent_execution_status", "container_lifecycle_state",
            "agent_active_execution_count", "agent_latest_execution_id",
        ):
            assert row[field] == source[field]
        assert not row.get("read_required")
    assert "session_health" not in context.get("retrieval", {})


def test_exact_duplicates_share_bindings_and_changed_versions_remain_distinct() -> None:
    binding = {
        "id": "binding",
        "task_id": "task",
        "dispatch_id": "dispatch",
        "base_commit": "1" * 40,
        "commit_sha": "2" * 40,
        "worktree_path": "/worktrees/task",
        "state": "active",
    }
    snapshot = {"managed_repo_id": "repo", "commit_sha": "3" * 40, "pinned_ref": "refs/review/one"}
    repo = {
        "project_id": "project",
        "managed_repo_id": "repo",
        "target_dir": "/control",
        "git_work_contexts": [binding],
    }
    evidence = {"id": "evidence-a", "external_version": "1", "content_digest": "sha256:" + "a" * 64}
    lease = {
        "agent": {"id": "worker"},
        "project_repo": repo,
        "project_repos": [repo, deepcopy(repo)],
        "reviews": [
            {
                "id": "review",
                "repository_snapshot": snapshot,
                "instructions": "Review all criteria.",
            }
        ],
        "external_source_evidence_snapshots": [
            evidence,
            deepcopy(evidence),
            {**evidence, "id": "evidence-b", "content_digest": "sha256:" + "b" * 64},
            {**evidence, "id": "evidence-c", "external_version": "2"},
        ],
    }
    lease["project_repos"].append(
        {**repo, "git_work_contexts": [{**binding, "base_commit": "4" * 40}]}
    )
    lease["project_repos"][0]["review_repository_snapshots"] = [
        {**snapshot, "review_request_id": "review"}
    ]
    context = build_compact_wake_context(lease)
    assert len(context["git_work_contexts"]) == 2
    assert len(context["review_repository_snapshots"]) == 1
    assert len(context["external_source_evidence_snapshots"]) == 3
    assert context["reviews"][0]["instructions"] == "Review all criteria."
    permuted = {
        **lease,
        "project_repos": list(reversed(lease["project_repos"])),
        "external_source_evidence_snapshots": list(
            reversed(lease["external_source_evidence_snapshots"])
        ),
    }
    assert compact_context_json(context) == compact_context_json(
        build_compact_wake_context(permuted)
    )


@pytest.mark.parametrize("detail", ["summary", "full"])
def test_dispatch_reads_preserve_authorized_scope_and_never_select_newer_git(
    tmp_path: Path, detail: str
) -> None:
    runtime = _runtime(tmp_path, allowed_tools=list(EXPECTED_READ_TOOL_NAMES))
    repo = _seed_read_tool_state(runtime.config.runtime_dir)

    def activate(state):
        dispatch = TeamDispatch(
            id="frozen",
            agent_id="alpha-dev",
            session_id="session-alpha-dev",
            cause_kind="scheduled",
            task_ids=["alpha-task-a"],
            project_ids=["alpha-project"],
        )
        state.dispatches[dispatch.id] = dispatch
        state.agent_runtimes["alpha-dev"].active_dispatch_id = dispatch.id
        for name, dispatch_id, sha in (
            ("frozen-binding", "frozen", "a" * 40),
            ("newer-binding", "newer", "b" * 40),
        ):
            state.git_work_contexts[name] = GitWorkContext(
                id=name,
                agent_id="alpha-dev",
                task_id="alpha-task-a",
                dispatch_id=dispatch_id,
                agent_branch=f"agents/{name}",
                base_commit=sha,
                commit_sha=sha,
                worktree_path=f"/worktrees/{name}",
            )
        state.tasks["alpha-task-a"].description = "Complete specification. " * 100
        return state

    repo.update(activate)
    server = create_server(runtime)
    record = _item(
        _call_tool(
            server,
            team_mcp.TEAM_GET_CURRENT_DISPATCH_CONTEXT_TOOL,
            {"session_id": "session-alpha-dev", "detail": detail},
        )
    )
    assert record["dispatch"]["task_ids"] == ["alpha-task-a"]
    assert [row["id"] for row in record["git_work_contexts"]] == ["frozen-binding"]
    assert record["git_work_contexts"][0]["base_commit"] == "a" * 40
    assert "newer-binding" not in str(record)
    assert "beta-task" not in str(record)
    assert [row["id"] for row in record["tasks"]] == ["alpha-task-a"]
    full = _item(
        _call_tool(
            server,
            team_mcp.TEAM_GET_CURRENT_DISPATCH_CONTEXT_TOOL,
            {"session_id": "session-alpha-dev"},
        )
    )
    if detail == "full":
        assert record == full
    else:
        task = _item(
            _call_tool(
                server,
                team_mcp.TEAM_GET_TASK_TOOL,
                {"session_id": "session-alpha-dev", "task_id": "alpha-task-a"},
            )
        )
        assert task["description"] == full["tasks"][0]["description"]
        assert len(record["tasks"][0]["description_preview"]) <= 480
        assert "git_work_contexts" not in record["tasks"][0]
        assert record["tasks"][0]["git_work_context_ids"] == ["frozen-binding"]
    with pytest.raises(ToolError):
        _call_tool(
            server,
            team_mcp.TEAM_GET_CURRENT_DISPATCH_CONTEXT_TOOL,
            {"session_id": "session-alpha-dev", "detail": "invalid"},
        )


def test_individual_evidence_read_is_complete_and_bound_to_active_dispatch(tmp_path: Path) -> None:
    repo = _seed_repository(tmp_path)
    _state, snapshot = _create_snapshot(
        repo, snapshot_id="capture", body_content="Detailed source. " * 600
    )
    _create_snapshot(repo, snapshot_id="not-attached", attach=False, external_version="8")
    _, leases = TeamRuntimeService(repo).lease_due_agents(
        agent_id="worker", now=NOW, respect_wake_interval=False
    )
    state = repo.read()
    reader = TeamReadService(state=state, actor=state.agents["worker"])
    assert leases[0]["dispatch"]["source_evidence_snapshot_ids"] == [snapshot.id]
    full = reader.get_source_evidence_snapshot(snapshot.id)
    assert full is not None and full["body"]["content"] == snapshot.body.content
    assert reader.get_source_evidence_snapshot("not-attached") is None
    assert reader.get_current_dispatch_context(detail="summary")[
        "external_source_evidence_snapshots"
    ][0]["read_required"]
    state.agent_runtimes["worker"].active_dispatch_id = None
    assert reader.get_source_evidence_snapshot(snapshot.id) is None


def test_retrieval_references_resolve_to_registered_tools_and_evidence_scope(
    tmp_path: Path,
) -> None:
    allowed = [*EXPECTED_READ_TOOL_NAMES, team_mcp.TEAM_GET_SOURCE_EVIDENCE_SNAPSHOT_TOOL]
    runtime = _runtime(tmp_path, allowed_tools=allowed)
    repo = _seed_repository(runtime.config.runtime_dir)
    _upsert_session(runtime, session_id="session-worker", allowed_tools=allowed)
    _, snapshot = _create_snapshot(repo, snapshot_id="capture")
    _create_snapshot(repo, snapshot_id="outside", attach=False, external_version="8")
    TeamRuntimeService(repo).lease_due_agents(
        agent_id="worker", now=NOW, respect_wake_interval=False
    )
    server = create_server(runtime)
    for reference in RECORD_READS.values():
        tool = server._tool_manager.get_tool(reference["tool"])
        assert tool is not None
        if "id_parameter" in reference:
            assert reference["id_parameter"] in tool.parameters["properties"]
    record = _item(
        _call_tool(
            server,
            team_mcp.TEAM_GET_SOURCE_EVIDENCE_SNAPSHOT_TOOL,
            {"session_id": "session-worker", "snapshot_id": snapshot.id},
        )
    )
    assert record["acceptance_criteria"][0]["external_id"] == "criterion-1"
    result = _call_tool(
        server,
        team_mcp.TEAM_GET_SOURCE_EVIDENCE_SNAPSHOT_TOOL,
        {"session_id": "session-worker", "snapshot_id": "outside"},
    )
    assert result["success"] is False
    _upsert_session(runtime, session_id="session-worker", allowed_tools=[])
    with pytest.raises(ToolError):
        _call_tool(
            server,
            team_mcp.TEAM_GET_SOURCE_EVIDENCE_SNAPSHOT_TOOL,
            {"session_id": "session-worker", "snapshot_id": snapshot.id},
        )


@pytest.mark.parametrize("agent_id,display_name", [("manager", "Evander"), ("lead", "Trudy")])
def test_management_lineage_switching_keeps_current_overview_without_expanding_scope(
    tmp_path: Path,
    agent_id: str,
    display_name: str,
) -> None:
    repo = _seed_repo(tmp_path)
    _create_task(repo, task_id="family-a", owner_agent_id=agent_id, priority=1)
    _create_task(repo, task_id="family-b", owner_agent_id=agent_id, priority=2)
    _set_work_lineage_policy(repo, agent_id)
    repo.update(lambda state: _name_agent(state, agent_id, display_name))
    runtime = TeamRuntimeService(repo)
    _, first_leases = runtime.lease_due_agents(
        agent_id=agent_id, now=NOW, respect_wake_interval=False, defer_activation=True
    )
    first = first_leases[0]
    assert first["dispatch"]["task_ids"] == ["family-a"]
    runtime.activate_delivered_agent_dispatch(
        dispatch_id=first["dispatch_id"], thread_id="thread-a", now=NOW
    )

    def finish(state):
        state.tasks["family-a"].state = TeamTaskState.FINISHED
        dispatch = state.dispatches[first["dispatch_id"]]
        dispatch.status = TeamDispatchStatus.COMPLETED
        dispatch.completed_at = NOW
        current = state.agent_runtimes[agent_id]
        current.active_dispatch_id = None
        current.wake_leased_until = None
        current.next_wake_at = None
        return state

    repo.update(finish)
    _, second_leases = runtime.lease_due_agents(
        agent_id=agent_id, now=NOW + timedelta(minutes=1), respect_wake_interval=False
    )
    second = second_leases[0]
    assert second["dispatch"]["task_ids"] == ["family-b"]
    assert second["dispatch"]["work_lineage_id"] != first["dispatch"]["work_lineage_id"]
    assert work_lineage_resume_thread_id(second) is None
    wake = build_compact_wake_context(second)
    overview = wake["supervisory_overview"]
    assert overview["read_only"] is True
    assert overview["projects"][0]["task_counts"] == {"finished": 1, "pending": 1}
    assert overview["retrieval"]["tasks"]["tool"] == "team_list_tasks"
    assert [row["id"] for row in wake["tasks"]] == ["family-b"]
    assert "backend" not in repo.read().agent_runtimes


def test_summary_retrieval_recovers_each_complete_scoped_record(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, allowed_tools=list(EXPECTED_READ_TOOL_NAMES))
    repo = _seed_read_tool_state(runtime.config.runtime_dir)

    def activate(state):
        state.blockers["alpha-blocker"] = TeamBlocker(
            id="alpha-blocker",
            team_id="alpha",
            project_id="alpha-project",
            task_id="alpha-task-a",
            source_kind="task",
            source_id="alpha-task-a",
            owner_agent_id="alpha-dev",
            summary="Resolve the dependency failure.",
            details="The complete failure diagnostic. " * 80,
        )
        state.dispatches["frozen"] = TeamDispatch(
            id="frozen",
            agent_id="alpha-dev",
            session_id="session-alpha-dev",
            cause_kind="scheduled",
            task_ids=["alpha-task-a"],
            inbox_item_ids=["alpha-inbox"],
            blocker_ids=["alpha-blocker"],
            review_request_ids=["alpha-review"],
            project_ids=["alpha-project"],
        )
        state.agent_runtimes["alpha-dev"].active_dispatch_id = "frozen"
        return state

    repo.update(activate)
    server = create_server(runtime)
    args = {"session_id": "session-alpha-dev"}
    summary = _item(
        _call_tool(
            server, team_mcp.TEAM_GET_CURRENT_DISPATCH_CONTEXT_TOOL, {**args, "detail": "summary"}
        )
    )
    full = _item(_call_tool(server, team_mcp.TEAM_GET_CURRENT_DISPATCH_CONTEXT_TOOL, args))
    for collection in ("tasks", "inbox", "reviews", "blockers", "projects"):
        reference = summary["retrieval"][collection]
        assert reference["count"] == 1
        record = _item(
            _call_tool(
                server,
                reference["tool"],
                {**args, reference["id_parameter"]: summary[collection][0]["id"]},
            )
        )
        if collection == "tasks":
            # General task reads retain their existing visible-context contract.
            # They must not replace the immutable dispatch's (empty here) bindings.
            assert [row["id"] for row in record.pop("git_work_contexts")] == ["alpha-context"]
            assert summary["git_work_contexts"] == full["git_work_contexts"] == []
        assert record == full[collection][0]
    state = repo.read()
    state.agent_runtimes["alpha-dev"].active_dispatch_id = None
    reader = TeamReadService(state=state, actor=state.agents["alpha-dev"])
    assert reader.get_current_dispatch_context(detail="summary") is None
    assert reader.get_current_dispatch_context(detail="full") is None


def _name_agent(state, agent_id, display_name):
    state.agents[agent_id].display_name = display_name
    return state
