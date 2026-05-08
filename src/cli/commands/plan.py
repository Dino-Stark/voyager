"""Plan command: plan an operation and compute affected files."""

from pathlib import Path

from rich.console import Console
from core.operation.models import AddFieldOperation, Operation, PlanResult, RemoveFieldOperation, RenameFieldOperation
from core.server.client import VoyagerServerClient
from storage.manager import StorageManager

console = Console()


def plan_operation(op_type: str, target: str, value: str | None) -> object | None:
    """
    Plan an operation and display affected files.

    Args:
        op_type: Type of operation (rename, add_field, remove_field).
        target: Target identifier (e.g. 'OrderDTO.userId').
        value: New value (depends on op_type).

    Returns:
        PlanResult dict or None on failure.
    """
    project_path = _find_project_root()
    storage = StorageManager(project_path)

    # Build operation
    try:
        operation = _build_operation(op_type, target, value)
    except ValueError as e:
        console.print(f"[red]Invalid operation: {e}[/red]")
        return None

    console.print(f"[bold]Planning:[/bold] {operation.op.value} {target}" + (f" -> {value}" if value else ""))

    # Execute plan through the persistent project server.
    try:
        result = PlanResult.model_validate(VoyagerServerClient(project_path).plan(operation))
    except Exception as e:
        console.print(f"[red]Plan failed: {e}[/red]")
        return None

    # Display result
    if result.is_valid:
        console.print(f"[green]Plan valid.[/green] {len(result.affected_files)} file(s) affected:")
        for fp in result.affected_files:
            console.print(f"  - {fp}")

        storage.save_pending_plan(operation)

        return result
    else:
        console.print("[red]Plan rejected. Violations:[/red]")
        for v in result.violations:
            action = v.get("action", "error")
            msg = v.get("message", str(v))
            style = "red" if action == "error" else "yellow"
            console.print(f"  [{style}][{action}][/{style}] {msg}")
        return result


def _build_operation(op_type: str, target: str, value: str | None) -> Operation:
    """
    Build an operation object from CLI arguments.
    """
    if op_type == "rename":
        if not value:
            raise ValueError("'rename' requires a new name as the third argument")
        return RenameFieldOperation(target=target, to=value)
    elif op_type == "add_field":
        parts = target.split(".", 1)
        class_name = parts[0]
        field_name = value or (parts[1] if len(parts) > 1 else "")
        if not field_name:
            raise ValueError("'add_field' requires <class> <field_name> [type]")
        return AddFieldOperation(target=class_name, field_name=field_name)
    elif op_type == "remove_field":
        parts = target.split(".", 1)
        if len(parts) != 2:
            raise ValueError("'remove_field' requires target in format ClassName.fieldName")
        return RemoveFieldOperation(target=parts[0], field_name=parts[1])
    else:
        raise ValueError(f"Unknown operation type: {op_type}")


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

