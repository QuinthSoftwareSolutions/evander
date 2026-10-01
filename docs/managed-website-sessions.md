# Managed website sessions

Enabled add-ons can supply additional login adapters. All adapters use the same
session proof, target origin, directory policy and broker expiry checks. Their
configuration and credential bindings are owned by the add-on; see the
[WordPress adapter](../packages/wordpress-addon/docs/operations.md#browser-and-builder-editing).
Generic JSON profiles can map temporary cookies with `cookies_response_pointer`;
exported cookies must be secure, scoped to the granted host and expire within the
broker's lifetime.

An operator can grant an assistant authenticated browser access to a website without
putting the website password in its VM or container. The existing HTTP target relay
logs in on the host and returns selected temporary browser storage. This initial
JSON adapter supports HTTPS POST logins with JSON responses and temporary storage/cookies;
interactive SSO and MFA need a different adapter.

In Admin Web, select the directory and open **Configuration → Access, approvals &
audit → Website logins**. Directory owners can inspect profiles; editing, credential
changes, and login tests require unlocked privileged operations.

1. Choose **New website login**, enter a profile ID, website origin, and login endpoint.
2. Select **Encrypted host storage**, define the login request fields, and enter their
   values. Only public identifiers such as a username should be marked as public.
3. Add browser storage mappings and configure the expiry source and renewal margin.
   Additional nonsecret request values can be entered under **Fixed login fields**.
4. Save, then choose **Test saved login**. The UI shows the test result and time;
   temporary tokens stay on the host during this test.
5. In **Sessions → Policy → HTTP targets**, add the website and select the profile in
   **Managed website login**. The same selector is available during agent creation.
   Include any separate login/API origins as additional HTTP targets. Provision the
   assistant with Playwright and use the managed context helper below.

Saved credential values are never returned to the UI. Leave a configured field blank
to keep its value, or enter a replacement and save to rotate it. Failed saves retain
the entered form values. **Remove saved credentials** disables subsequent authenticated
browser requests until credentials are supplied again. **Remove local profile**
removes this directory's override; a parent profile may become effective again.
Disable a profile locally to prevent its use in that directory instead.

Profiles inherit into child directories, but do not cross into siblings. The editor
labels inherited profiles; saving one replaces that profile in the child, including
its credential fields and storage mappings. Other parent profiles remain inherited.
Profiles are resolved from current saved layers on every request. Deleting a root
profile revokes further authentication, including cached state, without a restart.
Writes use revision
checks, preserving concurrent changes. A changed login destination, request fields,
or public-identifier policy requires reentering credentials so an existing password
cannot silently be redirected to another login endpoint.

The credential store uses authenticated encryption with a host-only key at
`runtime/secrets/website-credentials.key` and a private database at
`runtime/website-credentials/credentials.db`, both mode `0600`. Back up these together
using the host's protected backup process. Configuration contains an opaque credential
reference, never the entered values. Credential changes invalidate cached state on
the next request; they need no service restart. Tests persist only safe result/expiry
metadata and have a 60-second cooldown.

The `managed_website_sessions` map remains available through the directory provider
configuration API. Existing environment references are also supported by selecting
**Existing host environment variables** in the UI. These values remain managed by
the host environment and require a matching entry in the host-controlled JSON array
`EVANDER_MANAGED_WEBSITE_CREDENTIAL_ENV_ALLOWLIST`. Each entry binds its environment names
to one directory subtree, profile ID, request-field mapping, browser origin, and exact login URL.
This prevents a directory owner from reusing an approved credential for a sibling directory or
redirecting it to another destination. Switch to encrypted storage for credential management in
the UI.
For example, an environment-backed configuration is:

```json
{
  "managed_website_sessions": {
    "training-app": {
      "browser_origin": "https://training.example.org",
      "login_url": "https://training-api.example.org/auth/login",
      "credential_fields": {
        "email": "TRAINING_LOGIN_USER",
        "password": "TRAINING_LOGIN_PASSWORD"
      },
      "public_credential_fields": ["email"],
      "static_fields": {"tenant": "training"},
      "local_storage": {
        "currentUser": {"response_pointer": "", "encoding": "json"}
      },
      "jwt_response_pointer": "/token",
      "max_age_seconds": 28800,
      "renew_before_seconds": 60
    }
  }
}
```

The matching host grant is a single-line JSON value in `config/http-target-relay.env` (copied
from `config/http-target-relay.env.example`) or an equivalent service-manager secret source:

```bash
EVANDER_MANAGED_WEBSITE_CREDENTIAL_ENV_ALLOWLIST='[{"directory_path":"root","profile_id":"training-app","browser_origin":"https://training.example.org","login_url":"https://training-api.example.org/auth/login","credential_fields":{"email":"TRAINING_LOGIN_USER","password":"TRAINING_LOGIN_PASSWORD"},"public_credential_fields":["email"],"static_fields":{"tenant":"training"}}]'
```

The grant applies to its named directory and descendants, matching normal profile inheritance.
Changing the profile ID, browser origin, login URL, credential fields, public fields, or static
fields requires a new host grant. Systemd loads `config/http-target-relay.env` into both Admin Web
and the HTTP target relay so saving, testing, and session acquisition enforce the same grants.
Keep the actual credential values in that ignored host-local file, repo-root `secrets.env`, or an
equivalent service-manager secret source loaded by both services.

With `credential_source: "environment"` (the backward-compatible default),
`credential_fields` maps JSON request fields to host environment variables. With
`credential_source: "stored"`, Admin Web stores the configured field values and sets
`credential_ref`; callers cannot choose another profile's stored credential reference. Only
mark identifiers the application reflects, such as email, in `public_credential_fields`;
never mark passwords. `static_fields` supplies nonsecret fixed JSON values. Missing
credentials fail closed. Passwords reflected in selected response values are rejected.

`local_storage` and `session_storage` map browser storage keys to RFC 6901 response
pointers. An empty pointer selects the whole response; `/result/accessToken` selects
a nested field, and `~1`/`~0` escape slash/tilde. `encoding: "json"` serializes the value;
`"string"` requires and copies a JSON string. Select only the temporary authentication
state the application needs. Returned state is sensitive, even though it is temporary.

Expiry is capped by `max_age_seconds` (60–86400; default 3600) and the assistant
session's own expiry. Optionally choose either `jwt_response_pointer` to read a JWT's
`exp`, or `expires_in_response_pointer` to read a lifetime in seconds. JWT parsing is
only a cache-expiry hint; it does not validate the token's signature. The website
still enforces authentication. Without an expiry source, configure the age below the
site's real lifetime. `renew_before_seconds` defaults to 60, and the maximum age must
exceed twice this margin. Login timeouts default to 20 seconds (maximum 60), covering
DNS resolution, connection/TLS, response headers and the complete response body.
The deadline cancels slow or trickling responses and closes the connection. Duplicate
requests for the same session/target share a login; other sessions and targets can
continue independently. Response limits default to 256 KiB (maximum 1 MiB).

The UI's target binding corresponds to this supported session policy/metadata API
payload, including the login endpoint's origin:

```json
{
  "http_targets": {
    "targets": {
      "training-ui": {
        "base_url": "https://training.example.org",
        "managed_website_session": "training-app"
      },
      "training-api": {"base_url": "https://training-api.example.org"}
    }
  }
}
```

Browsing permission alone does not grant authentication. The browser origin must
exactly match the bound target's scheme, host, and effective port. Login is restricted
to configured public HTTPS destinations, with DNS resolution pinned to approved
addresses. Redirects, private addresses, and metadata endpoints are rejected.

Provision with `playwright=true` and use the shared helper in either execution backend:

```js
const { launchSeededChromium, createManagedWebsiteContext } =
  await import(process.env.EVANDER_PLAYWRIGHT_LAUNCH_HELPER);
const browser = await launchSeededChromium(chromium, { headless: true });
try {
  const context = await createManagedWebsiteContext(browser, "training-ui");
  const page = await context.newPage();
  await page.goto("https://training.example.org/processes");
  await page.screenshot({ path: "/artifacts/processes.png" });
} finally {
  await browser.close();
}
```

The helper calls `POST /session/<id>/website-session/<alias>` on port 11777 with
`x-evander-mcp-session-token` proof. The route always requires valid proof, including
for legacy sessions, and returns `Cache-Control: no-store`. Proof is accepted only in
that header. The browser proxy on port 11778 continues to enforce the existing
website allowlist. No new service, port, or timer is needed.

Each new context requests current state. The broker caches it only in host memory
and renews it when near expiry; idle assistants cause no repeated logins. A restart
causes a fresh login on next use. An already-open context retains the site's normal
expiry: create another managed context after expiry. Early server-side revocation
is not detected by this TTL cache; an operator can invalidate the profile/credentials
or restart the relay. Failed renewals never return stale state and have a 60-second
retry cooldown. Check credentials, profile, grants, and site availability if the
helper reports authentication unavailable; do not print tokens or login responses.

Removing a binding/profile, expiring the assistant session, or rotating its proof
prevents subsequent broker requests. It cannot retract tokens already issued by the
website; revoke those at the website if immediate token revocation is needed.
Website account permissions govern available actions; read-only access requires an
appropriately restricted website account as well as assistant instructions.
