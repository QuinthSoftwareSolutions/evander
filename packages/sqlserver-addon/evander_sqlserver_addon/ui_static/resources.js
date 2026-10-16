const root = document.querySelector('main');
const base = '/api/addons/' + root.dataset.addon;
const el = id => document.getElementById(id);
let loaded = null;
let generation = 0;

async function api(path, method = 'GET', body, directory = loaded?.directory || el('directory').value) {
  const response = await fetch(base + path + '?directory=' + encodeURIComponent(directory), {
    method, headers: {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok) throw Error(data.error || 'Request failed');
  return data;
}
function current() {
  if (!loaded || loaded.directory !== el('directory').value) throw Error('Refresh this directory first');
  return loaded;
}
function table(id, headings, rows, cells, action) {
  const table = document.createElement('table');
  const header = document.createElement('tr');
  for (const label of [...headings, ...(action ? ['Actions'] : [])]) {
    const th = document.createElement('th'); th.textContent = label; header.append(th);
  }
  table.append(header);
  for (const row of rows) {
    const tr = document.createElement('tr');
    for (const value of cells(row)) {
      const td = document.createElement('td'); td.textContent = value ?? ''; tr.append(td);
    }
    if (action) {
      const td = document.createElement('td'); const button = document.createElement('button');
      button.textContent = action.label;
      button.onclick = () => action.run(row);
      td.append(button); tr.append(td);
    }
    table.append(tr);
  }
  el(id).replaceChildren(table);
}
async function readiness(kind, row) {
  try {
    const {directory} = current(); const gen = generation;
    const value = await api('/' + kind + '/' + encodeURIComponent(row.id) + '/readiness', 'POST', {});
    if (gen === generation && directory === el('directory').value) el('status').textContent = JSON.stringify(value);
  } catch (error) { el('status').textContent = error.message; }
}
async function refresh() {
  const gen = ++generation;
  const directory = el('directory').value;
  loaded = null; el('save').disabled = el('credential-save').disabled = true;
  try {
    const [settings, leases, operations] = await Promise.all(['/settings', '/leases', '/operations'].map(p => api(p, 'GET', undefined, directory)));
    if (gen !== generation || directory !== el('directory').value) return;
    loaded = {directory, revision: settings.revision, settings: settings.settings};
    el('settings').value = JSON.stringify(settings.settings, null, 2);
    const connections = Object.entries(settings.settings.connections).map(([id, connection]) => ({id, ...connection}));
    table('connections', ['Connection', 'Enabled operations'], connections, row => [row.id, (row.operations || []).join(', ')], {label: 'Check readiness', run: row => readiness('connections', row)});
    const profiles = Object.entries(settings.settings.profiles).map(([id, profile]) => ({id, ...profile}));
    table('profiles', ['Profile', 'Target', 'Resources', 'Idle lease'], profiles, row => [row.id, row.worker_id || row.connection_id || '', row.options.databases?.join(', ') || row.options.mode || '', row.idle_seconds + ' seconds'], {label: 'Check readiness', run: row => readiness('profiles', row)});
    table('leases', ['Lease', 'Owner', 'Profile', 'State', 'Expires'], leases.leases, row => [row.lease_id, row.owner.key, row.profile_id, row.state, new Date(row.expires_at * 1000).toLocaleString()], {label: 'Release / retry cleanup', run: async row => {
      try { current(); await api('/leases/' + row.lease_id + '/release', 'POST', {expected_revision: row.revision}); await refresh(); }
      catch (error) { el('status').textContent = error.message; }
    }});
    table('operations', ['Operation', 'Resource / request', 'Connection', 'State'], operations.operations, row => [row.operation || (row.database ? 'Managed database' : 'Resource operation'), row.database ? row.id + ' (' + row.database + ')' : row.resource_id || row.id, row.connection_id || '', row.state]);
    el('credential-connection').replaceChildren(...connections.map(row => { const option = document.createElement('option'); option.value = option.textContent = row.id; return option; }));
    el('save').disabled = false; el('credential-save').disabled = !connections.length;
    el('status').textContent = settings.settings.allocations_enabled ? 'New test allocations enabled for configured profiles.' : 'New test allocations disabled. Persistent operations use their separate grants.';
  } catch (error) { if (gen === generation) el('status').textContent = error.message; }
}
el('directory').oninput = () => {
  generation++; loaded = null;
  el('save').disabled = el('credential-save').disabled = true;
  el('credential-password').value = '';
  for (const id of ['connections', 'profiles', 'leases', 'operations']) el(id).replaceChildren();
  el('status').textContent = 'Refresh the selected directory before making changes.';
};
el('refresh').onclick = refresh;
el('save').onclick = async () => {
  try { const state = current(); await api('/settings', 'PUT', {expected_revision: state.revision, settings: JSON.parse(el('settings').value)}); await refresh(); }
  catch (error) { el('status').textContent = error.message; }
};
el('credential-save').onclick = async () => {
  try {
    const state = current(); const connection = el('credential-connection').value; const purpose = el('credential-purpose').value;
    const values = {username: el('credential-username').value, password: el('credential-password').value};
    el('credential-password').value = '';
    const result = await api('/credentials', 'POST', {expected_revision: state.revision, connection_id: connection, purpose, values}, state.directory);
    if (loaded !== state || state.directory !== el('directory').value) return;
    state.settings.connections[connection].credentials ||= {};
    state.settings.connections[connection].credentials[purpose] = {credential_ref: result.credential_ref};
    await api('/settings', 'PUT', {expected_revision: state.revision, settings: state.settings}, state.directory);
    await refresh();
  } catch (error) { el('status').textContent = error.message; }
};
refresh();
