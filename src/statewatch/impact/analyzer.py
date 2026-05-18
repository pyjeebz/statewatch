"""Impact analyzer — who is affected when a resource drifts?

Edge direction (locked Phase 2): ``A -> B`` means "A depends on B". So the blast radius
of a drifted node B is its **transitive predecessors** (everything with a directed path
*into* B), not its successors. Get this backwards and the tool is worse than useless.

``nx.single_target_shortest_path_length(g, B)`` yields ``(source, distance)`` for every
node that can reach B — exactly the predecessor set, with the hop distance used for
DIRECT / INDIRECT / WATCH labelling.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from statewatch.graph.builder import Graph
from statewatch.impact import rules


@dataclass(frozen=True)
class ImpactedNode:
    """One resource affected by a drift, with its blast-radius label."""

    resource_id: str
    resource_type: str
    name: str
    distance: int
    label: str
    external: bool


def analyze_impact(
    graph: Graph,
    drifted_id: str,
    *,
    propagating: bool,
) -> list[ImpactedNode]:
    """Return the impacted nodes (transitive predecessors) of ``drifted_id``.

    Returns ``[]`` (never raises) when the drifted resource isn't a managed node in the
    graph — an external/placeholder or absent node can't have a meaningful blast radius;
    the caller surfaces a warning. A managed node with no predecessors also yields ``[]``,
    which is normal (nothing depends on it), not an error.
    """
    if drifted_id not in graph:
        return []
    if graph.nodes[drifted_id].get("external"):
        return []

    # networkx 3.5+ returns a dict here; older versions an iterator of pairs.
    raw = nx.single_target_shortest_path_length(graph, drifted_id)
    distances = raw.items() if isinstance(raw, dict) else raw

    impacted: list[ImpactedNode] = []
    for source, dist in distances:
        if source == drifted_id or dist == 0:
            continue
        data = graph.nodes[source]
        impacted.append(
            ImpactedNode(
                resource_id=source,
                resource_type=data.get("resource_type") or "unknown",
                name=data.get("name") or source,
                distance=dist,
                label=rules.label_for(dist, propagating),
                external=bool(data.get("external")),
            )
        )

    impacted.sort(key=lambda n: (rules.LABEL_ORDER.get(n.label, 9), n.distance, n.resource_id))
    return impacted
