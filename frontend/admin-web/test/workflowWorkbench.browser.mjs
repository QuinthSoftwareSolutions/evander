import assert from "node:assert/strict";
import test from "node:test";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {mkdir,readFile} from "node:fs/promises";
import {createServer} from "vite";
import {suiteBrowser,browserTestTimeout,browserHookTimeout,listeningAddress,withTimeout} from "./support/browserHarness.mjs";

const browser=suiteBrowser();
const json=(body)=>({contentType:"application/json",body:JSON.stringify(body)});
const field=(key,label,kind,extra={})=>({key,label,kind,required:false,bindable:false,read_only:false,multiline:false,description:null,placeholder:null,options:[],item_fields:[],...extra});
async function editor(t,{mobile=false,locked=false}={}){
  const server=await createServer({root:path.resolve(fileURLToPath(new URL("..",import.meta.url))),server:{host:"127.0.0.1",port:0},logLevel:"error"});
  t.after(()=>server.close(),{timeout:browserHookTimeout});
  await withTimeout(server.listen(),15_000,"Vite startup timeout");
  const context=await browser.newContext(t,{viewport:mobile?{width:390,height:844}:{width:1440,height:1100},isMobile:mobile,hasTouch:mobile});
  const page=await context.newPage();
  const saves=[],errors=[];
  page.on("pageerror",error=>errors.push(error.message));
  const permissions=["workflows.read","workflow_runs.read",...(!locked?["workflows.write","workflow_runs.control"]:[])];
  let document={worker_id:"trigger-orchestrator",revision:"revision-1",config_path:"workflow.json",layout_path:"layout.json",config_exists:true,layout_exists:true,
    config:{triggers:[{id:"source",display_name:"Incoming ticket",type:"trigger:manual",config:{}}],gates:[{id:"map",display_name:"Prepare request",type:"gate:map",config:{template:{issue_id:"${{source.ticket.id}}",title:"Ticket ${{source.ticket.title}}",attempts:2,enabled:false,empty:null,details:{nested:["${{source.ticket.id}}",false,null]},items:[1,"2",true]},extra_setting:{retained:true}}}],effects:[{id:"prompt",display_name:"Send prompt",type:"effect:agent:prompt",config:{prompt:"${{map.title}}"}}],queries:[],edges:[{source_id:"source",target_id:"map"},{source_id:"map",target_id:"prompt"}]},
    layout:{nodes:{source:{x:0,y:100},map:{x:340,y:100},prompt:{x:680,y:100}}},graphs:[{id:"intake",name:"Ticket intake",directory_path:"root",node_ids:["source","map","prompt"],user_ids:[]}],output_schemas:{source:{ticket:{type:"object",properties:{id:{type:"string"},title:{type:"string"}}}}},validation_diagnostics:[]};
  const catalog={worker_id:"trigger-orchestrator",internal_events:[],nodes:[
    {kind:"trigger",type:"trigger:manual",label:"Manual",fields:[],input_ports:[],output_ports:[{key:"completed",label:"Completed"}]},
    {kind:"gate",type:"gate:map",label:"Map",fields:[field("template","Template","json",{required:true,bindable:true}),field("outputs","Outputs","json",{read_only:true})],input_ports:[{key:"completed",label:"Completed"}],output_ports:[{key:"completed",label:"Completed"}]},
    {kind:"effect",type:"effect:agent:prompt",label:"Prompt",fields:[field("prompt","Prompt","string",{bindable:true})],input_ports:[{key:"completed",label:"Completed"}],output_ports:[]}]};
  await page.route("**/api/**",async route=>{
    const request=route.request(),name=new URL(request.url()).pathname;
    if(name==="/api/auth/session")return route.fulfill(json({auth_mode:"none",auth_required:false,authenticated:true,username:"operator",display_name:"Operator",permissions,effective_permissions:permissions,session_id:"session-1",privileged_operations:{unlocked:!locked},available_login_methods:[]}));
    if(name==="/api/admin/state")return route.fulfill(json({selected_directory_path:"root",directory_scoped_config_enabled:false,execution_backend:"libvirt_vm",permissions,effective_permissions:permissions,privileged_operations:{unlocked:!locked},assistant:{enabled:false,show_launcher_when_disabled:false}}));
    if(name==="/api/configuration/state")return route.fulfill(json({directory_scoped_config_enabled:false,selected_directory_path:"root",execution_backend:"libvirt_vm",selected_directory:{path:"root",parent_path:null,can_create:false,can_delete:false},tree:{ok:true,data:{directory:{id:"root",name:"root",path:"root",parent_id:null,metadata:{}},children:[],config_domains:[]},errors:[],validation_diagnostics:[]}}));
    if(name==="/api/admin/deployment-status")return route.fulfill(json({active:false}));
    if(name.startsWith("/api/admin/branding/assets/evander-logo-"))return route.fulfill({contentType:"image/png",body:await readFile(new URL(`../branding/evander-logo-${name.includes("dark")?"dark":"light"}.png`,import.meta.url))});
    if(name.startsWith("/api/admin/branding/assets/"))return route.fulfill({status:204,body:""});
    if(name==="/api/admin/user-notifications")return route.fulfill(json({notifications:[],unread_count:0}));
    if(name==="/api/admin/user-preferences/workflow-tutorial-progress")return route.fulfill(json({completed_tutorial_ids:[],skipped_tutorial_ids:[]}));
    if(name==="/api/workflow-editor/document"){
      if(request.method()==="PUT"){
        const body=request.postDataJSON();saves.push(body);document={...document,...body,revision:"revision-2"};
      }
      return route.fulfill(json(document));
    }
    if(name==="/api/workflow-editor/catalog")return route.fulfill(json(catalog));
    if(name==="/api/workflow-editor/validate"){
      const body=request.postDataJSON();return route.fulfill(json({valid:true,config:body.config,layout:body.layout,graphs:body.graphs,diagnostics:[],output_schemas:document.output_schemas}));
    }
    if(name==="/api/workflow-editor/runs")return route.fulfill(json({worker_id:"trigger-orchestrator",runs:[],node_activity:{},recent_node_activity:[]}));
    if(name==="/api/workflow-editor/runs/stream")return route.fulfill({contentType:"text/event-stream",body:": keepalive\n\n"});
    if(name==="/api/workflow-editor/node-executions/search")return route.fulfill(json({rows:[],total_count:0,fields:[]}));
    return route.fulfill(json({}));
  });
  await page.goto(`http://127.0.0.1:${listeningAddress(server).port}/#workflow`,{waitUntil:"domcontentloaded"});
  await page.locator('.react-flow__node[data-id="map"]').click();
  if(mobile)await page.locator('.compact-workflow-toolbar').getByRole("button",{name:"Inspector",exact:true}).click();
  const panel=page.locator(mobile?'.compact-workflow-sheet':'.workflow-workbench');
  await panel.getByLabel("Output name 1",{exact:true}).waitFor();
  return{page,panel,saves,errors};
}

