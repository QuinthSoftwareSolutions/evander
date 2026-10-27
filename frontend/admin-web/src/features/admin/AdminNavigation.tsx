import {
  BarChart3,
  Bot,
  ChevronDown,
  Cpu,
  CreditCard,
  Eye,
  FolderOpen,
  GitFork,
  Laptop2,
  LayoutDashboard,
  PackageOpen,
  Plug,
  Puzzle,
  ScrollText,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  SquareTerminal,
  Ticket,
  Users,
  Workflow,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";

import {
  ADMIN_NAVIGATION_GROUPS,
  TABS,
  type AdminNavigationGroup,
  type AdminTab,
  type AdminTabDefinition,
  type BuiltinAdminTab,
} from "../../adminRouting";
import { Tooltip } from "./Tooltip";

const TAB_ICONS: Readonly<Record<BuiltinAdminTab, LucideIcon>> = {
  dashboard: LayoutDashboard,
  reports: BarChart3,
  agents: Bot,
  subscriptions: CreditCard,
  sessions: SquareTerminal,
  issues: Ticket,
  approvals: ShieldCheck,
  artifacts: PackageOpen,
  workflow: Workflow,
  starters: Sparkles,
  skills: Sparkles,
  addons: Puzzle,
  connectors: Plug,
  "host-directories": FolderOpen,
  "git-repositories": GitFork,
  vms: Laptop2,
  workers: Cpu,
  "tool-visibility": Eye,
  "mcp-tools": Wrench,
  "tool-calls": ScrollText,
  configuration: Settings2,
  privacy: ShieldCheck,
  users: Users,
};

const GROUP_LABELS = Object.fromEntries(
  ADMIN_NAVIGATION_GROUPS.map((group) => [group.key, group.label]),
) as Record<AdminNavigationGroup, string>;

const COMPACT_GROUP_LABELS: Record<AdminNavigationGroup, string> = {
  overview: "Overview",
  operations: "Ops",
  automation: "Auto",
  extensions: "Ext",
  infrastructure: "Infra",
  governance: "Gov",
};

const COLLAPSED_GROUPS_STORAGE_KEY = "admin-web:navigation:collapsed-groups";

function groupForTab(tabs: ReadonlyArray<AdminTabDefinition>, tab: AdminTab): AdminNavigationGroup | undefined {
  return tabs.find((item) => item.key === tab)?.group
    ?? TABS.find((item) => item.key === tab)?.group
    ?? (tab.startsWith("addon:") ? "extensions" : undefined);
}

function readCollapsedGroups(): Set<AdminNavigationGroup> | null {
  try {
    const stored = window.localStorage.getItem(COLLAPSED_GROUPS_STORAGE_KEY);
    if (stored !== null) {
      const parsed: unknown = JSON.parse(stored);
      if (Array.isArray(parsed) && parsed.every((value) => typeof value === "string")) {
        return new Set(ADMIN_NAVIGATION_GROUPS.filter((group) => parsed.includes(group.key)).map((group) => group.key));
      }
    }
  } catch {
    // Storage can be unavailable or contain an invalid value; use the defaults.
  }
  return null;
}

function isPageReload(): boolean {
  try {
    const navigation = window.performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
    return navigation?.type === "reload";
  } catch {
    return false;
  }
}

function initialCollapsedGroups(
  tabs: ReadonlyArray<AdminTabDefinition>,
  routeTab: AdminTab,
  pageReload: boolean,
): Set<AdminNavigationGroup> {
  const stored = readCollapsedGroups();
  const groups = stored ?? new Set<AdminNavigationGroup>(["extensions"]);
  const activeGroup = groupForTab(tabs, routeTab);
  if (activeGroup && (!pageReload || stored === null)) groups.delete(activeGroup);
  return groups;
}

type AdminNavigationProps = {
  tabs: ReadonlyArray<AdminTabDefinition>;
  activeTab: AdminTab;
  routeTab: AdminTab;
  collapsed: boolean;
  disabled: boolean;
  onToggleCollapsed: () => void;
  onNavigate: (tab: AdminTab) => void;
};

function iconForTab(tab: AdminTabDefinition): LucideIcon {
  return tab.key.startsWith("addon:") ? Puzzle : TAB_ICONS[tab.key as BuiltinAdminTab];
}

function tabMatchesQuery(tab: AdminTabDefinition, query: string): boolean {
  const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return true;
  const haystack = [tab.label, tab.shortLabel, tab.description, GROUP_LABELS[tab.group]]
    .join(" ")
    .toLocaleLowerCase();
  return terms.every((term) => haystack.includes(term));
}

export function AdminNavigation({
  tabs,
  activeTab,
  routeTab,
  collapsed,
  disabled,
  onToggleCollapsed,
  onNavigate,
}: AdminNavigationProps) {
  const [quickNavigatorOpen, setQuickNavigatorOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [pageReload] = useState(isPageReload);
  const [collapsedGroups, setCollapsedGroups] = useState(() => initialCollapsedGroups(tabs, routeTab, pageReload));
  const previousRouteTabRef = useRef<AdminTab | null>(pageReload ? routeTab : null);
  const storageOutOfSyncRef = useRef(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const dialogRef = useRef<HTMLElement | null>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const filteredTabs = useMemo(
    () => tabs.filter((tab) => tabMatchesQuery(tab, query)),
    [query, tabs],
  );

  useEffect(() => {
    if (previousRouteTabRef.current === routeTab) return;
    previousRouteTabRef.current = routeTab;
    const group = groupForTab(tabs, routeTab);
    if (group) setGroupCollapsed(group, false);
  }, [routeTab, tabs]);

  useEffect(() => {
    function handleStorage(event: StorageEvent) {
      if (event.key !== COLLAPSED_GROUPS_STORAGE_KEY && event.key !== null) return;
      if (storageOutOfSyncRef.current) return;
      setCollapsedGroups(readCollapsedGroups() ?? new Set<AdminNavigationGroup>(["extensions"]));
    }
    window.addEventListener("storage", handleStorage);
    return () => window.removeEventListener("storage", handleStorage);
  }, []);

  function setGroupCollapsed(group: AdminNavigationGroup, shouldCollapse: boolean) {
    const current = storageOutOfSyncRef.current ? collapsedGroups : readCollapsedGroups() ?? collapsedGroups;
    if (current.has(group) === shouldCollapse) {
      if (current !== collapsedGroups) setCollapsedGroups(current);
      return;
    }
    const next = new Set(current);
    if (shouldCollapse) next.add(group);
    else next.delete(group);
    setCollapsedGroups(next);
    try {
      window.localStorage.setItem(COLLAPSED_GROUPS_STORAGE_KEY, JSON.stringify([...next]));
      storageOutOfSyncRef.current = false;
    } catch {
      // Keep using in-memory choices until they can be saved successfully.
      storageOutOfSyncRef.current = true;
    }
  }

  function toggleGroup(group: AdminNavigationGroup) {
    setGroupCollapsed(group, !collapsedGroups.has(group));
  }

  function openQuickNavigator() {
    previousFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    setQuery("");
    setSelectedIndex(0);
    setQuickNavigatorOpen(true);
  }

  function closeQuickNavigator() {
    setQuickNavigatorOpen(false);
    window.requestAnimationFrame(() => previousFocusRef.current?.focus());
  }

  function selectTab(tab: AdminTabDefinition) {
    onNavigate(tab.key);
    closeQuickNavigator();
  }

  function handleDialogKeydown(event: ReactKeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeQuickNavigator();
      return;
    }
    if (event.key !== "Tab" || !dialogRef.current) return;
    const focusable = Array.from(
      dialogRef.current.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), [tabindex]:not([tabindex="-1"])'),
    );
    if (focusable.length === 0) return;
    const currentIndex = focusable.indexOf(document.activeElement as HTMLElement);
    const nextIndex = event.shiftKey
      ? (currentIndex <= 0 ? focusable.length - 1 : currentIndex - 1)
      : (currentIndex === focusable.length - 1 ? 0 : currentIndex + 1);
    event.preventDefault();
    focusable[nextIndex]?.focus();
  }

  useEffect(() => {
    if (selectedIndex < filteredTabs.length) return;
    setSelectedIndex(Math.max(0, filteredTabs.length - 1));
  }, [filteredTabs.length, selectedIndex]);

  useEffect(() => {
    if (!quickNavigatorOpen) return;
    inputRef.current?.focus();
  }, [quickNavigatorOpen]);

  useEffect(() => {
    if (!quickNavigatorOpen) return;
    dialogRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }, [quickNavigatorOpen, selectedIndex, filteredTabs]);

  useEffect(() => {
    function handleKeydown(event: KeyboardEvent) {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLocaleLowerCase() !== "k") {
        return;
      }
      if (!quickNavigatorOpen && document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) {
        return;
      }
      event.preventDefault();
      if (quickNavigatorOpen) {
        inputRef.current?.focus();
        return;
      }
      openQuickNavigator();
    }
    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, [quickNavigatorOpen]);

  return (
    <>
      <nav className={`admin-shell-nav${collapsed ? " collapsed" : ""}`} aria-label="Admin sections">
        <div className="admin-shell-nav-header">
          <div className="admin-shell-nav-header-row">
            <div className="section-label">Sections</div>
            <Tooltip content={collapsed ? "Expand the left navigation sections." : "Collapse the left navigation sections."}>
              <button
                type="button"
                className="admin-shell-nav-toggle"
                onClick={onToggleCollapsed}
                disabled={disabled}
                aria-label={collapsed ? "Expand sections" : "Collapse sections"}
              >
                <span aria-hidden="true">≡</span>
              </button>
            </Tooltip>
          </div>
          <p>Operational views for machines, policy, reviews, and workflow state.</p>
        </div>

        <Tooltip content="Find an Admin Web page. Shortcut: Ctrl+K or Command+K." inline={false}>
          <button
            type="button"
            className="admin-shell-nav-find"
            onClick={openQuickNavigator}
            disabled={disabled}
            aria-label="Find page"
          >
            <Search size={17} strokeWidth={1.9} aria-hidden="true" />
            <span className="admin-shell-nav-find-full">Find page</span>
            <span className="admin-shell-nav-find-short">Find</span>
            <kbd>Ctrl K</kbd>
          </button>
        </Tooltip>

        <div className="admin-shell-nav-groups">
          {ADMIN_NAVIGATION_GROUPS.map((group) => {
            const groupTabs = tabs.filter((tab) => tab.group === group.key);
            if (groupTabs.length === 0) return null;
            const groupCollapsed = collapsedGroups.has(group.key);
            return (
              <section
                className="admin-shell-nav-group"
                aria-labelledby={`admin-navigation-group-${group.key}`}
                key={group.key}
              >
                <h2 className="admin-shell-nav-group-label section-label" id={`admin-navigation-group-${group.key}`}>
                  <button
                    type="button"
                    className="admin-shell-nav-group-toggle"
                    aria-label={group.label}
                    aria-expanded={!groupCollapsed}
                    aria-controls={`admin-navigation-group-items-${group.key}`}
                    onClick={() => toggleGroup(group.key)}
                  >
                    <span className="admin-shell-nav-group-label-full">{group.label}</span>
                    <span className="admin-shell-nav-group-label-compact" aria-hidden="true">{COMPACT_GROUP_LABELS[group.key]}</span>
                    <ChevronDown size={14} strokeWidth={2} aria-hidden="true" />
                  </button>
                </h2>
                <div className="admin-shell-nav-group-items" id={`admin-navigation-group-items-${group.key}`} hidden={groupCollapsed}>
                  {groupTabs.map((tab) => {
                    const Icon = iconForTab(tab);
                    return (
                      <Tooltip key={tab.key} content={tab.description} inline={false}>
                        <button
                          type="button"
                          className={`admin-shell-tab ${tab.key === activeTab ? "active" : ""}`}
                          onClick={() => {
                            if (tab.key !== activeTab) onNavigate(tab.key);
                          }}
                          aria-current={tab.key === activeTab ? "page" : undefined}
                        >
                          <span className="admin-shell-tab-icon" aria-hidden="true">
                            <Icon size={17} strokeWidth={1.9} />
                          </span>
                          <span className="admin-shell-tab-label admin-shell-tab-label-full">{tab.label}</span>
                          <span className="admin-shell-tab-label admin-shell-tab-label-short">{tab.shortLabel}</span>
                        </button>
                      </Tooltip>
                    );
                  })}
                </div>
              </section>
            );
          })}
        </div>
      </nav>

      {quickNavigatorOpen ? (
        <div className="modal-backdrop admin-quick-nav-backdrop" onClick={closeQuickNavigator} role="presentation">
          <section
            ref={dialogRef}
            className="modal-card admin-quick-nav"
            role="dialog"
            aria-modal="true"
            aria-labelledby="admin-quick-nav-title"
            onClick={(event) => event.stopPropagation()}
            onKeyDown={handleDialogKeydown}
          >
            <div className="admin-quick-nav-header">
              <div>
                <div className="eyebrow">Navigation</div>
                <h2 id="admin-quick-nav-title">Find a page</h2>
              </div>
              <button
                type="button"
                className="admin-quick-nav-close"
                onClick={closeQuickNavigator}
                aria-label="Close page finder"
              >
                <X size={18} strokeWidth={2} aria-hidden="true" />
              </button>
            </div>
            <label className="admin-quick-nav-search">
              <Search size={18} strokeWidth={1.9} aria-hidden="true" />
              <input
                ref={inputRef}
                type="search"
                value={query}
                placeholder="Search pages and sections"
                aria-label="Search pages"
                aria-controls="admin-quick-nav-results"
                aria-activedescendant={filteredTabs[selectedIndex] ? `admin-quick-nav-option-${filteredTabs[selectedIndex].key}` : undefined}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setSelectedIndex(0);
                }}
                onKeyDown={(event) => {
                  if (event.key === "ArrowDown" && filteredTabs.length > 0) {
                    event.preventDefault();
                    setSelectedIndex((current) => (current + 1) % filteredTabs.length);
                    return;
                  }
                  if (event.key === "ArrowUp" && filteredTabs.length > 0) {
                    event.preventDefault();
                    setSelectedIndex((current) => (current - 1 + filteredTabs.length) % filteredTabs.length);
                    return;
                  }
                  if (event.key === "Enter" && filteredTabs[selectedIndex]) {
                    event.preventDefault();
                    selectTab(filteredTabs[selectedIndex]);
                  }
                }}
              />
              <kbd>Esc</kbd>
            </label>
            <div className="admin-quick-nav-results" id="admin-quick-nav-results" role="listbox" aria-label="Admin pages">
              {filteredTabs.map((tab, index) => {
                const Icon = iconForTab(tab);
                return (
                  <button
                    type="button"
                    id={`admin-quick-nav-option-${tab.key}`}
                    role="option"
                    aria-selected={index === selectedIndex}
                    className={`admin-quick-nav-option${index === selectedIndex ? " selected" : ""}`}
                    key={tab.key}
                    onMouseEnter={() => setSelectedIndex(index)}
                    onFocus={() => setSelectedIndex(index)}
                    onClick={() => selectTab(tab)}
                  >
                    <span className="admin-quick-nav-option-icon" aria-hidden="true">
                      <Icon size={19} strokeWidth={1.9} />
                    </span>
                    <span className="admin-quick-nav-option-copy">
                      <strong>{tab.label}</strong>
                      <small>{tab.description}</small>
                    </span>
                    <span className="admin-quick-nav-option-group">{GROUP_LABELS[tab.group]}</span>
                  </button>
                );
              })}
              {filteredTabs.length === 0 ? (
                <div className="admin-quick-nav-empty">No pages match “{query.trim()}”.</div>
              ) : null}
            </div>
            <div className="admin-quick-nav-footer">
              <span><kbd>↑</kbd><kbd>↓</kbd> Move</span>
              <span><kbd>Enter</kbd> Open</span>
            </div>
          </section>
        </div>
      ) : null}
    </>
  );
}
