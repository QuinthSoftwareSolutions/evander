export {};
declare const chrome: any;
const $ = (id: string) => document.getElementById(id)!;
let after = 0;
async function call(message: any) {
  const result = await chrome.runtime.sendMessage(message);
  if (!result.ok) throw new Error(result.error);
  return result;
}
function failure(error: any) { $("error").textContent = error.message; }
async function refresh() {
  const result = await call({ kind: "state", after });
  $("pairing").hidden = !!result.session;
  $("session").hidden = !result.session;
  if (!result.session) return;
  $("title").textContent = result.session.title;
  $("shared-origin").textContent = "Sharing " + (result.session.current_url ? new URL(result.session.current_url).origin : "the selected tab");
  $("status").textContent = !result.connected ? "Disconnected — agent control is paused" :
    result.granted ? "Truman may interact with this tab" : "Observe only — you control this tab";
  for (const message of result.messages ?? []) {
    const item = document.createElement("p");
    item.textContent = (message.actor.startsWith("agent:") ? "Truman: " : "You: ") + message.body;
    $("messages").append(item); after = Math.max(after, message.seq);
  }
}
$("pair").addEventListener("click", async () => {
  try {
    $("error").textContent = "";
    const invitation = ($("invitation") as HTMLInputElement).value;
    const url = new URL(invitation);
    const granted = await chrome.permissions.request({ origins: [url.origin + "/*"] });
    if (!granted) throw new Error("Connection permission was not granted");
    await call({ kind: "pair", invitation }); await refresh();
  } catch (error) { failure(error); }
});
for (const [button, action] of [["grant", "grant"], ["pause", "pause"]])
  $(button).addEventListener("click", () => void call({ kind: "control", action }).then(refresh).catch(failure));
$("voice").addEventListener("click", () => void call({ kind: "voice" }).catch(failure));
$("origin").addEventListener("click", () => void call({ kind: "origin" }).then(refresh).catch(failure));
$("disconnect").addEventListener("click", () => void call({ kind: "disconnect" }).then(() => {
  after = 0; $("messages").replaceChildren(); return refresh();
}).catch(failure));
$("chat").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const body = $("body") as HTMLTextAreaElement;
    await call({ kind: "message", body: body.value }); body.value = ""; await refresh();
  } catch (error) { failure(error); }
});
setInterval(() => void refresh().catch(failure), 2000);
void refresh().catch(failure);
