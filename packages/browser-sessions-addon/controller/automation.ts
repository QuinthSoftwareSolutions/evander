/** Private CDP bridge. Browser lifetime belongs to the sharing session, not its clients. */
import { createServer } from "node:http";
import { timingSafeEqual } from "node:crypto";
import { WebSocketServer } from "ws";

function equal(a: string, b: string) {
  const x=Buffer.from(a), y=Buffer.from(b);
  return !!a && x.length===y.length && timingSafeEqual(x,y);
}
function interruptedFocus(error: unknown) {
  const message=error instanceof Error?error.message:String(error);
  return (error instanceof Error && error.name==="TargetClosedError") ||
    ["page_unavailable","stale_control"].includes(message) ||
    /\b(?:Target page, context or browser has been closed|Target closed|Session closed)\b/.test(message);
}
export async function observeTargetDestruction(owner:any, notify:(target:string)=>void) {
  // Page.close on a CDP observer also fires when that observer disconnects.
  // Only the browser-lifetime owner's independent protocol connection can
  // supply the destruction evidence used to release a target's input fence.
  const session=await owner.newBrowserCDPSession();
  session.on("Target.targetDestroyed",({targetId}:{targetId:string})=>notify(targetId));
  await session.send("Target.setDiscoverTargets",{discover:true});
  return session;
}
export function automationBridge(config: any, endpoint: string, update: (state:any)=>Promise<void>, initial: any, focus: (target:string)=>Promise<void>=async()=>{}, fenceCommands: ()=>Promise<void>=async()=>{}) {
  let state=initial, token="";
  const clients=new Set<any>();
  const upstreams=new Set<any>();
  const pending=new Set<Promise<void>>();
  const targetDestructions=new Set<(target:string)=>void>();
  const allowed=(url: string)=>url==="about:blank" || config.allowed_origins.includes(new URL(url).origin);
  const revoke=async()=>{
    token="";
    for(const client of clients) client.close(4001,"control_revoked");
    clients.clear();
    // The agent cannot enqueue input after this point. Already dispatched commands
    // may have completed; do not acknowledge human control until they settle.
    const settling=[...pending,fenceCommands()];
    let timer:ReturnType<typeof setTimeout>|undefined;
    try {await Promise.race([Promise.all(settling),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error("fence_unconfirmed")),10000);})]);}
    finally {if(timer)clearTimeout(timer);}
  };
  const setState=async(next:any)=>{
    if(next.id!==state.id || next.control_generation<state.control_generation)throw new Error("stale_control");
    const changed=next.control_generation!==state.control_generation || next.controller!==state.controller;
    // Withdraw the old command authority before awaiting pending browser work.
    state=next;config.allowed_origins=state.allowed_origins;await update(state);
    if(changed)await revoke();
  };
  const server=createServer(async(req,res)=>{
    const respond=(status:number,body:any)=>{res.writeHead(status,{"Content-Type":"application/json","Cache-Control":"no-store"});res.end(JSON.stringify(body));};
    if(req.method!=="POST" || req.url!=="/control" || !equal(req.headers.authorization??"","Bearer "+config.endpoint_token)) {respond(403,{error:"forbidden"});return;}
    try {
      const chunks:Buffer[]=[];let size=0;
      for await(const chunk of req) {size+=chunk.length;if(size>65536) throw new Error("request_too_large");chunks.push(chunk);}
      const input=JSON.parse(Buffer.concat(chunks).toString());
      if(input.session) {
        await setState(input.session);
      }
      if(input.revoke_automation || input.automation_token) await revoke();
      if(input.automation_token && config.playwright_enabled && state.controller==="agent:"+state.agent_session_id) token=input.automation_token;
      respond(200,{fenced:true});
    } catch {respond(409,{error:"fence_unconfirmed"});}
  });
  const sockets=new WebSocketServer({noServer:true,maxPayload:8*1024*1024});
  server.on("upgrade",(req,socket,head)=>{
    const generation=String(req.headers["x-evander-control-generation"]??"");
    if(req.url!=="/connect" || !config.playwright_enabled || !token || !equal(req.headers.authorization??"","Bearer "+token) || generation!==String(state.control_generation) || state.controller!=="agent:"+state.agent_session_id || Date.now()/1000>=state.expires_at || clients.size) {socket.destroy();return;}
    sockets.handleUpgrade(req,socket,head,(client:any)=>sockets.emit("connection",client));
  });
  sockets.on("connection",(client:any)=>{
    const clientToken=token,clientGeneration=state.control_generation;
    clients.add(client);
    const upstream=new WebSocket(endpoint);
    upstreams.add(upstream);
    const waits=new Map<number,{sessionId?:string;done:()=>void}>();
    const retired=new Set<number>();
    const closedTargets=new Set<string>();
    const queued:any[]=[];
    const targets=new Map<string,string>();
    const fail=(message:any)=>client.send(JSON.stringify({id:message.id,sessionId:message.sessionId,error:{code:-32000,message:"Evander browser policy denied command"}}));
    const closed=(message:any)=>{
      if(client.readyState===1)client.send(JSON.stringify({id:message.id,sessionId:message.sessionId,error:{code:-32000,message:"Target closed"}}));
    };
    const targetDestroyed=(target:string)=>{
      // Only independent browser Target.targetDestroyed evidence calls this.
      // Neither observer Page.close nor a CDP detach can release an input fence.
      closedTargets.add(target);
      for(const [id,wait] of waits)if(wait.sessionId && targets.get(wait.sessionId)===target) {
        waits.delete(id);retired.add(id);wait.done();closed({id,sessionId:wait.sessionId});
      }
    };
    targetDestructions.add(targetDestroyed);
    const focusFailed=(error:unknown)=>{
      // Input may already have reached Chrome. A queued viewer selection can
      // lose its target while that page closes; it must not kill the controller.
      // Other failures remain visible as an interrupted automation connection.
      if(!interruptedFocus(error))client.close(1011,"browser_focus_failed");
    };
    client.on("message",(raw:any)=>{
      try {
        if(!clients.has(client) || !equal(clientToken,token) || clientGeneration!==state.control_generation || state.controller!=="agent:"+state.agent_session_id || !token) {client.close(4001,"control_revoked");return;}
        const message=JSON.parse(String(raw));
        const target=targets.get(message.sessionId);
        if(target && closedTargets.has(target)) {closed(message);return;}
        if(["Browser.close","Browser.crash","DOM.setFileInputFiles","Target.exposeDevToolsProtocol","Target.sendMessageToTarget"].includes(message.method)) {fail(message);return;}
        if(message.method==="Network.loadNetworkResource" && !allowed(message.params?.url??"")) {fail(message);return;}
        if(message.method==="Target.attachToTarget" && message.params?.flatten===false) {fail(message);return;}
        if(["Page.navigate","Target.createTarget"].includes(message.method) && message.params?.url && !allowed(message.params.url)) {fail(message);return;}
        if((message.method.startsWith("Input.") || message.method==="Page.navigate") && targets.has(message.sessionId)) {
          try {void focus(targets.get(message.sessionId)!).catch(focusFailed);}
          catch(error) {focusFailed(error);}
        }
        if(message.method==="Target.createBrowserContext") {
          message.params={...message.params,disposeOnDetach:false};
          if(config.proxy) {message.params.proxyServer=config.proxy.server;message.params.proxyBypassList="<-loopback>"+(config.proxy.bypass?","+config.proxy.bypass:"");}
          else {delete message.params.proxyServer;delete message.params.proxyBypassList;}
        }
        if(["Browser.setDownloadBehavior","Page.setDownloadBehavior"].includes(message.method)) message.params={...message.params,behavior:"deny"};
        let done!:()=>void;
        const settled=new Promise<void>(resolve=>{done=resolve;});
        pending.add(settled);waits.set(message.id,{sessionId:message.sessionId,done:()=>{pending.delete(settled);done();}});
        if(upstream.readyState===WebSocket.OPEN) upstream.send(JSON.stringify(message));else queued.push(message);
      } catch {client.close(1008,"invalid_command");}
    });
    upstream.addEventListener("open",()=>{for(const message of queued) upstream.send(JSON.stringify(message));queued.length=0;});
    upstream.addEventListener("message",(event:any)=>{
      try {
        const message=JSON.parse(String(event.data));
        if(message.method==="Target.attachedToTarget")targets.set(message.params.sessionId,message.params.targetInfo.targetId);
        if(message.id!==undefined) {
          if(retired.delete(message.id))return;
          waits.get(message.id)?.done();waits.delete(message.id);
        }
        if(client.readyState===1) client.send(String(event.data));
      } catch {client.close(1011,"browser_disconnected");}
    });
    upstream.addEventListener("close",()=>{for(const wait of waits.values())wait.done();waits.clear();targetDestructions.delete(targetDestroyed);upstreams.delete(upstream);client.close(1011,"browser_disconnected");});
    upstream.addEventListener("error",()=>upstream.close());
    client.on("close",()=>{
      // A descriptor authorizes one connection. An old close notification must
      // also never revoke a replacement credential issued during handover.
      if(equal(clientToken,token))token="";
      clients.delete(client);
      // Pending CDP requests settle on the upstream before disposal. Contexts use
      // disposeOnDetach=false, so closing this transport preserves pages and login.
      void Promise.all([...pending]).then(()=>upstream.close());
    });
  });
  const listening=new Promise<number>(resolve=>server.once("listening",()=>resolve((server.address() as any).port)));
  server.listen(config.control_port??11895,"0.0.0.0");
  return {revoke,setState,listening,targetDestroyed:(target:string)=>{for(const notify of targetDestructions)notify(target);},active:()=>clients.size>0,close:async()=>{await revoke().catch(()=>{});for(const upstream of upstreams)upstream.close();sockets.close();await new Promise<void>(resolve=>server.close(()=>resolve()));}};
}
