import assert from "node:assert/strict";
import test from "node:test";
import { chromium } from "playwright";
import { collectionsFixture } from "./fixtures/adminCollections.mjs";

const route = "/#configuration?config_tab=people_communications&config_section=targets";

test("configuration collections search, sort, edit original records, and preserve failed-save drafts", {timeout:60000}, async t => {
  const fixture = await collectionsFixture(); t.after(()=>fixture.server.close());
  const browser = await chromium.launch({headless:true}); t.after(()=>browser.close());
  const page = await browser.newPage({viewport:{width:1440,height:900}});
  page.setDefaultTimeout(7000);
  const errors=[]; page.on("pageerror",error=>errors.push(error.message));
  await page.goto(fixture.url + route);
  const collection = page.locator('.record-collection[aria-label="Communication targets"]');
  await collection.getByRole("searchbox").waitFor();
  const rows = collection.locator(".record-collection-summary");
  assert.equal(await rows.count(),18);
  assert.equal(await page.getByLabel("Team id",{exact:true}).isVisible(),false);
  assert.equal(await collection.getByLabel("Target id",{exact:true}).count(),0);
  const visible = await rows.evaluateAll(rows=>rows.filter(row=>row.getBoundingClientRect().bottom<window.innerHeight).length);
  assert.ok(visible >= 6, `Only ${visible} targets fit at 1440x900`);
  await collection.getByLabel("Sort communication targets").selectOption("descending");
  assert.match(await rows.first().innerText(),/teams-17/);
  await collection.getByLabel("Filter communication targets by provider",{exact:true}).selectOption("discord");
  assert.equal(await rows.count(),9);
  await collection.getByRole("searchbox").fill("DISCORD-02 notify");
  assert.equal(await rows.count(),1);
  await rows.first().focus();
  await page.keyboard.press("Enter");
  await collection.getByLabel("Target id",{exact:true}).fill("renamed-target");
  assert.equal(await collection.getByLabel("Target id",{exact:true}).inputValue(),"renamed-target");
  await collection.getByLabel("Address",{exact:true}).fill("channel:changed");
  await rows.first().click();
  assert.equal(await rows.count(),0);
  await collection.getByRole("button",{name:"Clear filters"}).click();
  await collection.getByRole("searchbox").fill("renamed-target");
  await rows.first().click();
  assert.equal(await collection.getByLabel("Address",{exact:true}).inputValue(),"channel:changed");
  fixture.setFailSave(true);
  await page.getByRole("button",{name:"Save changes",exact:true}).click();
  await page.getByText(/changed on the server/).waitFor();
  assert.equal(await collection.getByLabel("Address",{exact:true}).inputValue(),"channel:changed");
  assert.equal(fixture.writes[0].expected_revision,"r1");
  assert.equal(fixture.writes[0].config.targets[16].id,"renamed-target");
  assert.equal(fixture.writes[0].config.targets[0].id,"discord-18");
  fixture.setFailSave(false);
  await page.getByRole("button",{name:"Save changes",exact:true}).click();
  await page.getByText("Saved People & Communications overrides for root.",{exact:true}).waitFor();
  await collection.getByRole("searchbox").fill("no-such-target");
  await page.getByRole("button",{name:"Add target",exact:true}).click();
  await collection.getByLabel("Target id",{exact:true}).waitFor();
  assert.equal(await collection.getByRole("searchbox").inputValue(),"");
  assert.equal(await collection.getByLabel("Target id",{exact:true}).evaluate(el=>document.activeElement===el),true);
  await collection.getByLabel("Target id",{exact:true}).pressSequentially("new-target");
  assert.equal(await collection.getByLabel("Target id",{exact:true}).inputValue(),"new-target");
  await collection.getByLabel("Address",{exact:true}).fill("team:new/channel:general");
  await collection.getByRole("button",{name:"Remove",exact:true}).click();
  assert.equal(await rows.count(),18);
  assert.equal(await collection.getByLabel("Target id",{exact:true}).count(),0);
  assert.deepEqual(errors,[]);
});

test("configuration collections remain readable across desktop, narrow screens, and dark mode", {timeout:60000}, async t=>{
  const fixture=await collectionsFixture();t.after(()=>fixture.server.close());
  const browser=await chromium.launch({headless:true});t.after(()=>browser.close());
  const page=await browser.newPage();page.setDefaultTimeout(7000);
  await page.goto(fixture.url+route);
  await page.getByRole("searchbox",{name:"Search communication targets"}).waitFor();
  for (const width of [1600,1280,1000,768,390]) {
    await page.setViewportSize({width,height:900});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`Page overflow at ${width}px`);
    const content=page.locator(".feature-content");
    assert.ok(await content.evaluate(el=>el.scrollWidth<=el.clientWidth+1),`Content overflow at ${width}px`);
    const rows=page.locator('.record-collection[aria-label="Communication targets"] .record-collection-summary');
    await rows.first().click();
    assert.ok(await content.evaluate(el=>el.scrollWidth<=el.clientWidth+1),`Editor overflow at ${width}px`);
    await rows.first().click();
  }
  await page.getByRole("button",{name:"Dark mode",exact:true}).click();
  assert.equal(await page.locator("html").getAttribute("data-theme"),"dark");
  assert.notEqual(await page.locator(".record-collection-search").evaluate(el=>getComputedStyle(el).backgroundColor),"rgba(255, 255, 255, 0.6)");
  await page.setViewportSize({width:1440,height:900});
  await page.goto(fixture.url+"/#configuration?config_tab=people_communications&config_section=mailboxes");
  await page.getByRole("searchbox",{name:"Search email mailboxes"}).waitFor();
  await page.getByRole("searchbox",{name:"Search email mailboxes"}).fill("support@example.test");
  const mailboxes=page.locator('.record-collection[aria-label="Email mailboxes"]');
  assert.equal(await mailboxes.locator(".record-collection-summary").count(),1);
  await mailboxes.locator(".record-collection-summary").click();
  await mailboxes.getByLabel("Mailbox address",{exact:true}).fill("help@example.test");
  assert.equal(await mailboxes.getByLabel("Mailbox address",{exact:true}).inputValue(),"help@example.test");
  await page.goto(fixture.url+"/#configuration?config_tab=people_communications&config_section=contacts");
  const contacts=page.locator('.record-collection[aria-label="Contacts"]');
  await contacts.getByRole("searchbox").fill("on-call");
  await contacts.locator(".record-collection-summary").click();
  await contacts.getByLabel("Name",{exact:true}).fill("Operations support");
  assert.equal(await contacts.getByLabel("Name",{exact:true}).inputValue(),"Operations support");
});


