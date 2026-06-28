# 17. Users, Groups, And Permissions

This section covers the identity and access layer operators use every day inside **Users & Groups**.
Use section 2 for sign-in basics, section 13 when Team chat access needs to be reviewed in
team workflows, and section 16 when directory configuration or session-wide ownership boundaries are
part of your change.

## 17.1 User Records

A user record is the base entry for Admin Web sign-in and permission assignment.

User fields usually include:

1. login identifier (email-style ID),
2. display name,
3. local password (only when using local password sign-in),
4. legacy role,
5. linked contact,
6. group assignments,
7. direct workflow grants,
8. effective directory access.

How these apply:

1. In local password mode, a local password is required before the user can sign in. In Azure SSO mode,
   admin users usually log in through the external identity flow and do not depend on the local password
   field.
2. Legacy role values are kept for compatibility, but day-to-day access control is usually managed through
   groups and direct grants.
3. You can assign a directory-scoped contact so messages and reports can be routed to the right person.
4. Removing a user from the directory clears their record from that scope; a removed user loses
   authenticated access on the next Admin Web request.

## 17.2 Groups

Groups let you assign repeated permission sets once and reuse them across users.

Users & Groups has two modes:

1. **Users** mode at `/#users`,
2. **Groups** mode at `/#users/groups`.

For each group you can define:

1. `id`: short stable group key,
2. display name,
3. optional description,
4. selected permissions.

Use directory-scoped group assignments so a user can have one core role in root and narrower additions in a
subdirectory. Directory assignments inherit downward, so keep high-level access in parent directories and avoid
copying the same grant into many child folders.

Editing rules:

1. `groups.manage` permission is required to create and change reusable groups.
2. `users.manage` permission is required to edit user records, assign user groups, and set direct workflow grants.
3. Users without those permissions can still use other pages based on their effective rights, but cannot
   perform these access-management mutations.

## 17.3 Workflow Grants

Workflow grants control who can open and read workflow records, and who can edit workflow content.

Two grant types are exposed to operators:

1. read access,
2. write access.

Practical rule:

1. write grants include read capabilities.
2. You can grant workflow access directly on a user, or via a group.
3. Direct user grants are useful for one-off exceptions.

Guidance:

1. Use groups first for common patterns (for example all operators in a directory).
2. Use direct grants when a single user needs extra access for a short period.
3. If a direct grant is temporary, remove it after the work closes to avoid over-permissioned accounts.

## 17.4 Team Chat Visibility

Team chat visibility is separated from normal workflow, issue, and session permissions.

1. By default, operators can inspect their own Team Add-on chat stream.
2. `team_chats.read_all` adds a read-only inspection mode for other operators' chats.
3. `team_chats.read_all` does not grant send rights in those chats.
4. The UI marks read-only inspection mode explicitly to prevent accidental posting.

Use this with caution. Any audit trail or review workflow that depends on chat visibility should be logged and
checked with section 7 and section 13 before escalation.

## 17.5 Access Reviews

Access reviews keep directory access tight and reduce surprise permissions.

Run periodic checks for:

1. users who no longer work in the directory,
2. users with broad workflow write grants,
3. users in too many groups for their role,
4. direct grants that were meant to be temporary,
5. legacy role-only accounts that still need explicit groups or direct grants,
6. team members carrying broad `team_chats.read_all` outside operational windows.

Use this pattern before big workflow changes:

1. confirm the directory,
2. unlock once for the review edits,
3. compare grants versus active responsibilities,
4. remove stale grants first,
5. only then run save,
6. document why any high-risk grant remains.

## How-to: Create A New User

Use when onboarding a person who needs direct Admin Web access.

Prerequisites:

1. You are signed in with enough privilege to open **Users & Groups**.
2. Privileged operations are unlocked.
3. Correct directory is selected.

Steps:

1. Open **Users & Groups** and select **Users**.
2. Click **New user**.
3. Enter login identifier and optional display name.
4. Set a local password when local-password authentication is active.
5. Choose a legacy role if required by your admin policy.
6. Optionally attach a directory contact.
7. Add group assignments and/or direct workflow read/write grants.
8. Click **Save**.
9. Confirm:
   1. the user appears in the list,
   2. the user can sign in with expected method,
   3. permissions appear as expected in workflow and directory tests.

Expected result:

1. A usable user record exists.
2. The user can perform only actions covered by their effective permissions.

## How-to: Create A Permission Group

Use this when multiple users need the same access set.

Prerequisites:

1. `groups.manage` permission.
2. Privileges unlocked.
3. Correct directory selected.

Steps:

1. Open **Users & Groups** and switch to **Groups** (or open `/#users/groups`).
2. Click **New group**.
3. Enter:
   1. group ID,
   2. group name,
   3. optional description.
4. Select the permissions this group should grant.
5. Save the group.
6. Return to **Users** mode and assign the group to one or more users.
7. Re-open one user to verify the new effective permissions are visible.

Expected result:

1. The group is reusable and can be assigned to many users.
2. Revoking the group later updates all assigned users consistently.

## How-to: Grant Workflow Access

Use this for short-term or role-specific workflow read/write permissions.

Prerequisites:

1. `users.manage` permission.
2. Target user is in the selected directory list.

Steps:

1. Open **Users & Groups** in **Users** mode.
2. Find and open the target user.
3. Add direct workflow grants.
4. Choose read or write:
   1. read for viewing runs and definitions,
   2. write for editing workflow definitions and publishing updates.
5. Note that write access includes read access.
6. Save and keep a short reason in your change notes.
7. Validate by checking a read path and a write path that the user needs.

Expected result:

1. The user gains the intended workflow capability.
2. Existing group grants remain active and add to the final permission set.

## How-to: Remove A User Safely

Use this when a user leaves the team or no longer needs Admin Web access.

Prerequisites:

1. Privileges unlocked.
2. Correct directory selected.
3. You have a replacement for any active responsibility.

Steps:

1. Open **Users & Groups** and locate the user.
2. Verify active work:
   1. open recent sessions, approvals, and workflow actions tied to that user role,
   2. confirm no active handoff depends on the user,
   3. close pending work in section 6 if needed.
3. Reassign direct tasks, workflow ownership, and group-based responsibility before deletion.
4. Remove direct workflow grants if this user may be restored later as inactive.
5. Delete the user record.
6. Confirm with a colleague sign-in test that the user no longer has valid Admin Web access.
7. Check section 7 for a final audit record of the removal.

Expected result:

1. The user can no longer authenticate in that scope.
2. Directory access is reduced without deleting unrelated users or groups.
3. Remaining workflow and team ownership is explicit and assigned.
