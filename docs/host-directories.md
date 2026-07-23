# Host Directories

Host Directories are directory-scoped, host-owned filesystem resources. They let an agent list,
import, or quarantine entries below one configured root without exposing that absolute root or
granting arbitrary host filesystem access. They are separate from email mailboxes, communication
targets, connectors, managed Git repositories, and guest workspace mounts.

## Trust boundary

An operator defines a stable resource ID and an absolute host root in the
`host_directories` directory-config domain. A session receives only explicit capabilities for
named resource IDs:

```json
{
  "host_directory_grants": {
    "accounting-inbox": ["list", "import"],
    "processed": ["list", "delete"]
  }
}
```

Granting a capability enables its dedicated MCP tool:

| Capability | MCP tool | Result |
|---|---|---|
| `list` | `host_directory_list` | Relative file/folder metadata and opaque revisions |
| `import` | `host_directory_import` | A session-owned communication-artifact handle |
| `delete` | `host_directory_delete` | A recoverable quarantine record |

The configured root is available only to host operators. Tool results, trigger events, session
manifests, and agent create-options contain the resource ID and relative paths, never the root.
Every tool call still passes the normal session tool-policy, argument-policy, approval, and audit
pipeline.

Path resolution is descriptor-relative and refuses traversal, backslashes, symlinks, special
files, and mount-boundary crossings. The configured root must be an absolute real directory and
cannot be a filesystem root such as `/`. Runtime health checks catch missing, symlinked, or
unreadable roots without making those paths visible to agents. When `delete` is enabled, health
also creates and removes a private probe below the reserved quarantine to verify that recoverable
deletes are actually writable.

## Directory configuration

Use Admin Web **Host Directories** or the supported directory-config API/CLI. The live file for a
directory is `runtime/directory-config/<directory-path>/host-directories.json`; do not hand-edit a
live file around the revision-locked Admin API.

```json
{
  "host_directories": {
    "accounting-inbox": {
      "display_name": "Accounting inbox",
      "description": "New documents awaiting classification.",
      "root": "/srv/evander/accounting-inbox",
      "capabilities": ["list", "import", "delete"],
      "delete_requires_approval": true,
      "max_import_bytes": 10485760,
      "max_import_entries": 1000,
      "max_watch_entries": 10000,
      "disabled": false
    }
  }
}
```

Definitions inherit by resource ID from `root` through the selected Evander directory. A child can
override individual fields or set `disabled: true` to make an inherited resource unavailable to
agents and triggers in that scope. Disabling does not destroy its quarantine; an owner can still
restore or purge existing records.

Limits are validated before use:

- `max_import_bytes`: 1 through 100 MiB; the total regular-file payload limit for one import.
- `max_import_entries`: 1 through 10,000; includes the imported folder itself.
- `max_watch_entries`: 1 through 100,000; bounds recursive listing, delete inspection, and trigger
  scans.

## Agent operations

`host_directory_list` accepts `directory_id`, an optional relative `path`, `recursive`, `limit`,
and an opaque pagination `cursor`. It returns only regular files and real directories. Each entry
has a relative path, kind, size when applicable, modification time, and revision. The quarantine
namespace `.evander-trash` at the configured root is reserved and never appears. A directory with
that name below another ordinary folder is normal content and remains visible and bounded by the
same limits.

`host_directory_import` imports one relative file or directory:

- A file is copied byte-for-byte into the host artifact store.
- A directory becomes a deterministic `tar.gz` with sorted entries and normalized ownership,
  permissions, and timestamps.
- The source is checked before and after reading. Size and entry limits fail the whole operation.
- The returned `comm_...` artifact belongs to the requesting session. Import does not mount the
  host root or silently write into the guest; a separately allowed artifact-copy operation is
  required when the bytes must enter the workspace.

`host_directory_delete` requires the exact revision returned by list/import. A non-empty directory
also requires `recursive: true`. Delete moves the entry atomically under the resource's reserved
`.evander-trash` directory and returns a `recovery_id`; it does not unlink the payload. When
`delete_requires_approval` is true (the default), the gateway creates a one-use, exact-payload
approval before the move. Operators restore or permanently purge records from Admin Web. V1 has
manual retention: no background process purges quarantine automatically.

The revision is an optimistic concurrency guard, not a cooperative filesystem lock. Evander
rechecks identity, mode, size, and modification time across the quarantine rename and rolls the
entry back when those values change. A hostile writer able to make a same-size in-place change and
restore the original modification time inside that narrow interval is outside this non-cooperative
filesystem guarantee.

Recovery writes an immutable scope marker, fsyncs control directories around moves, and reports
incomplete records instead of presenting them as healthy. A record whose payload is missing or
whose metadata is corrupt cannot be restored, but remains visible to the owning directory scope
and can be explicitly purged.

## File-added trigger

Use `trigger:host-directory:file-added` to react to new regular files through the existing trigger
orchestrator:

```json
{
  "id": "accounting-file-arrived",
  "type": "trigger:host-directory:file-added",
  "directory_id": "accounting-inbox",
  "path": "incoming",
  "recursive": true,
  "include_globs": ["**/*.pdf", "**/*.csv"],
  "exclude_globs": ["**/*.part"],
  "settle_seconds": 5
}
```

The trigger takes an initial baseline, so enabling it does not emit events for files that already
exist. A new file must have the same identity, size, and modification time for at least two scans
and for `settle_seconds` before it is eligible. Directories and symlinks do not emit events, and
ordinary content updates to an already known file are ignored. Pending events keep a stable ID
until durable publication is acknowledged. Replacing a known path without an observed absent scan
is also treated as an update; deleting it, observing the deletion, and adding it again creates a
new file-added event.

Events contain `directory_id`, `relative_path`, `size`, `modified_at`, `revision`, and
`detected_at`. Cursor state is stored at
`runtime/trigger-cursors-host-directories.json`. Scanning uses the trigger orchestrator's normal
poll interval; Host Directories add no daemon or guest-visible watcher.

## Operator recovery

In Admin Web:

1. Select the owning Evander directory and open **Host Directories**.
2. Add or edit a resource, unlock privileged operations, and save the revision-checked
   configuration.
3. Run **Check health** before granting it to sessions or selecting it in a workflow.
4. Use the structured Host Directory selector in Agent creation, Agent policy, or Sessions policy
   to grant only required capabilities.
   Reconciliation of an existing libvirt session adds the dedicated tools implied by those grants
   without removing its unrelated allowed tools.
5. Review **Quarantine recovery** for relative path, deleting session, time, and recovery ID.
6. Restore when the original destination is absent, or explicitly confirm permanent purge.

If a root is unavailable, repair the configured path or host permissions. Do not replace it with a
symlink, broaden it to `/`, expose it as a guest mount, or manually move quarantine payloads.
