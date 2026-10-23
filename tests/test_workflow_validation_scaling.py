from __future__ import annotations

import random
from collections import Counter, deque
from types import SimpleNamespace

from evander.workflow import validation
from evander.workflow.validation import (
    _compute_dominators,
    derive_workflow_output_schemas,
    validate_workflow_binding_diagnostics,
    validate_workflow_graph,
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
    config = reverse_chain()
    config["effects"][-1]["outputs"] = {"body": {"type": "string"}}
    config["effects"][0]["config"] = {"prompt": "${{node-0099.body}}"}
    with LineProbe("_compute_dominators") as probe:
        assert validate_workflow_binding_diagnostics(config) == []
    assert 0 < probe.line_events < 10_000


def test_cycle_validation_bounds_graph_lookups_for_reverse_listed_paths():
    class CountedId(str):
        hashes = 0

        def __hash__(self):
            CountedId.hashes += 1
            return super().__hash__()

    node_ids = [CountedId(f"node-{index}") for index in range(400)]
    nodes = [SimpleNamespace(id=node_id, type="effect:agent:prompt") for node_id in node_ids]
    edges = [
        SimpleNamespace(source_id=node_ids[index], target_id=node_ids[index + 1])
        for index in reversed(range(len(node_ids) - 1))
    ]
    assert validate_workflow_graph(nodes=nodes, edges=edges, trigger_node_ids=[]) == []
    # Rewalking each growing path hashes over 240,000 IDs for the same valid graph.
    assert CountedId.hashes < 20 * len(node_ids)


def test_cycle_validation_preserves_ordered_diagnostics_for_invalid_drafts():
    nodes = [SimpleNamespace(id=node_id, type="effect:agent:prompt") for node_id in "abc"]
    edges = [
        SimpleNamespace(source_id=source, target_id=target, allow_cycle=allowed)
        for source, target, allowed in [
            ("a", "b", False),
            ("a", "b", False),
            ("b", "a", False),
            ("b", "c", False),
            ("c", "a", False),
            ("c", "b", True),
            ("missing", "missing", False),
        ]
    ]
    assert validate_workflow_graph(nodes=nodes, edges=edges, trigger_node_ids=["a"]) == [
        "Non-cyclic edges cannot form a cycle: b -> a",
        "Non-cyclic edges cannot form a cycle: c -> a",
        "Non-cyclic edges cannot form a cycle: missing -> missing",
        "Edge endpoints must reference existing node IDs: missing",
        "Edges cannot connect a node to itself: missing",
        "trigger a must have 0 inbound edges, found 2",
    ]


def test_literal_binding_diagnostics_skip_graph_analysis_and_observe_new_bindings():
    config, reads = filter_chain(300)
    assert validate_workflow_binding_diagnostics(config) == []
    assert reads == {}
    config["gates"][-1]["config"] = {"value": "${{start.missing}}"}
    diagnostics = validate_workflow_binding_diagnostics(config)
    assert [(item.code, item.node_id, item.reference) for item in diagnostics] == [
        ("missing_binding_output", "filter-299", "start.missing")
    ]


def test_binding_diagnostics_only_walk_ancestors_for_nodes_with_references(monkeypatch):
    reads = Counter()
    original_index = validation._index_upstream_node_ids

    class CountedIndex(dict):
        def get(self, node_id, default=None):
            reads[node_id] += 1
            return super().get(node_id, default)

    monkeypatch.setattr(
        validation,
        "_index_upstream_node_ids",
        lambda edges: CountedIndex(original_index(edges)),
    )
    config = reverse_chain(400)
    config["effects"][-1]["outputs"] = {"body": {"type": "string"}}
    config["effects"][0]["config"] = {"prompt": "${{node-0399.body}}"}
    config["effects"].append({"id": "disconnected", "type": "effect:agent:prompt"})
    assert validate_workflow_binding_diagnostics(config) == []
    assert sum(reads.values()) == 400
    assert "disconnected" not in reads


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
