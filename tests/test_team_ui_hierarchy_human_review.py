from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from team_ui_controller_harness import controller_script

from evander_team_addon.providers import (
    TEAM_AGENT_GUIDANCE_VERSION,
    _project_repo_prompt_guidance,
    _task_creation_mandate_guidance,
    _team_agent_guidance_content,
)
from evander_team_addon.provider_prompts import (
    _build_governance_prompt,
    _build_prompt,
    _build_team_chat_prompt,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("mode", ["ordinary", "retained", "chat", "chat_mutations", "governance"])
def test_wake_prompts_request_readable_references_without_reformatting_context(mode: str) -> None:
    agent_id = "team-agent-" + "a" * 32
    agent = {"id": agent_id, "display_name": "Avery", "role_name": "Reviewer"}
    lease = {"agent": agent, "reason": "scheduled"}
    if mode in {"chat", "chat_mutations"}:
        prompt = _build_team_chat_prompt(
            state={"agents": {agent_id: agent}, "conversation_messages": {}},
            conversation={"id": "conversation", "agent_id": agent_id, "operator_user_id": "operator"},
            message={"id": "message", "body": "Summarize the case."},
            mutation_allowed=mode == "chat_mutations",
        )
        assert '`conversation_id="conversation"`' in prompt
        assert '`message_ids=["message"]`' in prompt
    elif mode == "governance":
        prompt = _build_governance_prompt({**lease, "governance_group": {"id": "governance"}})
        assert f'"id": "{agent_id}"' in prompt
    else:
        prompt = _build_prompt(lease, effective_resume_mode="same_context" if mode == "retained" else None)
        assert f'"id":"{agent_id}"' in prompt

    assert prompt.count("Human-facing references:") == 1
    assert "display name and role when available" in prompt
    assert "each exact value alone in Markdown inline backticks" in prompt
    assert "Avoid repeating IDs" in prompt
    assert "structured identifier arguments, URLs, commands and code blocks unchanged" in prompt
    assert f"`{agent_id}`" not in prompt


def test_versioned_agent_guidance_explains_task_topology_and_cancellation() -> None:
    content = _team_agent_guidance_content(
        agent={"id": "implementer", "display_name": "Implementer"}
    )

    assert TEAM_AGENT_GUIDANCE_VERSION == 47
    assert "Every test command must have an explicit timeout" in content
    assert "timeout --preserve-status -k 20s <seconds> <test command>" in content
    assert "Do not retry the unchanged command indefinitely" in content
    assert "parent_task_id means constituent composition" in content
    assert "dependency_task_ids means execution ordering" in content
    assert "originating_task_id records provenance" in content
    assert "ordinary final response is not delivered to the operator" in content
    assert "resolve the callable name from ALL_TOOLS" in content
    assert "tools[replyToolName](...)" in content
    assert "Do not use tools.team_reply_to_chat" in content
    assert "missing first snapshot is not a terminal failure" in content
    assert "do not throw" in content
    assert "finish before the tool call succeeds" in content
    assert "injected into the next model call" in content
    assert "team_acknowledge_operator_chat_messages" in content
    assert "originating_task_id alone does not seed Git content" in content
    assert "every accepted predecessor whose content is required" in content
    assert "silently starting from the project base branch" in content
    assert "prepared worktree HEAD has the same tree as that candidate" in content
    assert "do not choose or merge a different source ad hoc" in content
    assert "model that work as a self-owned child" in content
    assert "the parent stays quiet" in content
    assert "must appear in source_deliverable_ids" in content
    assert "team_cancel_task" in content
    assert "stable reason code and reason" in content
    assert "submits an assessable delivered/evidenced-noop contribution" in content
    assert "team_report_blocker" in content
    assert "team_route_human_finding_correction" in content
    assert "team_propose_human_finding_resolution" in content
    assert "never substitutes for the human decision" in content
    assert "team_close_post_release_case" in content
    assert "does not roll back publication" in content
    assert "human_review_correction_work" in content
    assert "must resolve through accepted deliverables" in content
    assert "submit one assessable correction deliverable" in content
    assert "Do not finish that child directly" in content
    assert "Team derives the finding resolution" in content
    assert "creates the changed successor release automatically" in content
    assert "Do not submit a coordination-parent deliverable" in content
    assert "produces no deliverable" in content
    assert "automatic successors of generated rejected-candidate" in content
    assert "task_preparation_invalidated" in content
    assert "dependency_composition_manifest_recovery_required" in content


def test_task_creation_guidance_keeps_root_authority_distinct_from_provenance() -> None:
    child_only = _task_creation_mandate_guidance(
        {
            "effective_role_permissions": [],
            "project_assignments": [],
        }
    )
    root_authorized = _task_creation_mandate_guidance(
        {
            "effective_role_permissions": ["create_root_task"],
            "project_assignments": [{"kind": "project_lead", "project_id": "project-alpha"}],
        }
    )

    assert "must set parent_task_id to one of your own active tasks" in child_only
    assert "Do not misuse originating_task_id to bypass that root" in child_only
    assert "parent_task_id only for constituent work" in root_authorized
    assert "originating_task_id when that root is correction or follow-up work" in (root_authorized)
    assert "also include the earlier task in dependency_task_ids" in root_authorized
    assert "instead of a base-branch-only worktree" in root_authorized
    assert "generated human-review correction findings" in root_authorized
    assert "instead of creating a replacement root" in root_authorized


def test_project_repo_guidance_emits_exact_task_worktree_mapping() -> None:
    content = _project_repo_prompt_guidance(
        {
            "target_dir": "/home/agent/repo",
            "project_id": "project-alpha",
            "guest_repo_name": "alpha-repo",
            "git_work_contexts": [
                {
                    "task_id": "task-alpha",
                    "worktree_path": "/home/agent/worktrees/task-alpha",
                    "agent_branch": "agents/session-alpha/task-alpha",
                    "branch_context_id": "ctx-task-alpha",
                }
            ],
        }
    )

    assert "Project repository control checkout: /home/agent/repo" in content
    assert "| Task | Project | Repository | Worktree | Branch | Context |" in content
    assert (
        "| task-alpha | project-alpha | alpha-repo | "
        "/home/agent/worktrees/task-alpha | agents/session-alpha/task-alpha | "
        "ctx-task-alpha |"
    ) in content
    assert "Never edit in the control checkout" in content
    assert "reuse another task's worktree, branch, context, or dispatch" in content


def test_task_board_dependency_wait_labels_handle_mixed_and_resolved_tasks() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for Team UI dependency-label tests")
    script = """
      const assert = require("node:assert/strict");
      const terminalTaskStates = new Set(["finished", "accepted", "rejected", "cancelled"]);
      const state = {
        tasks: {
          blocked: { state: "blocked" },
          pending: { state: "pending" },
        },
      };
      const text = (value) => String(value ?? "").trim();
      const escapeHtml = (value) => text(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;");
      const taskBoardReadiness = (task) =>
        text(task?.readiness || task?.assessment?.readiness) || "unknown";
      // CONTROLLER

      const mixed = {
        state: "pending",
        readiness: "waiting_on_dependencies",
        resolution_status: "unresolved",
        dependency_status: "blocked",
        blocked_by_task_ids: ["blocked", "pending"],
      };
      assert.equal(taskBoardWaitsOnDependencies(mixed), true);
      assert.equal(
        renderTaskBoardDependencyWaitBadge(mixed),
        '<span class="state-pill warning">Waiting on 2 dependencies; 1 blocked</span>',
      );

      const cancelled = {
        ...mixed,
        state: "cancelled",
        resolution_status: "resolved",
        dependency_status: "waiting",
      };
      assert.equal(taskBoardWaitsOnDependencies(cancelled), false);
      assert.equal(renderTaskBoardDependencyWaitBadge(cancelled), "");
      assert.equal(
        taskBoardWaitsOnDependencies({ ...mixed, state: "rejected" }),
        false,
      );
      assert.equal(
        taskBoardWaitsOnDependencies({ ...mixed, resolution_status: "resolved" }),
        false,
      );

      const scoped = {
        ...mixed,
        blocked_by_task_ids: ["unavailable", "pending"],
      };
      assert.equal(
        renderTaskBoardDependencyWaitBadge(scoped),
        '<span class="state-pill warning">Waiting on 2 dependencies; at least one blocked</span>',
      );
    """

    script = controller_script(
        "board",
        script,
        "{app: {state}}",
        ["taskBoardWaitsOnDependencies", "renderTaskBoardDependencyWaitBadge"],
    )
    result = subprocess.run(
        [node, "--input-type=commonjs"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr


def test_live_release_verification_retries_after_transient_request_failure() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for Team UI polling tests")
    script = """
      const assert = require("node:assert/strict");
      const text = (value) => String(value ?? "").trim();
      const API_BASE = "/api/team";
      const RELEASE_VERIFICATION_LIVE_POLL_MS = 2000;
      const state = {
        release_packages: {
          "release-1": {
            id: "release-1",
            verification_runs: [{ id: "run-1", state: "running" }],
          },
        },
      };
      const scheduled = [];
      const window = {
        setTimeout: (callback, delay) => {
          scheduled.push({ callback, delay });
          return scheduled.length;
        },
        clearTimeout: () => {},
      };
      const mergeReleaseDetailWithCurrentState = (release) => release;
      const renderReleaseDetailSurfaces = () => {};
      const responseText = async () => "temporary gateway failure";
      let requestCount = 0;
      const teamFetch = async () => {
        requestCount += 1;
        if (requestCount === 1) {
          return { ok: false, text: async () => "temporary gateway failure" };
        }
        return {
          ok: true,
          json: async () => ({ run: { state: "passed" } }),
        };
      };
      // CONTROLLER
      controller.expandReleaseDetail("release-1");

      (async () => {
        await loadReleaseVerificationLive("release-1");
        assert.equal(requestCount, 1);
        assert.equal(scheduled.length, 1);
        assert.equal(scheduled[0].delay, RELEASE_VERIFICATION_LIVE_POLL_MS);
        assert.equal(
          controller.cachedVerificationLive("release-1").error,
          "temporary gateway failure",
        );

        scheduled.shift().callback();
        await new Promise((resolve) => setImmediate(resolve));

        assert.equal(requestCount, 2);
        assert.equal(controller.cachedVerificationLive("release-1").error, null);
        assert.equal(
          controller.cachedVerificationLive("release-1").payload.run.state,
          "passed",
        );
        assert.equal(scheduled.length, 0);
      })().catch((error) => {
        console.error(error);
        process.exitCode = 1;
      });
    """

    script = controller_script(
        "releases",
        script,
        "{app: {state}, projects: {renderProjectFocus: () => {}}, delivery: {renderReviewDeliveryWorkspace: () => {}}, access: {teamFetch}}",
        ["loadReleaseVerificationLive"],
    )
    result = subprocess.run(
        [node, "--input-type=commonjs"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr


def test_rejected_review_exception_action_is_hidden_after_successor_exists() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is unavailable")
    script = """
      const assert = require("node:assert/strict");
      const state = { release_packages: {} };
      const text = (value) => value == null ? "" : String(value);
      const values = (record) => Object.values(record || {});
      const escapeAttr = text;
      const escapeHtml = text;
      // CONTROLLER

      const predecessor = {
        id: "release-predecessor",
        state: "rejected",
        gate_failure: { stage: "review", kind: "rejected", source_id: "review-1" },
      };
      assert.match(renderReleaseGateAction(predecessor), /Retry rejected review by exception/);
      state.release_packages.successor = {
        id: "release-successor",
        supersedes_release_package_id: predecessor.id,
      };
      assert.equal(renderReleaseGateAction(predecessor), "");
    """

    script = controller_script("releases", script, "{app: {state}}", ["renderReleaseGateAction"])
    result = subprocess.run(
        [node, "--input-type=commonjs"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
