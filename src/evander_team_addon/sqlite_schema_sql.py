"""Current Team SQLite schema and additive index definitions."""

from __future__ import annotations

SQLITE_SCHEMA_VERSION = 81

_LIFECYCLE_INDEX_SQL = """
DROP INDEX IF EXISTS idx_team_dispatches_pending_mirror;
CREATE INDEX IF NOT EXISTS idx_team_dispatches_pending_activation
    ON team_dispatches(id) WHERE status IN ('preparing', 'leased') AND activation_completed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_team_dispatches_receipt_recovery
    ON team_dispatches(id) WHERE activation_completed_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_team_agent_runtimes_active_dispatch
    ON team_agent_runtimes(active_dispatch_id) WHERE active_dispatch_id IS NOT NULL;
"""

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS team_state_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_teams (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    manager_agent_id TEXT NOT NULL,
    operator_communication_target_id TEXT,
    max_cost_usd TEXT,
    cost_warning_ratio TEXT NOT NULL DEFAULT '0.8',
    cost_budget_renewal_mode TEXT NOT NULL DEFAULT 'manual',
    cost_budget_windows_json TEXT NOT NULL DEFAULT '[]',
    escalate_to_governance INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_teams_archived ON team_teams(archived, id);

CREATE TABLE IF NOT EXISTS team_team_projects (
    team_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    PRIMARY KEY(team_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_team_projects_project ON team_team_projects(project_id);

CREATE TABLE IF NOT EXISTS team_governance_groups (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    manager_agent_id TEXT NOT NULL,
    max_cost_usd TEXT,
    archived INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_governance_groups_archived
    ON team_governance_groups(archived, id);

CREATE TABLE IF NOT EXISTS team_governance_group_members (
    governance_group_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    team_id TEXT NOT NULL,
    PRIMARY KEY(governance_group_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_governance_group_members_team
    ON team_governance_group_members(team_id);

CREATE TABLE IF NOT EXISTS team_governance_group_goals (
    governance_group_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    goal_id TEXT NOT NULL,
    goal_name TEXT NOT NULL,
    description TEXT NOT NULL,
    state TEXT NOT NULL,
    PRIMARY KEY(governance_group_id, position)
);

CREATE TABLE IF NOT EXISTS team_governance_agenda (
    id TEXT PRIMARY KEY,
    governance_group_id TEXT NOT NULL,
    item_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    team_id TEXT,
    project_id TEXT,
    agent_id TEXT,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL,
    priority TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_governance_agenda_group_state
    ON team_governance_agenda(governance_group_id, state, priority, id);
CREATE INDEX IF NOT EXISTS idx_team_governance_agenda_team
    ON team_governance_agenda(team_id, state, id);

CREATE TABLE IF NOT EXISTS team_governance_runtimes (
    governance_group_id TEXT PRIMARY KEY,
    wake_leased_until TEXT,
    active_dispatch_id TEXT,
    preparation_expires_at TEXT,
    queued_at TEXT,
    requested_lease_seconds INTEGER NOT NULL DEFAULT 900,
    wake_reason TEXT,
    last_wake_at TEXT,
    next_wake_at TEXT,
    last_cost_signal_fingerprints_json TEXT NOT NULL DEFAULT '[]',
    last_blocked_signal_fingerprints_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_roles (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    project_id TEXT,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    max_ephemeral_concurrent INTEGER NOT NULL,
    archived INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_roles_team_project ON team_roles(team_id, project_id, archived, id);

CREATE TABLE IF NOT EXISTS team_role_permissions (
    role_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    permission TEXT NOT NULL,
    PRIMARY KEY(role_id, position)
);

CREATE TABLE IF NOT EXISTS team_projects (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    name TEXT NOT NULL,
    lead_agent_id TEXT NOT NULL,
    directory_path TEXT NOT NULL DEFAULT 'root',
    git_json TEXT,
    release_policy_json TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_projects_team_status ON team_projects(team_id, status, id);

CREATE TABLE IF NOT EXISTS team_project_goals (
    project_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    goal_id TEXT NOT NULL,
    description TEXT NOT NULL,
    state TEXT NOT NULL,
    PRIMARY KEY(project_id, position)
);

CREATE TABLE IF NOT EXISTS team_agents (
    id TEXT PRIMARY KEY,
    team_id TEXT,
    display_name TEXT,
    avatar_json TEXT,
    voice_profile_json TEXT,
    role_id TEXT,
    session_id TEXT NOT NULL,
    reports_to_agent_id TEXT,
    retired_team_id TEXT,
    max_ephemeral_concurrent INTEGER NOT NULL,
    status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_agents_status_scope ON team_agents(status, team_id, id);
CREATE INDEX IF NOT EXISTS idx_team_agents_session ON team_agents(session_id);

CREATE TABLE IF NOT EXISTS team_project_assignments (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    role_id TEXT,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_team_project_assignments_unique_kind
    ON team_project_assignments(project_id, agent_id, kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_team_project_assignments_one_lead
    ON team_project_assignments(project_id, kind) WHERE kind = 'project_lead';
CREATE INDEX IF NOT EXISTS idx_team_project_assignments_agent
    ON team_project_assignments(agent_id, project_id, kind);

CREATE TABLE IF NOT EXISTS team_agent_subordinates (
    agent_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    subordinate_agent_id TEXT NOT NULL,
    PRIMARY KEY(agent_id, position)
);

CREATE TABLE IF NOT EXISTS team_agent_stand_downs (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    reason TEXT NOT NULL,
    source TEXT NOT NULL,
    set_by TEXT NOT NULL,
    set_at TEXT NOT NULL,
    cleared_by TEXT,
    cleared_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_team_agent_stand_downs_agent_active
    ON team_agent_stand_downs(agent_id, cleared_at, source, id);

CREATE TABLE IF NOT EXISTS team_agent_runtimes (
    agent_id TEXT PRIMARY KEY,
    wake_leased_until TEXT,
    active_dispatch_id TEXT,
    wake_reason TEXT,
    last_wake_at TEXT,
    next_wake_at TEXT,
    no_progress_count INTEGER NOT NULL,
    last_progress_at TEXT,
    low_priority_batch_started_at TEXT,
    low_priority_batch_cycle_count INTEGER NOT NULL DEFAULT 0,
    low_priority_batch_due_after_cycles INTEGER,
    context_clearing_policy TEXT NOT NULL DEFAULT 'off',
    context_clearing_idle_grace_seconds INTEGER NOT NULL DEFAULT 0,
    context_idle_since_at TEXT,
    context_idle_rotation_due_at TEXT,
    auto_context_clearing_enabled INTEGER NOT NULL,
    context_rotation_pending INTEGER NOT NULL,
    context_rotation_requested_by TEXT,
    context_rotation_requested_at TEXT,
    context_rotation_reason TEXT,
    context_rotation_request_note TEXT,
    last_handoff_summary TEXT,
    context_handoff_preparation_pending INTEGER NOT NULL DEFAULT 0,
    context_handoff_preparation_status TEXT,
    last_agent_handoff_summary TEXT,
    last_agent_handoff_at TEXT,
    last_context_rotated_at TEXT,
    last_thread_id TEXT,
    last_delivered_role_description_fingerprint TEXT,
    work_lineage_thread_ids_json TEXT NOT NULL DEFAULT '{}',
    work_lineage_thread_dispatch_ids_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_agent_runtimes_wake
    ON team_agent_runtimes(wake_leased_until, next_wake_at, active_dispatch_id);

CREATE TABLE IF NOT EXISTS team_work_lineages (
    id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_external_source_evidence_snapshots (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    project_id TEXT,
    provider_type TEXT NOT NULL,
    source_type TEXT NOT NULL,
    external_identifier TEXT NOT NULL,
    external_version TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    expires_at TEXT,
    freshness_status TEXT NOT NULL,
    access_classification TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_external_source_evidence_scope
    ON team_external_source_evidence_snapshots(team_id, project_id, fetched_at, id);
CREATE INDEX IF NOT EXISTS idx_team_external_source_evidence_identity
    ON team_external_source_evidence_snapshots(
        provider_type, source_type, external_identifier, external_version, id
    );
CREATE INDEX IF NOT EXISTS idx_team_external_source_evidence_digest
    ON team_external_source_evidence_snapshots(content_digest, id);

CREATE TABLE IF NOT EXISTS team_cases (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    project_id TEXT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    owner_agent_id TEXT,
    source TEXT,
    source_ref TEXT,
    tags_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    task_ids_json TEXT NOT NULL,
    source_evidence_snapshot_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_team_cases_team_status
    ON team_cases(team_id, status, priority, id);
CREATE INDEX IF NOT EXISTS idx_team_cases_project_status
    ON team_cases(project_id, status, priority, id);
CREATE INDEX IF NOT EXISTS idx_team_cases_owner_status
    ON team_cases(owner_agent_id, status, priority, id);
CREATE INDEX IF NOT EXISTS idx_team_cases_source ON team_cases(source, source_ref, id);

CREATE TABLE IF NOT EXISTS team_tasks (
    id TEXT PRIMARY KEY,
    owner_agent_id TEXT,
    assignment_mode TEXT NOT NULL DEFAULT 'agent',
    assignment_role_id TEXT,
    assignment_started_at TEXT,
    project_id TEXT NOT NULL,
    case_id TEXT,
    priority INTEGER NOT NULL,
    state TEXT NOT NULL,
    state_entered_at TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    created_by_agent_id TEXT,
    created_by_actor TEXT,
    parent_task_id TEXT,
    authored_by_json TEXT,
    work_item_attributions_json TEXT,
    originating_task_id TEXT,
    release_base_package_id TEXT,
    work_lineage_id TEXT,
    git_base_branch TEXT,
    git_base_commit_sha TEXT,
    mutation_policy_json TEXT,
    cancellation_json TEXT,
    task_kind TEXT NOT NULL DEFAULT 'support',
    outcome_contract_json TEXT,
    integration_boundary INTEGER NOT NULL DEFAULT 0,
    deliverable_contract_json TEXT,
    completion_json TEXT,
    source_evidence_snapshot_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_tasks_owner_state_priority
    ON team_tasks(owner_agent_id, state, priority, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_tasks_project_state
    ON team_tasks(project_id, state, priority, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_tasks_parent ON team_tasks(parent_task_id, id);

CREATE TABLE IF NOT EXISTS team_task_dependencies (
    task_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    dependency_task_id TEXT NOT NULL,
    PRIMARY KEY(task_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_task_dependencies_dependency
    ON team_task_dependencies(dependency_task_id);

CREATE TABLE IF NOT EXISTS team_task_deliverables (
    task_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    deliverable_id TEXT NOT NULL,
    PRIMARY KEY(task_id, position)
);

CREATE TABLE IF NOT EXISTS team_deliverables (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    producer_agent_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    state TEXT NOT NULL,
    branch_context_id TEXT,
    agent_branch TEXT,
    review_branch TEXT,
    feature_branch TEXT,
    release_branch TEXT,
    commit_sha TEXT,
    submitted_commit_sha TEXT,
    reviewed_commit_sha TEXT,
    feature_commit_sha TEXT,
    release_commit_sha TEXT,
    accepted_pinned_ref TEXT,
    verification_json TEXT,
    outcomes_json TEXT NOT NULL DEFAULT '[]',
    carried_findings_json TEXT NOT NULL DEFAULT '[]',
    review_manifest_json TEXT,
    dependency_composition_manifest_json TEXT,
    acceptance_policy_version INTEGER NOT NULL DEFAULT 1,
    acceptance_recipient_agent_id TEXT,
    acceptance_decision_json TEXT,
    initial_release_disposition_json TEXT,
    supersedes_deliverable_id TEXT,
    superseded_by_deliverable_id TEXT,
    supersession_reason TEXT,
    superseded_at TEXT,
    superseded_by_actor TEXT,
    review_cohort_id TEXT,
    review_cohort_sealed_at TEXT,
    rework_requested_at TEXT,
    rework_requested_by_agent_id TEXT,
    rework_reason TEXT,
    completion_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_deliverables_task_state ON team_deliverables(task_id, state, id);
CREATE INDEX IF NOT EXISTS idx_team_deliverables_producer ON team_deliverables(producer_agent_id, created_at, id);

CREATE TABLE IF NOT EXISTS team_deliverable_artifacts (
    deliverable_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    artifact_ref TEXT NOT NULL,
    PRIMARY KEY(deliverable_id, position)
);

CREATE TABLE IF NOT EXISTS team_deliverable_reviews (
    deliverable_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    review_request_id TEXT NOT NULL,
    PRIMARY KEY(deliverable_id, position)
);

CREATE TABLE IF NOT EXISTS team_deliverable_promotion_records (
    deliverable_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    promotion_record_id TEXT NOT NULL,
    PRIMARY KEY(deliverable_id, position)
);

CREATE TABLE IF NOT EXISTS team_deliverable_sources (
    deliverable_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    source_deliverable_id TEXT NOT NULL,
    PRIMARY KEY(deliverable_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_deliverable_sources_source
    ON team_deliverable_sources(source_deliverable_id, deliverable_id);

CREATE TABLE IF NOT EXISTS team_review_profiles (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    project_id TEXT,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    archived INTEGER NOT NULL,
    required_outcome_kinds_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_review_profiles_scope
    ON team_review_profiles(team_id, project_id, archived, id);

CREATE TABLE IF NOT EXISTS team_outcome_kind_definitions (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    project_id TEXT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    reference_requirements TEXT NOT NULL,
    evidence_requirements TEXT NOT NULL,
    archived INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_team_outcome_kind_definitions_scope_kind
    ON team_outcome_kind_definitions(
        team_id,
        COALESCE(project_id, ''),
        kind
    );
CREATE INDEX IF NOT EXISTS idx_team_outcome_kind_definitions_scope
    ON team_outcome_kind_definitions(team_id, project_id, archived, kind, id);

CREATE TABLE IF NOT EXISTS team_review_profile_roles (
    profile_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    role_id TEXT NOT NULL,
    PRIMARY KEY(profile_id, position)
);

CREATE TABLE IF NOT EXISTS team_review_profile_criteria (
    profile_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    criterion_id TEXT NOT NULL,
    label TEXT NOT NULL,
    description TEXT NOT NULL,
    required INTEGER NOT NULL,
    PRIMARY KEY(profile_id, position)
);

CREATE TABLE IF NOT EXISTS team_review_requests (
    id TEXT PRIMARY KEY,
    deliverable_id TEXT NOT NULL,
    release_package_id TEXT,
    candidate_commit_sha TEXT,
    reviewer_agent_id TEXT NOT NULL,
    requested_by_agent_id TEXT,
    requested_by_actor TEXT,
    state TEXT NOT NULL,
    review_profile_id TEXT,
    criteria_snapshot_json TEXT,
    required_outcome_kind_definitions_json TEXT NOT NULL DEFAULT '[]',
    instructions TEXT,
    reviewed_commit_sha TEXT,
    repository_snapshot_json TEXT,
    review_cohort_id TEXT,
    supersedes_review_request_id TEXT,
    superseded_by_review_request_id TEXT,
    retry_reason TEXT,
    retry_requested_by TEXT,
    block_kind TEXT,
    retired_at TEXT,
    retired_by TEXT,
    retirement_reason TEXT,
    created_at TEXT,
    decision_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_team_review_requests_deliverable_state
    ON team_review_requests(deliverable_id, state, id);
CREATE INDEX IF NOT EXISTS idx_team_review_requests_reviewer_state
    ON team_review_requests(reviewer_agent_id, state, id);

CREATE TABLE IF NOT EXISTS team_review_request_findings (
    review_request_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    finding TEXT NOT NULL,
    PRIMARY KEY(review_request_id, position)
);

CREATE TABLE IF NOT EXISTS team_review_request_criteria (
    review_request_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    criterion_id TEXT NOT NULL,
    state TEXT NOT NULL,
    finding TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(review_request_id, position)
);

CREATE TABLE IF NOT EXISTS team_inbox (
    id TEXT PRIMARY KEY,
    recipient_agent_id TEXT NOT NULL,
    item_type TEXT NOT NULL,
    sender_agent_id TEXT,
    task_id TEXT,
    response_requested INTEGER NOT NULL DEFAULT 1,
    priority TEXT NOT NULL DEFAULT 'normal',
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_inbox_recipient_state_created
    ON team_inbox(recipient_agent_id, state, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_inbox_task_state ON team_inbox(task_id, state, id);

CREATE TABLE IF NOT EXISTS team_blockers (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    project_id TEXT,
    task_id TEXT,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    mcp_capability_identity_json TEXT,
    reporter_agent_id TEXT,
    reporter_actor TEXT,
    reason_code TEXT NOT NULL DEFAULT 'unspecified',
    resolution_scope TEXT NOT NULL DEFAULT 'task',
    owner_agent_id TEXT,
    delegate_agent_id TEXT,
    state TEXT NOT NULL,
    route TEXT NOT NULL,
    summary TEXT NOT NULL,
    details TEXT NOT NULL,
    priority TEXT NOT NULL,
    escalation_depth INTEGER NOT NULL DEFAULT 0,
    active_dispatch_id TEXT,
    follow_up_at TEXT,
    last_progress_at TEXT,
    terminal_reference_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_team_blockers_open_source
    ON team_blockers(team_id, source_kind, source_id) WHERE state = 'open';
CREATE INDEX IF NOT EXISTS idx_team_blockers_owner_state
    ON team_blockers(owner_agent_id, state, updated_at, id);
CREATE INDEX IF NOT EXISTS idx_team_blockers_delegate_state
    ON team_blockers(delegate_agent_id, state, updated_at, id);
CREATE INDEX IF NOT EXISTS idx_team_blockers_dispatch_state
    ON team_blockers(active_dispatch_id, state, id);
CREATE INDEX IF NOT EXISTS idx_team_blockers_team_project_state
    ON team_blockers(team_id, project_id, state, updated_at, id);

CREATE TABLE IF NOT EXISTS team_blocker_affected_tasks (
    blocker_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    task_id TEXT NOT NULL,
    PRIMARY KEY(blocker_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_blocker_affected_tasks_task
    ON team_blocker_affected_tasks(task_id, blocker_id);

CREATE TABLE IF NOT EXISTS team_blocker_events (
    id TEXT PRIMARY KEY,
    blocker_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_agent_id TEXT,
    from_agent_id TEXT,
    to_agent_id TEXT,
    dispatch_id TEXT,
    note TEXT NOT NULL,
    reference_ids_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_blocker_events_blocker
    ON team_blocker_events(blocker_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_blocker_events_dispatch
    ON team_blocker_events(dispatch_id, created_at, id);

CREATE TABLE IF NOT EXISTS team_action_records (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    dispatch_id TEXT NOT NULL,
    action_json TEXT NOT NULL,
    success INTEGER NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    dismissed_at TEXT,
    dismissed_by TEXT,
    dismissed_reason TEXT,
    feedback_delivered_at TEXT,
    feedback_delivered_by_dispatch_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_team_action_records_agent_dispatch
    ON team_action_records(agent_id, dispatch_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_action_records_attention
    ON team_action_records(success, dismissed_at, created_at, id);

CREATE TABLE IF NOT EXISTS team_memories (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    project_id TEXT,
    codebase_id TEXT,
    agent_id TEXT,
    scope TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    body TEXT,
    status TEXT NOT NULL,
    confidence REAL,
    supersedes_memory_id TEXT,
    superseded_by_memory_id TEXT,
    created_by_agent_id TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_memories_team_status_updated
    ON team_memories(team_id, status, updated_at DESC, id);
CREATE INDEX IF NOT EXISTS idx_team_memories_project_status
    ON team_memories(project_id, status, updated_at DESC, id);
CREATE INDEX IF NOT EXISTS idx_team_memories_agent_status
    ON team_memories(agent_id, status, updated_at DESC, id);
CREATE INDEX IF NOT EXISTS idx_team_memories_codebase_status
    ON team_memories(codebase_id, status, updated_at DESC, id);
CREATE INDEX IF NOT EXISTS idx_team_memories_kind_status
    ON team_memories(kind, status, updated_at DESC, id);

CREATE TABLE IF NOT EXISTS team_memory_tags (
    memory_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY(memory_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_memory_tags_tag ON team_memory_tags(tag, memory_id);

CREATE TABLE IF NOT EXISTS team_memory_source_refs (
    memory_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    source_ref TEXT NOT NULL,
    PRIMARY KEY(memory_id, position)
);

CREATE TABLE IF NOT EXISTS team_memory_artifact_refs (
    memory_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    artifact_ref TEXT NOT NULL,
    PRIMARY KEY(memory_id, position)
);

CREATE TABLE IF NOT EXISTS team_conversations (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    operator_user_id TEXT NOT NULL,
    operator_display_name TEXT,
    created_by TEXT,
    last_message_preview TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_interaction_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_conversations_recent
    ON team_conversations(last_interaction_at DESC, id);

CREATE TABLE IF NOT EXISTS team_conversation_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    sender TEXT NOT NULL,
    message_type TEXT NOT NULL,
    body TEXT,
    status TEXT NOT NULL,
    dispatch_id TEXT,
    voice_session_id TEXT,
    source_message_id TEXT,
    attachment_ids_json TEXT NOT NULL DEFAULT '[]',
    source_message_ids_json TEXT NOT NULL DEFAULT '[]',
    seen_at TEXT,
    seen_dispatch_id TEXT,
    error TEXT,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_conversation_messages_conversation
    ON team_conversation_messages(conversation_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_conversation_messages_dispatch
    ON team_conversation_messages(dispatch_id, id);

CREATE TABLE IF NOT EXISTS team_external_chat_ingress (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    communication_target_id TEXT NOT NULL,
    source_message_id TEXT NOT NULL,
    source_thread_id TEXT,
    source_author_id TEXT NOT NULL,
    source_author_name TEXT,
    operator_user_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    dispatch_id TEXT NOT NULL,
    allow_team_mutations INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(provider, communication_target_id, source_message_id)
);
CREATE INDEX IF NOT EXISTS idx_team_external_chat_ingress_message
    ON team_external_chat_ingress(message_id, id);

CREATE TABLE IF NOT EXISTS team_external_chat_deliveries (
    id TEXT PRIMARY KEY,
    agent_reply_message_id TEXT NOT NULL,
    operator_message_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    communication_target_id TEXT NOT NULL,
    source_thread_id TEXT,
    status TEXT NOT NULL,
    provider_message_id TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    attempted_at TEXT,
    delivered_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(agent_reply_message_id, communication_target_id)
);
CREATE INDEX IF NOT EXISTS idx_team_external_chat_deliveries_reply
    ON team_external_chat_deliveries(agent_reply_message_id, id);

CREATE TABLE IF NOT EXISTS team_chat_attachments (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime_type TEXT,
    byte_count INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    sender TEXT NOT NULL,
    created_by_session_id TEXT,
    external_source_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_chat_attachments_message
    ON team_chat_attachments(message_id, created_at, id);

CREATE TABLE IF NOT EXISTS team_chat_attachment_policies (
    directory_path TEXT PRIMARY KEY,
    operator_to_agent_enabled INTEGER,
    agent_to_operator_enabled INTEGER,
    agent_send_requires_approval INTEGER,
    max_attachment_bytes INTEGER,
    max_attachments_per_message INTEGER,
    allowed_mime_types_json TEXT,
    allowed_extensions_json TEXT,
    blocked_extensions_json TEXT,
    updated_by TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_release_packages (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    owner_agent_id TEXT,
    work_lineage_id TEXT,
    summary TEXT,
    supersedes_release_package_id TEXT,
    lineage_root_release_package_id TEXT,
    lineage_sequence INTEGER NOT NULL DEFAULT 0,
    release_branch TEXT,
    canonical_branch TEXT,
    git_base_branch TEXT,
    published_commit_sha TEXT,
    base_commit_sha TEXT,
    canonical_base_commit_sha TEXT,
    candidate_commit_sha TEXT,
    candidate_tree_hash TEXT,
    approved_commit_sha TEXT,
    review_correction_task_id TEXT,
    verification_run_ids_json TEXT NOT NULL DEFAULT '[]',
    review_request_ids_json TEXT NOT NULL DEFAULT '[]',
    review_cohort_id TEXT,
    review_cohort_sealed_at TEXT,
    manifest_json TEXT NOT NULL DEFAULT '{}',
    gate_failure_json TEXT,
    state TEXT NOT NULL,
    published_at TEXT,
    error TEXT,
    policy_version INTEGER NOT NULL DEFAULT 1,
    verification_waivers_json TEXT NOT NULL DEFAULT '[]',
    gate_obligation_waivers_json TEXT NOT NULL DEFAULT '[]',
    scope_validation_json TEXT,
    cancellation_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_team_release_packages_project_state
    ON team_release_packages(project_id, state, created_at, id);

CREATE TABLE IF NOT EXISTS team_release_verification_runs (
    id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_release_deliverables (
    package_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    deliverable_id TEXT NOT NULL,
    PRIMARY KEY(package_id, position)
);

CREATE TABLE IF NOT EXISTS team_release_artifacts (
    package_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    artifact_ref TEXT NOT NULL,
    PRIMARY KEY(package_id, position)
);

CREATE TABLE IF NOT EXISTS team_release_feature_branches (
    package_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    feature_branch TEXT NOT NULL,
    PRIMARY KEY(package_id, position)
);

CREATE TABLE IF NOT EXISTS team_release_git_promotions (
    package_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    promotion_record_id TEXT NOT NULL,
    PRIMARY KEY(package_id, position)
);

CREATE TABLE IF NOT EXISTS team_human_review_requests (
    id TEXT PRIMARY KEY,
    release_package_id TEXT NOT NULL,
    state TEXT NOT NULL,
    candidate_commit_sha TEXT NOT NULL,
    candidate_tree_hash TEXT NOT NULL,
    assignment_generation INTEGER NOT NULL,
    preparation_generation INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_team_human_review_requests_release
    ON team_human_review_requests(release_package_id);
CREATE INDEX IF NOT EXISTS idx_team_human_review_requests_state
    ON team_human_review_requests(state, id);

CREATE TABLE IF NOT EXISTS team_human_review_request_configured_groups (
    human_review_request_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    group_id TEXT NOT NULL,
    PRIMARY KEY(human_review_request_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_human_review_request_groups_group
    ON team_human_review_request_configured_groups(
        group_id, human_review_request_id
    );

CREATE TABLE IF NOT EXISTS team_human_review_request_initial_users (
    human_review_request_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    PRIMARY KEY(human_review_request_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_human_review_request_initial_users_user
    ON team_human_review_request_initial_users(
        user_id, human_review_request_id
    );

CREATE TABLE IF NOT EXISTS team_human_review_request_current_users (
    human_review_request_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    PRIMARY KEY(human_review_request_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_human_review_request_current_users_user
    ON team_human_review_request_current_users(
        user_id, human_review_request_id
    );

CREATE TABLE IF NOT EXISTS team_human_review_request_explicit_users (
    human_review_request_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    PRIMARY KEY(human_review_request_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_human_review_request_explicit_users_user
    ON team_human_review_request_explicit_users(
        user_id, human_review_request_id
    );

CREATE TABLE IF NOT EXISTS team_human_review_decisions (
    id TEXT PRIMARY KEY,
    human_review_request_id TEXT NOT NULL,
    reviewer_user_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    request_id TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(human_review_request_id, request_id)
);
CREATE INDEX IF NOT EXISTS idx_team_human_review_decisions_request
    ON team_human_review_decisions(human_review_request_id, decided_at, id);

CREATE TABLE IF NOT EXISTS team_human_review_decision_confirmed_findings (
    human_review_decision_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    finding_id TEXT NOT NULL,
    PRIMARY KEY(human_review_decision_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_human_review_decision_findings_finding
    ON team_human_review_decision_confirmed_findings(
        finding_id, human_review_decision_id
    );

CREATE TABLE IF NOT EXISTS team_human_review_findings (
    id TEXT PRIMARY KEY,
    source_human_review_request_id TEXT NOT NULL,
    correction_task_id TEXT NOT NULL,
    state TEXT NOT NULL,
    resolved_by_human_review_request_id TEXT,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_human_review_findings_request_state
    ON team_human_review_findings(source_human_review_request_id, state, id);
CREATE INDEX IF NOT EXISTS idx_team_human_review_findings_task
    ON team_human_review_findings(correction_task_id, state, id);

CREATE TABLE IF NOT EXISTS team_human_review_finding_targets (
    finding_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    deliverable_id TEXT NOT NULL,
    PRIMARY KEY(finding_id, position)
);

CREATE TABLE IF NOT EXISTS team_human_review_finding_proposals (
    finding_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    deliverable_id TEXT NOT NULL,
    PRIMARY KEY(finding_id, position)
);

CREATE TABLE IF NOT EXISTS team_dispatches (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    cause_kind TEXT NOT NULL,
    cause_id TEXT,
    work_lineage_id TEXT,
    native_thread_id TEXT,
    native_thread_observed_at TEXT,
    claimed_role_task_ids_json TEXT NOT NULL DEFAULT '[]',
    chat_message_ids_json TEXT NOT NULL DEFAULT '[]',
    review_routing_authorities_json TEXT NOT NULL DEFAULT '[]',
    initial_release_decision_deliverable_ids_json TEXT NOT NULL DEFAULT '[]',
    mcp_capability_receipt_json TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    preparation_expires_at TEXT,
    queued_at TEXT,
    requested_lease_seconds INTEGER NOT NULL DEFAULT 900,
    activation_completed_at TEXT,
    session_metadata_mirrored_at TEXT,
    role_description_fingerprint TEXT,
    wake_work_fingerprint TEXT,
    wake_work_record_fingerprints_json TEXT NOT NULL DEFAULT '{}',
    action_feedback_dispatch_ids_json TEXT NOT NULL DEFAULT '[]',
    source_evidence_snapshot_ids_json TEXT NOT NULL DEFAULT '[]',
    prepared_at TEXT,
    leased_at TEXT,
    lease_expires_at TEXT,
    completed_at TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_team_dispatches_agent_status
    ON team_dispatches(agent_id, status, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_dispatches_session_status
    ON team_dispatches(session_id, status, created_at, id);


CREATE TABLE IF NOT EXISTS team_dispatch_tasks (
    dispatch_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    task_id TEXT NOT NULL,
    PRIMARY KEY(dispatch_id, position)
);
CREATE TABLE IF NOT EXISTS team_dispatch_blockers (
    dispatch_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    blocker_id TEXT NOT NULL,
    PRIMARY KEY(dispatch_id, position)
);
CREATE TABLE IF NOT EXISTS team_dispatch_reviews (
    dispatch_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    review_request_id TEXT NOT NULL,
    PRIMARY KEY(dispatch_id, position)
);
CREATE TABLE IF NOT EXISTS team_dispatch_inbox (
    dispatch_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    inbox_item_id TEXT NOT NULL,
    PRIMARY KEY(dispatch_id, position)
);
CREATE TABLE IF NOT EXISTS team_dispatch_projects (
    dispatch_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    PRIMARY KEY(dispatch_id, position)
);
CREATE TABLE IF NOT EXISTS team_dispatch_repositories (
    dispatch_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    managed_repo_id TEXT NOT NULL,
    directory_path TEXT NOT NULL DEFAULT 'root',
    PRIMARY KEY(dispatch_id, position)
);

CREATE TABLE IF NOT EXISTS team_worktree_cleanup_candidates (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    git_work_context_id TEXT NOT NULL,
    managed_repo_id TEXT NOT NULL,
    worktree_path TEXT,
    state TEXT NOT NULL,
    eligible_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_worktree_cleanup_state_eligible
    ON team_worktree_cleanup_candidates(state, eligible_at, id);

CREATE TABLE IF NOT EXISTS team_git_recovery_incidents (
    id TEXT PRIMARY KEY,
    blocker_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    state TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_git_recovery_task_state ON team_git_recovery_incidents(task_id, state);
CREATE TABLE IF NOT EXISTS team_git_preparation_attempts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_git_preparation_attempts_task_state
    ON team_git_preparation_attempts(task_id, state, updated_at, id);

CREATE TABLE IF NOT EXISTS team_ephemeral_assignments (
    id TEXT PRIMARY KEY,
    parent_agent_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    state TEXT NOT NULL,
    agent_branch TEXT,
    commit_sha TEXT,
    summary TEXT,
    error TEXT,
    model TEXT,
    agent_provider TEXT,
    execution_backend TEXT,
    container_image TEXT,
    ttl_hours INTEGER,
    directory_path TEXT,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_ephemeral_assignments_parent_state
    ON team_ephemeral_assignments(parent_agent_id, state, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_ephemeral_assignments_session
    ON team_ephemeral_assignments(session_id);
CREATE INDEX IF NOT EXISTS idx_team_ephemeral_assignments_project_task
    ON team_ephemeral_assignments(project_id, task_id, state, id);

CREATE TABLE IF NOT EXISTS team_ephemeral_assignment_artifacts (
    assignment_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    artifact_ref TEXT NOT NULL,
    PRIMARY KEY(assignment_id, position)
);

CREATE TABLE IF NOT EXISTS team_ephemeral_assignment_allowed_tools (
    assignment_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    tool TEXT NOT NULL,
    PRIMARY KEY(assignment_id, position)
);

CREATE TABLE IF NOT EXISTS team_git_promotions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    actor_agent_id TEXT NOT NULL,
    source_branch TEXT NOT NULL,
    sources_json TEXT NOT NULL DEFAULT '[]',
    target_branch TEXT NOT NULL,
    promotion_kind TEXT NOT NULL,
    task_id TEXT,
    deliverable_id TEXT,
    release_package_id TEXT,
    git_base_branch TEXT,
    base_commit_sha TEXT,
    state TEXT NOT NULL,
    commit_sha TEXT,
    outputs_json TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_git_promotions_project_state
    ON team_git_promotions(project_id, state, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_git_promotions_deliverable
    ON team_git_promotions(deliverable_id, promotion_kind, state, id);
CREATE INDEX IF NOT EXISTS idx_team_git_promotions_release
    ON team_git_promotions(release_package_id, promotion_kind, state, id);

CREATE TABLE IF NOT EXISTS team_dependency_composition_recoveries (
    id TEXT PRIMARY KEY,
    blocker_id TEXT NOT NULL,
    target_task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    managed_repo_id TEXT NOT NULL,
    directory_path TEXT NOT NULL,
    base_branch TEXT NOT NULL,
    base_commit_sha TEXT NOT NULL,
    base_tree_hash TEXT NOT NULL,
    dependency_git_sources_json TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    preparation_generation TEXT NOT NULL,
    failed_dependency_task_id TEXT NOT NULL,
    failed_deliverable_id TEXT NOT NULL,
    conflict_paths_json TEXT NOT NULL DEFAULT '[]',
    recovery_task_id TEXT,
    verified_deliverable_id TEXT,
    verified_commit_sha TEXT,
    verified_at TEXT,
    accepted_deliverable_id TEXT,
    resolved_commit_sha TEXT,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_team_dependency_composition_recovery_source
    ON team_dependency_composition_recoveries(
        target_task_id,
        source_fingerprint,
        preparation_generation
    );
CREATE INDEX IF NOT EXISTS idx_team_dependency_composition_recovery_blocker
    ON team_dependency_composition_recoveries(blocker_id, state, id);
CREATE INDEX IF NOT EXISTS idx_team_dependency_composition_recovery_task
    ON team_dependency_composition_recoveries(recovery_task_id, state, id);

CREATE TABLE IF NOT EXISTS team_git_work_contexts (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    dispatch_id TEXT,
    worktree_path TEXT,
    base_branch TEXT,
    base_commit TEXT,
    agent_branch TEXT NOT NULL,
    commit_sha TEXT,
    dependency_git_sources_json TEXT NOT NULL DEFAULT '[]',
    dependency_composition_recovery_id TEXT,
    manual_dependency_git_sources_json TEXT NOT NULL DEFAULT '[]',
    dependency_composition_manifest_json TEXT,
    preparation_generation TEXT,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_git_work_contexts_agent_task
    ON team_git_work_contexts(agent_id, task_id, state, id);
CREATE INDEX IF NOT EXISTS idx_team_git_work_contexts_dispatch
    ON team_git_work_contexts(dispatch_id, state, id);
CREATE INDEX IF NOT EXISTS idx_team_git_work_contexts_branch
    ON team_git_work_contexts(agent_branch, state, id);

CREATE TABLE IF NOT EXISTS team_dispatch_activity (
    id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    caller_session_id TEXT NOT NULL,
    caller_identity TEXT NOT NULL,
    agent_id TEXT,
    governance_group_id TEXT,
    assignment_id TEXT,
    dispatch_id TEXT,
    request_id TEXT,
    correlation_id TEXT,
    causation_id TEXT,
    status TEXT NOT NULL,
    progress_classification TEXT NOT NULL,
    action_record_id TEXT,
    audit_record_id TEXT,
    error_code TEXT,
    error_message TEXT,
    correction_context_summary TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_dispatch_activity_dispatch
    ON team_dispatch_activity(dispatch_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_dispatch_activity_caller
    ON team_dispatch_activity(caller_identity, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_dispatch_activity_session
    ON team_dispatch_activity(caller_session_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_dispatch_activity_request
    ON team_dispatch_activity(tool_name, caller_identity, dispatch_id, request_id);

CREATE TABLE IF NOT EXISTS team_idempotency_records (
    id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    caller_identity TEXT NOT NULL,
    scope_kind TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    normalized_payload_digest TEXT NOT NULL,
    response_payload_json TEXT NOT NULL,
    created_domain_ids_json TEXT NOT NULL,
    created_event_ids_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tool_name, caller_identity, scope_kind, scope_id, request_id)
);
CREATE INDEX IF NOT EXISTS idx_team_idempotency_records_scope
    ON team_idempotency_records(tool_name, caller_identity, scope_kind, scope_id, request_id);

CREATE TABLE IF NOT EXISTS team_outbox (
    id TEXT PRIMARY KEY,
    intent_type TEXT NOT NULL,
    aggregate_type TEXT,
    aggregate_id TEXT,
    idempotency_key TEXT,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    available_at TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    claimed_at TEXT,
    claimed_by TEXT,
    claim_token TEXT,
    claim_expires_at TEXT,
    completed_at TEXT,
    last_error_code TEXT,
    last_error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_team_outbox_idempotency_key
    ON team_outbox(idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_team_outbox_claimable
    ON team_outbox(status, available_at, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_outbox_claim_token
    ON team_outbox(claim_token, status, id);
CREATE INDEX IF NOT EXISTS idx_team_outbox_aggregate
    ON team_outbox(aggregate_type, aggregate_id, id);
"""
