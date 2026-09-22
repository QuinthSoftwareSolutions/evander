import assert from "node:assert/strict";
import test from "node:test";
import {mkdir} from "node:fs/promises";
import {fileURLToPath} from "node:url";
import {chromium} from "playwright";
import {createServer} from "vite";

test("credential UI clears submitted values, enforces locks and isolates directory changes", {timeout:60000}, async t => {
  const server = await createServer({
    root:fileURLToPath(new URL("..", import.meta.url)), server:{host:"127.0.0.1",port:0}, logLevel:"error",
    plugins:[{name:"credential-fixture", configureServer(server) {
      server.middlewares.use(async (request,response,next) => {
        if (request.url !== "/credential-fixture") return next();
        const html = await server.transformIndexHtml(request.url, `<html><body><div id="root"></div><script type="module">
          import React from 'react'; import {createRoot} from 'react-dom/client';
          import {CredentialsConfiguration} from '/src/features/configuration/CredentialsConfiguration.tsx';
          import '/src/styles.css';
          function Fixture() {
            const [scope,setScope] = React.useState('root');
            const [locked,setLocked] = React.useState(false);
            return React.createElement('main',{style:{padding:'20px',maxWidth:'1200px'}},
              React.createElement('button',{onClick:()=>setScope(scope==='root'?'root/other':'root')},'Switch directory'),
              React.createElement('button',{onClick:()=>setLocked(!locked)},'Toggle lock'),
              React.createElement(CredentialsConfiguration,{directoryPath:scope,disabled:locked}));
          }
          createRoot(document.getElementById('root')).render(React.createElement(Fixture));
        </script></body></html>`);
        response.setHeader("Content-Type","text/html"); response.end(html);
      });
    }}],
  });
  await server.listen(); t.after(()=>server.close());
  const browser = await chromium.launch({headless:true}); t.after(()=>browser.close());
  const page = await browser.newPage({viewport:{width:1280,height:900}});
  page.setDefaultTimeout(7000);
  const errors=[]; page.on("pageerror", error=>errors.push(error.message));
  page.on("dialog", dialog=>dialog.accept());
  const ref="cred_"+"a".repeat(32);
  let state={directory_path:"root",credentials:[],connector_profiles:[{id:"documents",type:"bearer_env",fields:["token"],credential_ref:null}],
    connector_revision:"config-1",teams_revision:"teams-1",teams_configured:false,store:{initialized:true,available:true}};
  const submissions=[];
  let fail=true, delayed;
  await page.route("**/api/admin/credentials**", async route=>{
    const request=route.request(), scope=new URL(request.url()).searchParams.get("directory_path");
    if(request.method()==="GET") return route.fulfill({json:scope==="root"?state:{...state,directory_path:scope,
      credentials:state.credentials.map(c=>({...c,local:false,bindings:[]})),store:{initialized:true,available:false}}});
    const payload=request.postDataJSON(); submissions.push(payload);
    if(fail) {fail=false;return route.fulfill({status:409,json:{error:"Configuration changed. Reload before saving."}});}
    if(payload.values?.token==="delayed-value") {delayed=route;return;}
    if(request.method()==="POST") {
      state={...state,connector_revision:"config-2",credentials:[{id:ref,label:"Documents",kind:"connector",owner_directory:"root",
        local:true,status:"active",revision:"1:1",expires_at:null,bindings:[{consumer:"connector",connector_id:"documents",profile_id:"documents"}],
        current_bindings:[{consumer:"connector",connector_id:"documents",destination:"https://documents.example.test",operations:["read"]}],bindings_revision:"bindings-1"}],
        connector_profiles:[{...state.connector_profiles[0],type:"bearer",credential_ref:ref}]};
    } else {
      const record=state.credentials[0];
      state={...state,credentials:payload.action==="delete"?[]:[{...record,revision:"2:2",
        status:payload.action==="disable"?"disabled":payload.action==="enable"?"active":record.status}]};
    }
    return route.fulfill({json:state});
  });
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/credential-fixture`);
  await page.getByLabel("Credential authentication profile").selectOption("documents");
  await page.getByLabel("Credential token",{exact:true}).fill("synthetic-ui-secret");
  await page.getByRole("button",{name:"Save and use credential",exact:true}).click();
  await page.getByRole("alert").filter({hasText:"Configuration changed"}).waitFor();
  assert.equal(await page.getByLabel("Credential token",{exact:true}).inputValue(),"");
  await page.getByLabel("Credential token",{exact:true}).fill("synthetic-ui-secret");
  await page.getByRole("button",{name:"Save and use credential",exact:true}).click();
  await page.getByRole("status").filter({hasText:"Credential changes saved"}).waitFor();
  assert.equal(submissions.at(-1).expected_config_revision,"config-1");
  assert.equal((await page.locator("body").innerText()).includes("synthetic-ui-secret"),false);
  await page.getByRole("button",{name:"Replace values",exact:true}).click();
  await page.getByLabel("Credential token",{exact:true}).fill("replacement-ui-secret");
  await page.getByRole("button",{name:"Save replacement",exact:true}).click();
  await page.getByRole("status").filter({hasText:"Credential changes saved"}).waitFor();
  assert.equal(submissions.at(-1).expected_revision,"1:1");
  await page.getByRole("button",{name:"Toggle lock",exact:true}).click();
  assert.equal(await page.getByRole("button",{name:"Replace values",exact:true}).isDisabled(),true);
  await page.getByRole("button",{name:"Toggle lock",exact:true}).click();
  await page.getByRole("button",{name:"Disable",exact:true}).click();
  await page.getByRole("button",{name:"Enable",exact:true}).waitFor();
  await page.getByRole("button",{name:"Enable",exact:true}).click();
  await page.getByRole("button",{name:"Disable",exact:true}).waitFor();
  await page.getByRole("button",{name:"Approve current consumers",exact:true}).click();
  await page.getByRole("status").filter({hasText:"Credential changes saved"}).waitFor();
  assert.equal(submissions.at(-1).expected_bindings_revision,"bindings-1");
  const artifacts=fileURLToPath(new URL("../../../output/playwright/",import.meta.url));
  await mkdir(artifacts,{recursive:true});
  await page.screenshot({path:artifacts+"credentials-desktop.png",fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:artifacts+"credentials-mobile.png",fullPage:true});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1));
  await page.getByRole("button",{name:"Replace values",exact:true}).click();
  await page.getByLabel("Credential token",{exact:true}).fill("delayed-value");
  await page.getByRole("button",{name:"Save replacement",exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('button[type="submit"]')?.disabled);
  await page.getByRole("button",{name:"Switch directory",exact:true}).click();
  await page.getByText("Manage in owning directory",{exact:true}).waitFor();
  assert.equal(await page.getByRole("button",{name:"Save and use credential",exact:true}).isDisabled(),true);
  assert.ok(delayed);
  await delayed.fulfill({json:state});
  await page.getByText("Manage in owning directory",{exact:true}).waitFor();
  assert.equal(await page.getByLabel("Credential token",{exact:true}).count(),0);
  assert.equal(await page.getByRole("button",{name:"Replace values",exact:true}).count(),0);

  // A root owner can sign into either shared credential without a root Teams consumer.
  const teamsRefs=["cred_"+"b".repeat(32),"cred_"+"c".repeat(32)];
  state={...state,credentials:teamsRefs.map((id,index)=>({id,label:`Shared Teams ${index+1}`,kind:"teams_oauth",owner_directory:"root",
    local:true,status:"active",revision:"1:1",expires_at:null,bindings:[{consumer:"teams",directory:"root/a",identity_id:`shared-${index+1}`,destination:"https://graph.microsoft.com/v1.0"}]}))};
  const authRequests=[];
  let delayLogin=false, pendingLogin;
  const loginResult={authenticated:false,status:"device_code_pending",login_session_id:"pending-2",user_code:"ABCD",verification_uri:"https://microsoft.com/devicelogin"};
  await page.route("**/api/teams-auth/**",async route=>{
    const request=route.request(),url=new URL(request.url());
    authRequests.push({path:url.pathname,ref:url.searchParams.get("credential_ref"),scope:url.searchParams.get("directory_path")});
    if(url.pathname.endsWith("/login")) {
      if(delayLogin) {pendingLogin=route;return;}
      return route.fulfill({json:loginResult});
    }
    const authenticated=url.pathname.endsWith("/complete");
    return route.fulfill({json:{authenticated,status:authenticated?"authenticated":"logged_out"}});
  });
  await page.getByRole("button",{name:"Switch directory",exact:true}).click();
  await page.getByRole("row").filter({hasText:"Shared Teams 2"}).getByRole("button",{name:"Teams sign-in",exact:true}).click();
  const signIn=page.getByRole("region",{name:"Teams sign-in for Shared Teams 2"});
  await signIn.getByText("Signed out.",{exact:true}).waitFor();
  await signIn.getByRole("button",{name:"Start sign-in",exact:true}).click();
  await signIn.getByText("ABCD",{exact:true}).waitFor();
  await signIn.getByRole("button",{name:"Check sign-in status",exact:true}).click();
  await signIn.getByText("Signed in.",{exact:true}).waitFor();
  await signIn.getByRole("button",{name:"Sign out",exact:true}).click();
  await signIn.getByText("Signed out.",{exact:true}).waitFor();
  assert.deepEqual(authRequests.map(r=>r.path.split("/").at(-1)),["status","login","complete","logout"]);
  assert.ok(authRequests.every(r=>r.ref===teamsRefs[1] && r.scope==="root"));
  await page.getByRole("button",{name:"Toggle lock",exact:true}).click();
  assert.equal(await signIn.getByRole("button",{name:"Start sign-in",exact:true}).isDisabled(),true);
  await page.getByRole("button",{name:"Toggle lock",exact:true}).click();
  await page.getByRole("row").filter({hasText:"Shared Teams 1"}).getByRole("button",{name:"Teams sign-in",exact:true}).click();
  const firstSignIn=page.getByRole("region",{name:"Teams sign-in for Shared Teams 1"});
  await firstSignIn.getByText("Signed out.",{exact:true}).waitFor();
  delayLogin=true;
  const loginRequest=page.waitForRequest(r=>r.url().includes("/teams-auth/login"));
  await firstSignIn.getByRole("button",{name:"Start sign-in",exact:true}).click();
  await loginRequest;
  await page.getByRole("button",{name:"Switch directory",exact:true}).click();
  await page.getByText("Manage credentials for root/other.",{exact:false}).waitFor();
  assert.ok(pendingLogin);
  await pendingLogin.fulfill({json:loginResult});
  assert.equal(await page.getByRole("region",{name:/Teams sign-in for/}).count(),0);
  assert.equal(await page.getByText("ABCD",{exact:true}).count(),0);
  assert.deepEqual(errors,[]);
});
