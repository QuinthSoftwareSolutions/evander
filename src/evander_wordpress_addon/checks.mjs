import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
let raw = '';
for await (const chunk of process.stdin) raw += chunk;
const config = JSON.parse(raw);
const { chromium } = await import(pathToFileURL(config.module));
const browser = await chromium.launch({headless:true, args:[
  `--host-resolver-rules=${Object.entries(config.hosts).map(([host,ip])=>`MAP ${host} ${ip}`).join(',')},EXCLUDE localhost`,
  '--disable-background-networking','--no-proxy-server','--disable-quic'], executablePath:process.env.PLAYWRIGHT_MCP_EXECUTABLE_PATH||undefined});
const result = {failures:[], images:[], observations:[], uncertain:false};
try {
  for (const viewport of [{width:1280,height:800},{width:390,height:844}]) {
    const context = await browser.newContext({viewport, serviceWorkers:'block'});
    await context.routeWebSocket('**/*', route=>route.close({code:1008,reason:'Verification does not use WebSockets'}));
    await context.route('**/*', async route => {
      let origin;
      try { origin = new URL(route.request().url()).origin; } catch { return route.abort(); }
      if (!config.origins.includes(origin)) return route.abort();
      await route.continue();
    });
    for (const check of config.checks) {
      const page = await context.newPage();
      const prefix = `${check.path}:${viewport.width}`;
      const fail = (kind, objective=true, specification=null) => result.failures.push({
        id:prefix+(specification===null ? '' : ':'+createHash('sha256').update(specification).digest('hex'))+':'+kind,objective});
      page.on('pageerror', error=>fail('script:'+error.name));
      try {
        const response = await page.goto(config.origin+check.path,{waitUntil:'domcontentloaded',timeout:20000});
        if (!response || response.status() >= 500) fail('server');
        else if (response.status() >= 400) fail('navigation');
        const text = await page.locator('body').innerText({timeout:5000});
        if (!text.trim() || /critical error on this website|Fatal error:|Parse error:/i.test(text)) fail('rendering');
        if (check.selector && !await page.locator(check.selector).isVisible()) fail('required-element',true,check.selector);
        if (check.form_selector) {
          const form = page.locator(check.form_selector);
          if (!await form.isVisible() || !await form.locator('input, textarea, select, button').count()) fail('form',true,check.form_selector);
        }
        for (const link of check.links) {
          const expected = new URL(link, config.origin).href;
          const matched = await page.locator('a').evaluateAll((items, target) =>
            items.find(a=>a.href===target)?.href ?? null, expected);
          if (!matched) { fail('link:'+link); continue; }
          // Use Chromium's pinned DNS and origin guard for destinations too.
          const destination = await context.newPage();
          try {
            const target = await destination.goto(matched,{waitUntil:'domcontentloaded',timeout:10000});
            if (!target || target.status()>=400 || new URL(destination.url()).href!==expected) fail('destination:'+link);
          } finally { await destination.close(); }
        }
        const shot = await page.screenshot({fullPage:false,timeout:10000});
        result.images.push({path:check.path,viewport,data:shot.toString('base64')});
        result.observations.push({path:check.path,viewport,status:response?.status()});
      } catch { result.uncertain=true; fail('connectivity',false); }
      await page.close();
    }
    await context.close();
  }
  process.stdout.write(JSON.stringify(result));
} finally { await browser.close(); }
