from __future__ import annotations

import random
from collections import Counter, deque

from evander.workflow.validation import (
    _compute_dominators,
    derive_workflow_output_schemas,
    validate_workflow_binding_diagnostics,
)
from read_scaling_probe import EdgeReads, LineProbe


def independent_pairs(count=100):
    reads = Counter()
    config = {
        "triggers": [
            {"id": f"t-{i}", "type": "trigger:manual", "outputs": {"body": {"type": "string"}}}
            for i in range(count)
        ],
        "effects": [
            {
                "id": f"e-{i}",
                "type": "effect:agent:prompt",
                "config": {"prompt": "${{t-" + str(i) + ".body}}"},
            }
            for i in range(count)
        ],
        "edges": [
            EdgeReads({"source_id": f"t-{i}", "target_id": f"e-{i}"}, counter=reads)
            for i in range(count)
        ],
    }
    return config, reads


def filter_chain(count=100):
    reads = Counter()
    config = {
        "triggers": [
            {"id": "start", "type": "trigger:manual", "outputs": {"body": {"type": "string"}}}
        ],
        "gates": [{"id": f"filter-{i}", "type": "gate:filter"} for i in range(count)],
        "edges": [
            EdgeReads(
                {"source_id": f"filter-{i - 1}" if i else "start", "target_id": f"filter-{i}"},
                counter=reads,
            )
            for i in range(count)
        ],
    }
    return config, reads


def reverse_chain(count=100):
    # The root sorts last, forcing repeated fixed-point passes on the old path.
    return {
        "effects": [{"id": f"node-{i:04}", "type": "effect:agent:prompt"} for i in range(count)],
        "edges": [
            {"source_id": f"node-{i:04}", "target_id": f"node-{i - 1:04}"} for i in range(1, count)
        ],
    }


def test_binding_diagnostics_share_inbound_index_across_independent_nodes():
    config, reads = independent_pairs()
    assert validate_workflow_binding_diagnostics(config) == []
    assert reads["source_id"] <= 2 * len(config["edges"])
    assert reads["target_id"] <= 2 * len(config["edges"])

    config["edges"][0]["source_id"] = "t-1"
    diagnostics = validate_workflow_binding_diagnostics(config)
    assert [(item.code, item.node_id, item.reference) for item in diagnostics] == [
        ("invalid_binding_node", "e-0", "t-0.body")
    ]


def test_derived_filter_outputs_index_edges_once_and_observe_graph_edits():
    config, reads = filter_chain()
    schemas = derive_workflow_output_schemas(config)
    assert schemas["filter-99"]["body"] == {"type": "string"}
    assert schemas["filter-99"]["success"] == {"type": "boolean"}
    assert schemas["start"] == {"body": {"type": "string"}}
    assert reads["source_id"] == reads["target_id"] == len(config["edges"])
    config["edges"].pop()
    assert "body" not in derive_workflow_output_schemas(config)["filter-99"]


def test_output_schema_index_retains_first_input_duplicates_and_loop_edges():
    config = {
        "triggers": [
            {"id": "left", "type": "trigger:manual", "outputs": {"left": {"type": "string"}}},
            {"id": "right", "type": "trigger:manual", "outputs": {"right": {"type": "integer"}}},
        ],
        "gates": [{"id": "filter", "type": "gate:filter"}, {"id": "merge", "type": "gate:merge"}],
        "edges": [
            {"source_id": " left ", "target_id": " filter ", "allow_cycle": True},
            {"source_id": "right", "target_id": "filter"},
            {"source_id": "left", "target_id": "merge"},
            {"source_id": "left", "target_id": "merge"},
            None,
            {"source_id": "", "target_id": "merge"},
        ],
    }
    schemas = derive_workflow_output_schemas(config)
    assert schemas["filter"]["left"] == {"type": "string"}
    assert "right" not in schemas["filter"]
    assert schemas["merge"]["left"] == {"type": "string"}


def test_dominator_validation_bounds_reverse_chain_work():
    with LineProbe("_compute_dominators") as probe:
        assert validate_workflow_binding_diagnostics(reverse_chain()) == []
    assert 0 < probe.line_events < 10_000


def reference_dominators(edges, nodes):
    """An independent definition: removing a dominator blocks all root paths."""
    predecessors = {node: set() for node in nodes}
    successors = {node: set() for node in nodes}
    for edge in edges:
        if edge.get("allow_cycle"):
            continue
        source, target = edge["source_id"], edge["target_id"]
        if source in nodes and target in nodes:
            predecessors[target].add(source)
            successors[source].add(target)
    roots = {node for node in nodes if not predecessors[node]}
    result = {node: set() for node in nodes}
    for removed in nodes:
        reachable = set(roots - {removed})
        pending = deque(reachable)
        while pending:
            for target in successors[pending.popleft()]:
                if target != removed and target not in reachable:
                    reachable.add(target)
                    pending.append(target)
        for blocked in nodes - reachable:
            result[blocked].add(removed)
    return result


def test_dominators_match_independent_path_definition_with_multiple_roots_and_draft_cycles():
    rng = random.Random(20261009)
    for _ in range(150):
        nodes = {f"n-{i}" for i in range(rng.randrange(2, 12))}
        ordered = sorted(nodes)
        edges = [
            {"source_id": source, "target_id": target, "allow_cycle": rng.random() < 0.15}
            for source in ordered
            for target in ordered
            if rng.random() < 0.12
        ]
        assert _compute_dominators(edges, nodes) == reference_dominators(edges, nodes)


def test_binding_ancestors_preserve_breadth_order_and_invalid_cycle_diagnostics():
    config = {
        "triggers": [
            {"id": "start", "type": "trigger:manual", "outputs": {"body": {"type": "string"}}}
        ],
        "effects": [
            {"id": "a", "type": "effect:agent:prompt", "config": {"prompt": "${{b.missing}}"}},
            {"id": "b", "type": "effect:agent:prompt", "config": {"prompt": "${{start.body}}"}},
        ],
        "edges": [
            {"source_id": "start", "target_id": "a"},
            {"source_id": "a", "target_id": "b"},
            {"source_id": "b", "target_id": "a"},
        ],
    }
    diagnostics = validate_workflow_binding_diagnostics(config)
    assert [(item.code, item.node_id) for item in diagnostics] == [("missing_binding_output", "a")]
