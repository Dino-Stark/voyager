"""Programmatic API for running Voyager operations without the CLI.

Provides a single-call interface that chains scan → plan → apply, suitable for
running from an IDE or a script's ``if __name__ == '__main__'`` block.

Usage::

    from voyager_cmd.runner import VoyagerRunner

    runner = VoyagerRunner(project_path="examples/shop-dto")
    runner.run_rename("com.shop.OrderDTO.userId", "customerId")
"""

import logging
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from core.engine.execution_engine import ExecutionEngine
from core.graph.semantic_graph import SymbolType
from core.operation.models import (
    AddFieldOperation,
    ApplyResult,
    Operation,
    PlanResult,
    RemoveFieldOperation,
    RenameClassOperation,
    RenameFieldOperation,
    RenameMethodOperation,
)
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
    One-shot runner that chains scan → plan → apply in a single process.

    Unlike the CLI (which splits scan/plan/apply into separate invocations),
    the runner holds engine state in memory so none of the intermediate
    serialization to ``pending_plan.json`` is needed.
    """

    def __init__(self, project_path: str | Path, verbose: bool = True) -> None:
        self.project_path = Path(project_path).resolve()
        self.verbose = verbose
        _setup_logging(verbose)

        self.storage = StorageManager(self.project_path)
        self.engine = ExecutionEngine(self.project_path, self.storage)

    # -- scan ------------------------------------------------------------------

    def scan(self) -> int:
        """
        Scan the project and build the semantic graph.

        Returns the number of symbols discovered, or 0 on failure.
        """
        from core.parser.java_parser import parse_java_project
        from core.graph.builder import GraphBuilder

        console.print(f"[bold]Scanning[/bold] {self.project_path} ...")

        try:
            classes = parse_java_project(self.project_path)
        except Exception as e:
            console.print(f"[red]Failed to parse project: {e}[/red]")
            return 0

        if not classes:
            console.print("[yellow]No Java classes found.[/yellow]")
            return 0

        console.print(f"Found [green]{len(classes)}[/green] Java classes. Building semantic graph...")

        graph = GraphBuilder(self.project_path).build(classes)
        self.engine.graph = graph
        self.storage.save_graph(graph)

        classes_count = sum(1 for s in graph.symbols if s.type == SymbolType.CLASS)
        fields_count = sum(1 for s in graph.symbols if s.type == SymbolType.FIELD)
        refs_count = len(graph.references)
        console.print(
            f"[green]Scan complete:[/green] "
            f"{classes_count} classes, {fields_count} fields, {refs_count} references"
        )
        return len(graph.symbols)

    # -- plan ------------------------------------------------------------------

    def plan(self, operation: Operation) -> PlanResult:
        """
        Plan an operation against the current graph.

        Calls :meth:`scan` first if no graph is loaded yet.
        """
        if self.engine.graph is None:
            self.scan()

        graph = self.engine.ensure_graph()
        result = self.engine.plan(operation)

        if result.is_valid:
            console.print(f"[green]Plan valid.[/green] {len(result.affected_files)} file(s) affected:")
            for fp in result.affected_files:
                console.print(f"  - {fp}")
        else:
            console.print("[red]Plan rejected. Violations:[/red]")
            for v in result.violations:
                action = v.get("action", "error")
                msg = v.get("message", str(v))
                style = "red" if action == "error" else "yellow"
                console.print(f"  [{style}][{action}][/{style}] {msg}")

        return result

    # -- apply -----------------------------------------------------------------

    def apply(self, operation: Operation, skip_confirm: bool = True) -> ApplyResult:
        """
        Apply an operation through the full pipeline.

        Calls :meth:`scan` first if no graph is loaded yet.
        """
        if self.engine.graph is None:
            self.scan()

        result = self.engine.apply(operation)

        if result.success:
            console.print("[green]Operation applied successfully.[/green]")
            for fp in result.modified_files:
                console.print(f"  Modified: {fp}")
        else:
            console.print("[red]Operation failed.[/red]")
            for err in result.errors:
                console.print(f"  [red]Error:[/red] {err}")

        return result

    # -- convenience -----------------------------------------------------------

    def run_rename(
        self,
        target: str,
        new_name: str,
        *,
        scan: bool = True,
        apply: bool = True,
    ) -> ApplyResult | PlanResult:
        """
        End-to-end rename_field: scan (optional) → plan → apply (optional).

        Args:
            target: Field spec in ``package.ClassName.fieldName`` format.
            new_name: New field name.
            scan: Whether to scan first (default True).
            apply: Whether to apply after planning (default True).

        Returns:
            The PlanResult (if apply=False) or ApplyResult (if apply=True).
        """
        operation = RenameFieldOperation(target=target, to=new_name)

        if scan:
            self.scan()

        plan_result = self.plan(operation)
        if not plan_result.is_valid:
            return plan_result

        if apply:
            return self.apply(operation)

        return plan_result

    def run_rename_method(
        self,
        target: str,
        new_name: str,
        *,
        scan: bool = True,
        apply: bool = True,
    ) -> ApplyResult | PlanResult:
        """
        End-to-end rename_method: scan (optional) -> plan -> apply (optional).
        """
        operation = RenameMethodOperation(target=target, to=new_name)

        if scan:
            self.scan()

        plan_result = self.plan(operation)
        if not plan_result.is_valid:
            return plan_result

        if apply:
            return self.apply(operation)

        return plan_result

    def run_rename_class(
        self,
        target: str,
        new_name: str,
        *,
        scan: bool = True,
        apply: bool = True,
    ) -> ApplyResult | PlanResult:
        """
        End-to-end rename_class: scan (optional) -> plan -> apply (optional).
        """
        operation = RenameClassOperation(target=target, to=new_name)

        if scan:
            self.scan()

        plan_result = self.plan(operation)
        if not plan_result.is_valid:
            return plan_result

        if apply:
            return self.apply(operation)

        return plan_result

    def run_add_field(
        self,
        class_name: str,
        field_name: str,
        field_type: str = "String",
        default_value: str | None = None,
        *,
        scan: bool = True,
        apply: bool = True,
    ) -> ApplyResult | PlanResult:
        """
        End-to-end add_field: scan (optional) -> plan -> apply (optional).
        """
        operation = AddFieldOperation(
            target=class_name,
            field_name=field_name,
            field_type=field_type,
            default_value=default_value,
        )

        if scan:
            self.scan()

        plan_result = self.plan(operation)
        if not plan_result.is_valid:
            return plan_result

        if apply:
            return self.apply(operation)

        return plan_result

    def run_remove_field(
        self,
        class_name: str,
        field_name: str,
        *,
        scan: bool = True,
        apply: bool = True,
    ) -> ApplyResult | PlanResult:
        """
        End-to-end remove_field: scan (optional) -> plan -> apply (optional).
        """
        operation = RemoveFieldOperation(target=class_name, field_name=field_name)

        if scan:
            self.scan()

        plan_result = self.plan(operation)
        if not plan_result.is_valid:
            return plan_result

        if apply:
            return self.apply(operation)

        return plan_result
