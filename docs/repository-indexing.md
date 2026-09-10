# Repository indexing

Repository indexes provide text search, optional semantic search, and C#/TypeScript
symbol navigation for an exact managed Git commit. Agents can also reuse an indexed
ancestor for unchanged files in their assigned commit, with explicit provenance.
Indexes are portable: export a
snapshot and query it without an Evander installation, gateway, worker, or Qdrant server.

Indexing is disabled by default. Installing this feature does not enable any repository,
import a workflow pack, or deploy an indexing image.

## Prepare an indexing worker

Build the normal runner image first, then run:

```bash
scripts/build_code_index_image.sh
```

The build script requires the host's .NET SDK 10.0.111, Node 24.20.0 and Evander's
Python 3.12 environment. It downloads/builds tools on the host, stages their runtime
dependencies, builds Docker with `--network=none`, and saves
`dist/evander-code-index.tar`, an image ID sidecar, and a toolchain manifest.
`--help` lists input and output overrides.

The image pins scip-dotnet 0.2.15 to source commit
`47884461a79839fb74c99e6a0a7978cd7eb62476`; that version is built from source because
the published 0.2.14 package uses older Roslyn APIs. scip-typescript 0.4.0 and
TypeScript 5.9.3 use the checked-in npm lockfile. Update the image, `TOOLCHAIN` in
`src/evander/code_index/models.py`, and relevant fixtures together when upgrading.

Add the archive to the worker's existing `evander_runner_image_archives` Ansible list:

```yaml
- image_name: evander-code-index:1
  archive_path: "{{ playbook_dir }}/../dist/evander-code-index.tar"
  remote_archive_path: "{{ evander_worker_root }}/images/evander-code-index.tar"
  build_command: scripts/build_code_index_image.sh
```

Keep the worker's other archive entries. Use the existing worker playbook to deploy
the image. Add `code-index: evander-code-index:1` to the selected named worker's
`container_workers.<worker-id>.images` map. For the legacy single-worker configuration,
add it to `container_worker_images`:

```json
{"container_worker_images": {"code-index": "evander-code-index:1"}}
```

Add that entry alongside the existing aliases. The repository's `image` setting selects
the alias (default `code-index`); session provisioning resolves it and the compiler job
uses the returned Docker image reference. Update older profiles that specify
`evander-code-index:1` to use the approved alias.
No new service or firewall port is needed. Indexing sessions use
`container_worker_vm`, read-only managed Git access, no MCP tools, and the existing
package relays/caches. Builds run in disposable session workspaces with 8 GiB memory,
one CPU by default (`worker_cpus`, configurable from 0.1 to 4), and a 1,024-process
limit. Only one compiler indexing job can run across the control-plane runtime,
including jobs for other repositories/directories. A pending submission keeps that
slot reserved while its outcome is uncertain; retries adopt the same worker job.
Provisioning failures before compiler submission do not reserve this slot. Cancellation
waits for the current reconciliation pass, then confirms shutdown before releasing
capacity. Indexing sessions bootstrap NuGet with the configured host relay.
If retries fail and stopping the job cannot be confirmed, other compiler builds stay
queued. Restore worker connectivity and cancel that failed build to confirm shutdown
and release the slot.
Repository targets, analyzers, source generators
and npm installation run there; the control plane only verifies and imports data.

## Configure and operate

Open **Managed Git → repository → Repository indexing** in Admin Web. Directory
owners can view coverage and export published snapshots. Unlock privileged operations
to change settings, build, rebuild or cancel. Settings saves require the revision
shown by the last load; concurrent edits return HTTP 409.

`config/repository-indexing/app.json.example` is an opt-in APP profile. Set its worker
ID if the directory has no appropriate default. The profile discovers all tracked
`.csproj` files and lists the APP frontend configurations explicitly. Its timeout is
one hour. APP's frontend lockfiles are currently untracked, so the profile explicitly
allows npm to resolve dependencies when a lock is missing. Resulting lock hashes are
recorded per project. Retain those worker lockfiles to reproduce that resolution.
The general default requires a package lock. A repository containing only an unrelated
root lockfile does not satisfy a nested package's dependencies. npm workspace members
share their owning root's restore and lockfile; membership is resolved by npm.

Semantic indexing uses the owning directory's existing semantic provider settings:
`semantic_embedding_provider`, `semantic_embedding_model`,
`semantic_embedding_dimensions`, `semantic_vector_distance`, `openai_api_base_url`,
`openai_api_key_env`, `qdrant_base_url` and `qdrant_api_key_env`. Credentials stay on
the control plane. Disable semantic indexing to use text and symbol search alone.

