# 14. Accounting And Tax Add-Ons

Accounting and tax add-ons are optional, assisted workflows for operations that depend on
external services. They let automation prepare bookkeeping and tax actions, while leaving production
submissions and spend decisions to operator review.

Use this section when you run or monitor:

1. bookkeeping prep in Bokio
2. tax filing drafts in Skatteverket
3. accounting teams that coordinate both from one workflow

For base add-on registration details, start with section 16. For approval flow mechanics, use
section 6. For evidence artifacts and follow-on review chains, use section 10. For team setup and
handoff conventions, use section 13.

## 14.1 Add-On Availability

Both add-ons exist only when enabled in host add-on loading. If the add-on is not loaded, its tab is
not shown in Admin Web.

What to expect:

1. Each add-on keeps its own navigation and runtime surface (for example `Bokio` and
   `Skatteverket` in Admin Web).
2. Each add-on owns its own persistent records separate from core session records.
3. Each add-on is backed by host-side credentials and service configuration.
4. Add-on actions are still enforced by normal tool policy and approval controls.

If a tab is missing:

1. Open **Configuration** and confirm the add-on is enabled for the active directory.
2. Confirm the workflow workers have been restarted after add-on configuration changes.
3. Refresh and re-open Admin Web.
4. If still missing, review host settings in section 16 and service restart guidance in
   `operations.md`.

## 14.2 Bokio Assisted Bookkeeping

The Bokio add-on is used for bookkeeping preparation and execution.

Read capabilities:

1. Company and chart reads, including journal entries and invoices.
2. Supplier invoice reads and document uploads through controlled staging paths.
3. Prepared actions for:
   - journal entries
   - supplier invoices
   - bank payments

Operational model:

1. Agents and workflows create prepared actions first.
2. A prepared action stays pending until approved.
3. Approved actions are available for production execution through the same policy and approval system.

Important safety boundaries:

1. `prepared_action.status` values are stateful, such as `pending_approval`, `approved`,
   `executed`, `rejected`, and `failed`.
2. Bank payment actions still require signatory confirmation in Bokio before bank submission.
3. Supplier invoice paths may rely on preview APIs and should be treated as less stable.

For quick operational checks, use section 6 if approvals are not moving and section 10 for action
artifact traceability.

## 14.3 Skatteverket Assisted Filing

The Skatteverket add-on is for tax filing preparation and submission support.

What it tracks:

1. Available catalog of supported Skatteverket APIs and enabled API names.
2. Connection objects and readiness fields (`configured` and `ready`).
3. Drafts and submission records with revision and status.

Typical assisted filing flow:

1. Create or validate a draft for a supported API.
2. Check `submission` API status and API-specific validation.
3. Request submission approval.
4. Submit only with a matching approved request.

Important constraints:

1. Some API catalog entries may show `blocked`/`not_implemented_for_v1` behavior depending on workflow
   coverage.
2. External onboarding is still required before production filing can work.
3. API endpoints, secrets, and certificate values are host-side and should remain out of shared config.

Use section 6 for human review and section 16 for environment variable and connection metadata setup.

## 14.4 Approval Boundaries

The core principle is operator control. No add-on is intended to bypass human sign-off for sensitive
actions.

Boundary rules:

1. Agents or workflows can prepare actions and drafts.
2. Agents cannot complete sensitive production steps without policy-compliant approvals.
3. Exact-request matching is enforced on approved requests so stale or mismatched approvals are not used.
4. Bank payment actions that are prepared in Bokio still need external signatory workflow.

Recommended operating pattern:

1. Review and approve only after comparing purpose, supporting references, and expected mapping.
2. Reject and request a fresh action if the mapping or payload changed.
3. Execute or submit only the latest approved version.

See section 6 for the exact review sequence and payload diagnostics.

## 14.5 Accounting Department Team Pattern

Use the Team Add-on accounting template when bookkeeping and tax filing should be coordinated by
named roles.

Pattern in practice:

1. The Team template creates ownership roles (for example accounting manager, bookkeeping lead,
   reviewer, and payment preparer).
2. Team reviews and tasks provide explicit ownership handoffs before live submission.
3. Drafts and submissions are recorded in dedicated add-on stores, while Team state tracks who owns
   each handoff.

