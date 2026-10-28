/** Keep the controller's navigation routes without letting its default CDP
 * context cancel proxy authentication for contexts owned by another client.
 */
import {once} from "node:events";
import {WebSocketServer} from "ws";

type Proxy = {server: string; username?: string; password?: string};

export function proxyAuthentication(proxy: Proxy) {
  const challenges = new Set<string>(), attempted = new Set<string>();
  const requests = new Map<string, Set<string>>();
  const key = (sessionId: string | undefined, id: string) => JSON.stringify([sessionId ?? "", id]);
  const cleanup = (network: string) => {
    for (const id of requests.get(network) ?? []) { challenges.delete(id); attempted.delete(id); }
    requests.delete(network);
  };
  return {
    received(message: any) {
      const {method, params, sessionId} = message;
      if (method === "Fetch.requestPaused" && params.networkId) {
        const network = key(sessionId, params.networkId);
        let ids = requests.get(network);
        if (!ids) requests.set(network, ids = new Set());
        ids.add(key(sessionId, params.requestId));
      } else if (method === "Network.loadingFinished" || method === "Network.loadingFailed") {
        cleanup(key(sessionId, params.requestId));
      } else if (method === "Target.detachedFromTarget") {
        const detached = params.sessionId;
        for (const id of requests.keys()) if (JSON.parse(id)[0] === detached) cleanup(id);
        for (const id of challenges) if (JSON.parse(id)[0] === detached) challenges.delete(id);
        for (const id of attempted) if (JSON.parse(id)[0] === detached) attempted.delete(id);
      } else if (method === "Fetch.authRequired") {
        const id = key(sessionId, params.requestId);
        challenges.delete(id);
        if (params.authChallenge.source === "Proxy") {
          try {
            if (new URL(params.authChallenge.origin).origin === new URL(proxy.server).origin) challenges.add(id);
          } catch { /* Never answer an unrecognized challenge with proxy proof. */ }
        }
      }
    },
    sending(message: any) {
      if (message.method !== "Fetch.continueWithAuth") return message;
      const id = key(message.sessionId, message.params.requestId);
      const trusted = challenges.delete(id);
      if (!trusted || message.params.authChallengeResponse.response !== "Default" ||
          proxy.username === undefined || proxy.password === undefined) return message;
      const authChallengeResponse = attempted.has(id) ? {response: "CancelAuth"} :
        {response: "ProvideCredentials", username: proxy.username, password: proxy.password};
      attempted.add(id);
      return {...message, params: {...message.params, authChallengeResponse}};
    },
  };
}

export async function controllerConnection(endpoint: string, proxy?: Proxy) {
  if (proxy?.username === undefined || proxy.password === undefined) return {endpoint, close: async () => {}};
  // Only the controller uses this loopback endpoint. Agent connections still go
  // through the separately authorized, generation-fenced automation bridge.
  const path = "/" + crypto.randomUUID();
  const server = new WebSocketServer({host: "127.0.0.1", port: 0, path, maxPayload: 8 * 1024 * 1024});
  const upstreams = new Set<WebSocket>();
  server.on("connection", client => {
    const upstream = new WebSocket(endpoint);
    const auth = proxyAuthentication(proxy);
    const queued: string[] = [];
    upstreams.add(upstream);
    client.on("message", raw => {
      try {
        const message = JSON.stringify(auth.sending(JSON.parse(String(raw))));
        if (upstream.readyState === WebSocket.OPEN) upstream.send(message); else queued.push(message);
      } catch { client.close(1011, "browser_disconnected"); }
    });
    upstream.addEventListener("open", () => {
      if (client.readyState !== 1) { upstream.close(); return; }
      for (const message of queued) upstream.send(message);
      queued.length = 0;
    });
    upstream.addEventListener("message", event => {
      try {
        auth.received(JSON.parse(String(event.data)));
        if (client.readyState === 1) client.send(String(event.data));
      } catch { client.close(1011, "browser_disconnected"); }
    });
    upstream.addEventListener("close", () => { upstreams.delete(upstream); client.close(1011, "browser_disconnected"); });
    upstream.addEventListener("error", () => upstream.close());
    client.on("close", () => upstream.close());
    client.on("error", () => upstream.close());
  });
  await once(server, "listening");
  return {
    endpoint: "ws://127.0.0.1:" + (server.address() as {port: number}).port + path,
    close: async () => {
      for (const client of server.clients) client.terminate();
      for (const upstream of upstreams) upstream.close();
      await new Promise<void>(resolve => server.close(() => resolve()));
    },
  };
}
