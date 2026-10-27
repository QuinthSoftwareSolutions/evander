import test from "node:test";
import assert from "node:assert/strict";
import {chromium} from "playwright";
import {BrowserEngine} from "../dist/engine.mjs";

async function fixture(locale="en-US") {
  const browser=await chromium.launch({headless:true,args:["--no-sandbox"]});
  const context=await browser.newContext({locale});
  const page=await context.newPage();
  await page.route("https://application.test/**",route=>route.fulfill({contentType:"text/html",body:`<!doctype html>
    <form aria-label="Editor"><label>Pickup date<input id="date" type="date" value="2024-12-31" min="2020-01-01" max="2030-12-31"></label>
    <label>Quantity<input id="quantity" type="number" value="2"></label>
    <label>Country<select id="country"><option value="">Choose</option><option value="se">Sweden</option><option value="ch">Switzerland</option><option value="us">United States</option></select></label>
    <label>Notes<input id="notes"></label></form><script>window.changes=[];window.dateChanges=[];country.addEventListener('change',()=>changes.push(country.value));date.addEventListener('change',()=>dateChanges.push(date.value));</script>`}));
  await page.goto("https://application.test/");
  const cdp=await context.newCDPSession(page), calls=[];
  let intercept=()=>{}, beforeSend=()=>{};
  const state={id:"native-fixture",controller:"agent:a",control_generation:1,allowed_origins:["https://application.test"],expires_at:Date.now()/1000+120};
  const engine=new BrowserEngine(async(method,params)=>{
    calls.push(method);await beforeSend(method,params);const result=await cdp.send(method,params);await intercept(method,params);return result;
  },state,()=>{});
  const command=(action,extra={})=>({action,generation:1,deadline_at:Date.now()/1000+5,...extra});
  const observe=()=>engine.execute(command("observe",{include_image:false}),"agent:a");
  const act=(observation,action,target,text)=>engine.execute(command(action,{observation_id:observation.observation_id,ref:target.ref,text,revalidate:true}),"agent:a");
  return {browser,page,cdp,engine,state,calls,observe,act,setIntercept:fn=>intercept=fn,setBeforeSend:fn=>beforeSend=fn};
}

for(const locale of ["en-US","en-GB","sv-SE","de-DE"]) test("native date fill, replacement and clear across locale "+locale,{timeout:15000},async()=>{
  const f=await fixture(locale);
  try {
    for(const value of ["2028-02-29","2027-10-31",""]){
      const observation=await f.observe(), target=observation.elements.find(e=>e.role==="date");
      assert.equal(target.name,"Pickup date");assert.equal(target.native_control,"date");
      assert.equal(target.value_format,"YYYY-MM-DD");
      assert.ok(!observation.elements.some(e=>e.name.startsWith("Month")),"Internal segments are not separate agent targets");
      await f.act(observation,"fill",target,value);
      assert.equal(await f.page.locator("#date").inputValue(),value);
      assert.equal((await f.observe()).elements.find(e=>e.role==="date").value,value);
    }
    assert.deepEqual(await f.page.evaluate(()=>dateChanges),["2028-02-29","2027-10-31",""]);
  } finally {await f.browser.close();}
});

test("native select chooses an observed option once and numbers accept fill",{timeout:15000},async()=>{
  const f=await fixture();
  try {
    let o=await f.observe();const select=o.elements.find(e=>e.role==="combobox");
    assert.equal(select.native_control,"select");
    assert.deepEqual(select.options.map(e=>e.name),["Choose","Sweden","Switzerland","United States"]);
    assert.ok(!o.elements.some(e=>e.role==="option"),"Native options are metadata, not unclickable targets");
    for(const [name,value] of [["Switzerland","ch"],["Sweden","se"],["United States","us"]]) {
      o=await f.observe();await f.act(o,"select",o.elements.find(e=>e.role==="combobox"),name);
      assert.equal(await f.page.locator("#country").inputValue(),value);
    }
    assert.deepEqual(await f.page.evaluate(()=>changes),["ch","se","us"]);
    o=await f.observe();const quantity=o.elements.find(e=>e.role==="spinbutton");
    await f.act(o,"fill",quantity,"12");
    assert.equal(await f.page.locator("#quantity").inputValue(),"12");
  } finally {await f.browser.close();}
});