Why this pattern helps:

1. It removes role ambiguity for who can prepare, who can review, and who can submit.
2. It prevents accidental cross-team ownership by keeping accounting operations in one Team domain.
3. It supports continuity: when one person is unavailable, the role owner can be reassigned in Team.

For Team setup, template behavior, and role permissions, see section 13. For communication targets and
notification behavior, see section 15.

## How-to: Check Whether Bokio Is Ready

Use this when Bokio action support should be active but appears unavailable.

Prerequisites:

1. Correct directory selected.
2. Access to Admin Web.

Steps:

1. Open **Bokio** from Admin Web.
2. Open **Refresh**.
3. Read the connection summary:
   1. `status` should indicate a configured and healthy setup.
   2. `company_id` should be present.
4. Open the **Company** panel and confirm a valid response.
5. Open **Prepared actions** and confirm recent actions list the expected `kind`.
6. If status is not ready:
   1. Compare host config with section 16 expectations.
2. Confirm the required Bokio company and integration-token settings are present in the host
      configuration.
   3. Restart the affected services only after configuration is corrected.

Expected result:

1. Company data is visible.
2. New actions appear with expected `pending_approval` or `approved` state.

## How-to: Review A Prepared Bokio Action

Use this when a prepared action is waiting, failing, or before execution.

Prerequisites:

1. Correct directory selected.
2. If you need to create an approval, privileges unlocked as required by your policy.

Steps:

1. Open **Bokio** and go to **Prepared actions**.
2. Find the action by `action_id` or most recent timestamp.
3. Review action metadata:
   1. `kind` (journal entry, supplier invoice, or bank payment)
   2. status
   3. payload note
4. Confirm supporting documents were uploaded to the intended scope.
5. Check mapped accounts and tax category matches expected policy.
6. If the action is `pending_approval`, open the matching approval in **Approvals**.
7. If approved:
   1. confirm account mapping still matches current rules
   2. execute through the normal approved action path used in your workflow
8. If status is `rejected` or `failed`, review the error/reason and request a corrected re-submission.

Expected result:

1. Only approved actions proceed to execution.
2. Failed actions are either corrected and re-prepared or explicitly blocked by operator decision.

## How-to: Check Skatteverket Connection Status

Use this when filing actions appear blocked or always fail validation.

Prerequisites:

1. Correct directory selected.
2. Access to the **Skatteverket** tab.

Steps:

1. Open **Skatteverket**.
2. Click **Refresh**.
3. Open **Connection status** and confirm:
   1. `configured` is true.
   2. the environment and connection id are expected.
   3. readiness flag is true after connection values are set.
4. Open **Connection settings** and review:
   1. enabled APIs
   2. environment
   3. token/OAuth/certificate settings
5. Open **Catalog** and confirm your target API name is listed and selected.
6. Open **Drafts** to confirm the draft status lifecycle for current work.

Troubleshooting:

1. If `configured` is false, check host-side secrets and section 16 config values.
2. If draft actions fail with unsupported status, confirm API coverage and disabled API operations.
3. If no connection is selected as default, set the intended connection as enabled.

Expected result:

1. A stable connection object is selected.
2. The catalog/API list includes your filing target.
3. Drafts and submissions move through validation and approval stages without unexpected blocks.

## How-to: Review And Submit An Approved Tax Draft

Use this when a Skatteverket draft has been prepared and approved for submission.

Prerequisites:

1. Matching approval exists in **Approvals** with matching scope.
2. Unlock as required for approval-dependent operator tasks.

Steps:

1. Open **Skatteverket** and review **Drafts**.
2. Select the target draft and confirm:
   1. `api_name`
   2. `status` is `validated`
   3. revision appears current
3. If validation failed, return to draft validation before submission.
4. Open the approval path in **Approvals** and confirm:
   1. decision is approved
   2. operator identity is expected
   3. request context matches this draft revision
5. Submit the draft only once with the matching approved request.
6. Open **Submissions** and confirm:
   1. a `submission_id` is present
   2. draft and submission status align
   3. result details are recorded
7. Link the submission record to the task or ticket and add a short operator note.

Expected result:

1. Draft status moves to a submitted outcome.
2. A submission record exists with traceable status and IDs.
3. Operator evidence is available for review and handoff.
