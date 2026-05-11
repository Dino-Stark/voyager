"""Execution engine for Voyager's semantic operations."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.engine.errors import (
    EngineError,
    ErrorType,
    LspUnavailableError,
    SymbolNotFoundError,
    UnsupportedOperationError,
)
from core.graph.builder import GraphBuilder
from core.graph.semantic_graph import SemanticGraph, Symbol
from core.lsp.client import LspClient, LspPosition, LspTextEdit, uri_to_path, LspWorkspaceEdit
from core.lsp.config import Language, get_language_config
from core.operation.models import (
    AddFieldOperation,
    ApplyResult,
    Operation,
    PatchOperation,
    PlanResult,
    RemoveFieldOperation,
    RenameClassOperation,
    RenameFieldOperation,
    RenameMethodOperation,
)
from core.parser.java_parser import (
    parse_java_project,
    parse_java_project_async,
    parse_java_project_static_with_overrides,
)
from core.rules.validator import RuleValidator
from core.diff.patch_engine import (
    ParsedPatchFile,
    PatchParseError,
    apply_parsed_patch,
    parse_unified_patch,
)
from storage.manager import StorageManager
from utils.async_helpers import run_async

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FilePatch:
    """
    An in-memory representation of a pending file modification.

    Captures the original and modified content so the engine can apply the
    changes atomically and roll back on failure.

    Attributes:
        path: Absolute path to the file being modified.
        original: File content before modification.
        modified: File content after modification.
    """

    # NOTE: FilePatch stores full original/modified content rather than line-level diffs.
    # This avoids ambiguity when the same line pattern appears multiple times in a file
    # (e.g. `for (int i = 0; i < 10; i++)` in multiple loops).
    # I suppose we need to capture the line number and the code.

    path: Path
    original: str
    modified: str
    destination: Path | None = None
    exists: bool = True
    delete: bool = False


class ExecutionEngine:
    """
    Plan and execute operations with all-or-nothing semantics.

    This is the central orchestrator for Voyager's semantic operations.  The fixed
    V1 pipeline is:

    1. :meth:`plan` -- validate pre-conditions against the current graph
    2. :meth:`apply` -- build patches, re-validate, commit

    Rename requires JDT LS and goes through the full pipeline. Field add/remove
    operations use conservative static source patches and the same validation
    and commit stages.
    """

    def __init__(self, project_path: Path, storage: StorageManager | None = None) -> None:
        """
        Initialize the execution engine.

        Args:
            project_path: Root directory of the Java project to operate on.
            storage: Optional storage manager for persisting graphs and rules.
                     Defaults to a new StorageManager scoped to project_path.
        """
        self.project_path = project_path.resolve()
        self.storage = storage or StorageManager(self.project_path)
        self.graph: SemanticGraph | None = None
        self.validator = RuleValidator(self.storage.load_rules_path())
        self._lsp_client: LspClient | None = None

    def ensure_graph(self, force_rebuild: bool = False) -> SemanticGraph:
        """
        Load a graph from storage or build one from source.

        Args:
            force_rebuild: If True, discard any cached graph and rebuild from source.
                           If False (default), return the cached graph if available.

        Returns:
            The semantic graph for the project.
        """
        return run_async(self.ensure_graph_async(force_rebuild))

    async def ensure_graph_async(self, force_rebuild: bool = False) -> SemanticGraph:
        """
        Async variant of :meth:`ensure_graph` for long-lived server sessions.
        """
        if self.graph is not None and not force_rebuild:
            return self.graph

        graph = None if force_rebuild else self.storage.load_graph()
        if graph is None:
            classes = await self._parse_project_async()
            graph = GraphBuilder(self.project_path).build(classes)
            self.storage.save_graph(graph)

        self.graph = graph
        return graph

    # rebuild_graph_static uses the static parser (not LSP) for speed during
    # post-validation: LSP re-initialization is too expensive for a dry-run check.
    def rebuild_graph_static(self, file_overrides: dict[Path, str] | None = None) -> SemanticGraph:
        """
        Rebuild the graph with optional in-memory file content.

        This avoids LSP re-initialization overhead during post-validation.

        Args:
            file_overrides: Mapping of file paths to their content, used to simulate
                           uncommitted changes without touching the filesystem.

        Returns:
            A fresh semantic graph reflecting the given file overrides.
        """
        classes = parse_java_project_static_with_overrides(self.project_path, file_overrides or {})
        return GraphBuilder(self.project_path).build(classes)

    def set_lsp_client(self, client: LspClient | None) -> None:
        """
        Install a reusable LSP client for long-lived sessions.

        When set, the engine will reuse the existing JDT LS process for parse and
        rename operations instead of spawning a fresh server for every command.
        """
        self._lsp_client = client

    def plan(self, operation: Operation) -> PlanResult:
        """
        Validate an operation and compute the likely affected files.

        Args:
            operation: The semantic operation to validate (e.g. RenameFieldOp).

        Returns:
            A PlanResult indicating whether the operation is valid and which
            files are expected to be affected.
        """
        return run_async(self.plan_async(operation))

    async def plan_async(self, operation: Operation) -> PlanResult:
        """
        Async variant of :meth:`plan` for server usage.
        """
        graph = await self.ensure_graph_async()
        violations = self.validator.validate_pre(graph, operation)
        if violations:
            return PlanResult(
                operation=operation,
                affected_files=[],
                violations=violations,
                is_valid=False,
            )

        return PlanResult(
            operation=operation,
            affected_files=self._compute_affected_files(graph, operation),
            violations=[],
            is_valid=True,
        )

    def apply(self, operation: Operation) -> ApplyResult:
        """
        Apply an operation through the fixed V1 pipeline.

        The pipeline validates pre-conditions, builds file patches, re-validates
        against the patched graph, then commits the changes atomically.

        Args:
            operation: The semantic operation to execute (e.g. RenameFieldOp).

        Returns:
            An ApplyResult indicating success or failure, including any
            modified files or validation errors.
        """
        return run_async(self.apply_async(operation))

    async def apply_async(self, operation: Operation) -> ApplyResult:
        """
        Async variant of :meth:`apply` for server usage.
        """
        graph = await self.ensure_graph_async()
        violations = self.validator.validate_pre(graph, operation)
        if violations:
            return ApplyResult(success=False, operation=operation, errors=violations)

        try:
            patches = await self._build_patches_async(graph, operation)
            if not patches:
                raise EngineError(
                    ErrorType.VALIDATION_FAILED,
                    "Operation produced no file changes",
                    target=_operation_target(operation),
                )

            overrides = {patch.path: patch.modified for patch in patches}
            new_graph = self.rebuild_graph_static(overrides)

            post_violations = self.validator.validate_post(new_graph, operation)
            if post_violations:
                return ApplyResult(
                    success=False,
                    operation=operation,
                    errors=post_violations,
                )

            modified_files = self._commit(patches)
            if any(patch.destination is not None or patch.delete for patch in patches):
                new_graph = self.rebuild_graph_static()
            self.graph = new_graph
            self.storage.save_graph(new_graph)
            self.storage.log_operation(operation, modified_files)

            return ApplyResult(
                success=True,
                operation=operation,
                modified_files=modified_files,
            )
        except EngineError as exc:
            logger.error("Apply failed: %s", exc)
            return ApplyResult(success=False, operation=operation, errors=[exc.to_dict()])
        except Exception as exc:
            logger.exception("Unexpected apply failure")
            error = EngineError(
                ErrorType.INTERNAL_ERROR,
                str(exc),
                target=_operation_target(operation),
            )
            return ApplyResult(success=False, operation=operation, errors=[error.to_dict()])

    def _build_patches(self, graph: SemanticGraph, operation: Operation) -> list[FilePatch]:
        return run_async(self._build_patches_async(graph, operation))

    async def _build_patches_async(
        self, graph: SemanticGraph, operation: Operation
    ) -> list[FilePatch]:
        if isinstance(operation, RenameFieldOperation):
            return await self._build_rename_patches_async(graph, operation)
        if isinstance(operation, RenameMethodOperation):
            return await self._build_rename_method_patches_async(graph, operation)
        if isinstance(operation, RenameClassOperation):
            return await self._build_rename_class_patches_async(graph, operation)
        if isinstance(operation, AddFieldOperation):
            return self._build_add_field_patches(graph, operation)
        if isinstance(operation, RemoveFieldOperation):
            return self._build_remove_field_patches(graph, operation)
        if isinstance(operation, PatchOperation):
            return self._build_patch_operation_patches(operation)
        raise UnsupportedOperationError(f"Unsupported operation: {operation}")

    def _build_rename_patches(
        self, graph: SemanticGraph, operation: RenameFieldOperation
    ) -> list[FilePatch]:
        return run_async(self._build_rename_patches_async(graph, operation))

    async def _build_rename_patches_async(
        self, graph: SemanticGraph, operation: RenameFieldOperation
    ) -> list[FilePatch]:
        field_symbol: Symbol = graph.resolve_field(operation.class_name, operation.field_name)
        if field_symbol is None:
            raise SymbolNotFoundError(operation.target)

        source_path: Path = self._project_file_path(field_symbol.file_path)
        if not source_path.exists():
            raise SymbolNotFoundError(operation.target, file_path=str(source_path))
        if field_symbol.line <= 0 or field_symbol.column <= 0:
            raise EngineError(
                ErrorType.VALIDATION_FAILED,
                "Target field has no source position; run scan again",
                target=operation.target,
                file_path=str(source_path),
            )

        self._ensure_lsp_rename_available(operation, "rename_field")
        return await self._build_lsp_rename_patches(
            source_path, field_symbol, operation, self._lsp_client
        )

    async def _build_rename_method_patches_async(
        self, graph: SemanticGraph, operation: RenameMethodOperation
    ) -> list[FilePatch]:
        method_symbol: Symbol = graph.resolve_method(operation.class_name, operation.method_name)
        if method_symbol is None:
            raise SymbolNotFoundError(operation.target)

        source_path = self._project_file_path(method_symbol.file_path)
        if not source_path.exists():
            raise SymbolNotFoundError(operation.target, file_path=str(source_path))
        if method_symbol.line <= 0 or method_symbol.column <= 0:
            raise EngineError(
                ErrorType.VALIDATION_FAILED,
                "Target method has no source position; run scan again",
                target=operation.target,
                file_path=str(source_path),
            )

        self._ensure_lsp_rename_available(operation, "rename_method")
        return await self._build_lsp_rename_patches(
            source_path, method_symbol, operation, self._lsp_client
        )

    async def _build_rename_class_patches_async(
        self, graph: SemanticGraph, operation: RenameClassOperation
    ) -> list[FilePatch]:
        class_symbol: Symbol = graph.resolve_class(operation.class_name)
        if class_symbol is None:
            raise SymbolNotFoundError(operation.target)

        source_path = self._project_file_path(class_symbol.file_path)
        if not source_path.exists():
            raise SymbolNotFoundError(operation.target, file_path=str(source_path))
        if class_symbol.line <= 0 or class_symbol.column <= 0:
            raise EngineError(
                ErrorType.VALIDATION_FAILED,
                "Target class has no source position; run scan again",
                target=operation.target,
                file_path=str(source_path),
            )

        self._ensure_lsp_rename_available(operation, "rename_class")
        patches = await self._build_lsp_rename_patches(
            source_path, class_symbol, operation, self._lsp_client
        )

        old_simple_name = class_symbol.name
        if source_path.name != f"{old_simple_name}.java":
            return patches

        destination = source_path.with_name(f"{operation.to}.java")
        if destination == source_path:
            return patches
        self._assert_inside_project(destination)
        if destination.exists():
            raise EngineError(
                ErrorType.WRITE_ERROR,
                "Refusing to overwrite an existing Java file during class rename",
                target=operation.target,
                file_path=str(destination),
            )

        return [
            FilePatch(
                path=patch.path,
                original=patch.original,
                modified=patch.modified,
                destination=destination if patch.path == source_path else patch.destination,
            )
            for patch in patches
        ]

    def _build_add_field_patches(
        self, graph: SemanticGraph, operation: AddFieldOperation
    ) -> list[FilePatch]:
        """
        Build a source patch that adds a private field plus JavaBean accessors.

        Add/remove operations are static source edits in V1. They still reuse the
        same engine transaction, graph rebuild, post-validation, and commit path
        as LSP-backed rename operations.
        """
        class_symbol = graph.resolve_class(operation.class_name)
        if class_symbol is None:
            raise SymbolNotFoundError(operation.class_name)

        source_path = self._project_file_path(class_symbol.file_path)
        if not source_path.exists():
            raise SymbolNotFoundError(operation.class_name, file_path=str(source_path))

        original = source_path.read_text(encoding="utf-8")
        body_range = _find_class_body_range(original, class_symbol.name)
        if body_range is None:
            raise EngineError(
                ErrorType.VALIDATION_FAILED,
                "Could not locate target class body",
                target=operation.class_name,
                file_path=str(source_path),
            )

        line_sep = _line_separator(original)
        member_indent = _detect_member_indent(original, body_range.start, body_range.end)
        field_insert = _field_insert_offset(original, graph, class_symbol.id, body_range)
        accessor_insert = _accessor_insert_offset(original, body_range)
        field_text = _build_field_declaration(operation, member_indent, line_sep)
        accessor_text = _build_accessor_methods(operation, member_indent, line_sep)

        modified = original
        modified = modified[:accessor_insert] + accessor_text + modified[accessor_insert:]
        modified = modified[:field_insert] + field_text + modified[field_insert:]

        return [FilePatch(path=source_path, original=original, modified=modified)]

    def _build_remove_field_patches(
        self, graph: SemanticGraph, operation: RemoveFieldOperation
    ) -> list[FilePatch]:
        """
        Build a source patch that removes a field and its JavaBean accessors.
        """
        field_symbol = graph.resolve_field(operation.class_name, operation.field_name)
        if field_symbol is None:
            raise SymbolNotFoundError(f"{operation.class_name}.{operation.field_name}")

        source_path = self._project_file_path(field_symbol.file_path)
        if not source_path.exists():
            raise SymbolNotFoundError(operation.target, file_path=str(source_path))

        original = source_path.read_text(encoding="utf-8")
        ranges: list[SourceRange] = []
        field_range = _field_declaration_range(original, field_symbol)
        if field_range is None:
            raise EngineError(
                ErrorType.VALIDATION_FAILED,
                "Could not locate target field declaration",
                target=f"{operation.class_name}.{operation.field_name}",
                file_path=str(source_path),
            )
        ranges.append(field_range)

        for method_symbol in _bean_accessor_symbols(graph, field_symbol):
            method_range = _method_declaration_range(original, method_symbol)
            if method_range is not None:
                ranges.append(method_range)

        modified = _remove_source_ranges(original, ranges)
        if original == modified:
            raise EngineError(
                ErrorType.VALIDATION_FAILED,
                "remove_field produced no source changes",
                target=f"{operation.class_name}.{operation.field_name}",
            )
        return [FilePatch(path=source_path, original=original, modified=modified)]

    def _build_patch_operation_patches(self, operation: PatchOperation) -> list[FilePatch]:
        """
        Build file patches from a unified diff operation.
        """
        try:
            parsed_files = parse_unified_patch(operation.patch)
        except PatchParseError as exc:
            raise EngineError(
                ErrorType.VALIDATION_FAILED,
                str(exc),
                target=operation.description,
            ) from exc

        patches: list[FilePatch] = []
        for patch_file in parsed_files:
            patches.append(self._build_single_unified_diff_patch(patch_file, operation))
        return patches

    def _build_single_unified_diff_patch(
        self, patch_file: ParsedPatchFile, operation: PatchOperation
    ) -> FilePatch:
        """
        Convert one parsed unified diff file section to a FilePatch.
        """
        target_path = (self.project_path / patch_file.target_path).resolve()
        self._assert_inside_project(target_path)

        if patch_file.is_new_file:
            if target_path.exists():
                raise EngineError(
                    ErrorType.WRITE_ERROR,
                    "Patch creates a file that already exists",
                    target=operation.description,
                    file_path=str(target_path),
                )
            original = ""
            exists = False
        else:
            if not target_path.exists():
                raise EngineError(
                    ErrorType.WRITE_ERROR,
                    "Patch targets a missing file",
                    target=operation.description,
                    file_path=str(target_path),
                )
            original = target_path.read_text(encoding="utf-8")
            exists = True

        try:
            modified = apply_parsed_patch(original, patch_file)
        except PatchParseError as exc:
            raise EngineError(
                ErrorType.VALIDATION_FAILED,
                str(exc),
                target=operation.description,
                file_path=str(target_path),
            ) from exc

        if patch_file.is_deleted_file and modified:
            raise EngineError(
                ErrorType.VALIDATION_FAILED,
                "Deleted-file patch did not remove all file content",
                target=operation.description,
                file_path=str(target_path),
            )

        return FilePatch(
            path=target_path,
            original=original,
            modified=modified,
            exists=exists,
            delete=patch_file.is_deleted_file,
        )

    def _ensure_lsp_rename_available(self, operation: Operation, operation_name: str) -> None:
        # The language is hardcoded to Java.  Multi-language support requires:
        # 1. Detecting/persisting the project language(s) during scan.
        # 2. Looking up the per-file LSP config from the symbol's file extension.
        if get_language_config(Language.JAVA).find_server_command() is None:
            raise LspUnavailableError(
                f"{operation_name} requires jdtls on PATH so Voyager can use LSP semantic rename",
                target=_operation_target(operation),
            )

    async def _build_lsp_rename_patches(
        self,
        source_path: Path,
        symbol: Symbol,
        operation: RenameFieldOperation | RenameMethodOperation | RenameClassOperation,
        client: LspClient | None,
    ) -> list[FilePatch]:
        workspace_edit = await self._request_lsp_rename(
            source_path, symbol, operation, client
        )
        if workspace_edit.is_empty:
            raise EngineError(
                ErrorType.VALIDATION_FAILED,
                "LSP returned an empty rename edit",
                target=operation.target,
            )

        patches: list[FilePatch] = []
        for uri, edits in workspace_edit.changes.items():
            path = uri_to_path(uri).resolve()
            self._assert_inside_project(path)
            if not path.exists():
                raise EngineError(
                    ErrorType.WRITE_ERROR,
                    "LSP returned an edit for a missing file",
                    target=operation.target,
                    file_path=str(path),
                )
            original = path.read_text(encoding="utf-8")
            modified = apply_lsp_edits(original, edits)
            modified = _normalize_newlines(modified, original)
            if original != modified:
                patches.append(FilePatch(path=path, original=original, modified=modified))

        return patches

    async def _request_lsp_rename(
        self,
        source_path: Path,
        symbol: Any,
        operation: RenameFieldOperation | RenameMethodOperation | RenameClassOperation,
        client: LspClient | None = None,
    ) -> LspWorkspaceEdit:
        if client is not None:
            return await self._request_lsp_rename_with_client(source_path, symbol, operation, client)

        async with LspClient(Language.JAVA, self.project_path) as client:
            return await self._request_lsp_rename_with_client(source_path, symbol, operation, client)

    async def _request_lsp_rename_with_client(
        self,
        source_path: Path,
        symbol: Any,
        operation: RenameFieldOperation | RenameMethodOperation | RenameClassOperation,
        client: LspClient,
    ) -> LspWorkspaceEdit:
        # Voyager's Symbol uses 1-based line/column (human-readable), but LSP uses
        # 0-based positions internally, so we subtract 1 for the protocol conversion.
        position = LspPosition(
            line=symbol.line - 1,
            character=symbol.column - 1,
        )
        rename_range = await client.prepare_rename(source_path, position)
        if rename_range is None:
            raise EngineError(
                ErrorType.VALIDATION_FAILED,
                "LSP rejected this location for rename",
                target=operation.target,
                file_path=str(source_path),
            )
        return await client.rename_symbol(source_path, position, operation.to)

    def _compute_affected_files(self, graph: SemanticGraph, operation: Operation) -> list[str]:
        if isinstance(operation, RenameFieldOperation):
            return graph.get_affected_files_for_field(operation.class_name, operation.field_name)
        if isinstance(operation, RenameMethodOperation):
            return graph.get_affected_files_for_method(operation.class_name, operation.method_name)
        if isinstance(operation, RenameClassOperation):
            return graph.get_affected_files_for_class(operation.class_name)
        if isinstance(operation, AddFieldOperation):
            symbol = graph.resolve_class(operation.class_name)
            return [symbol.file_path] if symbol else []
        if isinstance(operation, RemoveFieldOperation):
            field = graph.resolve_field(operation.class_name, operation.field_name)
            return [field.file_path] if field else []
        if isinstance(operation, PatchOperation):
            try:
                return sorted(file.target_path for file in parse_unified_patch(operation.patch))
            except PatchParseError:
                return []
        return []

    def _project_file_path(self, file_path: str) -> Path:
        path: Path = Path(file_path)
        if path.is_absolute():
            return path.resolve()
        return (self.project_path / path).resolve()

    def _assert_inside_project(self, path: Path) -> None:
        try:
            path.resolve().relative_to(self.project_path)
        except ValueError as exc:
            raise EngineError(
                ErrorType.WRITE_ERROR,
                "Refusing to write outside the project root",
                file_path=str(path),
            ) from exc

    def _commit(self, patches: list[FilePatch]) -> list[str]:
        """
        Write all patches, rolling back if any write fails.

        Args:
            patches: List of FilePatch objects describing the changes to apply.

        Returns:
            List of relative paths for all successfully modified files.

        Raises:
            EngineError: If a write fails and rollback cannot fully restore state.
        """
        backups = {patch.path: (patch.exists, patch.original) for patch in patches}
        modified: list[str] = []
        moved: list[tuple[Path, Path]] = []

        try:
            for patch in patches:
                self._assert_inside_project(patch.path)
                write_path = patch.destination or patch.path
                self._assert_inside_project(write_path)
                if patch.delete:
                    if patch.path.exists():
                        patch.path.unlink()
                else:
                    write_path.parent.mkdir(parents=True, exist_ok=True)
                    write_path.write_text(patch.modified, encoding="utf-8")
                if patch.destination is not None:
                    moved.append((patch.path, patch.destination))
                    patch.path.unlink()
                modified.append(str(write_path.relative_to(self.project_path)))
            return modified
        except Exception as exc:
            for original_path, destination in reversed(moved):
                try:
                    if destination.exists():
                        destination.unlink()
                    original_path.write_text(backups[original_path][1], encoding="utf-8")
                except Exception as rollback_exc:
                    logger.error("Rollback failed for moved file %s: %s", original_path, rollback_exc)
            for path, (existed, original) in backups.items():
                try:
                    if existed:
                        path.write_text(original, encoding="utf-8")
                    elif path.exists():
                        path.unlink()
                except Exception as rollback_exc:
                    logger.error("Rollback failed for %s: %s", path, rollback_exc)
            if isinstance(exc, EngineError):
                raise
            raise EngineError(ErrorType.WRITE_ERROR, f"Write failed and was rolled back: {exc}")

    async def _parse_project_async(self) -> list[Any]:
        return await parse_java_project_async(
            self.project_path,
            lsp_client=self._lsp_client,
        )

    def _parse_project(self) -> list[Any]:
        return run_async(self._parse_project_async())


def apply_lsp_edits(content: str, edits: list[LspTextEdit]) -> str:
    """
    Apply LSP edits to content.

    Edits are applied from the end of the file to the beginning so ranges remain
    valid.  Offsets are computed against UTF-16 code units as specified by LSP.
    For ASCII/typical Java source this is identical to Python character offsets;
    the helper still handles non-ASCII by measuring UTF-16 units.
    """
    line_offsets = _line_offsets(content)

    def offset_for(position: LspPosition) -> int:
        if position.line >= len(line_offsets):
            return len(content)
        line_start = line_offsets[position.line]
        line_end = line_offsets[position.line + 1] if position.line + 1 < len(line_offsets) else len(content)
        line_text = content[line_start:line_end]
        return line_start + _utf16_index_to_py_index(line_text, position.character)

    ordered = sorted(
        edits,
        key=lambda edit: (
            offset_for(edit.range.start),
            offset_for(edit.range.end),
        ),
        reverse=True,
    )

    result = content
    for edit in ordered:
        start = offset_for(edit.range.start)
        end = offset_for(edit.range.end)
        result = result[:start] + edit.new_text + result[end:]
    return result


def _line_offsets(content: str) -> list[int]:
    # Build a list where element N is the byte offset of line N in the content.
    # Line 0 always starts at offset 0. Each subsequent line starts right after
    # the "\n" of the previous line (hence index + 1), which is the first
    # character of the next line.
    offsets = [0]
    for index, char in enumerate(content):
        if char == "\n":
            offsets.append(index + 1)
    return offsets


def _utf16_index_to_py_index(text: str, utf16_index: int) -> int:
    units = 0
    for index, char in enumerate(text):
        if units >= utf16_index:
            return index
        units += len(char.encode("utf-16-le")) // 2
    return len(text)


def _normalize_newlines(modified: str, original: str) -> str:
    """
    Normalize mixed newlines introduced by LSP edits.
    """
    preferred = "\r\n" if "\r\n" in original else "\n"
    return modified.replace("\r\n", "\n").replace("\r", "\n").replace("\n", preferred)


def _operation_target(operation: Operation) -> str | None:
    return getattr(operation, "target", None)


@dataclass(frozen=True)
class SourceRange:
    """
    A half-open source range measured in Python string offsets.
    """

    start: int
    end: int


def _find_class_body_range(text: str, class_name: str) -> SourceRange | None:
    """
    Locate the body range for a top-level Java type by simple class name.
    """
    pattern = re.compile(
        rf"\b(?:class|interface|enum|record)\s+{re.escape(class_name)}\b"
    )
    clean = _mask_comments_and_literals(text)
    match = pattern.search(clean)
    if match is None:
        return None

    brace_index = clean.find("{", match.end())
    if brace_index < 0:
        return None
    close_index = _find_matching_brace_offset(clean, brace_index)
    if close_index is None:
        return None
    return SourceRange(start=brace_index + 1, end=close_index)


def _field_insert_offset(
    text: str,
    graph: SemanticGraph,
    class_id: str,
    body_range: SourceRange,
) -> int:
    """
    Return the preferred insertion offset for a new field declaration.
    """
    fields = [
        symbol
        for symbol in graph.symbols
        if symbol.parent_id == class_id and symbol.type.value == "field"
    ]
    field_ranges = [
        item
        for item in (_field_declaration_range(text, field) for field in fields)
        if item is not None
    ]
    if field_ranges:
        return max(item.end for item in field_ranges)

    newline = text.find("\n", body_range.start)
    if newline >= 0 and newline < body_range.end:
        return newline + 1
    return body_range.start


def _accessor_insert_offset(text: str, body_range: SourceRange) -> int:
    """
    Return the preferred insertion offset for generated accessor methods.
    """
    return body_range.end


def _build_field_declaration(
    operation: AddFieldOperation, member_indent: str, line_sep: str
) -> str:
    """
    Render a private Java field declaration.
    """
    initializer = (
        f" = {operation.default_value.strip()}" if operation.default_value is not None else ""
    )
    return f"{member_indent}private {operation.field_type.strip()} {operation.field_name}{initializer};{line_sep}"


def _build_accessor_methods(
    operation: AddFieldOperation, member_indent: str, line_sep: str
) -> str:
    """
    Render JavaBean getter and setter methods for an added field.
    """
    field_type = operation.field_type.strip()
    field_name = operation.field_name
    suffix = _java_bean_suffix(field_name)
    getter_name = f"is{suffix}" if field_type in {"boolean", "Boolean"} else f"get{suffix}"
    setter_name = f"set{suffix}"
    inner_indent = f"{member_indent}    "

    return (
        f"{line_sep}"
        f"{member_indent}public {field_type} {getter_name}() {{{line_sep}"
        f"{inner_indent}return {field_name};{line_sep}"
        f"{member_indent}}}{line_sep}"
        f"{line_sep}"
        f"{member_indent}public void {setter_name}({field_type} {field_name}) {{{line_sep}"
        f"{inner_indent}this.{field_name} = {field_name};{line_sep}"
        f"{member_indent}}}{line_sep}"
    )


def _field_declaration_range(text: str, field_symbol: Symbol) -> SourceRange | None:
    """
    Locate the source line that declares a field symbol.
    """
    if field_symbol.line <= 0:
        return None
    start = _line_start_offset(text, field_symbol.line)
    end = _next_line_offset(text, start)
    line = text[start:end]
    if not re.search(rf"\b{re.escape(field_symbol.name)}\b", line):
        return None
    return SourceRange(start=start, end=end)


def _method_declaration_range(text: str, method_symbol: Symbol) -> SourceRange | None:
    """
    Locate the source range for a method declaration and body.
    """
    if method_symbol.line <= 0:
        return None
    start = _line_start_offset(text, method_symbol.line)
    clean = _mask_comments_and_literals(text)
    open_brace = clean.find("{", start)
    semicolon = clean.find(";", start)
    if open_brace < 0 and semicolon < 0:
        return None
    if semicolon >= 0 and (open_brace < 0 or semicolon < open_brace):
        return _expand_to_following_blank_line(text, SourceRange(start, semicolon + 1))

    close_brace = _find_matching_brace_offset(clean, open_brace)
    if close_brace is None:
        return None
    return _expand_to_following_blank_line(
        text,
        SourceRange(start=start, end=_next_line_offset(text, close_brace)),
    )


def _bean_accessor_symbols(graph: SemanticGraph, field_symbol: Symbol) -> list[Symbol]:
    """
    Return getter/setter symbols conventionally associated with a field symbol.
    """
    if not field_symbol.parent_id:
        return []
    names = _bean_accessor_names(
        field_symbol.name,
        str(field_symbol.extra.get("type_name", "")),
    )
    return [
        symbol
        for symbol in graph.symbols
        if symbol.parent_id == field_symbol.parent_id
        and symbol.type.value == "method"
        and symbol.name in names
    ]


def _remove_source_ranges(text: str, ranges: list[SourceRange]) -> str:
    """
    Remove non-overlapping source ranges from a string.
    """
    result = text
    ordered = sorted(ranges, key=lambda item: item.start, reverse=True)
    for item in ordered:
        result = result[: item.start] + result[item.end :]
    return result


def _expand_to_following_blank_line(text: str, source_range: SourceRange) -> SourceRange:
    """
    Extend a range through one following blank line when present.
    """
    next_start = source_range.end
    next_end = _next_line_offset(text, next_start)
    if next_start < len(text) and text[next_start:next_end].strip() == "":
        return SourceRange(start=source_range.start, end=next_end)
    return source_range


def _detect_member_indent(text: str, body_start: int, body_end: int) -> str:
    """
    Infer the indentation used for direct class members.
    """
    body = text[body_start:body_end]
    for line in body.splitlines():
        if line.strip():
            return line[: len(line) - len(line.lstrip())]
    return "    "


def _find_matching_brace_offset(text: str, open_index: int) -> int | None:
    """
    Find the closing brace for an opening brace in already-masked Java source.
    """
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _line_start_offset(text: str, one_based_line: int) -> int:
    """
    Return the string offset where a one-based source line begins.
    """
    if one_based_line <= 1:
        return 0
    offset = 0
    for _ in range(one_based_line - 1):
        newline = text.find("\n", offset)
        if newline < 0:
            return len(text)
        offset = newline + 1
    return offset


def _next_line_offset(text: str, offset: int) -> int:
    """
    Return the offset immediately after the current line.
    """
    newline = text.find("\n", offset)
    return len(text) if newline < 0 else newline + 1


def _line_separator(text: str) -> str:
    """
    Preserve the dominant newline style of the source file.
    """
    return "\r\n" if "\r\n" in text else "\n"


def _java_bean_suffix(name: str) -> str:
    """
    Convert a field name to the JavaBean accessor suffix.
    """
    if not name:
        return ""
    if len(name) > 1 and name[0].islower() and name[1].isupper():
        return name
    return name[:1].upper() + name[1:]


def _bean_accessor_names(field_name: str, field_type: str = "") -> set[str]:
    """
    Return conventional accessor names for a field.
    """
    suffix = _java_bean_suffix(field_name)
    getter = f"is{suffix}" if field_type.strip() in {"boolean", "Boolean"} else f"get{suffix}"
    return {getter, f"set{suffix}"}


def _mask_comments_and_literals(text: str) -> str:
    """
    Replace comments and string literals with spaces while preserving offsets.
    """
    def replace(match: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))

    text = re.sub(r"/\*.*?\*/", replace, text, flags=re.DOTALL)
    text = re.sub(r"//.*", replace, text)
    text = re.sub(r'"(?:\\.|[^"\\])*"', replace, text)
    text = re.sub(r"'(?:\\.|[^'\\])*'", replace, text)
    return text