Import `config/workflow-packs/repository-indexing.v1-example.json` through the workflow
editor into the repository's owning directory, or an authorized ancestor. Its minute
tick calls `effect:code-index:reconcile` through the existing effect orchestrator.
The reconciler detects changes to locally refreshed managed Git refs; the normal
managed Git refresh paths remain responsible for fetching upstream changes. The
managed Git refresh worker is an on-demand broker, not an upstream polling schedule.
Fetching must be triggered separately; a quiet mirror can remain stale.

Automatic builds default to a minimum interval of one hour
(`refresh_interval_seconds=3600`, allowed range 300..604800). While a repository has
an active build, or its most recent build request/start is inside that interval,
new branch heads do not queue more builds. When eligible, reconciliation reads the
current branch head and skips intermediate commits. Manual **Build index** / **Rebuild
index**, CLI ensure and explicit workflow ensure effects can request a particular
commit outside the automatic interval; the single-compiler limit still applies.
Disable `refresh_on_git_change` to use only explicitly requested builds, for example
from an overnight workflow. Compiler passes still analyze the full selected snapshot;
lower CPU limits can increase their duration.

Team preparation, index queries, guest commits and guest pushes never request a full
compiler index. The former `index_task_commits` setting is accepted for older profiles
but ignored. No branch-specific vector projection is needed to query a baseline.

Operator CLI equivalents:

```bash
evander-cli code-index status REPO --directory-path root/app
evander-cli code-index configure REPO settings.json REVISION --directory-path root/app
evander-cli code-index ensure REPO --commit-sha SHA --directory-path root/app
evander-cli code-index reconcile --directory-path root/app
evander-cli code-index ensure REPO --commit-sha SHA --rebuild --directory-path root/app
evander-cli code-index cancel REPO BUILD_ID --directory-path root/app
evander-cli code-index search REPO SHA 'durable event retry' --directory-path root/app
evander-cli code-index navigate REPO SHA SYMBOL --kind references --directory-path root/app
evander-cli code-index search REPO SHA 'durable event retry' --allow-baseline --directory-path root/app
```

Use `--config /path/to/runtime` for another runtime. `ensure` queues or reuses a build;
`reconcile` advances a bounded pass. Failed/cancelled builds need an explicit rebuild
and do not create an endless retry loop. A failed rebuild preserves the previous
published snapshot with the same source policy.

Workflow effects are `effect:code-index:ensure` and `effect:code-index:reconcile`.
Queries are `query:code-index:status`, `query:code-index:search`, and
`query:code-index:navigate`. Search/navigation require a target `commit_sha` and
default to exact coverage; set `allow_baseline=true` to reuse an indexed ancestor.
Status with a SHA includes `available_index` alongside the exact build history.
Nodes cannot elevate their workflow's directory scope.

## Agent access and coverage

The gateway exposes `code_index_status`, `code_search` and `code_navigate`. A session
needs the named tool and a current managed Git grant for the repository. Ordinary
sessions supply an exact commit. Team sessions are additionally restricted to the
active dispatch's persisted task Git contexts and frozen review snapshots; a unique
binding supplies the SHA when omitted. Completed/expired dispatches lose that scope.
Ephemeral agents use server-derived task bindings frozen when requested.

Team preparation only reads existing coverage and includes its provenance in the
compact wake context. Agent tools default to `allow_baseline=true`. If the assigned
commit has no published index, the service searches at most 32 recently published,
policy-compatible snapshots for a proven Git ancestor, within a two-second ancestry
budget. Missing objects, unrelated history, expired scope or incompatible policy
cannot widen access. Set `allow_baseline=false` to require an exact index.

For baseline results, `commit_sha` is the indexed ancestor, `requested_commit_sha` is
the authorized task/review commit, and `match` is `baseline`. Git tree differences
exclude modified, deleted and renamed paths before returning search results. New
files have no baseline entries. Navigation also excludes changed paths, but its
relationships describe the indexed ancestor: a dependency change can alter symbol
bindings in otherwise unchanged files. Responses explicitly warn about that limit.
Baseline navigation is bounded to 1,000 candidates before filtering/pagination.

Agents search new, changed and dirty code using local `rg` and the Git diff. This is
baseline reuse, not a branch overlay or incremental compiler analysis. After a local
commit, an agent's assigned snapshot remains its query scope; its own edits require
local inspection. Team guidance version 43 explains this behavior and is refreshed
on the next applicable wake after deployment. Read relevant checkout files before
editing or issuing review findings.

Each snapshot reports text, semantic and navigation coverage separately, with project
statuses and diagnostics. A project restore failure does not claim compiler coverage.
C# build/workspace failures can retain partial navigation. Angular HTML templates,
styles, Markdown and SQL provide text/semantic retrieval; SCIP does not provide Angular
template navigation. Source-generated code participates in compilation, but generated
and untracked files are excluded from retrieval/export. Large sources above the
configured byte limit are skipped with diagnostics. File exclusions apply again during
import, and changing the source policy blocks obsolete snapshots from queries/export.

