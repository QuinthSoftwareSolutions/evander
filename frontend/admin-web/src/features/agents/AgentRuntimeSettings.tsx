import type { AgentExecutionSettings } from "../admin/types";
import { Tooltip } from "../admin/Tooltip";

const sourceLabels = {
  prompt: "Prompt override",
  session: "Session default at dispatch",
  default: "Runtime default at dispatch",
};

function speedLabel(value: AgentExecutionSettings["fast_mode"] | undefined): string {
  if (value === "ultrafast") return "Ultrafast";
  if (value === true) return "Fast";
  if (value === false) return "Standard";
  return "Not reported";
}

function Setting({ label, value, source }: {
  label: string;
  value: string;
  source?: AgentExecutionSettings["fast_mode_source"];
}) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>
        {value}
        {source ? <span className="muted agent-runtime-setting-source">{sourceLabels[source]}</span> : null}
      </dd>
    </div>
  );
}

export function AgentRuntimeSettings({ settings }: { settings: AgentExecutionSettings[] | undefined }) {
  const runs = settings?.length ? settings : [null];
  return (
    <section className="panel agent-runtime-settings" aria-label="Current run settings">
      <div className="panel-header">
        <h3>{runs.length > 1 ? "Current runs" : "Current run"}</h3>
        <Tooltip content="Settings captured when the run was dispatched. Speed is the requested service tier.">
          <span className="panel-meta">Run settings</span>
        </Tooltip>
      </div>
      {runs.map((run, index) => (
        <div key={run?.execution_id ?? index}>
          {runs.length > 1 ? <p className="panel-meta">{run?.execution_id}</p> : null}
          <dl className="property-grid agent-runtime-settings-grid">
            <Setting label="Model" value={run?.model || "Not reported"} />
            <Setting label="Speed" value={speedLabel(run?.fast_mode)} source={run?.fast_mode_source} />
            <Setting label="Reasoning" value={run?.reasoning_effort || (run?.reasoning_effort_source === "default" ? "Provider default" : "Not reported")} source={run?.reasoning_effort_source} />
          </dl>
        </div>
      ))}
    </section>
  );
}
