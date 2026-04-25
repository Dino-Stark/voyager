"""Apply command: execute the pending planned operation."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm

from core.engine.execution_engine import ExecutionEngine
from core.operation.models import (
    AddFieldOp,
    Operation,
    RemoveFieldOp,
    RenameFieldOp,
)
from storage.manager import StorageManager

console = Console()


def apply_plan(skip_confirm: bool = False) -> dict | None:
    """Apply the pending planned operation.

    Args:
        skip_confirm: If True, skip the confirmation prompt.

    Returns:
        ApplyResult dict or None on failure.
    """
    project_path = _find_project_root()
    plan_file = project_path / ".voyager" / "pending_plan.json"

    if not plan_file.exists():
        console.print("[red]No pending plan found.[/red]")
        console.print("Run [bold]voyager plan[/bold] first.")
        return None

    # Load pending plan
    try:
        data = json.loads(plan_file.read_text(encoding="utf-8"))
    except Exception as e:
        console.print(f"[red]Failed to load plan: {e}[/red]")
        return None

    operation = _deserialize_operation(data)
    console.print(f"[bold]Applying:[/bold] {operation.op.value}")

    # Confirm
    if not skip_confirm:
        if not Confirm.ask("Apply this operation?"):
            console.print("[yellow]Cancelled.[/yellow]")
            return None

    # Execute
    storage = StorageManager(project_path)
    engine = ExecutionEngine(project_path, storage)
    result = engine.apply(operation)

    # Display result
    if result.success:
        console.print("[green]Operation applied successfully.[/green]")
        for fp in result.modified_files:
            console.print(f"  Modified: {fp}")

        # Clean up pending plan
        plan_file.unlink(missing_ok=True)
    else:
        console.print("[red]Operation failed.[/red]")
        for err in result.errors:
            console.print(f"  [red]Error:[/red] {err}")

    return result.model_dump(mode="json") if hasattr(result, 'model_dump') else None


def _find_project_root() -> Path:
    """Find the project root by looking for .voyager directory."""
    current = Path.cwd()
    while current != current.parent:
        if (current / ".voyager").exists():
            return current
        current = current.parent
    return Path.cwd()


def _deserialize_operation(data: dict) -> Operation:
    """Deserialize an operation from a dict."""
    op_type = data.get("op", "")
    if op_type == "rename_field":
        return RenameFieldOp(**data)
    elif op_type == "add_field":
        return AddFieldOp(**data)
    elif op_type == "remove_field":
        return RemoveFieldOp(**data)
    else:
        raise ValueError(f"Unknown operation type: {op_type}")
