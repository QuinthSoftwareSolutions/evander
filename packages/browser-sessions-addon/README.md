# Browser Sessions Add-on

| Mode | Browser host | Destinations |
| --- | --- | --- |
| Hosted | Owning agent's assigned container worker | Approved HTTP targets or leased application endpoints |
| Local support | User's existing Chrome tab | Explicitly shared tab and locally approved origins |

Hosted viewers join the already active browser, including its application login and navigation. Viewer chat resumes the authoring task and thread; browser_session_reply answers that transcript. Teams and Discord keep their own transcripts.

Install this optional package explicitly. Core installation does not enable or deploy it. See [operations](docs/operations.md), [protocol](docs/protocol.md), and the [browser-only sessions and Playwright](docs/independent-sessions.md).

## Managed connections

Owners configure worker, relay and speech destinations and store their credentials through **Configure** after unlocking privileged operations. Credentials are encrypted by the platform store and bound to their purpose, complete destination and directory scope. Browser managers can use configured connections and adjust ordinary session settings without reading credentials or selecting environment variables. Existing sessions retain their original connections.

See [credential migration](docs/credentials.md) before upgrading an installation that uses environment-based browser settings.

## Build and verify

Use Node 22+ and the repository's Python environment. Run npm ci --ignore-scripts, npm run build, and python scripts/package_extension.py. Run python -m pytest -q, npm test, and npm run test:browser. Set EVANDER_TEST_PYTHON to the repository Python interpreter for integration fixtures.

Python package builds include the built viewer/controller and extension ZIP. Extract evander_browser_sessions_addon/ui_static/chrome-extension.zip and load its directory in Chrome developer mode, or distribute a signed extension through managed Chrome deployment.

The browser suite uses loopback fixtures, ephemeral Chrome profiles, the actual MV3 debugger API, authenticated hosted state, frame streaming, control handover and source/style updates. It pregrants only its optional loopback host permission. Installation dialogs, enterprise policy, real worker networking and SQL require deployment qualification.

## Agent usage

All authorized agents receive the add-on's packaged [operating guide](evander_browser_sessions_addon/agent-guide.md)
and browser-creation schema through `browser_session_profiles` and
`browser_session_create`. To resume an existing session, request
`browser_session_status` with `include_guidance: true`. This is the shared source
of operating instructions for all agent roles; application-specific recipes stay
in operator-owned storage.

1. Grant desired browser_session tools explicitly and discover browser profiles.
2. For an external application, select the owning agent's approved HTTP target aliases.
3. For hosted application code, create it through the independent Application Environments add-on and await its endpoint references.
4. Call browser_session_create in hosted mode with a browser profile, targets and entry_url. Reuse request_id for identical retries.
5. Share the returned viewer_url through existing configured delivery or CI output.
6. Observe before acting. Human takeover fences both typed input and optional Playwright connections. Reobserve after an unknown outcome; never replay it.
7. Close the browser when finished. Close its application separately when desired.

See [managed login, Playwright, page selection and CI output](docs/independent-sessions.md).

For local support, create mode user_hosted and call browser_session_invite. The user opens their app tab and extension, pastes the invitation and approves the control-plane connection. It starts in observe mode. Only that host grants input, approves another origin or stops sharing. The agent that creates the session observes and interacts with it using its existing context and browser tool grants. Extension chat resumes that same agent and its originating thread; replies remain in the extension transcript. No separate support agent is created.

After upgrading, existing local sessions can be observed by their creating agent. Use **Allow agent to interact** again to grant that agent input; legacy support agents are removed when their session closes.

For scanner-style keyboard input and clicks inside scrollable containers, update and reload Chrome extension **0.2.4** or later. The type action sends character keystrokes; use fill to replace a specific text field and key with Enter to submit. Hosted browser controllers also need the updated package before starting a new preview.

Browser feedback is pinned to the creating conversation. If its native thread is still being recorded, messages wait for that run rather than resume another recent chat. For an older session without a recorded conversation, create a new sharing session from the intended assistant conversation.

Workflow nodes effect:browser-session:ensure, query:browser-session:status and effect:browser-session:close use the same service and require the workflow and owner to share a directory. See [node examples](examples/workflow-nodes.json).

## Hosted viewer

