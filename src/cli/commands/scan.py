"""Scan command: parse Java project and build semantic graph."""

from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from core.server.client import VoyagerServerClient

console = Console()


@dataclass
class ScanResult:
    """
    Result of a scan operation.

    Attributes:
        symbols_count: Total number of symbols (classes + fields + methods).
        references_count: Total number of typed references between symbols.
    """

    symbols_count: int
    references_count: int


def scan_project(project_path: Path) -> ScanResult | None:
    """
    Scan a Java project directory and build the semantic graph.

    Args:
        project_path: Root path of the Java project.

    Returns:
        ScanResult with counts, or None on failure.
    """
    console.print(f"[bold]Scanning[/bold] {project_path} ...")

    try:
        result = VoyagerServerClient(project_path).scan()
    except Exception as e:
        console.print(f"[red]Failed to parse project: {e}[/red]")
        return None

    if not result:
        console.print("[yellow]No Java classes found in the project.[/yellow]")
        return None

    console.print(
        f"Found [green]{len(result.get('classes', []))}[/green] Java classes. Building semantic graph..."
    )

    # Print summary
    from rich.table import Table

    table = Table(title="Detected Symbols")
    table.add_column("Class", style="cyan")
    table.add_column("Fields", justify="right")
    table.add_column("Methods", justify="right")
    table.add_column("References", justify="right")

    for item in result.get("classes", []):
        table.add_row(
            item.get("name", ""),
            str(item.get("fields", 0)),
            str(item.get("methods", 0)),
            str(item.get("references", 0)),
        )

    console.print(table)

    return ScanResult(
        symbols_count=result.get("symbols_count", 0),
        references_count=result.get("references_count", 0),
    )
