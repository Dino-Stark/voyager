"""Voyager CLI entry point.

Usage:
    voyager scan <project_path>
    voyager plan rename <class.field> <new_name>
    voyager apply
    voyager status
"""

from __future__ import annotations

import logging
import sys

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from cli.commands.apply import apply_plan
from cli.commands.apply import _find_project_root as _find_project_root_for_status
from cli.commands.plan import plan_operation
from cli.commands.scan import scan_project

console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output")
@click.version_option(version="0.1.0", prog_name="voyager")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """
    Voyager - Semantic code modification system.
    """
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    _setup_logging(verbose)


@cli.command()
@click.argument("project_path", type=click.Path(exists=True, file_okay=False))
@click.pass_context
def scan(ctx: click.Context, project_path: str) -> None:
    """
    Scan a Java project and build the semantic graph.
    """
    from pathlib import Path
    result = scan_project(Path(project_path))
    if result:
        console.print(f"[green]Scan complete:[/green] {result.symbols_count} symbols, {result.references_count} references")
    else:
        console.print("[red]Scan failed.[/red]")
        sys.exit(1)


@cli.command()
@click.argument("op_type", type=click.Choice(["rename", "add_field", "remove_field"]))
@click.argument("target")
@click.argument("value", required=False)
@click.pass_context
def plan(ctx: click.Context, op_type: str, target: str, value: str | None) -> None:
    """
    Plan an operation and show affected files.
    """
    result = plan_operation(op_type, target, value)
    if result is None:
        sys.exit(1)


@cli.command()
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
@click.pass_context
def apply(ctx: click.Context, yes: bool) -> None:
    """
    Apply the last planned operation.
    """
    result = apply_plan(skip_confirm=yes)
    if result is None:
        sys.exit(1)


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """
    Show current project status and graph info.
    """
    from storage.manager import StorageManager

    manager = StorageManager(_find_project_root_for_status())
    graph = manager.load_graph()

    if graph is None:
        console.print("[yellow]No semantic graph found.[/yellow]")
        console.print("Run [bold]voyager scan <project_path>[/bold] to build one.")
        return

    table = Table(title="Voyager Status")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    classes = [s for s in graph.symbols if s.type.value == "class"]
    fields = [s for s in graph.symbols if s.type.value == "field"]
    methods = [s for s in graph.symbols if s.type.value == "method"]

    table.add_row("Classes", str(len(classes)))
    table.add_row("Fields", str(len(fields)))
    table.add_row("Methods", str(len(methods)))
    table.add_row("References", str(len(graph.references)))

    console.print(table)


if __name__ == "__main__":
    # ── IDE / script mode: edit the values below and run directly ──────────
    from voyager_cmd.runner import VoyagerRunner

    PROJECT = r"examples\shop-dto"
    # OPERATION: "rename" | "add_field" | "remove_field"
    OPERATION = "rename"
    TARGET = "OrderDTO.userId"
    VALUE = "customerId"

    runner = VoyagerRunner(PROJECT)

    if OPERATION == "rename":
        runner.run_rename(TARGET, VALUE)
    elif OPERATION in ("add_field", "remove_field"):
        runner.scan()
        from core.operation.models import AddFieldOperation, RemoveFieldOperation

        if OPERATION == "add_field":
            op = AddFieldOperation(target=TARGET.split(".", 1)[0], field_name=VALUE)
        else:
            parts = TARGET.split(".", 1)
            op = RemoveFieldOperation(target=parts[0], field_name=parts[1])
        plan_result = runner.plan(op)
        if plan_result.is_valid:
            runner.apply(op)
    else:
        # Fallback: run the CLI
        cli()
