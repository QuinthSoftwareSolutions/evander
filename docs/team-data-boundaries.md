# Team models and persistence

`evander_team_addon.models` remains the supported compatibility import surface.
It explicitly re-exports the original model classes and projection functions;
there is one definition of each class. Domain implementations can import the
narrower modules directly.

| Responsibility | Modules |
| --- | --- |
| Shared enums, normalization, and Pydantic configuration | `model_common` |
| Teams, projects, roles, membership, and agent runtime records | `model_organization` |
| Cases, evidence, task contracts, and task records | `model_tasks` |
| Deliverables and Git composition | `model_deliverables_git` |
| Reviews, inbox, blockers, and memory | `model_reviews`, `model_inbox_memory` |
| Dispatch, journal, outbox, and ephemeral assignment records | `model_dispatch` |
| Conversations, attachments, chat ingress, and work lineage | `model_chat` |
| Release packages, verification, and human review | `model_releases` |
| Aggregate state and cross-collection validation | `model_state` |
| Relationship queries and wake fingerprints | `model_queries`, `model_work_fingerprints` |
| Read payloads | `state_projections`, `activity_projections`, `runtime_projections`, `attention_projections`, `release_projections` |

Read projections depend on model definitions. Aggregate validators import their
relationship queries inside the methods that need them, so the aggregate and
query modules do not require partially initialized module objects.

`TeamStateSqliteStore` continues to own connection lifetime, locking, snapshot
reads, revision checks, state validation, and transaction boundaries. The domain
adapters in `sqlite_organization`, `sqlite_tasks`, `sqlite_deliverables`,
`sqlite_reviews`, `sqlite_inbox_memory`, `sqlite_chat`, `sqlite_releases`, and
`sqlite_git` read and write rows using a connection provided by that store.
They do not begin, commit, or close transactions. `sqlite_journal` holds command
activity, idempotency, and outbox operations; command transactions reuse the
state transaction's connection when writing these records.

`sqlite_persistence_map` defines normalized table ownership and deletion order.
Changes to a collection's tables must update both its adapter and that map.
`sqlite_codec` supplies shared value conversion and command identity handling.

`sqlite_schema` coordinates schema upgrades in their historical order. The
current table definitions live in `sqlite_schema_sql`; data migrations are
grouped by membership, worktrees, acceptance, Git, and outcome catalogs. Column
additions must retain their ordering around table rebuilds and data migrations.
Migrations that validate and rewrite aggregate state receive callbacks bound to
the store and use the same open connection.

Read projections retain their omitted-collection guard and cannot be persisted.
The store remains responsible for ensuring complete state reaches every update
path and for closing SQLite connections after transaction context exit.
