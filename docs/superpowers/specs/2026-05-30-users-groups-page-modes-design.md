# Users & Groups Page Modes Design

## Status

Implemented on 2026-05-30. The approved direction kept the existing Admin navigation item as
**Users & Groups**, but split the
page into two clear top-level modes: **Users** and **Groups**.

## Problem

The current Users & Groups page mixes user selection, user editing, group selection, and group
editing in one sidebar/content flow. This makes the page hard to scan because operators must keep
two different task models in mind at the same time:

- managing user records and their access assignments
- managing reusable permission groups and their permission catalog entries

The page should preserve the existing capability set while making the active task explicit.

## Chosen Approach

Add a top-level tab control inside `UsersPage` with two modes:

- **Users** mode
- **Groups** mode

This keeps the global Admin tab count stable and avoids a route/API reshuffle, while still giving
operators a clear page-level separation between user management and group management.

The selected mode must also be addressable from the URL. The Admin shell currently uses hash-based
navigation, so the mode should be represented as a path-like hash segment:

- `/#users` or `/#users/users` opens Users mode
- `/#users/groups` opens Groups mode

This gives operators a direct link to Groups mode without promoting Groups to a separate global
Admin navigation item.

## Users Mode

Users mode owns all user-focused UI:

- the user list/sidebar
- **New user**
- refresh
- selected user title and delete action
- user identity form
- access assignments
- effective permission summary
- user records table

The group assignment editor may still reference groups in selects because group assignments are part
of a user record. It must not show the group list or group editor in this mode.

## Groups Mode

Groups mode owns all group-focused UI:

- the group list/sidebar
- **New group**
- refresh
- selected group title and delete action for non-required groups
- group metadata form
- permission catalog checkbox grid

User-specific panels and the user records table are hidden in this mode.

## State And Data Flow

Keep state local to `UsersPage`.

- Add `activeMode` state with values `"users"` and `"groups"`, initialized from the Users hash
  subpath.
- Preserve `selectedLogin` and `selectedGroupId` independently so switching modes does not discard
  the operator's current selection.
- Keep using the existing `/api/admin/users?directory_path=...` load because it already returns
  users, groups, contacts, and permission catalog data needed by both modes.
- Keep existing user and group mutation APIs unchanged.
- `beginNewUser()` should switch to Users mode and update the hash to the Users-mode URL.
- `beginNewGroup()` should switch to Groups mode and update the hash to `#users/groups`.
- Changing the in-page Users/Groups tabs should update `window.location.hash` so the browser URL,
  back/forward behavior, and reload behavior all preserve the chosen mode.

## Routing

Update Admin hash parsing so `#users/groups` still resolves to the existing global `users` Admin
tab. Invalid Users subpaths should fall back to Users mode rather than showing a blank or unknown
Admin tab.

Do not add a separate global Admin navigation entry for Groups.

## Error Handling

Use the existing loading, status, error, and saving state.

- Mode switches should clear transient status/error text only when caused by an explicit selection or
  create action, matching current page behavior.
- Permission-denied state should remain a single warning for the whole page.
- Empty states should be mode-specific: no users in Users mode, no groups in Groups mode.

## Testing

Add a focused frontend test for the structural split. The test should verify:

- Users mode contains user-facing controls and does not render the group editor.
- Groups mode contains group-facing controls and does not render user edit panels.
- `#users/groups` resolves to the Users & Groups Admin tab with Groups mode selected.
- Switching modes updates the hash so reloads and direct links preserve the selected mode.
- Existing permission help tooltip usage remains present for group permissions and effective user
  permissions.

Implementation can keep the current source-inspection style if that is the established local
pattern for this page, but behavioral rendering coverage is preferable if a lightweight harness
already exists nearby.

## Out Of Scope

- API schema changes
- RBAC semantics changes
- separate global Admin navigation entries for Users and Groups
- permission catalog content changes beyond preserving existing help text
- broad visual restyling outside the Users & Groups page
