"""Terminal output for drift results, using ``rich`` tables.

Phase 1's only renderer. JSON output, Slack, and PR comments come in later phases; they
will consume the same :class:`statewatch.differ.ResourceDiff` list.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.table import Table

from statewatch.differ import ADDED, MODIFIED, REMOVED, ResourceDiff

_KIND_STYLE = {ADDED: "green", REMOVED: "red", MODIFIED: "yellow"}
_STATUS_LABEL = {
    "drifted": "DRIFTED",
    "missing_in_live": "MISSING IN LIVE",
    "unmanaged": "UNMANAGED (not in Terraform)",
}


def _fmt(value: Any) -> str:
    if value is None:
        return "∅"
    if isinstance(value, str):
        return value or '""'
    return repr(value)


def render_drift(results: list[ResourceDiff], *, console: Console | None = None) -> None:
    """Print drift results as a table. Prints a clean message when there is no drift."""
    console = console or Console()

    if not results:
        console.print("[green]✓ No drift detected.[/green] Live state matches Terraform.")
        return

    drifted = [r for r in results if r.status == "drifted"]
    other = [r for r in results if r.status != "drifted"]

    if drifted:
        table = Table(title="Infrastructure drift", show_lines=True)
        table.add_column("Resource", style="bold cyan", no_wrap=False)
        table.add_column("Type", style="dim")
        table.add_column("Attribute path")
        table.add_column("Terraform", overflow="fold")
        table.add_column("Live", overflow="fold")
        table.add_column("Change", justify="center")
        for rd in drifted:
            for i, ch in enumerate(rd.changes):
                style = _KIND_STYLE.get(ch.change_kind, "white")
                table.add_row(
                    rd.name if i == 0 else "",
                    rd.resource_type if i == 0 else "",
                    ch.path,
                    _fmt(ch.old_value),
                    _fmt(ch.new_value),
                    f"[{style}]{ch.change_kind}[/{style}]",
                )
        console.print(table)

    for rd in other:
        label = _STATUS_LABEL.get(rd.status, rd.status)
        console.print(
            f"[yellow]• {label}[/yellow]: {rd.resource_type} "
            f"[bold]{rd.name}[/bold] ({rd.resource_id})"
        )

    total_changes = sum(len(r.changes) for r in drifted)
    console.print(
        f"\n[bold]Summary:[/bold] {len(drifted)} resource(s) drifted "
        f"({total_changes} attribute change(s)); {len(other)} other finding(s)."
    )
