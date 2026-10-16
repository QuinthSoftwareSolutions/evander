const $ = id => document.getElementById(id);
const base = '/api/addons/test-fixtures-addon';
let loadedSettings;
let refreshGeneration = 0;
async function api(path, directory, method='GET', body) {
  const response = await fetch(base+path+'?directory='+encodeURIComponent(directory), {
    method, headers: {'Content-Type':'application/json'}, body:body ? JSON.stringify(body) : undefined
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'Fixture request failed');
  return value;
}
function message(error) { $('status').textContent = error.message; }
function currentScope() {
  if (!loadedSettings || loadedSettings.directory !== $('directory').value.trim()) {
    throw new Error('Refresh this directory before changing settings or leases.');
  }
  return loadedSettings;
}
async function refresh() {
  const directory = $('directory').value.trim();
  const generation = ++refreshGeneration;
  loadedSettings = undefined;
  try {
    const [leases, settings] = await Promise.all([api('/leases', directory), api('/settings', directory)]);
    if (generation !== refreshGeneration || directory !== $('directory').value.trim()) return;
    loadedSettings = {directory, revision: settings.revision};
    $('settings').value = JSON.stringify(settings.settings, null, 2);
    $('leases').replaceChildren();
    for (const lease of leases.leases) {
      const row = document.createElement('tr');
      for (const text of [lease.lease_id+' / '+lease.scope.task_id,
        lease.scope.agent_id+' / '+lease.scope.worker_id,
        lease.state.replaceAll('_',' ').replace(/^./, value => value.toUpperCase())+(lease.error ? ': '+lease.error : ''),
        new Date(lease.expires_at*1000).toLocaleString(),
        Object.entries(lease.resource_counts || {}).map(([k,v]) => `${v} ${v === 1 ? k.slice(0,-1) : k}`).join(', ') || 'None']) {
        const cell = document.createElement('td'); cell.textContent = text; row.append(cell);
      }
      const cell = document.createElement('td');
      if (!['released','expired'].includes(lease.state)) {
        const button = document.createElement('button');
        button.textContent = lease.state === 'cleanup_failed' ? 'Retry cleanup' : 'Release';
        button.onclick = async () => { try {
          if (currentScope().directory !== directory) throw new Error('Refresh this directory before changing leases.');
          await api('/leases/'+encodeURIComponent(lease.lease_id)+'/release', directory, 'POST', {expected_revision:lease.revision});
          await refresh();
        } catch(error) { message(error); } };
        cell.append(button);
      }
      row.append(cell); $('leases').append(row);
    }
    $('status').textContent = settings.settings.drain_only ? 'Draining existing leases; new bundles are disabled.' : settings.settings.allocations_enabled ? 'New allocations enabled for configured teams.' : 'New allocations disabled. Cleanup remains active.';
  } catch(error) { if (generation === refreshGeneration) message(error); }
}
$('directory').oninput = () => {
  loadedSettings = undefined;
  ++refreshGeneration;
  $('status').textContent = 'Refresh to load this directory before making changes.';
};
$('scope').onsubmit = event => { event.preventDefault(); return refresh(); };
$('settings-form').onsubmit = async event => { event.preventDefault(); try {
  const scope = currentScope();
  await api('/settings', scope.directory, 'PUT', {expected_revision:scope.revision, settings:JSON.parse($('settings').value)});
  await refresh();
} catch(error) { message(error); } };
refresh();
