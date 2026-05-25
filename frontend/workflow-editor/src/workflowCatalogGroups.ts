import type { WorkflowEditorNodeCatalogEntry, WorkflowNodeKind } from "./types";

export type WorkflowNodeCatalogSubgroup = {
  key: string;
  label: string;
  description: string;
  entries: WorkflowEditorNodeCatalogEntry[];
};

type WorkflowNodeCatalogSubgroupDefinition = {
  key: string;
  label: string;
  description: string;
  types?: readonly string[];
  prefixes?: readonly string[];
};

const NODE_SUBGROUPS: Record<WorkflowNodeKind, readonly WorkflowNodeCatalogSubgroupDefinition[]> = {
  trigger: [
    {
      key: "scheduled",
      label: "Scheduled",
      description: "Start workflows from time-based schedules.",
      types: ["trigger:cron"],
    },
    {
      key: "platform",
      label: "Platform events",
      description: "Start from internal workflow events, webhook deliveries, agent lifecycle signals, or operator requests.",
      types: [
        "trigger:event",
        "trigger:http:webhook",
        "trigger:script-run-request",
        "trigger:agent:run:completed",
      ],
    },
    {
      key: "tickets-reviews",
      label: "Tickets & reviews",
      description: "Start from tickets, tracked issues, pull requests, or review requests.",
      prefixes: [
        "trigger:ticket:",
        "trigger:issue:",
        "trigger:pull-request:",
        "trigger:review:",
      ],
    },
    {
      key: "communications",
      label: "Communications",
      description: "Start from inbound chat, provider-neutral communication targets, Teams, Discord, or email activity.",
      prefixes: [
        "trigger:communication:",
        "trigger:discord:",
        "trigger:teams:",
        "trigger:email:",
      ],
    },
    {
      key: "git",
      label: "Git",
      description: "Start from repository or CI activity.",
      prefixes: ["trigger:git:", "trigger:ci:"],
    },
  ],
  gate: [
    {
      key: "filtering",
      label: "Filtering",
      description: "Filter or suppress activations before they continue.",
      types: ["gate:filter", "gate:dedupe"],
    },
    {
      key: "data-shaping",
      label: "Data shaping",
      description: "Map, parse, or serialize workflow payloads.",
      types: ["gate:map", "gate:deserialize", "gate:serialize"],
    },
    {
      key: "flow-control",
      label: "Flow control",
      description: "Join, branch, pause, or loop workflow paths.",
      types: ["gate:merge", "gate:switch", "gate:await-agent", "gate:for"],
    },
    {
      key: "timing",
      label: "Timing",
      description: "Delay forwarding until timing conditions are met.",
      types: ["gate:debounce"],
    },
  ],
  effect: [
    {
      key: "agents",
      label: "Agents",
      description: "Create, manage, prompt, or execute work inside agent sessions.",
      prefixes: ["effect:agent:"],
    },
    {
      key: "vms",
      label: "VMs",
      description: "Create, manage, bootstrap, or execute work inside libvirt VMs.",
      prefixes: ["effect:vm:"],
    },
    {
      key: "shell",
      label: "Shell & host scripts",
      description: "Run host-side shell scripts.",
      prefixes: ["effect:bash:"],
    },
    {
      key: "http",
      label: "HTTP",
      description: "Perform allowlisted host-side HTTP requests and expose upstream responses.",
      prefixes: ["effect:http:"],
    },
    {
      key: "semantic",
      label: "Semantic store",
      description: "Manage semantic collections and points.",
      prefixes: ["effect:semantic:"],
    },
    {
      key: "runtime-state",
      label: "Runtime state",
      description: "Read or modify graph-local workflow state.",
      prefixes: ["effect:state:"],
    },
    {
      key: "artifacts",
      label: "Artifacts",
      description: "Stage and publish agent artifacts.",
      prefixes: ["effect:artifact:"],
    },
    {
      key: "issues-sessions",
      label: "Issues & sessions",
      description: "Update tracked issues or session metadata.",
      prefixes: ["effect:issue:", "effect:ado:", "effect:session:"],
    },
    {
      key: "git-pr",
      label: "Git & PRs",
      description: "Promote branches or create pull requests.",
      prefixes: ["effect:git:", "effect:pr:", "effect:pull-request:"],
    },
    {
      key: "communications",
      label: "Communications",
      description: "Send messages through declared communication targets.",
      prefixes: ["effect:communication:"],
    },
    {
      key: "events-notifications",
      label: "Events & notifications",
      description: "Emit internal events or send operator notifications.",
      prefixes: ["effect:event:", "effect:notification:"],
    },
  ],
  query: [
    {
      key: "runtime",
      label: "Runtime state",
      description: "Read VM, session, agent, or workflow runtime state.",
      types: ["query:session-context"],
      prefixes: ["query:vm:", "query:agent:", "query:state:"],
    },
    {
      key: "work-tracking",
      label: "Work tracking",
      description: "Read tracked issues or Azure DevOps work items.",
      prefixes: ["query:issue:", "query:ado:"],
    },
    {
      key: "communications",
      label: "Communications",
      description: "Read messages from Teams or Discord communication targets.",
      prefixes: ["query:communication:"],
    },
    {
      key: "git-pr",
      label: "Git & PRs",
      description: "Read Git or pull request metadata.",
      prefixes: ["query:git:", "query:pull-request:"],
    },
    {
      key: "semantic",
      label: "Semantic store",
      description: "Read semantic store status, search results, or points.",
      prefixes: ["query:semantic:"],
    },
  ],
};

