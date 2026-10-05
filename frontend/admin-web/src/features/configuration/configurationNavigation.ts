export type ConfigurationField = {
  source_domain: string;
  field_name: string;
  label: string;
  kind: "bool" | "int" | "float" | "literal" | "text" | "multiline_text" | "string_list" | "json";
  options: string[];
  assistant_options?: { session_id: string; directory_path: string; name: string }[];
  session_options?: { session_id: string; directory_path: string; name: string }[];
  phone_route_options?: { route_id: string; did: string | null; called_number: string | null; directory_path: string }[];
  scope?: "directory" | "global";
  root_starts_editable: boolean;
  tooltip: string;
};

export type ConfigurationSection = {
  key: string;
  title: string;
  fields: ConfigurationField[];
};

export type ConfigurationTab = {
  key: string;
  title: string;
  help_text: string | null;
  underlying_domains: string[];
  sections: ConfigurationSection[];
};

export type ConfigurationUtilityKey =
  | "credentials"
  | "website-logins"
  | "teams-auth"
  | "model-pricing"
  | "classification"
  | "events"
  | "validation"
  | "directory-overrides"
  | "effective-config"
  | "parent-defaults";

export type ConfigurationUtility = {
  key: ConfigurationUtilityKey;
  label: string;
  description: string;
};

export type ConfigurationDestination = {
  key: string;
  label: string;
  description: string;
  kind: "section" | "utility";
  fieldCount?: number;
};

export type ConfigurationLocation = {
  tabKey: string;
  destinationKey: string;
  fieldName: string | null;
};

export type ConfigurationSearchResult = ConfigurationLocation & {
  id: string;
  kind: "area" | "section" | "field" | "utility";
  label: string;
  breadcrumb: string;
  description: string;
  technical: string | null;
};

const DEFAULT_CONFIGURATION_TAB_KEY = "people_communications";

const COMMON_UTILITIES: ReadonlyArray<ConfigurationUtility> = [
  {
    key: "validation",
    label: "Validation",
    description: "Review validation diagnostics for the selected configuration area.",
  },
  {
    key: "directory-overrides",
    label: "Directory override JSON",
    description: "Inspect and copy unified explicit overrides for the selected directory.",
  },
  {
    key: "effective-config",
    label: "Effective configuration",
    description: "Inspect resolved configuration after inheritance is applied.",
  },
  {
    key: "parent-defaults",
    label: "Parent defaults",
    description: "Inspect the inherited configuration baseline from the parent directory.",
  },
];

export function configurationUtilitiesForTab(tabKey: string): ReadonlyArray<ConfigurationUtility> {
  const specialized: ConfigurationUtility[] = [];
  if (tabKey === "automation_assignment") {
    specialized.push({ key: "events", label: "Events", description: "Manage inherited event schemas, manual forms, and workflow validation contracts." });
  }
  if (tabKey === "access_approvals_audit") {
    specialized.push({key:"credentials",label:"Credentials",description:"Manage encrypted Teams and connector credentials, ownership and expiry."});
    specialized.push({key: "website-logins", label: "Website logins", description: "Manage website profiles, encrypted credentials and login tests."});
  }
  if (tabKey === "people_communications") {
    specialized.push({
      key: "teams-auth",
      label: "Teams delegated auth",
      description: "Inspect and manage the host-side Microsoft Teams delegated sign-in.",
    });
  }
  if (tabKey === "models_assistant_knowledge") {
    specialized.push({key: "classification", label: "Classification", description: "Configure providers, reusable classification profiles, and sample evaluations."});
    specialized.push({
      key: "model-pricing",
      label: "Model pricing catalog",
      description: "Edit host-global pricing rules used for model cost estimates.",
    });
  }
  return [...specialized, ...COMMON_UTILITIES];
}

export function configurationDestinationsForTab(tab: ConfigurationTab): ConfigurationDestination[] {
  return [
    ...tab.sections.map((section) => ({
      key: section.key,
      label: section.title,
      description: `${section.fields.length} ${section.fields.length === 1 ? "setting" : "settings"}`,
      kind: "section" as const,
      fieldCount: section.fields.length,
    })),
    ...configurationUtilitiesForTab(tab.key).map((utility) => ({
      key: utility.key,
      label: utility.label,
      description: utility.description,
      kind: "utility" as const,
    })),
  ];
}

export function defaultConfigurationDestination(tab: ConfigurationTab): string {
  return tab.sections[0]?.key ?? configurationUtilitiesForTab(tab.key)[0]?.key ?? "validation";
}

export function configurationHash(
  tabKey: string,
  destinationKey: string,
  fieldName?: string | null,
): string {
  const query = new URLSearchParams({
    config_tab: tabKey,
    config_section: destinationKey,
  });
  if (fieldName?.trim()) query.set("config_field", fieldName.trim());
  return `#configuration?${query.toString()}`;
}