test("lower workbench preserves horizontal geometry, viewport, drafts, and a remembered split",{timeout:browserTestTimeout},async t=>{
  const {page,panel,saves,errors}=await editor(t);
  if(process.env.WORKFLOW_WORKBENCH_SCREENSHOTS){
    const directory=process.env.WORKFLOW_WORKBENCH_SCREENSHOTS;
    await mkdir(directory,{recursive:true});
    await page.mouse.move(1300,60);
    await page.getByRole("tooltip").waitFor({state:"hidden"});
    await page.screenshot({path:path.join(directory,"workflow-workbench-light.png"),fullPage:true});
    await page.getByRole("button",{name:"Dark mode",exact:true}).click();
    await page.screenshot({path:path.join(directory,"workflow-workbench-dark.png"),fullPage:true});
    await page.getByRole("button",{name:"Light mode",exact:true}).click();
  }
  await page.getByRole("button",{name:"Add node",exact:true}).click();
  const palette=page.getByRole("dialog",{name:"Add node",exact:true});
  await palette.getByLabel("Search nodes").fill("map");
  assert.equal(await palette.getByRole("button",{name:"Map",exact:true}).isVisible(),true);
  await palette.getByLabel("Search nodes").press("Escape");
  assert.equal(await palette.isVisible(),false);
  await page.getByRole("button",{name:"Graphs",exact:true}).click();
  const graphs=page.getByRole("dialog",{name:"Graphs",exact:true});
  assert.equal(await graphs.getByText("Ticket intake",{exact:true}).isVisible(),true);
  await graphs.getByRole("button",{name:"Close",exact:true}).click();
  const canvas=await page.locator('.canvas-shell').boundingBox(),workbench=await panel.boundingBox();
  assert(workbench.y>=canvas.y+canvas.height,"editor is below the canvas");
  assert(Math.abs(workbench.width-canvas.width)<2,"editor uses full canvas width");
  const viewport=()=>page.locator('.react-flow__viewport').evaluate(element=>getComputedStyle(element).transform);
  const before=await viewport();
  await panel.getByLabel("Output value 3",{exact:true}).fill("2e1");
  await panel.getByRole("tab",{name:"Outputs",exact:true}).click();
  await panel.getByRole("tab",{name:"Configuration",exact:true}).click();
  assert.equal(await panel.getByLabel("Output value 3",{exact:true}).inputValue(),"2e1");
  const separator=page.getByRole("separator",{name:"Resize workflow workbench"});
  const box=await separator.boundingBox();
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();await page.mouse.move(box.x+box.width/2,box.y+box.height/2+65);await page.mouse.up();
  const share=Number(await separator.getAttribute("aria-valuenow"));assert(share>34);
  await separator.focus();await separator.press("ArrowUp");assert(Number(await separator.getAttribute("aria-valuenow"))<share);
  const nextBox=await separator.boundingBox();
  await page.mouse.move(nextBox.x+nextBox.width/2,nextBox.y+nextBox.height/2);await page.mouse.down();
  await page.evaluate(()=>window.dispatchEvent(new Event("blur")));await page.mouse.up();
  await page.waitForFunction(()=>!document.querySelector('.is-resizing-workbench')&&document.body.style.cursor!=="row-resize");
  assert.equal(await viewport(),before,"resizing keeps pan and zoom");
  await panel.getByRole("button",{name:"Expand editor",exact:true}).click();assert.equal(await page.locator('.canvas-shell').isVisible(),false);
  await panel.getByRole("button",{name:"Restore canvas",exact:true}).click();assert.equal(await viewport(),before);
  await panel.getByRole("button",{name:"Collapse editor",exact:true}).click();
  await page.getByRole("button",{name:"Show editor",exact:true}).click();
  assert.equal(await panel.getByLabel("Output value 3",{exact:true}).inputValue(),"2e1");
  await panel.getByRole("button",{name:"Raw JSON",exact:true}).click();
  const raw=JSON.parse(await panel.getByLabel("Map template JSON").inputValue());assert.equal(raw.attempts,20);assert.equal(raw.enabled,false);assert.equal(raw.empty,null);assert.deepEqual(raw.items,[1,"2",true]);assert.deepEqual(raw.details,{nested:["${{source.ticket.id}}",false,null]});
  await panel.getByRole("button",{name:"Structured form",exact:true}).click();
  await page.locator('.workflow-command-bar').getByRole("button",{name:"Save workflow",exact:true}).click();
  await page.getByText("Workflow saved",{exact:true}).waitFor();
  assert.equal(saves.length,1);assert.equal(saves[0].expected_revision,"revision-1");
  assert.deepEqual(saves[0].layout.nodes,{source:{x:0,y:100},map:{x:340,y:100},prompt:{x:680,y:100}});
  assert.equal(saves[0].config.gates[0].config.template.attempts,20);assert.deepEqual(saves[0].config.gates[0].config.extra_setting,{retained:true});
  const stored=await page.evaluate(()=>JSON.parse(localStorage.getItem("workflow-editor:workbench-layout")));
  await page.reload({waitUntil:"domcontentloaded"});await page.getByRole("separator",{name:"Resize workflow workbench"}).waitFor();
  assert.equal(Number(await page.getByRole("separator",{name:"Resize workflow workbench"}).getAttribute("aria-valuenow")),Math.round(stored.canvasShare));
  assert.deepEqual(errors,[]);
});

