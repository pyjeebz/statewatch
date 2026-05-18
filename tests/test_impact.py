from __future__ import annotations

from typing import Any

from statewatch.adapters.gcp import GCPAdapter
from statewatch.classifier import CRITICAL, LOW, MEDIUM
from statewatch.differ import Change, ResourceDiff, diff_resources
from statewatch.graph.builder import build_graph
from statewatch.impact import rules
from statewatch.impact.analyzer import analyze_impact
from statewatch.normalizer import Resource
from statewatch.report import build_report
from statewatch.resources import normalize_tf
from statewatch.tfstate import iter_managed_resources

PROJECT = "demo-project"


def _resources(tfstate: dict[str, Any]) -> list[Resource]:
    out = []
    for inst in iter_managed_resources(tfstate):
        r = normalize_tf(
            inst.type, inst.attributes, project=PROJECT,
            terraform_address=inst.address, depends_on=inst.dependencies,
        )
        if r is not None:
            out.append(r)
    return out


# --- rules --------------------------------------------------------------------------
def test_label_bands_and_non_propagating_override() -> None:
    assert rules.label_for(1, True) == rules.DIRECT
    assert rules.label_for(2, True) == rules.INDIRECT
    assert rules.label_for(3, True) == rules.WATCH
    # LOW-only drift does not propagate -> everything WATCH regardless of distance
    assert rules.label_for(1, False) == rules.WATCH
    assert rules.is_propagating(CRITICAL) and rules.is_propagating(MEDIUM)
    assert not rules.is_propagating(LOW)


# --- predecessor direction (the critical correctness property) ----------------------
def test_impact_walks_predecessors_not_successors() -> None:
    # vm depends on subnet:  vm -> subnet
    vm = Resource("vm", "google_compute_instance", "gcp", "vm", parent_refs=("subnet",))
    subnet = Resource("subnet", "google_compute_subnetwork", "gcp", "subnet")
    g = build_graph([vm, subnet])

    # subnet drifts -> vm (its predecessor) is impacted DIRECT
    impacted = analyze_impact(g, "subnet", propagating=True)
    assert [(n.resource_id, n.label) for n in impacted] == [("vm", rules.DIRECT)]
    # vm drifts -> subnet is NOT impacted (impact flows against dependency edges)
    assert analyze_impact(g, "vm", propagating=True) == []


def test_analyzer_is_graceful_on_external_or_absent() -> None:
    vm = Resource("vm", "google_compute_instance", "gcp", "vm", parent_refs=("ext",))
    g = build_graph([vm])
    assert analyze_impact(g, "ext", propagating=True) == []  # external placeholder
    assert analyze_impact(g, "missing", propagating=True) == []  # absent node


# --- firewall applicability inference -----------------------------------------------
def test_firewall_applicability_edges() -> None:
    fw = normalize_tf(
        "google_compute_firewall",
        {"name": "allow-http", "network": "vpc", "direction": "INGRESS",
         "target_tags": ["http-server"], "allow": [{"protocol": "tcp", "ports": ["80"]}]},
        project=PROJECT,
    )
    served = normalize_tf(
        "google_compute_instance",
        {"name": "web", "zone": "us-central1-a", "tags": ["http-server"],
         "network_interface": [{"network": "vpc"}]},
        project=PROJECT,
    )
    other = normalize_tf(
        "google_compute_instance",
        {"name": "db", "zone": "us-central1-a", "tags": ["db"],
         "network_interface": [{"network": "vpc"}]},
        project=PROJECT,
    )
    assert fw and served and other
    g = build_graph([fw, served, other])
    # firewall drift -> only the tag-matching instance is impacted
    impacted = {n.resource_id for n in analyze_impact(g, fw.resource_id, propagating=True)}
    assert served.resource_id in impacted
    assert other.resource_id not in impacted


# --- exit-code matrix ----------------------------------------------------------------
def _diff(rtype: str, changes: list[Change]) -> ResourceDiff:
    return ResourceDiff("rid", rtype, "n", "drifted", changes)


def test_exit_code_matrix() -> None:
    from statewatch.graph.builder import build_graph as bg

    empty = bg([])
    assert build_report([], empty).exit_code() == 0

    low = build_report(
        [_diff("google_compute_instance", [Change("rid", "labels.x", "a", "b", "modified")])],
        empty,
    )
    assert low.exit_code() == 1  # LOW severity floor

    fw_change = Change("rid", "source_ranges[0]", "a", "b", "modified")
    crit = build_report([_diff("google_compute_firewall", [fw_change])], empty)
    assert crit.exit_code() == 2  # CRITICAL always 2


# --- end-to-end showcase -------------------------------------------------------------
def test_end_to_end_firewall_and_subnet_blast_radius(fw_subnet_tfstate: dict[str, Any]) -> None:
    tf = _resources(fw_subnet_tfstate)
    graph = build_graph(tf)
    live = GCPAdapter(stub=True).fetch_resources(["google_compute_instance",
                                                  "google_compute_firewall",
                                                  "google_compute_subnetwork"], scope=PROJECT)
    report = build_report(diff_resources(tf, live), graph)

    by_type = {f.resource_type: f for f in report.findings}
    fw = by_type["google_compute_firewall"]
    sub = by_type["google_compute_subnetwork"]

    assert fw.severity == CRITICAL
    assert fw.direct_count() == 2  # the two http-server instances
    assert sub.severity == MEDIUM
    assert sub.direct_count() == 3  # every instance in the subnet -> wide blast radius
    assert report.exit_code() == 2  # CRITICAL + significant blast radius
