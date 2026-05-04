"""Rule validation for semantic operations."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

from core.graph.semantic_graph import RefType, SemanticGraph, SymbolType
from core.operation.models import AddFieldOp, Operation, RemoveFieldOp, RenameFieldOp

logger = logging.getLogger(__name__)


class RuleAction(str, Enum):
    """
    Severity level for a rule violation.
    """
    WARN = "warn"
    ERROR = "error"


@dataclass(frozen=True)
class RuleDefinition:
    """
    A rule loaded from ``rules.yaml``.

    Attributes:
        id: Unique rule identifier used in violation reports.
        type: Rule type, e.g. ``"symbol_uniqueness"``.
        target: Optional scope filter, e.g. ``"DTO"``.
        action: ``"error"`` (blocks execution) or ``"warn"`` (logs only).
    """
    id: str
    type: str
    target: str | None = None
    action: RuleAction = RuleAction.ERROR


class RuleValidator:
    """
    Validate operations and graph-level invariants.

    Runs pre- and post-condition checks before and after every apply.  Rules are
    loaded from ``.voyager/rules.yaml``; built-in checks include symbol existence,
    name conflicts, and DTO uniqueness.
    """

    id: str
    type: str
    target: str | None = None
    action: RuleAction = RuleAction.ERROR

    def __init__(self, rules_path: Path | None = None) -> None:
        self.rules: list[RuleDefinition] = []
        if rules_path and rules_path.exists():
            self._load_rules(rules_path)

    def _load_rules(self, rules_path: Path) -> None:
        try:
            data = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
            for raw in data.get("rules", []):
                self.rules.append(
                    RuleDefinition(
                        id=raw["id"],
                        type=raw["type"],
                        target=raw.get("target"),
                        action=RuleAction(raw.get("action", "error")),
                    )
                )
        except Exception as exc:
            logger.warning("Failed to load rules from %s: %s", rules_path, exc)

    def validate_pre(self, graph: SemanticGraph, operation: Operation) -> list[dict]:
        violations: list[dict] = []

        if isinstance(operation, RenameFieldOp):
            field = graph.resolve_field(operation.class_name, operation.field_name)
            if field is None:
                violations.append(
                    _violation(
                        "symbol_not_found",
                        f"Field '{operation.target}' was not found",
                        operation.target,
                    )
                )
            elif graph.resolve_field(operation.class_name, operation.to) is not None:
                violations.append(
                    _violation(
                        "symbol_already_exists",
                        f"Field '{operation.class_name}.{operation.to}' already exists",
                        f"{operation.class_name}.{operation.to}",
                    )
                )

        elif isinstance(operation, AddFieldOp):
            if graph.resolve_class(operation.class_name) is None:
                violations.append(
                    _violation(
                        "symbol_not_found",
                        f"Class '{operation.class_name}' was not found",
                        operation.class_name,
                    )
                )
            elif graph.resolve_field(operation.class_name, operation.field_name) is not None:
                violations.append(
                    _violation(
                        "symbol_already_exists",
                        f"Field '{operation.class_name}.{operation.field_name}' already exists",
                        f"{operation.class_name}.{operation.field_name}",
                    )
                )

        elif isinstance(operation, RemoveFieldOp):
            if graph.resolve_field(operation.class_name, operation.field_name) is None:
                violations.append(
                    _violation(
                        "symbol_not_found",
                        f"Field '{operation.class_name}.{operation.field_name}' was not found",
                        f"{operation.class_name}.{operation.field_name}",
                    )
                )

        violations.extend(self._check_custom_rules(graph))
        return violations

    def validate_post(self, graph: SemanticGraph, operation: Operation) -> list[dict]:
        violations = self._check_duplicate_definitions(graph)

        if isinstance(operation, RenameFieldOp):
            class_symbol = graph.resolve_class(operation.class_name)
            old_target_id = (
                f"{class_symbol.id}.{operation.field_name}" if class_symbol is not None else None
            )
            old_field = graph.resolve_field(operation.class_name, operation.field_name)
            if graph.resolve_field(operation.class_name, operation.to) is None:
                violations.append(
                    _violation(
                        "validation_failed",
                        f"Rename failed: '{operation.class_name}.{operation.to}' not found after apply",
                        operation.target,
                    )
                )
            if old_field is not None:
                violations.append(
                    _violation(
                        "validation_failed",
                        f"Rename failed: old field '{operation.target}' still exists after apply",
                        operation.target,
                    )
                )
            unresolved_old_refs = [
                ref
                for ref in graph.references
                if ref.ref_type == RefType.FIELD_ACCESS
                and old_target_id is not None
                and ref.to_symbol == old_target_id
            ]
            if unresolved_old_refs:
                locations = sorted({f"{ref.file_path}:{ref.line}" for ref in unresolved_old_refs})
                violations.append(
                    _violation(
                        "validation_failed",
                        f"Rename left references to old field '{operation.field_name}': {locations}",
                        operation.target,
                    )
                )

        violations.extend(self._check_custom_rules(graph))
        return violations

    def _check_custom_rules(self, graph: SemanticGraph) -> list[dict]:
        violations: list[dict] = []
        for rule in self.rules:
            if rule.type == "symbol_uniqueness" and (rule.target or "").upper() == "DTO":
                violations.extend(self._check_dto_uniqueness(graph, rule))
        return violations

    def _check_dto_uniqueness(self, graph: SemanticGraph, rule: RuleDefinition) -> list[dict]:
        violations: list[dict] = []
        class_symbols = [
            symbol
            for symbol in graph.symbols_by_type(SymbolType.CLASS)
            if symbol.extra.get("is_dto", False)
        ]

        by_name: dict[str, list] = {}
        by_shape: dict[tuple[str, ...], list] = {}
        for cls in class_symbols:
            by_name.setdefault(cls.name, []).append(cls)
            fields = tuple(
                sorted(
                    symbol.name
                    for symbol in graph.symbols_by_type(SymbolType.FIELD)
                    if symbol.parent_id == cls.id
                )
            )
            by_shape.setdefault(fields, []).append(cls)

        for name, symbols in by_name.items():
            if len(symbols) <= 1:
                continue
            shapes = {
                tuple(
                    sorted(
                        field.name
                        for field in graph.symbols_by_type(SymbolType.FIELD)
                        if field.parent_id == symbol.id
                    )
                )
                for symbol in symbols
            }
            if len(shapes) > 1:
                violations.append(
                    _violation(
                        rule.id,
                        f"DTO '{name}' is defined with different field sets",
                        name,
                        action=rule.action.value,
                    )
                )

        for shape, symbols in by_shape.items():
            names = {symbol.name for symbol in symbols}
            if shape and len(names) > 1:
                violations.append(
                    _violation(
                        rule.id,
                        f"DTOs share the same structure: {sorted(names)}",
                        ",".join(sorted(names)),
                        action=RuleAction.WARN.value,
                    )
                )

        return violations

    def _check_duplicate_definitions(self, graph: SemanticGraph) -> list[dict]:
        violations: list[dict] = []
        seen: dict[str, list[str]] = {}
        for symbol in graph.symbols_by_type(SymbolType.CLASS):
            seen.setdefault(symbol.id, []).append(symbol.file_path)
        for symbol_id, files in seen.items():
            if len(set(files)) > 1:
                violations.append(
                    _violation(
                        "duplicate_definition",
                        f"Class '{symbol_id}' is defined in multiple files: {files}",
                        symbol_id,
                    )
                )
        return violations


def _violation(
    kind: str,
    message: str,
    target: str | None = None,
    action: str = RuleAction.ERROR.value,
) -> dict:
    result = {"type": kind, "message": message, "action": action}
    if target:
        result["target"] = target
    return result
