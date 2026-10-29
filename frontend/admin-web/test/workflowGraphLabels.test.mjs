import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { after, before, test } from "node:test";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

let server;
let workflowGraphLabels;
let cacheDir;

before(async () => {
  cacheDir = await mkdtemp(join(tmpdir(), "evander-graph-labels-"));
  server = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)),
    cacheDir,
    server: { middlewareMode: true, watch: null },
    optimizeDeps: { noDiscovery: true, include: [] },
    logLevel: "error",
  });
  ({ workflowGraphLabels } = await server.ssrLoadModule(
    "/src/features/workflow-editor/canvasModel.tsx",
  ));
});
after(async () => {
  await server?.close();
  if (cacheDir) await rm(cacheDir, { recursive: true, force: true });
});

function graph(nodeIds) {
  return { id: "test-flow", name: "Test flow", directory_path: "root", user_ids: [], node_ids: nodeIds };
}

function node(id, x, y, height = 80) {
  return { id, position: { x, y }, measured: { width: 200, height }, data: {} };
}

test("labels follow the uppermost node in the leftmost column despite branches above and below", () => {
  const flow = graph(["lower-left", "above-right", "entry", "end"]);
  const nodes = [
    node("end", 900, 1400, 200),
    node("above-right", 500, 20),
    node("lower-left", 100, 500),
    node("entry", 100, 240),
  ];
  const viewport = { x: 30, y: -10, zoom: 0.5 };

  assert.deepEqual(workflowGraphLabels([flow], nodes, viewport), [
    { graph: flow, left: 56, top: 130 },
  ]);

  nodes[0].position.y = 3000;
  assert.deepEqual(workflowGraphLabels([flow], nodes, viewport), [
    { graph: flow, left: 56, top: 130 },
  ]);
});

test("labels ignore missing and unrelated nodes and use fallback node dimensions", () => {
  const flow = graph(["missing", "entry"]);
  const nodes = [node("unrelated", -1000, -1000), { id: "entry", position: { x: -40, y: 120 }, data: {} }];

  assert.deepEqual(workflowGraphLabels([flow], nodes, { x: 8, y: 10, zoom: 2 }), [
    { graph: flow, left: -96, top: 358 },
  ]);
  assert.deepEqual(workflowGraphLabels([graph(["missing"])], nodes, { x: 0, y: 0, zoom: 1 }), []);
});
