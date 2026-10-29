# Browser-only sessions and Playwright

Hosted browsers now target approved external HTTP aliases or leased application
endpoint references. They do not accept workspaces, application images, launch
commands or database configuration. Application hosting belongs to the optional
[Application Environments add-on](../../application-environments-addon/README.md).
Neither add-on requires the other. See its [cutover guide](../../application-environments-addon/docs/operations.md)
before upgrading an existing combined deployment.

## External targets

Grant the owning agent its normal HTTP targets, including API/asset origins the
application needs. Create a browser with an approved browser profile:

```json
{
  "mode": "hosted",
  "request_id": "test-browser-1",
  "profile_id": "browser",
  "target_aliases": ["test-ui", "test-api"],
  "entry_url": "https://test.example.test/"
}
```

The worker needs a reachable `http_target_browser_proxy_url` (11778) and, for
managed login, `http_target_relay_url` (11777). It receives delegated browser proof
restricted to selected target aliases; the parent's MCP proof is not copied.
Raw external connections remain blocked even if a Playwright script changes its
proxy or navigation handlers. The viewer URL always requires existing Admin
sign-in and directory-scoped browser permission. It contains no connection secret.

For a managed website login, grant `browser_session_authenticate` explicitly and
call it with the selected alias and current control generation. It uses the
existing host-managed login profile; passwords and browser state are not returned
in the tool receipt. Playwright can instead use the existing
`createManagedWebsiteContext` helper, which obtains current authentication through
the owning job's normal broker configuration.

Both login paths use the browser's delegated target proxy. The hosted controller
keeps proxy authentication working for contexts created by another Playwright
connection, including after that connection detaches. The broker's successful
login response establishes the available website session; verify the signed-in
application page after navigating the managed context.

## Connecting test code

Enable `playwright_enabled` in both copies of the approved browser profile and
grant `browser_session_connect`. Observe the current page first; connect requires
that observation ID and control generation within 30 seconds. Each connection
consumes its observation; reconnecting requires another observation. Its result supplies a private
`config_path`, projected only into the current authorized worker job.

```js
const { connectSharedChromium, createManagedWebsiteContext } =
  await import(process.env.EVANDER_PLAYWRIGHT_LAUNCH_HELPER);
const browser = await connectSharedChromium(chromium, connection.config_path);
try {
  const context = await createManagedWebsiteContext(browser, "test-ui");
  const page = await context.newPage();
  await page.goto("https://test.example.test/");
  // Run the test against this browser. Humans see these same pages and login.
} finally {
  await browser.close(); // Detach this client; retain the shared browser.
}
```

This is Chromium CDP integration. Use the pinned Playwright/Chromium build. It
supports page-focused testing, managed contexts and screenshots; CDP does not
provide every native Playwright-protocol capability. After reconnection reacquire
existing pages by URL; pre-existing isolated contexts may appear through CDP's
default context rather than as separate context objects. Do not close contexts at
script exit if their pages should remain available for inspection. Close sharing
explicitly using `browser_session_close`.

Human takeover revokes the automation token and connection. Previously dispatched
actions can have completed; the fence waits for their acknowledgements before
human input is enabled. Pending managed login is cancelled, and dispatched typed
commands must settle before takeover is acknowledged. If the fence cannot be
confirmed, control stays paused. A
subsequent handback needs a fresh observation and explicit connection. Scripts
must treat a disconnection as interruption, never automatically replay a test
button or other side effect. Closing, pausing and losing the relay also revoke
script input. Browser contexts and website login survive ordinary disconnection.

The viewer's Page selector watches actual tabs; agent input follows its active tab.
Typed commands carry page identities and observation references. Existing local
Chrome sharing remains confined to the explicitly shared tab.

## CI output

The creation/status receipt's `viewer_url` is safe to print into the existing CI
log stream:

```js
console.log(`Shared browser: ${receipt.viewer_url}`);
```

Print no private connection descriptor, headers, tokens, cookies or login state.
Adding this line to operator-owned instructions or workflow output does not require
pipeline source changes. This release does not edit a live pipeline or workflow.
