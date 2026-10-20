# Protocol and isolation

The TLS edge routes only /_browser/* to the relay. Its /internal/* paths require a private bearer; the supervisor's /v1/* uses a distinct worker bearer. No Docker API or CDP endpoint is exposed.

Hosted HTML, APIs and WebSockets use Admin UI identity. Signed-out links redirect to /?next=<local viewer path>; password and SSO sign-in preserve that destination. Encoded external redirects are rejected. Hosted access is rejected when Admin authentication is disabled. Streams recheck identity and directory permissions each second. Logout revocation is persisted across processes.

Assign browser_sessions.view, browser_sessions.control and browser_sessions.manage through directory-scoped groups. Owners have all three. View permits observation; control permits input/chat; management permits configuration, creation and termination.

Guest invitations cannot access hosted sessions. Random tokens are hashed, single-use and expiring; invitation tokens stay in URL fragments. Pairing exchanges one for distinct endpoint and host credentials in Chrome session storage. Each endpoint role and host capability is bound to one local session.

The creating agent owns both hosted and local sessions. Browser tools still require its configured grants; another agent cannot inspect or operate the session. Local hosts grant and revoke input to that creating agent. Extension messages resume the owner's original thread and reply only to the browser transcript. Legacy support-agent IDs are retained solely for cleanup.

The bundled MV3 extension requests a control-plane host permission after an explicit Share click and attaches to one selected tab. It exposes no externally_connectable web API. Stop revokes local input before network acknowledgement. Detach, reconnect, origin changes, expiry and handover fence input. Reconnect never restores input silently.

## Typed commands

Protocol version 1 uses authenticated WSS. Hosted viewers pass through Admin Web. JPEG frames and viewer queues are bounded; slow consumers drop old frames.

Broker and endpoint both check live state, origin, generation and controller. Actions are observe, navigate, click, hover, fill, type, selected keys, scroll, dialog, highlight and reload. There is no arbitrary evaluation, CDP passthrough, cookie export or filesystem API. Observation returns a screenshot and bounded accessibility references. Navigation invalidates references; control changes fence input between focus and typing.

The type action emits character keydown, keypress and keyup events for page-level keyboard listeners as well as focused fields. The fill action focuses and replaces a referenced field with direct text insertion. The key action supports Enter, Space and the bounded navigation/editing keys; Enter and Space include their text events. Every input event rechecks the control generation and approved origin.

Command IDs are durable and one-use. Timeout/disconnect means unknown outcome. Mutations are never replayed automatically. Pause always revokes input despite unrelated session revisions; grants/handover use their own control generation.

## Resource isolation

Operator profiles pin images, CPU/memory, read-only toolchains, exact IPv4 egress and optional SQL. The worker independently compares the complete profile. Repo manifests accept argv, working directory, readiness/entry paths, ports and an optional scoped database request. Agents cannot supply images, host paths, Docker flags, secrets or egress.

Opaque workspace references are owner-bound. Only repositories and host-issued task-worktree receipts under that agent home qualify. The app receives only that checkout at /workspace. Directory handles pin path components before the bind, preventing symlink replacement. No Docker socket or whole agent home is mounted.

App/browser containers have separate processes, dropped capabilities, no privilege escalation and bounded resources/logs/deadlines. The browser has no checkout. Its credential is outside the app mount. The app may provide /run/preview-output/browser-state.json before readiness; only cookies/storage for approved preview origins are accepted.

Each preview has a separate IPv4 bridge. Forwarding and host-input firewall rules precede container startup. Only owned internal traffic, established replies and approved destinations pass. External DNS forwarding is disabled. Relay addressing is host-resolved and pinned. Only the browser IP reaches the relay; only the app IP receives profile egress. No core guest firewall port is opened.

Optional SQL keeps admin credentials on the worker. Generated database/login names and passwords are supervisor-owned. The app receives EVANDER_PREVIEW_DATABASE only for its generated database. Migrations/seeding are startup responsibilities. Cleanup drops only generated resources.

## Recovery and retention

SQLite records intent before side effects and pins active placement/cleanup credentials. Request IDs prevent duplicates and reject changed inputs. Interrupted startup fails without replaying migrations.

The worker reaper, app timeout and browser deadline survive agent turns and control-plane loss. Control-plane reconciliation handles inactivity, disabled sharing, missing/expired owners, startup failure and cleanup retry. Core deletion consults a provider-neutral dependency registry even when the add-on is disabled. Failed cleanup preserves the checkout.

Chat/action metadata follows retention. Action audit omits form text, cookies, tokens and pixels. Frames remain in memory. Explicit browser_session_save_screenshot creates an expiring communication artifact. No continuous recording exists.

Chrome integration uses the official [debugger API](https://developer.chrome.com/docs/extensions/reference/api/debugger) and its [Manifest V3 worker lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle). Active debugger sessions keep the service worker available; detach is a deliberate stop boundary.

Voice endpoints are documented in [voice setup and usage](voice.md). Hosted /sessions/{id}/voice/info, /transcribe and /speech retain Admin permissions and exact-origin mutation checks. Local /_browser/guest/voice/{action} requires the guest_voice role and only supports info, state, message, transcribe and speech. That role is rejected by browser endpoint, host control and internal APIs. Audio uploads are bounded binary requests with X-Voice-Request-ID; speech selects a session-owned agent reply, never arbitrary supplied text.
