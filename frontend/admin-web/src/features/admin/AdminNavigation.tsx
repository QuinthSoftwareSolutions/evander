import {
  BarChart3,
  Bot,
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

type AdminNavigationProps = {
  tabs: ReadonlyArray<AdminTabDefinition>;
  activeTab: AdminTab;
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
  collapsed,
  disabled,
  onToggleCollapsed,
  onNavigate,
}: AdminNavigationProps) {
  const [quickNavigatorOpen, setQuickNavigatorOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const dialogRef = useRef<HTMLElement | null>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const filteredTabs = useMemo(
    () => tabs.filter((tab) => tabMatchesQuery(tab, query)),
    [query, tabs],
  );

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
            return (
              <section
                className="admin-shell-nav-group"
                aria-labelledby={`admin-navigation-group-${group.key}`}
                key={group.key}
              >
                <h2 className="admin-shell-nav-group-label section-label" id={`admin-navigation-group-${group.key}`}>
                  {group.label}
                </h2>
                <div className="admin-shell-nav-group-items">
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
