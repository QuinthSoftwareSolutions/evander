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

for (const notify of [true, false]) {
  test("navigation during focus prevents input on another origin" + (notify ? "" : " before its event"), async () => {
    const state = { id: "b", controller: "agent:a", control_generation: 1,
      allowed_origins: ["https://application.test"], expires_at: Date.now()/1000+60 };
    let url = "https://application.test/", engine;
    const inputs = [];
    const send = async (method) => {
      if (method === "Page.getFrameTree") return {frameTree:{frame:{url}}};
      if (method === "Accessibility.getFullAXTree") return {nodes:[
        {backendDOMNodeId:10,role:{value:"textbox"},name:{value:"Material"}}]};
      if (method === "Page.captureScreenshot") return {data:"jpeg"};
      if (method === "Page.getLayoutMetrics") return {cssVisualViewport:{clientWidth:800,clientHeight:600}};
      if (method === "DOM.focus") {
        url = "https://unapproved.test/";
        if (notify) engine.navigate();
      }
      if (method.startsWith("Input.")) inputs.push(method);
      return {};
    };
    engine = new BrowserEngine(send, state, () => {});
    const observed = await engine.execute({action:"observe",generation:1},"agent:a");
    await assert.rejects(engine.execute({action:"fill",generation:1,observation_id:observed.observation_id,
      ref:"node-10",text:"private value"},"agent:a"), /stale_observation|origin_denied/);
    assert.deepEqual(inputs, [], "No input may follow the origin change");
  });
}

test("navigation after mouse-down fences the remaining mouse input", async () => {
  const state = {id:"b",controller:"agent:a",control_generation:1,
    allowed_origins:["https://application.test"],expires_at:Date.now()/1000+60};
  let engine, url="https://application.test/";
  const inputs=[];
  const send=async(method,params)=>{
    if(method==="Page.getFrameTree")return {frameTree:{frame:{url}}};
    if(method==="Accessibility.getFullAXTree")return {nodes:[]};
    if(method==="Page.captureScreenshot")return {data:"jpeg"};
    if(method==="Page.getLayoutMetrics")return {cssVisualViewport:{clientWidth:800,clientHeight:600}};
    if(method==="Input.dispatchMouseEvent"){
      inputs.push(params.type);
      if(params.type==="mousePressed"){url="https://unapproved.test/";engine.navigate();}
    }
    return {};
  };
  engine=new BrowserEngine(send,state,()=>{});
  const observed=await engine.execute({action:"observe",generation:1},"agent:a");
  await assert.rejects(engine.execute({action:"click",generation:1,observation_id:observed.observation_id,
    x:20,y:20},"agent:a"),/stale_observation|origin_denied/);
  assert.deepEqual(inputs,["mouseMoved","mousePressed"]);
});

test("observation rejects a changed origin before returning page contents", async () => {
  const state={id:"b",controller:null,control_generation:1,
    allowed_origins:["https://application.test"],expires_at:Date.now()/1000+60};
  let url="https://application.test/";
  const send=async(method)=>{
    if(method==="Page.getFrameTree")return {frameTree:{frame:{url}}};
    if(method==="Accessibility.getFullAXTree")return {nodes:[]};
    if(method==="Page.captureScreenshot"){url="https://unapproved.test/";return {data:"private screenshot"};}
    return {};
  };
  const engine=new BrowserEngine(send,state,()=>{});
  await assert.rejects(engine.execute({action:"observe",generation:1},"agent:a"),/origin_denied/);
});

for (const interruption of ["pause", "origin"]) {
  for (const action of ["type", "click"]) {
    test(action + " stops after " + interruption + " interrupts an input primitive", async () => {
      let state = {id:"b", controller:"agent:a", control_generation:1,
        allowed_origins:["https://application.test"], expires_at:Date.now()/1000+60};
      let engine, url = "https://application.test/";
      const inputs = [];
      const send = async (method, params) => {
        if (method === "Page.getFrameTree") return {frameTree:{frame:{url}}};
        if (method === "Accessibility.getFullAXTree") return {nodes:[
          {backendDOMNodeId:10,role:{value:"button"},name:{value:"Select"}}]};
        if (method === "Page.captureScreenshot") return {data:"jpeg"};
        if (method === "Page.getLayoutMetrics") return {cssVisualViewport:{clientWidth:800,clientHeight:600}};
        if (method.startsWith("Input.") || method === "DOM.scrollIntoViewIfNeeded") {
          inputs.push({method, type:params.type, key:params.key});
          if (interruption === "pause") {
            state = {...state, controller:null, control_generation:2}; engine.update(state);
          } else { url = "https://unapproved.test/"; }
        }
        return {};
      };
      engine = new BrowserEngine(send, state, () => {});
      const observation = await engine.execute({action:"observe",generation:1},"agent:a");
      await assert.rejects(engine.execute({action,generation:1,ref:"node-10",
        observation_id:observation.observation_id,text:"AB"},"agent:a"), /stale_control|origin_denied/);
      assert.deepEqual(inputs, action === "type"
        ? [{method:"Input.dispatchKeyEvent",type:"keyDown",key:"A"}]
        : [{method:"DOM.scrollIntoViewIfNeeded",type:undefined,key:undefined}]);
    });
  }
}
