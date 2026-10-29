import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { once } from "node:events";
export async function until(check, label, ms=15000) {
  const end = Date.now()+ms;
  while (Date.now()<end) {
    const result = await check();
    if (result) return result;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error("Timed out: " + label);
}
export async function fixture() {
  const folder = await mkdtemp(join(tmpdir(), "evander-browser-"));
  const child = spawn(process.env.EVANDER_TEST_PYTHON ?? "python3",
    ["test/server.py", folder], { stdio:["ignore", "pipe", "pipe"] });
  let logs = ""; child.stderr.on("data", data => logs += data);
  const first = await until(async () => {
    const chunk = child.stdout.read();
    if (!chunk) return null;
    return JSON.parse(String(chunk).trim().split("\n")[0]);
  }, "integration fixture");
  await until(async () => {
    if (child.exitCode !== null) throw new Error("Fixture exited during startup: " + logs);
    try {
      return (await fetch(first.base + "/healthz", {signal:AbortSignal.timeout(1000)})).ok;
    } catch { return false; }
  }, "integration fixture readiness");
  const api = async (path, body) => {
    const response = await fetch(first.base+path, { method: body ? "POST" : "GET",
      headers: body ? {"Content-Type":"application/json"} : {}, body: body ? JSON.stringify(body) : undefined, signal:AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error("Fixture API " + response.status + " " + await response.text());
    return response.json();
  };
  return { folder, base:first.base, api, stop:async()=>{
    child.kill("SIGTERM");
    if(child.exitCode===null && child.signalCode===null) await Promise.race([once(child,"exit"),new Promise(resolve=>setTimeout(resolve,3000))]);
    if(child.exitCode===null && child.signalCode===null){child.kill("SIGKILL");await once(child,"exit");}
    await rm(folder,{recursive:true,force:true});
    assert.equal(logs, "", "fixture should have no server errors");
  }};
}
