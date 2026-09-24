# Admin Web test lanes

- `npm run test:unit` runs the `.test.mjs` files without launching a browser.
- `npm run test:browser` runs the `.browser.mjs` integration files.
- `npm test` runs both lanes, preserving the complete historical default selection.
- `npm run test:smoke` runs every `.smoke.mjs` browser journey.
- `npm run test:all` runs all three lanes, including the optional smoke collection.

The UI rollout smoke tests use the packaged New and Classic applications. In a
fresh checkout, run `npm run build` first to generate those local assets.

Each lane has a per-test deadline. For a broad local run, also bound the whole
command, for example `timeout --kill-after=10s 600s npm run test:all`. A timeout,
failed assertion, browser launch failure, or server startup failure is a failed
run; it must not silently skip coverage. Run browser suites where loopback
servers and Chromium are available.

Navigation tests share their Vite server and Chromium process within one file.
Team smoke tests share Chromium, while their mutable fixture server and state
remain per case. Every browser case creates and closes its own context so cookies,
storage, API routes, pages, and permission state do not carry between cases.
Cleanup hooks are registered immediately after acquisition and have their own
deadlines. Keep startup-specific server/browser tests independent.
