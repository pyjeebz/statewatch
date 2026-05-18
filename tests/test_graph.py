from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from statewatch.graph.builder import build_graph, parse_tf_address
from statewatch.graph.manual import (
    ManualEdge,
    ManualEdgeError,
    load_manual_edges,
    manual_edges_from_dicts,
)
from statewatch.graph.render import render_dot, render_json, render_text
from statewatch.graph.validator import validate_graph
from statewatch.normalizer import Resource
from statewatch.resources.compute_instance import (
    normalize_from_tfstate as normalize_compute_instance_from_tfstate,
)
from statewatch.tfstate import extract_compute_instances

PROJECT = "demo-project"
FIXTURES = Path(__file__).parent / "fixtures"


def _resources_from_fixture(tfstate: dict[str, Any]) -> list[Resource]:
    return [
        normalize_compute_instance_from_tfstate(
            inst.attributes,
            project=PROJECT,
            terraform_address=inst.address,
            depends_on=inst.dependencies,
        )
        for inst in extract_compute_instances(tfstate)
    ]


# 1 --------------------------------------------------------------------------------------
def test_build_graph_from_phase1_fixture_has_managed_nodes(tfstate: dict[str, Any]) -> None:
    g = build_graph(_resources_from_fixture(tfstate))
    managed = {n for n, d in g.nodes(data=True) if d.get("managed")}
    assert "projects/demo-project/zones/us-central1-a/instances/api-server-prod" in managed
    assert "projects/demo-project/zones/us-central1-b/instances/worker-01" in managed
    assert g.number_of_edges() > 0


# 2 --------------------------------------------------------------------------------------
def test_automatic_edges_from_depends_on(tfstate: dict[str, Any]) -> None:
    g = build_graph(_resources_from_fixture(tfstate))
    api = "projects/demo-project/zones/us-central1-a/instances/api-server-prod"
    # api_server depends_on google_compute_subnetwork.prod_subnet (an external node).
    assert g.has_edge(api, "google_compute_subnetwork.prod_subnet")
    edge = g.edges[api, "google_compute_subnetwork.prod_subnet"]
    assert "automatic" in edge["kinds"]
    assert g.nodes["google_compute_subnetwork.prod_subnet"]["external"] is True


# 3 --------------------------------------------------------------------------------------
def test_inferred_edges_from_parent_refs(tfstate: dict[str, Any]) -> None:
    g = build_graph(_resources_from_fixture(tfstate))
    api = "projects/demo-project/zones/us-central1-a/instances/api-server-prod"
    inferred = [
        (u, v, d)
        for u, v, d in g.out_edges(api, data=True)
        if d.get("kind") == "inferred"
    ]
    targets = {v for _, v, _ in inferred}
    # The defensible part: subnetwork + service account edges Terraform doesn't track.
    assert any("subnetworks/prod-subnet" in t for t in targets)
    assert any("serviceAccounts/sa-api@" in t for t in targets)
    assert len(inferred) >= 2
    for _, v, _ in inferred:
        assert g.nodes[v]["external"] is True


# 4 --------------------------------------------------------------------------------------
def test_manual_edge_added_programmatically(tfstate: dict[str, Any]) -> None:
    edges = [
        ManualEdge(
            from_ref="google_compute_instance.api_server",
            to_ref="google_storage_bucket.app_data",
            reason="reads/writes app data",
        )
    ]
    g = build_graph(_resources_from_fixture(tfstate), edges)
    api = "projects/demo-project/zones/us-central1-a/instances/api-server-prod"
    assert g.has_edge(api, "google_storage_bucket.app_data")
    edge = g.edges[api, "google_storage_bucket.app_data"]
    assert edge["kind"] == "manual"
    # Reason is the raw user text; the "manual:" label comes from `kind`, not the reason.
    assert edge["reason"] == "reads/writes app data"
    assert g.nodes["google_storage_bucket.app_data"]["external"] is True


