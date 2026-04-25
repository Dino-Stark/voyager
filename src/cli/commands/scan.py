"""Scan command: parse Java project and build semantic graph."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from core.graph.builder import GraphBuilder
from core.parser.java_parser import parse_java_project
from storage.manager import StorageManager

console = Console()


@dataclass
class ScanResult:
    symbols_count: int
    references_count: int


def scan_project(project_path: Path) -> ScanResult | None:
    """Scan a Java project directory and build the semantic graph.

    Args:
        project_path: Root path of the Java project.

    Returns:
        ScanResult with counts, or None on failure.
    """
    console.print(f"[bold]Scanning[/bold] {project_path} ...")

    try:
        classes = parse_java_project(project_path)
    except Exception as e:
        console.print(f"[red]Failed to parse project: {e}[/red]")
        return None

    if not classes:
        console.print("[yellow]No Java classes found in the project.[/yellow]")
        return None

    console.print(f"Found [green]{len(classes)}[/green] Java classes. Building semantic graph...")

    builder = GraphBuilder()
    graph = builder.build(classes)

    # Persist the graph
    storage = StorageManager(project_path)
    storage.save_graph(graph)

    # Print summary
    from rich.table import Table
    from core.graph.semantic_graph import SymbolType

    table = Table(title="Detected Symbols")
    table.add_column("Class", style="cyan")
    table.add_column("Fields", justify="right")
    table.add_column("Methods", justify="right")
    table.add_column("References", justify="right")

    classes_sorted = sorted(
        [s for s in graph.symbols if s.type == SymbolType.CLASS],
        key=lambda s: s.name,
    )
    for cls_sym in classes_sorted:
        fields = [s for s in graph.symbols if s.parent_id == cls_sym.id and s.type == SymbolType.FIELD]
        methods = [s for s in graph.symbols if s.parent_id == cls_sym.id and s.type == SymbolType.METHOD]
        refs_to = [r for r in graph.references if r.to_symbol == cls_sym.id]
        table.add_row(cls_sym.name, str(len(fields)), str(len(methods)), str(len(refs_to)))

    console.print(table)

    return ScanResult(
        symbols_count=len(graph.symbols),
        references_count=len(graph.references),
    )
