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

The browser suite uses loopback fixtures, ephemeral Chrome profiles, the actual MV3 debugger API, authenticated hosted state, frame streaming, control handover and source/style updates. It pregrants only its optional loopback host permission. Installation dialogs, enterprise policy, real worker networking and SQL require deployment qualification.

## Agent usage

1. Grant desired browser_session tools to the authoring agent.
2. Discover browser_session_profiles and browser_session_workspaces.
3. Prepare a repo-local .evander/preview.json; call browser_session_create with a fresh request ID, approved profile and opaque workspace reference.
4. Poll browser_session_status and send its viewer URL in the existing Teams/Discord conversation.
5. Observe before acting. Use current element references and generation. Human takeover fences agent input. Observe again after an unknown action outcome instead of replaying it.
6. Viewer feedback resumes the authoring task. Answer with browser_session_reply and its message ID. Source edits stay normal repository edits.
7. Close to release resources before removing the owning checkout.

For local support, create mode user_hosted and call browser_session_invite. The user opens their app tab and extension, pastes the invitation and approves the control-plane connection. It starts in observe mode. Only that host grants input, approves another origin or stops sharing. A separate browser-only support agent inherits no repository, connector, contact, HTTP-target or publication grants.

Workflow nodes effect:browser-session:ensure, query:browser-session:status and effect:browser-session:close use the same service and require the workflow and owner to share a directory. See [node examples](examples/workflow-nodes.json).

## Voice

Enable optional speech in directory settings. Hosted viewers record bounded push-to-talk clips and can hear new replies. Local hosts open a scoped companion voice page from the Chrome extension. See [voice setup and usage](docs/voice.md).

## Boundaries

Hosted sessions require container_worker_vm on Linux with Docker IPv4 bridge filtering. Local hosting targets Chrome 125+. Pop-up windows, browser chrome, file transfer, arbitrary JavaScript, clipboard sharing and continuous recordings are outside this version. Cross-origin navigation pauses local input until approved. Debugger detach requires a new invitation.

Defaults: one controller; one hosted preview per worker and authoring agent; four-hour hard deadline; thirty-minute inactivity limit; ten-minute invitations. “Keep active” resets inactivity without extending the hard deadline. Frames and polling do not renew activity.

Customer recipes, credentials and seed data belong in operator-owned storage.
