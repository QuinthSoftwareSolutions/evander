import test from "node:test";
import assert from "node:assert/strict";
import {once} from "node:events";
import {WebSocket, WebSocketServer} from "ws";
import {automationBridge} from "../dist/automation.mjs";

async function fixture(focus, respond=(message,socket)=>socket.send(JSON.stringify({id: message.id, sessionId: message.sessionId, result: {}}))) {
  const chrome = new WebSocketServer({host: "127.0.0.1", port: 0});
  await once(chrome, "listening");
  const forwarded = [];
  let peer;
  chrome.on("connection", socket => {
    peer=socket;
    socket.send(JSON.stringify({method: "Target.attachedToTarget", params: {sessionId: "page-session", targetInfo: {targetId: "page-target"}}}));
    socket.on("message", raw => {
      const message = JSON.parse(String(raw));
      forwarded.push(message);
      respond(message,socket);
    });
  });
  const state = {id: "browser-test", agent_session_id: "agent-test", controller: "agent:agent-test", control_generation: 0, expires_at: Date.now()/1000+60, allowed_origins: ["https://app.example.test"]};
  const bridge = automationBridge({control_port: 0, playwright_enabled: true, allowed_origins: state.allowed_origins, endpoint_token: "worker-proof"}, "ws://127.0.0.1:"+chrome.address().port, async()=>{}, state, focus);
  const port = await bridge.listening;
  const control=async body=>{
    const response=await fetch("http://127.0.0.1:"+port+"/control", {method: "POST", headers: {Authorization: "Bearer worker-proof", "Content-Type": "application/json"}, body: JSON.stringify(body), signal: AbortSignal.timeout(2000)});
    assert.equal(response.status,200);
    return response.json();
  };
  await control({session:state,automation_token:"agent-proof"});
  const client = new WebSocket("ws://127.0.0.1:"+port+"/connect", {headers: {Authorization: "Bearer agent-proof", "X-Evander-Control-Generation": "0"}});
  const attached = once(client, "message");
  await once(client, "open");
  await attached;
  return {client, forwarded, peer, state, bridge, control, close: async()=>{
    client.terminate();
    for(const socket of chrome.clients)socket.terminate();
    await bridge.close();
    await new Promise(resolve=>chrome.close(resolve));
  }};
}

for (const [label, error, sync] of [
  ["missing queued page",new Error("page_unavailable"),false],
  ["closed queued target",new Error("cdpSession.send: Target page, context or browser has been closed"),false],
  ["closed CDP session",new Error("Protocol error (Page.startScreencast): Session closed. Most likely the page has been closed."),false],
  ["revoked focus",new Error("stale_control"),false],
  ["synchronous closed target",new Error("Target closed"),true],
]) {
  test(`background focus handles ${label} without changing explicit CDP replies`, {timeout: 5000}, async()=>{
    const f=await fixture(sync?()=>{throw error;}:async()=>{throw error;});
    try {
      const inputReply=once(f.client,"message");
      f.client.send(JSON.stringify({id: 1,sessionId: "page-session",method: "Input.dispatchMouseEvent",params: {type:"mouseMoved",x:1,y:1}}));
      assert.equal(JSON.parse(String((await inputReply)[0])).id,1);
      const nextReply=once(f.client,"message");
      f.client.send(JSON.stringify({id: 2,method: "Browser.getVersion"}));
      assert.equal(JSON.parse(String((await nextReply)[0])).id,2);
      assert.equal(f.client.readyState,WebSocket.OPEN);
      assert.equal(f.forwarded.length,2);
    } finally {await f.close();}
  });
}

test("unexpected background focus failure interrupts automation with a safe error", {timeout: 5000}, async()=>{
  const f=await fixture(async()=>{throw new Error("unexpected private upstream detail");});
  try {
    const closed=once(f.client,"close");
    f.client.send(JSON.stringify({id:1,sessionId:"page-session",method:"Input.dispatchMouseEvent",params:{type:"mouseMoved",x:1,y:1}}));
    const [code,reason]=await closed;
    assert.equal(code,1011);
    assert.equal(String(reason),"browser_focus_failed");
    assert.ok(!String(reason).includes("private"));
  } finally {await f.close();}
});

test("an explicit CDP target error remains visible after a harmless focus interruption", {timeout:5000},async()=>{
  const f=await fixture(async()=>{throw new Error("page_unavailable");},(message,socket)=>{
    socket.send(JSON.stringify({id:message.id,sessionId:message.sessionId,error:{code:-32000,message:"Target closed"}}));
  });
  try {
    const reply=once(f.client,"message");
    f.client.send(JSON.stringify({id:1,sessionId:"page-session",method:"Input.dispatchMouseEvent",params:{type:"mouseMoved",x:1,y:1}}));
    assert.deepEqual(JSON.parse(String((await reply)[0])).error,{code:-32000,message:"Target closed"});
    assert.equal(f.client.readyState,WebSocket.OPEN);
  } finally {await f.close();}
});

async function until(check) {
  const deadline=Date.now()+1500;
  while(Date.now()<deadline) {
    const value=check();if(value)return value;
    await new Promise(resolve=>setTimeout(resolve,5));
  }
  throw new Error("Timed out waiting for synthetic CDP state");
}

test("physical target closure retires only its waits; session detach does not bypass a live-target fence", {timeout:5000},async()=>{
  const held=new Map();
  const f=await fixture(async()=>{},(message,socket)=>{
    if(message.method.startsWith("Input."))held.set(message.id,{message,socket});
    else socket.send(JSON.stringify({id:message.id,result:{}}));
  });
  try {
    const received=[];
    f.client.on("message",raw=>received.push(JSON.parse(String(raw))));
    f.peer.send(JSON.stringify({method:"Target.attachedToTarget",params:{sessionId:"live-session",targetInfo:{targetId:"live-target"}}}));
    await until(()=>received.some(message=>message.params?.sessionId==="live-session"));
    for(const [id,sessionId] of [[1,"page-session"],[2,"live-session"]])
      f.client.send(JSON.stringify({id,sessionId,method:"Input.dispatchMouseEvent",params:{type:"mouseMoved",x:1,y:1}}));
    await until(()=>held.size===2);
    f.bridge.targetDestroyed("page-target");
    assert.deepEqual((await until(()=>received.find(message=>message.id===1))).error,{code:-32000,message:"Target closed"});
    // A delayed browser response must not become a second reply to the retired request.
    f.peer.send(JSON.stringify({id:1,sessionId:"page-session",result:{}}));
    f.client.send(JSON.stringify({id:3,method:"Browser.getVersion"}));
    await until(()=>received.some(message=>message.id===3));
    assert.equal(received.filter(message=>message.id===1).length,1);
    f.peer.send(JSON.stringify({method:"Target.detachedFromTarget",params:{sessionId:"live-session",targetId:"live-target"}}));
    await until(()=>received.some(message=>message.method==="Target.detachedFromTarget"));
    let settled=false;
    const disconnected=once(f.client,"close");
    const fencing=f.control({session:{...f.state,controller:"user:operator",control_generation:1}}).then(()=>{settled=true;});
    await disconnected;
    await new Promise(resolve=>setTimeout(resolve,20));
    assert.equal(settled,false,"A detached but live target still has unacknowledged input");
    held.get(2).socket.send(JSON.stringify({id:2,sessionId:"live-session",result:{}}));
    await fencing;
    assert.equal(settled,true);
  } finally {await f.close();}
});
