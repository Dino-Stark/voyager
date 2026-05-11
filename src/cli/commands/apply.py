"""Apply command: execute the pending planned operation."""

from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm

from core.operation.models import (
    AddFieldOperation,
    ApplyResult,
    Operation,
    PatchOperation,
    RemoveFieldOperation,
    RenameClassOperation,
    RenameFieldOperation,
    RenameMethodOperation,
)
from core.server.client import VoyagerServerClient
from storage.manager import StorageManager

console = Console()


def apply_plan(skip_confirm: bool = False) -> dict | None:
    """
    Apply the pending planned operation.

    Args:
        skip_confirm: If True, skip the confirmation prompt.

    Returns:
        ApplyResult dict or None on failure.
    """
    project_path = _find_project_root()
    storage = StorageManager(project_path)
    data = storage.load_pending_plan()

    if data is None:
        console.print("[red]No pending plan found.[/red]")
        console.print("Run [bold]voyager plan[/bold] first.")
        return None

    operation = _deserialize_operation(data)
    console.print(f"[bold]Applying:[/bold] {operation.op.value}")

    # Confirm
    if not skip_confirm:
        if not Confirm.ask("Apply this operation?"):
            console.print("[yellow]Cancelled.[/yellow]")
            return None

    # Execute through the persistent project server.
    try:
        result = ApplyResult.model_validate(VoyagerServerClient(project_path).apply(operation))
    except Exception as e:
        console.print(f"[red]Apply failed: {e}[/red]")
        return None

    # Display result
    if result.success:
        console.print("[green]Operation applied successfully.[/green]")
        for fp in result.modified_files:
            console.print(f"  Modified: {fp}")

        storage.clear_pending_plan()
    else:
        console.print("[red]Operation failed.[/red]")
        for err in result.errors:
            console.print(f"  [red]Error:[/red] {err}")

    return result.model_dump(mode="json") if hasattr(result, 'model_dump') else None


def _find_project_root() -> Path:
    """
    Find the project root by looking for .voyager directory.
    """
    current = Path.cwd()
    while current != current.parent:
        if (current / ".voyager").exists():
            return current
        current = current.parent
    return Path.cwd()


def _deserialize_operation(data: dict) -> Operation:
    """
    Deserialize an operation from a dict.
    """
    op_type = data.get("op", "")
    if op_type == "rename_field":
        return RenameFieldOperation(**data)
    elif op_type == "rename_method":
        return RenameMethodOperation(**data)
    elif op_type == "rename_class":
        return RenameClassOperation(**data)
    elif op_type == "add_field":
        return AddFieldOperation(**data)
    elif op_type == "remove_field":
        return RemoveFieldOperation(**data)
    elif op_type == "patch":
        return PatchOperation(**data)
    else:
        raise ValueError(f"Unknown operation type: {op_type}")
