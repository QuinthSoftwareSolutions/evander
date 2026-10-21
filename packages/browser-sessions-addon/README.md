# Browser Sessions Add-on

| Mode | Application/browser host | Human access |
| --- | --- | --- |
| Hosted demonstration | Authoring agent's assigned container worker | Admin UI sign-in and directory permission |
| Hosted development | Same worker and selected checkout | Same authenticated viewer with dev-server updates |
| Local support | User's existing Chrome tab | Single-use invitation, extension, explicit local input grant |

Hosted viewers join the already active browser, including its application login and navigation. Viewer chat resumes the authoring task and thread; browser_session_reply answers that transcript. Teams and Discord keep their own transcripts.

Install this optional package explicitly. Core installation does not enable or deploy it. See [operations](docs/operations.md), [protocol](docs/protocol.md), and the [synthetic launch recipe](examples/preview/README.md).

## Build and verify

Use Node 22+ and the repository's Python environment. Run npm ci --ignore-scripts, npm run build, and python scripts/package_extension.py. Run python -m pytest -q, npm test, and npm run test:browser. Set EVANDER_TEST_PYTHON to the repository Python interpreter for integration fixtures.

Python package builds include the built viewer/controller and extension ZIP. Extract evander_browser_sessions_addon/ui_static/chrome-extension.zip and load its directory in Chrome developer mode, or distribute a signed extension through managed Chrome deployment.

The browser suite uses loopback fixtures, ephemeral Chrome profiles, the actual MV3 debugger API, authenticated hosted state, frame streaming, control handover and source/style updates. It pregrants only its optional loopback host permission. Installation dialogs, enterprise policy, real worker networking and SQL require deployment qualification. For the real SQL provisioning/isolation/cleanup check, set EVANDER_BROWSER_TEST_SQL_SERVER, EVANDER_BROWSER_TEST_SQL_PASSWORD and optionally EVANDER_BROWSER_TEST_SQLCMD for a dedicated test server, then run python -m unittest discover -s tests -p test_worker_sql.py. The check creates and drops only generated preview databases and logins; it is skipped when those settings are absent.

## Agent usage

All authorized agents receive the add-on's packaged [operating guide](evander_browser_sessions_addon/agent-guide.md)
and launch-manifest schema through `browser_session_profiles` and
`browser_session_create`. To resume an existing session, request
`browser_session_status` with `include_guidance: true`. This is the shared source
of operating instructions for all agent roles; application-specific recipes stay
in operator-owned storage.

1. Grant desired browser_session tools to the authoring agent.
2. Discover browser_session_profiles and browser_session_workspaces.
3. Prepare a repo-local .evander/preview.json; call browser_session_create with a fresh request ID, approved profile and opaque workspace reference.
4. Poll browser_session_status and send its viewer URL in the existing Teams/Discord conversation.
5. Observe before acting. Use current element references and generation. Human takeover fences agent input. Observe again after an unknown action outcome instead of replaying it.
6. Viewer feedback resumes the authoring task. Send progress updates and replies with browser_session_reply and the original message ID, as often as needed. Give each outgoing reply a fresh request_id and reuse it only when retrying the identical reply. Source edits stay normal repository edits.
7. Close to release resources before removing the owning checkout.

For local support, create mode user_hosted and call browser_session_invite. The user opens their app tab and extension, pastes the invitation and approves the control-plane connection. It starts in observe mode. Only that host grants input, approves another origin or stops sharing. The agent that creates the session observes and interacts with it using its existing context and browser tool grants. Extension chat resumes that same agent and its originating thread; replies remain in the extension transcript. No separate support agent is created.

After upgrading, existing local sessions can be observed by their creating agent. Use **Allow Truman to interact** again to grant that agent input; legacy support agents are removed when their session closes.

For scanner-style keyboard input and clicks inside scrollable containers, update and reload Chrome extension **0.2.4** or later. The type action sends character keystrokes; use fill to replace a specific text field and key with Enter to submit. Hosted browser controllers also need the updated package before starting a new preview.

Browser feedback is pinned to the creating conversation. If its native thread is still being recorded, messages wait for that run rather than resume another recent chat. For an older session without a recorded conversation, create a new sharing session from the intended assistant conversation.

Workflow nodes effect:browser-session:ensure, query:browser-session:status and effect:browser-session:close use the same service and require the workflow and owner to share a directory. See [node examples](examples/workflow-nodes.json).

## Hosted viewer

The standalone viewer fills the page with a compact browser toolbar, the shared application and chat, without a separate page header, footer or session-list link. Session details stay in the toolbar. One button indicates control ownership and toggles between **Take control** and **Hand back to Truman**. The theme button beside **Talk with Truman** remembers the light/dark preference used by Admin Web; embedded views follow their host's theme. This changes the viewer's appearance, independently of the shared application's own theme.

Enter sends a non-empty message; Shift+Enter inserts a newline. Observe-only clicks show a brief reminder, and other errors remain dismissible inside the toolbar without covering chat.

## Voice

Enable optional speech in directory settings. Microphone transcription requires FFmpeg on the Admin Web and relay hosts. Hosted viewers and the Chrome sidebar support push-to-talk or opt-in hands-free capture with local speech detection. Streamed replies are buffered for smoother playback, and the default voice is Onyx. First-use microphone permission may need a small permission window; ongoing voice uses no separate tab. Use Chrome extension 0.6.0 and Browser Sessions package 0.8.0 for hands-free capture. See [voice setup and usage](docs/voice.md).

## Boundaries

Hosted sessions require container_worker_vm on Linux with Docker IPv4 bridge filtering. Local hosting targets Chrome 125+. Pop-up windows, browser chrome, file transfer, arbitrary JavaScript, clipboard sharing and continuous recordings are outside this version. Cross-origin navigation pauses local input until approved. Debugger detach requires a new invitation.

Defaults: one controller; one hosted preview per worker and authoring agent; four-hour hard deadline; thirty-minute inactivity limit; ten-minute invitations. “Keep active” resets inactivity without extending the hard deadline. Frames and polling do not renew activity.

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
