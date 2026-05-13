"""Semantic graph models for the Agent-friendly operation layer."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr


class SymbolType(str, Enum):
    """
    Kinds of symbols tracked in the semantic graph.
    """

    CLASS = "class"
    FIELD = "field"
    METHOD = "method"


class RefType(str, Enum):
    """
    Kinds of references between symbols in the semantic graph.
    """

    TYPE_REF = "type_ref"
    PARAM_REF = "param_ref"
    RETURN_REF = "return_ref"
    FIELD_ACCESS = "field_access"
    METHOD_CALL = "method_call"


class Symbol(BaseModel):
    """
    A semantic symbol known to Voyager.

    Symbols are the nodes of the semantic graph.  Each one corresponds to a
    Java class, field, or method extracted from the source.

    Attributes:
        id: Stable symbol identifier, e.g. ``"com.example.OrderDTO.userId"``.
        type: One of ``CLASS``, ``FIELD``, or ``METHOD``.
        name: Short name as declared in source.
        file_path: File path relative to the project root (or absolute if outside).
        line: 1-based source line of the declaration.
        column: 1-based source column of the declaration.
        parent_id: ID of the enclosing symbol (e.g. containing class for a field).
        extra: Arbitrary additional facts (type name, modifiers, etc.).
    """

    id: str = Field(description="Stable id, e.g. com.example.OrderDTO.userId")
    type: SymbolType
    name: str
    file_path: str
    line: int = 0
    column: int = 0
    parent_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Reference(BaseModel):
    """
    A typed relation between two symbols.

    References are the edges of the semantic graph. Each one records a usage
    of one symbol inside another, e.g. a field of type ``Order`` creates a
    ``TYPE_REF`` from the containing class to ``Order``.

    Attributes:
        from_symbol: ID of the symbol that contains the reference.
        to_symbol: ID of the symbol being referenced.
        ref_type: Kind of reference (TYPE_REF, PARAM_REF, RETURN_REF, FIELD_ACCESS).
        file_path: File where the reference appears.
        line: 1-based source line of the reference.
        column: 1-based source column of the reference.
        extra: Arbitrary additional facts about the reference.
    """

    from_symbol: str
    to_symbol: str
    ref_type: RefType
    file_path: str
    line: int = 0
    column: int = 0
    extra: dict[str, Any] = Field(default_factory=dict)


# NOTE: Agents and LLM-assisted tools may default to grep-based search instead of
# querying the SemanticGraph.  The graph provides precise, typed lookups (resolve_field,
# find_references_to, etc.) that are more reliable than text search for semantic operations.
class SemanticGraph(BaseModel):
    """
    Minimal V1 code graph.

    The graph is the "weak PSI layer" described in the design docs: it turns LSP
    coordinates and parser facts into stable objects that operations can target.
    It provides in-memory lookup indexes for fast symbol and reference resolution.

    Attributes:
        project_path: Root path of the scanned project.
        symbols: All symbols (classes, fields, methods) discovered during scan.
        references: All typed references between symbols.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    project_path: str = ""
    symbols: list[Symbol] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)
    # A field's references are equivalent to IntelliJ IDEA's "Find Usages" results —
    # every location where the symbol is referenced across the project.

    _symbol_index: dict[str, Symbol] = PrivateAttr(default_factory=dict)
    _simple_index: dict[tuple[SymbolType, str], list[Symbol]] = PrivateAttr(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        self.build_index()

    # Incremental index refresh is not yet implemented.  After applying patches or
    # adding/removing files, call build_index() to rebuild from scratch.  A future
    # optimization should support partial reindexing for large projects.
    def build_index(self) -> None:
        """
        Build in-memory lookup indexes.
        """
        self._symbol_index = {symbol.id: symbol for symbol in self.symbols}
        simple_index: dict[tuple[SymbolType, str], list[Symbol]] = {}
        for symbol in self.symbols:
            simple_index.setdefault((symbol.type, symbol.name), []).append(symbol)
        self._simple_index = simple_index
        # _simple_index maps (SymbolType, simple_name) → list[Symbol].  Multiple symbols
        # can share the same simple name (e.g. "userId" fields across different classes),
        # so resolution via _simple_index requires disambiguation (see resolve_class/resolve_field).

    def get_symbol(self, symbol_id: str) -> Symbol | None:
        return self._symbol_index.get(symbol_id)

    def resolve_class(self, class_name: str) -> Symbol | None:
        """
        Resolve a class by FQN or unambiguous simple name.
        """
        symbol = self.get_symbol(class_name)
        if symbol and symbol.type == SymbolType.CLASS:
            return symbol
        matches = self._simple_index.get((SymbolType.CLASS, class_name), [])
        return matches[0] if len(matches) == 1 else None

    def resolve_field(self, class_name: str, field_name: str) -> Symbol | None:
        """
        Resolve a field by class FQN/simple name and field name.
        """
        class_symbol = self.resolve_class(class_name)
        if class_symbol is None:
            return None
        return self.get_symbol(f"{class_symbol.id}.{field_name}")

    def get_field_symbol(self, class_name: str, field_name: str) -> Symbol | None:
        return self.resolve_field(class_name, field_name)

    def resolve_method(self, class_name: str, method_name: str) -> Symbol | None:
        """
        Resolve a method by class FQN/simple name and method name.

        V1 method IDs include parameter types, so overloaded methods remain
        distinct. Name-only resolution is still intentionally ambiguous when a
        class has multiple overloads with the same simple name.
        """
        matches = self.find_methods(class_name, method_name)
        return matches[0] if len(matches) == 1 else None

    def find_methods(self, class_name: str, method_name: str) -> list[Symbol]:
        """
        Return all methods in a class with the given simple method name.
        """
        class_symbol = self.resolve_class(class_name)
        if class_symbol is None:
            return []
        return [
            symbol
            for symbol in self.symbols
            if symbol.type == SymbolType.METHOD
            and symbol.parent_id == class_symbol.id
            and symbol.name == method_name
        ]

    def find_references_to(self, symbol_id: str) -> list[Reference]:
        return [ref for ref in self.references if ref.to_symbol == symbol_id]

    def find_references_from(self, symbol_id: str) -> list[Reference]:
        return [ref for ref in self.references if ref.from_symbol == symbol_id]

    def get_affected_files_for_field(self, class_name: str, field_name: str) -> list[str]:
        """
        Return files affected by a field operation.
        """
        field = self.resolve_field(class_name, field_name)
        if field is None:
            return []

        files = {field.file_path}
        parent = self.get_symbol(field.parent_id or "")
        if parent is not None:
            files.add(parent.file_path)

        for ref in self.find_references_to(field.id):
            files.add(ref.file_path)

        for method in self._bean_accessor_symbols(field):
            files.add(method.file_path)
            for ref in self.find_references_to(method.id):
                files.add(ref.file_path)
        return sorted(files)

    def get_affected_files_for_method(self, class_name: str, method_name: str) -> list[str]:
        """
        Return files affected by a method operation.
        """
        method = self.resolve_method(class_name, method_name)
        if method is None:
            return []

        files = {method.file_path}
        for ref in self.find_references_to(method.id):
            files.add(ref.file_path)
        return sorted(files)

    def get_affected_files_for_class(self, class_name: str) -> list[str]:
        """
        Return files affected by a class operation.
        """
        class_symbol = self.resolve_class(class_name)
        if class_symbol is None:
            return []

        files = {class_symbol.file_path}
        for ref in self.find_references_to(class_symbol.id):
            files.add(ref.file_path)

        for symbol in self.symbols:
            if symbol.parent_id == class_symbol.id:
                files.add(symbol.file_path)
        return sorted(files)

    def symbols_by_type(self, symbol_type: SymbolType) -> list[Symbol]:
        return [symbol for symbol in self.symbols if symbol.type == symbol_type]

    def _bean_accessor_symbols(self, field: Symbol) -> list[Symbol]:
        """
        Return JavaBean accessor methods that are conventionally tied to a field.
        """
        parent_id = field.parent_id
        if not parent_id:
            return []

        suffix = _java_bean_suffix(field.name)
        names = {f"get{suffix}", f"set{suffix}", f"is{suffix}"}
        return [
            symbol
            for symbol in self.symbols
            if symbol.type == SymbolType.METHOD
            and symbol.parent_id == parent_id
            and symbol.name in names
        ]


def _java_bean_suffix(name: str) -> str:
    if not name:
        return ""
    if len(name) > 1 and name[0].islower() and name[1].isupper():
        return name
    return name[:1].upper() + name[1:]
