"""Render a dependency graph as text, JSON, or Graphviz DOT.

DOT is the only visualization statewatch ships — it is the universal interchange format,
so users pipe it to graphviz themselves (``statewatch graph --format dot | dot -Tpng``).
No SVG/Mermaid/D3 generators (scope guard).
"""

from __future__ import annotations

import json
from typing import Any

from statewatch.graph.builder import Graph, edge_records
from statewatch.graph.validator import GraphValidation


def _node_label(g: Graph, node_id: str) -> str:
    data = g.nodes[node_id]
    rtype = data.get("resource_type") or "unknown"
    tag = " [external]" if data.get("external") else ""
    return f"{rtype} {data.get('name') or node_id}{tag}"


def render_text(g: Graph, validation: GraphValidation | None = None) -> str:
    """Adjacency-list view, grouped by source node, dependencies indented beneath."""
    lines: list[str] = []
    managed = sorted(n for n, d in g.nodes(data=True) if not d.get("external"))
    external = sorted(n for n, d in g.nodes(data=True) if d.get("external"))

    lines.append(
        f"Dependency graph: {g.number_of_nodes()} nodes "
        f"({len(managed)} managed, {len(external)} external), "
        f"{g.number_of_edges()} edges"
    )
    lines.append("")

    for node in managed:
        lines.append(f"{_node_label(g, node)}  ({node})")
        out_edges = sorted(g.out_edges(node, data=True), key=lambda e: e[1])
        if not out_edges:
            lines.append("    (no dependencies)")
        for _, dst, data in out_edges:
            lines.append(
                f"    └─ depends on {_node_label(g, dst)}  "
                f"[{data.get('kind')}: {data.get('reason')}]"
            )
        lines.append("")

    if external:
        lines.append("External / unmanaged targets (not in Terraform state):")
        for node in external:
            lines.append(f"  - {_node_label(g, node)}  ({node})")
        lines.append("")

    if validation and not validation.ok:
        lines.append("Validation warnings:")
        for w in validation.warnings():
            lines.append(f"  ! {w}")

    return "\n".join(lines).rstrip() + "\n"


def render_json(g: Graph, validation: GraphValidation | None = None) -> str:
    """Stable JSON: ``{nodes, edges, validation}``."""
    nodes = [
        {
            "id": n,
            "resource_type": d.get("resource_type"),
            "name": d.get("name"),
            "provider": d.get("provider"),
            "terraform_address": d.get("terraform_address"),
            "external": bool(d.get("external")),
            "managed": bool(d.get("managed")),
        }
        for n, d in sorted(g.nodes(data=True))
    ]
    edges = sorted(edge_records(g), key=lambda e: (e["from"], e["to"]))
    payload: dict[str, Any] = {"nodes": nodes, "edges": edges}
    if validation is not None:
        payload["validation"] = {
            "ok": validation.ok,
            "cycles": validation.cycles,
            "orphans": validation.orphans,
            "external_refs": validation.external_refs,
            "warnings": validation.warnings(),
        }
    return json.dumps(payload, indent=2, sort_keys=False)


def _dot_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_dot(g: Graph, validation: GraphValidation | None = None) -> str:
    """Valid Graphviz DOT. External nodes are dashed; edges colored by kind."""
    edge_color = {"automatic": "black", "inferred": "blue", "manual": "darkgreen"}
    lines = ["digraph statewatch {", "  rankdir=LR;", '  node [shape=box];', ""]

    for node, data in sorted(g.nodes(data=True)):
        rtype = data.get("resource_type") or "unknown"
        label = _dot_escape(f"{rtype}\\n{data.get('name') or node}")
        if data.get("external"):
            style = f'[label="{label}", style="dashed,filled", fillcolor="lightgrey"]'
        else:
            style = f'[label="{label}", style=filled, fillcolor="white"]'
        lines.append(f'  "{_dot_escape(node)}" {style};')

    lines.append("")
    for src, dst, data in sorted(g.edges(data=True), key=lambda e: (e[0], e[1])):
        kind = data.get("kind", "automatic")
        color = edge_color.get(kind, "black")
        elabel = _dot_escape(kind)
        lines.append(
            f'  "{_dot_escape(src)}" -> "{_dot_escape(dst)}" '
            f'[label="{elabel}", color="{color}"];'
        )

    lines.append("}")
    return "\n".join(lines) + "\n"


def render(fmt: str, g: Graph, validation: GraphValidation | None = None) -> str:
    """Dispatch on format name. Raises ValueError on an unknown format."""
    if fmt == "text":
        return render_text(g, validation)
    if fmt == "json":
        return render_json(g, validation)
    if fmt == "dot":
        return render_dot(g, validation)
    raise ValueError(f"unknown graph format: {fmt!r} (expected text|json|dot)")