const NAMESPACE_LABELS: Record<string, string> = {
  ado: "ADO",
  agent: "Agent",
  artifact: "Artifact",
  bash: "Bash",
  cron: "Cron",
  communication: "Communication",
  discord: "Discord",
  email: "Email",
  event: "Event",
  git: "Git",
  issue: "Issue",
  notification: "Notification",
  pr: "PR",
  pull: "Pull",
  semantic: "Semantic",
  state: "State",
  session: "Session",
  teams: "Teams",
  ticket: "Ticket",
  vm: "VM",
};

export function compareWorkflowNodeCatalogEntries(
  left: WorkflowEditorNodeCatalogEntry,
  right: WorkflowEditorNodeCatalogEntry,
): number {
  return (
    left.label.localeCompare(right.label, undefined, { sensitivity: "base", numeric: true }) ||
    left.type.localeCompare(right.type, undefined, { sensitivity: "base", numeric: true })
  );
}

export function groupWorkflowNodeCatalogEntries(
  entries: ReadonlyArray<WorkflowEditorNodeCatalogEntry>,
  kind: WorkflowNodeKind,
): WorkflowNodeCatalogSubgroup[] {
  const definitions = NODE_SUBGROUPS[kind];
  const knownBuckets = new Map<string, WorkflowEditorNodeCatalogEntry[]>();
  const fallbackBuckets = new Map<string, WorkflowNodeCatalogSubgroup>();

  for (const entry of entries) {
    const definition = definitions.find((candidate) => subgroupMatches(candidate, entry.type));
    if (definition) {
      const bucket = knownBuckets.get(definition.key) ?? [];
      bucket.push(entry);
      knownBuckets.set(definition.key, bucket);
      continue;
    }

    const fallbackKey = fallbackSubgroupKey(entry.type, kind);
    const fallbackGroup = fallbackBuckets.get(fallbackKey) ?? {
      key: fallbackKey,
      label: fallbackSubgroupLabel(entry.type, kind),
      description: fallbackSubgroupDescription(entry.type, kind),
      entries: [],
    };
    fallbackGroup.entries.push(entry);
    fallbackBuckets.set(fallbackKey, fallbackGroup);
  }

  const knownGroups = definitions.flatMap((definition) => {
    const bucket = knownBuckets.get(definition.key);
    if (!bucket?.length) return [];
    return [{
      key: definition.key,
      label: definition.label,
      description: definition.description,
      entries: [...bucket].sort(compareWorkflowNodeCatalogEntries),
    }];
  });

  const fallbackGroups = [...fallbackBuckets.values()]
    .map((group) => ({
      ...group,
      entries: [...group.entries].sort(compareWorkflowNodeCatalogEntries),
    }))
    .sort((left, right) => left.label.localeCompare(right.label, undefined, { sensitivity: "base" }));

  return [...knownGroups, ...fallbackGroups];
}

function subgroupMatches(definition: WorkflowNodeCatalogSubgroupDefinition, type: string): boolean {
  return Boolean(
    definition.types?.includes(type) ||
    definition.prefixes?.some((prefix) => type.startsWith(prefix)),
  );
}

function fallbackSubgroupKey(type: string, kind: WorkflowNodeKind): string {
  return `${kind}:${nodeTypeNamespace(type, kind) ?? "other"}`;
}

function fallbackSubgroupLabel(type: string, kind: WorkflowNodeKind): string {
  return nodeTypeNamespace(type, kind)?.split(/[-_]/).filter(Boolean).map(namespacePartLabel).join(" ") ??
    `Other ${pluralNodeKind(kind)}`;
}

function fallbackSubgroupDescription(type: string, kind: WorkflowNodeKind): string {
  const namespace = nodeTypeNamespace(type, kind);
  if (!namespace) return `Additional ${pluralNodeKind(kind)}.`;
  return `Additional ${kind} node types in the ${fallbackSubgroupLabel(type, kind)} namespace.`;
}

function nodeTypeNamespace(type: string, kind: WorkflowNodeKind): string | null {
  const [typeKind, namespace] = type.split(":");
  if (typeKind !== kind || !namespace) return null;
  return namespace;
}

function namespacePartLabel(value: string): string {
  const normalized = value.toLowerCase();
  if (NAMESPACE_LABELS[normalized]) return NAMESPACE_LABELS[normalized];
  return `${normalized.slice(0, 1).toUpperCase()}${normalized.slice(1)}`;
}

function pluralNodeKind(kind: WorkflowNodeKind): string {
  if (kind === "query") return "queries";
  return `${kind}s`;
}
