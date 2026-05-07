"""Java source analyzer for Voyager V1.

The parser has two modes:

* LSP mode uses ``textDocument/documentSymbol`` when jdtls is available.
* Static mode is a conservative POJO/DTO parser used for scan/plan and tests.

Only execution of semantic rename requires LSP.  Static parsing deliberately
extracts simple structure and explicit references; it does not guess dynamic
behavior.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from core.lsp.client import LspClient, LspSymbolInfo
from core.lsp.config import Language, get_language_config
from utils.async_helpers import run_async

logger = logging.getLogger(__name__)


@dataclass
class JavaField:
    """
    A Java field or constructor parameter declaration.

    Represents a single member variable or a method parameter.  Source positions
    (``line``, ``column``) are 1-based, matching the original file.

    Attributes:
        name: Field or parameter name.
        type_name: Fully qualified or simple type name as written in source.
        modifiers: Java modifiers, e.g. ``["private", "final"]``.
        line: 1-based line number of the declaration in source.
        column: 1-based column number of the declaration in source.
    """

    name: str
    type_name: str
    modifiers: list[str] = field(default_factory=list)
    line: int = 0
    column: int = 0


@dataclass
class JavaMethod:
    """
    A Java method or constructor declaration.

    Attributes:
        name: Method name.
        return_type: Return type as written in source, or ``None`` for constructors.
        parameters: List of formal parameters.
        modifiers: Java modifiers, e.g. ``["public", "static"]``.
        line: 1-based line number of the declaration in source.
        column: 1-based column number of the declaration in source.
    """

    name: str
    return_type: str | None = None
    parameters: list[JavaField] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)
    line: int = 0
    column: int = 0


@dataclass
class JavaClass:
    """
    A Java top-level type (class, interface, enum, or record).

    A flattened view of a single type produced by either the LSP
    ``documentSymbol`` mode or the built-in static parser.

    Attributes:
        name: Simple class name.
        file_path: Absolute path to the source file.
        package: Java package, e.g. ``"com.example"``.
        fields: Declared fields and constructor parameters.
        methods: Declared methods and constructors.
        imports: All ``import`` statements in the source file.
        line: 1-based line number of the type declaration.
        column: 1-based column number of the type declaration.
        is_dto: Whether this type looks like a DTO/POJO (heuristic-based).
    """

    name: str
    file_path: Path
    package: str = ""
    fields: list[JavaField] = field(default_factory=list)
    methods: list[JavaMethod] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    line: int = 0
    column: int = 0
    is_dto: bool = False

    @property
    def fqn(self) -> str:
        return f"{self.package}.{self.name}" if self.package else self.name



CLASS_KINDS = {5, 11, 23}  # Class, Struct, TypeParameter-ish fallback
INTERFACE_KIND = 11
ENUM_KIND = 10
FIELD_KIND = 8
METHOD_KIND = 6
CONSTRUCTOR_KIND = 9
PROPERTY_KIND = 7

_IDENT = r"[A-Za-z_$][\w$]*"
_TYPE = r"[\w$.\[\]<>?, extends super&\s]+"
_CLASS_RE = re.compile(
    rf"\b(?P<mods>(?:(?:public|protected|private|abstract|final|static|sealed|non-sealed)\s+)*)"
    rf"(?P<kind>class|interface|enum|record)\s+"
    rf"(?P<name>{_IDENT})\b"
)
_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.*]+)\s*;", re.MULTILINE)
_FIELD_RE = re.compile(
    rf"^\s*(?P<mods>(?:(?:public|protected|private|static|final|volatile|transient)\s+)*)"
    rf"(?P<type>{_TYPE}?)\s+"
    rf"(?P<name>{_IDENT})\s*(?:=\s*[^;]+)?;\s*$"
)
_METHOD_RE = re.compile(
    rf"^\s*(?P<mods>(?:(?:public|protected|private|static|final|abstract|synchronized|native)\s+)*)"
    rf"(?:(?P<type>{_TYPE}?)\s+)?"
    rf"(?P<name>{_IDENT})\s*\((?P<params>[^)]*)\)\s*"
    rf"(?:throws\s+[\w.,\s]+)?(?:\{{|;)\s*$"
)
_ANNOTATION_RE = re.compile(r"^\s*@")
_COMMENT_LINE_RE = re.compile(r"^\s*(//|\*)")


def parse_java_project(project_path: Path, prefer_lsp: bool = True) -> list[JavaClass]:
    """
    Parse all Java files under ``project_path``.

    LSP is attempted first when available.  If jdtls is not installed or fails
    to initialize, Voyager falls back to the static parser so scan and plan stay
    useful in lightweight environments.

    Args:
        project_path: Root path of the project.
        prefer_lsp: Always ``true``, will be removed later.

    Returns: A list of parsed Java classes.

    """

    # JDTLS must be fully initialized before LSP queries return correct results.
    # If jdtls is missing or crashes, we fall back to the static parser so that
    # scan and plan remain usable in lightweight environments.

    project_path = project_path.resolve()
    if prefer_lsp and get_language_config(Language.JAVA).find_server_command():
        try:
            classes: list[JavaClass] = run_async(_analyze_with_lsp(project_path))

            # The static parser runs as a completeness check: LSP may return partial
            # results if jdtls hasn't finished indexing.  If the LSP result covers at
            # least as many classes/fields, we trust it; otherwise we fall back.
            static_classes: list[JavaClass] = parse_java_project_static(project_path)
            if _is_lsp_result_complete_enough(classes, static_classes):
                return classes
            logger.warning("LSP Java analysis was incomplete, falling back to static parser")
            return static_classes
        except Exception as exc:
            logger.warning("LSP Java analysis failed, falling back to static parser: %s", exc)

    return parse_java_project_static(project_path)


def parse_java_project_static(project_path: Path) -> list[JavaClass]:
    """Parse all Java files using the built-in conservative parser."""

    classes: list[JavaClass] = []
    for file_path in sorted(project_path.rglob("*.java")):
        if _is_ignored_path(file_path):
            continue
        try:
            classes.extend(parse_java_file(file_path))
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", file_path, exc)
    return classes


def parse_java_project_static_with_overrides(
    project_path: Path, file_overrides: dict[Path, str]
) -> list[JavaClass]:
    """Parse a project using in-memory content for selected files.

    Used by the execution engine for post-validation: after applying patches
    in-memory, this re-parses the project with the modified file contents so
    that rule validators can check the would-be result before committing.

    For on-disk modifications, use ``parse_java_project_static()`` after the
    execution engine has committed the patches via ``_commit()``.
    """

    project_path = project_path.resolve()
    normalized = {path.resolve(): content for path, content in file_overrides.items()}
    classes: list[JavaClass] = []
    for file_path in sorted(project_path.rglob("*.java")):
        if _is_ignored_path(file_path):
            continue
        resolved = file_path.resolve()
        try:
            if resolved in normalized:
                classes.extend(parse_java_source(resolved, normalized[resolved]))
            else:
                classes.extend(parse_java_file(resolved))
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", resolved, exc)
    return classes


def parse_java_file(file_path: Path) -> list[JavaClass]:
    """Parse one Java file with the static parser."""

    text = file_path.read_text(encoding="utf-8")
    return parse_java_source(file_path.resolve(), text)


def parse_java_source(file_path: Path, text: str) -> list[JavaClass]:
    """Parse Java source text as if it came from ``file_path``."""

    package = _parse_package(text)
    imports = _parse_imports(text)
    classes: list[JavaClass] = []

    for match in _CLASS_RE.finditer(_strip_comments_keep_layout(text)):
        name = match.group("name")
        body_start = text.find("{", match.end())
        if body_start < 0:
            continue
        body_end = _find_matching_brace(text, body_start)
        if body_end is None:
            continue

        line = _line_number(text, match.start())
        column = _column_number(text, match.start("name"))
        body = text[body_start + 1 : body_end]
        body_base_line = _line_number(text, body_start + 1)
        cls = JavaClass(
            name=name,
            file_path=file_path.resolve(),
            package=package,
            imports=imports,
            line=line,
            column=column,
        )
        _parse_class_members(cls, body, body_base_line)
        cls.is_dto = _is_dto(cls)
        classes.append(cls)

    return classes


async def _analyze_with_lsp(project_path: Path) -> list[JavaClass]:
    async with LspClient(Language.JAVA, project_path=project_path) as client:
        java_files = [
            path for path in sorted(project_path.rglob("*.java")) if not _is_ignored_path(path)
        ]
        classes: list[JavaClass] = []
        for file_path in java_files:
            symbols: list[LspSymbolInfo] = await client.get_symbols(file_path)
            for symbol in symbols:
                if _is_type_symbol(symbol):
                    cls = _symbol_to_java_class(file_path, symbol)
                    if cls is not None:
                        classes.append(cls)
        return classes


def _symbol_to_java_class(file_path: Path, class_symbol_info: LspSymbolInfo) -> JavaClass | None:
    # We read the source file because documentSymbol does not provide package/imports,
    # which are needed for FQN construction and type resolution in GraphBuilder.
    text = file_path.read_text(encoding="utf-8")
    cls = JavaClass(
        name=class_symbol_info.name,
        file_path=file_path.resolve(),
        package=_parse_package(text),
        imports=_parse_imports(text),
        line=class_symbol_info.selection_range.start.line + 1 if class_symbol_info.selection_range else 0,
        column=class_symbol_info.selection_range.start.character + 1 if class_symbol_info.selection_range else 0,
    )

    for child in class_symbol_info.children:
        if child.kind in {FIELD_KIND, PROPERTY_KIND}:
            type_name = _clean_lsp_detail(child.detail) or _infer_field_type_from_line(
                text, child.selection_range.start.line + 1 if child.selection_range else 0, child.name
            )
            cls.fields.append(
                JavaField(
                    name=child.name,
                    type_name=type_name or "Object",
                    line=child.selection_range.start.line + 1 if child.selection_range else 0,
                    column=child.selection_range.start.character + 1 if child.selection_range else 0,
                )
            )
        elif child.kind in {METHOD_KIND, CONSTRUCTOR_KIND}:
            return_type, params = _parse_method_detail(child.detail)
            # jdtls hierarchical document symbols put the signature in the
            # *name* field, e.g. "setOrderId(String)".  Try to extract
            # parameters from the name when detail is empty or lacks them.
            method_name = child.name
            if "(" in child.name:
                method_name, params_from_name = _parse_method_detail(child.name)
                method_name = method_name or child.name.split("(")[0]
                if params_from_name:
                    params = params_from_name
            cls.methods.append(
                JavaMethod(
                    name=method_name,
                    return_type=return_type if child.kind != CONSTRUCTOR_KIND else cls.name,
                    parameters=params,
                    line=child.selection_range.start.line + 1 if child.selection_range else 0,
                    column=child.selection_range.start.character + 1 if child.selection_range else 0,
                )
            )

    cls.is_dto = _is_dto(cls)
    return cls


def _parse_class_members(cls: JavaClass, body: str, body_base_line: int) -> None:
    """Parse direct members from a class body."""

    lines = body.splitlines()
    depth = 0
    pending = ""
    pending_start_line = body_base_line
    pending_start_column = 1

    for offset, line in enumerate(lines):
        stripped = line.strip()
        current_line = body_base_line + offset

        if depth == 0 and stripped and not _ANNOTATION_RE.match(line) and not _COMMENT_LINE_RE.match(line):
            if not pending:
                pending_start_line = current_line
                pending_start_column = len(line) - len(line.lstrip()) + 1
            pending = (pending + " " + stripped).strip()

            if stripped.endswith(";"):
                _parse_member_statement(cls, pending, pending_start_line, pending_start_column)
                pending = ""
            elif stripped.endswith("{") or stripped.endswith("}"):
                _parse_member_statement(cls, pending, pending_start_line, pending_start_column)
                pending = ""

        depth += _brace_delta(_remove_string_literals(line))
        if depth < 0:
            depth = 0


def _parse_member_statement(
    cls: JavaClass, statement: str, line: int, column: int
) -> None:
    statement = _drop_generics_prefix(statement.strip())
    if not statement or statement.startswith(("if ", "for ", "while ", "switch ", "catch ")):
        return

    method_match = _METHOD_RE.match(statement)
    if method_match and "(" in statement:
        name = method_match.group("name")
        if name in {"if", "for", "while", "switch", "catch", "new"}:
            return
        modifiers = _split_modifiers(method_match.group("mods"))
        return_type = _normalize_type(method_match.group("type") or cls.name)
        params = _parse_parameters(method_match.group("params"))
        cls.methods.append(
            JavaMethod(
                name=name,
                return_type=return_type,
                parameters=params,
                modifiers=modifiers,
                line=line,
                column=column + max(statement.find(name), 0),
            )
        )
        return

    if "(" in statement:
        return

    field_match = _FIELD_RE.match(statement)
    if not field_match:
        return
    modifiers = _split_modifiers(field_match.group("mods"))
    if "static" in modifiers and "final" in modifiers:
        return
    type_name = _normalize_type(field_match.group("type"))
    name = field_match.group("name")
    if not type_name or type_name in {"return", "throw"}:
        return
    cls.fields.append(
        JavaField(
            name=name,
            type_name=type_name,
            modifiers=modifiers,
            line=line,
            column=column + max(statement.find(name), 0),
        )
    )


def _parse_parameters(params: str) -> list[JavaField]:
    result: list[JavaField] = []
    for raw in _split_top_level(params, ","):
        raw = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", raw.strip())
        raw = re.sub(r"\bfinal\s+", "", raw)
        if not raw:
            continue
        parts = raw.rsplit(None, 1)
        if len(parts) == 2:
            type_name, name = parts
            name = name.strip()
        else:
            # jdtls detail only contains types, no names (e.g. "String, int")
            type_name = raw
            name = ""
        if name.startswith("..."):
            name = name[3:]
        result.append(JavaField(name=name, type_name=_normalize_type(type_name)))
    return result


def _parse_method_detail(detail: str) -> tuple[str | None, list[JavaField]]:
    detail = detail.strip().lstrip(":").strip()
    if not detail or "(" not in detail:
        return _normalize_type(detail) or None, []

    before, _, after = detail.partition("(")
    params_part = after.rsplit(")", 1)[0]
    before = before.strip()

    # jdtls detail can be either "returnType methodName(...)" or just "methodName(...)"
    # The method name is always the last identifier before '('.
    parts = before.rsplit(None, 1)
    if len(parts) == 2:
        return_type = _normalize_type(parts[0]) or None
    else:
        return_type = None
    return return_type, _parse_parameters(params_part)


def _parse_package(text: str) -> str:
    match = _PACKAGE_RE.search(text)
    return match.group(1) if match else ""


def _parse_imports(text: str) -> list[str]:
    return [match.group(1) for match in _IMPORT_RE.finditer(text)]


def _is_type_symbol(symbol: LspSymbolInfo) -> bool:
    return symbol.kind in {5, 10, 11, 23}


def _is_lsp_result_complete_enough(
    lsp_classes: list[JavaClass], static_classes: list[JavaClass]
) -> bool:
    if not lsp_classes:
        return False
    static_by_fqn = {cls.fqn: cls for cls in static_classes}
    if static_by_fqn and {cls.fqn for cls in lsp_classes} != set(static_by_fqn):
        return False
    lsp_members = sum(len(cls.fields) + len(cls.methods) for cls in lsp_classes)
    static_members = sum(len(cls.fields) + len(cls.methods) for cls in static_classes)
    return lsp_members >= static_members


def _is_dto(cls: JavaClass) -> bool:
    # Heuristic: a class with no static methods and no main() is likely a DTO/POJO.
    # This is intentionally conservative — service classes may also lack static methods,
    # and DTO classes may have static factory methods.  The result is "dto-like", not
    # a guarantee.
    upper = cls.name.upper()
    markers = {"DTO", "VO", "BO", "PO", "QO", "MODEL", "ENTITY", "REQUEST", "RESPONSE"}
    if any(marker in upper for marker in markers):
        return True
    if not cls.fields:
        return False
    return not any(method.name == "main" or "static" in method.modifiers for method in cls.methods)


def _is_ignored_path(path: Path) -> bool:
    ignored_parts = {".git", ".voyager", "target", "build", ".gradle", ".idea"}
    return any(part in ignored_parts for part in path.parts)


def _clean_lsp_detail(detail: str) -> str:
    detail = detail.strip().lstrip(":").strip()
    if not detail:
        return ""
    if ":" in detail:
        return _normalize_type(detail.rsplit(":", 1)[1])
    parts = detail.split()
    return _normalize_type(parts[0]) if parts else ""


def _infer_field_type_from_line(text: str, line_number: int, field_name: str) -> str:
    if line_number <= 0:
        return ""
    lines = text.splitlines()
    if line_number > len(lines):
        return ""
    match = _FIELD_RE.match(lines[line_number - 1].strip())
    if match and match.group("name") == field_name:
        return _normalize_type(match.group("type"))
    return ""


def _normalize_type(type_name: str | None) -> str:
    if not type_name:
        return ""
    type_name = re.sub(r"\s+", " ", type_name.strip())
    type_name = type_name.replace(" ...", "...")
    for prefix in ("final ", "var "):
        if type_name.startswith(prefix):
            type_name = type_name[len(prefix) :]
    return type_name.strip()


def _split_modifiers(modifiers: str | None) -> list[str]:
    return [part for part in (modifiers or "").split() if part]


def _drop_generics_prefix(statement: str) -> str:
    if statement.startswith("<") and ">" in statement:
        return statement[statement.find(">") + 1 :].strip()
    return statement


def _split_top_level(text: str, sep: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        elif char == sep and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _find_matching_brace(text: str, open_index: int) -> int | None:
    depth = 0
    clean = _remove_string_literals(_strip_comments_keep_layout(text))
    for index in range(open_index, len(clean)):
        char = clean[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _brace_delta(line: str) -> int:
    return line.count("{") - line.count("}")


def _line_number(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _column_number(text: str, index: int) -> int:
    line_start = text.rfind("\n", 0, index) + 1
    return index - line_start + 1


def _strip_comments_keep_layout(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))

    text = re.sub(r"/\*.*?\*/", replace, text, flags=re.DOTALL)
    text = re.sub(r"//.*", replace, text)
    return text


def _remove_string_literals(text: str) -> str:
    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
    text = re.sub(r"'(?:\\.|[^'\\])*'", "''", text)
    return text
