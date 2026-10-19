import assert from "node:assert/strict";
import test from "node:test";
import { BrowserEngine } from "../dist/engine.mjs";

test("pause between focus and key dispatch prevents entering a value", async () => {
  let state = { id: "b", controller: "agent:a", control_generation: 1,
    allowed_origins: ["https://application.test"], expires_at: Date.now()/1000+60 };
  const calls = [];
  let engine;
  const send = async (method, params) => {
    calls.push(method);
    if (method === "Page.getFrameTree") return { frameTree: { frame: { url: "https://application.test/" } } };
    if (method === "Accessibility.getFullAXTree") return { nodes: [{ backendDOMNodeId: 10, role: {value:"textbox"}, name:{value:"Material"} }] };
    if (method === "Page.captureScreenshot") return { data: "jpeg" };
    if (method === "Page.getLayoutMetrics") return { cssVisualViewport: { clientWidth: 800, clientHeight: 600 } };
    if (method === "DOM.focus") {
      state = { ...state, controller: null, control_generation: 2 }; engine.update(state);
    }
    return {};
  };
  engine = new BrowserEngine(send, state, () => {});
  const observed = await engine.execute({action:"observe", generation:1}, "agent:a");
  await assert.rejects(engine.execute({action:"fill", generation:1, observation_id:observed.observation_id, ref:"node-10", text:"do not enter"}, "agent:a"), /stale_control/);
  assert.ok(!calls.includes("Input.insertText"));
  assert.ok(!calls.includes("Input.dispatchKeyEvent"));
});
