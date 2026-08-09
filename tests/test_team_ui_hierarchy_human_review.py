from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from evander_team_addon.api import (
    TEAM_STATE_COLLECTION_KEYS,
    TEAM_VIEW_COLLECTIONS,
)
from evander_team_addon.providers import (
    TEAM_AGENT_GUIDANCE_VERSION,
    _project_repo_prompt_guidance,
    _task_creation_mandate_guidance,
    _team_agent_guidance_content,
)

ROOT = Path(__file__).resolve().parents[1]


def test_task_surfaces_receive_dispatch_and_worktree_identity_collections() -> None:
    required = {
        "dispatches",
        "worktree_cleanup_candidates",
        "git_work_contexts",
    }

    assert required <= set(TEAM_STATE_COLLECTION_KEYS)
    for view in (
        "workspace",
        "operations",
        "delivery",
        "projects",
        "task-board",
        "people",
        "planning",
    ):
        assert required <= TEAM_VIEW_COLLECTIONS[view]


def test_projects_view_receives_release_evidence_collections() -> None:
    assert {
        "review_requests",
        "git_promotions",
    } <= TEAM_VIEW_COLLECTIONS["projects"]


def test_workspace_excludes_operator_directives_from_attention() -> None:
    source = (
        ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js"
    ).read_text(encoding="utf-8")

    assert "function workspaceInboxItemNeedsAttention(item)" in source
    assert 'return text(item.item_type) !== "operator_directive";' in source
    assert "const attentionInbox = pendingInbox.filter(workspaceInboxItemNeedsAttention);" in source
    assert "pendingInbox: attentionInbox," in source
    assert "function workspaceRuntimeAttentionNeedsAttention(row)" in source
    assert ".filter(workspaceRuntimeAttentionNeedsAttention)" in source
    assert "function pendingInboxForAgent(agentId)" in source


def test_workspace_attention_has_pagination_controls() -> None:
    source = (
        ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js"
    ).read_text(encoding="utf-8")
    stylesheet = (
        ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.css"
    ).read_text(encoding="utf-8")

    assert "const workspaceAttentionPageSize = 7;" in source
    assert "let workspaceAttentionPage = 1;" in source
    assert "function bindWorkspaceAttentionPagination()" in source
    assert "data-workspace-attention-page" in source
    assert "function renderWorkspaceAttentionPagination(total, page, pageCount)" in source
    assert "Showing ${first}-${last} of ${total}" in source
    assert ".workspace-attention-pagination" in stylesheet


def test_workspace_places_delivery_under_current_workload_without_tab_shortcuts() -> None:
    source = (
        ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js"
    ).read_text(encoding="utf-8")
    dashboard_source = source[
        source.index("function renderWorkspaceDashboard()") : source.index(
            "function renderWorkspaceTarget("
        )
    ]

    assert "renderWorkspaceAttentionPanel(model)," in dashboard_source
    assert "renderWorkspaceWorkStatusPanel(model)," in dashboard_source
    assert "renderWorkspaceDeliveryPanel(model)," in dashboard_source
    assert "renderWorkspaceNavigationPanel" not in source
    assert "Detailed tabs" not in source


def test_attention_excludes_retired_or_superseded_delivery_lineage() -> None:
    source = (
        ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js"
    ).read_text(encoding="utf-8")

    assert "function isCurrentDeliverable(deliverable)" in source
    assert 'return !text(deliverable?.superseded_by_deliverable_id);' in source
    assert "function isCurrentReviewRequest(review)" in source
    assert 'return !review?.retired_at && !text(review?.superseded_by_review_request_id);' in source
    assert ".filter(workspaceReviewNeedsAttention)" in source
    assert "if (!isCurrentDeliverable(row.deliverable))" in source
    assert "const currentReviews = reviews.filter(isCurrentReviewRequest);" in source


def test_planning_view_receives_formal_review_status_collection() -> None:
    assert "review_requests" in TEAM_VIEW_COLLECTIONS["planning"]


