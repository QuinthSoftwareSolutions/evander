import { Search, ChevronRight } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  configurationDestinationsForTab,
  configurationUtilitiesForTab,
  searchConfiguration,
  type ConfigurationSearchResult,
  type ConfigurationTab,
} from "./configurationNavigation";

type ConfigurationNavigatorProps = {
  tabs: ReadonlyArray<ConfigurationTab>;
  selectedTabKey: string;
  selectedDestinationKey: string;
  dirtyTabs: Readonly<Record<string, boolean>>;
  modelPricingDirty: boolean;
  disabled: boolean;
  onSelectArea: (tabKey: string) => void;
  onSelectDestination: (tabKey: string, destinationKey: string, fieldName?: string | null) => void;
};

const SEARCH_RESULTS_ID = "configuration-search-results";

export function ConfigurationNavigator({
  tabs,
  selectedTabKey,
  selectedDestinationKey,
  dirtyTabs,
  modelPricingDirty,
  disabled,
  onSelectArea,
  onSelectDestination,
}: ConfigurationNavigatorProps) {
  const [query, setQuery] = useState("");
  const [activeResultIndex, setActiveResultIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const selectedTab = tabs.find((tab) => tab.key === selectedTabKey) ?? tabs[0] ?? null;
  const destinations = selectedTab ? configurationDestinationsForTab(selectedTab) : [];
  const sectionKeys = new Set(selectedTab?.sections.map((section) => section.key) ?? []);
  const results = useMemo(() => searchConfiguration(tabs, query), [query, tabs]);
  const searchOpen = query.trim().length > 0;

  useEffect(() => {
    setActiveResultIndex(0);
  }, [query]);

  function chooseSearchResult(result: ConfigurationSearchResult) {
    onSelectDestination(result.tabKey, result.destinationKey, result.fieldName);
    setQuery("");
    inputRef.current?.focus();
  }

  function handleSearchKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (!searchOpen) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveResultIndex((current) => results.length ? (current + 1) % results.length : 0);
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveResultIndex((current) => results.length ? (current - 1 + results.length) % results.length : 0);
      return;
    }
    if (event.key === "Enter" && results[activeResultIndex]) {
      event.preventDefault();
      chooseSearchResult(results[activeResultIndex]);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      setQuery("");
    }
  }

  return (
    <>
      <label className="configuration-search">
        <span className="sr-only">Find a configuration setting</span>
        <Search aria-hidden="true" size={17} />
        <input
          ref={inputRef}
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={handleSearchKeyDown}
          placeholder="Find setting"
          role="combobox"
          aria-label="Find setting"
          aria-autocomplete="list"
          aria-expanded={searchOpen}
          aria-controls={SEARCH_RESULTS_ID}
          aria-activedescendant={
            searchOpen && results[activeResultIndex]
              ? `configuration-search-result-${activeResultIndex}`
              : undefined
          }
          disabled={disabled}
        />
      </label>

      {searchOpen ? (
        <div className="configuration-search-panel">
          <div className="configuration-search-summary">
            <strong>{results.length}</strong> {results.length === 1 ? "match" : "matches"}
          </div>
          <div id={SEARCH_RESULTS_ID} className="configuration-search-results" role="listbox">
            {results.map((result, index) => (
              <button
                key={result.id}
                id={`configuration-search-result-${index}`}
                type="button"
                role="option"
                aria-selected={index === activeResultIndex}
                className={`configuration-search-result${index === activeResultIndex ? " active" : ""}`}
                onMouseEnter={() => setActiveResultIndex(index)}
                onClick={() => chooseSearchResult(result)}
              >
                <span className="configuration-search-result-heading">
                  <strong>{result.label}</strong>
                  <span>{result.kind}</span>
                </span>
                <span className="configuration-search-result-breadcrumb">{result.breadcrumb}</span>
                <span className="configuration-search-result-description">{result.description}</span>
                {result.technical ? <code>{result.technical}</code> : null}
              </button>
            ))}
            {!results.length ? (
              <div className="empty-state configuration-search-empty">No configuration settings match “{query.trim()}”.</div>
            ) : null}
          </div>
        </div>
      ) : (
        <>
          <div className="configuration-mobile-selectors">
            <label className="field-stack">
              <span>Configuration area</span>
              <select
                value={selectedTab?.key ?? ""}
                onChange={(event) => onSelectArea(event.target.value)}
                disabled={disabled || !tabs.length}
              >
                {tabs.map((tab) => (
                  <option key={tab.key} value={tab.key}>{tab.title}</option>
                ))}
              </select>
            </label>
            <label className="field-stack">
              <span>Section or utility</span>
              <select
                value={selectedDestinationKey}
                onChange={(event) => onSelectDestination(selectedTab?.key ?? "", event.target.value)}
                disabled={disabled || !selectedTab}
              >
                {selectedTab?.sections.length ? <optgroup label="Settings">
                  {selectedTab.sections.map((section) => (
                    <option key={section.key} value={section.key}>{section.title}</option>
                  ))}
                </optgroup> : null}
                {selectedTab ? <optgroup label="Utilities">
                  {configurationUtilitiesForTab(selectedTab.key).map((utility) => (
                    <option key={utility.key} value={utility.key}>{utility.label}</option>
                  ))}
                </optgroup> : null}
              </select>
            </label>
          </div>

          <nav className="configuration-navigation" aria-label="Configuration areas and sections">
            <div className="configuration-area-list">
              {tabs.map((tab) => {
                const isActive = tab.key === selectedTab?.key;
                const isDirty = Boolean(dirtyTabs[tab.key])
                  || (tab.key === "models_assistant_knowledge" && modelPricingDirty);
                return (
                  <div key={tab.key} className={`configuration-area${isActive ? " active" : ""}`}>
                    <button
                      type="button"
                      className="configuration-area-button"
                      onClick={() => onSelectArea(tab.key)}
                      aria-expanded={isActive}
                      disabled={disabled}
                    >
                      <span>
                        <strong>{tab.title}</strong>
                        <small>{tab.sections.length} {tab.sections.length === 1 ? "section" : "sections"}</small>
                      </span>
                      {isDirty ? <span className="configuration-dirty-dot" title="Unsaved changes">Unsaved</span> : <ChevronRight aria-hidden="true" size={15} />}
                    </button>
                    {isActive ? (
                      <div className="configuration-destination-groups">
                        <div className="configuration-destination-group">
                          <div className="configuration-destination-heading">Settings</div>
                          {destinations.filter((destination) => sectionKeys.has(destination.key)).map((destination) => (
                            <button
                              key={destination.key}
                              type="button"
                              className={`configuration-destination${destination.key === selectedDestinationKey ? " selected" : ""}`}
                              onClick={() => onSelectDestination(tab.key, destination.key)}
                              aria-current={destination.key === selectedDestinationKey ? "page" : undefined}
                              disabled={disabled}
                            >
                              <span>{destination.label}</span>
                              <small>{destination.fieldCount}</small>
                            </button>
                          ))}
                        </div>
                        <div className="configuration-destination-group">
                          <div className="configuration-destination-heading">Utilities</div>
                          {destinations.filter((destination) => !sectionKeys.has(destination.key)).map((destination) => (
                            <button
                              key={destination.key}
                              type="button"
                              className={`configuration-destination${destination.key === selectedDestinationKey ? " selected" : ""}`}
                              onClick={() => onSelectDestination(tab.key, destination.key)}
                              aria-current={destination.key === selectedDestinationKey ? "page" : undefined}
                              disabled={disabled}
                            >
                              <span>{destination.label}</span>
                            </button>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </nav>
        </>
      )}
    </>
  );
}
