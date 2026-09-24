from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from evander_team_addon.providers import (
    TEAM_AGENT_GUIDANCE_VERSION,
    _project_repo_prompt_guidance,
    _task_creation_mandate_guidance,
    _team_agent_guidance_content,
)

ROOT = Path(__file__).resolve().parents[1]


def test_versioned_agent_guidance_explains_task_topology_and_cancellation() -> None:
    content = _team_agent_guidance_content(
        agent={"id": "implementer", "display_name": "Implementer"}
    )

    assert TEAM_AGENT_GUIDANCE_VERSION == 45
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
    assert "run git push-agent and require it to succeed" in content
    assert "Confirm the managed agent branch resolves to the same commit" in content
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
    source = (ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js").read_text(
        encoding="utf-8"
    )
    helper_source = source[
        source.index("function taskBoardDependencyWaitIds(task)") : source.index(
            "function renderTaskBoardBlockerLine(task)"
        )
    ]
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for Team UI dependency-label tests")
    script = f"""
      const assert = require("node:assert/strict");
      const terminalTaskStates = new Set(["finished", "accepted", "rejected", "cancelled"]);
      const state = {{
        tasks: {{
          blocked: {{ state: "blocked" }},
          pending: {{ state: "pending" }},
        }},
      }};
      const text = (value) => String(value ?? "").trim();
      const escapeHtml = (value) => text(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;");
      const taskBoardReadiness = (task) =>
        text(task?.readiness || task?.assessment?.readiness) || "unknown";
      {helper_source}

      const mixed = {{
        state: "pending",
        readiness: "waiting_on_dependencies",
        resolution_status: "unresolved",
        dependency_status: "blocked",
        blocked_by_task_ids: ["blocked", "pending"],
      }};
      assert.equal(taskBoardWaitsOnDependencies(mixed), true);
      assert.deepEqual(taskBoardBlockedDependencyIds(mixed), ["blocked"]);
      assert.equal(
        renderTaskBoardDependencyWaitBadge(mixed),
        '<span class="state-pill warning">Waiting on 2 dependencies; 1 blocked</span>',
      );

      const cancelled = {{
        ...mixed,
        state: "cancelled",
        resolution_status: "resolved",
        dependency_status: "waiting",
      }};
      assert.equal(taskBoardWaitsOnDependencies(cancelled), false);
      assert.equal(renderTaskBoardDependencyWaitBadge(cancelled), "");
      assert.equal(
        taskBoardWaitsOnDependencies({{ ...mixed, state: "rejected" }}),
        false,
      );
      assert.equal(
        taskBoardWaitsOnDependencies({{ ...mixed, resolution_status: "resolved" }}),
        false,
      );

      const scoped = {{
        ...mixed,
        blocked_by_task_ids: ["unavailable", "pending"],
      }};
      assert.equal(
        renderTaskBoardDependencyWaitBadge(scoped),
        '<span class="state-pill warning">Waiting on 2 dependencies; at least one blocked</span>',
      );
    """

    result = subprocess.run(
        [node, "--input-type=commonjs"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_live_release_verification_retries_after_transient_request_failure() -> None:
    source = (ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js").read_text(
        encoding="utf-8"
    )
    polling_source = source[
        source.index("function activeReleaseVerificationRun(") : source.index(
            "async function loadMyHumanReviews("
        )
    ]
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for Team UI polling tests")
    script = f"""
      const assert = require("node:assert/strict");
      const text = (value) => String(value ?? "").trim();
      const API_BASE = "/api/team";
      const RELEASE_VERIFICATION_LIVE_POLL_MS = 2000;
      const expandedReleaseDetailIds = new Set(["release-1"]);
      const releaseDetailCache = new Map();
      const releaseVerificationLiveCache = new Map();
      const releaseVerificationLivePollTimers = new Map();
      const state = {{
        release_packages: {{
          "release-1": {{
            id: "release-1",
            verification_runs: [{{ id: "run-1", state: "running" }}],
          }},
        }},
      }};
      const scheduled = [];
      const window = {{
        setTimeout: (callback, delay) => {{
          scheduled.push({{ callback, delay }});
          return scheduled.length;
        }},
        clearTimeout: () => {{}},
      }};
      const mergeReleaseDetailWithCurrentState = (release) => release;
      const renderReleaseDetailSurfaces = () => {{}};
      const responseText = async () => "temporary gateway failure";
      let requestCount = 0;
      const teamFetch = async () => {{
        requestCount += 1;
        if (requestCount === 1) {{
          return {{ ok: false }};
        }}
        return {{
          ok: true,
          json: async () => ({{ run: {{ state: "passed" }} }}),
        }};
      }};
      {polling_source}

      (async () => {{
        await loadReleaseVerificationLive("release-1");
        assert.equal(requestCount, 1);
        assert.equal(scheduled.length, 1);
        assert.equal(scheduled[0].delay, RELEASE_VERIFICATION_LIVE_POLL_MS);
        assert.equal(releaseVerificationLivePollTimers.has("release-1"), true);
        assert.equal(
          releaseVerificationLiveCache.get("release-1").error,
          "temporary gateway failure",
        );

        scheduled.shift().callback();
        await new Promise((resolve) => setImmediate(resolve));

        assert.equal(requestCount, 2);
        assert.equal(releaseVerificationLiveCache.get("release-1").error, null);
        assert.equal(
          releaseVerificationLiveCache.get("release-1").payload.run.state,
          "passed",
        );
        assert.equal(releaseVerificationLivePollTimers.has("release-1"), false);
      }})().catch((error) => {{
        console.error(error);
        process.exitCode = 1;
      }});
    """

    result = subprocess.run(
        [node, "--input-type=commonjs"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_rejected_review_exception_action_is_hidden_after_successor_exists() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is unavailable")
    source = (ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js").read_text(
        encoding="utf-8"
    )
    action_source = source[
        source.index("function releaseHasSuccessor(releaseId)") : source.index(
            "async function retryReleaseGate"
        )
    ]
    script = f"""
      const assert = require("node:assert/strict");
      const state = {{ release_packages: {{}} }};
      const text = (value) => value == null ? "" : String(value);
      const values = (record) => Object.values(record || {{}});
      const escapeAttr = text;
      const escapeHtml = text;
      {action_source}

      const predecessor = {{
        id: "release-predecessor",
        state: "rejected",
        gate_failure: {{ stage: "review", kind: "rejected", source_id: "review-1" }},
      }};
      assert.match(renderReleaseGateAction(predecessor), /Retry rejected review by exception/);
      state.release_packages.successor = {{
        id: "release-successor",
        supersedes_release_package_id: predecessor.id,
      }};
      assert.equal(renderReleaseGateAction(predecessor), "");
    """

    result = subprocess.run(
        [node, "--input-type=commonjs"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_agent_editor_round_trips_idle_context_grace_minutes() -> None:
    source = (ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js").read_text(
        encoding="utf-8"
    )
    editor_source = source[
        source.index(
            'if (type === "agent") {', source.index("function renderEditFields")
        ) : source.index('if (type === "role") {', source.index("function renderEditFields"))
    ]
    payload_source = source[
        source.index(
            'if (type === "agent") {', source.index("function editPayloadForForm")
        ) : source.index('if (type === "role") {', source.index("function editPayloadForForm"))
    ]
    binding_source = source[
        source.index("function bindAgentEditTeamScopedSelects") : source.index(
            "function refreshAgentEditTeamScopedSelects"
        )
    ]

    assert "contextClearingIdleGraceMinutesForEdit" in editor_source
    assert 'numberField("Idle grace minutes"' in editor_source
    assert (
        'data-agent-context-idle-grace${contextPolicy === "idle" ? "" : " hidden"}' in editor_source
    )
    assert "max: 10080" in editor_source
    assert "context_clearing_idle_grace_seconds" in payload_source
    assert "context_clearing_idle_grace_minutes" in payload_source
    assert "* 60" in payload_source
    assert 'if (contextClearingPolicy === "idle")' in payload_source
    assert 'select[name="context_clearing_policy"]' in binding_source
    assert "syncAgentEditContextIdleGraceField" in binding_source
    assert 'graceInput.value = "60"' in source
    assert "idleGraceField.hidden = !isIdle;" in source
