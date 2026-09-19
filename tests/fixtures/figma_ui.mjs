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
      targets: {mockups: {name: fileKey, file_key: fileKey, connection_id: 'studio', grants: {}}},
    },
    connections: {studio: {connected: false, authoring_ready: false}},
  };
}

function page() {
  const nodes = new Map(), all = [], requests = [];
  class Element {
    constructor(tag = 'input') {
      Object.assign(this, {tag, value: '', disabled: false, children: [], handlers: {}, data: {}});
      this.classList = {toggle() {}};
      all.push(this);
    }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    addEventListener(type, callback) { this.handlers[type] = callback; }
    reset() { this.data = {}; }
  }
  const get = selector => {
    if (!nodes.has(selector)) nodes.set(selector, new Element(selector.endsWith('-form') ? 'form' : 'input'));
    return nodes.get(selector);
  };
  const context = vm.createContext({
    document: {
      querySelector: get,
      querySelectorAll(selector) {
        if (selector === 'button, input, select') return all.filter(node => node.tag !== 'form');
        if (selector === 'form:not(#directory-form)') return all.filter(node => node.tag === 'form' && node !== get('#directory-form'));
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

const grant = {target_id: 'mockups', session_id: 'designer-b', read: 'on', edit: 'on', roots: ''};

test('a pending directory switch cannot save the previous directory configuration', async () => {
  const ui = page();
  ui.requests[0].respond(settings('fileAAAA')); await settle();
  ui.get('#directory').value = 'root/b'; ui.submit('#directory-form');
  assert.equal(ui.requests[1].directory, 'root/b');
  assert.equal(ui.get('#directory').disabled, true);
  ui.submit('#grant-form', grant); await settle();
  assert.equal(ui.requests.length, 2);
  ui.requests[1].respond(settings('fileBBBB')); await settle();
  ui.submit('#grant-form', grant); await settle();
  const write = ui.requests[2];
  assert.equal(write.method, 'PUT');
  assert.equal(write.directory, 'root/b');
  assert.equal(write.body.settings.targets.mockups.file_key, 'fileBBBB');
  assert.equal(write.body.expected_revision, 1);
});

test('failed directory loads preserve the loaded configuration and its directory', async () => {
  const ui = page();
  ui.requests[0].respond(settings('fileAAAA')); await settle();
  ui.get('#directory').value = 'root/b'; ui.submit('#directory-form');
  ui.requests[1].respond({error: 'Load failed'}, false); await settle();
  assert.equal(ui.get('#directory').value, 'root/a');
  assert.equal(ui.get('#directory').disabled, false);
  ui.submit('#grant-form', grant); await settle();
  assert.equal(ui.requests[2].directory, 'root/a');
  assert.equal(ui.requests[2].body.settings.targets.mockups.file_key, 'fileAAAA');
});

test('initial loading and pending saves serialize directory changes', async () => {
  const ui = page();
  ui.submit('#grant-form', grant); await settle();
  assert.equal(ui.requests.length, 1);
  ui.requests[0].respond(settings('fileAAAA')); await settle();
  ui.submit('#grant-form', grant);
  ui.get('#directory').value = 'root/b'; ui.submit('#directory-form'); await settle();
  assert.equal(ui.requests.length, 2);
  assert.equal(ui.requests[1].directory, 'root/a');
  ui.requests[1].respond({revision: 2}); await settle();
  assert.equal(ui.requests[2].directory, 'root/a');
  ui.requests[2].respond(settings('fileAAAA')); await settle();
  assert.equal(ui.get('#directory').value, 'root/a');
});
