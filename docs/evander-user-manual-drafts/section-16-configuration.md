# 16. Configuration

This section helps you control what Evander is allowed to do and how it behaves for a selected directory.

Use this section before changing any setting that can affect message routing, approvals, wake signals, session launch, model routing, or host-visible UI behavior.
For session-level policy details, see section 5. For approval implications, see section 6. For message channels, see section 15.
For who can edit configuration, see section 17. For worker-level service impact, see section 18.

## 16.1 Configuration Domains

Think of configuration as grouped into domains that each control a different operational surface.

1. Policy controls common safety defaults such as session expiry and approval behavior.
2. Providers control integrations like OpenAI routing, NuGet, GitHub, Azure DevOps, Teams, and container worker settings.
3. Contacts and communication targets define who appears in provider actions and who is reachable.
4. Email covers mailbox definitions and mailbox runtime behavior.
5. Wake covers wake prompts used by scheduled and PR-based resume paths.
6. ADO worker settings control repository and feedback poll behavior for Azure DevOps completion and feedback streams.
7. Issue setup controls issue workflow defaults such as git host, git user, and model fallback behavior.
8. VM controls relay and network defaults and the VM template selection catalog.
9. PR template defines the standard markdown that can be prepended when automation writes pull requests.
10. Model pricing controls local usage estimate rates and labels used in usage reporting.
11. Admin Web branding controls browser title, header text, and logo presentation.

Add-on loader settings are not edited in the selected directory's Configuration page. Optional tabs
and add-on workflow provider blocks come from host `addon_loader` configuration and worker
configuration loaded when Admin Web, the trigger orchestrator, and the effect orchestrator start or
reload. If an add-on tab is missing, check host add-on configuration and restart or reload the
affected services after correcting it.

The system uses inherited values:

1. `root` provides the baseline.
2. Parent directories add overrides.
3. The selected directory can add its own override.
4. The live effective value is what the system and operators will actually use.

Root is the starting point for rollout, and subdirectories can tighten or change behavior for one team or product without affecting siblings.

Directory-scoped editing is only available when directory-scoped configuration is enabled in host settings. If that switch is off, this tab may still show values but editing will remain disabled.

## 16.2 Effective Values And Overrides

The most important operator question is: what value is actually used right now?

1. The Configuration page shows **effective values** first.
2. It also shows where values came from: local override versus parent defaults.
3. In many cases, each row has a raw override view so you can confirm the exact data being saved.
4. Some fields are inherited by default and remain editable only when you intentionally set a local override.

Use this pattern:

1. Open Effective view and confirm inherited values for context.
2. Compare against the directory override view for the live changes already stored here.
3. Make only the specific edits needed for your use case.
4. Save and verify that the effective value changed as expected.

For operator-safe troubleshooting, take both:

1. A screenshot of the effective view for evidence.
2. The override JSON copy for diffing with peer operators.

If something is rejected at save time, use the validation message as the first clue before changing a second field.

## 16.3 Safe Save Behavior

Evander uses revision checks to avoid silent overwrite.

1. A save can fail if another operator changed the same configuration in between your edit and save.
2. In that case, the UI asks you to reload and reapply your intended edits.
3. Communication or high-risk edits should be done one domain at a time.
4. Locking, unlocking, and privilege mode are still required for writes.
5. Keep local edits short and re-open read-only if a page feels stale.

Safe save habits:

1. Refresh after selecting a directory.
2. Open only one domain section at a time.
3. Edit the smallest possible set of fields.
4. Save.
5. Confirm the effective value in the read result.
6. If the save fails, reload first, then reapply.

If your team changes settings in parallel, avoid copying files from a stale local checkout. The UI and save path are designed to coordinate revisions and protect concurrent edits.

## 16.4 Teams Delegated Auth

Teams access in this deployment is delegated host-side auth.

1. Teams credentials are not placed into guest VMs.
2. Each directory can have its own delegated identity binding.
3. Child directories inherit binding from parent unless they define their own.
4. You can start/refresh/sign out auth from Configuration.
5. Teams ingest paths depend on these credentials for channel and chat reads/writes.

The expected operator flow is:

1. Keep directory and communication provider settings correct.
2. Check the Teams delegated auth status in Configuration.
3. Start device-code sign-in and complete the browser step.
4. Refresh status and verify it shows active.
5. If the app registration changed scopes, sign out and sign back in.

Use session allowlist and target settings from section 15 alongside this section to ensure directory policy and channel/user lists match the authenticated identity.

## 16.5 Model Pricing And Usage

Evander stores a local model pricing catalog for cost estimates used by usage queries.