test("invalid mapping drafts remain editable across tabs and block writes until corrected",{timeout:browserTestTimeout},async t=>{
  const {page,panel,saves}=await editor(t);
  const save=page.locator('.workflow-command-bar').getByRole("button",{name:"Save workflow",exact:true});
  await panel.getByRole("button",{name:"Add output",exact:true}).click();
  assert.equal(await save.isDisabled(),true);
  await panel.getByLabel("Output name 8",{exact:true}).fill("attempts");
  assert.equal(await save.isDisabled(),true);
  await panel.getByRole("tab",{name:"Outputs",exact:true}).click();await panel.getByRole("tab",{name:"Configuration",exact:true}).click();
  assert.equal(await panel.getByLabel("Output name 8",{exact:true}).inputValue(),"attempts");
  await panel.getByLabel("Output name 8",{exact:true}).fill("copies");
  await panel.getByLabel("Value type 8",{exact:true}).selectOption("number");
  await panel.getByLabel("Output value 8",{exact:true}).fill("1e999");assert.equal(await save.isDisabled(),true);
  await panel.getByLabel("Output value 8",{exact:true}).fill("4");assert.equal(await save.isDisabled(),false);
  await panel.getByLabel("Value type 8",{exact:true}).selectOption("binding");
  await panel.getByLabel("Output value 8",{exact:true}).selectOption("${{source.ticket.id}}");
  await panel.getByRole("button",{name:"Raw JSON",exact:true}).click();
  const raw=panel.getByLabel("Map template JSON");assert.equal(JSON.parse(await raw.inputValue()).copies,"${{source.ticket.id}}");
  await raw.fill("{");assert.equal(await save.isDisabled(),true);
  await panel.getByRole("button",{name:"Structured form",exact:true}).click();assert.equal(await raw.inputValue(),"{");
  await raw.fill("42");assert.equal(await save.isDisabled(),true);
  await raw.fill('{"count":1e999}');assert.equal(await save.isDisabled(),true);
  assert.equal(saves.length,0);
});

