import { fileURLToPath } from "node:url";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { createServer } from "vite";

export async function collectionsFixture({ root = fileURLToPath(new URL("../..", import.meta.url)), port = 0, directoryPath = "root", writable = true, contacts } = {}) {
  const writes = [];
  let failSave = false;
  const targets = Array.from({length: 18}, (_, index) => ({
    id: `${index % 2 ? "teams" : "discord"}-${String(18 - index).padStart(2, "0")}`,
    provider: index % 2 ? "teams" : "discord", kind: index % 2 ? "teams" : "discord",
    address: index % 2 ? `team:operations/channel:channel-${index}` : `channel:1234567890${index}`,
    policy: {capabilities: ["read", "notify"]},
  }));
  const field = (source_domain, field_name, label) => ({source_domain, field_name, label, kind:"json", options:[], root_starts_editable:true, tooltip:`Manage ${label.toLowerCase()}.`});
  const tab = {key:"people_communications", title:"People & Communications", help_text:"People and messaging.", underlying_domains:["contacts", "communication_targets", "email"], sections:[
    {key:"targets", title:"Communication targets", fields:[field("communication_targets", "targets", "Communication targets")]},
    {key:"contacts", title:"Contacts", fields:[field("contacts", "contacts", "Contacts")]},
    {key:"mailboxes", title:"Email mailboxes", fields:[field("email", "email_mailboxes", "Email mailboxes")]},
  ]};
  let config = {targets, contacts:contacts ?? {ops:{name:"Operations", description:"On-call team", email_addresses:["ops@example.test"], phone_numbers:[]}}, email_mailboxes:[
    {mailbox_id:"support",provider:"graph", account_identifier:"support@example.test", auth_secret_refs:{tenant_id:"MAIL_TENANT", client_id:"MAIL_CLIENT", client_secret:"MAIL_SECRET"}, folder_mapping:{inbox:"Inbox"}, folder_allowlist_defaults:["inbox"]},
    {mailbox_id:"operations",provider:"gmail", account_identifier:"ops@example.test", auth_secret_refs:{access_token:"MAIL_TOKEN"}},
  ]};
  let revision = 1;
  const snapshot = () => ({key:tab.key, title:tab.title, revision:`r${revision}`, raw:{exists:true,config}, effective:{config,validation_diagnostics:[],errors:[],payload:{}}, parent_defaults:{directory_path:directoryPath === "root" ? null : "root",config:{targets:directoryPath === "root" ? [] : [{id:"inherited-alerts",provider:"teams",kind:"teams",address:"team:parent/channel:alerts",policy:{capabilities:["notify"]}}]},payload:{}}});
  const session = {auth_mode:"none",auth_required:false,authenticated:true,username:"admin",display_name:"Admin",can_manage_users:true,permissions:["admin.owner"],effective_permissions:["admin.owner"],session_id:"fixture",available_login_methods:[],azure_sso_login_url:null,privileged_operations:{unlocked:true}};
  const server = await createServer({root,server:{host:"127.0.0.1",port},logLevel:"error",plugins:[{
    name:"admin-collection-fixture", configureServer(server) {
      server.middlewares.use(async (req,res,next) => {
        const url = new URL(req.url, "http://localhost");
        if (!url.pathname.startsWith("/api/")) return next();
        if (url.pathname.startsWith("/api/admin/branding/assets/")) {
          const filename = path.basename(url.pathname);
          if (["evander-logo-light.png", "evander-logo-dark.png"].includes(filename)) {
            res.setHeader("Content-Type", "image/png");
            return res.end(await readFile(path.join(root, "branding", filename)));
          }
        }
        let data = {};
        if (url.pathname === "/api/auth/session") data = session;
        else if (url.pathname === "/api/admin/state") data = {...session,selected_directory_path:directoryPath,directory_scoped_config_enabled:writable,execution_backend:"libvirt_vm",assistant:{enabled:false,show_launcher_when_disabled:false}};
        else if (url.pathname === "/api/configuration/state") data = {
          directory_scoped_config_enabled:writable,selected_directory_path:directoryPath,selected_directory:{path:directoryPath,parent_path:directoryPath === "root" ? null : "root",can_create:false,can_delete:false},
          tree:{ok:true,data:{directory:{id:"root",name:"root",path:"root",parent_id:null,metadata:{}},config_domains:[],children:[]},errors:[],validation_diagnostics:[]},
          tabs:[tab],tab_snapshots:{[tab.key]:snapshot()},directory_overrides:{directory_path:"root",exists:true,domain_count:3,field_count:3,overrides:{},errors:[],payload:{}},
        };
        else if (url.pathname === "/api/admin/user-notifications") data = {unread_count:0,notifications:[]};
        else if (url.pathname === "/api/admin/deployment-status") data = {active:false};
        else if (url.pathname === "/api/configuration/model-pricing") data = {status:"ok",version:"1",rules:[],sources:[],catalog:{version:"1",rules:[]},editable:false};
        else if (url.pathname === "/api/teams-auth/status") data = {state:"authenticated",directory_path:"root",backend:"fixture"};
        else if (url.pathname.startsWith("/api/configuration/domains/") && req.method === "PUT") {
          let body = ""; for await (const chunk of req) body += chunk;
          const payload = JSON.parse(body); writes.push(payload);
          if (failSave) {res.statusCode=409;data={error:"Concurrent configuration change"};}
          else {
            // DirectoryConfigStore serializes object keys in sorted order before readback.
            config={...payload.config, contacts:Object.fromEntries(Object.entries(payload.config.contacts ?? {}).sort(([left], [right]) => left.localeCompare(right)))};
            revision++;
            data={write_result:{ok:true,errors:[],validation_diagnostics:[]},tab_snapshot:snapshot()};
          }
        }
        res.setHeader("Content-Type","application/json");res.end(JSON.stringify(data));
      });
    },
  }]});
  await server.listen();
  return {server,writes,setFailSave:value=>{failSave=value;},url:`http://127.0.0.1:${server.httpServer.address().port}`};
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const fixture = await collectionsFixture({port:Number(process.env.FIXTURE_PORT || 5177), ...(process.env.FIXTURE_ROOT ? {root:process.env.FIXTURE_ROOT} : {})});
  console.log(fixture.url);
}
