const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = readFileSync(path.join(__dirname,
  '../evander_test_fixtures_addon/ui_static/fixtures.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

class Element {
  value = '';
  textContent = '';
  children = [];
  append(child) { this.children.push(child); }
  replaceChildren() { this.children = []; }
}

function harness({pauseA = false, leases = false} = {}) {
  const elements = Object.fromEntries(['directory', 'settings', 'leases', 'status',
    'scope', 'settings-form'].map(id => [id, new Element()]));
  elements.directory.value = 'root/a';
  const policies = Object.fromEntries(['a', 'b'].map(id => ['root/' + id, {
    revision: 7,
    settings: {allocations_enabled: id === 'a', allowed_team_ids: ['team-' + id], workers: {}}
  }]));
  const writes = [];
  let unblockA;
  const gate = pauseA ? new Promise(resolve => { unblockA = resolve; }) : null;
  let failB = false;
  const context = vm.createContext({
    document: {
      getElementById: id => elements[id],
      createElement: () => new Element()
    },
    fetch: async (url, options) => {
      const parsed = new URL(url, 'http://fixtures.test');
      const directory = parsed.searchParams.get('directory');
      if (options.method === 'GET' && directory === 'root/a' && gate) await gate;
      if (options.method === 'GET' && directory === 'root/b' && failB) {
        return {ok: false, json: async () => ({error: 'Scope load failed'})};
      }
      if (options.method !== 'GET') {
        const body = JSON.parse(options.body);
        writes.push({directory, path: parsed.pathname, ...body});
        if (options.method === 'PUT') {
          assert.equal(body.expected_revision, policies[directory].revision);
          policies[directory] = {revision: body.expected_revision + 1, settings: body.settings};
        }
      }
      const value = parsed.pathname.endsWith('/leases') ? {leases: leases ? [{
        lease_id: 'lease-' + directory.slice(-1), scope: {task_id: 'task-a',
          agent_id: 'agent-a', worker_id: 'worker-a'}, state: 'ready',
        revision: 2, expires_at: 1000, resource_counts: {}
      }] : []} : policies[directory];
      return {ok: true, json: async () => structuredClone(value)};
    }
  });
  vm.runInContext(source, context);
  return {
    elements, policies, writes,
    unblockA: () => unblockA(),
    failNextScope: () => { failB = true; },
    changeDirectory(directory) {
      elements.directory.value = directory;
      if (elements.directory.oninput) elements.directory.oninput();
    },
    async refresh() { await elements.scope.onsubmit({preventDefault() {}}); await flush(); },
    async save() { await elements['settings-form'].onsubmit({preventDefault() {}}); await flush(); }
  };
}

test('changing directory cannot save the previous policy at a coincident revision', async () => {
  const app = harness();
  await flush();
  app.changeDirectory('root/b');
  await app.save();
  assert.equal(app.writes.length, 0);
  assert.deepEqual(app.policies['root/b'].settings.allowed_team_ids, ['team-b']);
  await app.refresh();
  app.elements.settings.value = JSON.stringify({allocations_enabled: true,
    allowed_team_ids: ['team-b'], workers: {}});
  await app.save();
  assert.equal(app.writes.length, 1);
  assert.equal(app.writes[0].directory, 'root/b');
  assert.equal(app.writes[0].expected_revision, 7);
  assert.deepEqual(app.writes[0].settings.allowed_team_ids, ['team-b']);
});

test('a late response for the previous directory cannot replace the current editor', async () => {
  const app = harness({pauseA: true});
  app.changeDirectory('root/b');
  await app.refresh();
  app.unblockA();
  await flush();
  await app.save();
  assert.equal(app.writes.length, 1);
  assert.equal(app.writes[0].directory, 'root/b');
  assert.deepEqual(app.writes[0].settings.allowed_team_ids, ['team-b']);
});

test('a failed refresh leaves old settings unavailable for mutation', async () => {
  const app = harness();
  await flush();
  app.changeDirectory('root/b');
  app.failNextScope();
  await app.refresh();
  await app.save();
  assert.equal(app.writes.length, 0);
  assert.deepEqual(app.policies['root/b'].settings.allowed_team_ids, ['team-b']);
});

test('release buttons from a previous directory cannot mutate that old scope', async () => {
  const app = harness({leases: true});
  await flush();
  const button = app.elements.leases.children[0].children.at(-1).children[0];
  app.changeDirectory('root/b');
  await button.onclick();
  assert.equal(app.writes.length, 0);
  await app.refresh();
  const currentButton = app.elements.leases.children[0].children.at(-1).children[0];
  await currentButton.onclick();
  assert.equal(app.writes.length, 1);
  assert.equal(app.writes[0].directory, 'root/b');
  assert.ok(app.writes[0].path.endsWith('/lease-b/release'));
});
