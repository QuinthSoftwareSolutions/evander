from __future__ import annotations

from pathlib import Path

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


def test_planning_view_receives_formal_review_status_collection() -> None:
    assert "review_requests" in TEAM_VIEW_COLLECTIONS["planning"]


def test_versioned_agent_guidance_explains_task_topology_and_cancellation() -> None:
    content = _team_agent_guidance_content(
        agent={"id": "implementer", "display_name": "Implementer"}
    )

    assert TEAM_AGENT_GUIDANCE_VERSION == 23
    assert "parent_task_id means constituent composition" in content
    assert "dependency_task_ids means execution ordering" in content
    assert "originating_task_id records provenance" in content
    assert "model that work as a self-owned child" in content
    assert "the parent stays quiet" in content
    assert "accepted child deliverables through source_deliverable_ids" in content
    assert "team_cancel_task" in content
    assert "stable reason code and reason" in content
    assert "submits an assessable delivered/evidenced-noop contribution" in content
    assert "team_report_blocker" in content
    assert "team_create_human_finding_correction_root" in content
    assert "team_propose_human_finding_resolution" in content
    assert "never substitutes for the human decision" in content
    assert "team_close_post_release_case" in content
    assert "does not roll back publication" in content


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


def test_human_review_non_approval_actions_bypass_required_checklist_validation() -> None:
    source = (
        ROOT / "src" / "evander_team_addon" / "ui_static" / "team-ui.js"
    ).read_text(encoding="utf-8")

    assert 'data-human-review-decision="approved">Approve</button>' in source
    assert (
        'data-human-review-decision="changes_requested" formnovalidate>'
        "Request changes</button>"
    ) in source
    assert (
        'data-human-review-decision="blocked" formnovalidate>Blocked</button>'
    ) in source
