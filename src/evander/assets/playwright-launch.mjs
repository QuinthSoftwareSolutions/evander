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
