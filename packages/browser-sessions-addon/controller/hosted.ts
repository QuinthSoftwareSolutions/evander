import { readFile, stat, mkdtemp, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join as joinPath } from "node:path";
import { createRequire } from "node:module";
import { createServer } from "node:net";
import { once } from "node:events";
import { automationBridge } from "./automation.js";
import { BrowserEngine } from "../shared/engine.js";

const config = JSON.parse(await readFile(process.env.EVANDER_BROWSER_CONNECTION!, "utf8"));
const requirePlaywright = createRequire(import.meta.url);
const { chromium } = requirePlaywright(process.env.EVANDER_PLAYWRIGHT_MODULE ??
  "/opt/evander/playwright-runtime/node_modules/playwright");
const portProbe=createServer();portProbe.listen(0,"127.0.0.1");await once(portProbe,"listening");
const debugPort=(portProbe.address() as any).port;await new Promise<void>(resolve=>portProbe.close(()=>resolve()));
// Numeric container users need writable Chrome configuration independent of the image's HOME.
const chromeRuntime=await mkdtemp(joinPath(tmpdir(),"evander-browser-"));
const chromeConfig=joinPath(chromeRuntime,"config"),chromeCache=joinPath(chromeRuntime,"cache");
await mkdir(chromeConfig,{mode:0o700});await mkdir(chromeCache,{mode:0o700});
const owner = await chromium.launch({ headless: true, handleSIGTERM: false, handleSIGINT: false,
  env:{...process.env,XDG_CONFIG_HOME:chromeConfig,XDG_CACHE_HOME:chromeCache},
  executablePath: process.env.PLAYWRIGHT_MCP_EXECUTABLE_PATH || undefined,
  ...(config.proxy ? {proxy:config.proxy} : {}),
  args: ["--disable-dev-shm-usage","--remote-debugging-port="+debugPort,"--remote-debugging-address=127.0.0.1"] });
const browser = await chromium.connectOverCDP("http://127.0.0.1:"+debugPort);
const version = await fetch("http://127.0.0.1:"+debugPort+"/json/version").then(response=>response.json()) as any;
let storageState: any = undefined;
try {
  const statePath = process.env.EVANDER_PREVIEW_STATE ?? "/run/preview-output/browser-state.json";
  if ((await stat(statePath)).size > 1048576) throw new Error("state_too_large");
  const candidate = JSON.parse(await readFile(statePath, "utf8"));
  if (!Array.isArray(candidate.cookies) || !Array.isArray(candidate.origins)) throw new Error("invalid_state");
  const hosts = config.allowed_origins.map((value: string) => new URL(value).hostname);
  if (candidate.cookies.some((cookie: any) => !hosts.includes(cookie.domain.replace(/^\./, ""))) ||
      candidate.origins.some((item: any) => !config.allowed_origins.includes(item.origin)))
    throw new Error("state_origin_mismatch");
  storageState = candidate;
} catch (error: any) {
  if (error.code !== "ENOENT") throw new Error("Invalid preview browser-state handoff");
}
const guarded=new WeakSet<any>();
async function guardContext(current:any) {
  if(guarded.has(current))return;guarded.add(current);
  await current.route("**/*",async(route:any)=>{
    const request=route.request();
    if(request.isNavigationRequest() && request.url()!=="about:blank" && !config.allowed_origins.includes(new URL(request.url()).origin))await route.abort();
    else await route.continue();
  });
}
const context = await browser.newContext({ ...(config.proxy ? {proxy:config.proxy}:{}), viewport: { width: 1440, height: 900 }, storageState,
  acceptDownloads: false });