The default `excludes` list contains `**/packages/**` for legacy dependency caches.
For npm workspaces whose tracked application source lives under `packages/*`, remove
that entry in **Exclude files**, retain the other exclusions, and save the settings.
`includes` must also match the source files; excludes take precedence over includes.
Rebuild existing snapshots after changing this policy. Git metadata, `node_modules`,
build output and credential files remain unconditionally excluded.

Results contain repository-relative paths, line ranges, symbols, the exact commit and
coverage. Lines are one-based; navigation columns are zero-based with an explicit
UTF-8/UTF-16/UTF-32 encoding. Search returns at most 50 bounded snippets per page,
within a 1,000-candidate window. Symbol relationship expansion is bounded at 2,000.

## Portable export

Choose **Download portable index** in Admin Web, or:

```bash
evander-cli code-index export REPO BUILD_ID app-index.zip --directory-path root/app
# Add --include-embeddings to export available cached vectors too.
unzip app-index.zip -d app-index
python3 app-index/query.py --db app-index/index.sqlite status
python3 app-index/query.py --db app-index/index.sqlite search 'durable event retry'
python3 app-index/query.py --db app-index/index.sqlite navigate SYMBOL --kind definitions
```

The version-1 ZIP format contains:

- `manifest.json`: exact commit, creation time, toolchain, coverage, sanitized embedding
  model/dimensions/distance, and SHA-256 checksums for the database, query script and SCIP files.
- `index.sqlite`: only this snapshot's chunks, FTS5 search table, occurrences,
  relationships and optional embedding vectors. It contains no other repository's data.
- `scip/*.scip`: original SCIP documents filtered to permitted source paths, with
  machine-specific metadata and external symbol records removed. Repeated documents
  are stored once; the SQLite navigation tables merge all indexed compilation contexts.
- `query.py` and `README.txt`: a Python 3.10+ standard-library interface. SQLite must
  include FTS5. JSON output is suitable for another agent runner, shell wrapper or MCP server.

External workflows can open SQLite directly, consume SCIP with compatible tools, or
invoke `query.py`. Semantic queries need a query vector generated with the manifest's
embedding model and dimensions; pass it using `search QUERY --query-vector vector.json`.
The offline tool ranks included vectors locally and combines them with text results.
Exports contain permitted source snippets. They contain no provider credentials,
service URLs, worker state, live Git grants or authentication configuration. Exported
data has its own lifecycle; later access revocation in Evander cannot revoke a copy.

## Storage, recovery and limits

The control plane stores SQLite state under `runtime/code-index/state.db`; Qdrant is
the optional vector projection. Snapshot identity includes repository, commit, source
policy, toolchain and embedding fingerprint. Embeddings are reused by content hash
within a repository and provider fingerprint. Compiler analysis currently reruns for
each new snapshot; unchanged embedding content is reused, not compiler outputs.

Jobs and sessions have deterministic IDs for restart adoption. Git commits are retained
under `refs/evander/code-index/BUILD_ID`. Imports validate source blobs against that
commit and publish only after an atomic SQLite import. Artifacts are limited to 512 MiB.
Semantic ingestion batches up to 32 texts per request, 512 chunks per pass, with a time
budget between requests. Embedding input is capped at 2,000 characters per chunk;
text retrieval retains the full stored chunk. Provider failures leave explicit partial
coverage; lexical queries remain available. Lexical queries also work after the atomic
import while semantic ingestion is still running. Changing embedding settings requires a
new snapshot for semantic search.

Reconciliation records embedding requests, input characters, cache hits and provider
token usage when reported. These counters are not a complete billing ledger or a
measurement of agent savings. Failed requests can incur unreported provider usage.

Retention defaults to 30 days. Active builds, active Team task/review/ephemeral bindings
and the newest published snapshot per repository are retained. Old vectors, worker
sessions, retained refs and unreferenced SQLite content are reclaimed in that order.
Cleanup retries on failure, preserving the snapshot and Git pin until worker deletion,
host Git authorization revocation and session binding removal are confirmed.
Builds cancelled before provisioning can release their pin without a
worker session. If an old vector store's configuration has changed, cleanup
is deferred until that store can be addressed again; it does not silently orphan vectors.

No live deployment is required for tests. Run the focused indexing tests and use
`scripts/benchmark_code_index.py` with an isolated worker artifact to evaluate a fixed
commit. Report query relevance, navigation coverage, index size and measured latency
separately from future token/cost savings.

See [the APP validation report](repository-indexing-validation.md) for measured
coverage, retrieval results, portability checks and remaining validation limits.
