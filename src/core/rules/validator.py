"""Rule validation for patch-first Voyager operations."""

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

from core.graph.semantic_graph import SemanticGraph, SymbolType
from core.operation.models import Operation, PatchOperation

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
        action: ``"error"`` or ``"warn"``.
    """

    id: str
    type: str
    target: str | None = None
    action: RuleAction = RuleAction.ERROR


class RuleValidator:
    """
    Validate patch operations and graph-level invariants.
    """

    def __init__(self, rules_path: Path | None = None) -> None:
        self.rules: list[RuleDefinition] = []
        if rules_path and rules_path.exists():
            self._load_rules(rules_path)

    def validate_pre(self, graph: SemanticGraph, operation: Operation) -> list[dict]:
        """
        Validate an operation before patch construction.
        """
        violations: list[dict] = []
        if not isinstance(operation, PatchOperation):
            violations.append(
                _violation(
                    "unsupported_operation",
                    "Voyager editing is patch-only; submit a unified diff patch set",
                )
            )
        violations.extend(self._check_custom_rules(graph))
        return violations

    def validate_post(self, graph: SemanticGraph, operation: Operation) -> list[dict]:
        """
        Validate the graph produced by a virtual patch transaction.
        """
        violations = self._check_duplicate_definitions(graph)
        violations.extend(self._check_custom_rules(graph))
        return violations

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
    """
    Return a JSON-friendly validation violation.
    """
    result = {"type": kind, "message": message, "action": action}
    if target:
        result["target"] = target
    return result