# 5 --------------------------------------------------------------------------------------
def test_manual_edges_loaded_from_yaml_merge_into_auto_graph(tfstate: dict[str, Any]) -> None:
    manual = load_manual_edges(FIXTURES / "statewatch.manual.yaml")
    g = build_graph(_resources_from_fixture(tfstate), manual)
    api = "projects/demo-project/zones/us-central1-a/instances/api-server-prod"
    # Auto edges still present...
    assert any(d.get("kind") == "inferred" for _, _, d in g.out_edges(api, data=True))
    # ...and the manual one is merged in.
    assert g.has_edge(api, "google_storage_bucket.app_data")
    assert g.edges[api, "google_storage_bucket.app_data"]["kind"] == "manual"


# 6 --------------------------------------------------------------------------------------
def test_cycle_detection() -> None:
    a = Resource("A", "google_compute_instance", "gcp", "a", parent_refs=("B",))
    b = Resource("B", "google_compute_instance", "gcp", "b", parent_refs=("A",))
    g = build_graph([a, b])
    v = validate_graph(g)
    assert v.cycles
    assert sorted(v.cycles[0]) == ["A", "B"]
    assert any("cycle" in w for w in v.warnings())


# 7 --------------------------------------------------------------------------------------
def test_orphan_detection() -> None:
    lonely = Resource("solo", "google_compute_instance", "gcp", "solo")
    g = build_graph([lonely])
    v = validate_graph(g)
    assert v.orphans == ["solo"]
    assert not v.ok


# 8 --------------------------------------------------------------------------------------
def test_reference_to_nonexistent_resource_warns_not_crashes() -> None:
    r = Resource(
        "projects/p/zones/z/instances/x",
        "google_compute_instance",
        "gcp",
        "x",
        depends_on=("google_compute_subnetwork.ghost",),
    )
    g = build_graph([r])  # must not raise
    v = validate_graph(g)  # must not raise
    assert "google_compute_subnetwork.ghost" in v.external_refs
    assert g.nodes["google_compute_subnetwork.ghost"]["external"] is True


# 9 --------------------------------------------------------------------------------------
def test_dot_output_is_structurally_valid(tfstate: dict[str, Any]) -> None:
    g = build_graph(_resources_from_fixture(tfstate))
    dot = render_dot(g)
    assert dot.startswith("digraph statewatch {")
    assert dot.rstrip().endswith("}")
    assert dot.count("{") == dot.count("}")
    assert " -> " in dot  # at least one edge rendered
    # json + text renderers also produce usable output
    parsed = json.loads(render_json(g, validate_graph(g)))
    assert parsed["nodes"] and parsed["edges"]
    assert "Dependency graph:" in render_text(g)


# 10 -------------------------------------------------------------------------------------
def test_duplicate_edge_from_two_sources_merges() -> None:
    r = Resource("A", "google_compute_instance", "gcp", "a", parent_refs=("B",))
    # Manual edge asserts the same A -> B that inference will.
    g = build_graph([r], [ManualEdge("A", "B", "documented coupling")])
    assert g.number_of_edges() == 1
    edge = g.edges["A", "B"]
    assert set(edge["kinds"]) == {"inferred", "manual"}
    # Precedence: inferred wins over manual for the single honest label.
    assert edge["kind"] == "inferred"
    assert len(edge["reasons"]) == 2


# 11 -------------------------------------------------------------------------------------
def test_manual_edge_requires_reason() -> None:
    with pytest.raises(ManualEdgeError, match="reason"):
        manual_edges_from_dicts([{"from": "a", "to": "b"}])
    with pytest.raises(ManualEdgeError, match="reason"):
        manual_edges_from_dicts([{"from": "a", "to": "b", "reason": "   "}])


# 12 -------------------------------------------------------------------------------------
def test_parse_tf_address_handles_modules_and_indexes() -> None:
    assert parse_tf_address("google_compute_instance.api") == ("google_compute_instance", "api")
    assert parse_tf_address(
        "module.data_plane.google_compute_subnetwork.prod_subnet[0]"
    ) == ("google_compute_subnetwork", "prod_subnet")
    assert parse_tf_address('module.a.module.b.google_x.y["k"]') == ("google_x", "y")
