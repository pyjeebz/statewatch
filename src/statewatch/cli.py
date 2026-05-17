"""statewatch command-line interface.

``scan`` (Phase 1) diffs live GCP state against Terraform state. ``graph`` (Phase 2)
builds and prints the resource dependency graph. ``init``, ``--watch`` and friends arrive
in later phases.
"""

from __future__ import annotations

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
from statewatch.normalizer import Resource, normalize_compute_instance_from_tfstate
from statewatch.notifiers.terminal import render_drift
from statewatch.tfstate import (
    TerraformStateError,
    extract_compute_instances,
    load_tfstate,
)

app = typer.Typer(
    add_completion=False,
    help="Dependency-aware infrastructure drift detector for GCP.",
    no_args_is_help=True,
)

_err = Console(stderr=True)

# Resource types statewatch supports in v0.1 Phase 1.
_PHASE1_TYPES = ["google_compute_instance"]


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

    return [
        normalize_compute_instance_from_tfstate(
            inst.attributes,
            project=project,
            terraform_address=inst.address,
            depends_on=inst.dependencies,
        )
        for inst in extract_compute_instances(state)
    ]


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
) -> None:
    """Compare live GCP state against Terraform state and report drift."""
    # 1. Load + parse + normalize Terraform state.
    tf_resources = _load_tf_resources(tfstate, project)

    # 2. Fetch live state from the cloud adapter.
    adapter = GCPAdapter()
    try:
        adapter.authenticate()
    except AdapterAuthError as exc:
        # Phase 1 note: the live-state fetch is still stubbed, so a missing-credentials
        # situation isn't fatal yet — warn and carry on so `scan` is demonstrable offline.
        # Once fetch_resources makes a real Cloud Asset Inventory call this MUST become a
        # hard error (raise typer.Exit(2)).
        _err.print(f"[yellow]Warning:[/yellow] {exc}")
        _err.print("[yellow]Proceeding with stubbed live state (Phase 1).[/yellow]")
    except AdapterError as exc:
        _err.print(f"[red]GCP authentication failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    try:
        live_resources = adapter.fetch_resources(_PHASE1_TYPES, scope=project)
    except AdapterError as exc:
        _err.print(f"[red]Failed to fetch live state:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    # 3. Diff and render.
    results = diff_resources(tf_resources, live_resources)
    render_drift(results)

    # Phase 1: informational only. Severity-based exit codes land in Phase 3.
    raise typer.Exit(code=0)


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
