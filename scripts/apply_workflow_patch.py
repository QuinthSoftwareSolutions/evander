#!/usr/bin/env python3
"""Preview or revision-safely apply a declarative workflow document patch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evander.admin.composition import build_admin_runtime
from evander.workflow.document_patch import apply_workflow_patch, apply_workflow_patch_layout


def update_document(service, *, patch, apply=False):
    pack = patch["workflow_pack"]
    document = service.read_workflow_editor_document(workflow_pack=pack)
    graphs = [graph.model_dump(mode="json") for graph in document.graphs]
    config, next_graphs = apply_workflow_patch(document.config, graphs, patch)
    layout = apply_workflow_patch_layout(document.layout, patch)
    baseline = service.validate_workflow_editor_document(
        config=document.config,
        graphs=graphs,
        layout=document.layout,
        workflow_pack=pack,
    )
    candidate = service.validate_workflow_editor_document(
        config=config,
        graphs=next_graphs,
        layout=layout,
        workflow_pack=pack,
    )
    added = [
        item
        for item in candidate.validation_diagnostics
        if item not in baseline.validation_diagnostics
    ]
    if added:
        raise ValueError("New workflow validation diagnostics: " + json.dumps(added))
    changed = (
        candidate.config != baseline.config
        or candidate.graphs != baseline.graphs
        or candidate.layout != baseline.layout
    )
    result = {
        "pack": pack,
        "previous_revision": document.revision,
        "changed": changed,
        "applied": False,
        "new_diagnostics": added,
    }
    if apply and changed:
        saved = service.save_workflow_editor_document(
            config=config,
            graphs=next_graphs,
            layout=layout,
            workflow_pack=pack,
            expected_revision=document.revision,
        )
        reread = service.read_workflow_editor_document(workflow_pack=pack)
        if saved.revision != reread.revision:
            raise RuntimeError("Workflow changed after save; inspect the current revision")
        result.update(applied=True, revision=saved.revision)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", default="runtime")
    parser.add_argument("--patch", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    patch = json.loads(args.patch.read_text(encoding="utf-8"))
    runtime = build_admin_runtime(args.runtime_dir)
    print(
        json.dumps(
            update_document(
                runtime.workflow_config_admin,
                patch=patch,
                apply=args.apply,
            )
        )
    )


if __name__ == "__main__":
    main()
