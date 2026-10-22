import test from "node:test";
import assert from "node:assert/strict";
import {commandAuthority} from "../dist/commands.mjs";

function fixture() {
  let state={id:"browser-test",controller:"agent:agent-a",control_generation:0,allowed_origins:["https://test.example.test"],expires_at:Date.now()/1000+60};
  const authority=commandAuthority(()=>state);
  return {authority,handover(){state={...state,controller:"user:operator",control_generation:1};}};
}

test("takeover rejects commands received before the fence even if they were queued",async()=>{
  const f=fixture();
  const queued=f.authority.capture({action:"authenticate",generation:0},"agent:agent-a");
  f.handover();await f.authority.fence();
  let changed=false;
  await assert.rejects(queued(async()=>{changed=true;}),/stale_control/);
  assert.equal(changed,false);
  assert.equal(await f.authority.capture({action:"select_page",generation:1},"user:operator")(async()=>"selected"),"selected");
});

test("takeover aborts a pending login and drains dispatched browser work before acknowledging",async()=>{
  const f=fixture();
  let release,signal,started;
  const entered=new Promise(resolve=>{started=resolve;});
  const browserWork=new Promise(resolve=>{release=resolve;});
  let changed=false,cleaned=false,fenced=false;
  const pending=f.authority.capture({action:"authenticate",generation:0},"agent:agent-a")(async(guard,abort)=>{
    signal=abort;started();
    try {await browserWork;guard();changed=true;}
    finally {cleaned=true;}
  });
  const rejected=assert.rejects(pending,/stale_control/);
  await entered;f.handover();
  const fencing=f.authority.fence().then(()=>{fenced=true;});
  await Promise.resolve();
  assert.equal(signal.aborted,true);assert.equal(fenced,false);
  release();await rejected;await fencing;
  assert.equal(cleaned,true);assert.equal(changed,false);assert.equal(fenced,true);
});

test("page selection requires the current controller while page enumeration remains readable",async()=>{
  const f=fixture();f.handover();await f.authority.fence();
  await assert.rejects(f.authority.capture({action:"select_page",generation:1},"agent:agent-a")(async()=>{}),/control_required/);
  assert.equal(await f.authority.capture({action:"pages",generation:1},"agent:agent-a")(async()=>"pages"),"pages");
});
