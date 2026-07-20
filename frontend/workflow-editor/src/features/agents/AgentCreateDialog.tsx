import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { Tooltip } from "../admin/Tooltip";
import { StringListEditor, StringMapEditor } from "../admin/StructuredPolicyInputs";
import type {
  AgentBackend,
  AgentCreateConnectorOption,
  AgentCreateOptionsResponse,
  AgentCreateRequest,
} from "../admin/types";
import {
  agentCreateTargetLabel,
  buildCreatePayload,
  dedupeStrings,
  emptyCreateForm,
  loadAgentCreateOptions,
  parseDurationSeconds,
  type AgentCreateFormState,
  type AgentCreateSubmitMeta,
} from "./agentCreateShared";
import {
  CommunicationTargetCheckboxTable,
  EmailMailboxGrantEditor,
} from "../communications/SessionGrantEditors";

type AgentCreateDialogProps = {
  directoryPath: string;
  onClose: () => void;
  onCreate: (payload: AgentCreateRequest, meta: AgentCreateSubmitMeta) => void | Promise<void>;
  title?: string;
  description?: ReactNode;
  submitLabel?: string;
  submittingLabel?: string;
  closeLabel?: string;
  privilegedUnlocked?: boolean;
  closeOnSuccess?: boolean;
  requireSessionId?: boolean;
  requireSessionIdMessage?: string;
};

function CreateFieldLabel({ children, tooltip }: { children: ReactNode; tooltip: ReactNode }) {
  const labelText = typeof children === "string" ? children : "Field";
  return (
    <span className="config-field-label">
      <span>{children}</span>
      <Tooltip
        content={tooltip}
        focusable
        ariaLabel={`${labelText} help`}
        triggerClassName="config-field-tooltip-trigger"
      >
        <span className="config-field-tooltip-icon" aria-hidden="true">i</span>
      </Tooltip>
    </span>
  );
}

function ConnectorGrantSelector({
  values,
  options,
  onChange,
}: {
  values: Readonly<Record<string, string[]>>;
  options: readonly AgentCreateConnectorOption[];
  onChange: (nextValues: Record<string, string[]>) => void;
}) {
  function setOperation(connectorId: string, operationName: string, checked: boolean) {
    const current = values[connectorId] ?? [];
    const nextOperations = checked
      ? dedupeStrings([...current, operationName])
      : current.filter((name) => name !== operationName);
    const next = { ...values };
    if (nextOperations.length) next[connectorId] = nextOperations;
    else delete next[connectorId];
    onChange(next);
  }

  if (!options.length) {
    return <div className="empty-state">No connectors are configured for this directory.</div>;
  }
  return (
    <div className="detail-stack">
      {options.map((connector) => (
        <details key={connector.connector_id} className="compact-disclosure">
          <summary className="compact-disclosure-summary">
            <span>{connector.display_name}</span>
            <span className="panel-meta">{values[connector.connector_id]?.length ?? 0} granted</span>
          </summary>
          <p className="muted-text">{connector.description}</p>
          <div className="feature-list compact-list">
            {connector.operations.map((operation) => (
              <label className="session-tool-row" key={operation.name}>
                <span className="session-tool-row-main">
                  <input
                    type="checkbox"
                    checked={(values[connector.connector_id] ?? []).includes(operation.name)}
                    onChange={(event) =>
                      setOperation(connector.connector_id, operation.name, event.target.checked)
                    }
                  />
                  <span>
                    <strong>{operation.name}</strong>
                    <span className="muted-text"> {operation.description}</span>
                  </span>
                </span>
                <span className="session-tool-row-meta">
                  <span className="status-pill state-idle">{operation.access}</span>
                  {operation.requires_approval ? <span className="status-pill state-waiting">Approval</span> : null}
                </span>
              </label>
            ))}
          </div>
        </details>
      ))}
    </div>
  );
}

