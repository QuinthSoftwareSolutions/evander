# Declarative workflow document patches

Use `scripts/apply_workflow_patch.py --patch <json-file>` to preview a namespaced
node/edge extension to an existing operator-owned workflow. Add `--apply` to save
through `WorkflowConfigAdminService` with expected-revision validation and readback.
The installer contains no repository, command, deployment or agent-prompt rules.

A version-1 document declares:

- `workflow_pack`: the operator pack to read and update.
- `graph_anchor`: an existing node identifying exactly one graph.
- `required_node_ids`: prerequisites that must already exist.
- `node_prefix`: the namespace managed by this patch. Existing nodes in that
  namespace are replaced, along with their edges; nodes in other graphs cannot
  share the prefix.
- `remove_edges`: the original routing edges being replaced. Initial installation
  requires each edge exactly once; subsequent applications may omit removed edges.
- `nodes`, `edges`: ordinary workflow definitions, including connections to the
  pre-existing nodes in the same graph. Each new edge must touch a patch node.
  Routing, bindings, conditions and prompts remain data.
- `layout.nodes`: optional explicit `{ "x", "y" }` positions for every patch node.
  Reapplying replaces coordinates only inside the declared node namespace and
  preserves the rest of the graph layout.
- `append_config_lists`: optional `{ "node_id", "field", "values", "optional" }`
  entries, appending missing values to a static config array without replacing
  existing values or unrelated configuration. Missing required nodes fail; an
  explicitly optional entry is ignored when its node is absent.

The patch does not change worker-global settings. Its graph-depth check refuses
an extension that would exceed the configured limit. Candidate diagnostics are
compared with the current baseline; new diagnostics prevent saving. Preview does
not write. A repeated application of the same normalized configuration is a no-op.

This is an explicit configuration installation tool, not an automatic reconciler.
Reapplying overwrites operator edits inside the declared namespace, so incorporate
those edits into the patch before applying a newer version. Keep pack revisions
for rollback through the same supported Admin API; never overwrite live pack files.
