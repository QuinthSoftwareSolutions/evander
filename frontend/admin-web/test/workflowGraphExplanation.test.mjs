import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

let server;
let workflowGraphExplanationRequest;
let sanitizeGraphExplanationValue;
before(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)),
    server: { middlewareMode: true, watch: null },
    optimizeDeps: { noDiscovery: true, include: [] }, logLevel: "error",
  });
  ({ workflowGraphExplanationRequest, sanitizeGraphExplanationValue } = await server.ssrLoadModule("/src/features/workflow-editor/graphExplanation.ts"));
});
after(async () => { await server?.close(); });

// A mixed graph must retain executable routes and bindings while excluding other graphs and host settings.
test("graph explanations retain draft behavior and include only the selected graph's context", () => {
  const node = (id, type, config = {}) => ({ id, type, display_name: id, config });
  const catalogEntry = (type) => ({ type, kind: type.split(":")[0], label: type, description: "Provider behavior", fields: [], input_ports: [], output_ports: [] });
  const document = {
    revision: "saved-7", selected_workflow_pack: { reference: "operator-pack" },
    config: {
      runtime_dir: "/private/runtime", unrelated_setting: "host-only-setting",
      triggers: [node("entry", "trigger:event", { enabled: false, event_type: "demo.requested" }), node("other", "trigger:cron")],
      gates: [node("decision", "gate:condition", { condition: "{{ entry.accepted }}" })],
      queries: [node("lookup", "query:record")], effects: [node("action", "effect:message", { text: "Unsaved new message" })],
      edges: [
        { source_id: "entry", source_port: "completed", target_id: "lookup" },
        { source_id: "lookup", target_id: "decision" },
        { source_id: "decision", source_port: "true", target_id: "action", allow_cycle: true },
        { source_id: "other", target_id: "action" },
      ],
    },
    output_schemas: { lookup: { result: { type: "object" } }, other: { private: { type: "string" } } },
  };
  const graph = { id: "chosen", name: "Selected graph", directory_path: "root/demo", node_ids: ["entry", "lookup", "decision", "action"], user_ids: [] };
  const catalog = { nodes: ["trigger:event", "gate:condition", "query:record", "effect:message", "trigger:cron"].map(catalogEntry) };
  const before = JSON.stringify(document);
  const prompt = workflowGraphExplanationRequest(document, graph, catalog, true);
  const snapshot = prompt.context;
  assert.equal(snapshot.has_unsaved_changes, true);
  assert.equal(snapshot.saved_revision, "saved-7");
  assert.equal(snapshot.workflow_pack, "operator-pack");
  assert.deepEqual(snapshot.config.triggers.map(({ id }) => id), ["entry"]);
  assert.equal(snapshot.config.triggers[0].config.enabled, false);
  assert.equal(snapshot.config.gates[0].config.condition, "{{ entry.accepted }}");
  assert.equal(snapshot.config.effects[0].config.text, "Unsaved new message");
  assert.deepEqual(snapshot.config.edges.map(({ source_id, target_id, source_port, allow_cycle }) => [source_id, target_id, source_port, allow_cycle]), [
    ["entry", "lookup", "completed", undefined], ["lookup", "decision", undefined, undefined], ["decision", "action", "true", true],
  ]);
  assert.deepEqual(Object.keys(snapshot.output_schemas), ["lookup"]);
  assert.deepEqual(snapshot.node_catalog.map(({ type }) => type), ["trigger:event", "gate:condition", "query:record", "effect:message"]);
  assert.equal(JSON.stringify(prompt.context).includes("host-only-setting"), false);
  assert.equal(JSON.stringify(prompt.context).includes("/private/runtime"), false);
  assert.equal(JSON.stringify(document), before, "asking for an explanation must not mutate the editor document");
});


test("graph context removes sensitive nested values, credentials in URLs, and authorization strings", () => {
  const sanitized = sanitizeGraphExplanationValue({
    apiKey: "synthetic-key", nested: [{ password: "synthetic-password", clientSecret: "synthetic-secret", pat: "synthetic-pat" }],
    headers: { "X-Custom": "synthetic-header" }, environment: { APP_VALUE: "synthetic-env" },
    url: "https://synthetic-user:synthetic-pass@example.test/path?value=synthetic-query#synthetic-fragment",
    prompt: "Send with Bearer synthetic-bearer", event_type: "demo.requested", binding: "${{ entry.result }}", path: "/allowed/path",
  });
  const encoded = JSON.stringify(sanitized);
  assert.equal(encoded.includes("synthetic-"), false);
  assert.equal(sanitized.event_type, "demo.requested");
  assert.equal(sanitized.binding, "${{ entry.result }}");
  assert.equal(sanitized.path, "/allowed/path");
  assert.equal(new URL(sanitized.url).hostname, "example.test");
});
