import test from "node:test";
import assert from "node:assert/strict";
import {once} from "node:events";
import {WebSocket, WebSocketServer} from "ws";
import {controllerConnection, proxyAuthentication} from "../dist/connection.mjs";

const proxy = {server: "http://proxy.example.test:11778", username: "browser-one", password: "delegated-proof"};
function challenge(filter, {sessionId="page-a", requestId="request-one", source="Proxy", origin=proxy.server} = {}) {
  filter.received({method: "Fetch.authRequired", sessionId, params: {requestId, authChallenge: {source, origin}}});
}
function response(filter, {sessionId="page-a", requestId="request-one", response="Default"} = {}) {
  return filter.sending({id: 17, method: "Fetch.continueWithAuth", sessionId, params: {requestId, authChallengeResponse: {response}}});
}

test("controller supplies delegated proof only for its configured proxy's default challenge", () => {
  const filter = proxyAuthentication(proxy);
  for (const other of [{source: "Server", origin: "https://app.example.test"}, {origin: "http://unrelated.example.test:11778"}, {origin: "invalid-origin"}]) {
    challenge(filter, other);
    assert.deepEqual(response(filter).params.authChallengeResponse, {response: "Default"});
  }
  // An unpaired request or another target cannot consume a proxy challenge.
  assert.deepEqual(response(filter).params.authChallengeResponse, {response: "Default"});
  challenge(filter);
  assert.deepEqual(response(filter, {sessionId: "page-b"}).params.authChallengeResponse, {response: "Default"});
  assert.deepEqual(response(filter).params.authChallengeResponse, {response: "ProvideCredentials", username: "browser-one", password: "delegated-proof"});
  // The same receipt cannot be answered a second time with credentials.
  assert.deepEqual(response(filter).params.authChallengeResponse, {response: "Default"});
});

test("failed proxy proof is cancelled without replacing an explicit client authentication decision", () => {
  const filter = proxyAuthentication(proxy);
  for (const decision of ["CancelAuth", "ProvideCredentials"]) {
    challenge(filter);
    assert.equal(response(filter, {response: decision}).params.authChallengeResponse.response, decision);
  }
  challenge(filter);
  assert.equal(response(filter).params.authChallengeResponse.response, "ProvideCredentials");
  challenge(filter);
  assert.deepEqual(response(filter).params.authChallengeResponse, {response: "CancelAuth"});
});

for (const method of ["Network.loadingFinished", "Network.loadingFailed", "Target.detachedFromTarget"]) {
  test(`${method} releases authentication state without clearing another target's request`, () => {
    const filter = proxyAuthentication(proxy);
    for (const sessionId of ["page-a", "page-b"]) {
      filter.received({method: "Fetch.requestPaused", sessionId, params: {requestId: "request-one", networkId: "network-one"}});
      challenge(filter, {sessionId});
      assert.equal(response(filter, {sessionId}).params.authChallengeResponse.response, "ProvideCredentials");
    }
    filter.received({method, sessionId: "page-a", params: method === "Target.detachedFromTarget" ? {sessionId: "page-a"} : {requestId: "network-one"}});
    for (const sessionId of ["page-a", "page-b"]) challenge(filter, {sessionId});
    assert.equal(response(filter).params.authChallengeResponse.response, "ProvideCredentials");
    assert.equal(response(filter, {sessionId: "page-b"}).params.authChallengeResponse.response, "CancelAuth");
  });
}

test("the private controller connection relays CDP and closes both transports", {timeout: 10000}, async () => {
  const upstream = new WebSocketServer({host: "127.0.0.1", port: 0});
  await once(upstream, "listening");
  let connection, client;
  try {
    const accepted = once(upstream, "connection");
    connection = await controllerConnection("ws://127.0.0.1:" + upstream.address().port, proxy);
    client = new WebSocket(connection.endpoint);
    await once(client, "open");
    const [chrome] = await accepted;
    const incoming = once(client, "message");
    chrome.send(JSON.stringify({method: "Fetch.authRequired", sessionId: "page-a", params: {requestId: "request-one", authChallenge: {source: "Proxy", origin: proxy.server}}}));
    const [event] = await incoming;
    assert.equal(JSON.parse(String(event)).method, "Fetch.authRequired");
    const outgoing = once(chrome, "message");
    client.send(JSON.stringify({id: 17, method: "Fetch.continueWithAuth", sessionId: "page-a", params: {requestId: "request-one", authChallengeResponse: {response: "Default"}}}));
    const [raw] = await outgoing;
    assert.deepEqual(JSON.parse(String(raw)).params.authChallengeResponse, {response: "ProvideCredentials", username: "browser-one", password: "delegated-proof"});
    const clientClosed = once(client, "close"), chromeClosed = once(chrome, "close");
    await connection.close();
    await Promise.all([clientClosed, chromeClosed]);
    connection = undefined;
  } finally {
    client?.terminate();
    await connection?.close();
    for (const socket of upstream.clients) socket.terminate();
    await new Promise(resolve => upstream.close(resolve));
  }
});
