export const defaultTeamView = "people";
export const teamViewStorageKey = "team-addon:active-view";

const teamViewIds = new Set([
  "workspace",
  "operations",
  "delivery",
  "memory",
  "projects",
  "task-board",
  "cases",
  "people",
  "chat",
  "mail",
  "activity",
  "governance",
  "planning",
  "timeline",
  "org",
]);

export function emptyState() {
  return {
    revision: 0,
    teams: {},
    governance_groups: {},
    governance_agenda: {},
    governance_runtimes: {},
    projects: {},
    roles: {},
    agents: {},
    project_assignments: {},
    agent_runtimes: {},
    cases: {},
    tasks: {},
    deliverables: {},
    review_requests: {},
    outcome_kind_definitions: {},
    review_profiles: {},
    inbox: {},
    blockers: {},
    blocker_events: {},
    dependency_composition_recoveries: {},
    action_records: {},
    release_packages: {},
    dispatches: {},
    worktree_cleanup_candidates: {},
    git_work_contexts: {},
    ephemeral_assignments: {},
    git_promotions: {},
    stand_downs: {},
    runtime_operations: {
      generated_at: null,
      agents: [],
      attention: [],
    },
    resource_tokens: {},
    available_role_permissions: [],
  };
}

export function emptyTimelineState() {
  return {
    revision: 0,
    derived: true,
    audit_log: false,
    source: "derived_from_current_team_state",
    description:
      "Activity reconstructed from records retained in the current Team snapshot; this is not an audit log.",
    filters: {},
    timestampless_count: 0,
    total_count: 0,
    limit: 10,
    offset: 0,
    has_more: false,
    facets: {
      types: [],
      statuses: [],
    },
    entries: [],
  };
}

export function emptyCostState() {
  return {
    generated_at: null,
    coverage_status: "partial",
    coverage_reasons: ["costs_not_loaded"],
    team_costs: {},
    governance_group_costs: {},
    project_costs: {},
    unattributed_costs: null,
    source_counts: {},
    events_count: 0,
    priced_events_count: 0,
    unpriced_events_count: 0,
    backfill: null,
    error: null,
  };
}

export function emptyChatState() {
  return {
    revision: 0,
    selected_conversation_id: null,
    current_operator: null,
    selected_operator: null,
    can_view_all_operators: false,
    read_only: false,
    operator_options: [],
    eligible_agents: [],
    conversations: [],
    loading: false,
    error: null,
  };
}

export function normalizedTeamView(view) {
  return teamViewIds.has(view) ? view : defaultTeamView;
}

export function readStoredTeamView() {
  try {
    const storedView = window.localStorage.getItem(teamViewStorageKey);
    const normalizedView = normalizedTeamView(storedView);
    if (storedView && storedView !== normalizedView) {
      window.localStorage.setItem(teamViewStorageKey, normalizedView);
    }
    return normalizedView;
  } catch {
    return defaultTeamView;
  }
}

export function readAddonDeepLinkParams() {
  const params = new URLSearchParams(window.location.search);
  const hash = String(window.location.hash || "");
  const hashQueryIndex = hash.indexOf("?");
  if (hashQueryIndex >= 0) {
    const hashParams = new URLSearchParams(hash.slice(hashQueryIndex + 1));
    hashParams.forEach((value, key) => {
      if (!params.has(key)) {
        params.set(key, value);
      }
    });
  }
  return params;
}

export function readHumanReviewDeepLink() {
  const params = readAddonDeepLinkParams();
  const enabled = params.get("view") === "my-human-reviews";
  return {
    enabled,
    requestId: enabled
      ? String(params.get("human_review_request_id") || "").trim()
      : "",
  };
}

export function readChatDeepLink() {
  const params = readAddonDeepLinkParams();
  const enabled = params.get("view") === "chat";
  return {
    enabled,
    conversationId: enabled
      ? String(params.get("conversation_id") || "").trim()
      : "",
  };
}

export function storeTeamView(view) {
  try {
    window.localStorage.setItem(teamViewStorageKey, normalizedTeamView(view));
  } catch {
    // Storage can be unavailable in hardened browser contexts.
  }
}