def test_versioned_agent_guidance_explains_task_topology_and_cancellation() -> None:
    content = _team_agent_guidance_content(
        agent={"id": "implementer", "display_name": "Implementer"}
    )

    assert TEAM_AGENT_GUIDANCE_VERSION == 30
    assert "parent_task_id means constituent composition" in content
    assert "dependency_task_ids means execution ordering" in content
    assert "originating_task_id records provenance" in content
    assert "originating_task_id alone does not seed Git content" in content
    assert "every accepted predecessor whose content is required" in content
    assert "silently starting from the project base branch" in content
    assert "prepared worktree HEAD has the same tree as that candidate" in content
    assert "do not choose or merge a different source ad hoc" in content
    assert "model that work as a self-owned child" in content
    assert "the parent stays quiet" in content
    assert "accepted child deliverables through source_deliverable_ids" in content
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


def test_task_ui_renders_self_contained_inherited_blocker_reasons() -> None:
    source = (
        ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js"
    ).read_text(encoding="utf-8")

    assert "function inheritedTaskBlockers(task)" in source
    assert "function inheritedTaskBlockerLabel(blocker)" in source
    assert "function renderInheritedTaskBlockerDetail(blocker)" in source
    assert "blocker.reason || blocker.summary" in source
    assert "task-inherited-blocker-card" in source


def test_task_board_distinguishes_dependency_waits_from_assigned_blockers() -> None:
    source = (
        ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js"
    ).read_text(encoding="utf-8")

    assert "function taskBoardWaitsOnDependencies(task)" in source
    assert "function taskBoardBlockedDependencyIds(task)" in source
    assert "function renderTaskBoardDependencyWaitBadge(task)" in source
    assert "Waiting on dependencies:" in source
    assert "Waiting on blocked dependencies:" not in source
    assert "at least one blocked" in source
    assert "Assigned blocker:" in source
    assert "assigned blocker" in source
    assert "Unresolved dependency task IDs" in source
    assert "Blocking task IDs" not in source
    assert "if (taskBoardWaitsOnDependencies(task) && dependencyWaitIds.length)" in source


def test_project_git_editor_exposes_distinct_agent_commit_identities() -> None:
    source = (
        ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js"
    ).read_text(encoding="utf-8")

    for field_name in (
        "agent_author_name",
        "agent_author_email",
        "agent_committer_name",
        "agent_committer_email",
    ):
        assert f'name="{field_name}"' in source
    assert "git.agent_author_identity = { name: authorName, email: authorEmail };" in source
    assert "git.agent_committer_identity = { name: committerName, email: committerEmail };" in source


def test_task_board_dependency_wait_labels_handle_mixed_and_resolved_tasks() -> None:
    source = (
        ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js"
    ).read_text(encoding="utf-8")
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


def test_human_review_findings_require_explicit_release_or_deliverable_scope() -> None:
    source = (
        ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js"
    ).read_text(encoding="utf-8")
    selector_source = source[
        source.index("function renderHumanReviewTargetSelector(") : source.index(
            "function renderHumanReviewFindingRow("
        )
    ]
    form_source = source[
        source.index("function humanReviewFindingFromRow(") : source.index(
            "function humanReviewFindingDraftFromRow("
        )
    ]

    assert "Entire release candidate" in selector_source
    assert "human_finding_target_release_candidate" in selector_source
    assert "human_finding_target_deliverable_ids" in selector_source
    assert "(optional)" not in selector_source
    assert 'target_scope: targetsEntireRelease' in form_source
    assert '"release_candidate"' in form_source
    assert '"deliverables"' in form_source
    assert (
        "Select the entire release candidate or at least one affected deliverable."
        in form_source
    )


def test_task_cleanup_ui_renders_bounded_inventory_evidence() -> None:
    source = (
        ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js"
    ).read_text(encoding="utf-8")

    assert "candidate.ignored_file_count" in source
    assert "candidate.ignored_file_inventory_truncated" in source
    assert "candidate.ignored_file_inventory_sha256" in source
    assert "(candidate.ignored_file_inventory || []).slice(0, 5)" in source


