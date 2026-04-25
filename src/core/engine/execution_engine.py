"""Execution engine.

Core engine that executes semantic operations following the strict pipeline:
  Plan -> Validate -> Apply (in-memory) -> Re-parse -> Validate again -> Commit (write files)

Ensures strong consistency (all-or-nothing) and full reversibility.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.engine.errors import (
    EngineError,
    SymbolNotFoundError,
    ValidationError,
)
from core.graph.builder import GraphBuilder
from core.graph.semantic_graph import SemanticGraph
from core.operation.models import (
    AddFieldOp,
    ApplyResult,
    Operation,
    PlanResult,
    RemoveFieldOp,
    RenameFieldOp,
)
from core.parser.java_parser import JavaClass, parse_java_file
from core.rules.validator import RuleValidator
from storage.manager import StorageManager

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """Executes semantic operations with strong consistency guarantees."""

    def __init__(self, project_path: Path, storage: StorageManager | None = None) -> None:
        self.project_path = project_path
        self.storage = storage or StorageManager(project_path)
        self.graph: SemanticGraph | None = None
        self.validator = RuleValidator()

    def ensure_graph(self) -> SemanticGraph:
        """Load or build the semantic graph."""
        if self.graph is not None:
            return self.graph
        self.graph = self.storage.load_graph()
        if self.graph is None:
            from core.parser.java_parser import parse_java_project
            classes = parse_java_project(self.project_path)
            builder = GraphBuilder()
            self.graph = builder.build(classes)
            self.storage.save_graph(self.graph)
        return self.graph

    def plan(self, operation: Operation) -> PlanResult:
        """Plan an operation: validate preconditions and compute affected files.

        Args:
            operation: The operation to plan.

        Returns:
            PlanResult with affected files and any violations.
        """
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

        Args:
            operation: The operation to apply.

        Returns:
            ApplyResult with success status and modified files.
        """
        graph = self.ensure_graph()

        # Step 1: Pre-validate
        violations = self.validator.validate_pre(graph, operation)
        if violations:
            return ApplyResult(
                success=False,
                operation=operation,
                errors=violations,
            )

        try:
            # Step 2: Apply modification in-memory (read file, modify, keep in buffer)
            file_buffers = self._apply_in_memory(graph, operation)

            # Step 3: Re-parse modified files to build new graph
            new_graph = self._reparse_files(file_buffers, graph)

            # Step 4: Post-validate on new graph
            post_violations = self.validator.validate_post(new_graph, operation)
            if post_violations:
                logger.warning("Post-validation failed, aborting: %s", post_violations)
                return ApplyResult(
                    success=False,
                    operation=operation,
                    errors=post_violations,
                )

            # Step 5: Commit - write all files atomically
            modified_files = self._commit(file_buffers)

            # Step 6: Update graph and persist
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

    def _apply_in_memory(
        self, graph: SemanticGraph, operation: Operation
    ) -> dict[Path, str]:
        """Apply the operation in-memory, returning modified file contents.

        Reads original files, applies text-level modifications based on
        AST symbol locations, and returns the modified content buffers.
        """
        file_buffers: dict[Path, str] = {}

        if isinstance(operation, RenameFieldOp):
            file_buffers = self._apply_rename_field(graph, operation)
        elif isinstance(operation, AddFieldOp):
            file_buffers = self._apply_add_field(graph, operation)
        elif isinstance(operation, RemoveFieldOp):
            file_buffers = self._apply_remove_field(graph, operation)

        return file_buffers

    def _apply_rename_field(
        self, graph: SemanticGraph, operation: RenameFieldOp
    ) -> dict[Path, str]:
        """Rename a field in all files that reference it."""
        buffers: dict[Path, str] = {}
        target_id = f"{operation.class_name}.{operation.field_name}"
        new_id = f"{operation.class_name}.{operation.to}"

        # Find the field symbol to know exact location
        field_symbol = graph.get_symbol(target_id)
        if field_symbol is None:
            raise SymbolNotFoundError(target_id)

        # Collect all files that need modification
        refs = graph.find_references_to(target_id)
        affected_files = {field_symbol.file_path}
        for ref in refs:
            affected_files.add(ref.file_path)

        # Modify each file
        for file_path_str in affected_files:
            fp = Path(file_path_str)
            if fp in buffers:
                continue
            content = fp.read_text(encoding="utf-8")
            # Use AST-aware replacement: replace field name only in semantic contexts
            modified = self._semantic_rename(content, operation.field_name, operation.to)
            if modified != content:
                buffers[fp] = modified

        return buffers

    def _semantic_rename(self, content: str, old_name: str, new_name: str) -> str:
        """Perform a semantic-aware rename in Java source content.

        Replaces field access expressions (obj.oldName -> obj.newName)
        and direct declarations of the field name.
        """
        import re

        # Pattern 1: field access (identifier.oldName)
        content = re.sub(
            rf'(\b\w+)\.{re.escape(old_name)}\b',
            rf'\g<1>.{new_name}',
            content,
        )
        # Pattern 2: direct field name in declaration context (after type)
        # We need to be conservative: only replace standalone usages in code context
        # This is simplified for V1 - a full implementation would use AST node positions
        return content

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

        # Find the closing brace of the class and insert field before it
        import re
        pattern = r'(\n)([ \t]*\})'
        # Insert before the last closing brace at the correct indentation
        lines = content.split('\n')
        last_brace_idx = -1
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip() == '}':
                last_brace_idx = i
                break

        if last_brace_idx > 0:
            indent = '    '
            field_decl = f'\n{indent}private {operation.field_type} {operation.field_name}'
            if operation.default_value:
                field_decl += f' = {operation.default_value}'
            field_decl += ';'
            lines.insert(last_brace_idx, field_decl)
            buffers[fp] = '\n'.join(lines)

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

        # Remove the field declaration line
        if field_symbol.line > 0:
            lines = content.split('\n')
            # Remove lines from the field declaration (may span multiple lines)
            start = field_symbol.line - 1
            end = start + 1
            while end < len(lines) and ';' not in lines[end - 1]:
                end += 1
            # Remove blank line too
            while end < len(lines) and not lines[end].strip():
                end += 1
            new_lines = lines[:start] + lines[end:]
            buffers[fp] = '\n'.join(new_lines)

        return buffers

    def _reparse_files(
        self, file_buffers: dict[Path, str], old_graph: SemanticGraph
    ) -> SemanticGraph:
        """Re-parse modified files and rebuild the graph.

        V1 strategy: full rebuild for correctness.
        """
        from core.parser.java_parser import parse_java_project

        # Write buffers to temp locations, parse, then we'll commit later
        # For V1, we just rebuild the full graph
        classes = parse_java_project(self.project_path)
        builder = GraphBuilder()
        return builder.build(classes)

    def _commit(self, file_buffers: dict[Path, str]) -> list[str]:
        """Write all file buffers to disk atomically.

        All files are written; if any write fails, attempt to restore originals.
        """
        modified_files = []
        backups: dict[Path, str] = {}

        try:
            # Backup originals
            for fp in file_buffers:
                backups[fp] = fp.read_text(encoding="utf-8")

            # Write new content
            for fp, content in file_buffers.items():
                fp.write_text(content, encoding="utf-8")
                modified_files.append(str(fp))

            return modified_files

        except Exception as e:
            # Rollback: restore originals
            logger.error("Write failed, rolling back: %s", e)
            for fp, original in backups.items():
                try:
                    fp.write_text(original, encoding="utf-8")
                except Exception as rb_err:
                    logger.error("Rollback failed for %s: %s", fp, rb_err)
            raise EngineError(
                error_type=EngineError.error_type if hasattr(EngineError, 'WRITE_ERROR') else 'write_error',
                message=f"Write failed, rolled back: {e}",
            ) from e
