import test from "node:test";
import assert from "node:assert/strict";
import {createServer} from "node:http";
import {once} from "node:events";
import {chromium} from "playwright";
import {automationBridge} from "../dist/automation.mjs";

test("Playwright shares actual pages, takeover preserves login and fences old input",{timeout:45000},async()=>{
  let clicks=0;
  const fixture=createServer((req,res)=>{if(req.url==="/identity"){res.end(req.headers.cookie?.includes("signed-in=yes")?"signed-in":"anonymous");return;}if(req.url==="/click"){clicks++;res.end("ok");return;}res.setHeader("Content-Type","text/html");res.end('<button onclick="fetch(\'/click\')">Run</button>');});
  fixture.listen(0,"127.0.0.1");await once(fixture,"listening");
  const origin="http://127.0.0.1:"+fixture.address().port;
  const portServer=createServer();portServer.listen(0,"127.0.0.1");await once(portServer,"listening");const debugPort=portServer.address().port;await new Promise(resolve=>portServer.close(resolve));
  let owner,observer,bridge,agent;
  try {
    owner=await chromium.launch({headless:true,args:["--remote-debugging-port="+debugPort]});
    observer=await chromium.connectOverCDP("http://127.0.0.1:"+debugPort);
    const endpoint=await fetch("http://127.0.0.1:"+debugPort+"/json/version").then(r=>r.json());
    let state={id:"browser-synthetic",agent_session_id:"agent-one",controller:"agent:agent-one",control_generation:0,expires_at:Date.now()/1000+60,allowed_origins:[origin]};
    bridge=automationBridge({control_port:0,playwright_enabled:true,allowed_origins:[origin],endpoint_token:"worker-proof"},endpoint.webSocketDebuggerUrl,async()=>{},state);
    const controlPort=await bridge.listening;
    async function control(body){const response=await fetch("http://127.0.0.1:"+controlPort+"/control",{method:"POST",headers:{Authorization:"Bearer worker-proof","Content-Type":"application/json"},body:JSON.stringify(body)});assert.equal(response.status,200);assert.equal((await response.json()).fenced,true);}
    await assert.rejects(chromium.connectOverCDP("ws://127.0.0.1:"+controlPort+"/connect",{headers:{Authorization:"Bearer ","X-Evander-Control-Generation":"0"},timeout:500}));
    await control({session:state,automation_token:"agent-proof"});
    const url="ws://127.0.0.1:"+controlPort+"/connect";
    agent=await chromium.connectOverCDP(url,{headers:{Authorization:"Bearer agent-proof","X-Evander-Control-Generation":"0"}});
    const context=await agent.newContext();
    await context.addCookies([{name:"signed-in",value:"yes",url:origin,httpOnly:true}]);
    const page=await context.newPage();await page.goto(origin);await page.evaluate(()=>sessionStorage.setItem("operator","1"));
    const sibling=await context.newPage();await sibling.goto(origin+"/operator");
    await page.getByRole("button").click();
    for(let i=0;i<50 && clicks!==1;i++)await new Promise(r=>setTimeout(r,10));
    assert.equal(clicks,1);
    const disconnected=once(agent,"disconnected");
    state={...state,controller:"user:operator",control_generation:1};
    await control({session:state});await disconnected;
    await assert.rejects(page.getByRole("button").click({timeout:500}));
    const live=observer.contexts().flatMap(c=>c.pages());
    assert.equal(live.length,2);
    const retained=live.find(p=>p.url()===origin+"/");
    assert.equal(await retained.evaluate(()=>sessionStorage.getItem("operator")),"1");
    assert.equal(await retained.evaluate(()=>fetch("/identity").then(r=>r.text())),"signed-in");
    await assert.rejects(chromium.connectOverCDP(url,{headers:{Authorization:"Bearer agent-proof","X-Evander-Control-Generation":"0"},timeout:500}));
    state={...state,controller:"agent:agent-one",control_generation:2};
    await control({session:state,automation_token:"fresh-agent-proof"});
    agent=await chromium.connectOverCDP(url,{headers:{Authorization:"Bearer fresh-agent-proof","X-Evander-Control-Generation":"2"}});
    const resumed=agent.contexts().flatMap(c=>c.pages()).find(p=>p.url()===origin+"/");
    await resumed.getByRole("button").click();
    for(let i=0;i<50 && clicks!==2;i++)await new Promise(r=>setTimeout(r,10));
    assert.equal(clicks,2);
    const cdp=await agent.newBrowserCDPSession();
    for(const [method,params] of [["Browser.close",{}],["DOM.setFileInputFiles",{files:["/run/browser.json"]}],["Target.exposeDevToolsProtocol",{targetId:"any"}],["Target.sendMessageToTarget",{message:"{}"}],["Network.loadNetworkResource",{url:"file:///run/browser.json"}]]) {
      await assert.rejects(cdp.send(method,params),/Evander browser policy denied/);
    }
    await agent.close();agent=undefined;
    assert.equal(observer.isConnected(),true);
    assert.equal(observer.contexts().flatMap(c=>c.pages()).length,2);
    // A normal detach also consumes its descriptor; reconnection needs a new grant.
    await assert.rejects(chromium.connectOverCDP(url,{headers:{Authorization:"Bearer fresh-agent-proof","X-Evander-Control-Generation":"2"},timeout:500}));
    await control({session:state,automation_token:"reconnected-agent-proof"});
    agent=await chromium.connectOverCDP(url,{headers:{Authorization:"Bearer reconnected-agent-proof","X-Evander-Control-Generation":"2"}});
    const reconnected=agent.contexts().flatMap(c=>c.pages()).find(p=>p.url()===origin+"/");
    assert.equal(await reconnected.evaluate(()=>sessionStorage.getItem("operator")),"1");
    assert.equal(await reconnected.evaluate(()=>fetch("/identity").then(r=>r.text())),"signed-in");
  } finally {
    await agent?.close().catch(()=>{});await bridge?.close();await observer?.close();await owner?.close();
    await new Promise(resolve=>fixture.close(resolve));
  }
});
