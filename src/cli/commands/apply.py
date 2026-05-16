"""Apply command: execute the pending planned operation."""

import json
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.prompt import Confirm

from cli.commands.errors import print_operation_errors
from core.operation.models import (
    ApplyResult,
    Operation,
    PatchOperation,
)
from core.server.client import VoyagerServerClient
from storage.manager import StorageManager

console = Console()


def apply_plan(skip_confirm: bool = False, json_output: bool = False) -> dict | None:
    """
    Apply the pending planned operation.

    Args:
        skip_confirm: If True, skip the confirmation prompt.
        json_output: If True, print a machine-readable JSON result.

    Returns:
        ApplyResult dict or None on failure.
    """
    project_path = _find_project_root()
    storage = StorageManager(project_path)
    data = storage.load_pending_plan()

    if data is None:
        payload = _failed_apply_payload("no_pending_plan", "No pending plan found.")
        if json_output:
            _print_json(payload)
        else:
            console.print("[red]No pending plan found.[/red]")
            console.print("Run [bold]voyager plan[/bold] first.")
        return None

    operation = _deserialize_operation(data)
    if not json_output:
        console.print(f"[bold]Applying:[/bold] {operation.op.value}")

    # Confirm
    if not skip_confirm:
        if not Confirm.ask("Apply this operation?"):
            payload = _failed_apply_payload("cancelled", "Apply cancelled by user.", operation)
            if json_output:
                _print_json(payload)
            else:
                console.print("[yellow]Cancelled.[/yellow]")
            return None

    # Execute through the persistent project server.
    try:
        result = ApplyResult.model_validate(VoyagerServerClient(project_path).apply(operation))
    except Exception as e:
        payload = _failed_apply_payload("apply_failed", str(e), operation)
        if json_output:
            _print_json(payload)
        else:
            console.print(f"[red]Apply failed: {e}[/red]")
        return None

    # Display result
    if result.success:
        if json_output:
            _print_json(result.model_dump(mode="json"))
        else:
            console.print("[green]Operation applied successfully.[/green]")
            for fp in result.modified_files:
                console.print(f"  Modified: {fp}")

        storage.clear_pending_plan()
    else:
        if json_output:
            _print_json(result.model_dump(mode="json"))
        else:
            print_operation_errors(console, "[red]Operation failed.[/red]", result.errors)

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
    if op_type == "patch":
        return PatchOperation(**data)
    raise ValueError(f"Unknown operation type: {op_type}. Voyager editing is patch-only.")


def _failed_apply_payload(
    error_type: str,
    message: str,
    operation: Operation | None = None,
) -> dict[str, Any]:
    return {
        "success": False,
        "operation": operation.model_dump(mode="json") if operation is not None else None,
        "modified_files": [],
        "errors": [{"type": error_type, "message": message, "action": "error"}],
    }


def _print_json(data: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
