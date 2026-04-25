"""Semantic graph builder.

Builds a SemanticGraph from parsed JavaClass objects by
extracting symbols and resolving references.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.graph.semantic_graph import (
    RefType,
    Reference,
    SemanticGraph,
    Symbol,
    SymbolType,
)
from core.parser.java_parser import JavaClass

logger = logging.getLogger(__name__)


class GraphBuilder:
    """Builds a SemanticGraph from a list of JavaClass."""

    def __init__(self) -> None:
        self._graph = SemanticGraph()
        self._class_map: dict[str, JavaClass] = {}
        self._fqcn_map: dict[str, str] = {}  # simple_name -> fqn

    def build(self, classes: list[JavaClass]) -> SemanticGraph:
        """Build a complete semantic graph.

        Args:
            classes: List of parsed JavaClass objects.

        Returns:
            A fully constructed SemanticGraph.
        """
        self._graph = SemanticGraph()
        self._class_map = {}
        self._fqcn_map = {}

        # Step 1: register all classes
        for cls in classes:
            self._register_class(cls)

        # Step 2: extract all symbols (class, field, method)
        for cls in classes:
            self._extract_symbols(cls)

        # Step 3: resolve references
        for cls in classes:
            self._extract_references(cls)

        self._graph.build_index()
        logger.info(
            "Built graph: %d symbols, %d references",
            len(self._graph.symbols),
            len(self._graph.references),
        )
        return self._graph

    def _register_class(self, cls: JavaClass) -> None:
        """Register a class for FQCN lookup."""
        fqn = f"{cls.package}.{cls.name}" if cls.package else cls.name
        self._class_map[fqn] = cls
        self._class_map[cls.name] = cls
        self._fqcn_map[cls.name] = fqn

    def _resolve_type_to_class(self, type_name: str) -> str | None:
        """Try to resolve a type name to a known class name."""
        if type_name in self._class_map:
            return type_name
        # Strip generic parameters
        base = type_name.split("<")[0]
        if base in self._class_map:
            return base
        # Try imports resolution
        for fqn, cls in self._class_map.items():
            if fqn.endswith(f".{type_name}"):
                return cls.name
        return None

    def _extract_symbols(self, cls: JavaClass) -> None:
        """Extract all symbols from a JavaClass."""
        class_symbol = Symbol(
            id=cls.name,
            type=SymbolType.CLASS,
            name=cls.name,
            file_path=str(cls.file_path),
            line=cls.line,
            column=cls.column,
        )
        self._graph.symbols.append(class_symbol)

        for f in cls.fields:
            field_symbol = Symbol(
                id=f"{cls.name}.{f.name}",
                type=SymbolType.FIELD,
                name=f.name,
                file_path=str(cls.file_path),
                line=f.line,
                column=f.column,
                parent_id=cls.name,
                extra={"type_name": f.type_name, "modifiers": f.modifiers},
            )
            self._graph.symbols.append(field_symbol)

        for m in cls.methods:
            method_symbol = Symbol(
                id=f"{cls.name}.{m.name}",
                type=SymbolType.METHOD,
                name=m.name,
                file_path=str(cls.file_path),
                line=m.line,
                column=m.column,
                parent_id=cls.name,
                extra={
                    "return_type": m.return_type,
                    "parameters": [(p.name, p.type_name) for p in m.parameters],
                    "modifiers": m.modifiers,
                },
            )
            self._graph.symbols.append(method_symbol)

    def _extract_references(self, cls: JavaClass) -> None:
        """Extract type references from a JavaClass."""
        # Method parameters referencing other classes
        for m in cls.methods:
            for param in m.parameters:
                target_class = self._resolve_type_to_class(param.type_name)
                if target_class:
                    ref = Reference(
                        from_symbol=f"{cls.name}.{m.name}",
                        to_symbol=target_class,
                        ref_type=RefType.PARAM_REF,
                        file_path=str(cls.file_path),
                        line=m.line,
                    )
                    self._graph.references.append(ref)

            # Return type references
            if m.return_type:
                target_class = self._resolve_type_to_class(m.return_type)
                if target_class:
                    ref = Reference(
                        from_symbol=f"{cls.name}.{m.name}",
                        to_symbol=target_class,
                        ref_type=RefType.RETURN_REF,
                        file_path=str(cls.file_path),
                        line=m.line,
                    )
                    self._graph.references.append(ref)

        # Field type references
        for f in cls.fields:
            target_class = self._resolve_type_to_class(f.type_name)
            if target_class:
                ref = Reference(
                    from_symbol=cls.name,
                    to_symbol=target_class,
                    ref_type=RefType.TYPE_REF,
                    file_path=str(cls.file_path),
                    line=f.line,
                )
                self._graph.references.append(ref)
