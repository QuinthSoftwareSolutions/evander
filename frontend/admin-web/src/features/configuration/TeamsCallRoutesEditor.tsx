import { JsonTextEditor } from "../admin/StructuredPolicyInputs";

type Route = { tenant_id: string; caller_user_id: string; directory_path: string; origin_session_id: string };
type Assistant = { session_id: string; directory_path: string; name: string };

export function TeamsCallRoutesEditor({ value, onChange, assistants, disabled }: {
  value: string;
  onChange: (value: string) => void;
  assistants: Assistant[];
  disabled: boolean;
}) {
  let parsed: unknown;
  try { parsed = JSON.parse(value); } catch { parsed = null; }
  if (!Array.isArray(parsed) || !parsed.every((entry) => entry && typeof entry === "object"
    && ["tenant_id", "caller_user_id", "directory_path", "origin_session_id"].every((key) => typeof entry[key] === "string"))) {
    return <JsonTextEditor value={value} onChange={onChange} fieldLabel="Teams call routes" disabled={disabled} />;
  }
  const routes = parsed as Route[];
  function change(index: number, patch: Partial<Route>) {
    onChange(JSON.stringify(routes.map((route, row) => row === index ? { ...route, ...patch } : route), null, 2));
  }
  return <div className="structured-policy-editor">
    {routes.map((route, index) => <div className="config-field-card" key={index}>
      <label>Tenant ID
        <input aria-label={`Tenant ID for route ${index + 1}`} value={route.tenant_id} disabled={disabled}
          onChange={(event) => change(index, { tenant_id: event.target.value })} />
      </label>
      <label>Caller Entra user ID
        <input aria-label={`Caller user ID for route ${index + 1}`} value={route.caller_user_id} disabled={disabled}
          onChange={(event) => change(index, { caller_user_id: event.target.value })} />
      </label>
      <label>Assistant
        <select aria-label={`Assistant for route ${index + 1}`} value={route.origin_session_id} disabled={disabled}
          onChange={(event) => {
            const selected = assistants.find((assistant) => assistant.session_id === event.target.value);
            if (selected) change(index, { origin_session_id: selected.session_id, directory_path: selected.directory_path });
            else if (!event.target.value) change(index, { origin_session_id: "", directory_path: "root" });
          }}>
          <option value="">Choose an assistant</option>
          {route.origin_session_id && !assistants.some((assistant) => assistant.session_id === route.origin_session_id)
            ? <option value={route.origin_session_id}>Unavailable: {route.origin_session_id}</option> : null}
          {assistants.map((assistant) => <option key={assistant.session_id} value={assistant.session_id}>
            {assistant.name} — {assistant.session_id} ({assistant.directory_path})
          </option>)}
        </select>
      </label>
      <button type="button" disabled={disabled} onClick={() => onChange(JSON.stringify(routes.filter((_, row) => row !== index), null, 2))}>Remove route</button>
    </div>)}
    <button type="button" disabled={disabled} onClick={() => onChange(JSON.stringify([...routes,
      { tenant_id: "", caller_user_id: "", directory_path: "root", origin_session_id: "" }], null, 2))}>Add caller route</button>
  </div>;
}
