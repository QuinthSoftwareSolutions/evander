import assert from "node:assert/strict";
import test from "node:test";
import { waitForCondition } from "./support/polling.mjs";

test("browser polling waits for an asynchronous condition to become true", async () => {
  let attempts = 0;
  await waitForCondition(async () => ++attempts === 3, "condition never became true", { intervalMs: 1 });
  assert.equal(attempts, 3);
});

test("browser polling rejects when an asynchronous condition remains false", async () => {
  await assert.rejects(waitForCondition(async () => false, "condition never became true", { timeoutMs: 0 }),
    /condition never became true/);
});

test("browser polling propagates predicate failures", async () => {
  const failure = new Error("page evaluation failed");
  await assert.rejects(waitForCondition(async () => { throw failure; }, "condition never became true"),
    error => error === failure);
});
