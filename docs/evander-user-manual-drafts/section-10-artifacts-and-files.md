# 10. Artifacts And Files

Artifacts are how Evander preserves important outputs from agent and workflow work.
Use this section to find, download, upload, and verify those outputs.

For workflow execution context, see section 11. For who requested or executed the actions that created an artifact, see section 7. For Team deliverable handoff, see section 13. For file transfer approvals and send limits, see section 15.

## 10.1 What Artifacts Are

For operators, an artifact is a durable package of one or more files that is stored for later use.

Use this concept in daily operations:

1. A session or workflow publishes finished outputs as an archived package.
2. The package gets a stable artifact key.
3. You can retrieve it later from the Artifacts tab.

Artifacts are different from:

1. temporary runtime files in an active VM or container session,
2. session logs and Tool Calls,
3. notifications and chat messages.

If a file should survive beyond a live session and be replayed later, it belongs in the artifact store.

## 10.2 Artifact Keys

An artifact key is the stable identifier used to find a published package later.

You usually see keys in:

1. workflow run outputs,
2. Tool Call metadata,
3. Team deliverable details,
4. manual uploads.

Use keys as your first lookup target when you already know the reference.

Safe key use:

1. Use one meaningful naming style per team.
2. Keep keys unique.
3. Prefer existing keys from source records over guessing.

If two processes use the same key, the result can become confusing to audit later, even when uploads succeed.

## 10.3 Resolving And Downloading

Use the Artifacts tab when you need to get a previously published package.

1. Open **Artifacts** from the left navigation.
2. Remember that the published archive list is not filtered by the directory in the shared header.
3. If you know the key, enter it and choose **Resolve key**.
4. If you do not know the exact key, filter by time range and result limit instead.
5. Select a row and open its metadata panel to check:
   - session id,
   - backend type,
   - file count,
   - byte count,
   - SHA-256,
   - source artifacts root.
6. Use **Download** to save the archive to your local machine.

Expected result:

1. You get a `.tar.gz` archive matching the selected row.
2. The filename and metadata align with the source context.

If nothing resolves:

1. Re-check the key for typos and case mismatch.
2. Use a wider published-time window in the filter.
3. Trace the run or deliverable where the key should have been written and verify it was published.
4. If the reference is an artifact-space reference rather than a published archive key, verify its subject and grants. Artifact-space access is not determined by the selected directory alone.

## 10.4 Manual Uploads

Use manual upload when an operator or external tool needs a file ready for future workflow steps.

1. Open **Artifacts**.
2. Click **Upload** (or equivalent upload action in the same tab).
3. Enter a unique artifact key for later references.
4. Select the local file.
5. Confirm upload.

Expected result:

1. A new artifact archive appears in the list.
2. Its key is immediately searchable and reusable by workflows.

Use this when:

1. You want to provide templates, test fixtures, or baseline docs.
2. You need a file ready before a workflow starts.
3. You need a deterministic input to a repeatable run.

If upload is blocked:

1. Confirm you have the privileges needed for mutation actions.
2. Confirm the file type or size is acceptable in your deployment policy.
3. Try a different artifact key if one already exists and you intended to create a new entry.

## 10.5 Artifact Safety

Treat artifacts as reusable evidence, not as automatically trusted content.

Before you open or reuse a package:

1. Check the source session and backend.
2. Verify the publish time matches expected work timing.
3. Confirm file count and byte count look reasonable for the known output.
4. Compare SHA-256 when you need strong integrity checks.

Do not use any artifact blindly for production output or external sharing.
Before sensitive reuse:

1. Confirm the artifact belongs to the correct session or workflow.
2. Confirm policy and approval expectations from section 15 if the artifact is being shared.
3. Confirm no approval bypass happened by linking to section 7 for the full call trail.

If a hash or metadata looks wrong:

1. Pause handoff or publication actions using this artifact.
2. Identify the producing session/workflow.
3. Re-open the source record and trace with section 7 or section 11 run inspection before deciding to continue.

## How-to: Download A Published Artifact

Use this when you already have a reference and need the exact package locally.

Prerequisites:

1. Correct artifact key, or ability to narrow by time range.
2. Enough source context to distinguish similarly named artifacts.

Steps:

1. Open **Artifacts**.
2. Enter the key and click **Resolve key**, or filter by publish window.
3. Select the target row.
4. Confirm metadata (session id, file count, byte count, SHA-256).
5. Click **Download**.
6. Save the archive and verify the filename is what you expected.

Expected result:

1. Download begins immediately for the selected artifact.
2. Saved archive includes metadata that matches the selected row.

If download fails:

1. Confirm network access and local browser session.
2. Retry after reloading the page.
3. Resolve from source workflow/session again and verify the key still exists.

## How-to: Upload A File For Later Workflow Use

Use this when you need to stage a file before a workflow starts.

Prerequisites:

1. Correct directory selected.
2. Mutation permission unlocked, if your environment requires it.
3. A unique artifact key ready.

Steps:

1. Open **Artifacts**.
2. Click **Upload**.
3. Enter a new artifact key.
4. Choose the file from local disk.
5. Click **Upload**.
6. Wait for the new row to appear in the list.
7. Open the new row and verify key, size, and source artifact metadata.

Expected result:

1. The file is available as a published artifact package.
2. The key can be reused by future workflow operations.

If upload does not create a row:

1. Retry with a different key.
2. Reduce file size or retry with a non-binary file if policy may block type.
3. Verify you still have permission to perform upload actions.

## How-to: Trace An Artifact Back To Work

Use this when you need the producing owner, session, or workflow for an artifact.

Steps:

1. Open the artifact row in the Artifacts tab.
2. Record the artifact key and session/metadata fields.
3. Open the linked session record (or search by session id in **Sessions**).
4. Follow the workflow run link if the artifact came from automation.
5. Open Tool Calls for the same session window and compare request IDs.
6. If this artifact belongs to Team output, open Team deliverables for that session or case.
7. Validate the chain from creation to publish before making handoff decisions.

Expected result:

1. A readable chain that answers who produced, where it was produced, and when.

If links are incomplete:

1. Re-check the source record's directory and time range in section 7 records; the published artifact list itself is not directory-filtered.
2. Confirm the artifact key did not come from another active run.
3. For artifact-space references, verify the subject and grants.
4. Review Team or delivery context in section 13 for alternative attribution.
