"""statewatch command-line interface.

``scan`` (Phase 1) diffs live GCP state against Terraform state. ``graph`` (Phase 2)
builds and prints the resource dependency graph. ``init``, ``--watch`` and friends arrive
in later phases.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console

from statewatch import __version__
from statewatch.adapters.base import AdapterAuthError, AdapterError
from statewatch.adapters.gcp import GCPAdapter
from statewatch.differ import diff_resources
from statewatch.graph.builder import build_graph
from statewatch.graph.manual import ManualEdgeError, load_manual_edges
from statewatch.graph.render import render as render_graph
from statewatch.graph.validator import validate_graph
from statewatch.normalizer import Resource
from statewatch.notifiers.terminal import render_report
from statewatch.report import build_report
from statewatch.resources import SUPPORTED_RESOURCE_TYPES, normalize_tf
from statewatch.tfstate import (
    TerraformStateError,
    iter_managed_resources,
    load_tfstate,
)

app = typer.Typer(
    add_completion=False,
    help="Dependency-aware infrastructure drift detector for GCP.",
    no_args_is_help=True,
)

_err = Console(stderr=True)


def _load_tf_resources(tfstate: Path, project: str) -> list[Resource]:
    """Load + normalize compute instances from Terraform state, with graph metadata.

    Shared by ``scan`` and ``graph`` so both see identical resource_ids, terraform
    addresses and depends_on. Exits(2) with a clean message on a bad state file.
    """
    try:
        state = load_tfstate(tfstate)
    except (TerraformStateError, FileNotFoundError) as exc:
        _err.print(f"[red]Error reading Terraform state:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    resources: list[Resource] = []
    for inst in iter_managed_resources(state):
        r = normalize_tf(
            inst.type,
            inst.attributes,
            project=project,
            terraform_address=inst.address,
            depends_on=inst.dependencies,
        )
        if r is not None:  # unsupported types are skipped, not errors
            resources.append(r)
    return resources


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"statewatch {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    _version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),
) -> None:
    """statewatch — your infrastructure drifted; this tells you what changed."""


@app.command()
def scan(
    tfstate: Path = typer.Option(
        ...,
        "--tfstate",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Path to the Terraform state file (.tfstate).",
    ),
    project: str = typer.Option(
        ...,
        "--project",
        help="GCP project id to compare live state against.",
    ),
    output: str = typer.Option(
        "text",
        "--output",
        help="Output format: text | json.",
    ),
) -> None:
    """Compare live GCP state against Terraform state; report severity × impact."""
    if output not in ("text", "json"):
        _err.print(f"[red]Unknown --output {output!r}[/red] (expected text|json)")
        raise typer.Exit(code=2)

    # 1. Load + normalize Terraform state, build the dependency graph from it.
    tf_resources = _load_tf_resources(tfstate, project)
    graph = build_graph(tf_resources)

    # 2. Fetch live state from the cloud adapter.
    adapter = GCPAdapter()
    try:
        adapter.authenticate()
    except AdapterAuthError as exc:
        # The live-state fetch is still stubbed, so missing credentials isn't fatal yet —
        # warn and carry on so scan is demonstrable offline. KNOWN_ISSUES #2: this MUST
        # become a hard exit once a real Cloud Asset Inventory call lands.
        _err.print(f"[yellow]Warning:[/yellow] {exc}")
        _err.print("[yellow]Proceeding with stubbed live state.[/yellow]")
    except AdapterError as exc:
        _err.print(f"[red]GCP authentication failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    try:
        live_resources = adapter.fetch_resources(
            sorted(SUPPORTED_RESOURCE_TYPES), scope=project
        )
    except AdapterError as exc:
        _err.print(f"[red]Failed to fetch live state:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    # 3. Diff -> classify severity -> traverse graph for impact -> report.
    diffs = diff_resources(tf_resources, live_resources)
    report = build_report(diffs, graph)

    if output == "json":
        typer.echo(json.dumps(report.to_dict(), indent=2))
    else:
        render_report(report)

    raise typer.Exit(code=report.exit_code())


@app.command()
def graph(
    tfstate: Path = typer.Option(
        ...,
        "--tfstate",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Path to the Terraform state file (.tfstate).",
    ),
    project: str = typer.Option(
        ...,
        "--project",
        help="GCP project id (used to derive canonical resource ids).",
    ),
    fmt: str = typer.Option(
        "text",
        "--format",
        help="Output format: text | json | dot.",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        exists=True,
        dir_okay=False,
        readable=True,
        help="statewatch.yaml with manual dependency edges (optional).",
    ),
) -> None:
    """Build and print the resource dependency graph (no live state needed)."""
    if fmt not in ("text", "json", "dot"):
        _err.print(f"[red]Unknown --format {fmt!r}[/red] (expected text|json|dot)")
        raise typer.Exit(code=2)

    tf_resources = _load_tf_resources(tfstate, project)

    manual_edges = []
    if config is not None:
        try:
            manual_edges = load_manual_edges(config)
        except (ManualEdgeError, FileNotFoundError) as exc:
            _err.print(f"[red]Error reading {config}:[/red] {exc}")
            raise typer.Exit(code=2) from exc

    g = build_graph(tf_resources, manual_edges)
    validation = validate_graph(g)

    # Validation findings go to stderr so stdout stays a clean, pipeable artifact
    # (e.g. `statewatch graph --format dot | dot -Tpng`).
    for warning in validation.warnings():
        _err.print(f"[yellow]warning:[/yellow] {warning}")

    typer.echo(render_graph(fmt, g, validation), nl=False)
    raise typer.Exit(code=0)


if __name__ == "__main__":  # pragma: no cover
    app()
