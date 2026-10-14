import { createServer } from "vite";
import { fileURLToPath } from "node:url";

// Synthetic endpoints exercise the editor's requests without logging into a website.
export async function websiteLoginFixture(options = {}) {
  const state = {profiles: [], revision: "profile-1", sites: {}, writes: [], tests: [], failSave: false, unavailable: false, ...options};
  const server = await createServer({
    root: fileURLToPath(new URL("../..", import.meta.url)), logLevel: "error", server: {host: "127.0.0.1", port: 0},
    plugins: [{name: "website-login-wizard-fixture", configureServer(vite) {
      vite.middlewares.use(async (request, response, next) => {
        const url = new URL(request.url, "http://fixture.test");
        const json = (value, status = 200) => {response.statusCode = status; response.setHeader("Content-Type", "application/json"); response.end(JSON.stringify(value));};
        if (url.pathname === "/api/addons/wordpress-addon/settings") {
          return state.unavailable ? json({error: "Add-on unavailable"}, 404) : json({revision: 1, settings: {sites: state.sites}});
        }
        if (url.pathname.startsWith("/api/managed-website-sessions")) {
          const directory = url.searchParams.get("directory_path");
          const snapshot = () => ({directory_path: directory, revision: state.revision, profiles: state.profiles});
          if (request.method === "GET") return json(snapshot());
          const chunks = [];
          for await (const chunk of request) chunks.push(chunk);
          const body = JSON.parse(Buffer.concat(chunks).toString());
          if (request.method === "PUT") {
            state.writes.push({directory, path: url.pathname, body});
            if (state.failSave) {state.failSave = false; return json({error: "Website configuration changed. Reload before saving."}, 409);}
            const id = decodeURIComponent(url.pathname.split("/").at(-1));
            const profile = {id, profile: body.profile, local: true,
              credentials: {configured_fields: Object.keys(body.profile.credential_fields), managed_by: body.profile.adapter === "wordpress" ? "wordpress" : undefined,
                updated_at: 1, owner_directory: directory}, last_test: null};
            state.profiles = [...state.profiles.filter(value => value.id !== id), profile];
            state.revision += "-saved";
          } else if (request.method === "POST") {
            state.tests.push({directory, path: url.pathname, body});
            const id = decodeURIComponent(url.pathname.split("/").at(-2));
            const profile = state.profiles.find(value => value.id === id);
            profile.last_test = {status: profile.profile.adapter === "wordpress" ? "adapter_configured" : "ok", checked_at: 1, expires_at: null};
          }
          return json(snapshot());
        }
        if (url.pathname !== "/website-login-wizard") return next();
        const html = await vite.transformIndexHtml(url.pathname, `<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head>
          <body><main style="max-width:1100px;margin:24px auto;padding:16px"><h1>Website logins</h1><div id="root"></div></main><script type="module">
          import React from 'react'; import {createRoot} from 'react-dom/client';
          import {WebsiteLoginsConfiguration} from '/src/features/configuration/WebsiteLoginsConfiguration.tsx';
          import '/src/styles.css'; import '/src/design/baseline.css'; import '/src/design/tokens.css';
          import '/src/design/controls.css'; import '/src/design/layout.css'; import '/src/design/collections.css';
          import '/src/design/workspaces.css'; import '/src/design/surfaces.css';
          document.documentElement.dataset.adminDesign = '';
          function Fixture() {
            const [directory,setDirectory] = React.useState('root/company');
            const [disabled,setDisabled] = React.useState(false);
            return React.createElement(React.Fragment,null,
              React.createElement('button',{onClick:()=>setDirectory(directory==='root/company'?'root/other':'root/company')},'Switch directory'),
              React.createElement('button',{onClick:()=>setDisabled(!disabled)},'Toggle lock'),
              React.createElement(WebsiteLoginsConfiguration,{directoryPath:directory,disabled}));
          }
          createRoot(document.getElementById('root')).render(React.createElement(Fixture));
          </script></body></html>`);
        response.setHeader("Content-Type", "text/html"); response.end(html);
      });
    }}],
  });
  await server.listen();
  return {server, state, url: "http://127.0.0.1:" + server.httpServer.address().port + "/website-login-wizard"};
}
