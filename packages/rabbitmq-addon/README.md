# RabbitMQ add-on

Optional, independently installed RabbitMQ management for registered services and disposable test resources. No Team or other resource add-on dependency is required.

RabbitMQ supports `inspect`, `manage`, `publish` and `get_messages`. Management covers vhosts, scoped users/permissions, queues, exchanges and bindings. Connection grants enumerate allowed vhosts and users. User creation takes a configured application credential purpose instead of receiving a password in a tool argument. Message get is a mutation even with `requeue=true`; consume with `requeue=false` explicitly.

Publish accepts one message or a `messages` batch of at most 100 messages and 64 KiB total JSON. A batch stops at its first unknown result and records acknowledged, uncertain and unattempted counts. Reusing its request ID never resends the batch. Message retrieval is limited to 100 messages and the configured response byte limit.

Profiles default to an isolated internal broker with one CPU, 1 GiB memory, bounded logs and no published ports. Operators must supply and preseed an approved digest. Shared profiles allocate a unique vhost and user on a registered broker. Shared and production brokers have no stop/pause controls. Isolated faults last 1 to 120 seconds and have worker-local recovery deadlines.

See [operations](docs/operations.md), [control-plane settings](examples/control-plane.json), [worker settings](examples/worker.json) and [systemd units](examples/systemd/). Defaults keep allocation and production operations disabled.

All tools use the `rabbitmq_` prefix. Lifecycle tools are `discover`, `acquire`, `get`, `reset`, `release` and `control`. Acquire, reset and control use stable request IDs; reuse an ID only for the same logical request. Status reads never renew leases.

Example acquisition arguments:

```json
{
  "profile_id": "isolated-test",
  "request_id": "verification-1"
}
```

For Team-owned sessions, enable `team-addon` in the selected profile's `allowed_owner_providers` and restrict its owner keys or team prefixes as appropriate. Include `owner_provider: "team-addon"` and `owner_reference: {"task_id": "task-id"}` on each request. The Team adapter validates the active owning dispatch. Other ordinary sessions use the default session owner. Workflow nodes derive their owner from the trusted workflow trace.

Data tools accept a registered `connection_id`, or a `lease_id` with scoped application credentials. Their typed `arguments` object is described by the MCP schema. Lease data calls are restricted to the lease's vhost. Applications initialize test topology through their scoped AMQP credentials. Management cannot use a disposable lease as a general administration connection.

A ready worker receipt provides the current job-private configuration path. Application credentials remain in that mode-0600 file; administration credentials never enter the job. Ordinary lease/status receipts and operator operation summaries contain no credentials or message/query results.
