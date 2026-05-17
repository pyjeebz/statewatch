"""Graph validation — structural sanity checks.

Phase 2 deliverable: detect cycles, orphans, and references to resources that aren't in
Terraform state. **This never raises and never crashes the run** — every finding is a
warning. A drift graph with an unresolved dependency is still a useful drift graph; the
point is to surface the issue, not abort.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from statewatch.graph.builder import Graph


@dataclass
class GraphValidation:
    """Outcome of validating a dependency graph. All findings are advisory."""

    #: Lists of node ids forming a dependency cycle.
    cycles: list[list[str]] = field(default_factory=list)
    #: Managed resources with no edges in or out (suspiciously disconnected).
    orphans: list[str] = field(default_factory=list)
    #: Node ids that are depended upon but aren't in Terraform state (external).
    external_refs: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when there is nothing worth warning about."""
        return not (self.cycles or self.orphans or self.external_refs)

    def warnings(self) -> list[str]:
        """Human-readable advisory lines (empty when :attr:`ok`)."""
        out: list[str] = []
        for cycle in self.cycles:
            out.append("dependency cycle: " + " -> ".join([*cycle, cycle[0]]))
        for node in self.orphans:
            out.append(f"orphan resource (no dependencies in or out): {node}")
        for node in self.external_refs:
            out.append(
                f"depends on a resource not in Terraform state (external): {node}"
            )
        return out


def validate_graph(g: Graph) -> GraphValidation:
    """Validate ``g``. Pure and total — returns findings, raises nothing."""
    result = GraphValidation()

    # Cycles. simple_cycles is well-defined on a DiGraph; sorted for stable output.
    try:
        cycles = [list(c) for c in nx.simple_cycles(g)]
    except Exception:  # defensive: validation must never crash the run
        cycles = []
    result.cycles = sorted(cycles, key=lambda c: (len(c), c))

    # Orphans: managed nodes with degree 0. External placeholders are expected to be
    # leaves, so they are never orphans.
    for node, data in g.nodes(data=True):
        if not data.get("managed"):
            continue
        if g.in_degree(node) == 0 and g.out_degree(node) == 0:
            result.orphans.append(node)
    result.orphans.sort()

    # External / unresolved references.
    result.external_refs = sorted(
        n for n, d in g.nodes(data=True) if d.get("external")
    )

    return result
