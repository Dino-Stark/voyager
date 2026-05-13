"""Voyager CLI entry point.

Usage:
    voyager start [<project_path>]
    voyager serve [<project_path>]
    voyager scan <project_path>
    voyager plan patch <patch_file> [<patch_file>...]
    voyager apply
    voyager status
    voyager progress
    voyager cancel
    voyager stop
"""

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
from core.engine.execution_engine import validation_capability
from core.server.client import VoyagerServerClient
from core.server.server import run_server

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
@click.argument("project_path", type=click.Path(exists=True, file_okay=False), required=False)
@click.pass_context
def serve(ctx: click.Context, project_path: str | None) -> None:
    """
    Run the Voyager server for a Java project.
    """
    from pathlib import Path

    root: Path = Path(project_path or ".").resolve()
    client: VoyagerServerClient = VoyagerServerClient(root, auto_start=False)
    try:
        status_result = client.status()
    except Exception:
        status_result = None

    if status_result and status_result.get("running"):
        console.print(f"[green]Voyager server already running for[/green] {root}")
        return

    console.print(f"[bold]Starting Voyager server[/bold] for {root}")
    console.print("[dim]Press Ctrl+C to stop the server.[/dim]")
    try:
        run_server(root)
    except KeyboardInterrupt:
        from storage.manager import StorageManager

        StorageManager(root).clear_server_info()
        console.print("\n[yellow]Voyager server stopped.[/yellow]")


@cli.command()
@click.argument("project_path", type=click.Path(exists=True, file_okay=False), required=False)
@click.pass_context
def start(ctx: click.Context, project_path: str | None) -> None:
    """
    Start the Voyager server in the background.
    """
    from pathlib import Path

    root = Path(project_path or ".").resolve()
    try:
        result = VoyagerServerClient(root).start()
    except Exception as exc:
        console.print(f"[red]Failed to start Voyager server:[/red] {exc}")
        sys.exit(1)

    pid = result.get("pid", "unknown")
    console.print(f"[green]Voyager server running[/green] for {root} [dim](pid {pid})[/dim]")


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
@click.argument(
    "op_type",
    type=click.Choice(
        [
            "patch",
        ]
    ),
)
@click.argument("target")
@click.argument("value", required=False)
@click.argument("extra", nargs=-1)
@click.pass_context
def plan(ctx: click.Context, op_type: str, target: str, value: str | None, extra: tuple[str, ...]) -> None:
    """
    Plan an operation and show affected files.
    """
    result = plan_operation(op_type, target, value, list(extra))
    if result is None or not result.is_valid:
        sys.exit(1)


@cli.command()
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
@click.pass_context
def apply(ctx: click.Context, yes: bool) -> None:
    """
    Apply the last planned operation.
    """
    result = apply_plan(skip_confirm=yes)
    if result is None or not result.get("success", False):
        sys.exit(1)


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """
    Show current project status and graph info.
    """
    from storage.manager import StorageManager

    project_path = _find_project_root_for_status()
    manager = StorageManager(project_path)
    graph = manager.load_graph()

    try:
        server_status = VoyagerServerClient(project_path, auto_start=False).status()
        server_value = f"running (pid {server_status.get('pid')})"
    except Exception:
        server_value = "stopped"
        server_status = {}

    capabilities = server_status.get("capabilities") or validation_capability(project_path).to_dict()
    progress = server_status.get("progress") or {}

    if graph is None:
        console.print("[yellow]No semantic graph found.[/yellow]")
        console.print("Run [bold]voyager scan <project_path>[/bold] to build one.")
        console.print(f"Voyager server: [cyan]{server_value}[/cyan]")
        console.print(f"JDT LS: [cyan]{_yes_no(capabilities.get('jdtls_available'))}[/cyan]")
        console.print(f"Java build metadata: [cyan]{_yes_no(capabilities.get('java_build_metadata'))}[/cyan]")
        console.print(f"Snapshot diagnostics: [cyan]{_yes_no(capabilities.get('snapshot_diagnostics'))}[/cyan]")
        return

    table = Table(title="Voyager Status")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Server", server_value)
    table.add_row("JDT LS", _yes_no(capabilities.get("jdtls_available")))
    table.add_row("Java build metadata", _yes_no(capabilities.get("java_build_metadata")))
    table.add_row("Snapshot diagnostics", _yes_no(capabilities.get("snapshot_diagnostics")))
    if progress:
        table.add_row("Last operation", _format_progress(progress))
    classes = [s for s in graph.symbols if s.type.value == "class"]
    fields = [s for s in graph.symbols if s.type.value == "field"]
    methods = [s for s in graph.symbols if s.type.value == "method"]

    table.add_row("Classes", str(len(classes)))
    table.add_row("Fields", str(len(fields)))
    table.add_row("Methods", str(len(methods)))
    table.add_row("References", str(len(graph.references)))

    console.print(table)


@cli.command()
@click.pass_context
def stop(ctx: click.Context) -> None:
    """
    Stop the Voyager server for the current project.
    """
    project_path = _find_project_root_for_status()
    try:
        VoyagerServerClient(project_path, auto_start=False).shutdown()
    except Exception as exc:
        console.print(f"[yellow]No running Voyager server found:[/yellow] {exc}")
        return
    console.print("[green]Voyager server stopped.[/green]")


@cli.command()
@click.pass_context
def progress(ctx: click.Context) -> None:
    """
    Show the last known Server operation progress.
    """
    project_path = _find_project_root_for_status()
    try:
        result = VoyagerServerClient(project_path, auto_start=False).progress()
    except Exception as exc:
        console.print(f"[yellow]No running Voyager server found:[/yellow] {exc}")
        return
    console.print(f"[cyan]{_format_progress(result)}[/cyan]")
    message = result.get("message")
    if message:
        console.print(str(message))


@cli.command()
@click.pass_context
def cancel(ctx: click.Context) -> None:
    """
    Request cancellation of the current Server operation.
    """
    project_path = _find_project_root_for_status()
    try:
        result = VoyagerServerClient(project_path, auto_start=False).cancel()
    except Exception as exc:
        console.print(f"[yellow]No running Voyager server found:[/yellow] {exc}")
        return
    style = "green" if result.get("accepted") else "yellow"
    console.print(f"[{style}]{result.get('message', 'Cancel request completed.')}[/{style}]")


def _yes_no(value: object) -> str:
    return "yes" if value is True else "no"


def _format_progress(progress: dict) -> str:
    status = progress.get("status") or "idle"
    stage = progress.get("stage") or "idle"
    cancel = " cancel requested" if progress.get("cancel_requested") else ""
    return f"{stage} / {status}{cancel}"


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cli()
        sys.exit(0)

    # ── IDE / script mode: edit the values below and run directly ──────────
    from voyager_cmd.runner import VoyagerRunner

    cli()
