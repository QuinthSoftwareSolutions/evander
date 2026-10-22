import test from "node:test";
import assert from "node:assert/strict";
import {createServer} from "node:http";
import {once} from "node:events";
import {spawn} from "node:child_process";
import {mkdtemp,writeFile,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {createRequire} from "node:module";
import {WebSocketServer} from "ws";
const require=createRequire(import.meta.url);

async function until(read,label) {
  const deadline=Date.now()+10000;
  while(Date.now()<deadline){const value=read();if(value)return value;await new Promise(r=>setTimeout(r,10));}
  throw new Error("Timed out: "+label);
}

test("hosted authentication is cancelled by a fenced human takeover",{timeout:30000},async()=>{
  let releaseLogin,loginStarted=false,loginCount=0,relay,controller;
  const heldLogin=new Promise(resolve=>{releaseLogin=resolve;});
  const results=new Map();
  const server=createServer(async(req,res)=>{
    if(req.url.startsWith("/session/")) {
      loginCount++;loginStarted=true;
      if(loginCount===1)await heldLogin;
      res.setHeader("Content-Type","application/json");
      res.end(JSON.stringify({origin,expiresAt:Date.now()/1000+60,storageState:{cookies:[],origins:[]},sessionStorage:{operator:"1"}}));
    } else {res.setHeader("Content-Type","text/html");res.end("<h1>Shared browser fixture</h1>");}
  });
  server.listen(0,"127.0.0.1");await once(server,"listening");
  const origin="http://127.0.0.1:"+server.address().port;
  const sockets=new WebSocketServer({server,path:"/relay"});
  const portProbe=createServer();portProbe.listen(0,"127.0.0.1");await once(portProbe,"listening");
  const controlPort=portProbe.address().port;await new Promise(resolve=>portProbe.close(resolve));
  let state={id:"browser-test",agent_session_id:"agent-a",controller:"agent:agent-a",control_generation:0,expires_at:Date.now()/1000+60,allowed_origins:[origin]};
  let ready=false,error="";
  sockets.on("connection",socket=>{relay=socket;socket.on("message",raw=>{
    const message=JSON.parse(String(raw));
    if(message.kind==="authenticate")socket.send(JSON.stringify({kind:"state",session:state}));
    if(message.kind==="ready")ready=true;
    if(message.kind==="result")results.set(message.id,message);
  });});
  const folder=await mkdtemp(join(tmpdir(),"evander-hosted-authority-"));
  try {
    await writeFile(join(folder,"connection.json"),JSON.stringify({session:state,entry_url:origin+"/original",allowed_origins:[origin],endpoint_token:"worker-proof",relay_url:origin+"/relay",control_port:controlPort,playwright_enabled:false,website_session:{relay_url:origin,session_id:"proxy~browser-test",token:"delegated-proof",aliases:["ui"]}}));
    controller=spawn(process.execPath,["evander_browser_sessions_addon/controller/hosted.mjs"],{env:{...process.env,EVANDER_BROWSER_CONNECTION:join(folder,"connection.json"),EVANDER_PREVIEW_STATE:join(folder,"missing-state.json"),EVANDER_PLAYWRIGHT_MODULE:require.resolve("playwright")},stdio:["ignore","ignore","pipe"]});
    controller.stderr.on("data",d=>error+=d);
    await until(()=>ready,"controller ready").catch(e=>{throw new Error(e.message+"; "+error);});
    const send=(id,actor,action)=>relay.send(JSON.stringify({kind:"command",actor,command:{id,action,target_alias:"ui",generation:state.control_generation}}));
    send("old-login","agent:agent-a","authenticate");
    await until(()=>loginStarted,"host login pending");
    state={...state,controller:"user:operator",control_generation:1};
    const claim=await fetch("http://127.0.0.1:"+controlPort+"/control",{method:"POST",headers:{Authorization:"Bearer worker-proof"},body:JSON.stringify({session:state}),signal:AbortSignal.timeout(12000)});
    assert.equal(claim.status,200);assert.equal((await claim.json()).fenced,true);
    releaseLogin();
    const rejected=await until(()=>results.get("old-login"),"interrupted login result");
    assert.equal(rejected.error,true);assert.equal(rejected.code,"stale_control");
    send("pages-after-claim","user:operator","pages");
    const pages=(await until(()=>results.get("pages-after-claim"),"retained pages")).result.pages;
    assert.equal(pages.length,1);assert.equal(pages[0].url,origin+"/original");
    send("human-login","user:operator","authenticate");
    const authenticated=await until(()=>results.get("human-login"),"authorized login");
    assert.equal(authenticated.error,undefined);assert.equal(authenticated.result.success,true);
    assert.equal(authenticated.result.pages.length,2);assert.equal(loginCount,2);
    assert.equal(error,"");
  } finally {
    releaseLogin();
    if(controller && controller.exitCode===null && controller.signalCode===null){
      controller.kill("SIGTERM");
      let timer;await Promise.race([once(controller,"exit"),new Promise(resolve=>{timer=setTimeout(resolve,3000);})]);clearTimeout(timer);
      if(controller.exitCode===null && controller.signalCode===null){controller.kill("SIGKILL");await once(controller,"exit");}
    }
    for(const socket of sockets.clients)socket.terminate();
    await new Promise(resolve=>sockets.close(resolve));server.closeAllConnections();
    await new Promise(resolve=>server.close(resolve));await rm(folder,{recursive:true,force:true});
  }
});