function resetBackendSensitiveFields(
  form: AgentCreateFormState,
  nextBackend: AgentBackend,
  options: AgentCreateOptionsResponse | null,
): AgentCreateFormState {
  const stillValid = (options?.agent_providers ?? []).some(
    (option) =>
      option.value === form.agentProvider &&
      option.execution_backends.includes(nextBackend),
  );
  const nextAgentProvider = form.agentProvider && stillValid ? form.agentProvider : "";
  return {
    ...form,
    backend: nextBackend,
    mssql: nextBackend === "libvirt_vm" ? form.mssql : false,
    mssqlInitRoot: nextBackend === "libvirt_vm" ? form.mssqlInitRoot : "",
    agentProvider: nextAgentProvider,
    modelProviderMode:
      nextAgentProvider && nextAgentProvider !== "codex"
        ? ""
        : form.modelProviderMode,
  };
}

function ttlDurationIsInvalid(value: string): boolean {
  const trimmed = value.trim();
  return !trimmed || parseDurationSeconds(trimmed) == null;
}

export function AgentCreateDialog({
  directoryPath,
  onClose,
  onCreate,
  title = "Create agent",
  description = null,
  submitLabel = "Create",
  submittingLabel = "Queueing...",
  closeLabel = "Close",
  privilegedUnlocked = true,
  closeOnSuccess = true,
  requireSessionId = false,
  requireSessionIdMessage = "Session id is required.",
}: AgentCreateDialogProps) {
  const [createForm, setCreateForm] = useState<AgentCreateFormState>(() => emptyCreateForm());
  const [createOptions, setCreateOptions] = useState<AgentCreateOptionsResponse | null>(null);
  const [inlineValidation, setInlineValidation] = useState("");
  const [createLabelsValidationError, setCreateLabelsValidationError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setCreateOptions(null);
    setCreateForm((current) => ({
      ...current,
      agentProvider: "",
      vmImage: "",
      containerImage: "",
      initialPolicy: {
        ...current.initialPolicy,
        connectorGrants: {},
        communicationTargetIds: [],
        emailMailboxGrants: [],
      },
    }));
    const refresh = async () => {
      try {
        const options = await loadAgentCreateOptions(directoryPath);
        if (!cancelled) setCreateOptions(options);
      } catch {
        if (!cancelled) setCreateOptions(null);
      }
    };
    void refresh();
    return () => {
      cancelled = true;
    };
  }, [directoryPath]);

  const createProviderOptions = useMemo(
    () => (createOptions?.agent_providers ?? []).filter((option) =>
      option.execution_backends.includes(createForm.backend),
    ),
    [createForm.backend, createOptions],
  );
  const showCreateModelProviderMode =
    !createForm.agentProvider || createForm.agentProvider === "codex";
  const createCommunicationTargetOptions = createOptions?.communication_targets ?? [];
  const createEmailMailboxOptions = createOptions?.email_mailboxes ?? [];

  async function handleSubmit() {
    if (createForm.backend === "libvirt_vm" && !createForm.vmName.trim()) {
      setInlineValidation("VM name is required.");
      return;
    }
    if (requireSessionId && !createForm.sessionId.trim()) {
      setInlineValidation(requireSessionIdMessage);
      return;
    }
    if (createForm.backend === "container_worker_vm" && !createForm.sessionId.trim()) {
      setInlineValidation("Session id is required for container workers.");
      return;
    }
    if (ttlDurationIsInvalid(createForm.ttlDuration)) {
      setInlineValidation(
        "Session TTL must use seconds, minutes, hours, days, or weeks, for example 30m, 8h, 7d, or 2w.",
      );
      return;
    }
    if (createLabelsValidationError) {
      setInlineValidation(createLabelsValidationError);
      return;
    }
    const submittedForm = createForm;
    const createPayload = buildCreatePayload(submittedForm, directoryPath);
    const targetLabel = agentCreateTargetLabel(submittedForm);
    setSubmitting(true);
    setInlineValidation("");
    try {
      await onCreate(createPayload, { form: submittedForm, targetLabel });
      setCreateForm(emptyCreateForm());
      setCreateLabelsValidationError(null);
      if (closeOnSuccess) onClose();
    } catch (reason: unknown) {
      setInlineValidation(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation">
      <section className="modal-card agent-crud-modal" role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-header">
          <div>
            <h2>{title}</h2>
            {description ? <p>{description}</p> : null}
          </div>
          <button className="admin-action-button compact" type="button" onClick={onClose} disabled={submitting}>
            {closeLabel}
          </button>
        </div>
        <div className="modal-body">
          {inlineValidation ? <div className="callout error-callout">{inlineValidation}</div> : null}
          <div className="config-field-grid">
            <label className="field-stack">
              <CreateFieldLabel tooltip="Execution target for the agent. VM agent launches a libvirt VM; Container worker prepares a worker session that runs in a managed container.">
                Backend
              </CreateFieldLabel>
              <span className="select-field-control">
                <select
                  value={createForm.backend}
                  onChange={(event) => {
                    const nextBackend = event.target.value as AgentBackend;
                    setCreateForm((current) => resetBackendSensitiveFields(current, nextBackend, createOptions));
                  }}
                >
                  <option value="libvirt_vm">VM agent</option>
                  <option value="container_worker_vm">Container worker</option>
                </select>
              </span>
            </label>
            <label className="field-stack">
              <CreateFieldLabel tooltip="Agent CLI to bootstrap and run in the target. Claude bootstraps Claude Code for VM agents; Directory default uses the configured provider default.">
                Agent provider
              </CreateFieldLabel>
              <span className="select-field-control">
                <select
                  value={createForm.agentProvider}
                  onChange={(event) =>
                    setCreateForm((current) => {
                      const nextAgentProvider = event.target.value;
                      return {
                        ...current,
                        agentProvider: nextAgentProvider,
                        modelProviderMode:
                          nextAgentProvider && nextAgentProvider !== "codex"
                            ? ""
                            : current.modelProviderMode,
                      };
                    })
                  }
                >
                  <option value="">Directory default</option>
                  {createProviderOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </span>
            </label>
            {showCreateModelProviderMode ? (
              <label className="field-stack">
                <CreateFieldLabel tooltip="Relay mode for Codex model requests. Leave blank to use the directory default; choose Local OpenAI for host-local Ollama.">
                  Model relay
                </CreateFieldLabel>
                <span className="select-field-control">
                  <select
                    value={createForm.modelProviderMode}
                    onChange={(event) =>
                      setCreateForm((current) => ({
                        ...current,
                        modelProviderMode: event.target.value,
                      }))
                    }
                  >
                    <option value="">Directory default</option>
                    {(createOptions?.model_provider_modes ?? []).map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </span>
              </label>
            ) : null}
            <label className="field-stack">
              <CreateFieldLabel tooltip="Host-side session id for policy, logs, model routing, and resume state. Required for container workers; VM agents can derive one from the VM name.">
                Session
              </CreateFieldLabel>
              <input
                value={createForm.sessionId}
                onChange={(event) => setCreateForm((current) => ({ ...current, sessionId: event.target.value }))}
                placeholder={requireSessionId || createForm.backend === "container_worker_vm" ? "Required" : "Optional"}
              />
            </label>
            <label className="field-stack">
              <CreateFieldLabel tooltip="How long the created session remains active before expiry. Use s, m, h, d, or w, for example 30m, 8h, 7d, or 2w.">
                Session TTL
              </CreateFieldLabel>
              <input
                value={createForm.ttlDuration}
                onChange={(event) =>
                  setCreateForm((current) => ({ ...current, ttlDuration: event.target.value }))
                }
                placeholder="7d"
              />
            </label>
            <label className="field-stack">
              <CreateFieldLabel tooltip="Model stored on the session for runtime prompts and resumes. Leave blank to use the selected agent provider or directory default.">
                Model
              </CreateFieldLabel>
              <input
                value={createForm.model}
                onChange={(event) => setCreateForm((current) => ({ ...current, model: event.target.value }))}
                placeholder={createForm.agentProvider === "claude" ? "claude-sonnet-4-6" : "Directory default"}
              />
            </label>
            {createForm.backend === "libvirt_vm" ? (
              <>
                <label className="field-stack">
                  <CreateFieldLabel tooltip="Libvirt domain name to create for this VM-backed agent. It must be unique on the host.">
                    VM name
                  </CreateFieldLabel>
                  <input
                    value={createForm.vmName}
                    onChange={(event) => setCreateForm((current) => ({ ...current, vmName: event.target.value }))}
                  />
                </label>
                <label className="field-stack">
                  <CreateFieldLabel tooltip="VM infrastructure provider, not the model provider. Leave blank to use the directory default, usually libvirt.">
                    Provider
                  </CreateFieldLabel>
                  <input
                    value={createForm.provider}
                    onChange={(event) => setCreateForm((current) => ({ ...current, provider: event.target.value }))}
                    placeholder="Directory default"
                  />
                </label>
                <label className="field-stack">
                  <CreateFieldLabel tooltip="Guest runtime user used for bootstraps and agent commands. Current standard VM images use agent.">
                    VM user
                  </CreateFieldLabel>
                  <input
                    value={createForm.vmUser}
                    onChange={(event) => setCreateForm((current) => ({ ...current, vmUser: event.target.value }))}
                  />
                </label>
                <label className="field-stack">
                  <CreateFieldLabel tooltip="Approved libvirt template alias for the base qcow2 image. Leave blank to use the directory default.">
                    VM image
                  </CreateFieldLabel>
                  <span className="select-field-control">
                    <select
                      value={createForm.vmImage}
                      onChange={(event) =>
                        setCreateForm((current) => ({ ...current, vmImage: event.target.value }))
                      }
                    >
                      <option value="">
                        {`Directory default (${createOptions?.libvirt.default_template ?? "default"})`}
                      </option>
                      {(createOptions?.libvirt.templates ?? []).map((template) => (
                        <option key={template.alias} value={template.alias}>
                          {template.alias}
                        </option>
                      ))}
                    </select>
                  </span>
                </label>
              </>
            ) : (
              <>
                <label className="field-stack">
                  <CreateFieldLabel tooltip="Display and control-plane target name for this container-worker agent. Leave blank to reuse the session id.">
                    Target name
                  </CreateFieldLabel>
                  <input
                    value={createForm.targetName}
                    onChange={(event) =>
                      setCreateForm((current) => ({ ...current, targetName: event.target.value }))
                    }
                    placeholder="Optional"
                  />
                </label>
                <label className="field-stack">
                  <CreateFieldLabel tooltip="Approved container image alias for worker sessions. Leave blank to use the directory default runner image.">
                    Container image selector
                  </CreateFieldLabel>
                  {createOptions ? (
                    <span className="select-field-control">
                      <select
                        value={createForm.containerImage}
                        onChange={(event) =>
                          setCreateForm((current) => ({ ...current, containerImage: event.target.value }))
                        }
                      >
                        <option value="">
                          {`Default image (${createOptions.container_worker.default_image ?? "default"})`}
                        </option>
                        {createOptions.container_worker.images.map((image) => (
                          <option key={image.alias} value={image.alias}>
                            {image.alias}
                          </option>
                        ))}
                      </select>
                    </span>
                  ) : (
                    <input
                      value={createForm.containerImage}
                      onChange={(event) =>
                        setCreateForm((current) => ({ ...current, containerImage: event.target.value }))
                      }
                      placeholder="Alias or default image"
                    />
                  )}
                </label>
              </>
            )}
            <fieldset className="agent-capability-group">
              <legend>
                <CreateFieldLabel tooltip="Optional capabilities to enable when the agent is created.">
                  Capabilities
                </CreateFieldLabel>
              </legend>
              <div className="agent-capability-grid">
                <label className="checkbox-row agent-capability-option">
                  <input
                    type="checkbox"
                    checked={createForm.dotnet}
                    onChange={(event) => setCreateForm((current) => ({ ...current, dotnet: event.target.checked }))}
                  />
                  <CreateFieldLabel tooltip="Seed .NET support for the session. VM agents copy the host SDK; container workers point at the seeded worker cache mount.">
                    .NET
                  </CreateFieldLabel>
                </label>
                <label className="checkbox-row agent-capability-option">
                  <input
                    type="checkbox"
                    checked={createForm.nuget}
                    onChange={(event) => setCreateForm((current) => ({ ...current, nuget: event.target.checked }))}
                  />
                  <CreateFieldLabel tooltip="Configure package restore through the host NuGet relay and session cache.">
                    NuGet
                  </CreateFieldLabel>
                </label>
                {createForm.backend === "libvirt_vm" ? (
                  <label className="checkbox-row agent-capability-option">
                    <input
                      type="checkbox"
                      checked={createForm.mssql}
                      onChange={(event) => setCreateForm((current) => ({ ...current, mssql: event.target.checked }))}
                    />
                    <CreateFieldLabel tooltip="Provision a guest-local SQL Server instance for VM agents. Container worker SQL uses session-owned MCP provisioning instead.">
                      MSSQL
                    </CreateFieldLabel>
                  </label>
                ) : null}
                <label className="checkbox-row agent-capability-option">
                  <input
                    type="checkbox"
                    checked={createForm.playwright}
                    onChange={(event) => setCreateForm((current) => ({ ...current, playwright: event.target.checked }))}
                  />
                  <CreateFieldLabel tooltip="Seed Playwright support for the session. VM agents install browser dependencies; container workers point at the seeded browser cache.">
                    Playwright
                  </CreateFieldLabel>
                </label>
                <label className="checkbox-row agent-capability-option">
                  <input
                    type="checkbox"
                    checked={createForm.initialPolicy.webSearchEnabled}
                    onChange={(event) =>
                      setCreateForm((current) => ({
                        ...current,
                        initialPolicy: { ...current.initialPolicy, webSearchEnabled: event.target.checked },
                      }))
                    }
                  />
                  <CreateFieldLabel tooltip="Allow this session's agent runner to use live web search when the selected provider supports it.">
                    Web search
                  </CreateFieldLabel>
                </label>
              </div>
              {createForm.backend === "libvirt_vm" && createForm.mssql ? (
                <div className="agent-capability-detail-row">
                  <label className="field-stack agent-capability-mssql-root">
                    <CreateFieldLabel tooltip="Optional host directory copied into the SQL init folder. If init.sql exists there, it runs after SQL Server starts.">
                      MSSQL init root
                    </CreateFieldLabel>
                    <input
                      value={createForm.mssqlInitRoot}
                      onChange={(event) => setCreateForm((current) => ({ ...current, mssqlInitRoot: event.target.value }))}
                    />
                  </label>
                </div>
              ) : null}
            </fieldset>
            <div className="field-stack wide-field">
              <CreateFieldLabel tooltip="Session policy values written when the agent is created. Empty rows are ignored.">
                Initial policy
              </CreateFieldLabel>
              <p className="field-description">
                Optional session policy values to apply when the agent is created. Blank rows are not sent.
              </p>
            </div>
            <div className="field-stack">
              <CreateFieldLabel tooltip="Key/value metadata stored on the session and shown in agent lists.">
                Labels
              </CreateFieldLabel>
              <StringMapEditor
                value={createForm.initialPolicy.labels}
                onChange={(nextValue) =>
                  setCreateForm((current) => ({
                    ...current,
                    initialPolicy: { ...current.initialPolicy, labels: nextValue },
                  }))
                }
                onValidationChange={setCreateLabelsValidationError}
                fieldLabel="Labels"
                emptyState="No labels are configured."
                addLabel="Add label"
                keyLabel="Key"
                valueLabel="Value"
              />
            </div>
            <div className="field-stack">
              <CreateFieldLabel tooltip="MCP tools this session may call without being hidden by policy. Approval rules still apply.">
                Allowed tools
              </CreateFieldLabel>
              <StringListEditor
                values={createForm.initialPolicy.allowedTools}
                onChange={(nextValues) =>
                  setCreateForm((current) => ({
                    ...current,
                    initialPolicy: { ...current.initialPolicy, allowedTools: nextValues },
                  }))
                }
                addLabel="Add tool"
                itemLabel="Tool"
                placeholder="run_script"
                emptyState="No tools are allowed explicitly."
              />
            </div>
            <div className="field-stack wide-field">
              <CreateFieldLabel tooltip="Named connector operations granted to the session. Selecting an operation also enables the generic connector discovery and call MCP tools.">
                Connector grants
              </CreateFieldLabel>
              <ConnectorGrantSelector
                values={createForm.initialPolicy.connectorGrants}
                options={createOptions?.connectors ?? []}
                onChange={(nextValues) =>
                  setCreateForm((current) => ({
                    ...current,
                    initialPolicy: {
                      ...current.initialPolicy,
                      connectorGrants: nextValues,
                      allowedTools: Object.keys(nextValues).length
                        ? dedupeStrings([...current.initialPolicy.allowedTools, "list_connectors", "call_connector"])
                        : current.initialPolicy.allowedTools,
                    },
                  }))
                }
              />
            </div>
            <div className="field-stack">
              <CreateFieldLabel tooltip="Script names this session may run through approved script execution paths.">
                Allowed scripts
              </CreateFieldLabel>
              <StringListEditor
                values={createForm.initialPolicy.allowedScriptNames}
                onChange={(nextValues) =>
                  setCreateForm((current) => ({
                    ...current,
                    initialPolicy: { ...current.initialPolicy, allowedScriptNames: nextValues },
                  }))
                }
                addLabel="Add script"
                itemLabel="Script"
                placeholder="collect-logs"
                emptyState="No scripts are allowed explicitly."
              />
            </div>
            <div className="field-stack wide-field agent-communication-targets-field">
              <CreateFieldLabel tooltip="Directory communication targets the session may use for messaging. Only registered targets with session-compatible capabilities are selectable.">
                Communication targets
              </CreateFieldLabel>
              <CommunicationTargetCheckboxTable
                values={createForm.initialPolicy.communicationTargetIds}
                options={createCommunicationTargetOptions}
                onChange={(nextValues) =>
                  setCreateForm((current) => ({
                    ...current,
                    initialPolicy: { ...current.initialPolicy, communicationTargetIds: nextValues },
                  }))
                }
                emptyState={
                  createCommunicationTargetOptions.length
                    ? "No communication targets selected."
                    : "No session-capable communication targets are registered for this directory."
                }
              />
            </div>
            <div className="field-stack wide-field agent-communication-targets-field">
              <CreateFieldLabel tooltip="Mailbox account access is separate from outbound destinations. Read-only is the default; draft and send access must be granted explicitly.">
                Email mailbox access
              </CreateFieldLabel>
              <EmailMailboxGrantEditor
                grants={createForm.initialPolicy.emailMailboxGrants}
                options={createEmailMailboxOptions}
                onChange={(nextGrants) =>
                  setCreateForm((current) => ({
                    ...current,
                    initialPolicy: { ...current.initialPolicy, emailMailboxGrants: nextGrants },
                  }))
                }
                emptyState={
                  createEmailMailboxOptions.length
                    ? "No mailbox accounts selected."
                    : "No mailbox accounts are configured for this directory."
                }
              />
            </div>
          </div>
          <div className="inline-actions compact-actions">
            <button className="admin-action-button primary compact" type="button" disabled={!privilegedUnlocked || submitting} onClick={() => void handleSubmit()}>
              {submitting ? submittingLabel : submitLabel}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
