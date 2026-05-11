"""Plan command: plan an operation and compute affected files."""

import sys
from pathlib import Path

from rich.console import Console
from core.operation.models import (
    AddFieldOperation,
    Operation,
    PatchOperation,
    PlanResult,
    RemoveFieldOperation,
    RenameClassOperation,
    RenameFieldOperation,
    RenameMethodOperation,
)
from core.server.client import VoyagerServerClient
from storage.manager import StorageManager

console = Console()


def plan_operation(
    op_type: str,
    target: str,
    value: str | None,
    extra: list[str] | None = None,
) -> object | None:
    """
    Plan an operation and display affected files.

    Args:
        op_type: Type of operation (rename, rename_field, rename_method, rename_class, add_field, remove_field, patch).
        target: Target identifier (e.g. 'com.shop.UserDTO.userName').
        value: New value (depends on op_type).
        extra: Additional CLI arguments for operations such as add_field.

    Returns:
        PlanResult dict or None on failure.
    """
    project_path = _find_project_root()
    storage = StorageManager(project_path)

    # Build operation
    try:
        operation = _build_operation(op_type, target, value, extra or [])
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


def _build_operation(
    op_type: str,
    target: str,
    value: str | None,
    extra: list[str] | None = None,
) -> Operation:
    """
    Build an operation object from CLI arguments.
    """
    extra = extra or []

    if op_type == "rename":
        if not value:
            raise ValueError("'rename' requires a new name as the third argument")
        if extra:
            raise ValueError("'rename' does not accept extra arguments")
        if target.startswith("field:"):
            return RenameFieldOperation(target=target.removeprefix("field:"), to=value)
        if target.startswith("method:"):
            return RenameMethodOperation(target=target.removeprefix("method:"), to=value)
        if target.startswith("class:"):
            return RenameClassOperation(target=target.removeprefix("class:"), to=value)
        raise ValueError(
            "'rename' requires a target prefix: field:<FQN.field>, method:<FQN.method>, or class:<FQN>"
        )
    elif op_type == "rename_field":
        if not value:
            raise ValueError("'rename_field' requires a new name as the third argument")
        if extra:
            raise ValueError("'rename_field' does not accept extra arguments")
        return RenameFieldOperation(target=target, to=value)
    elif op_type == "rename_method":
        if not value:
            raise ValueError("'rename_method' requires a new name as the third argument")
        if extra:
            raise ValueError("'rename_method' does not accept extra arguments")
        return RenameMethodOperation(target=target, to=value)
    elif op_type == "rename_class":
        if not value:
            raise ValueError("'rename_class' requires a new name as the third argument")
        if extra:
            raise ValueError("'rename_class' does not accept extra arguments")
        return RenameClassOperation(target=target, to=value)
    elif op_type == "add_field":
        field_name = value
        if not field_name:
            raise ValueError(
                "'add_field' requires <fully-qualified-class> <field_name> [type] [default_value]"
            )
        if len(extra) > 2:
            raise ValueError("'add_field' accepts at most [type] [default_value]")
        field_type = extra[0] if extra else "String"
        default_value = extra[1] if len(extra) == 2 else None
        return AddFieldOperation(
            target=target,
            field_name=field_name,
            field_type=field_type,
            default_value=default_value,
        )
    elif op_type == "remove_field":
        if extra:
            raise ValueError("'remove_field' does not accept extra arguments")
        if value:
            return RemoveFieldOperation(target=target, field_name=value)
        parts = target.rsplit(".", 1)
        if len(parts) != 2:
            raise ValueError(
                "'remove_field' requires <fully-qualified-class> <field_name> or <fully-qualified-class.field>"
            )
        return RemoveFieldOperation(target=parts[0], field_name=parts[1])
    elif op_type == "patch":
        if value:
            raise ValueError("'patch' does not accept a third argument")
        if len(extra) > 1:
            raise ValueError("'patch' requires exactly one patch file path or '-'")
        patch_source = target
        patch_text = sys.stdin.read() if patch_source == "-" else Path(patch_source).read_text(encoding="utf-8")
        return PatchOperation(patch=patch_text, description=patch_source)
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

