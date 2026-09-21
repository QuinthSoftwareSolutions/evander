import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../src/evander_figma_addon/ui_static/figma.js', import.meta.url), 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));
function settings(fileKey) {
  return {
    revision: 1,
    settings: {
      connections: {studio: {client_id: 'test', client_secret_env: 'TEST_FIGMA_SECRET',
        redirect_uri: 'https://evander.test/api/addons/figma-addon/oauth/callback'}},
      targets: {mockups: {name: fileKey, file_key: fileKey, connection_id: 'studio', access: ['read'], edit_roots: []}},
    },
    connections: {studio: {connected: false, authoring_ready: false}},
  };
}

function page() {
  const nodes = new Map(), all = [], requests = [];
  class Element {
    constructor(tag = 'input') {
      Object.assign(this, {tag, selectedValue: '', disabled: false, children: [], handlers: {}, data: {}});
      this.classList = {toggle() {}};
      all.push(this);
    }
    get value() { return this.selectedValue || (this.tag === 'select' ? this.children[0]?.value || '' : ''); }
    set value(value) { this.selectedValue = value; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; this.selectedValue = ''; }
    addEventListener(type, callback) { this.handlers[type] = callback; }
    reset() { this.data = {}; }
  }
  const get = selector => {
    if (!nodes.has(selector)) {
      const tag = selector.endsWith('-form') ? 'form' : selector.endsWith('-target') ? 'select' : 'input';
      const node = new Element(tag);
      node.elements = new Proxy({}, {get: (_, name) => get(`${selector} [name=${name}]`)});
      nodes.set(selector, node);
    }
    return nodes.get(selector);
  };
  const context = vm.createContext({
    document: {
      querySelector: get,
      querySelectorAll(selector) {
        if (selector === 'button, input, select') return all.filter(node => node.tag !== 'form');
        if (selector === 'form:not(#directory-form)') return all.filter(node => node.tag === 'form' && node !== get('#directory-form'));
        if (selector === '.target-select') return [get('#permissions-target'), get('#history-target')];
        return [];
      },
      createElement: tag => new Element(tag),
    },
    location: {search: '?directory=root/a', origin: 'https://evander.test'},
    history: {replaceState() {}}, URL, URLSearchParams, structuredClone,
    FormData: class { constructor(form) { return Object.entries(form.data); } },
    fetch(url, options) {
      return new Promise(resolve => requests.push({
        directory: new URL(url, 'https://evander.test').searchParams.get('directory'),
        method: options.method,
        body: options.body && JSON.parse(options.body),
        respond(body, ok = true) { resolve({ok, json: async () => structuredClone(body)}); },
      }));
    },
  });
  vm.runInContext(source, context);
  return {
    get, requests,
    submit(selector, data = {}) {
      const form = get(selector); form.data = data;
      // Invoke even if controls are disabled, to exercise the request guard as well.
      form.handlers.submit({preventDefault() {}, target: form, submitter: new Element('button')});
    },
  };
}

const permissions = {target_id: 'mockups', read: 'on', edit: 'on', roots: '12:34, 56:78'};

test('a pending directory switch cannot save the previous directory configuration', async () => {
  const ui = page();
  ui.requests[0].respond(settings('fileAAAA')); await settle();
  ui.get('#directory').value = 'root/b'; ui.submit('#directory-form');
  assert.equal(ui.requests[1].directory, 'root/b');
  assert.equal(ui.get('#directory').disabled, true);
  ui.submit('#permissions-form', permissions); await settle();
  assert.equal(ui.requests.length, 2);
  ui.requests[1].respond(settings('fileBBBB')); await settle();
  ui.submit('#permissions-form', permissions); await settle();
  const write = ui.requests[2];
  assert.equal(write.method, 'PUT');
  assert.equal(write.directory, 'root/b');
  assert.equal(write.body.settings.targets.mockups.file_key, 'fileBBBB');
  assert.equal(write.body.expected_revision, 1);
  assert.deepEqual(write.body.settings.targets.mockups.access, ['read', 'edit']);
  assert.deepEqual(write.body.settings.targets.mockups.edit_roots, ['12:34', '56:78']);
  assert.equal('grants' in write.body.settings.targets.mockups, false);
});

test('failed directory loads preserve the loaded configuration and its directory', async () => {
  const ui = page();
  ui.requests[0].respond(settings('fileAAAA')); await settle();
  ui.get('#directory').value = 'root/b'; ui.submit('#directory-form');
  ui.requests[1].respond({error: 'Load failed'}, false); await settle();
  assert.equal(ui.get('#directory').value, 'root/a');
  assert.equal(ui.get('#directory').disabled, false);
  ui.submit('#permissions-form', permissions); await settle();
  assert.equal(ui.requests[2].directory, 'root/a');
  assert.equal(ui.requests[2].body.settings.targets.mockups.file_key, 'fileAAAA');
});

test('initial loading and pending saves serialize directory changes', async () => {
  const ui = page();
  ui.submit('#permissions-form', permissions); await settle();
  assert.equal(ui.requests.length, 1);
  ui.requests[0].respond(settings('fileAAAA')); await settle();
  ui.submit('#permissions-form', permissions);
  ui.get('#directory').value = 'root/b'; ui.submit('#directory-form'); await settle();
  assert.equal(ui.requests.length, 2);
  assert.equal(ui.requests[1].directory, 'root/a');
  ui.requests[1].respond({revision: 2}); await settle();
  assert.equal(ui.requests[2].directory, 'root/a');
  ui.requests[2].respond(settings('fileAAAA')); await settle();
  assert.equal(ui.get('#directory').value, 'root/a');
});

test('selecting a file loads its own permissions and preserves other files on save', async () => {
  const ui = page(), data = settings('fileAAAA');
  data.settings.targets.mockups.edit_roots = ['12:34'];
  data.settings.targets.other = {name: 'Other file', file_key: 'fileBBBB', connection_id: 'studio', access: ['read', 'edit'], edit_roots: ['56:78']};
  ui.requests[0].respond(data); await settle();
  const form = ui.get('#permissions-form');
  assert.equal(form.elements.read.checked, true);
  assert.equal(form.elements.edit.checked, false);
  assert.equal(form.elements.roots.value, '12:34');
  const select = ui.get('#permissions-target');
  select.value = 'other'; select.handlers.change();
  assert.equal(form.elements.edit.checked, true);
  assert.equal(form.elements.roots.value, '56:78');
  ui.submit('#permissions-form', {target_id: 'other', roots: '56:78'}); await settle();
  const saved = ui.requests[1].body.settings.targets;
  assert.deepEqual(saved.mockups, data.settings.targets.mockups);
  assert.deepEqual(saved.other.access, []);
  assert.deepEqual(saved.other.edit_roots, ['56:78']);
});
