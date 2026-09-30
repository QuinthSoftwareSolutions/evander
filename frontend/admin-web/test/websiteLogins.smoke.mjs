import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { createServer } from "vite";

test("website login UI edits profiles, keeps failed inputs, rotates secrets, tests login and binds targets", {timeout: 60000}, async t => {
  const server = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), server: {host: "127.0.0.1", port: 0}, logLevel: "error",
    plugins: [{name: "website-login-fixture", configureServer(server) {
      server.middlewares.use(async (request, response, next) => {
        if (request.url !== "/website-login-fixture") return next();
        const html = await server.transformIndexHtml(request.url, `<html><body><div id="root"></div><script type="module">
          import React from 'react'; import {createRoot} from 'react-dom/client';
          import {WebsiteLoginsConfiguration} from '/src/features/configuration/WebsiteLoginsConfiguration.tsx';
          import {HttpTargetsEditor} from '/src/features/httpTargets/HttpTargetsEditor.tsx';
          import {httpTargetsPayload} from '/src/features/httpTargets/httpTargetDraft.ts';
          import '/src/design/baseline.css';
          import '/src/design/tokens.css';
          import '/src/design/controls.css';
          import '/src/design/layout.css';
          import '/src/design/collections.css';
          import '/src/design/workspaces.css';
          import '/src/design/surfaces.css';
          document.documentElement.dataset.adminDesign = '';
          function Fixture() {
            const [mode,setMode] = React.useState('profiles');
            const [scope,setScope] = React.useState('root');
            const [locked,setLocked] = React.useState(false);
            const [entries,setEntries] = React.useState([{id:'one',alias:'app',baseUrl:'https://training.example.test',metadata:{owner:'workflow'}}]);
            return React.createElement(React.Fragment, null,
              React.createElement('button',{onClick:()=>setMode(mode==='profiles'?'targets':'profiles')},'Switch editor'),
              React.createElement('button',{onClick:()=>setScope(scope==='root'?'root/other':'root')},'Switch directory'),
              React.createElement('button',{onClick:()=>setLocked(!locked)},'Toggle lock'),
              mode==='profiles' ? React.createElement(WebsiteLoginsConfiguration,{directoryPath:scope,disabled:locked}) :
                React.createElement(HttpTargetsEditor,{entries,onChange:setEntries,directoryPath:scope}),
              React.createElement('button',{onClick:()=>window.savedTarget=httpTargetsPayload(entries)},'Save targets'));
          }
          createRoot(document.getElementById('root')).render(React.createElement(Fixture));
        </script></body></html>`);
        response.setHeader("Content-Type", "text/html"); response.end(html);
      });
    }}],
  });
  await server.listen(); t.after(() => server.close());
  const browser = await chromium.launch({headless: true, executablePath: process.env.PLAYWRIGHT_MCP_EXECUTABLE_PATH || undefined}); t.after(() => browser.close());
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  page.setDefaultTimeout(7000);
  const errors = []; page.on("pageerror", e => errors.push(e.message));
  let snapshot = {directory_path: "root", revision: "r0", profiles: []};
  let failSave = true;
  const submissions = [];
  await page.route("**/api/managed-website-sessions**", async route => {
    const request = route.request(), url = new URL(request.url());
    if (request.method() === "GET") {
      const data = url.searchParams.get("directory_path") === "root" ? snapshot : {directory_path: "root/other", revision:"other", profiles: []};
      return route.fulfill({json: data});
    }
    const payload = request.postDataJSON();
    if (request.method() === "PUT") {
      submissions.push(payload);
      if (failSave) {failSave = false; return route.fulfill({status:409,json:{error:"Website configuration changed. Reload before saving."}});}
      snapshot = {...snapshot, revision: `r${submissions.length}`, profiles: [{id: decodeURIComponent(url.pathname.split('/').at(-1)), profile: payload.profile, local: true,
        credentials: {configured_fields: Object.keys(payload.profile.credential_fields), managed_by: payload.profile.adapter === 'wordpress' ? 'wordpress' : undefined,
          updated_at:Date.now()/1000, owner_directory:"root"}, last_test:null}]};
    } else if (request.method() === "POST") {
      snapshot.profiles[0].last_test = {status: "ok", checked_at:Date.now()/1000, expires_at:Date.now()/1000+3600};
    } else if (url.pathname.endsWith("/credentials")) {
      snapshot.profiles[0].credentials.configured_fields = []; snapshot.revision += "-removed";
    } else {snapshot.profiles = []; snapshot.revision += "-deleted";}
    return route.fulfill({json:snapshot});
  });
  const address = server.httpServer.address();
  await page.goto(`http://127.0.0.1:${address.port}/website-login-fixture`);
  await page.getByRole("button", {name:"New website login",exact:true}).click();
  await page.getByLabel("Profile ID",{exact:true}).fill("training");
  await page.getByLabel("Website origin",{exact:true}).fill("https://training.example.test");
  await page.getByLabel("Login endpoint",{exact:true}).fill("https://training.example.test/auth");
  await page.getByLabel("Value for username",{exact:true}).fill("fake-ui-user");
  await page.getByLabel("Value for password",{exact:true}).fill("fake-ui-password");
  await page.getByLabel("Expiry source",{exact:true}).selectOption("jwt");
  await page.getByLabel("Maximum age (seconds)",{exact:true}).fill("28800");
  await page.getByRole("button",{name:"Save website login",exact:true}).click();
  await page.getByRole("alert").filter({hasText:"configuration changed"}).waitFor();
  assert.equal(await page.getByLabel("Value for password",{exact:true}).inputValue(), "fake-ui-password");
  await page.getByRole("button",{name:"Save website login",exact:true}).click();
  await page.getByRole("status").filter({hasText:"Website login saved"}).waitFor();
  assert.equal(await page.getByLabel("Value for password",{exact:true}).inputValue(), "");
  assert.equal(await page.getByLabel("Value for username",{exact:true}).inputValue(), "");
  assert.equal(submissions.at(-1).profile.jwt_response_pointer, "/token");
  assert.equal(submissions.at(-1).profile.max_age_seconds, 28800);
  assert.equal(submissions.at(-1).credentials.password, "fake-ui-password");
  await page.getByLabel("Value for password",{exact:true}).fill("rotated-ui-password");
  await page.getByRole("button",{name:"Save website login",exact:true}).click();
  await page.getByRole("status").filter({hasText:"Website login saved"}).waitFor();
  assert.deepEqual(submissions.at(-1).credentials, {password:"rotated-ui-password"});
  await page.getByRole("button",{name:"Test saved login",exact:true}).click();
  await page.getByRole("status").filter({hasText:"Login succeeded"}).waitFor();
  assert.equal((await page.locator("body").innerText()).includes("rotated-ui-password"), false);
  await page.getByRole("button",{name:"Toggle lock",exact:true}).click();
  assert.equal(await page.getByRole("button",{name:"Test saved login",exact:true}).isDisabled(), true);
  await page.getByRole("button",{name:"Toggle lock",exact:true}).click();
  await page.getByRole("button",{name:"Switch editor",exact:true}).click();
  await page.getByLabel("Managed website login",{exact:true}).selectOption("training");
  await page.getByLabel("Allowed base URL",{exact:true}).fill("https://training.example.test/path");
  await page.getByRole("button",{name:"Save targets",exact:true}).click();
  const target = await page.evaluate(() => window.savedTarget.targets.app);
  assert.equal(target.managed_website_session,"training");
  assert.deepEqual(target.metadata,{owner:"workflow"});
  await page.getByRole("button",{name:"Switch directory",exact:true}).click();
  await page.getByRole("option",{name:"training (unavailable; retained)",exact:true}).waitFor({state:"attached"});
  assert.equal(await page.getByLabel("Managed website login",{exact:true}).inputValue(), "training");
  await page.getByRole("button",{name:"Switch directory",exact:true}).click();
  await page.getByRole("button",{name:"Switch editor",exact:true}).click();
  await page.getByRole("button",{name:"training",exact:true}).click();
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:"/tmp/website-logins-mobile.png",fullPage:true});
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
  await page.setViewportSize({width:1440,height:1000});
  await page.screenshot({path:"/tmp/website-logins-desktop.png",fullPage:true});
  const checkbox = await page.getByRole("checkbox", {name: "Enabled", exact: true}).boundingBox();
  const input = await page.getByRole("textbox", {name: "Login endpoint", exact: true}).boundingBox();
  assert.ok(checkbox && input);
  assert.ok(Math.abs((checkbox.y + checkbox.height / 2) - (input.y + input.height / 2)) <= 2,
    "standalone checkboxes should align with the adjacent text input");
  page.on("dialog", dialog => dialog.accept());
  await page.getByRole("button",{name:"Remove saved credentials",exact:true}).click();
  await page.getByText("Credentials missing",{exact:true}).waitFor();
  await page.getByRole("button",{name:"Remove local profile",exact:true}).click();
  await page.getByText("No website logins configured.",{exact:true}).waitFor();

  const adapterProfile = {enabled:true, adapter:'wordpress', adapter_config:{site_id:'main'},
    browser_origin:'https://design.example.test', login_url:'https://design.example.test/wp-json/evander/v1/browser/ticket',
    credential_source:'environment', credential_ref:null, credential_fields:{}, public_credential_fields:[],
    static_fields:{}, local_storage:{}, session_storage:{}, cookies_response_pointer:null,
    jwt_response_pointer:null, expires_in_response_pointer:null,
    max_age_seconds:900, renew_before_seconds:60, timeout_seconds:20, max_response_bytes:262144};
  snapshot = {directory_path:'root', revision:'adapter-r0', profiles:[{id:'wordpress', profile:adapterProfile, local:true,
    credentials:{configured_fields:[], managed_by:'wordpress', updated_at:null, owner_directory:null}, last_test:null}]};
  await page.getByRole('button',{name:'Reload website logins',exact:true}).click();
  await page.getByRole('button',{name:'wordpress',exact:true}).click();
  await page.getByRole('checkbox',{name:'Enabled',exact:true}).uncheck();
  await page.getByLabel('Maximum age (seconds)',{exact:true}).fill('600');
  await page.getByRole('button',{name:'Save website login',exact:true}).click();
  await page.getByRole('status').filter({hasText:'Website login saved'}).waitFor();
  assert.deepEqual(submissions.at(-1).profile, {...adapterProfile, enabled:false, max_age_seconds:600});
  assert.deepEqual(submissions.at(-1).credentials, {});
  assert.equal(await page.getByRole('button',{name:'Add login field',exact:true}).count(), 0);
  assert.equal(await page.getByLabel('Credential source',{exact:true}).count(), 0);
  assert.deepEqual(errors,[]);
});
