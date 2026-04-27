"""Execution engine for Voyager's semantic operations."""

from __future__ import annotations

import asyncio
import logging
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
from core.graph.semantic_graph import SemanticGraph
from core.lsp.client import LspClient, LspPosition, LspTextEdit, uri_to_path
from core.lsp.config import Language, get_language_config
from core.operation.models import (
    AddFieldOp,
    ApplyResult,
    Operation,
    PlanResult,
    RemoveFieldOp,
    RenameFieldOp,
)
from core.parser.java_parser import parse_java_project, parse_java_project_static_with_overrides
from core.rules.validator import RuleValidator
from storage.manager import StorageManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FilePatch:
    path: Path
    original: str
    modified: str


class ExecutionEngine:
    """Plan and execute operations with all-or-nothing semantics."""

    def __init__(self, project_path: Path, storage: StorageManager | None = None) -> None:
        self.project_path = project_path.resolve()
        self.storage = storage or StorageManager(self.project_path)
        self.graph: SemanticGraph | None = None
        self.validator = RuleValidator(self.storage.load_rules_path())

    def ensure_graph(self, force_rebuild: bool = False) -> SemanticGraph:
        """Load a graph from storage or build one from source."""

        if self.graph is not None and not force_rebuild:
            return self.graph

        graph = None if force_rebuild else self.storage.load_graph()
        if graph is None:
            classes = parse_java_project(self.project_path)
            graph = GraphBuilder(self.project_path).build(classes)
            self.storage.save_graph(graph)

        self.graph = graph
        return graph

    def rebuild_graph_static(self, file_overrides: dict[Path, str] | None = None) -> SemanticGraph:
        """Rebuild the graph with optional in-memory file content."""

        classes = parse_java_project_static_with_overrides(self.project_path, file_overrides or {})
        return GraphBuilder(self.project_path).build(classes)

    def plan(self, operation: Operation) -> PlanResult:
        """Validate an operation and compute the likely affected files."""

        graph = self.ensure_graph()
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
        """Apply an operation through the fixed V1 pipeline."""

        graph = self.ensure_graph()
        violations = self.validator.validate_pre(graph, operation)
        if violations:
            return ApplyResult(success=False, operation=operation, errors=violations)

        try:
            patches = self._build_patches(graph, operation)
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
        if isinstance(operation, RenameFieldOp):
            return self._build_rename_patches(graph, operation)
        if isinstance(operation, AddFieldOp):
            raise UnsupportedOperationError("add_field is declared but not implemented in V1")
        if isinstance(operation, RemoveFieldOp):
            raise UnsupportedOperationError("remove_field is declared but not implemented in V1")
        raise UnsupportedOperationError(f"Unsupported operation: {operation}")

    def _build_rename_patches(
        self, graph: SemanticGraph, operation: RenameFieldOp
    ) -> list[FilePatch]:
        field_symbol = graph.resolve_field(operation.class_name, operation.field_name)
        if field_symbol is None:
            raise SymbolNotFoundError(operation.target)

        source_path = self._project_file_path(field_symbol.file_path)
        if not source_path.exists():
            raise SymbolNotFoundError(operation.target, file_path=str(source_path))
        if field_symbol.line <= 0 or field_symbol.column <= 0:
            raise EngineError(
                ErrorType.VALIDATION_FAILED,
                "Target field has no source position; run scan again",
                target=operation.target,
                file_path=str(source_path),
            )

        if get_language_config(Language.JAVA).find_server_command() is None:
            raise LspUnavailableError(
                "rename_field requires jdtls on PATH so Voyager can use LSP semantic rename",
                target=operation.target,
            )

        workspace_edit = _run_async(self._request_lsp_rename(source_path, field_symbol, operation))
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
        field_symbol: Any,
        operation: RenameFieldOp,
    ):
        async with LspClient(Language.JAVA, self.project_path) as client:
            position = LspPosition(
                line=field_symbol.line - 1,
                character=field_symbol.column - 1,
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
        if isinstance(operation, RenameFieldOp):
            return graph.get_affected_files_for_field(operation.class_name, operation.field_name)
        if isinstance(operation, AddFieldOp):
            symbol = graph.resolve_class(operation.class_name)
            return [symbol.file_path] if symbol else []
        if isinstance(operation, RemoveFieldOp):
            return graph.get_affected_files_for_field(operation.class_name, operation.field_name)
        return []

    def _project_file_path(self, file_path: str) -> Path:
        path = Path(file_path)
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
        """Write all patches, rolling back if any write fails."""

        backups = {patch.path: patch.original for patch in patches}
        modified: list[str] = []

        try:
            for patch in patches:
                self._assert_inside_project(patch.path)
                patch.path.write_text(patch.modified, encoding="utf-8")
                modified.append(str(patch.path.relative_to(self.project_path)))
            return modified
        except Exception as exc:
            for path, original in backups.items():
                try:
                    path.write_text(original, encoding="utf-8")
                except Exception as rollback_exc:
                    logger.error("Rollback failed for %s: %s", path, rollback_exc)
            if isinstance(exc, EngineError):
                raise
            raise EngineError(ErrorType.WRITE_ERROR, f"Write failed and was rolled back: {exc}")


def apply_lsp_edits(content: str, edits: list[LspTextEdit]) -> str:
    """Apply LSP edits to content.

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
    """Normalize mixed newlines introduced by LSP edits."""

    preferred = "\r\n" if "\r\n" in original else "\n"
    return modified.replace("\r\n", "\n").replace("\r", "\n").replace("\n", preferred)


def _run_async(coro: object) -> Any:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # type: ignore[arg-type]

    if loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return loop.run_until_complete(coro)


def _operation_target(operation: Operation) -> str | None:
    return getattr(operation, "target", None)
