import { JsonTextEditor } from "../admin/StructuredPolicyInputs";

type Assignment = { caller_number: string; session_id: string; directory_path: string };
type CallerMap = { did: string; route_id: string; callers: Assignment[] };
type Session = { session_id: string; directory_path: string; name: string };
type Route = { route_id: string; did: string | null; called_number: string | null; directory_path: string };

function choiceKey(id: string, scope: string): string {
  return JSON.stringify([id, scope]);
}

function numberKey(value: string): string {
  return value.replace(/\D/g, "");
}

function isCallerMap(value: unknown): value is CallerMap {
  if (!value || typeof value !== "object") return false;
  const group = value as CallerMap;
  return typeof group.did === "string" && typeof group.route_id === "string"
    && Array.isArray(group.callers) && group.callers.every((entry) => entry
      && typeof entry.caller_number === "string" && typeof entry.session_id === "string"
      && typeof entry.directory_path === "string");
}

export function PhoneCallerMapsEditor({ value, onChange, sessions, routes, disabled }: {
  value: string;
  onChange: (value: string) => void;
  sessions: Session[];
  routes: Route[];
  disabled: boolean;
}) {
  let parsed: unknown;
  try { parsed = JSON.parse(value); } catch { parsed = null; }
  if (!Array.isArray(parsed) || !parsed.every(isCallerMap)) {
    return <JsonTextEditor value={value} onChange={onChange} fieldLabel="Caller routing" disabled={disabled} />;
  }
  const maps = parsed as CallerMap[];
  function change(group: number, patch: Partial<CallerMap>) {
    onChange(JSON.stringify(maps.map((entry, index) => index === group ? { ...entry, ...patch } : entry), null, 2));
  }
  function changeCaller(group: number, row: number, patch: Partial<Assignment>) {
    change(group, { callers: maps[group].callers.map((entry, index) => index === row ? { ...entry, ...patch } : entry) });
  }
  return <div className="string-list-selector structured-input-editor">
    <p className="muted-text">Assign callers to agent sessions for each called number. Calls to these numbers require an assignment.</p>
    {maps.map((group, groupIndex) => <fieldset className="phone-caller-map" key={groupIndex}>
      <legend>Called number {groupIndex + 1}</legend>
      <div className="form-grid">
      <label className="field-stack structured-input-field"><span>Phone route</span>
        <select aria-label={`Phone route for called number ${groupIndex + 1}`}
          value={group.route_id ? choiceKey(group.route_id, numberKey(group.did)) : ""} disabled={disabled}
          onChange={(event) => {
            const selected = routes.find((route) => choiceKey(route.route_id, numberKey(route.did ?? route.called_number ?? "")) === event.target.value);
            change(groupIndex, { route_id: selected?.route_id ?? "", did: selected?.did ?? selected?.called_number ?? group.did });
          }}>
          <option value="">Choose a phone route</option>
          {group.route_id && !routes.some((route) => route.route_id === group.route_id
            && numberKey(route.did ?? route.called_number ?? "") === numberKey(group.did))
            ? <option value={choiceKey(group.route_id, numberKey(group.did))}>Unavailable: {group.route_id} ({group.did})</option> : null}
          {routes.map((route, index) => <option key={index} value={choiceKey(route.route_id, numberKey(route.did ?? route.called_number ?? ""))}>
            {route.route_id} — {route.did ?? route.called_number} ({route.directory_path})
          </option>)}
        </select>
      </label>
      <label className="field-stack structured-input-field"><span>Called DID</span>
        <input aria-label={`Called DID for map ${groupIndex + 1}`} value={group.did} disabled={disabled}
          type="tel" placeholder="+15551230000"
          onChange={(event) => change(groupIndex, { did: event.target.value })} />
      </label>
      </div>
      {group.callers.length === 0 ? <p className="muted-text">No callers assigned. All calls to this number will be rejected.</p> : null}
      {group.callers.map((entry, row) => <div className="form-grid phone-caller-assignment" key={row}>
        <label className="field-stack structured-input-field"><span>Caller phone number</span>
          <input aria-label={`Caller phone number ${row + 1} for map ${groupIndex + 1}`} value={entry.caller_number}
            type="tel" placeholder="+15551230001" disabled={disabled}
            onChange={(event) => changeCaller(groupIndex, row, { caller_number: event.target.value })} />
        </label>
        <label className="field-stack structured-input-field"><span>Agent session</span>
          <select aria-label={`Agent session ${row + 1} for map ${groupIndex + 1}`}
            value={entry.session_id ? choiceKey(entry.session_id, entry.directory_path) : ""} disabled={disabled}
            onChange={(event) => {
              const selected = sessions.find((session) => choiceKey(session.session_id, session.directory_path) === event.target.value);
              changeCaller(groupIndex, row, {
                session_id: selected?.session_id ?? "", directory_path: selected?.directory_path ?? entry.directory_path,
              });
            }}>
            <option value="">Choose an agent session</option>
            {entry.session_id && !sessions.some((session) => session.session_id === entry.session_id
              && session.directory_path === entry.directory_path)
              ? <option value={choiceKey(entry.session_id, entry.directory_path)}>Unavailable: {entry.session_id} ({entry.directory_path})</option> : null}
            {sessions.map((session) => <option key={session.session_id} value={choiceKey(session.session_id, session.directory_path)}>
              {session.name} — {session.session_id} ({session.directory_path})
            </option>)}
          </select>
        </label>
        <div className="inline-actions compact-actions field-span-2">
        <button type="button" className="danger" disabled={disabled}
          onClick={() => change(groupIndex, { callers: group.callers.filter((_, index) => index !== row) })}>Remove caller</button>
        </div>
      </div>)}
      <p className="muted-text">The phone route supplies the greeting and caller access policy.</p>
      <div className="inline-actions compact-actions">
      <button type="button" disabled={disabled} onClick={() => change(groupIndex, {
        callers: [...group.callers, { caller_number: "", session_id: "", directory_path: "root" }],
      })}>Add caller</button>
      <button type="button" className="danger" disabled={disabled} onClick={() => onChange(JSON.stringify(
        maps.filter((_, index) => index !== groupIndex), null, 2,
      ))}>Remove called number and restore fixed routing</button>
      </div>
    </fieldset>)}
    <div className="inline-actions compact-actions">
    <button type="button" disabled={disabled} onClick={() => onChange(JSON.stringify(
      [...maps, { did: "", route_id: "", callers: [] }], null, 2,
    ))}>Add called number</button>
    </div>
  </div>;
}