export function resolveConfigurationLocation(
  tabs: ReadonlyArray<ConfigurationTab>,
  hash: string,
): ConfigurationLocation | null {
  if (!tabs.length) return null;
  const queryStart = hash.indexOf("?");
  const query = new URLSearchParams(queryStart >= 0 ? hash.slice(queryStart + 1) : "");
  const requestedTab = query.get("config_tab")?.trim() ?? "";
  const tab = tabs.find((candidate) => candidate.key === requestedTab)
    ?? tabs.find((candidate) => candidate.key === DEFAULT_CONFIGURATION_TAB_KEY)
    ?? tabs[0];
  const destinations = configurationDestinationsForTab(tab);
  const requestedDestination = query.get("config_section")?.trim() ?? "";
  const destination = destinations.find((candidate) => candidate.key === requestedDestination)
    ?? destinations.find((candidate) => candidate.kind === "section")
    ?? destinations[0];
  if (!destination) return null;
  const requestedField = query.get("config_field")?.trim() ?? "";
  const section = destination.kind === "section"
    ? tab.sections.find((candidate) => candidate.key === destination.key)
    : null;
  const fieldName = section?.fields.some((field) => field.field_name === requestedField)
    ? requestedField
    : null;
  return { tabKey: tab.key, destinationKey: destination.key, fieldName };
}

function normalizedSearchText(value: string): string {
  return value
    .normalize("NFKD")
    .toLocaleLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function searchScore(query: string, label: string, friendlyText: string, technicalText: string): number | null {
  const normalizedQuery = normalizedSearchText(query);
  if (!normalizedQuery) return null;
  const tokens = normalizedQuery.split(/\s+/).filter(Boolean);
  const normalizedLabel = normalizedSearchText(label);
  const normalizedFriendly = normalizedSearchText(friendlyText);
  const normalizedTechnical = normalizedSearchText(technicalText);
  const combined = `${normalizedFriendly} ${normalizedTechnical}`;
  if (!tokens.every((token) => combined.includes(token))) return null;
  if (normalizedLabel === normalizedQuery) return 0;
  if (normalizedLabel.startsWith(normalizedQuery)) return 10;
  if (tokens.every((token) => normalizedLabel.includes(token))) return 20;
  if (tokens.every((token) => normalizedFriendly.includes(token))) return 30;
  return 40;
}

export function searchConfiguration(
  tabs: ReadonlyArray<ConfigurationTab>,
  query: string,
): ConfigurationSearchResult[] {
  const matches: Array<ConfigurationSearchResult & { score: number; order: number }> = [];
  let order = 0;
  for (const tab of tabs) {
    const firstDestination = defaultConfigurationDestination(tab);
    const areaScore = searchScore(query, tab.title, `${tab.title} ${tab.help_text ?? ""}`, tab.key);
    if (areaScore !== null) {
      matches.push({
        id: `area:${tab.key}`,
        kind: "area",
        tabKey: tab.key,
        destinationKey: firstDestination,
        fieldName: null,
        label: tab.title,
        breadcrumb: "Configuration area",
        description: tab.help_text ?? "Configuration controls for this area.",
        technical: tab.key,
        score: areaScore,
        order: order++,
      });
    }
    for (const section of tab.sections) {
      const sectionScore = searchScore(
        query,
        section.title,
        `${tab.title} ${tab.help_text ?? ""} ${section.title}`,
        `${tab.key} ${section.key}`,
      );
      if (sectionScore !== null) {
        matches.push({
          id: `section:${tab.key}:${section.key}`,
          kind: "section",
          tabKey: tab.key,
          destinationKey: section.key,
          fieldName: null,
          label: section.title,
          breadcrumb: tab.title,
          description: `${section.fields.length} ${section.fields.length === 1 ? "setting" : "settings"}`,
          technical: section.key,
          score: sectionScore,
          order: order++,
        });
      }
      for (const field of section.fields) {
        const fieldScore = searchScore(
          query,
          field.label,
          `${tab.title} ${tab.help_text ?? ""} ${section.title} ${field.label} ${field.tooltip}`,
          `${tab.key} ${section.key} ${field.source_domain} ${field.field_name}`,
        );
        if (fieldScore === null) continue;
        matches.push({
          id: `field:${tab.key}:${section.key}:${field.field_name}`,
          kind: "field",
          tabKey: tab.key,
          destinationKey: section.key,
          fieldName: field.field_name,
          label: field.label,
          breadcrumb: `${tab.title} › ${section.title}`,
          description: field.tooltip || "Configuration setting.",
          technical: `${field.source_domain}.${field.field_name}`,
          score: fieldScore,
          order: order++,
        });
      }
    }
    for (const utility of configurationUtilitiesForTab(tab.key)) {
      const utilityScore = searchScore(
        query,
        utility.label,
        `${tab.title} ${tab.help_text ?? ""} ${utility.label} ${utility.description}`,
        `${tab.key} ${utility.key}`,
      );
      if (utilityScore === null) continue;
      matches.push({
        id: `utility:${tab.key}:${utility.key}`,
        kind: "utility",
        tabKey: tab.key,
        destinationKey: utility.key,
        fieldName: null,
        label: utility.label,
        breadcrumb: `${tab.title} › Utilities`,
        description: utility.description,
        technical: utility.key,
        score: utilityScore,
        order: order++,
      });
    }
  }
  return matches
    .sort((left, right) => left.score - right.score || left.order - right.order || left.label.localeCompare(right.label))
    .slice(0, 50)
    .map(({ score: _score, order: _order, ...result }) => result);
}

export function configurationFieldDomId(tabKey: string, fieldName: string): string {
  return `configuration-field-${tabKey}-${fieldName}`.replace(/[^a-zA-Z0-9_-]/g, "-");
}
