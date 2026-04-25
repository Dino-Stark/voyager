"""Java AST parser based on javalang.

Responsible for parsing Java source files and extracting
class, field, and method declarations with type information.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import javalang

logger = logging.getLogger(__name__)


@dataclass
class JavaField:
    """A field declaration extracted from a Java class."""

    name: str
    type_name: str
    modifiers: list[str] = field(default_factory=list)
    line: int = 0
    column: int = 0


@dataclass
class JavaMethod:
    """A method declaration extracted from a Java class."""

    name: str
    return_type: str | None = None
    parameters: list[JavaField] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)
    line: int = 0
    column: int = 0


@dataclass
class JavaClass:
    """A class/interface/enum declaration extracted from a Java file."""

    name: str
    file_path: Path
    package: str = ""
    fields: list[JavaField] = field(default_factory=list)
    methods: list[JavaMethod] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    line: int = 0
    column: int = 0
    is_dto: bool = False


class JavaParseException(Exception):
    """Raised when a Java file cannot be parsed."""


def parse_java_file(file_path: Path) -> JavaClass | None:
    """Parse a single Java file and extract class/field/method info.

    Args:
        file_path: Absolute path to the .java file.

    Returns:
        JavaClass if the file contains a type declaration, None otherwise.

    Raises:
        JavaParseException: If the file cannot be parsed.
    """
    try:
        source = file_path.read_text(encoding="utf-8")
    except Exception as e:
        raise JavaParseException(f"Cannot read file {file_path}: {e}") from e

    try:
        tree = javalang.parse.parse(source)
    except javalang.parser.JavaSyntaxError as e:
        raise JavaParseException(f"Syntax error in {file_path}: {e}") from e

    if not tree.package or not tree.imports or not tree.types:
        logger.debug("Skipping %s: no package/imports/types", file_path)
        return None

    package_name = tree.package.name if tree.package else ""
    import_names = [imp.path for imp in (tree.imports or [])]

    classes: list[JavaClass] = []

    for type_decl in tree.types:
        if not hasattr(type_decl, "name"):
            continue

        fields: list[JavaField] = []
        methods: list[JavaMethod] = []

        for member in getattr(type_decl, "body", []) or []:
            if isinstance(member, javalang.tree.FieldDeclaration):
                for declarator in member.declarators:
                    f = JavaField(
                        name=declarator.name,
                        type_name=_resolve_type(member.type),
                        modifiers=list(member.modifiers or []),
                        line=member.position.line if member.position else 0,
                        column=member.position.column if member.position else 0,
                    )
                    fields.append(f)

            elif isinstance(member, javalang.tree.MethodDeclaration):
                params: list[JavaField] = []
                for param in member.parameters or []:
                    params.append(
                        JavaField(
                            name=param.name,
                            type_name=_resolve_type(param.type),
                            modifiers=list(param.modifiers or []),
                        )
                    )
                m = JavaMethod(
                    name=member.name,
                    return_type=_resolve_type(member.return_type) if member.return_type else None,
                    parameters=params,
                    modifiers=list(member.modifiers or []),
                    line=member.position.line if member.position else 0,
                    column=member.position.column if member.position else 0,
                )
                methods.append(m)

        cls = JavaClass(
            name=type_decl.name,
            file_path=file_path,
            package=package_name,
            fields=fields,
            methods=methods,
            imports=import_names,
            line=type_decl.position.line if type_decl.position else 0,
            column=type_decl.position.column if type_decl.position else 0,
        )
        cls.is_dto = _is_dto(cls)
        classes.append(cls)

    return classes[0] if len(classes) == 1 else None


def parse_java_project(project_path: Path) -> list[JavaClass]:
    """Parse all Java files in a project directory.

    Args:
        project_path: Root path of the Java project.

    Returns:
        List of all JavaClass found in the project.
    """
    java_files = list(project_path.rglob("*.java"))
    classes: list[JavaClass] = []

    for jf in java_files:
        try:
            cls = parse_java_file(jf)
            if cls is not None:
                classes.append(cls)
        except JavaParseException:
            logger.warning("Failed to parse %s, skipping", jf)

    logger.info("Parsed %d classes from %d Java files", len(classes), len(java_files))
    return classes


def _resolve_type(type_node) -> str:
    """Resolve a javalang type node to a simple type name string."""
    if type_node is None:
        return "void"
    if hasattr(type_node, "name"):
        return type_node.name
    if hasattr(type_node, "arguments"):
        base = type_node.name if hasattr(type_node, "name") else str(type_node)
        args = ", ".join(_resolve_type(a.type) for a in (type_node.arguments or []))
        return f"{base}<{args}>"
    return str(type_node)


def _is_dto(cls: JavaClass) -> bool:
    """Heuristic: check if a class looks like a DTO."""
    name_upper = cls.name.upper()
    dto_markers = {"DTO", "VO", "BO", "PO", "QO", "MODEL", "ENTITY", "REQUEST", "RESPONSE"}
    if any(marker in name_upper for marker in dto_markers):
        return True
    if cls.fields and not any(
        m.name == "main" or "static" in m.modifiers for m in cls.methods
    ):
        return True
    return False
