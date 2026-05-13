"""Shared CLI rendering for Voyager operation errors."""

from __future__ import annotations

from typing import Any

from rich.console import Console


def print_operation_errors(console: Console, title: str, errors: list[dict[str, Any]]) -> None:
    """
    Print plan/apply errors with special handling for structured diagnostics.
    """
    console.print(title)
    for error in errors:
        diagnostics = _diagnostics_from_error(error)
        if diagnostics:
            console.print("  [red][validation_failed][/red] LSP snapshot diagnostics failed")
            _print_diagnostics(console, diagnostics)
            continue

        action = error.get("action") or error.get("type") or "error"
        msg = error.get("message", str(error))
        style = "red" if action in {"error", "validation_failed"} else "yellow"
        console.print(f"  [{style}][{action}][/{style}] {msg}")


def _diagnostics_from_error(error: dict[str, Any]) -> list[dict[str, Any]]:
    details = error.get("details")
    if not isinstance(details, dict):
        return []
    diagnostics = details.get("diagnostics")
    if not isinstance(diagnostics, list):
        return []
    return [item for item in diagnostics if isinstance(item, dict)]


def _print_diagnostics(console: Console, diagnostics: list[dict[str, Any]]) -> None:
    by_file: dict[str, list[dict[str, Any]]] = {}
    for diagnostic in diagnostics:
        by_file.setdefault(str(diagnostic.get("file", "<unknown>")), []).append(diagnostic)

    for file_path in sorted(by_file):
        console.print(f"    [cyan]{file_path}[/cyan]")
        ordered = sorted(
            by_file[file_path],
            key=lambda item: (int(item.get("line", 0)), int(item.get("column", 0))),
        )
        for diagnostic in ordered:
            line = int(diagnostic.get("line", 0))
            column = int(diagnostic.get("column", 0))
            message = str(diagnostic.get("message", "Java diagnostic error")).strip()
            console.print(f"      [red]{line}:{column}[/red] {message}")
