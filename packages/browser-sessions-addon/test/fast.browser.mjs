import test from "node:test";
import assert from "node:assert/strict";
import {chromium} from "playwright";
import {BrowserEngine} from "../dist/engine.mjs";

test("real Chrome: rich observations, row context, keyboard selection and stale-target fencing",async()=>{
  const browser=await chromium.launch({headless:true,args:["--no-sandbox"]});
  try {
    const page=await browser.newPage();
    await page.route("https://application.test/**",route=>route.fulfill({contentType:"text/html",body:`<!doctype html><title>Accounts</title>
      <form aria-label="Account"><label>Name<input value="Example"></label><label>Password<input type="password" value="synthetic-secret"></label>
      <label>Active<input type="checkbox" checked></label><label>Region<select><option>North</option><option>South</option></select></label></form>
      <table><tr><td>Account A</td><td><button>Remove</button></td></tr><tr><td>Account B</td><td><button>Remove</button></td></tr></table>
      <p role="alert">Ready</p>`}));
    await page.goto("https://application.test/");
    const cdp=await page.context().newCDPSession(page);
    const state={id:"b",controller:"agent:a",control_generation:1,allowed_origins:["https://application.test"],expires_at:Date.now()/1000+60};
    const calls=[];
    const engine=new BrowserEngine((method,params)=>{calls.push(method);return cdp.send(method,params);},state,()=>{});
    const command=(action,extra={})=>({action,generation:1,deadline_at:Date.now()/1000+5,...extra});
    let o=await engine.execute(command("observe",{include_image:false}),"agent:a");
    assert.equal(o.title,"Accounts");assert.ok(o.text.includes("Ready"));assert.equal(o.image,undefined);
    assert.ok(!JSON.stringify(o).includes("synthetic-secret"));
    assert.equal(o.elements.find(e=>e.name==="Name").value,"Example");
    assert.equal(o.elements.find(e=>e.role==="checkbox").checked,"true");
    const remove=o.elements.filter(e=>e.role==="button" && e.name==="Remove");
    assert.equal(remove.length,2);
    assert.ok(remove[0].context.some(c=>c.text.includes("Account A")));
    assert.ok(remove[1].context.some(c=>c.text.includes("Account B")));
    const select=o.elements.find(e=>e.role==="combobox");
    await engine.execute(command("key",{observation_id:o.observation_id,revalidate:true,ref:select.ref,text:"ArrowDown"}),"agent:a");
    assert.equal(await page.locator("select").inputValue(),"South");
    o=await engine.execute(command("observe",{include_image:false}),"agent:a");
    const target=o.elements.find(e=>e.role==="button" && e.name==="Remove");
    await page.locator("td").first().evaluate(e=>e.textContent="Another account");
    const before=calls.filter(x=>x.startsWith("Input.")).length;
    await assert.rejects(engine.execute(command("click",{observation_id:o.observation_id,revalidate:true,ref:target.ref}),"agent:a"),/stale_target/);
    assert.equal(calls.filter(x=>x.startsWith("Input.")).length,before);
  } finally {await browser.close();}
});
