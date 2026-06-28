# 2. Getting Started

This section gets you into Evander quickly and helps you orient yourself before using sensitive actions.

## 2.1 Open Evander

For most installs, Evander runs on your operator machine at:

`http://127.0.0.1:8100/`

If you were told a different address, use that URL instead.

1. Open the URL in a browser that has your normal workstation access.
2. If a sign-in screen appears, complete it as described in section 2.2.
3. After a successful sign-in, you should land on the Dashboard.
4. Confirm the page shows live status cards and not obvious errors.

What to expect first:

- A left navigation menu for the main operator pages.
- A shared top header that shows the current directory, lock state, and selected user.
- A Dashboard that gives an operator-level snapshot.

If the page does not load, treat it as a potential runtime issue and note the page URL, time, and any browser error before moving on. Then use the health-check guidance in section 21.

## 2.2 Sign In And Sign Out

Evander can be used with one of three sign-in modes, depending on how your host is configured.

### Local sign-in

- You sign in using a local username and password.
- The browser session is managed by Evander cookies.

### Microsoft sign-in

- You are redirected to an Entra sign-in flow.
- Complete sign-in with your approved work account.

### No sign-in mode

- Some trusted local deployments use no sign-in gate.
- If you are unexpectedly sent to a sign-in screen, ask an administrator to confirm the deployment settings.

How to handle common sign-in outcomes:

1. If login is accepted, proceed to section 2.3 and check the Dashboard state.
2. If login is rejected:
   - recheck your username, password, and account status;
   - retry once;
   - if it still fails, contact an administrator and avoid guessing credentials.
3. If your session expires while you are working:
   - re-authenticate at the same screen;
   - reopen the page you were using after login.
4. To sign out, use the sign-out action in the header.

## 2.3 Understand The Main Screen

Once signed in, the shared shell is the same across pages so you can move between sections quickly.

1. Header: shows active section, directory, current user, theme, privilege lock, directory chooser, and sign-out.
2. Navigation: opens all major operator pages such as Dashboard, Agents, Sessions, Approvals, Tool Calls, Workflow, Configuration, Users, Reports, and Workers.
3. Directory scope: shown in the header and applied to many lists and actions.
4. Privilege lock icon/state: `locked` means read-only mode. Mutations are blocked until you unlock.
5. Deployment banner: if present, gives platform-level health or update context.
6. Assistant launcher: optional lower-corner tool when enabled by your deployment.
7. Page content: the page you selected in navigation.

Tip for first sessions: stay on Dashboard first (section 3) and use it to verify that data is appearing as expected before editing anything.

## 2.4 Choose The Right Directory

Directory selection controls the scope for team, tenant, environment, product, or project-facing operations.

1. Click **Choose directory** in the header.
2. Browse the directory tree and choose the scope you are supposed to operate in.
3. Confirm the selection appears in the header.
4. Perform your task in that directory.
5. When you switch, the following pages usually change what they show:
   - Agents
   - Sessions
   - Issues
   - Approvals
   - Workflow
   - Tool Calls
   - Configuration
   - Users

Why this matters:

- Teams and environments can look similar but hold different active work.
- You can accidentally read or approve the wrong work if the directory is wrong.
- The same action name can affect different records in another directory.

If you are uncertain, pause and recheck in section 2.4 before creating agents, changing policies, or approving requests.

## 2.5 Unlock Privileged Operations

Evander uses a deliberate safety gate before changes.

When the lock is closed, the interface is in read-only mode.

- Safe for review, triage, filtering, and inspection.
- Not safe to run changes through.

When unlocked, privileged actions become available, including:

- creating, starting, stopping, and deleting agents and VMs;
- approving or denying approval requests;
- saving configuration;
- editing sessions and workflow items;
- changing directory-scoped policy-like state.

Use the lock deliberately:

1. Unlock only when you are ready to make a real change.
2. Complete and save your changes quickly.
3. Lock again immediately after completion.
4. Leave long-running windows unlocked only when absolutely needed.

## How-to: Open Evander For The First Time

1. Open the Evander URL for your deployment.
2. Sign in using the method your environment requires (local password or Microsoft).
3. Confirm your user name appears in the header and note the active directory.
4. Check the header for `locked` and the Dashboard for a live read.
5. Read the Dashboard summary once, then open a second page you will work on next.

Expected result:

- You are signed in and have a baseline understanding of current system status.

If the screen stays blank or shows nothing useful:

1. Capture the URL and current directory shown in the header.
2. Try a browser refresh.
3. Check for sign-in state issues.
4. Use the sign-out/in cycle if needed before moving to troubleshooting.

## How-to: Switch To A Team Or Project Directory

1. Click **Choose directory** in the top header.
2. Open the team/product branch of the directory tree.
3. Select the target directory.
4. Confirm the new directory name appears in the header.
5. Open one page tied to that scope, such as Sessions or Agents, and verify records match the target team/project.

Expected result:

- Lists, approvals, and policies reflect the selected scope.

Common recovery:

1. If the wrong records are still visible, return to directory chooser and reselect.
2. If selection does not persist, refresh once and reselect.
3. If you still cannot change directory, stop and escalate using section 21.

## How-to: Safely Unlock And Relock Changes

1. Verify you are in the correct directory.
2. Click **Unlock** in the header.
3. Perform one focused set of changes only.
4. Save when the save action becomes available.
5. Confirm the result on the page or by opening the related linked page.
6. Click **Lock** as soon as changes are complete.

When to unlock:

- creating or stopping agents;
- approving, denying, or revising approvals;
- editing workflow or configuration items;
- deleting records.

When not to unlock:

- during initial browsing of unrelated pages;
- when validating only;
- when you are uncertain about directory scope.

Result:

- The system returns to read-only mode and reduces accidental edits.

After you finish, continue to:
- Section 3 for day-to-day monitoring habits,
- Section 3.2 for refresh and staleness handling,
- Section 6 for review workflows if you unlocked for approvals,
- Section 16 for safe configuration changes.