await context.route("**/*", async (route: any) => {
  const request = route.request();
  if (request.isNavigationRequest() && request.url()!=="about:blank" && !config.allowed_origins.includes(new URL(request.url()).origin)) {
    await route.abort(); return;
  }
  await route.continue();
});
let socket: WebSocket | undefined;
let shuttingDown = false;
let state = config.session;
const send = (value:any)=>{if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify(value));};
const pages=new Map<string,any>();
const known=new WeakMap<any,string>();
const targetPages=new Map<string,string>();
const engines=new Map<string,{page:any,cdp:any,engine:BrowserEngine}>();
let active="";
const registering=new WeakMap<any,Promise<void>>();
function register(page:any):Promise<void> {
  const existing=registering.get(page);if(existing)return existing;
  const ready=registerPage(page);registering.set(page,ready);return ready;
}
async function registerPage(page:any) {
  await guardContext(page.context());
  const id=crypto.randomUUID();known.set(page,id);pages.set(id,page);
  const cdp=await page.context().newCDPSession(page);
  const info=await cdp.send("Target.getTargetInfo");targetPages.set(info.targetInfo.targetId,id);
  const engine=new BrowserEngine((method,params)=>cdp.send(method,params),state,send);
  engines.set(id,{page,cdp,engine});
  cdp.on("Page.frameNavigated",()=>engine.navigate());
  cdp.on("Page.navigatedWithinDocument",()=>engine.navigate());
  cdp.on("Page.screencastFrame",async(event:any)=>{
    try {
      if(active!==id)return;
      const metrics=await cdp.send("Page.getLayoutMetrics");
      send({kind:"frame",image:event.data,width:Math.round(metrics.cssVisualViewport.clientWidth),height:Math.round(metrics.cssVisualViewport.clientHeight),page_epoch:id+":"+event.sessionId,url:page.url(),page_id:id,pages:pageList()});
    } finally {await cdp.send("Page.screencastFrameAck",{sessionId:event.sessionId}).catch(()=>{});}
  });
  await cdp.send("Page.enable");
  page.on("close",()=>{pages.delete(id);engines.delete(id);if(active===id){active="";const next=pages.keys().next().value;if(next)void select(next);}});
  if(!active)await select(id);
}
async function select(id:string) {
  const selected=engines.get(id);if(!selected)throw new Error("page_unavailable");
  const previous=engines.get(active);
  if(previous)await previous.cdp.send("Page.stopScreencast").catch(()=>{});
  active=id;
  await selected.cdp.send("Page.startScreencast",{format:"jpeg",quality:65,maxWidth:1920,maxHeight:1080});
}
function pageList(){return [...pages].map(([id,page])=>({id,url:page.url(),selected:id===active})).filter(item=>item.url==="about:blank"||config.allowed_origins.includes(new URL(item.url).origin));}
for(const current of browser.contexts()) {
  await guardContext(current);
  for(const page of current.pages())await register(page);
  current.on("page",(page:any)=>void register(page));
}
browser.on("disconnected",()=>{socket?.close();});
context.on("page",(page:any)=>void register(page));
const page=await context.newPage();await register(page);
const cdp=engines.get(known.get(page)!)!.cdp;
const bridge=automationBridge(config,version.webSocketDebuggerUrl,async next=>{
  state=next;for(const current of engines.values())current.engine.update(state);
},state,async target=>{const id=targetPages.get(target);if(id && active!==id)await select(id);});
for(const signal of ["SIGTERM","SIGINT"] as const)process.on(signal,()=>{
  shuttingDown=true;socket?.close();void bridge.close().finally(()=>owner.close()).finally(()=>rm(chromeRuntime,{recursive:true,force:true})).finally(()=>process.exit(0));
});
await cdp.send("Page.enable");
await page.goto(config.entry_url, { waitUntil: "domcontentloaded", timeout: 60000 });
let commandChain = Promise.resolve();
let streaming = false;
async function join() {
  streaming = false;
  socket = new WebSocket(config.relay_url.replace(/^http/, "ws"));
  socket.addEventListener("open", () => send({ kind: "authenticate", version: 1, token: config.endpoint_token, features:{fast_execution:1} }));
  socket.addEventListener("error", () => socket?.close());
  socket.addEventListener("message", (event) => {
    let message: any;
    try { message = JSON.parse(String(event.data)); } catch { socket?.close(); return; }
    if (message.kind === "state") {
      void bridge.setState(message.session).catch(()=>socket?.close());
      state = message.session; for(const current of engines.values())current.engine.update(state);
      if (!streaming) {
        streaming = true;
        send({ kind: "ready", url: engines.get(active)?.page.url() ?? page.url() });
        void select(active);
      }
    } else if (message.kind === "pause") {
      state = { ...state, controller: null, control_generation: state.control_generation + 1 };
      void bridge.setState(state).catch(()=>socket?.close());
      for(const current of engines.values())current.engine.update(state);
    } else if (message.kind === "command") {
      commandChain = commandChain.then(async () => {
        try {
          if(!["observe","highlight","pages","select_page"].includes(message.command.action) && bridge.active())throw new Error("automation_controls_browser");
          let result:any;
          if(message.command.action==="authenticate") {
            const alias=message.command.target_alias, login=config.website_session;
            if(!login.relay_url || !login.aliases.includes(alias))throw new Error("website_session_not_granted");
            const response=await fetch(login.relay_url+"/session/"+encodeURIComponent(login.session_id)+"/website-session/"+encodeURIComponent(alias),{method:"POST",redirect:"error",headers:{"x-evander-mcp-session-token":login.token},signal:AbortSignal.timeout(90000)});
            if(!response.ok)throw new Error("website_session_unavailable");
            const auth=await response.json() as any;
            if(!config.allowed_origins.includes(auth.origin) || auth.expiresAt<=Date.now()/1000)throw new Error("website_session_unavailable");
            const managed=await browser.newContext({storageState:auth.storageState,acceptDownloads:false,serviceWorkers:"block",...(config.proxy?{proxy:config.proxy}:{})});
            await managed.addInitScript(({origin,values}:any)=>{if(window.location.origin===origin)for(const [key,value] of Object.entries(values))window.sessionStorage.setItem(key,String(value));},{origin:auth.origin,values:auth.sessionStorage});
            managed.on("page",(page:any)=>void register(page));
            const target=await managed.newPage();await register(target);await target.goto(auth.origin,{waitUntil:"domcontentloaded"});await select(known.get(target)!);
            result={success:true,pages:pageList(),generation:state.control_generation};
          }
          else if(message.command.action==="pages") result={pages:pageList(),generation:state.control_generation};
          else if(message.command.action==="select_page") {
            if(!pageList().some(item=>item.id===message.command.page_id))throw new Error("page_unavailable");
            await select(message.command.page_id);result={pages:pageList(),generation:state.control_generation};
          } else {
            const target=message.command.page_id??active;
            if(target!==active)await select(target);
            const current=engines.get(target);if(!current)throw new Error("page_unavailable");
            result=await current.engine.execute(message.command,message.actor);
            result.page_id=target;result.pages=pageList();
          }
          send({ kind: "result", id: message.command.id, result });
        } catch (error: any) {
          send({ kind: "result", id: message.command.id, error: true,
            code: /^[a-z_]+$/.test(error.message) ? error.message : "browser_error" });
        }
      });
    }
  });
  return new Promise<void>((resolve) => socket!.addEventListener("close", () => {
    state = { ...state, controller: null, control_generation: state.control_generation + 1 };
    void bridge.revoke();
    for(const current of engines.values())current.engine.update(state); resolve();
  }));
}
const deadline = setTimeout(() => void owner.close().finally(()=>rm(chromeRuntime,{recursive:true,force:true})).finally(() => process.exit(0)),
  Math.max(1, config.session.expires_at * 1000 - Date.now()));
const heartbeat = setInterval(() => send({ kind: "heartbeat" }), 20000);
try {
  while (!shuttingDown && Date.now() / 1000 < config.session.expires_at) {
    await join();
    await new Promise((resolve) => setTimeout(resolve, 3000));
  }
} finally { clearInterval(heartbeat); clearTimeout(deadline); await bridge.close(); await owner.close(); await rm(chromeRuntime,{recursive:true,force:true}); }
