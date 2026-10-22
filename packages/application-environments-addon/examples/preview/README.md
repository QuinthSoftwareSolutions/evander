# Synthetic launch recipe

Copy preview.json to .evander/preview.json and server.mjs to the repo root. Select a preseeded Node image. The app listens on 0.0.0.0:4200; Node watch restarts after edits.

For Angular use the existing dev server, a 0.0.0.0 listener, host validation for the internal preview hostname, and declared additional API ports or an app-owned reverse proxy.

Startup can seed a disposable login and write Playwright state to /run/preview-output/browser-state.json before readiness, using EVANDER_PREVIEW_ORIGIN (the internal app origin). EVANDER_PREVIEW_ENTRY_URL and EVANDER_PREVIEW_OUTPUT are also supplied by the supervisor. The viewer joins that authenticated browser without receiving cookies.

For SQL request database "preview" and enable it in the approved profile. Use EVANDER_PREVIEW_DATABASE only. Approve its exact server IP/port as app egress. Never use production connections.
