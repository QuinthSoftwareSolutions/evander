# WordPress Add-on

The WordPress Add-on lets an authorized agent change a website's design, publish
directly, verify the result, and provide an undo receipt. It supports content and
blocks, templates, global styles, custom CSS, navigation/menu records, design
options, post/term metadata, ACF, covered builder screens, and custom theme/plugin
source through managed Git. No permanent staging site is required.

The add-on is generic across WordPress installations. Site-specific configuration
and evidence belong in operator storage, outside the repository. Enablement is
explicit; adding the package never connects to or modifies an existing website.

## Install and verify recovery

1. [Install this add-on project](../README.md#install) separately from Evander.
   Enable **WordPress Add-on** in Admin Web's **Add-ons** page and restart the
   listed control-plane services, including the HTTP target relay for browser use.
2. Open `/addons/wordpress-addon/` and download `evander-wordpress.zip`. Install and
   activate that companion plugin in WordPress. Use HTTPS and InnoDB for the
   WordPress tables and companion journal. The host and plugin use protocol v1.
3. Create a dedicated WordPress service account with the capabilities needed for
   the intended resources, including `edit_theme_options` for the companion API.
   New post HTML must satisfy WordPress KSES unless that account has
   `unfiltered_html`; unsafe proposals are rejected before publication. Conditional
   undo can still restore the exact captured baseline after that capability is removed.
   Create an application password and place it in a **managed Basic connector
   credential**, using the existing Credentials and Connectors admin pages. Approve
   its exact current consumer binding. The username/password stays on the host.
4. Configure a connector with driver `wordpress`, `config.base_url` equal to the
   site's HTTPS origin, and the six operations below. The following host-side
   snippet generates their complete JSON schemas; merge these into the connector
   definition in the directory's Connectors configuration:

   ```python
   import json
   from evander_wordpress_addon.models import operation_definitions
   print(json.dumps({name: operation.model_dump(mode="json")
                     for name, operation in operation_definitions().items()}, indent=2))
   ```

5. Install Node.js and a seeded Playwright/Chromium runtime on the control plane.
   Set `EVANDER_WORDPRESS_PLAYWRIGHT_MODULE` to its absolute `playwright/index.mjs`
   path for an installed wheel. Source-loaded packages use this project's `node_modules/`. Set
   `PLAYWRIGHT_MCP_EXECUTABLE_PATH` when Chromium is operator-provisioned elsewhere.
   Probes run as bounded subprocesses against approved public HTTPS origins.
   Chromium's sandbox must work for the service user. Verification fails closed if
   it cannot launch with the sandbox; run the opt-in browser fixture below under
   that user before enabling a site. Do not use an executable wrapper that disables
   the sandbox or run the verifier as root.
   Playwright must support [WebSocket routing](https://playwright.dev/docs/api/class-browsercontext#browser-context-route-web-socket)
   (v1.48 or later). Verification blocks WebSockets and service workers.
6. Save a disabled site profile in the correct directory. Example:

   ```json
   {
     "sites": {
       "main": {
         "enabled": false,
         "origin": "https://design.example.test",
         "connector_id": "website",
         "capabilities": ["cms"],
         "checks": [
           {"path": "/", "selector": "main", "links": ["/contact/"]},
           {"path": "/contact/", "form_selector": "form"}
         ]
       }
     }
   }
   ```

7. Use **Discover** to check adapters and **Test recovery** to exercise an actual
   CMS round trip inside a rolled-back transaction. Source-enabled sites also
   exercise their external release switch; browser-enabled sites require declared
   plugin coverage. Setup runs browser probes. A successful test records a stamp
   for the exact profile; API clients cannot forge that stamp. Then enable the site.
   Configuration changes invalidate the stamp and require another test.
8. Import `packages/wordpress-addon/examples/workflow-packs/wordpress.v1-example.json` through the supported
   workflow configuration UI/API. Set its graph's directory to the site's directory
   and keep the existing trigger/effect workers running. Pending operations retry
   independently of a chat turn. Confirm this graph is enabled before granting edits.
9. Grant the session `list_connectors`, `call_connector`, and the appropriate exact
   connector operations. Read-only grants can use `discover`, `inspect` and
   `change_status`; publication requires explicit write grants. Existing connector
   policy can require an additional approval before a mutation is queued.

Browser editing needs the relay to use the site's managed connector credentials.
The core installer keeps credential-key access unchanged for ordinary deployments.
For WordPress browser editing, explicitly install this add-on's relay drop-in after
initializing the existing credential store and running the core service installer:

```bash
sudo install -D -m 0644 \
  packages/wordpress-addon/examples/systemd/30-credential-store.conf \
  /etc/systemd/system/evander-http-target-relay.service.d/30-credential-store.conf
sudo systemctl daemon-reload
sudo systemctl restart evander-http-target-relay.service
```

This grants that host process access to `evander-credentials-key`; no key reaches
the browser. Remove the drop-in and restart the relay when withdrawing this access.
See [Credentials](../../../docs/credentials.md), [Connectors](../../../docs/connectors.md)
and [managed browser sessions](../../../docs/managed-website-sessions.md).

`origin` remains an HTTPS origin. For WordPress in a subdirectory, configure
`rest_path: "/wordpress/wp-json/"`. For plain permalinks use
`rest_path: "/wordpress/index.php?rest_route=/"` (or `/index.php?rest_route=/`
at the root). The browser profile's `login_url` must use that exact REST path.
Add required asset/CDN origins to `browser_origins`; browser probes block other
origins and pin approved DNS addresses, including when checking link destinations.

## Agent operations

Use the normal `list_connectors`/`call_connector` surface:

| Operation | Purpose |
| --- | --- |
| `discover` | Inspect supported adapters and recovery readiness for `site_id`. |
| `inspect` | Read a resource's value and current SHA-256 version. |
| `prepare_change` | Capture private recovery artifacts, baseline checks and pinned source; return a durable change ID. |
| `apply_change` | Queue publication of that change with its expected revision and an idempotency key. |
| `change_status` | Read its state, changed URLs, evidence and undo ID, or the site's history. |
| `restore_change` | Queue a conditional restore with an expected revision and idempotency key. |

Resource keys use `post:123`, `meta:123:_builder_data`,
`option:theme_mods_example`, `term:123`, `termmeta:123:field`, `acf:123:field`,
or `relationships:123:nav_menu`.
An inspected post returns its raw fields, so block markup is preserved. Post values
are patches of supported fields. Metadata values are arrays of individual stored
values. ACF values contain `value`, `reference` and `exists`, capturing both the
field value and its reference. Use `post:new-unique-name` to create a post; inspection
returns a null value and its version until publication assigns a real ID.
Publication and change-status receipts expose `resources["post:new-unique-name"]`
with `resolved_key` and the recorded `version`. Use that real key to inspect or
prepare subsequent edits; private snapshots and browser state remain on the host.
Option values contain `value` and `autoload`, so undo preserves the option's cache
metadata too. Relationship values are lists of `term_taxonomy_id` and `term_order`;
they cover one object's associations in the named taxonomy and preserve other
objects and language taxonomies. `nav_menu` is approved by default; operators may
extend `evander_design_taxonomies` for other design taxonomies. Menu associations
are available through CMS operations; browser screens that update extra taxonomy
records need explicit recovery adapters.

Templates, styles, navigation, menu items, custom CSS and media metadata are
WordPress records addressed using their post/meta IDs. Uploaded files, theme files,
compiled CSS/JavaScript and plugin code use the source/release path or a dedicated
resource adapter; the generic metadata adapter rejects `_wp_attached_file` and
secret-bearing metadata. Only approved design options are writable. The default
option allowlist contains the active theme's modifications, plus the private
restore probe; extend it with `evander_design_options`.

For example, prepare an edit after inspecting `post:123`:

```json
{
  "site_id": "main",
  "idempotency_key": "homepage-heading-1",
  "summary": "Update the homepage heading",
  "resources": [{
    "key": "post:123", "expected_version": "<version returned by inspect>",
    "value": {"post_title": "A new heading"}
  }]
}
```

Publication takes `site_id`, `change_id`, `expected_revision` and
`idempotency_key`. Repeat a request with the same key and payload to retrieve its
existing outcome. A different payload with an already-used key conflicts. Use the
receipt's current revision for a new action. Do not invent a new key to retry an
unknown remote mutation; the worker reconciles the existing operation first.

The plugin locks affected rows and compares versions in the transaction that
writes them. Undo checks the recorded result version and restores only those
resources. Human edits after publication produce a conflict instead of being
overwritten. Translation relationships and unrequested language records are
preserved; include each translated record explicitly when changing multiple languages.

## Browser and builder editing

Add `browser` to the profile capabilities and set `browser_target` to a granted
HTTP target alias with the same origin. Configure its existing managed website
profile as:

```json
{
  "enabled": true,
  "adapter": "wordpress",
  "adapter_config": {"site_id": "main"},
  "browser_origin": "https://design.example.test",
  "login_url": "https://design.example.test/wp-json/evander/v1/browser/ticket",
  "max_age_seconds": 900
}
```

Browser editing requires both `prepare_change` and `apply_change`, and the
`call_connector` tool, to permit writes without approval. Browser requests write
immediately, so a later publication approval cannot authorize them. Use queued
CMS changes when publication requires approval. Current session and directory
argument policies apply to browser access as well as background publication.

Prepare a change with `browser: true` and all resources that the screen can write.
Recovery is captured before the managed browser broker can issue a session. Use
the seeded `createManagedWebsiteContext(browser, targetAlias)` helper. After editing,
queue `apply_change`; publication closes the browser session, records its actual
resource versions, verifies the live pages, and returns the same kind of receipt.

When a screen needs more resources, call `prepare_change` with the same
`change_id`, its current `expected_revision`, a new `idempotency_key` and the
additional inspected resources. Their snapshots are saved durably before the
plugin adds them to browser coverage. Existing coverage is retained; an extension
cannot replace an already covered resource or change the pinned source/checks.
Do not edit the new resources until the receipt reports `ready` and `browser_ready`.
While coverage is pending, the original browser change continues to own the site
and its recovery data is retained. An operator can still undo it; expiration,
site disablement or revoked session authorization restores the existing edits
without requiring the extension to finish first. These recovery checks also apply
after browser changes are queued for publication or have an uncertain outcome.

The host exchanges its application password for a 60-second, single-use ticket.
The plugin redeems it into scoped, temporary, HttpOnly HTTPS cookies. Sessions last
at most 15 minutes and are also bounded by the agent and change TTLs. Cookies/nonces
are never stored in receipts or tool-call arguments. Completion, undo and expired
change recovery revoke the browser token; the broker refuses state for closed changes.
Renewal revokes the previous token and unredeemed tickets. Cached state rechecks
current tool/operation grants, connector configuration and host credential approval;
credential revisions invalidate the cache. Disabling a site prevents new editing
while preserving undo and scheduling recovery of open browser changes.
Protocol responses are checked before returning or caching them for raw credential
material and common JSON, URL and Base64 representations. Each check uses the
credential snapshot that authenticated the request, so rotation during a request
cannot allow the previous credential to be exported.

The plugin wraps browser writes in a transaction, locks the prepared resources,
checks their versions and rejects SQL outside the recovery manifest. File editors,
installers, general settings administration and account administration are denied.
Temporary sessions do not receive `manage_options`, including for approved builder
plugins. Use covered design screens or the CMS option adapter for allowlisted
settings; the All Settings page and general REST settings are unavailable.
Ticket redemption is rejected inside an existing scoped browser request.
Active plugins must be explicitly
declared compatible through `evander_browser_compatible_plugins`, including on
multisite installations. An operator must verify that each compatible plugin's
write paths are covered. A builder's custom tables/files need a dedicated adapter
or the source/release path; granting a cookie alone does not provide recovery.
For multisite browser editing, network-activate the companion so all blogs enforce
the blog/change marker on both admin and logged-in cookies. Each configured blog
needs its own journal tables; on an existing network initialize additional blogs
with operator WP-CLI, for example
`wp --url=https://design.example.test/child/ eval 'Evander_WordPress::install();'`.
Discovery and recovery tests must pass for each configured blog.

Implement `Evander_Resource_Adapter` and register it through
`evander_resource_adapters` to cover additional storage. An adapter must implement
discovery, inspection, validation, locking, apply/restore through `write`, and
browser SQL coverage through `allows_query`. File/storage adapters must supply their
own durable conditional recovery protocol; do not approve uncovered browser writes.

## Source builds and external deployment

Register the theme/plugin source through existing managed Git. Extend the site with
`source` capability and its managed repository, fixed component and deployment:

```json
{
  "source": {
    "managed_repo_id": "<registered repository ID>",
    "component": "theme", "source_path": ".",
    "required_checks": ["php-lint", "tests"]
  },
  "deployment": {"kind": "pipeline", "connector_id": "hosting"}
}
```

The change specifies `code.source_ref`, `commit_sha` and `tree_hash`. Managed Git
validates the exact snapshot, retains a private immutable ref, and exports a
source-only archive. A host-approved HTTPS builder runs configured builds, PHP lint
and relevant checks in `libvirt_vm` or `container_worker_vm`. Its result must attest
the exact SHA, backend, each required check and the built archive's SHA-256 digest.
The host verifies that digest and stores the artifact before deploying it. No code
arrives in MCP payloads and no build command runs in the control-plane process.

The selected hosting adapter is fixed for the prepared change. A lost reply triggers
status reconciliation; the engine never switches adapters or blindly republishes.

### HTTPS pipeline protocol

Use a host REST connector bound to the pipeline's HTTPS origin and managed auth.
REST connector definitions require at least one declared operation (for example a
read-only pipeline status operation); this add-on uses its fixed host protocol
endpoints internally and does not grant guests arbitrary build/deploy access.
Its fixed prefix defaults to `/evander-releases/v1`. Implement POST endpoints
`prepare`, `deploy`, `status`, `restore` and `probe`. Each response includes
`protocol_version: 1`. `operation_id` is durable and idempotent; a reused ID with a
different source/artifact conflicts. Return HTTP 409 for version conflicts.
When the hosting connector also performs the build, all build requests use
`deployment.prefix`, including resumed builds. A separate `source.build_connector_id`
uses the default build prefix.

For `prepare` with a source archive, the request contains commit/tree/source digest,
base64 ZIP, component, source path and required checks. Return the matching commit,
`execution_backend`, a boolean `checks` map, base64 `artifact_archive` and
`artifact_sha256`. Long builds may return `state: building`; repeated preparation
of the same operation reports its durable build result rather than starting another
build. Queued execution resumes this operation through the workflow worker.

For hosting `prepare`, return `previous_release` and `expected_release`, recording
both before building. Initial preparation reserves the operation and commit with a
null `artifact_sha256`; a later preparation binds the verified artifact to that
same commit and original baseline. It must never refresh that baseline after a
manual edit during the build. `deploy` receives the verified archive/digest, pinned SHA,
expected release and monotonic fencing token. Compare the current release and fence
under the same hosting lock used for the switch. Return `state: deployed`,
`release_id` and `release_version`. The version must detect manual file changes.

`status` reports the durable operation state and release version; `not_found` means
the operation provably never started. Ambiguous/in-progress states must not be
reported as `not_found`. `restore` conditionally restores `previous_release` only
when the candidate version still matches, and returns `state: restored` after
verifying the switch. Both endpoints must operate outside WordPress, including
when a deployed PHP error has broken REST and wp-admin. `probe` exercises the actual
restore switch against the current release and returns `restore_verified: true`.

Source archives are limited to 50 MiB and built archives to 100 MiB. HTTP calls have
bounded deadlines; a long build returns a durable pending state. Keep previous
releases until the host's recovery retention no longer needs them.

### Restricted SSH deployment

Set deployment to `{"kind":"ssh","profile_id":"design-site"}` and configure
`source.build_connector_id` for the isolated HTTPS builder described above. SSH
consumes its already-built artifact; it never evaluates an agent build command.

Provision a protected operator profile at
`/etc/evander/wordpress-ssh/design-site.json`, mode 0600, owned by root or the service
account, with `directory`, `site_id`, `origin`, `host`, `user`, `port`, absolute
`key_file` and `known_hosts_file`. Bind the profile to exactly one directory/site.
Use a dedicated key, pinned known-host entry, and a remote `authorized_keys` entry
with `restrict,command="/usr/local/bin/evander-wordpress-helper"`. Install the bundled
`ssh_helper.py` at that path and provision a private
`/etc/evander/wordpress-release.json` containing:

```json
{"components": {"theme": "/srv/design-site/theme"}}
```

Provision `releases/initial` and `current -> releases/initial` under the component
root; point WordPress's theme path at that `current` symlink. The helper validates
ZIP paths, sizes, digests and symlinks, records intents before switching, and
atomically updates `current`. It retains the previous release and verifies its
content hash before restore. Keys and paths never reach the guest. Do not configure
this key for arbitrary shell access. Reconcile interrupted switches through
`status`; PHP execution is not needed for recovery.
Artifact files use mode 0644 and every release directory uses 0755, regardless of
the SSH account's umask; the helper's state directory remains private. Provision
the component root and `releases/` so the web runtime can traverse them too.

When PHP's OPcache/realpath cache or a frontend cache retains old files, configure
`post_switch_command` as a fixed, absolute argv list in the protected helper JSON,
for example a narrowly authorized PHP-FPM reload/cache-purge helper. It runs without
a shell after deploy, restore and the recovery probe; the caller cannot supply or
change it. A failed activation remains a durable pending operation, readable through
`status`; undo switches back without first activating the failed candidate. The HTTPS
hosting adapter must likewise finish cache/runtime activation before reporting a
release as deployed or restored. Otherwise immediate verification could see an old
release while a newly deployed PHP error is waiting for cache expiry.

## Verification, retention and recovery

The ledger tracks prepared, queued, applying, verifying, published, restoring,
restored, conflict, uncertain, failed and recovery-required states. OS locks
serialize a physical site across processes/directories; persisted fencing tokens
reject stale remote owners. Configuration, credentials and current session grants
are rechecked before new writes. Private snapshots, pinned artifacts and step
state remain available after worker or host-process restarts.
Mixed source/CMS changes must still have an unexpired CMS preparation before code
deployment. If preparation expires during deployment or CMS application, recovery
restores the previous release and captured content. An already applied CMS request
remains eligible for reconciliation after expiration, including after a lost reply.

Checks cover affected URLs plus configured shared-template samples/navigation and
forms. Screenshots use 1280×800 and 390×844 viewports. Two probes ten seconds apart
must confirm a newly introduced objective server, rendering, navigation or required
form failure before automatic undo. Existing failures, cosmetic differences and
connectivity uncertainty do not trigger automatic undo. Form probes inspect controls
without submitting production data. Human review remains responsible for appearance.
Required navigation links must match the complete resolved site URL, including
origin, query and fragment; destination checks visit that matched link.

Source recovery runs before CMS recovery, restoring PHP availability when necessary.
Recovery itself is checked. Lost replies, pending restores and incomplete verification
remain `uncertain` and retry with the same change ID, retaining the site's reservation.
A conflict or confirmed failed recovery stops further changes to that site and reports
`recovery_required`. Investigate the recorded release/resource
versions and restore through the original adapter; do not overwrite a human edit
or deploy through another adapter to clear an unknown result.

If a human intentionally changed the site, an administrator can select **Verify
and keep current website** on the unresolved report. This checks that hosting has
no operation still in progress, closes scoped browser access, and runs both live
verification probes. A healthy current version is retained and the change becomes
`resolved`, releasing the site for another prepared edit. It does not overwrite
human content, erase recovery artifacts, or let an agent clear its own quarantine.
Configuration cannot remove or rebind a site while recovery is active.

History and screenshots are readable through directory-scoped admin permissions;
configuration, test, restore and recovery resolution require administrator ownership
and **Privileged operations** unlocked in Admin Web. Mutation requests must use
`application/json`; browser requests must come from the Admin Web origin. Direct
authenticated API clients may omit the Origin header. Snapshots are
private host files/SQLite data under `runtime/addons/wordpress-addon/`, with no
browser state or long-term credentials in the ledger. Default retention is 90 days;
unresolved changes and the latest published recovery baseline are always retained.
Back up this private runtime along with the managed repository retention pins.

## Validation and troubleshooting

Run the focused Python suites, and opt in to the disposable real WordPress/MariaDB
fixture when Docker is available:

```bash
PYTHONPATH=src timeout 120 ./.venv/bin/pytest -q packages/wordpress-addon/tests/test_wordpress_addon.py
PYTHONPATH=src EVANDER_RUN_WORDPRESS_INTEGRATION=1 timeout 240 \
  ./.venv/bin/pytest -q packages/wordpress-addon/tests/test_wordpress_integration.py
```

The integration fixture needs locally available `wordpress:php8.3-apache` and
`mariadb:11` images. It uses synthetic data, an internal network, no published ports,
container-local HTTP and cleanup in a `finally` block.

The separate `packages/wordpress-addon/tests/test_wordpress_browser_checks.py` fixture exercises the actual
desktop/mobile Chromium runner, HTTPS, links, forms, screenshots and confirmed
server failures without public internet. Run it with
`EVANDER_RUN_WORDPRESS_BROWSER_CHECKS=1` and the seeded module/executable settings
above. Its temporary certificate exception is limited to the fixture's public key;
production TLS verification and public-address validation are unchanged.

If discovery fails, check HTTPS/application-password handling, service capabilities
and protocol versions. If preparation fails, inspect adapter coverage, InnoDB,
credential consumer approval and browser baseline availability. If a change stays
queued, verify its directory's workflow graph and both orchestrators. If uncertain,
keep its ID and inspect status; do not submit a duplicate operation under a new ID.
For a broker failure, verify target origin, `wordpress` adapter enablement, active
prepared change ownership and relay access to the named host credential-store key.