test("readonly native date is not writable and receives no input",{timeout:10000},async()=>{
  const f=await fixture();
  try {
    await f.page.locator("#date").evaluate(e=>e.readOnly=true);
    const o=await f.observe(), date=o.elements.find(e=>e.role==="date"), start=f.calls.length;
    assert.equal(date.readonly,true);
    await assert.rejects(f.act(o,"fill",date,"2027-03-18"),/stale_target/);
    assert.ok(!f.calls.slice(start).some(m=>m.startsWith("Input.") || m==="DOM.focus"));
  } finally {await f.browser.close();}
});

test("select cannot mutate a native date through the direct action API",{timeout:10000},async()=>{
  const f=await fixture();
  try {
    const o=await f.observe(), date=o.elements.find(e=>e.role==="date"), start=f.calls.length;
    await assert.rejects(f.act(o,"select",date,"2027-03-18"),/unsupported_target/);
    assert.ok(!f.calls.slice(start).some(m=>m.startsWith("Input.") || m==="Runtime.callFunctionOn"));
    assert.equal(await f.page.locator("#date").inputValue(),"2024-12-31");
  } finally {await f.browser.close();}
});

for(const value of ["2027-02-29","2027-13-01","03/18/2027","2019-01-01"]) test("invalid native date is rejected before input: "+value,{timeout:10000},async()=>{
  const f=await fixture();
  try {
    const o=await f.observe(), date=o.elements.find(e=>e.role==="date"), start=f.calls.length;
    await assert.rejects(f.act(o,"fill",date,value),/invalid_date/);
    assert.ok(!f.calls.slice(start).some(m=>m.startsWith("Input.") || m==="DOM.focus"));
    assert.equal(await f.page.locator("#date").inputValue(),"2024-12-31");
  } finally {await f.browser.close();}
});

test("native date cannot set a value after control is revoked during node resolution",{timeout:10000},async()=>{
  const f=await fixture();
  try {
    const o=await f.observe(), date=o.elements.find(e=>e.role==="date"), start=f.calls.length;
    f.setIntercept(method=>{if(method==="DOM.resolveNode") f.engine.update({...f.state,controller:null,control_generation:2});});
    await assert.rejects(f.act(o,"fill",date,"2027-03-18"),/stale_control/);
    assert.ok(!f.calls.slice(start).some(m=>m.startsWith("Input.") || m==="Runtime.callFunctionOn"));
    assert.equal(await f.page.locator("#date").inputValue(),"2024-12-31");
  } finally {await f.browser.close();}
});

for(const mode of ["duplicate","disabled"]) test("native select rejects ambiguous or disabled labels before input: "+mode,{timeout:10000},async()=>{
  const f=await fixture();
  try {
    await f.page.evaluate(mode=>{
      const option=new Option("Sweden","other");
      if(mode==="disabled") document.querySelector('option[value="se"]').disabled=true;
      else document.querySelector("select").append(option);
    },mode);
    const o=await f.observe(), target=o.elements.find(e=>e.role==="combobox"), start=f.calls.length;
    await assert.rejects(f.act(o,"select",target,"Sweden"),/ambiguous_option/);
    assert.ok(!f.calls.slice(start).some(m=>m.startsWith("Input.") || m==="DOM.focus"));
    assert.equal(await f.page.locator("#country").inputValue(),"");
  } finally {await f.browser.close();}
});

test("native date enforces full bounds and preserves the value-attribute step base",{timeout:10000},async()=>{
  const f=await fixture();
  try {
    await f.page.locator("#date").evaluate(e=>{e.min="2020-06-15";e.max="2030-06-15";e.defaultValue="2027-03-18";e.step="2";});
    let o=await f.observe(),date=o.elements.find(e=>e.role==="date");
    assert.equal(date.step_base,"2020-06-15");
    await assert.rejects(f.act(o,"fill",date,"2020-01-01"),/invalid_date/);
    await f.page.locator("#date").evaluate(e=>e.removeAttribute("min"));
    o=await f.observe();date=o.elements.find(e=>e.role==="date");
    assert.equal(date.step_base,"2027-03-18");
    await f.act(o,"fill",date,"2027-03-20");
    o=await f.observe();date=o.elements.find(e=>e.role==="date");
    await assert.rejects(f.act(o,"fill",date,"2027-03-21"),/invalid_date/);
    assert.equal(await f.page.locator("#date").inputValue(),"2027-03-20");
    assert.deepEqual(await f.page.evaluate(()=>dateChanges),["2027-03-20"]);
  } finally {await f.browser.close();}
});

