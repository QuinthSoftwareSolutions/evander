"""Pure workflow starter for one directory's accepted resource lifecycle work."""

from evander.integration_contracts import IntegrationWorkflowStarter
from evander.workflow.packs import WorkflowPack

from .service import fingerprint


def maintenance(prefix):
    def build(context, inputs):
        suffix = fingerprint(context.directory_path)[:12]
        graph_id = f"{prefix}-resources-{suffix}"
        pending, reconcile = graph_id + "-pending", graph_id + "-reconcile"
        return WorkflowPack.model_validate(
            {
                "pack_id": graph_id,
                "pack_version": "v1",
                "description": "Maintain accepted resource leases without granting production data operations.",
                "graphs": [
                    {
                        "id": graph_id,
                        "name": prefix + " resource lifecycle",
                        "directory_path": context.directory_path,
                        "node_ids": [pending, reconcile],
                    }
                ],
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "node": {
                                "id": pending,
                                "display_name": "Pending resource leases",
                                "type": f"trigger:{prefix}:pending",
                                "config": {},
                            }
                        },
                        {
                            "node": {
                                "id": reconcile,
                                "display_name": "Reconcile resource lease",
                                "type": f"effect:{prefix}:operate",
                                "config": {
                                    "operation": "reconcile",
                                    "arguments": {"lease_id": "${{" + pending + ".lease_id}}"},
                                },
                            }
                        },
                    ],
                    "edges": [{"source_id": pending, "target_id": reconcile}],
                },
            }
        )

    return IntegrationWorkflowStarter(
        "maintenance",
        "Resource lifecycle",
        {"type": "object", "properties": {}, "additionalProperties": False},
        build,
        description="Activate once per directory to revalidate job bindings, renew active leases and finish cleanup. Does not allocate resources or grant production operations.",
    )
