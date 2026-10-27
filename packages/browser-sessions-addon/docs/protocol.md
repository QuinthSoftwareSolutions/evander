# Protocol and isolation

The TLS edge routes only /_browser/* to the relay. Its /internal/* paths require a private bearer; the supervisor's /v1/* uses a distinct worker bearer. No Docker API or CDP endpoint is exposed.

Hosted HTML, APIs and WebSockets use Admin UI identity. Signed-out links redirect to /?next=<local viewer path>; password and SSO sign-in preserve that destination. Encoded external redirects are rejected. Hosted access is rejected when Admin authentication is disabled. Streams recheck identity and directory permissions each second. Logout revocation is persisted across processes.

Assign browser_sessions.view, browser_sessions.control and browser_sessions.manage through directory-scoped groups. Owners have all three. View permits observation; control permits input/chat; management permits configuration, creation and termination.

Page enumeration and selection are viewing actions. Selecting a shared page changes the stream without granting input or changing control ownership; live generation and expiry checks still apply.

Guest invitations cannot access hosted sessions. Random tokens are hashed, single-use and expiring; invitation tokens stay in URL fragments. Pairing exchanges one for distinct endpoint and host credentials in Chrome session storage. Each endpoint role and host capability is bound to one local session.

The creating agent owns both hosted and local sessions. Browser tools still require its configured grants; another agent cannot inspect or operate the session. Local hosts grant and revoke input to that creating agent. Extension messages resume the owner's original thread and reply only to the browser transcript. Legacy support-agent IDs are retained solely for cleanup.

Browser chat permits any number of agent replies to an original participant message, including further updates after it is marked answered. Each reply keeps the original session and transcript channel. The optional browser_session_reply request_id identifies one outgoing reply within that browser session: reuse it only for an identical retry to the same original message; changed text or a changed parent is rejected. Fresh IDs allow intentional repeated text. Older callers may omit request_id; identical text for the same original message is then deduplicated, while different replies are accepted. Existing replies survive upgrades without a data migration.

The bundled MV3 extension requests a control-plane host permission after an explicit Share click and attaches to one selected tab. It exposes no externally_connectable web API. Stop revokes local input before network acknowledgement. Detach, reconnect, origin changes, expiry and handover fence input. Reconnect never restores input silently.

## Typed commands

Protocol version 1 uses authenticated WSS. Hosted viewers pass through Admin Web. JPEG frames and viewer queues are bounded; slow consumers drop old frames.

The hosted stream also sends `{"kind":"messages"}` when chat or delivery
receipts may have changed. These content-free hints use a separate coalescing
lane from frames. Viewers fetch messages and status updates through their existing
authenticated cursor API, which remains the source of truth after reconnects or
missed notifications. No client acknowledgement or message replay is implied.

Broker and endpoint both check live state, origin, generation and controller. Actions are observe, navigate, click, hover, fill, type, selected keys, scroll, dialog, highlight and reload. There is no arbitrary evaluation, CDP passthrough, cookie export or filesystem API. Observation returns a screenshot and bounded accessibility references. Navigation invalidates references; control changes fence input between focus and typing.

The type action emits character keydown, keypress and keyup events for page-level keyboard listeners as well as focused fields. The fill action focuses and replaces a referenced field with direct text insertion. The key action supports Enter, Space and the bounded navigation/editing keys; Enter and Space include their text events. Every input event rechecks the control generation and approved origin.

Referenced click and hover actions scroll their target into view before resolving viewport coordinates. Scrolling requires the same input grant and origin fence as a click. Highlight never scrolls the page. Known browser error codes become fixed recovery instructions; raw peer errors are never forwarded.

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

Chrome's normal integration uses the official [debugger API](https://developer.chrome.com/docs/extensions/reference/api/debugger) and its [Manifest V3 worker lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle). Active debugger sessions keep the service worker available; detach is a deliberate stop boundary.

Voice endpoints are documented in [voice setup and usage](voice.md). Hosted /sessions/{id}/voice/info, /transcribe and /speech retain Admin permissions and exact-origin mutation checks. Local /_browser/guest/voice/{action} requires the guest_voice role and only supports info, state, message, transcribe and speech. That role is rejected by browser endpoint, host control and internal APIs. Audio uploads are bounded binary requests with X-Voice-Request-ID; speech selects a session-owned agent reply, never arbitrary supplied text.

The current extension obtains its guest_voice capability through a sidebar-only background message bound to the active browser session ID. The sidebar keeps it in memory and sends microphone clips directly to the scoped voice endpoints; transcription is queued as normal session chat and agent replies can be fetched as bounded speech. Extension-origin requests are accepted only on the guest voice API after its capability and host checks. Hosted voice retains exact control-plane origin validation. Ending or changing sharing disposes the voice UI and aborts its in-flight work. The microphone permission popup has no sharing or voice capability and only grants the extension origin access to the local microphone.


When Chrome rejects debugger access specifically because a different extension owns a frame, the endpoint switches to compatibility mode. Chrome's [tabCapture API](https://developer.chrome.com/docs/extensions/reference/api/tabCapture) binds a video stream to the explicitly shared tab. An [offscreen document](https://developer.chrome.com/docs/extensions/reference/api/offscreen) converts that stream into bounded JPEG frames without opening a tab or capturing audio. The document receives no host/session credentials. Capture ends on close, expiry or loss of its 30-second frame-request watchdog; origin changes suspend the stream until the host approves the new page.

Compatibility DOM access runs a fixed adapter in Chrome's isolated world, restricted to the approved main document. Document IDs and navigation epochs fence observations and actions. The adapter derives bounded element references from application roles/labels, retains the shared engine's authority checks, and dispatches DOM events for forms and application keyboard listeners. Other extensions' frames are neither entered nor removed; child-document and native-dialog operations return bounded capability errors. Synthetic events have `isTrusted = false`, so some sites need local input or normal sharing mode. A runtime switch revokes input, invalidates old references and never replays a pending command. Only the cross-extension access error selects this fallback; permission-policy and competing-debugger failures remain explicit errors.


Optional helper mode keeps the same sidebar and command engine but replaces `chrome.debugger` transport with Chrome Native Messaging to a per-user executable. The helper discovers Chrome's consent-based loopback endpoint from its local user-data directory, attaches the exact selected page target and independently fences origin, controller, generation, document epoch and expiry. It accepts only the engine's fixed CDP method set, forwards selected-tab events and bounded JPEGs, and receives no Evander or provider credentials. Closing its native port disconnects its CDP client without closing Chrome. [Helper details](helper.md) cover installation and release packaging.

## Bounded decision execution

Endpoint authentication optionally advertises `features: {"fast_execution": 1}`.
The extension advertises it in helper mode only after a native `ping` reports the
same capability. Legacy endpoints remain protocol version 1 and support their
existing direct tools. Fast runs require the capability before observing a page.

Additive command fields are `include_image` (default true), `revalidate` (default
false), and `deadline_at` (optional absolute Unix seconds). Fast observations
omit image capture, retain bounded state and page context, and flag truncation.
Fast input references the endpoint's own preceding snapshot; semantic identity,
context, values and availability are rechecked before input. Subsequent characters
allow the command's own field changes while preserving identity/authority checks.

Control-plane and relay enforce a durable run lease independently. Internal
command envelopes carry the owning `run_id`; callers of direct tools cannot join
an active lease. Native commands carry deadlines outside CDP parameters; the
helper checks them immediately before dispatch. This adds no CDP methods or
arbitrary execution permissions. Helper observations remain below native-message
limits and report truncation rather than silently omitting targets.

Decision projections deduplicate controls/context and reveal overflow targets
through bounded `inspect` regions. They never replace the endpoint observation
used for command validation. The full decision payload is limited to 64 KiB;
observations marked truncated still yield without model decisions or input.

Optional ordered run checkpoints associate completed actions with typed
observation predicates. Their waits reuse the same automation lease, authority
checks and overall deadline, with a separate bounded condition deadline. They
add no endpoint methods or browser scripting. Durable checkpoint receipts retain
only IDs, outcomes, observation/page identities, timestamps and bounded counts
or target references; condition text, values and snapshots are not persisted.
Cancellation and uncertain input stop the run without replay, including while a
checkpoint is pending. Old receipts without checkpoints remain readable.

An optional run-level completion predicate uses those same observation conditions
without an action trigger. It is checked against fresh full observations only
after required checkpoints, and retains metadata-only completion evidence before
returning for owning-agent verification. It cannot be combined with a stopping
checkpoint or overridden by a model `done` choice. Read-only stale observations
may be retried up to three attempts with fresh control, grant and deadline checks;
input and uncertain outcomes are never replayed by this recovery.
