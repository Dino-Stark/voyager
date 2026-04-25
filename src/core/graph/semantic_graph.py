"""Semantic graph data models.

Defines the core data structures for the semantic graph:
symbols (classes, fields, methods) and their references.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class SymbolType(str, Enum):
    CLASS = "class"
    FIELD = "field"
    METHOD = "method"


class RefType(str, Enum):
    TYPE_REF = "type_ref"
    PARAM_REF = "param_ref"
    RETURN_REF = "return_ref"
    FIELD_ACCESS = "field_access"


class Symbol(BaseModel):
    """A symbol in the semantic graph."""

    id: str = Field(description="Fully qualified symbol id, e.g. 'OrderDTO.userId'")
    type: SymbolType
    name: str
    file_path: str
    line: int = 0
    column: int = 0
    parent_id: str | None = None
    extra: dict = Field(default_factory=dict)


class Reference(BaseModel):
    """A reference from one symbol to another."""

    from_symbol: str = Field(description="Source symbol id")
    to_symbol: str = Field(description="Target symbol id")
    ref_type: RefType
    file_path: str
    line: int = 0
    column: int = 0


class SemanticGraph(BaseModel):
    """The semantic graph containing all symbols and references."""

    symbols: list[Symbol] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)
    _symbol_index: dict[str, Symbol] | None = None

    def build_index(self) -> None:
        """Build a lookup index by symbol id."""
        self._symbol_index = {s.id: s for s in self.symbols}

    def get_symbol(self, symbol_id: str) -> Symbol | None:
        """Look up a symbol by id."""
        if self._symbol_index is None:
            self.build_index()
        return self._symbol_index.get(symbol_id)

    def get_field_symbol(self, class_name: str, field_name: str) -> Symbol | None:
        """Look up a field symbol by class name and field name."""
        target_id = f"{class_name}.{field_name}"
        return self.get_symbol(target_id)

    def find_references_to(self, symbol_id: str) -> list[Reference]:
        """Find all references pointing to a symbol."""
        return [r for r in self.references if r.to_symbol == symbol_id]

    def find_references_from(self, symbol_id: str) -> list[Reference]:
        """Find all references originating from a symbol."""
        return [r for r in self.references if r.from_symbol == symbol_id]

    def get_affected_files_for_field(self, class_name: str, field_name: str) -> list[str]:
        """Get all files that reference a specific field."""
        field_id = f"{class_name}.{field_name}"
        refs = self.find_references_to(field_id)
        files = {r.file_path for r in refs}
        symbol = self.get_symbol(field_id)
        if symbol:
            files.add(symbol.file_path)
        return sorted(files)
