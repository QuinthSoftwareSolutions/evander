import test from "node:test";
import assert from "node:assert/strict";
import {cp, readFile, writeFile} from "node:fs/promises";
import {join} from "node:path";
import {chromium} from "playwright";
import {fixture, until} from "./fixture.mjs";

test("guarded plans use the real service, relay and shared Chrome with crash-safe receipts", {timeout:60000}, async t => {
  const f=await fixture(); let context;
  try {
    const extension=join(f.folder,"extension"); await cp("dist/extension",extension,{recursive:true});
    const manifest=JSON.parse(await readFile(join(extension,"manifest.json"),"utf8"));
    manifest.host_permissions=["http://127.0.0.1/*"];
    await writeFile(join(extension,"manifest.json"),JSON.stringify(manifest));
    context=await chromium.launchPersistentContext(join(f.folder,"chrome"),{channel:"chromium",headless:true,
      args:["--no-sandbox","--disable-extensions-except="+extension,"--load-extension="+extension]});
    await context.addCookies([{name:"demo_session",value:"fixture",url:f.base}]);
    const page=await context.newPage(); await page.goto(f.base+"/application",{waitUntil:"domcontentloaded"});
    const worker=context.serviceWorkers()[0]??await context.waitForEvent("serviceworker",{timeout:10000});
    const panel=await context.newPage();
    await panel.goto("chrome-extension://"+new URL(worker.url()).host+"/panel.html");
    await page.bringToFront();
    const setup=await f.api("/test/setup",{local:true}), path="/test/"+setup.session.id;
    const sharing=message=>panel.evaluate(message=>chrome.runtime.sendMessage(message),message);
    assert.equal((await sharing({kind:"pair",invitation:setup.invitation_url})).ok,true);
    await until(async()=>(await f.api(path+"/state")).state==="paused","local observation");
    assert.equal((await sharing({kind:"control",action:"grant"})).ok,true);
    const state=await f.api(path+"/state");
    const observe=()=>f.api(path+"/command",{id:crypto.randomUUID(),generation:state.control_generation,action:"observe",include_image:false});
    const request=async (request_id, value, extra={})=>({request_id,generation:state.control_generation,
      observation_id:(await observe()).observation_id,goal:"Prepare and add the supplied material",
      values:{name:{description:"Material",value}},completion:{kind:"text",text:value+" added"},
      plan:[{action:"fill",target:{role:"textbox",name:"Material"},input:"name"},
            {action:"click",target:{role:"button",name:"Add material"}}],...extra});
    await page.evaluate(()=>{
      window.inputCount=0; window.submitCount=0;
      document.querySelector("input").addEventListener("input",()=>window.inputCount++);
      document.querySelector("button").addEventListener("click",()=>window.submitCount++);
    });
    await t.test("known fields batch without inference, then an identical retry performs no input",async()=>{
      const payload=await request("chrome-success","Copper");
      const result=await f.api(path+"/run",payload);
      assert.equal(result.status,"needs_verification",result.reason);
      assert.deepEqual(result.steps.map(step=>[step.step_index,step.outcome]),[[0,"completed"],[1,"completed"]]);
      assert.equal(await page.locator("#result").textContent(),"Copper added");
      const before=await page.evaluate(()=>[window.inputCount,window.submitCount]);
      const retry=await f.api(path+"/run",payload);
      assert.equal(retry.id,result.id); assert.equal(retry.observation,undefined);
      assert.deepEqual(await page.evaluate(()=>[window.inputCount,window.submitCount]),before);
    });
    await t.test("an owner snapshot cannot authorize input after an external edit",async()=>{
      const payload=await request("changed-owner-state","Tin");
      await page.locator("input").fill("Externally edited");
      const before=await page.evaluate(()=>window.inputCount);
      const result=await f.api(path+"/run",payload);
      assert.equal(result.status,"needs_pa");
      assert.equal(await page.locator("input").inputValue(),"Externally edited");
      assert.equal(await page.evaluate(()=>window.inputCount),before);
    });
    await t.test("a later target changing during input stops the rest of the plan",async()=>{
      await page.evaluate(()=>document.querySelector("input").addEventListener("input",()=>{
        document.querySelector("button").disabled=true;
      },{once:true}));
      const payload=await request("changed-future-target","Silver");
      const before=await page.evaluate(()=>window.submitCount);
      const result=await f.api(path+"/run",payload);
      assert.equal(result.status,"needs_pa",result.reason);
      assert.equal(result.steps.length,1);
      assert.equal(await page.locator("input").inputValue(),"Silver");
      assert.equal(await page.evaluate(()=>window.submitCount),before);
      await page.evaluate(()=>document.querySelector("button").disabled=false);
    });
    for(const phase of ["before_send","after_send","after_commit"]) await t.test("process death "+phase+" never resumes input",async()=>{
      const oldValue=await page.locator("input").inputValue();
      const value="Crash-"+phase, payload=await request("crash-"+phase,value);
      const before=await page.evaluate(()=>[window.inputCount,window.submitCount]);
      const crashed=await f.api(path+"/crash-batch",{phase,request:payload});
      assert.equal(crashed.exit_code,53,crashed.error);
      assert.equal(await page.locator("input").inputValue(),phase==="before_send"?oldValue:value);
      const pending=await f.api(path+"/run",payload);
      assert.equal(pending.status,"running");
      const expected=[before[0]+(phase==="before_send"?0:1),before[1]];
      assert.deepEqual(await page.evaluate(()=>[window.inputCount,window.submitCount]),expected);
      await until(async()=>{
        const s=await f.api(path+"/state"); return !s.active_run && s.last_run;
      },"crashed run deadline",7000);
      const recovered=await f.api(path+"/run",payload);
      assert.equal(recovered.id,pending.id);
      assert.equal(recovered.status,phase==="after_commit"?"needs_pa":"outcome_unknown");
      assert.deepEqual(recovered.steps.map(step=>step.outcome),[phase==="after_commit"?"completed":"unknown"]);
      assert.deepEqual(await page.evaluate(()=>[window.inputCount,window.submitCount]),expected);
      assert.notEqual(await page.locator("#result").textContent(),value+" added");
    });
    await t.test("new explicitly grounded work is possible after recovered receipts",async()=>{
      const result=await f.api(path+"/run",await request("new-after-crash","Zinc"));
      assert.equal(result.status,"needs_verification",result.reason);
      assert.equal(await page.locator("#result").textContent(),"Zinc added");
    });
    await t.test("one durable plan fills native date, number and text, then selects once",async()=>{
      await page.evaluate(()=>{
        const form=document.createElement("form");form.setAttribute("aria-label","Native editor");
        form.innerHTML='<label>Schedule date<input id="schedule" type="date"></label><label>Units<input id="units" type="number"></label><label>Memo<input id="memo"></label><label>Region<select id="region"><option>North</option><option>South</option><option>South extra</option></select></label>';
        document.body.append(form);window.nativeChanges=0;
        document.querySelector("#region").addEventListener("change",()=>window.nativeChanges++);
      });
      const observed=await observe();
      assert.ok(observed.elements.find(e=>e.name==="Schedule date").allowed_actions.includes("fill"));
      assert.ok(observed.elements.find(e=>e.name==="Region").allowed_actions.includes("select"));
      const payload={request_id:"native-controls",generation:state.control_generation,observation_id:observed.observation_id,
        goal:"Prepare native fields",values:{date:{description:"Schedule",value:"2027-03-18",sensitive:true},
          quantity:{description:"Units",value:"12"},memo:{description:"Memo",value:"Ready"},region:{description:"Region",value:"South"}},
        completion:{kind:"value",target:{role:"combobox",name:"Region"},input:"region"},
        plan:[{action:"fill",target:{role:"date",name:"Schedule date"},input:"date"},
              {action:"fill",target:{role:"spinbutton",name:"Units"},input:"quantity"},
              {action:"fill",target:{role:"textbox",name:"Memo"},input:"memo"},
              {action:"select",target:{role:"combobox",name:"Region"},input:"region"}]};
      const receipt=await f.api(path+"/run",payload);
      assert.equal(receipt.status,"needs_verification",receipt.reason);
      assert.deepEqual(receipt.steps.map(s=>s.outcome),["completed","completed","completed","completed"]);
      assert.equal(await page.locator("#schedule").inputValue(),"2027-03-18");
      assert.equal(await page.locator("#units").inputValue(),"12");
      assert.equal(await page.locator("#memo").inputValue(),"Ready");
      assert.equal(await page.locator("#region").inputValue(),"South");
      assert.equal(await page.evaluate(()=>window.nativeChanges),1);
      assert.equal(receipt.observation.elements.find(e=>e.role==="date").date_parts,"[redacted]");
      assert.equal((await f.api(path+"/run",payload)).id,receipt.id);
      assert.equal(await page.evaluate(()=>window.nativeChanges),1);
    });
    await t.test("acknowledged human pause during input prevents the second action",async()=>{
      await page.exposeFunction("testPause",()=>sharing({kind:"control",action:"pause"}));
      await page.evaluate(()=>document.querySelector("input").addEventListener("input",()=>{
        window.pauseAck=window.testPause();
        const end=performance.now()+250; while(performance.now()<end){}
      },{once:true}));
      const before=await page.evaluate(()=>window.submitCount);
      const result=await f.api(path+"/run",await request("human-pause","Gold"));
      const acknowledgement=await page.evaluate(()=>window.pauseAck);
      assert.equal(acknowledgement.ok,true,acknowledgement.error);
      assert.equal(acknowledgement.session.controller,null);
      assert.notEqual(result.status,"needs_verification");
      assert.equal(await page.evaluate(()=>window.submitCount),before);
      assert.equal((await f.api(path+"/state")).controller,null);
    });
    await sharing({kind:"disconnect"});
  } finally {await context?.close(); await f.stop();}
});
