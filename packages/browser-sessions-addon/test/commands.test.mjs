import test from "node:test";
import assert from "node:assert/strict";
import {commandAuthority} from "../dist/commands.mjs";

function fixture(controller="agent:agent-a") {
  let state={id:"browser-test",controller,control_generation:0,allowed_origins:["https://test.example.test"],expires_at:Date.now()/1000+60};
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

for (const controller of ["agent:agent-a", "user:other", null]) {
  test(`viewers can select shared pages while control belongs to ${controller}`,async()=>{
    const f=fixture(controller), actor="user:viewer";
    for (const action of ["pages", "select_page"]) {
      assert.equal(await f.authority.capture({action,generation:0},actor)(async()=>"viewed"),"viewed");
    }
    for (const action of ["navigate", "click", "type", "authenticate"]) {
      let applied=false;
      await assert.rejects(f.authority.capture({action,generation:0},actor)(async()=>{applied=true;}),/control_required/);
      assert.equal(applied,false,"Viewing pages must not grant browser input");
    }
    const queued=f.authority.capture({action:"select_page",generation:0},actor);
    f.handover();await f.authority.fence();
    let selected=false;
    await assert.rejects(queued(async()=>{selected=true;}),/stale_control/);
    assert.equal(selected,false,"Page selection must retain the control-generation fence");
  });
}
