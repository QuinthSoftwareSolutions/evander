import type { useModelPricingCatalog } from "./useModelPricingCatalog";
import { RecordCollection } from "../admin/RecordCollection";
import { Tooltip } from "../admin/Tooltip";

import { RegexListEditor, StringListEditor } from "../admin/StructuredPolicyInputs";
import { parseStringListDraftValue } from "../admin/structuredPolicyInputs";

function pricingPathSourceLabel(value: string): string {
  if (value === "env") return "Environment override";
  if (value === "default") return "Default catalog";
  return value || "Unknown";
}

export function ModelPricingConfiguration({ model, selectedDirectoryPath, loading, mutationLocked }: {
  model: ReturnType<typeof useModelPricingCatalog>;
  selectedDirectoryPath: string;
  loading: boolean;
  mutationLocked: boolean;
}) {
  const {
    modelPricingCatalog,
    modelPricingDirty,
    modelPricingSaving,
    modelPricingError,
    loadModelPricingCatalog,
    updateModelPricingDraft,
    updateModelPricingSource,
    addModelPricingSource,
    removeModelPricingSource,
    updateModelPricingRule,
    addModelPricingRule,
    duplicateModelPricingRule,
    deleteModelPricingRule,
    discardModelPricingChanges,
    saveModelPricingCatalog,
    activeModelPricingDraft,
    modelPricingPatternError,
    modelPricingPanelBodyId,
    modelPricingSaveDisabled,
  } = model;
  return (
    <article className="panel detail-panel configuration-summary-panel model-pricing-panel">
        <div id={modelPricingPanelBodyId} className="detail-stack compact-detail-stack">
        <div className="inline-actions compact-actions">
          {modelPricingDirty ? <span className="status-pill state-stale">Unsaved changes</span> : null}
          <button
            type="button"
            onClick={() => void loadModelPricingCatalog()}
            disabled={loading || modelPricingSaving}
          >
            Refresh
          </button>
          <button
            type="button"
            onClick={discardModelPricingChanges}
            disabled={modelPricingSaving || !modelPricingDirty}
          >
            Discard
          </button>
          <button
            type="button"
            className="primary"
            onClick={() => void saveModelPricingCatalog()}
            disabled={modelPricingSaveDisabled}
          >
            {modelPricingSaving ? "Saving..." : "Save pricing"}
          </button>
        </div>
        {!modelPricingCatalog ? (
          <div className="callout warning-callout">Loading model pricing catalog...</div>
        ) : null}
        {mutationLocked ? (
          <div className="callout warning-callout">
            Privileged operations are locked. Unlock privileged operations to save pricing changes.
          </div>
        ) : null}
        {modelPricingCatalog?.status === "error" ? (
          <div className="callout error-callout">
            {modelPricingCatalog.error ?? "Model pricing catalog could not be loaded."}
          </div>
        ) : null}
        {modelPricingError ? <div className="callout error-callout">{modelPricingError}</div> : null}
        {modelPricingPatternError ? <div className="callout error-callout" role="alert">{modelPricingPatternError}</div> : null}
        {modelPricingCatalog ? (
          <>
        <details className="compact-disclosure">
          <summary>Catalog details and sources · {activeModelPricingDraft.sources.length} sources</summary>
        <div className="detail-grid">
          <div><span>Revision</span><strong>{modelPricingCatalog.revision?.slice(0, 12) ?? "n/a"}</strong></div>
          <div><span>Path source</span><strong>{pricingPathSourceLabel(modelPricingCatalog.path_source)}</strong></div>
          <div><span>Override env</span><strong>{modelPricingCatalog.env_var}</strong></div>
          <div><span>Error class</span><strong>{modelPricingCatalog.error_class ?? "n/a"}</strong></div>
          <div><span>Catalog path</span><strong>{modelPricingCatalog.path ?? "n/a"}</strong></div>
          <div><span>Current rules</span><strong>{modelPricingCatalog.rule_count}</strong></div>
        </div>
        <div className="config-field-grid">
          <label className="field-stack">
            <span>Version</span>
            <input
              type="text"
              value={activeModelPricingDraft.version}
              onChange={(event) =>
                updateModelPricingDraft((draft) => ({ ...draft, version: event.target.value }))
              }
              disabled={modelPricingSaving}
            />
          </label>
          <label className="field-stack">
            <span>Currency</span>
            <select
              value={activeModelPricingDraft.currency}
              onChange={(event) =>
                updateModelPricingDraft((draft) => ({ ...draft, currency: event.target.value }))
              }
              disabled={modelPricingSaving}
            >
              <option value="usd">usd</option>
            </select>
          </label>
          <label className="field-stack wide-field">
            <span>Description</span>
            <textarea
              rows={3}
              value={activeModelPricingDraft.description}
              onChange={(event) =>
                updateModelPricingDraft((draft) => ({ ...draft, description: event.target.value }))
              }
              disabled={modelPricingSaving}
            />
          </label>
        </div>

        <div className="detail-stack compact-detail-stack">
          <div className="panel-header compact-panel-header">
            <div>
              <h4>Sources</h4>
              <p className="muted-text">OpenAI documentation URLs used to maintain this rate card.</p>
            </div>
            <div className="feature-toolbar-actions model-pricing-section-actions">
              <button type="button" onClick={addModelPricingSource} disabled={modelPricingSaving}>
                Add source
              </button>
            </div>
          </div>
          {activeModelPricingDraft.sources.length ? (
            <div className="detail-stack compact-detail-stack">
              {activeModelPricingDraft.sources.map((source, index) => (
                <div className="inline-actions compact-actions model-pricing-source-row" key={`source-${index}`}>
                  <input
                    type="url"
                    aria-label={`Pricing source ${index + 1}`}
                    value={source}
                    onChange={(event) => updateModelPricingSource(index, event.target.value)}
                    disabled={modelPricingSaving}
                  />
                  {/^https?:\/\//i.test(source) ? <a href={source} target="_blank" rel="noreferrer">Open source</a> : null}
                  <button
                    type="button"
                    onClick={() => removeModelPricingSource(index)}
                    disabled={modelPricingSaving}
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty-state">No source URLs are configured.</div>
          )}
        </div>

        </details>
        <div className="detail-stack compact-detail-stack">
          <div className="panel-header compact-panel-header">
            <div>
              <h4>Pricing rules</h4>
              <p className="muted-text">
                Rates are expressed per 1M tokens. Exact models are structured tags and patterns are validated regular expressions.
              </p>
            </div>
            <div className="feature-toolbar-actions model-pricing-section-actions">
              <button type="button" onClick={addModelPricingRule} disabled={modelPricingSaving}>
                Add rule
              </button>
            </div>
          </div>
          {activeModelPricingDraft.rules.length ? (
          <RecordCollection
            key={`${selectedDirectoryPath}:${modelPricingCatalog.revision}`}
            label="Pricing rules"
            entries={activeModelPricingDraft.rules}
            name={(rule, index) => rule.id || `Rule ${index + 1}`}
            description={(rule) => parseStringListDraftValue(rule.modelsText).join(", ") || rule.modelPatternsText || "No models selected"}
            searchText={(rule) => `${rule.provider} ${rule.providerEndpointIdentifier} ${rule.serviceTier} ${rule.modelPatternsText}`}
            category={(rule) => rule.provider}
            metadata={(rule) => <><span>Input {rule.inputRate || "—"}</span><span>Output {rule.outputRate || "—"}</span></>}
            isNew={(rule) => !rule.id.trim()}
          >
            {(rule, index) => (
              <div className="model-pricing-rule-card">
                <div className="feature-list-title-row">
                  <strong>{rule.id || `Rule ${index + 1}`}</strong>
                  <div className="inline-actions compact-actions">
                    <button
                      type="button"
                      onClick={() => duplicateModelPricingRule(index)}
                      disabled={modelPricingSaving}
                    >
                      Duplicate
                    </button>
                    <button
                      type="button"
                      className="danger"
                      onClick={() => deleteModelPricingRule(index)}
                      disabled={modelPricingSaving}
                    >
                      Delete
                    </button>
                  </div>
                </div>
                <div className="config-field-grid">
                  <label className="field-stack">
                    <span>Rule id</span>
                    <input
                      type="text"
                      value={rule.id}
                      onChange={(event) => updateModelPricingRule(index, { id: event.target.value })}
                      disabled={modelPricingSaving}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Provider</span>
                    <input
                      type="text"
                      value={rule.provider}
                      onChange={(event) => updateModelPricingRule(index, { provider: event.target.value })}
                      disabled={modelPricingSaving}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Effective from</span>
                    <input
                      type="text"
                      value={rule.effectiveFrom}
                      onChange={(event) => updateModelPricingRule(index, { effectiveFrom: event.target.value })}
                      disabled={modelPricingSaving}
                      placeholder="2026-06-04T00:00:00Z"
                    />
                  </label>
                  <label className="field-stack">
                    <span>Effective to</span>
                    <input
                      type="text"
                      value={rule.effectiveTo}
                      onChange={(event) => updateModelPricingRule(index, { effectiveTo: event.target.value })}
                      disabled={modelPricingSaving}
                      placeholder="optional"
                    />
                  </label>
                  <div className="field-stack wide-field">
                    <span>Models</span>
                    <StringListEditor
                      values={parseStringListDraftValue(rule.modelsText)}
                      onChange={(values) =>
                        updateModelPricingRule(index, { modelsText: values.join("\n") })
                      }
                      itemLabel="Exact model"
                      addLabel="Add model"
                      emptyState="No exact models are configured."
                      placeholder="Enter an exact model"
                      disabled={modelPricingSaving}
                    />
                  </div>
                  <div className="field-stack wide-field">
                    <span>Model patterns</span>
                    <RegexListEditor
                      values={parseStringListDraftValue(rule.modelPatternsText)}
                      onChange={(values) =>
                        updateModelPricingRule(index, { modelPatternsText: values.join("\n") })
                      }
                      onInvalidDraftChange={(values) =>
                        updateModelPricingRule(index, { modelPatternsText: values.join("\n") })
                      }
                      fieldLabel="Model patterns"
                      addLabel="Add pattern"
                      emptyState="No model patterns are configured."
                      placeholder="Enter a regular expression"
                      disabled={modelPricingSaving}
                    />
                  </div>
                  <label className="field-stack">
                    <span>Input rate</span>
                    <input
                      type="text"
                      value={rule.inputRate}
                      onChange={(event) => updateModelPricingRule(index, { inputRate: event.target.value })}
                      disabled={modelPricingSaving}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Cached input rate</span>
                    <input
                      type="text"
                      value={rule.cachedInputRate}
                      onChange={(event) => updateModelPricingRule(index, { cachedInputRate: event.target.value })}
                      disabled={modelPricingSaving}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Cache write rate (5 minutes)</span>
                    <input
                      type="text"
                      value={rule.cacheWrite5mRate}
                      onChange={(event) => updateModelPricingRule(index, { cacheWrite5mRate: event.target.value })}
                      disabled={modelPricingSaving}
                      placeholder="input rate"
                    />
                  </label>
                  <label className="field-stack">
                    <span>Cache write rate (1 hour)</span>
                    <input
                      type="text"
                      value={rule.cacheWrite1hRate}
                      onChange={(event) => updateModelPricingRule(index, { cacheWrite1hRate: event.target.value })}
                      disabled={modelPricingSaving}
                      placeholder="input rate"
                    />
                  </label>
                  <label className="field-stack">
                    <span>Output rate</span>
                    <input
                      type="text"
                      value={rule.outputRate}
                      onChange={(event) => updateModelPricingRule(index, { outputRate: event.target.value })}
                      disabled={modelPricingSaving}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Batch multiplier</span>
                    <input
                      type="text"
                      value={rule.batchMultiplier}
                      onChange={(event) => updateModelPricingRule(index, { batchMultiplier: event.target.value })}
                      disabled={modelPricingSaving}
                      placeholder="optional"
                    />
                  </label>
                  <label className="field-stack">
                    <span>Service tier</span>
                    <input
                      type="text"
                      value={rule.serviceTier}
                      onChange={(event) => updateModelPricingRule(index, { serviceTier: event.target.value })}
                      disabled={modelPricingSaving}
                      placeholder="optional"
                    />
                  </label>
                  <label className="field-stack">
                    <span>API endpoint</span>
                    <input
                      type="text"
                      value={rule.providerEndpointIdentifier}
                      onChange={(event) => updateModelPricingRule(index, { providerEndpointIdentifier: event.target.value })}
                      disabled={modelPricingSaving}
                      placeholder="all endpoints"
                    />
                  </label>
                  <label className="field-stack">
                    <span>Long-context threshold</span>
                    <input
                      type="text"
                      value={rule.longContextInputTokensOver}
                      onChange={(event) => updateModelPricingRule(index, { longContextInputTokensOver: event.target.value })}
                      disabled={modelPricingSaving}
                      placeholder="optional"
                    />
                  </label>
                  <label className="field-stack">
                    <span>Long-context input x</span>
                    <input
                      type="text"
                      value={rule.longContextInputMultiplier}
                      onChange={(event) => updateModelPricingRule(index, { longContextInputMultiplier: event.target.value })}
                      disabled={modelPricingSaving}
                      placeholder="optional"
                    />
                  </label>
                  <label className="field-stack">
                    <span>Long-context output x</span>
                    <input
                      type="text"
                      value={rule.longContextOutputMultiplier}
                      onChange={(event) => updateModelPricingRule(index, { longContextOutputMultiplier: event.target.value })}
                      disabled={modelPricingSaving}
                      placeholder="optional"
                    />
                  </label>
                </div>
              </div>
            )}
          </RecordCollection>
        ) : (
          <div className="empty-state">No pricing rules are configured.</div>
        )}
        </div>


          </>
        ) : null}
        </div>
    </article>

  );
}
