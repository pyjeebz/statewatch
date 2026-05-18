"""statewatch command-line interface.

``scan`` diffs live GCP state against Terraform state and reports severity × impact
(``--watch`` for continuous mode, ``--output json``, Slack via ``--config``). ``graph``
prints the dependency graph. ``init`` generates a starter ``statewatch.yaml``.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import schedule
import typer
from rich.console import Console

from statewatch import __version__
from statewatch.adapters.base import AdapterAuthError, AdapterError
from statewatch.adapters.gcp import GCPAdapter
from statewatch.config import ConfigError, load_config
from statewatch.differ import diff_resources
from statewatch.graph.builder import build_graph
from statewatch.graph.inferred import firewall_applicability_edges, inferred_edges
from statewatch.graph.manual import ManualEdgeError, load_manual_edges
from statewatch.graph.render import render as render_graph
from statewatch.graph.validator import validate_graph
from statewatch.normalizer import Resource
from statewatch.notifiers.slack import SlackError, post_report
from statewatch.notifiers.terminal import render_report
from statewatch.report import Report, build_report
from statewatch.resources import SUPPORTED_RESOURCE_TYPES, normalize_tf
from statewatch.tfstate import (
    TerraformStateError,
    iter_managed_resources,
    load_tfstate,
)
from statewatch.watchstate import changed_findings, state_key
from statewatch.watchstate import save as save_state

_INTERVAL = re.compile(r"^\s*(\d+)\s*([smh])\s*$")


def _parse_interval(value: str) -> int:
    """'30s' / '5m' / '1h' -> seconds. Raises ValueError on a bad spec."""
    m = _INTERVAL.match(value)
    if not m:
        raise ValueError(f"invalid --watch interval {value!r} (use e.g. 30s, 5m, 1h)")
    n, unit = int(m.group(1)), m.group(2)
    if n <= 0:
        raise ValueError("--watch interval must be positive")
    return n * {"s": 1, "m": 60, "h": 3600}[unit]

app = typer.Typer(
    add_completion=False,
    help="Dependency-aware infrastructure drift detector for GCP.",
    no_args_is_help=True,
)

_err = Console(stderr=True)


def _load_tf_resources(tfstate: str, project: str) -> list[Resource]:
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


def _produce_report(tfstate: str, project: str, *, stub: bool) -> Report:
    """Steps 1-3 of a scan: load state, fetch live, diff, classify, impact. Exits(2) on
    any load/auth/fetch failure with a clean message."""
    tf_resources = _load_tf_resources(tfstate, project)
    graph = build_graph(tf_resources)

    adapter = GCPAdapter(stub=stub or None)
    try:
        adapter.authenticate()
    except AdapterAuthError as exc:
        # KNOWN_ISSUES #2 (resolved): with a real CAI call, missing credentials is fatal.
        _err.print(f"[red]GCP authentication failed:[/red] {exc}")
        _err.print("[dim]Re-run with --stub for an offline demo.[/dim]")
        raise typer.Exit(code=2) from exc
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

    return build_report(diff_resources(tf_resources, live_resources), graph)


def _maybe_slack(report: Report, config_path: Path | None) -> None:
    if config_path is None:
        return
    try:
        cfg = load_config(config_path)
    except (ConfigError, FileNotFoundError) as exc:
        _err.print(f"[red]Error reading {config_path}:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    if cfg.slack is None:
        return
    try:
        sent = post_report(report, cfg.slack)
        if sent:
            _err.print("[dim]Posted findings to Slack.[/dim]")
    except SlackError as exc:
        _err.print(f"[yellow]Slack notification failed:[/yellow] {exc}")


@app.command()
def scan(
    tfstate: str = typer.Option(
        ..., "--tfstate",
        help="Terraform state: a local .tfstate path or a gs://bucket/path URI.",
    ),
    project: str = typer.Option(
        ..., "--project", help="GCP project id to compare live state against."
    ),
    output: str = typer.Option("text", "--output", help="Output format: text | json."),
    stub: bool = typer.Option(
        False, "--stub",
        help="Use built-in offline sample live-state instead of Cloud Asset Inventory.",
    ),
    config: Path | None = typer.Option(
        None, "--config", exists=True, dir_okay=False, readable=True,
        help="statewatch.yaml (Slack notifications, watch interval).",
    ),
    watch: str | None = typer.Option(
        None, "--watch",
        help="Continuous mode at an interval (e.g. 30m, 1h); notify only on new/changed "
        "drift. Runs until interrupted.",
    ),
) -> None:
    """Compare live GCP state against Terraform state; report severity × impact."""
    if output not in ("text", "json"):
        _err.print(f"[red]Unknown --output {output!r}[/red] (expected text|json)")
        raise typer.Exit(code=2)

    if not watch:
        report = _produce_report(tfstate, project, stub=stub)
        if output == "json":
            typer.echo(json.dumps(report.to_dict(), indent=2))
        else:
            render_report(report)
        _maybe_slack(report, config)
        raise typer.Exit(code=report.exit_code())

    # --watch: scan on an interval, notify only on new/changed drift.
    try:
        seconds = _parse_interval(watch)
    except ValueError as exc:
        _err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    key = state_key(project, tfstate)
    _err.print(f"[dim]watch: scanning every {watch}; Ctrl-C to stop.[/dim]")

    def tick() -> None:
        report = _produce_report(tfstate, project, stub=stub)
        changed = changed_findings(report, key)
        if changed:
            # Notify on ONLY the new/changed findings — never re-surface unchanged
            # drift just because some other finding changed. State is still saved
            # from the full report so every current fingerprint is remembered.
            notify = Report(findings=changed)
            render_report(notify)
            _maybe_slack(notify, config)
        else:
            _err.print("[dim]no new or changed drift.[/dim]")
        save_state(report, key)

    tick()  # first run establishes/refreshes the baseline and reports current drift
    schedule.every(seconds).seconds.do(tick)
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        raise typer.Exit(code=0) from None


@app.command()
def graph(
    tfstate: str = typer.Option(
        ...,
        "--tfstate",
        help="Terraform state: a local .tfstate path or a gs://bucket/path URI.",
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


@app.command()
def init(
    tfstate: str = typer.Option(
        ..., "--tfstate",
        help="Terraform state to scan: a local .tfstate path or a gs:// URI.",
    ),
    project: str = typer.Option(..., "--project", help="GCP project id."),
    out: Path = typer.Option(
        Path("statewatch.yaml"), "--out", help="Where to write the config."
    ),
) -> None:
    """Generate a starter statewatch.yaml from a Terraform state.

    Discovered resource types are written as fact; inferred dependencies are emitted as
    *commented-out* suggestions to review and opt into (the ``dependencies:`` block is for
    coupling Terraform can't express — never auto-asserted).
    """
    if out.exists():
        _err.print(f"[red]{out} already exists[/red] — refusing to overwrite.")
        raise typer.Exit(code=2)

    resources = _load_tf_resources(tfstate, project)
    types = sorted({r.resource_type for r in resources})

    suggestions: list[str] = []
    by_id = {r.resource_id: r for r in resources}
    for r in resources:
        for e in inferred_edges(r):
            tgt = by_id.get(e.target_id)
            tname = tgt.terraform_address or e.target_id if tgt else e.target_id
            suggestions.append(
                f"#  - from: {r.terraform_address or r.resource_id}\n"
                f"#    to: {tname}\n"
                f"#    reason: {e.reason}"
            )
    for e in firewall_applicability_edges(resources):
        src, tgt = by_id.get(e.source_id), by_id.get(e.target_id)
        suggestions.append(
            f"#  - from: {src.terraform_address if src else e.source_id}\n"
            f"#    to: {tgt.terraform_address if tgt else e.target_id}\n"
            f"#    reason: {e.reason}"
        )

    lines = [
        "version: 1",
        f"project: {project}",
        "",
        "resources:",
        *[f"  - {t}" for t in types],
        "",
        "# Inferred dependencies statewatch already derives automatically — listed here",
        "# only as review hints. Uncomment to pin a cross-project / non-Terraform edge:",
        "# dependencies:",
        *(suggestions or ["#  (none inferred)"]),
        "",
        "# notifications:",
        "#   slack:",
        "#     webhook: ${SLACK_WEBHOOK_URL}",
        "#     severity_threshold: medium",
        "#     include_impact_summary: true",
        "",
        "# watch:",
        "#   interval: 30m",
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    _err.print(
        f"[green]Wrote {out}[/green] — {len(types)} resource type(s), "
        f"{len(suggestions)} inferred-edge hint(s)."
    )
    raise typer.Exit(code=0)


if __name__ == "__main__":  # pragma: no cover
    app()
