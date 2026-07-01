# 18. Workers And Background Services

This section helps operators understand and manage background workers in one place.
Workers are the always-on processes that keep automation moving when no one is actively
watching the screen.

Core control-plane services such as the MCP gateway, relays, and Admin Web are managed as host
systemd services. Check those with the Operations runbook, `systemctl`, and service logs rather
than expecting them to appear in the Workers page.

For live operator status in one screen, use section 3. For event-level workflow failures,
go to section 11. For recurring failure patterns and operational cost signals, use section
12. For inbound notification flow and communication channel behavior, use section 15. For safe
service setting changes and revision handling, use section 16.

## 18.1 What Workers Do

Workers are grouped by role:

1. Workflow automation processing
   - `evander-trigger-orchestrator`
   - `evander-effect-orchestrator`
2. Inbound and resume automation
   - `evander-ado-worker`
   - `evander-proxy-issue-sync`
   - `evander-discord-ingest`
   - `evander-discord-wake`
   - `evander-scheduled-wake`
   - `evander-teams-ingest`
   - `evander-teams-meeting-ingest`

In many deployments, the exact set matches what is installed and enabled on the host.
If a worker is not active in this environment, it may still appear with a stopped state or not
show up at all.

Services such as `evander-gateway.service`, `evander-openai-relay.service`,
`evander-nuget-relay.service`, `evander-http-target-relay.service`, and
`evander-admin-web.service` are not Workers-managed entries. Use host service checks for
start, stop, and log review for those units.

What to remember:

1. Stopping a wake or ingest worker stops background message-driven automation for that channel,
   but does not erase past automation state.
2. Stopping workflow orchestrators pauses scheduled automation runs and effect execution.
3. Restarting a worker can require short re-sync time while it reconnects to runtime queues.

## 18.2 Worker Status

The Workers view is the operator surface for checking worker-level health.

1. **Active state** tells you if the process is currently running.
2. **Sub-state** shows if the service is currently starting, running, or cleaning up.
3. **Result** gives the last known startup, exit, or failure state.
4. **Unit name** links the worker in the host service manager so you can jump to `systemctl`
   checks.
5. **Main PID** tells you which process is currently handling the worker.
6. **Settings** shows current worker-global config snapshot.
7. **Recent log/Journaling** is where you confirm startup errors, failed connections, and retry
   activity.

Read status as a health signal, not a full diagnosis:

1. Green running state with active workflow events usually means background tasks are healthy.
2. Repeatedly failing worker starts means dependency checks (tokens, relay access, or network) often need operator attention.
3. If state is stale, verify the page and host time are aligned before changing settings.

## 18.3 Start And Stop

Use start/stop only when needed.

1. Use start when a paused automation path needs to resume now.
2. Use stop when a channel is unhealthy, under maintenance, or before a targeted recovery step.
3. Do not stop workers while an incident review is in progress unless you are intentionally pausing
   automation to stabilize active activity.

If you stop one worker, check which dependent workflow or wake path uses it before continuing
with manual runbooks.

## 18.4 Worker Settings

Some workers expose global settings on the Workers screen. Typical use cases include queue timing
and provider behavior defaults.

1. Settings are host-level and should be changed carefully.
2. Save conflicts can happen if two operators edit the same worker at the same time.
3. A setting change may require a service refresh for effect to apply.
4. A worker can have no settings in this interface; in that case, settings fields will be empty.

Common setting impact pattern:

1. Change only one area at a time.
2. Save and confirm.
3. Re-check if the worker status changed cleanly.
4. If the action is still failing, follow section 16 safe save guidance and re-apply the change from
   a fresh settings baseline.

## 18.5 Service Installation And Health

For initial host rollout, use the normal install flow from the operations runbook:

1. Run install preflight checks before enabling or changing services.
2. Install or re-render units once checks pass.
3. Start required services from the host service manager.

Typical service bootstrap steps are:

1. `./scripts/install_systemd_units.sh --preflight`
2. `sudo ./scripts/install_systemd_units.sh`
3. `sudo systemctl start ...`

Broad checks are best done from two locations:

1. Admin Web dashboard for one-page operator signals.
2. Host status command for machine-readable health and dependency checks.

## How-to: Start Or Stop A Worker

When to use: Use this to restart or pause background automation on one service without changing global
configuration.

Prerequisites:

1. Correct directory is selected in Admin Web.
2. Privileged operations are unlocked.
3. You understand which automation path depends on this worker.

Steps:

1. Open **Workers**.
2. Select the worker from the left list.
3. Review the worker name, active state, and recent log.
4. Click **Start** to start, or **Stop** to pause.
5. Click **Refresh**.
6. Confirm:
   1. active state updates as expected,
   2. sub-state matches the new lifecycle,
   3. logs show startup or stop confirmation.
7. If stopping, verify dependent flow owners to avoid confusion (for example wake workflows, triggers, or
   orchestrator paths).

If this is not the intended worker effect: stop immediately and restart the correct worker, then run the
broad health check.

## How-to: Change A Worker Setting

When to use: Use this when timing windows, queue behavior, or a global worker default needs tuning.

Prerequisites:

1. Privileged operations are unlocked.
2. You have confirmed the worker exposes editable global settings.
3. You know the intended effect and rollback plan.

Steps:

1. Open **Workers**.
2. Select the worker.
3. Open **Global settings** and expand the field you need.
4. Make one change only.
5. Click **Save**.
6. If a revision warning appears:
   1. reload the setting view,
   2. re-check your intended value,
   3. save again.
7. Reload the worker or restart it if the interface indicates restart is needed.
8. Run **Check Whether Background Automation Is Running** to verify behavior after the change.

Expected result:

1. The worker keeps the new behavior with no unexpected side effects.
2. Other directories are unaffected by host-global worker setting changes unless the deployment explicitly
   uses directory overlays.

## How-to: Check Whether Background Automation Is Running

When to use: Use this when a response is delayed, an automation did not run, or you need a quick operational
health check before changing settings.

Steps:

1. Open **Dashboard** and check the worker summary.
2. Open the **Workers** tab for each worker shown as non-running or warning.
3. Open **Workflow** and verify current runs if automation is delayed.
4. Open linked **Tool Calls** or approvals for worker-owned paths with errors.
5. If a worker appears stopped, verify whether the associated service is intentionally disabled.

If automation is not moving:

1. Confirm `Dashboard` and `Workers` show the same worker state.
2. Confirm the corresponding service process is running in host service status.
3. Collect evidence in a short note before any start/stop or config changes.

## How-to: Run A Broad Health Check

When to use: Use this at the start of a shift, after deployments, or after maintenance.

Steps:

1. Open Admin Web and use **Dashboard** refresh.
2. Verify worker panel, workflow activity, approvals, and session health cards are populated.
3. Open **Workers** and confirm key services are active.
4. Run:
   1. `evander-cli status --runtime-dir runtime`
   2. `evander-cli status --runtime-dir runtime --json`
   The status output reports config load, runtime store access, required units, endpoint reachability,
   and active/expired session summary.
5. If needed for one service, run:
   1. `sudo systemctl status <worker-service>`
   2. `sudo journalctl -u <worker-service> -n 200`
6. If there is a mismatch, pause new changes and follow the incident path in section 21.

Expected result:

1. You have a short evidence list for each worker and can justify whether background automation is healthy.
2. Any stopped critical worker is flagged before operators lose visibility into workflows or approvals.
