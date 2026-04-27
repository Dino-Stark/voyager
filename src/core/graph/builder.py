"""Build Voyager's semantic graph from parsed Java classes."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from core.graph.semantic_graph import RefType, Reference, SemanticGraph, Symbol, SymbolType
from core.parser.java_parser import JavaClass

logger = logging.getLogger(__name__)


class GraphBuilder:
    """Build a minimal symbol/reference graph for Java POJO projects."""

    def __init__(self, project_path: Path | None = None) -> None:
        self.project_path = project_path.resolve() if project_path else None
        self._graph = SemanticGraph(project_path=str(self.project_path or ""))
        self._classes_by_fqn: dict[str, JavaClass] = {}
        self._fqn_by_simple: dict[str, list[str]] = {}

    def build(self, classes: list[JavaClass]) -> SemanticGraph:
        """Build a complete graph from parsed Java classes."""

        self._graph = SemanticGraph(project_path=str(self.project_path or ""))
        self._classes_by_fqn = {}
        self._fqn_by_simple = {}

        for cls in classes:
            fqn = cls.fqn
            self._classes_by_fqn[fqn] = cls
            self._fqn_by_simple.setdefault(cls.name, []).append(fqn)

        for cls in classes:
            self._extract_symbols(cls)

        for cls in classes:
            self._extract_type_references(cls)
            self._extract_field_access_references(cls)

        self._graph.build_index()
        logger.info(
            "Built semantic graph: %d symbols, %d references",
            len(self._graph.symbols),
            len(self._graph.references),
        )
        return self._graph

    def _extract_symbols(self, cls: JavaClass) -> None:
        class_id = cls.fqn
        file_path = self._display_path(cls.file_path)
        self._graph.symbols.append(
            Symbol(
                id=class_id,
                type=SymbolType.CLASS,
                name=cls.name,
                file_path=file_path,
                line=cls.line,
                column=cls.column,
                extra={
                    "package": cls.package,
                    "imports": cls.imports,
                    "is_dto": cls.is_dto,
                },
            )
        )

        for field in cls.fields:
            self._graph.symbols.append(
                Symbol(
                    id=f"{class_id}.{field.name}",
                    type=SymbolType.FIELD,
                    name=field.name,
                    file_path=file_path,
                    line=field.line,
                    column=field.column,
                    parent_id=class_id,
                    extra={"type_name": field.type_name, "modifiers": field.modifiers},
                )
            )

        for method in cls.methods:
            self._graph.symbols.append(
                Symbol(
                    id=f"{class_id}.{method.name}",
                    type=SymbolType.METHOD,
                    name=method.name,
                    file_path=file_path,
                    line=method.line,
                    column=method.column,
                    parent_id=class_id,
                    extra={
                        "return_type": method.return_type,
                        "parameters": [
                            {"name": param.name, "type_name": param.type_name}
                            for param in method.parameters
                        ],
                        "modifiers": method.modifiers,
                    },
                )
            )

    def _extract_type_references(self, cls: JavaClass) -> None:
        class_id = cls.fqn
        file_path = self._display_path(cls.file_path)

        for field in cls.fields:
            target = self._resolve_type(field.type_name, cls)
            if target:
                self._add_reference(
                    class_id,
                    target,
                    RefType.TYPE_REF,
                    file_path,
                    field.line,
                    field.column,
                    {"via": field.name, "type_name": field.type_name},
                )

        for method in cls.methods:
            from_symbol = f"{class_id}.{method.name}"
            if method.return_type:
                target = self._resolve_type(method.return_type, cls)
                if target:
                    self._add_reference(
                        from_symbol,
                        target,
                        RefType.RETURN_REF,
                        file_path,
                        method.line,
                        method.column,
                        {"type_name": method.return_type},
                    )

            for param in method.parameters:
                target = self._resolve_type(param.type_name, cls)
                if target:
                    self._add_reference(
                        from_symbol,
                        target,
                        RefType.PARAM_REF,
                        file_path,
                        method.line,
                        method.column,
                        {"parameter": param.name, "type_name": param.type_name},
                    )

    def _extract_field_access_references(self, cls: JavaClass) -> None:
        """Extract simple typed field access references.

        This is intentionally conservative.  It only records ``var.field`` when
        ``var`` has an explicit type in the same file and that type is a known
        class.  Ambiguous cases are ignored.
        """

        try:
            text = cls.file_path.read_text(encoding="utf-8")
        except OSError:
            return

        variable_types = self._collect_variable_types(text, cls)
        if not variable_types:
            return

        file_path = self._display_path(cls.file_path)
        for var_name, class_id in variable_types.items():
            target_cls = self._classes_by_fqn.get(class_id)
            if target_cls is None:
                continue
            known_fields = {field.name for field in target_cls.fields}
            pattern = re.compile(
                rf"\b{re.escape(var_name)}\s*\.\s*(?P<member>[A-Za-z_$][\w$]*)\b"
            )
            for match in pattern.finditer(text):
                end = match.end()
                if end < len(text) and text[end:].lstrip().startswith("("):
                    continue
                member = match.group("member")
                target_field = f"{class_id}.{member}"
                from_symbol = self._nearest_method_id(cls, text, match.start()) or cls.fqn
                self._add_reference(
                    from_symbol,
                    target_field,
                    RefType.FIELD_ACCESS,
                    file_path,
                    text.count("\n", 0, match.start()) + 1,
                    match.start() - text.rfind("\n", 0, match.start()),
                    {"receiver": var_name, "resolved": member in known_fields},
                )

    def _collect_variable_types(self, text: str, cls: JavaClass) -> dict[str, str]:
        result: dict[str, str] = {}
        type_names = sorted(self._fqn_by_simple, key=len, reverse=True)
        if not type_names:
            return result

        type_pattern = "|".join(re.escape(name) for name in type_names)
        decl_re = re.compile(rf"\b(?P<type>{type_pattern})\s+(?P<name>[A-Za-z_$][\w$]*)\b")
        for match in decl_re.finditer(_remove_comments_and_strings(text)):
            type_name = match.group("type")
            var_name = match.group("name")
            target = self._resolve_type(type_name, cls)
            if target and var_name not in self._fqn_by_simple:
                result[var_name] = target
        return result

    def _nearest_method_id(self, cls: JavaClass, text: str, index: int) -> str | None:
        line = text.count("\n", 0, index) + 1
        previous = [method for method in cls.methods if method.line <= line]
        if not previous:
            return None
        method = max(previous, key=lambda item: item.line)
        return f"{cls.fqn}.{method.name}"

    def _resolve_type(self, type_name: str, context: JavaClass) -> str | None:
        for candidate in _type_candidates(type_name):
            if candidate in self._classes_by_fqn:
                return candidate
            if "." in candidate:
                simple = candidate.rsplit(".", 1)[-1]
                matches = self._fqn_by_simple.get(simple, [])
                if candidate in matches:
                    return candidate

            same_package = f"{context.package}.{candidate}" if context.package else candidate
            if same_package in self._classes_by_fqn:
                return same_package

            for imported in context.imports:
                if imported.endswith(f".{candidate}") and imported in self._classes_by_fqn:
                    return imported

            matches = self._fqn_by_simple.get(candidate, [])
            if len(matches) == 1:
                return matches[0]
        return None

    def _add_reference(
        self,
        from_symbol: str,
        to_symbol: str,
        ref_type: RefType,
        file_path: str,
        line: int = 0,
        column: int = 0,
        extra: dict | None = None,
    ) -> None:
        ref = Reference(
            from_symbol=from_symbol,
            to_symbol=to_symbol,
            ref_type=ref_type,
            file_path=file_path,
            line=line,
            column=column,
            extra=extra or {},
        )
        key = (ref.from_symbol, ref.to_symbol, ref.ref_type, ref.file_path, ref.line, ref.column)
        existing = {
            (item.from_symbol, item.to_symbol, item.ref_type, item.file_path, item.line, item.column)
            for item in self._graph.references
        }
        if key not in existing:
            self._graph.references.append(ref)

    def _display_path(self, path: Path) -> str:
        resolved = path.resolve()
        if self.project_path is not None:
            try:
                return resolved.relative_to(self.project_path).as_posix()
            except ValueError:
                pass
        return resolved.as_posix()


def _type_candidates(type_name: str) -> list[str]:
    clean = re.sub(r"@\w+(?:\([^)]*\))?", "", type_name)
    clean = clean.replace("...", "[]").strip()
    tokens = re.findall(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*", clean)
    primitives = {
        "byte",
        "short",
        "int",
        "long",
        "float",
        "double",
        "boolean",
        "char",
        "void",
        "String",
        "Object",
        "List",
        "Set",
        "Map",
        "Optional",
    }
    return [token for token in tokens if token not in primitives]


def _remove_comments_and_strings(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))

    text = re.sub(r"/\*.*?\*/", replace, text, flags=re.DOTALL)
    text = re.sub(r"//.*", replace, text)
    text = re.sub(r'"(?:\\.|[^"\\])*"', replace, text)
    text = re.sub(r"'(?:\\.|[^'\\])*'", replace, text)
    return text
