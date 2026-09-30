import { pathToFileURL } from 'node:url';
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
      const fail = (kind, objective=true) => result.failures.push({id:prefix+':'+kind,objective});
      page.on('pageerror', error=>fail('script:'+error.name));
      try {
        const response = await page.goto(config.origin+check.path,{waitUntil:'domcontentloaded',timeout:20000});
        if (!response || response.status() >= 500) fail('server');
        else if (response.status() >= 400) fail('navigation');
        const text = await page.locator('body').innerText({timeout:5000});
        if (!text.trim() || /critical error on this website|Fatal error:|Parse error:/i.test(text)) fail('rendering');
        if (check.selector && !await page.locator(check.selector).isVisible()) fail('required-element');
        if (check.form_selector) {
          const form = page.locator(check.form_selector);
          if (!await form.isVisible() || !await form.locator('input, textarea, select, button').count()) fail('form');
        }
        for (const link of check.links) {
          if (!await page.locator('a').evaluateAll((items, target) => items.some(a=>new URL(a.href).pathname===target),link)) fail('link:'+link);
          // Use Chromium's pinned DNS and origin guard for destinations too.
          const destination = await context.newPage();
          try {
            const target = await destination.goto(config.origin+link,{waitUntil:'domcontentloaded',timeout:10000});
            if (!target || target.status()>=400 || new URL(destination.url()).origin!==config.origin) fail('destination:'+link);
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