1. Estimates are operational, not final bill data.
2. The estimates use the loaded catalog file, model name, and reported token metadata.
3. Missing or custom pricing data can make cost values stale or incomplete.
4. Billing backends may include regional or account adjustments that are not inferred here.

Operator guidance:

1. Keep the catalog current when provider rates change.
2. Prefer minimal edits to only the affected rules.
3. Confirm the active catalog path before editing.
4. Keep currency and rule format consistent.

For report workflow and history after edits, use the usage and report sections for follow-up checks.

## 16.6 Branding And Admin Web Settings

Branding changes are global host-side UI settings, not per-directory controls.

1. Browser title and shell header title.
2. Header logo size and logo assets.
3. Sign-in bar and company logo assets.
4. Optional relative logo paths through the built-in branding asset route.

Most branding changes are applied through `config/admin-web.env` values in the `EVANDER_ADMIN_WEB_*` family.

After changing these values:

1. Save the file.
2. Restart or reload `evander-admin-web.service`.
3. Refresh browser tabs and confirm the header and sign-in branding.

If a deployment is exposed beyond localhost, confirm TLS and authentication policy as part of branding rollout.

## How-to: Update Directory Configuration Safely

Use this when you must change one directory-specific setting and keep blast radius small.

Prerequisites:

1. Correct directory selected.
2. Privileged operations unlocked.
3. You know whether the change is policy, provider, or communication related.

Steps:

1. Open **Configuration**.
2. Open the domain you need (policy, provider, wake, etc.).
3. Read **Effective config** and **Override JSON**.
4. Compare current parent values with directory override values.
5. Edit only the minimal fields needed for this task.
6. Save using the current config toolbar state.
7. If save reports a conflict, reopen the section and reload.
8. Recheck effective values and confirm the target behavior changed only in the selected directory.

Expected result:

1. The live effective value changes where intended.
2. No unrelated domain is changed.
3. A second operator can still continue with their own domain edits.

## How-to: Copy Effective Configuration For Support

Use this when support asks for live values during an incident.

Prerequisites:

1. Correct directory selected.
2. Read access to Configuration.
3. You can share operator evidence externally.

Steps:

1. Open **Configuration** and switch to the target directory.
2. Open the domain and copy the **effective config** view.
3. Copy the **directory override JSON** view immediately after.
4. Redact any host-only secret names, tokens, or local token names before sharing.
5. If needed, export status from:
   1. `evander-cli directories config-show --runtime-dir runtime --domain <domain> <directory>`
   2. `evander-cli status --runtime-dir runtime --json`
6. Send both a brief context note: operator action taken, issue id, and observed error/timeout.

Expected result:

1. Support can compare live effective values to overrides without guessing what is inherited.
2. You avoid exposing secrets while still sharing enough data to troubleshoot.

If output is still ambiguous, ask support for the same section from the same timestamp after a fresh page reload.

## How-to: Refresh Microsoft Teams Delegated Auth

Use this when Teams actions are not working, or after App ID/scope changes.

Prerequisites:

1. Directory provider binding is set in provider config.
2. Teams delegated auth is available in Configuration for that directory.
3. Access to the browser for device-code login.

Steps:

1. Open **Configuration** and confirm the target directory.
2. Open the Teams delegation area.
3. Read the current auth status; if `not signed in` or expired, use **Sign in** or **Start sign in**.
4. Complete the device-code flow in the browser tab.
5. Return and click **Refresh** to confirm signed-in status updates.
6. Run a narrow non-critical operator check that uses Teams (for example a low-impact communication target or inbound target check).
7. If scopes changed or status remains stale, use sign out and sign in again.
8. If UI flow is blocked, use CLI fallback:
   1. `evander-cli teams-auth status --directory <directory-path>`
   2. `evander-cli teams-auth login --directory <directory-path>`

Expected result:

1. Teams delegated status shows active for the intended identity.
2. Ingest and notification flows return to expected behavior without broad resets.

If auth repeatedly fails with tenant/client errors, check provider app registration and flow type in section 16.

## How-to: Adjust Model Pricing Data

Use this when the usage cost panel is showing outdated or suspicious model estimates.

Prerequisites:

1. Privileged configuration access.
2. Correct directory selected.
3. Agreement on whether to update full catalog or only a targeted rule.

Steps:

1. Open **Configuration**.
2. Go to the model pricing catalog editor.
3. Review active path and source (default file or overridden path).
4. Edit only the needed rules (rate values, rule match, currency label).
5. Run a quick validation pass in the editor before saving.
6. Save and resolve any stale-revision warning by reloading first.
7. Re-open a recent usage query to verify new estimate totals.

Expected result:

1. New rates are loaded and visible in pricing metadata.
2. Cost estimates move in line with new pricing values.

If no estimate changes appear immediately, confirm the same catalog path is still active for usage query runtime.