The standalone viewer fills the page with a compact browser toolbar, the shared application and chat, without a separate page header, footer or session-list link. Session details stay in the toolbar. One button indicates control ownership and toggles between **Take control** and **Hand back to agent**. The theme button beside **Talk with your agent** remembers the light/dark preference used by Admin Web; embedded views follow their host's theme. This changes the viewer's appearance, independently of the shared application's own theme.

Viewers can switch between shared tabs without taking control. Selecting a tab changes the shared view and preserves the current input controller.

Enter sends a non-empty message; Shift+Enter inserts a newline. Observe-only clicks show a brief reminder, and other errors remain dismissible inside the toolbar without covering chat.

## Voice

Enable optional speech in directory settings. Microphone transcription requires FFmpeg on the Admin Web and relay hosts. Hosted viewers and the Chrome sidebar support push-to-talk or opt-in hands-free capture with local speech detection. Streamed replies are buffered for smoother playback, and the default voice is Onyx. First-use microphone permission may need a small permission window; ongoing voice uses no separate tab. Use Chrome extension 0.6.0 and Browser Sessions package 0.8.0 for hands-free capture. See [voice setup and usage](docs/voice.md).

## Boundaries

Hosted sessions require container_worker_vm on Linux with Docker IPv4 bridge filtering. Local hosting targets Chrome 125+. Typed tools expose bounded browser actions. Granted Playwright connections support test scripts in the same Chromium browser; file transfer, clipboard sharing and continuous recordings remain unavailable. Cross-origin navigation pauses local input until approved. Debugger detach requires a new invitation.

Defaults: one controller; one hosted browser per worker and authoring agent; four-hour hard deadline; thirty-minute inactivity limit; ten-minute invitations. “Keep active” resets inactivity without extending the hard deadline. Frames and polling do not renew activity.

Customer recipes, credentials and seed data belong in operator-owned storage.


Chrome sharing automatically uses compatibility mode if a password manager or another extension injects a protected frame that blocks debugger attachment. Other extensions remain enabled and their frames stay in place. Compatibility mode captures only the selected tab, including while it is in the background, and supports application DOM inspection, form entry, clicks, scrolling and page keyboard handlers. It does not enter protected/cross-origin frames, operate native JavaScript dialogs, or generate trusted native input. Applications that require those capabilities can use the optional native helper, normal mode, or the host's local input. Click the Evander toolbar icon on the application tab before sharing so Chrome grants tab-capture access. No remote-debugging flag, local companion or application port forwarding is required.


For native input while keeping other extensions enabled, select **Installed helper — full browser control** after installing the optional standalone companion. The helper uses Chrome 144+ consent-based debugging; the sidebar remains the interface for sharing, chat and voice. See [helper installation, packaging and boundaries](docs/helper.md). Helper packages must be built and included in the deployment bundle before the download page offers them.

### Follow-up delivery

Browser chat automatically steers an active compatible Codex conversation. Typed
messages, sidebar voice transcripts and page annotations share this path. Idle
assistants start a normal turn; unsupported runners queue the request. The hosted
viewer and Chrome sidebar show whether each message is pending, queued, added to
the active turn, or uncertain. A delivery acknowledgement does not mean the agent
has acted; its reply still appears in this browser chat. Closing the sharing
session cancels requests still waiting to start.

Messages wake the relay dispatcher immediately through a local runtime socket.
Browser reconciliation runs independently so slow worker health checks do not
hold up chat. Hosted viewers receive message and delivery notifications over
their authenticated browser stream; periodic dispatch and cursor-based polling
recover missed notifications and support older clients. Notifications contain
no message text. Admin Web, MCP and the relay share the runtime directory, and
only one relay dispatcher may own its wake socket.

Deploy the core worker before the control plane and this add-on. See
[conversation steering operations](../../docs/operations.md#conversation-steering-from-teams-and-shared-browsers)
for capability checks, receipt recovery and Teams workflow activation.

## Faster multi-step interaction

The optional tools `browser_session_run` and
`browser_session_cancel_run` let the owning agent choose short multi-step objectives, supply
exact input values and verify the result; the directory's Jev or OpenAI
Decisions provider selects intermediate typed actions in the existing shared
browser. This is disabled by default. See [setup, agent guidance, limits and
benchmarks](docs/fast-execution.md).