test("native select handles repeated characters, prefixes, groups and disabled options exactly",{timeout:10000},async()=>{
  const f=await fixture();
  try {
    await f.page.locator("select").evaluate(e=>e.innerHTML='<option value="">Choose</option><optgroup label="Letters"><option disabled>AA</option><option value="aaa">AAA</option><option value="extra">AAA extra</option><option value="aab">AAB</option></optgroup><option value="aac">AAC</option>');
    for(const [label,value] of [["AAA","aaa"],["AAC","aac"],["AAB","aab"]]){
      const o=await f.observe();await f.act(o,"select",o.elements.find(e=>e.role==="combobox"),label);
      assert.equal(await f.page.locator("select").inputValue(),value);
    }
    assert.deepEqual(await f.page.evaluate(()=>changes),["aaa","aac","aab"]);
  } finally {await f.browser.close();}
});

test("native selection rechecks the exact option before committing once",{timeout:10000},async()=>{
  const f=await fixture();
  try {
    await f.page.locator("select").evaluate(e=>e.innerHTML='<option value="">Choose</option><option value="aaa">AAA</option><option value="aab">AAB</option><option value="aac">AAC</option>');
    const o=await f.observe();
    f.setBeforeSend(async method=>{
      if(method==="Runtime.callFunctionOn") await f.page.locator('option[value="aaa"]').evaluate(e=>e.label="Changed");
    });
    await assert.rejects(f.act(o,"select",o.elements.find(e=>e.role==="combobox"),"AAA"),/ambiguous_option/);
    assert.equal(await f.page.locator("select").inputValue(),"");
    assert.deepEqual(await f.page.evaluate(()=>changes),[]);
  } finally {await f.browser.close();}
});

test("native option whitespace is normalized consistently and collisions are refused",{timeout:10000},async()=>{
  const f=await fixture();
  try {
    await f.page.locator("select").evaluate(e=>e.innerHTML='<option value="">Choose</option><option value="nw">North&nbsp;West</option><option label="East   Coast" value="ec">East</option>');
    for(const [name,value] of [["North West","nw"],["East Coast","ec"]]){
      const o=await f.observe(), target=o.elements.find(e=>e.role==="combobox");
      assert.ok(target.options.some(e=>e.name===name));
      await f.act(o,"select",target,name);
      assert.equal(await f.page.locator("select").inputValue(),value);
      assert.equal((await f.observe()).elements.find(e=>e.role==="combobox").value,name);
    }
    await f.page.locator("select").evaluate(e=>e.add(new Option("North West","duplicate")));
    const o=await f.observe();
    await assert.rejects(f.act(o,"select",o.elements.find(e=>e.role==="combobox"),"North West"),/ambiguous_option/);
    assert.equal(await f.page.locator("select").inputValue(),"ec");
    assert.deepEqual(await f.page.evaluate(()=>changes),["nw","ec"]);
  } finally {await f.browser.close();}
});

for(const action of ["readonly","fieldset-disabled","expired"]) test("native date rechecks late constraints: "+action,{timeout:10000},async()=>{
  const f=await fixture();
  try {
    const o=await f.observe(), date=o.elements.find(e=>e.role==="date");
    f.setBeforeSend(async(method,params)=>{
      if(method!=="Runtime.callFunctionOn") return;
      if(action==="readonly") await f.page.locator("#date").evaluate(e=>e.readOnly=true);
      else if(action==="fieldset-disabled") await f.page.locator("#date").evaluate(e=>{const group=document.createElement("fieldset");e.replaceWith(group);group.append(e);group.disabled=true;});
      else params.arguments[3].value=Date.now()/1000-1;
    });
    await assert.rejects(f.act(o,"fill",date,"2027-03-18"),action==="expired" ? /run_deadline/ : /stale_target/);
    assert.equal(await f.page.locator("#date").inputValue(),"2024-12-31");
    assert.deepEqual(await f.page.evaluate(()=>dateChanges),[]);
  } finally {await f.browser.close();}
});
