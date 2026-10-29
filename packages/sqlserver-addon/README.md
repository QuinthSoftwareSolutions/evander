# SQL Server add-on

Optional, independently installed SQL Server management for registered services and disposable test resources. No Team or other resource add-on dependency is required.

SQL supports `inspect`, `query`, `execute`, `manage_database` and `manage_principal`. Read and write credentials are separate. Read accounts must have read-only SQL permissions; query text is not classified with a parser. Management uses only configured administration credentials. Database creation returns a managed resource ID; database removal requires that ID and a matching ownership marker. Principal operations are database-scoped create/drop and grants for `db_datareader`, `db_datawriter` and `db_ddladmin`. Creation can map a database user to an existing server `login`, or create a user without a login. Server login administration remains externally managed.

Profiles accept one to 32 arbitrary database aliases and an optional collation. They initialize empty databases. Applications apply schema and synthetic data. The existing core MSSQL and MSSQL Seed interfaces remain unchanged.

See [operations](docs/operations.md), [control-plane settings](examples/control-plane.json), [worker settings](examples/worker.json) and [systemd units](examples/systemd/). Defaults keep allocation and production operations disabled.

All tools use the `sqlserver_` prefix. Lifecycle tools are `discover`, `acquire`, `get`, `reset`, `release`. Acquire and reset use stable request IDs; reuse an ID only for the same logical request. Status reads never renew leases.

Example acquisition arguments:

```json
{
  "profile_id": "application-test",
  "request_id": "verification-1"
}
```

For Team-owned sessions, enable `team-addon` in the selected profile's `allowed_owner_providers` and restrict its owner keys or team prefixes as appropriate. Include `owner_provider: "team-addon"` and `owner_reference: {"task_id": "task-id"}` on each request. The Team adapter validates the active owning dispatch. Other ordinary sessions use the default session owner. Workflow nodes derive their owner from the trusted workflow trace.

Data tools accept a registered `connection_id`, or a `lease_id` with scoped application credentials. Their typed `arguments` object is described by the MCP schema. For SQL leases choose `database_alias` when the profile has several databases. Management cannot use a disposable lease as a general administration connection.

A ready worker receipt provides the current job-private configuration path. Application credentials remain in that mode-0600 file; administration credentials never enter the job. Ordinary lease/status receipts and operator operation summaries contain no credentials or message/query results.

Each private SQL alias contains connection fields, a SqlClient `connection_string`, and an ODBC 18 `odbc_connection_string`. Use the string appropriate to the application driver.