test("mobile mapping controls fit the sheet and read-only operators cannot mutate templates",{timeout:browserTestTimeout},async t=>{
  const {page,panel}=await editor(t,{mobile:true});
  const bounds=await panel.boundingBox();
  for(const control of await panel.locator('.map-template-row input,.map-template-row select,.map-template-row textarea').all()){
    if(!await control.isVisible())continue;const box=await control.boundingBox();assert(box.x>=bounds.x&&box.x+box.width<=bounds.x+bounds.width+1,"mapping control fits the mobile editor");
  }
  await panel.getByLabel("Output value 3",{exact:true}).fill("5");
  await panel.getByRole("tab",{name:"Outputs",exact:true}).click();await panel.getByRole("tab",{name:"Configuration",exact:true}).click();
  assert.equal(await panel.getByLabel("Output value 3",{exact:true}).inputValue(),"5");
  const readonly=await editor(t,{locked:true});
  assert.equal(await readonly.panel.getByLabel("Output name 1",{exact:true}).isDisabled(),true);
  assert.equal(await readonly.panel.getByRole("button",{name:"Raw JSON",exact:true}).isDisabled(),true);
  assert.equal(await readonly.panel.getByRole("button",{name:"Add output",exact:true}).isDisabled(),true);
  assert.equal(readonly.saves.length,0);
});
