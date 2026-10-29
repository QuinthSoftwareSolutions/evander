import test from "node:test";
import assert from "node:assert/strict";
import {createServer} from "node:net";
import {once} from "node:events";
import {WebSocket,WebSocketServer} from "ws";
import {chromium} from "playwright";
import {automationBridge,observeTargetDestruction} from "../dist/automation.mjs";

async function until(read,label) {
  const deadline=Date.now()+2000;
  while(Date.now()<deadline) {
    const value=read();if(value)return value;
    await new Promise(resolve=>setTimeout(resolve,5));
  }
  throw new Error("Timed out: "+label);
}

test("observer disconnect does not release held input while the owner's target remains alive",{timeout:15000},async()=>{
  const probe=createServer();probe.listen(0,"127.0.0.1");await once(probe,"listening");
  const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  let owner,observer,agent,bridge,proxy,page;
  const upstreams=new Set(),held=new Map(),destroyed=[];
  try {
    owner=await chromium.launch({headless:true,args:["--remote-debugging-port="+port]});
    page=await owner.newPage();
    const ownerPage=await page.context().newCDPSession(page);
    const {targetInfo}=await ownerPage.send("Target.getTargetInfo");
    const endpoint=(await fetch("http://127.0.0.1:"+port+"/json/version").then(response=>response.json())).webSocketDebuggerUrl;
    observer=await chromium.connectOverCDP(endpoint);
    const mirror=observer.contexts().flatMap(context=>context.pages()).find(item=>item.url()==="about:blank");
    assert.ok(mirror);
    let observerPageClosed=false;
    mirror.on("close",()=>{observerPageClosed=true;});
    // Hold a real Input response so the fence cannot finish through normal ACKs.
    proxy=new WebSocketServer({host:"127.0.0.1",port:0});await once(proxy,"listening");
    proxy.on("connection",client=>{
      const upstream=new WebSocket(endpoint);upstreams.add(upstream);
      const queue=[],inputIds=new Set();
      client.on("message",raw=>{
        const message=JSON.parse(String(raw));
        if(message.method.startsWith("Input."))inputIds.add(message.id);
        if(upstream.readyState===WebSocket.OPEN)upstream.send(String(raw));else queue.push(String(raw));
      });
      upstream.on("open",()=>{for(const raw of queue)upstream.send(raw);queue.length=0;});
      upstream.on("message",raw=>{
        const message=JSON.parse(String(raw));
        if(inputIds.has(message.id)){held.set(message.id,message);return;}
        if(client.readyState===WebSocket.OPEN)client.send(String(raw));
      });
      upstream.on("close",()=>{upstreams.delete(upstream);client.close();});
      upstream.on("error",()=>upstream.close());
      client.on("close",()=>upstream.close());
      client.on("error",()=>upstream.close());
    });
    let state={id:"browser-lifecycle",agent_session_id:"agent-one",controller:"agent:agent-one",control_generation:0,expires_at:Date.now()/1000+60,allowed_origins:["https://app.example.test"]};
    bridge=automationBridge({control_port:0,playwright_enabled:true,allowed_origins:state.allowed_origins,endpoint_token:"worker-proof"},"ws://127.0.0.1:"+proxy.address().port,async()=>{},state);
    await observeTargetDestruction(owner,id=>{destroyed.push(id);bridge.targetDestroyed(id);});
    const controlPort=await bridge.listening;
    const control=async body=>{
      const response=await fetch("http://127.0.0.1:"+controlPort+"/control",{method:"POST",headers:{Authorization:"Bearer worker-proof","Content-Type":"application/json"},body:JSON.stringify(body),signal:AbortSignal.timeout(5000)});
      assert.equal(response.status,200,await response.text());
    };
    await control({session:state,automation_token:"agent-proof"});
    agent=new WebSocket("ws://127.0.0.1:"+controlPort+"/connect",{headers:{Authorization:"Bearer agent-proof","X-Evander-Control-Generation":"0"}});
    const replies=[];
    agent.on("message",raw=>replies.push(JSON.parse(String(raw))));
    await once(agent,"open");
    agent.send(JSON.stringify({id:1,method:"Target.attachToTarget",params:{targetId:targetInfo.targetId,flatten:true}}));
    const attachment=await until(()=>replies.find(message=>message.id===1),"agent target attached");
    assert.ok(attachment.result?.sessionId);
    agent.send(JSON.stringify({id:2,sessionId:attachment.result.sessionId,method:"Input.dispatchMouseEvent",params:{type:"mouseMoved",x:1,y:1}}));
    await until(()=>held.size===1,"input acknowledgement held");
    await observer.close();
    assert.equal(observerPageClosed,true,"observer-local Page.close must occur in the regression");
    assert.equal(owner.isConnected(),true);
    assert.equal(page.isClosed(),false);
    assert.equal(await page.evaluate(()=>42),42);
    assert.ok(!destroyed.includes(targetInfo.targetId),"transport loss is not target destruction");
    let takeoverDone=false;
    const detached=once(agent,"close");
    state={...state,controller:"user:operator",control_generation:1};
    const takeover=control({session:state}).then(()=>{takeoverDone=true;});
    await detached;
    await new Promise(resolve=>setTimeout(resolve,30));
    assert.equal(takeoverDone,false,"held input in a live page must keep takeover pending");
    assert.equal(await page.evaluate(()=>42),42);
    await page.close();
    await until(()=>destroyed.includes(targetInfo.targetId),"independent target destruction observed");
    await takeover;
    assert.equal(takeoverDone,true);
  } finally {
    agent?.terminate();
    await owner?.close().catch(()=>{});
    await observer?.close().catch(()=>{});
    await bridge?.close();
    if(proxy){for(const socket of proxy.clients)socket.terminate();for(const socket of upstreams)socket.terminate();await new Promise(resolve=>proxy.close(resolve));}
  }
});