def test_human_review_non_approval_actions_bypass_required_checklist_validation() -> None:
    source = (
        ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js"
    ).read_text(encoding="utf-8")
    approval_click_source = source[
        source.index("const humanReviewApprove") : source.index("const findingAdd")
    ]

    assert 'data-human-review-decision="approved">Approve</button>' in source
    assert (
        'data-human-review-decision="changes_requested" formnovalidate>'
        "Request changes</button>"
    ) in source
    assert (
        'data-human-review-decision="blocked" formnovalidate>Blocked</button>'
    ) in source
    assert 'name="human_finding_text"' in approval_click_source
    assert 'name="human_finding_target_release_candidate"' in approval_click_source


def test_human_review_card_offers_a_pinned_source_bundle_download() -> None:
    source = (ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js").read_text(
        encoding="utf-8"
    )
    stylesheet = (ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.css").read_text(
        encoding="utf-8"
    )

    assert "function renderHumanReviewBundleDownload(request)" in source
    assert "Download review bundle" in source
    assert "human-review-requests/${encodeURIComponent(requestId)}/review-bundle" in source
    assert "dotnet restore" in source
    assert "npm ci" in source
    assert ".human-review-bundle-download" in stylesheet


def test_delivery_workspace_defaults_to_attention_and_exposes_project_focus() -> None:
    source = (
        ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js"
    ).read_text(encoding="utf-8")
    markup = (
        ROOT / "src" / "evander_team_addon" / "ui_static" / "index.html"
    ).read_text(encoding="utf-8")

    assert 'let selectedDeliveryFilter = "attention";' in source
    assert "function deliveryRowNeedsAttention(row)" in source
    assert "currentDeliveryReleasePackages(row.releasePackages).some" in source
    assert '"pending", "in_progress"].includes(text(release.state))' in source
    assert (
        "function renderDeliveryWorkspaceControls(allRows, statusRows, visibleRows)"
        in source
    )
    assert "data-delivery-project-filter" in source
    assert 'data-filter="attention"' in markup
    assert "Needs attention" in markup
    assert "All records" in markup


def test_deliverable_drill_down_renders_evidence_backed_release_pipeline() -> None:
    source = (
        ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js"
    ).read_text(encoding="utf-8")

    assert "function renderDeliverablePipeline(row)" in source
    assert 'label: "Implementation evidence"' in source
    assert '"Deliverable agent review"' in source
    assert '"Deliverable acceptance"' in source
    assert 'label: "Release inclusion"' in source
    assert 'label: "Release agent review"' in source
    assert 'stage.label !== "Candidate prepared"' in source
    assert "function deliveryPipelineBlockers(row, release)" in source
    assert "Load exact gate evidence" in source
    assert "releaseDetailCache.get(text(compactRelease.id))" in source


def test_delivery_view_surfaces_pending_and_audited_initial_release_decisions() -> None:
    source = (
        ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js"
    ).read_text(encoding="utf-8")

    assert 'initial_release_disposition?.state) === "pending"' in source
    assert "function renderInitialReleaseDisposition(deliverable)" in source
    assert "Initial release decision" in source
    assert "Waiting for the project lead or team manager" in source
    assert "Not planned for an initial release" in source
    assert "initialRelease.release_package_id" in source


def test_live_release_verification_retries_after_transient_request_failure() -> None:
    source = (
        ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js"
    ).read_text(encoding="utf-8")
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


def test_selected_agent_runtime_shows_reset_bounded_input_usage() -> None:
    source = (
        ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js"
    ).read_text(encoding="utf-8")
    detail_source = source[
        source.index("function agentRuntimeDetailRows(details)") : source.index(
            "function renderAgentOpenTasks(openTasks)"
        )
    ]
    runtime_source = source[
        source.index("function runtimeSummary(agentId)") : source.index(
            "function runtimeOperationForAgent(agentId)"
        )
    ]

    assert "Latest context reset: no completed context reset yet" in detail_source
    assert "Input tokens since latest reset" in detail_source
    assert 'runtime.contextClearingPolicy !== "every_wake"' in detail_source
    assert "runtime.context_reset_usage?.input_tokens_since_reset" in runtime_source
    assert 'contextClearingPolicy !== "every_wake"' in runtime_source
