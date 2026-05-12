"""Programmatic API for running Voyager patch operations without the CLI."""

import logging
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from core.engine.execution_engine import ExecutionEngine
from core.graph.semantic_graph import SymbolType
from core.operation.models import ApplyResult, PatchOperation, PlanResult
from storage.manager import StorageManager

console = Console()


def _setup_logging(verbose: bool = True) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
        force=True,
    )


class VoyagerRunner:
    """
    One-shot runner that chains scan, plan, and apply in a single process.
    """

    def __init__(self, project_path: str | Path, verbose: bool = True) -> None:
        self.project_path = Path(project_path).resolve()
        self.verbose = verbose
        _setup_logging(verbose)

        self.storage = StorageManager(self.project_path)
        self.engine = ExecutionEngine(self.project_path, self.storage)

    def scan(self) -> int:
        """
        Scan the project and build the semantic graph.
        """
        from core.graph.builder import GraphBuilder
        from core.parser.java_parser import parse_java_project

        console.print(f"[bold]Scanning[/bold] {self.project_path} ...")

        try:
            classes = parse_java_project(self.project_path)
        except Exception as e:
            console.print(f"[red]Failed to parse project: {e}[/red]")
            return 0

        if not classes:
            console.print("[yellow]No Java classes found.[/yellow]")
            return 0

        graph = GraphBuilder(self.project_path).build(classes)
        self.engine.graph = graph
        self.storage.save_graph(graph)

        classes_count = sum(1 for symbol in graph.symbols if symbol.type == SymbolType.CLASS)
        fields_count = sum(1 for symbol in graph.symbols if symbol.type == SymbolType.FIELD)
        console.print(
            f"[green]Scan complete:[/green] "
            f"{classes_count} classes, {fields_count} fields, {len(graph.references)} references"
        )
        return len(graph.symbols)

    def plan_patch(
        self,
        patch: str,
        patches: list[str] | None = None,
        description: str | None = None,
    ) -> PlanResult:
        """
        Plan a patch operation.
        """
        return self.plan(PatchOperation(patch=patch, patches=patches or [], description=description))

    def apply_patch(
        self,
        patch: str,
        patches: list[str] | None = None,
        description: str | None = None,
    ) -> ApplyResult:
        """
        Apply a patch operation.
        """
        return self.apply(PatchOperation(patch=patch, patches=patches or [], description=description))

    def plan(self, operation: PatchOperation) -> PlanResult:
        """
        Plan an operation against the current graph.
        """
        if self.engine.graph is None:
            self.scan()

        result = self.engine.plan(operation)
        if result.is_valid:
            console.print(f"[green]Plan valid.[/green] {len(result.affected_files)} file(s) affected:")
            for file_path in result.affected_files:
                console.print(f"  - {file_path}")
        else:
            console.print("[red]Plan rejected. Violations:[/red]")
            for violation in result.violations:
                console.print(f"  [red]Error:[/red] {violation}")
        return result

    def apply(self, operation: PatchOperation) -> ApplyResult:
        """
        Apply an operation through the full pipeline.
        """
        if self.engine.graph is None:
            self.scan()

        result = self.engine.apply(operation)
        if result.success:
            console.print("[green]Operation applied successfully.[/green]")
            for file_path in result.modified_files:
                console.print(f"  Modified: {file_path}")
        else:
            console.print("[red]Operation failed.[/red]")
            for error in result.errors:
                console.print(f"  [red]Error:[/red] {error}")
        return result
