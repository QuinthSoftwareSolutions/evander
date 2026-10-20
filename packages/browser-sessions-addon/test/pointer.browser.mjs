import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { chromium } from "playwright";
import { BrowserEngine } from "../dist/engine.mjs";

for (const left of [400, 1200]) {
  const top = left === 400 ? 80 : 600;
  test("real Chromium: referenced pointer actions reveal clipped targets at x=" + left, async () => {
    const server = createServer((_request, response) => {
      response.setHeader("Content-Type", "text/html");
      response.end(`<!doctype html><title>Scrollable action fixture</title>
        <style>#table{width:250px;height:180px;overflow:auto}
        #contents{position:relative;width:1600px;height:900px}
        button{position:absolute;left:${left}px;top:${top}px}</style>
        <div id="table"><div id="contents"><button onclick="window.selected=true">Select order</button></div></div>`);
    });
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    let browser;
    try {
      browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
      const page = await browser.newPage({ viewport: { width: 800, height: 600 } });
      const url = "http://127.0.0.1:" + server.address().port;
      await page.goto(url, { waitUntil: "domcontentloaded" });
      const cdp = await page.context().newCDPSession(page);
      const engine = new BrowserEngine((method, params) => cdp.send(method, params), {
        id:"pointer-fixture", controller:null, control_generation:1,
        allowed_origins:[url], expires_at:Date.now()/1000+60,
      }, () => {});
      let observed = await engine.execute({ action:"observe",generation:1 }, "agent:fixture");
      let target = observed.elements.find(item => item.name === "Select order");
      const scroll = () => page.locator("#table").evaluate(el => [el.scrollLeft,el.scrollTop]);
      const action = (kind, generation) => engine.execute({action:kind,generation,
        observation_id:observed.observation_id,ref:target.ref},"agent:fixture");
      await action("highlight",1).catch(error => {
        assert.match(error.message,/invalid_coordinates/);
      });
      await assert.rejects(action("click",1),/control_required/);
      assert.deepEqual(await scroll(),[0,0],"Observation and denied input must not scroll");
      engine.update({id:"pointer-fixture",controller:"agent:fixture",control_generation:2,
        allowed_origins:[url],expires_at:Date.now()/1000+60});
      observed = await engine.execute({action:"observe",generation:2},"agent:fixture");
      target = observed.elements.find(item => item.name === "Select order");
      await action("click",2);
      assert.equal(await page.evaluate(() => window.selected),true);
      const [xScroll,yScroll] = await scroll();
      assert.ok(xScroll > 0);
      assert.equal(yScroll > 0, top > 180);
      await page.locator("#table").evaluate(el => { el.scrollLeft=0;el.scrollTop=0; });
      observed = await engine.execute({action:"observe",generation:2},"agent:fixture");
      target = observed.elements.find(item => item.name === "Select order");
      await action("hover",2);
      assert.equal(await page.getByRole("button",{name:"Select order"}).evaluate(el => el.matches(":hover")),true);
    } finally {
      await browser?.close();
      await new Promise(resolve => server.close(resolve));
    }
  });
}
