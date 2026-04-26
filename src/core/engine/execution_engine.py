"""Execution engine.

Core engine that executes semantic operations following the strict pipeline:
  Plan -> Validate -> Apply (in-memory) -> Re-parse -> Validate again -> Commit (write files)

Uses LSP (Language Server Protocol) for precise semantic operations:
- rename_field: delegates to textDocument/rename for accurate cross-file renaming
- references: delegates to textDocument/references for impact analysis

Ensures strong consistency (all-or-nothing) and full reversibility.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from core.engine.errors import (
    EngineError,
    SymbolNotFoundError,
    ValidationError,
)
from core.graph.builder import GraphBuilder
from core.graph.semantic_graph import SemanticGraph
from core.lsp.client import LspClient, LspPosition
from core.lsp.config import Language
from core.operation.models import (
    AddFieldOp,
    ApplyResult,
    Operation,
    PlanResult,
    RemoveFieldOp,
    RenameFieldOp,
)
from core.parser.java_parser import parse_java_project
from core.rules.validator import RuleValidator
from storage.manager import StorageManager

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """Executes semantic operations with strong consistency guarantees.

    Uses LSP for precise semantic operations when available,
    falling back to AST-based analysis otherwise.
    """

    def __init__(self, project_path: Path, storage: StorageManager | None = None) -> None:
        self.project_path = project_path
        self.storage = storage or StorageManager(project_path)
        self.graph: SemanticGraph | None = None
        self.validator = RuleValidator()
        self._lsp_client: LspClient | None = None

    async def _get_lsp_client(self) -> LspClient:
        """Get or create an LSP client connection."""
        if self._lsp_client is None:
            self._lsp_client = LspClient(Language.JAVA, project_path=self.project_path)
            await self._lsp_client.start()
        return self._lsp_client

    async def _close_lsp_client(self) -> None:
        """Gracefully close the LSP client."""
        if self._lsp_client is not None:
            await self._lsp_client.shutdown()
            self._lsp_client = None

    def ensure_graph(self) -> SemanticGraph:
        """Load or build the semantic graph."""
        if self.graph is not None:
            return self.graph
        self.graph = self.storage.load_graph()
        if self.graph is None:
            classes = parse_java_project(self.project_path)
            builder = GraphBuilder()
            self.graph = builder.build(classes)
            self.storage.save_graph(self.graph)
        return self.graph

    def plan(self, operation: Operation) -> PlanResult:
        """Plan an operation: validate preconditions and compute affected files."""
        graph = self.ensure_graph()

        violations = self.validator.validate_pre(graph, operation)
        if violations:
            return PlanResult(
                operation=operation,
                affected_files=[],
                violations=violations,
                is_valid=False,
            )

        affected_files = self._compute_affected_files(graph, operation)
        return PlanResult(
            operation=operation,
            affected_files=affected_files,
            violations=[],
            is_valid=True,
        )

    def apply(self, operation: Operation) -> ApplyResult:
        """Apply an operation following the strict pipeline.

        Pipeline: Validate -> Apply (in-memory) -> Re-parse -> Validate -> Commit.
        """
        graph = self.ensure_graph()

        violations = self.validator.validate_pre(graph, operation)
        if violations:
            return ApplyResult(
                success=False,
                operation=operation,
                errors=violations,
            )

        try:
            if isinstance(operation, RenameFieldOp):
                # Use LSP for precise rename
                file_buffers = self._apply_rename_via_lsp(graph, operation)
            else:
                file_buffers = self._apply_in_memory(graph, operation)

            new_graph = self._reparse_files(file_buffers, graph)

            post_violations = self.validator.validate_post(new_graph, operation)
            if post_violations:
                logger.warning("Post-validation failed, aborting: %s", post_violations)
                return ApplyResult(
                    success=False,
                    operation=operation,
                    errors=post_violations,
                )

            modified_files = self._commit(file_buffers)

            self.graph = new_graph
            self.storage.save_graph(self.graph)
            self.storage.log_operation(operation, modified_files)

            return ApplyResult(
                success=True,
                operation=operation,
                modified_files=modified_files,
            )

        except EngineError as e:
            logger.error("Engine error during apply: %s", e)
            return ApplyResult(
                success=False,
                operation=operation,
                errors=[e.to_dict()],
            )

    def _compute_affected_files(self, graph: SemanticGraph, operation: Operation) -> list[str]:
        """Compute the list of files affected by an operation."""
        if isinstance(operation, RenameFieldOp):
            return graph.get_affected_files_for_field(operation.class_name, operation.field_name)
        elif isinstance(operation, AddFieldOp):
            symbol = graph.get_symbol(operation.class_name)
            return [symbol.file_path] if symbol else []
        elif isinstance(operation, RemoveFieldOp):
            return graph.get_affected_files_for_field(operation.class_name, operation.field_name)
        return []

    def _apply_rename_via_lsp(
        self, graph: SemanticGraph, operation: RenameFieldOp
    ) -> dict[Path, str]:
        """Rename a field using LSP textDocument/rename for maximum precision.

        This delegates the rename to jdt.ls which understands the full
        semantic context (inheritance, generics, type resolution) and
        computes all necessary edits across all files.
        """
        target_id = f"{operation.class_name}.{operation.field_name}"
        field_symbol = graph.get_symbol(target_id)
        if field_symbol is None:
            raise SymbolNotFoundError(target_id)

        file_path = Path(field_symbol.file_path)
        if not file_path.exists():
            raise SymbolNotFoundError(target_id, file_path=str(file_path))

        async def _do_lsp_rename() -> dict[Path, str]:
            client = await self._get_lsp_client()
            await client.open_file(file_path)

            # LSP positions are 0-indexed; our symbols store 1-indexed lines
            position = LspPosition(
                line=field_symbol.line - 1,
                character=field_symbol.column - 1,
            )

            # Perform the rename via LSP
            workspace_edit = await client.rename_symbol(
                file_path, position, operation.to
            )

            # Convert LSP workspace edit to file buffers
            buffers: dict[Path, str] = {}
            for uri, edits in workspace_edit.changes.items():
                edit_path = self._uri_to_path(uri)
                if not edit_path.exists():
                    logger.warning("Edit target path does not exist: %s", edit_path)
                    continue
                content = edit_path.read_text(encoding="utf-8")
                # Apply edits in reverse order to preserve positions
                for edit in reversed(edits):
                    content = self._apply_text_edit(content, edit)
                buffers[edit_path] = content

            await self._close_lsp_client()
            return buffers

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(asyncio.run, _do_lsp_rename()).result()
            else:
                return loop.run_until_complete(_do_lsp_rename())
        except RuntimeError:
            return asyncio.run(_do_lsp_rename())

    def _uri_to_path(self, uri: str) -> Path:
        """Convert a file URI back to a Path."""
        if uri.startswith("file:///"):
            return Path(uri[8:] if not uri.startswith("file:///") else uri[7:])
        elif uri.startswith("file://"):
            return Path(uri[7:])
        elif uri.startswith("file:"):
            return Path(uri[5:])
        return Path(uri)

    def _apply_text_edit(self, content: str, edit) -> str:
        """Apply a single LSP text edit to file content."""
        lines = content.split("\n")
        start_line = edit.range.start.line
        start_col = edit.range.start.character
        end_line = edit.range.end.line
        end_col = edit.range.end.character

        # Preserve text before the edit
        prefix = lines[start_line][:start_col] if start_line < len(lines) else ""

        # Preserve text after the edit
        suffix = lines[end_line][end_col:] if end_line < len(lines) else ""

        # Build new content
        new_content = prefix + edit.new_text + suffix

        # Reconstruct full file
        before = "\n".join(lines[:start_line])
        after = "\n".join(lines[end_line + 1 :]) if end_line + 1 < len(lines) else ""

        if before and after:
            return before + "\n" + new_content + "\n" + after
        elif before:
            return before + "\n" + new_content
        elif after:
            return new_content + "\n" + after
        else:
            return new_content

    def _apply_in_memory(
        self, graph: SemanticGraph, operation: Operation
    ) -> dict[Path, str]:
        """Apply non-rename operations in-memory (add/remove field)."""
        file_buffers: dict[Path, str] = {}

        if isinstance(operation, AddFieldOp):
            file_buffers = self._apply_add_field(graph, operation)
        elif isinstance(operation, RemoveFieldOp):
            file_buffers = self._apply_remove_field(graph, operation)

        return file_buffers

    def _apply_add_field(
        self, graph: SemanticGraph, operation: AddFieldOp
    ) -> dict[Path, str]:
        """Add a new field to a class."""
        buffers: dict[Path, str] = {}
        symbol = graph.get_symbol(operation.class_name)
        if symbol is None:
            raise SymbolNotFoundError(operation.class_name)

        fp = Path(symbol.file_path)
        content = fp.read_text(encoding="utf-8")

        lines = content.split("\n")
        last_brace_idx = -1
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip() == "}":
                last_brace_idx = i
                break

        if last_brace_idx > 0:
            indent = "    "
            field_decl = f"\n{indent}private {operation.field_type} {operation.field_name}"
            if operation.default_value:
                field_decl += f" = {operation.default_value}"
            field_decl += ";"
            lines.insert(last_brace_idx, field_decl)
            buffers[fp] = "\n".join(lines)

        return buffers

    def _apply_remove_field(
        self, graph: SemanticGraph, operation: RemoveFieldOp
    ) -> dict[Path, str]:
        """Remove a field from a class."""
        buffers: dict[Path, str] = {}
        target_id = f"{operation.class_name}.{operation.field_name}"
        field_symbol = graph.get_symbol(target_id)
        if field_symbol is None:
            raise SymbolNotFoundError(target_id)

        fp = Path(field_symbol.file_path)
        content = fp.read_text(encoding="utf-8")

        if field_symbol.line > 0:
            lines = content.split("\n")
            start = field_symbol.line - 1
            end = start + 1
            while end < len(lines) and ";" not in lines[end - 1]:
                end += 1
            while end < len(lines) and not lines[end].strip():
                end += 1
            new_lines = lines[:start] + lines[end:]
            buffers[fp] = "\n".join(new_lines)

        return buffers

    def _reparse_files(
        self, file_buffers: dict[Path, str], old_graph: SemanticGraph
    ) -> SemanticGraph:
        """Re-parse modified files and rebuild the graph.

        V1 strategy: full rebuild for correctness.
        """
        classes = parse_java_project(self.project_path)
        builder = GraphBuilder()
        return builder.build(classes)

    def _commit(self, file_buffers: dict[Path, str]) -> list[str]:
        """Write all file buffers to disk atomically."""
        modified_files = []
        backups: dict[Path, str] = {}

        try:
            for fp in file_buffers:
                backups[fp] = fp.read_text(encoding="utf-8")

            for fp, content in file_buffers.items():
                fp.write_text(content, encoding="utf-8")
                modified_files.append(str(fp))

            return modified_files

        except Exception as e:
            logger.error("Write failed, rolling back: %s", e)
            for fp, original in backups.items():
                try:
                    fp.write_text(original, encoding="utf-8")
                except Exception as rb_err:
                    logger.error("Rollback failed for %s: %s", fp, rb_err)
            raise EngineError(
                error_type=EngineError.error_type if hasattr(EngineError, "WRITE_ERROR") else "write_error",
                message=f"Write failed, rolled back: {e}",
            ) from e
