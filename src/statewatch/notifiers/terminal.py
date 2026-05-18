"""Terminal output — severity × impact, color-coded.

Renders a :class:`statewatch.report.Report`: each finding shows its severity, what
changed, and the blast radius (DIRECT/INDIRECT/WATCH counts inline). JSON output is the
report's ``to_dict`` (emitted by the CLI); Slack/PR come in Phase 4.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.table import Table

from statewatch.classifier import CRITICAL, LOW, MEDIUM
from statewatch.differ import ADDED, MODIFIED, REMOVED
from statewatch.impact import rules
from statewatch.report import Report

_SEV_STYLE = {CRITICAL: "bold red", MEDIUM: "yellow", LOW: "dim", "NONE": "dim"}
_KIND_STYLE = {ADDED: "green", REMOVED: "red", MODIFIED: "yellow"}
_LABEL_STYLE = {rules.DIRECT: "red", rules.INDIRECT: "yellow", rules.WATCH: "dim"}
_STATUS_LABEL = {
    "drifted": "drifted",
    "missing_in_live": "missing in live (in Terraform, not in GCP)",
    "unmanaged": "unmanaged (in GCP, not in Terraform)",
}


def _fmt(value: Any) -> str:
    if value is None:
        return "∅"
    if isinstance(value, str):
        return value or '""'
    return repr(value)


def _impact_summary(counts: dict[str, int]) -> str:
    parts = []
    for label in (rules.DIRECT, rules.INDIRECT, rules.WATCH):
        n = counts.get(label, 0)
        if n:
            style = _LABEL_STYLE[label]
            parts.append(f"[{style}]{n} {label}[/{style}]")
    return ", ".join(parts) if parts else "[dim]no downstream impact[/dim]"


def render_report(report: Report, *, console: Console | None = None) -> None:
    """Print the severity × impact report. Clean message when there's no drift."""
    console = console or Console()

    if not report.findings:
        console.print("[green]✓ No drift detected.[/green] Live state matches Terraform.")
        return

    for f in report.findings:
        sev_style = _SEV_STYLE.get(f.severity, "white")
        status = _STATUS_LABEL.get(f.status, f.status)
        counts = f.label_counts()
        console.print(
            f"\n[{sev_style}]{f.severity}[/{sev_style}] "
            f"[bold]{f.resource_type}[/bold] {f.name} "
            f"[dim]({status})[/dim] — {_impact_summary(counts)}"
        )
        console.print(f"  [dim]{f.resource_id}[/dim]")

        if f.changes:
            table = Table(show_header=True, header_style="dim", box=None, pad_edge=False)
            table.add_column("  attribute")
            table.add_column("Terraform", overflow="fold")
            table.add_column("Live", overflow="fold")
            table.add_column("change", justify="center")
            table.add_column("sev", justify="center")
            for ch in f.changes:
                ks = _KIND_STYLE.get(ch.change_kind, "white")
                csev = f.change_severity.get(ch.path, "")
                cstyle = _SEV_STYLE.get(csev, "dim")
                table.add_row(
                    f"  {ch.path}",
                    _fmt(ch.old_value),
                    _fmt(ch.new_value),
                    f"[{ks}]{ch.change_kind}[/{ks}]",
                    f"[{cstyle}]{csev}[/{cstyle}]",
                )
            console.print(table)

        direct = [n for n in f.impacted if n.label == rules.DIRECT]
        if direct:
            shown = ", ".join(f"{n.resource_type}/{n.name}" for n in direct[:8])
            extra = f" (+{len(direct) - 8} more)" if len(direct) > 8 else ""
            console.print(f"  [red]DIRECT:[/red] {shown}{extra}")

    sev = report.highest_severity()
    code = report.exit_code()
    console.print(
        f"\n[bold]Summary:[/bold] {len(report.findings)} finding(s); "
        f"highest severity [{_SEV_STYLE.get(sev, 'white')}]{sev}[/]; exit code {code}."
    )
