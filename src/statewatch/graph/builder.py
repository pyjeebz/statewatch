"""Resource dependency graph construction.

``build_graph`` is the Phase 2 entry point. It turns a list of normalized
:class:`~statewatch.normalizer.Resource` objects into a single ``networkx.DiGraph``
combining three edge sources:

* **automatic** — Terraform ``depends_on`` (Resource.depends_on, Terraform addresses)
* **inferred** — attribute references Terraform doesn't track (Resource.parent_refs)
* **manual**   — user-declared edges from ``statewatch.yaml``

Conventions (locked in Phase 2 design):

* Node identity is the canonical cloud ``resource_id``. The Terraform address is node
  metadata, used only to *resolve* depends_on / manual targets back to ids.
* Edge ``A → B`` means "A depends on B" (if B changes, A may be affected). Phase 3 impact
  traversal walks predecessors of a drifted node.
* Edge targets that aren't managed resources become **external placeholder nodes**
  (``external=True``) rather than being dropped — an unmanaged dependency is exactly the
  hidden coupling statewatch exists to surface.
* When several sources assert the same edge, it is merged into one. The single ``kind``
  label is honest about the headline claim: ``automatic`` if Terraform tracks it at all,
  else ``inferred`` if we inferred it, else ``manual``. The full set is kept in ``kinds``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeAlias

import networkx as nx

from statewatch.graph.inferred import firewall_applicability_edges, inferred_edges
from statewatch.graph.manual import ManualEdge
from statewatch.normalizer import Resource

if TYPE_CHECKING:
    from collections.abc import Iterable

# A statewatch dependency graph is a directed networkx graph with the node/edge attribute
# schema documented in this module. networkx ships no type stubs, so the alias resolves to
# Any for the type checker while keeping signatures self-documenting; a future wrapper
# type is a one-line change here.
Graph: TypeAlias = Any

_KIND_PRECEDENCE = ("automatic", "inferred", "manual")


def _label_kind(kinds: set[str]) -> str:
    """Collapse a set of contributing edge kinds to one honest label.

    ``automatic`` wins because if Terraform tracks the edge at all it is *not* a hidden
    dependency; ``inferred`` next; ``manual`` only when it is the sole source.
    """
    for kind in _KIND_PRECEDENCE:
        if kind in kinds:
            return kind
    return "manual"


def parse_tf_address(address: str) -> tuple[str, str]:
    """Best-effort ``(resource_type, name)`` from a Terraform address.

    Handles module prefixes and count/for_each index suffixes, e.g.
    ``module.data_plane.google_compute_subnetwork.prod_subnet[0]`` ->
    ``("google_compute_subnetwork", "prod_subnet")``.
    """
    addr = address.split("[", 1)[0]
    parts = addr.split(".")
    # Strip leading module.<name> pairs.
    while len(parts) >= 2 and parts[0] == "module":
        parts = parts[2:]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "unknown", addr


def _ensure_managed_node(g: Graph, resource: Resource) -> None:
    g.add_node(
        resource.resource_id,
        resource_type=resource.resource_type,
        name=resource.name,
        provider=resource.provider,
        terraform_address=resource.terraform_address,
        source=resource.source,
        external=False,
        managed=True,
    )


def _ensure_external_node(
    g: Graph,
    node_id: str,
    *,
    resource_type: str,
    name: str,
    terraform_address: str | None = None,
) -> None:
    """Add an external placeholder node, unless ``node_id`` is already a managed node."""
    if node_id in g and g.nodes[node_id].get("managed"):
        return
    if node_id in g:
        # Already an external node; only fill in missing metadata.
        data = g.nodes[node_id]
        if data.get("resource_type") in (None, "unknown") and resource_type != "unknown":
            data["resource_type"] = resource_type
        if terraform_address and not data.get("terraform_address"):
            data["terraform_address"] = terraform_address
        return
    g.add_node(
        node_id,
        resource_type=resource_type,
        name=name,
        provider=None,
        terraform_address=terraform_address,
        source=None,
        external=True,
        managed=False,
    )


def _add_edge(
    g: Graph,
    src: str,
    dst: str,
    *,
    kind: str,
    reason: str,
    source_attribute: str | None = None,
) -> None:
    """Add or merge a dependency edge. Self-edges are skipped."""
    if src == dst:
        return
    if g.has_edge(src, dst):
        data = g.edges[src, dst]
        kinds = set(data.get("kinds", []))
        kinds.add(kind)
        data["kinds"] = sorted(kinds)
        data["kind"] = _label_kind(kinds)
        reasons = list(data.get("reasons", []))
        if reason not in reasons:
            reasons.append(reason)
        data["reasons"] = reasons
        data["reason"] = "; ".join(reasons)
        if source_attribute and not data.get("source_attribute"):
            data["source_attribute"] = source_attribute
        return
    g.add_edge(
        src,
        dst,
        kind=kind,
        kinds=[kind],
        reason=reason,
        reasons=[reason],
        source_attribute=source_attribute,
    )


def build_graph(
    resources: list[Resource],
    manual_edges: Iterable[ManualEdge] | None = None,
) -> Graph:
    """Build the dependency graph from normalized resources (+ optional manual edges)."""
    g: Graph = nx.DiGraph()

    # 1. Managed nodes + address→id resolution index.
    address_index: dict[str, str] = {}
    for r in resources:
        _ensure_managed_node(g, r)
        if r.terraform_address:
            address_index[r.terraform_address] = r.resource_id

    def resolve(ref: str) -> tuple[str, bool]:
        """Return (node_id, is_managed). Tries the address index, else treats ref as id."""
        if ref in address_index:
            return address_index[ref], True
        node = g.nodes.get(ref)
        if node is not None and node.get("managed"):
            return ref, True
        return ref, False

    # 2. Automatic edges from Terraform depends_on.
    for r in resources:
        for dep in r.depends_on:
            target, is_managed = resolve(dep)
            if not is_managed:
                rtype, rname = parse_tf_address(dep)
                _ensure_external_node(
                    g, target, resource_type=rtype, name=rname, terraform_address=dep
                )
            _add_edge(
                g,
                r.resource_id,
                target,
                kind="automatic",
                reason="depends_on in Terraform state",
            )

    # 3. Inferred edges from resource attributes (parent_refs).
    for r in resources:
        for edge in inferred_edges(r):
            if edge.target_id not in g or not g.nodes[edge.target_id].get("managed"):
                _ensure_external_node(
                    g,
                    edge.target_id,
                    resource_type=edge.target_type,
                    name=edge.target_name,
                )
            _add_edge(
                g,
                edge.source_id,
                edge.target_id,
                kind="inferred",
                reason=edge.reason,
                source_attribute=edge.source_attribute,
            )

    # 3b. Cross-resource inferred edges: firewall applicability (instance -> firewall).
    for edge in firewall_applicability_edges(resources):
        if edge.target_id not in g or not g.nodes[edge.target_id].get("managed"):
            _ensure_external_node(
                g, edge.target_id, resource_type=edge.target_type, name=edge.target_name
            )
        _add_edge(
            g,
            edge.source_id,
            edge.target_id,
            kind="inferred",
            reason=edge.reason,
            source_attribute=edge.source_attribute,
        )

    # 4. Manual edges from statewatch.yaml.
    for medge in manual_edges or []:
        src, _ = resolve(medge.from_ref)
        dst, _ = resolve(medge.to_ref)
        for ref, node_id in ((medge.from_ref, src), (medge.to_ref, dst)):
            if node_id not in g:
                rtype, rname = (
                    parse_tf_address(ref) if "." in ref and "/" not in ref else ("unknown", ref)
                )
                _ensure_external_node(g, node_id, resource_type=rtype, name=rname)
        _add_edge(
            g,
            src,
            dst,
            kind="manual",
            # The renderer prefixes "[manual: ...]" from `kind`; don't double it here.
            reason=medge.reason,
        )

    return g


def edge_records(g: Graph) -> list[dict[str, Any]]:
    """Flatten edges to plain dicts (used by JSON/text/DOT renderers and tests)."""
    records: list[dict[str, Any]] = []
    for src, dst, data in g.edges(data=True):
        records.append(
            {
                "from": src,
                "to": dst,
                "kind": data.get("kind"),
                "kinds": data.get("kinds", []),
                "reason": data.get("reason"),
                "source_attribute": data.get("source_attribute"),
            }
        )
    return records
