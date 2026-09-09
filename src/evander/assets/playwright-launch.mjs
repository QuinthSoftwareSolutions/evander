export function seededChromiumLaunchOptions(options = {}) {
  if (options === null || typeof options !== "object" || Array.isArray(options)) {
    throw new TypeError("Playwright launch options must be an object.");
  }
  const executablePath = process.env.PLAYWRIGHT_MCP_EXECUTABLE_PATH;
  if (!executablePath) {
    throw new Error(
      "PLAYWRIGHT_MCP_EXECUTABLE_PATH is unavailable. Reprovision this session with playwright=true.",
    );
  }

  const proxyServer = process.env.EVANDER_HTTP_TARGET_BROWSER_PROXY_URL;
  let proxy;
  if (proxyServer) {
    if (Object.hasOwn(options, "proxy")) {
      throw new Error("A caller-supplied Playwright proxy cannot replace the Evander session proxy.");
    }
    const username = process.env.EVANDER_SESSION_ID;
    const password = process.env.EVANDER_MCP_SESSION_TOKEN;
    if (!username || !password) {
      throw new Error("The Evander browser proxy requires session credentials.");
    }
    proxy = { server: proxyServer, username, password };
  }

  return { ...options, ...(proxy ? { proxy } : {}), executablePath };
}

export function launchSeededChromium(chromium, options = {}) {
  if (!chromium || typeof chromium.launch !== "function") {
    throw new TypeError("Pass the chromium export from the repository's Playwright package.");
  }
  return chromium.launch(seededChromiumLaunchOptions(options));
}

// The broker renews expired website sessions using host-held credentials. Call
// this for each new context; an already-open page still has the site's normal TTL.
export async function createManagedWebsiteContext(browser, targetAlias, options = {}) {
  if (!browser || typeof browser.newContext !== "function") {
    throw new TypeError("Pass a Playwright browser instance.");
  }
  if (!options || typeof options !== "object" || Array.isArray(options)
      || Object.hasOwn(options, "storageState")) {
    throw new TypeError("Managed context options must not override storageState.");
  }
  if (typeof targetAlias !== "string" || !/^[a-zA-Z0-9][a-zA-Z0-9_.-]*$/.test(targetAlias)) {
    throw new TypeError("Pass an explicitly granted HTTP target alias.");
  }
  const base = process.env.HTTP_TARGET_RELAY_BASE_URL;
  const sid = process.env.EVANDER_SESSION_ID;
  const proof = process.env.EVANDER_MCP_SESSION_TOKEN;
  if (!base || !sid || !proof) {
    throw new Error("Managed website access requires the session relay configuration.");
  }
  const endpoint = new URL(base);
  // Existing bootstrap URLs may carry /auth/<proof>. Use header proof here so
  // this new endpoint never places credentials in URLs or access logs.
  endpoint.pathname = endpoint.pathname.replace(/\/auth\/[^/]+\/?$/, "").replace(/\/$/, "")
    + `/session/${encodeURIComponent(sid)}/website-session/${encodeURIComponent(targetAlias)}`;
  endpoint.search = "";
  endpoint.hash = "";
  let state;
  try {
    const response = await fetch(endpoint, {
      method: "POST", redirect: "error", cache: "no-store",
      headers: { "x-evander-mcp-session-token": proof },
      signal: AbortSignal.timeout(90000),
    });
    if (!response.ok) {
      await response.body?.cancel();
      throw new Error("broker rejected request");
    }
    const chunks = [];
    let size = 0;
    for await (const chunk of response.body) {
      size += chunk.byteLength;
      if (size > 2097152) throw new Error("broker response too large");
      chunks.push(chunk);
    }
    state = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    if (!Number.isFinite(state.expiresAt) || state.expiresAt * 1000 <= Date.now()
        || !state.origin || !state.storageState || !state.sessionStorage) {
      throw new Error("invalid broker state");
    }
  } catch {
    throw new Error("Managed website authentication unavailable. Ask the operator to check the grant and host login profile.");
  }
  const context = await browser.newContext({ ...options, storageState: state.storageState });
  try {
    await context.addInitScript(({ origin, values }) => {
      if (window.location.origin === origin) {
        for (const [key, value] of Object.entries(values)) window.sessionStorage.setItem(key, value);
      }
    }, { origin: state.origin, values: state.sessionStorage });
    return context;
  } catch {
    await context.close();
    throw new Error("Could not initialize the managed website context.");
  }
}
