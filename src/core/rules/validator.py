"""Rule validator.

Validates operations against predefined rules before and after execution.
Responsibilities: detect errors + block execution. Does NOT auto-fix.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

from core.graph.semantic_graph import SemanticGraph, Symbol, SymbolType
from core.operation.models import (
    AddFieldOp,
    Operation,
    RemoveFieldOp,
    RenameFieldOp,
)

logger = logging.getLogger(__name__)


class RuleAction(str, Enum):
    ERROR = "error"
    WARN = "warn"


@dataclass
class RuleViolation:
    """A single rule violation."""

    rule_id: str
    action: RuleAction
    message: str
    target: str | None = None


@dataclass
class RuleDef:
    """Definition of a validation rule."""

    id: str
    type: str
    target: str | None = None
    action: RuleAction = RuleAction.ERROR


class RuleValidator:
    """Validates operations against project rules."""

    def __init__(self, rules_path: Path | None = None) -> None:
        self.rules: list[RuleDef] = []
        if rules_path and rules_path.exists():
            self._load_rules(rules_path)

    def _load_rules(self, rules_path: Path) -> None:
        """Load rules from a YAML file."""
        try:
            with open(rules_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            for r in data.get("rules", []):
                self.rules.append(
                    RuleDef(
                        id=r["id"],
                        type=r["type"],
                        target=r.get("target"),
                        action=RuleAction(r.get("action", "error")),
                    )
                )
            logger.info("Loaded %d rules from %s", len(self.rules), rules_path)
        except Exception as e:
            logger.warning("Failed to load rules from %s: %s", rules_path, e)

    def validate_pre(self, graph: SemanticGraph, operation: Operation) -> list[dict]:
        """Pre-execution validation.

        Checks that the operation targets valid symbols and doesn't violate rules.

        Returns:
            List of violation dicts. Empty means all clear.
        """
        violations: list[dict] = []

        # Universal: check target symbol exists
        if isinstance(operation, RenameFieldOp):
            sym = graph.get_field_symbol(operation.class_name, operation.field_name)
            if sym is None:
                violations.append({
                    "type": "symbol_not_found",
                    "message": f"Field '{operation.target}' not found in graph",
                    "target": operation.target,
                    "action": "error",
                })
            else:
                # Check new name doesn't already exist
                existing = graph.get_field_symbol(operation.class_name, operation.to)
                if existing:
                    violations.append({
                        "type": "symbol_already_exists",
                        "message": f"Field '{operation.class_name}.{operation.to}' already exists",
                        "target": f"{operation.class_name}.{operation.to}",
                        "action": "error",
                    })

        elif isinstance(operation, AddFieldOp):
            sym = graph.get_symbol(operation.class_name)
            if sym is None:
                violations.append({
                    "type": "symbol_not_found",
                    "message": f"Class '{operation.class_name}' not found",
                    "target": operation.class_name,
                    "action": "error",
                })
            else:
                existing = graph.get_field_symbol(operation.class_name, operation.field_name)
                if existing:
                    violations.append({
                        "type": "symbol_already_exists",
                        "message": f"Field '{operation.class_name}.{operation.field_name}' already exists",
                        "target": f"{operation.class_name}.{operation.field_name}",
                        "action": "error",
                    })

        elif isinstance(operation, RemoveFieldOp):
            sym = graph.get_field_symbol(operation.class_name, operation.field_name)
            if sym is None:
                violations.append({
                    "type": "symbol_not_found",
                    "message": f"Field '{operation.class_name}.{operation.field_name}' not found",
                    "target": f"{operation.class_name}.{operation.field_name}",
                    "action": "error",
                })

        # Run custom rules
        violations.extend(self._check_custom_rules(graph, operation))

        return violations

    def validate_post(self, graph: SemanticGraph, operation: Operation) -> list[dict]:
        """Post-execution validation on the new graph.

        Verifies the graph is still consistent after the operation.
        """
        violations: list[dict] = []

        # Check for duplicate definitions
        violations.extend(self._check_duplicate_definitions(graph))

        # Verify the operation result
        if isinstance(operation, RenameFieldOp):
            new_field = graph.get_field_symbol(operation.class_name, operation.to)
            if new_field is None:
                violations.append({
                    "type": "validation_failed",
                    "message": f"Rename failed: field '{operation.class_name}.{operation.to}' not found after apply",
                    "target": operation.target,
                    "action": "error",
                })
            old_field = graph.get_field_symbol(operation.class_name, operation.field_name)
            if old_field is not None:
                violations.append({
                    "type": "validation_failed",
                    "message": f"Rename failed: old field '{operation.target}' still exists after apply",
                    "target": operation.target,
                    "action": "error",
                })

        return violations

    def _check_custom_rules(
        self, graph: SemanticGraph, operation: Operation
    ) -> list[dict]:
        """Check user-defined rules from rules.yaml."""
        violations: list[dict] = []

        for rule in self.rules:
            if rule.type == "symbol_uniqueness" and rule.target and rule.target.upper() == "DTO":
                violations.extend(self._check_dto_uniqueness(graph, rule))

        return violations

    def _check_dto_uniqueness(
        self, graph: SemanticGraph, rule: RuleDef
    ) -> list[dict]:
        """Check for duplicate DTO definitions.

        Rules:
        - same_name + different_structure -> error
        - different_name + same_structure -> warn
        """
        violations: list[dict] = []
        classes = [s for s in graph.symbols if s.type == SymbolType.CLASS]

        for i, cls_a in enumerate(classes):
            for cls_b in classes[i + 1:]:
                if cls_a.name == cls_b.name and cls_a.file_path != cls_b.file_path:
                    # Same name in different files - check structure
                    fields_a = set(
                        s.name for s in graph.symbols
                        if s.parent_id == cls_a.name and s.type == SymbolType.FIELD
                    )
                    fields_b = set(
                        s.name for s in graph.symbols
                        if s.parent_id == cls_b.name and s.type == SymbolType.FIELD
                    )
                    if fields_a != fields_b:
                        violations.append({
                            "type": rule.id,
                            "message": (
                                f"Duplicate DTO '{cls_a.name}' with different structure "
                                f"in {cls_a.file_path} vs {cls_b.file_path}"
                            ),
                            "action": rule.action.value,
                        })

        return violations

    def _check_duplicate_definitions(self, graph: SemanticGraph) -> list[dict]:
        """Post-apply check for structural consistency."""
        violations: list[dict] = []
        classes = [s for s in graph.symbols if s.type == SymbolType.CLASS]
        seen_names: dict[str, list[str]] = {}

        for cls in classes:
            seen_names.setdefault(cls.name, []).append(cls.file_path)

        for name, files in seen_names.items():
            if len(files) > 1:
                violations.append({
                    "type": "duplicate_definition",
                    "message": f"Class '{name}' defined in multiple files: {files}",
                    "action": "error",
                })

        return violations
