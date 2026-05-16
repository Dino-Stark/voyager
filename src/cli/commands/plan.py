"""Plan command: plan an operation and compute affected files."""

import json
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from cli.commands.errors import print_operation_errors
from core.operation.models import (
    Operation,
    PatchOperation,
    PlanResult,
)
from core.server.client import VoyagerServerClient
from storage.manager import StorageManager

console = Console()


def plan_operation(
    op_type: str,
    target: str,
    value: str | None,
    extra: list[str] | None = None,
    json_output: bool = False,
) -> object | None:
    """
    Plan an operation and display affected files.

    Args:
        op_type: Type of operation. V1 external editing is patch-only.
        target: Target identifier (e.g. 'com.shop.UserDTO.userName').
        value: New value (depends on op_type).
        extra: Additional CLI arguments.
        json_output: If True, print a machine-readable JSON result.

    Returns:
        PlanResult dict or None on failure.
    """
    project_path = _find_project_root()
    storage = StorageManager(project_path)

    # Build operation
    try:
        operation = _build_operation(op_type, target, value, extra or [])
    except Exception as e:
        if json_output:
            _print_json(_invalid_plan_payload("invalid_operation", str(e)))
        else:
            console.print(f"[red]Invalid operation: {e}[/red]")
        return None

    if not json_output:
        console.print(
            f"[bold]Planning:[/bold] {operation.op.value} {target}"
            + (f" -> {value}" if value else "")
        )

    # Execute plan through the persistent project server.
    try:
        result = PlanResult.model_validate(VoyagerServerClient(project_path).plan(operation))
    except Exception as e:
        if json_output:
            _print_json(_invalid_plan_payload("plan_failed", str(e)))
        else:
            console.print(f"[red]Plan failed: {e}[/red]")
        return None

    # Display result
    if result.is_valid:
        if json_output:
            _print_json(result.model_dump(mode="json"))
        else:
            console.print(
                f"[green]Plan valid.[/green] {len(result.affected_files)} file(s) affected:"
            )
            for fp in result.affected_files:
                console.print(f"  - {fp}")

        storage.save_pending_plan(operation)

        return result
    else:
        if json_output:
            _print_json(result.model_dump(mode="json"))
        else:
            print_operation_errors(console, "[red]Plan rejected. Violations:[/red]", result.violations)
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

    if op_type == "patch":
        if value:
            sources = [target, value, *extra]
        else:
            sources = [target, *extra]
        if sources.count("-") > 1:
            raise ValueError("'patch' accepts stdin '-' at most once")
        patch_texts = [
            sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
            for source in sources
        ]
        return PatchOperation(
            patch=patch_texts[0],
            patches=patch_texts[1:],
            description=", ".join(sources),
        )
    raise ValueError(f"Unknown operation type: {op_type}. Voyager editing is patch-only.")


def _invalid_plan_payload(error_type: str, message: str) -> dict[str, Any]:
    return {
        "operation": None,
        "affected_files": [],
        "violations": [{"type": error_type, "message": message, "action": "error"}],
        "is_valid": False,
    }


def _print_json(data: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")


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