test("inherited targets stay searchable and retain override/disable actions", {timeout:30000}, async t=>{
  const fixture=await collectionsFixture({directoryPath:"root/child"});t.after(()=>fixture.server.close());
  const browser=await chromium.launch({headless:true});t.after(()=>browser.close());
  const page=await browser.newPage();page.setDefaultTimeout(7000);
  await page.goto(fixture.url+route);
  const inherited=page.locator('.record-collection[aria-label="Inherited targets"]');
  await inherited.getByRole("searchbox").fill("parent/channel:alerts");
  await inherited.locator(".record-collection-summary").click();
  await inherited.getByRole("button",{name:"Disable",exact:true}).click();
  const local=page.locator('.record-collection[aria-label="Communication targets"]');
  await local.getByRole("searchbox").fill("inherited-alerts");
  await local.locator(".record-collection-summary").click();
  assert.equal(await local.getByLabel("Disable inherited target",{exact:true}).isChecked(),true);
  await page.getByRole("button",{name:"Save changes",exact:true}).click();
  await page.getByText("Saved People & Communications overrides for root/child.",{exact:true}).waitFor();
  assert.equal(fixture.writes.at(-1).config.targets.find(target=>target.id==="inherited-alerts").disabled,true);
});

test("read-only collections allow search and inspection while editing remains disabled", {timeout:30000}, async t=>{
  const fixture=await collectionsFixture({writable:false});t.after(()=>fixture.server.close());
  const browser=await chromium.launch({headless:true});t.after(()=>browser.close());
  const page=await browser.newPage();page.setDefaultTimeout(7000);
  await page.goto(fixture.url+route);
  const collection=page.locator('.record-collection[aria-label="Communication targets"]');
  await collection.getByRole("searchbox").fill("discord-02");
  await collection.locator(".record-collection-summary").click();
  assert.equal(await collection.getByLabel("Target id",{exact:true}).isDisabled(),true);
  assert.equal(await page.getByRole("button",{name:"Add target",exact:true}).isDisabled(),true);
  assert.equal(await page.getByRole("button",{name:"Save changes",exact:true}).isDisabled(),true);
  assert.deepEqual(fixture.writes,[]);
});

test("saving contacts closes the editor when sorted readback changes record indexes", {timeout:30000}, async t => {
  const fixture = await collectionsFixture({contacts:{
    zoe:{name:"Zoe", description:"Keep Zoe unchanged", email_addresses:[], phone_numbers:[]},
  }});
  t.after(() => fixture.server.close());
  const browser = await chromium.launch({headless:true});
  t.after(() => browser.close());
  const page = await browser.newPage();
  page.setDefaultTimeout(7000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(fixture.url + "/#configuration?config_tab=people_communications&config_section=contacts");
  const contacts = page.locator('.record-collection[aria-label="Contacts"]');
  await contacts.getByLabel("Sort contacts").selectOption("configured");
  await page.getByRole("button",{name:"Add contact",exact:true}).click();
  await contacts.getByLabel("Name",{exact:true}).fill("Amy");
  await contacts.locator(".contact-description-field textarea").fill("Amy before save");

  fixture.setFailSave(true);
  await page.getByRole("button",{name:"Save changes",exact:true}).click();
  await page.getByText(/changed on the server/).waitFor();
  assert.equal(await contacts.getByLabel("Name",{exact:true}).inputValue(),"Amy");
  assert.equal(await contacts.locator(".contact-description-field textarea").inputValue(),"Amy before save");

  fixture.setFailSave(false);
  await page.getByRole("button",{name:"Save changes",exact:true}).click();
  await page.getByText("Saved People & Communications overrides for root.",{exact:true}).waitFor();
  assert.deepEqual(Object.keys(fixture.writes.at(-1).config.contacts),["zoe","amy"]);
  const saved = await page.request.get(fixture.url + "/api/configuration/state");
  assert.deepEqual(Object.keys((await saved.json()).tab_snapshots.people_communications.raw.config.contacts),["amy","zoe"]);
  assert.equal(await contacts.locator('.record-collection-summary[aria-expanded="true"]').count(),0);
  assert.equal(await contacts.getByLabel("Name",{exact:true}).count(),0);

  await contacts.locator(".record-collection-summary").filter({hasText:"Amy"}).click();
  await contacts.locator(".contact-description-field textarea").fill("Amy after save");
  await page.getByRole("button",{name:"Save changes",exact:true}).click();
  await page.getByText("Saved People & Communications overrides for root.",{exact:true}).waitFor();
  assert.equal(fixture.writes.at(-1).config.contacts.amy.description,"Amy after save");
  assert.equal(fixture.writes.at(-1).config.contacts.zoe.description,"Keep Zoe unchanged");
  assert.deepEqual(errors,[]);
});
