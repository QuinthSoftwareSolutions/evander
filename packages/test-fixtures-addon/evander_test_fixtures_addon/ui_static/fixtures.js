const $ = id => document.getElementById(id);
const base = '/api/addons/test-fixtures-addon';
let revision;
async function api(path, method='GET', body) {
  const response = await fetch(base+path+'?directory='+encodeURIComponent($('directory').value), {
    method, headers: {'Content-Type':'application/json'}, body:body ? JSON.stringify(body) : undefined
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'Fixture request failed');
  return value;
}
function message(error) { $('status').textContent = error.message; }
async function refresh() {
  try {
    const [leases, settings] = await Promise.all([api('/leases'), api('/settings')]);
    revision = settings.revision;
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
          await api('/leases/'+encodeURIComponent(lease.lease_id)+'/release', 'POST', {expected_revision:lease.revision});
          await refresh();
        } catch(error) { message(error); } };
        cell.append(button);
      }
      row.append(cell); $('leases').append(row);
    }
    $('status').textContent = settings.settings.allocations_enabled ? 'New allocations enabled for configured teams.' : 'New allocations disabled. Cleanup remains active.';
  } catch(error) { message(error); }
}
$('scope').onsubmit = event => { event.preventDefault(); refresh(); };
$('settings-form').onsubmit = async event => { event.preventDefault(); try {
  await api('/settings', 'PUT', {expected_revision:revision, settings:JSON.parse($('settings').value)});
  await refresh();
} catch(error) { message(error); } };
refresh();
