"""Execution engine for Voyager's patch-first operations."""

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.diff.patch_engine import PatchParseError, parse_unified_patch
from core.engine.errors import EngineError, ErrorType
from core.graph.builder import GraphBuilder
from core.graph.semantic_graph import SemanticGraph
from core.lsp.client import LspPosition, LspTextEdit
from core.lsp.config import Language, get_language_config
from core.operation.models import ApplyResult, Operation, PatchOperation, PlanResult
from core.parser.java_parser import (
    parse_java_project_async,
    parse_java_project_static_with_overrides,
)
from core.rules.validator import RuleValidator
from core.vfs.transaction import (
    VirtualFilePatch,
    VirtualFileSystemTransaction,
    VirtualTransactionResult,
    materialize_snapshot,
)
from storage.manager import StorageManager
from utils.async_helpers import run_async

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FilePatch:
    """
    An in-memory representation of a pending file modification.

    Attributes:
        path: Absolute source path.
        original: File content before modification.
        modified: File content after modification.
        destination: Optional destination path for file moves.
        exists: Whether ``path`` existed before the transaction.
        delete: Whether the final state removes the file.
    """

    path: Path
    original: str
    modified: str
    destination: Path | None = None
    exists: bool = True
    delete: bool = False


class ExecutionEngine:
    """
    Plan and execute patch operations with all-or-nothing semantics.
    """

    def __init__(self, project_path: Path, storage: StorageManager | None = None) -> None:
        """
        Initialize the execution engine.
        """
        self.project_path = project_path.resolve()
        self.storage = storage or StorageManager(self.project_path)
        self.graph: SemanticGraph | None = None
        self.validator = RuleValidator(self.storage.load_rules_path())

    def ensure_graph(self, force_rebuild: bool = False) -> SemanticGraph:
        """
        Load a graph from storage or build one from source.
        """
        return run_async(self.ensure_graph_async(force_rebuild))

    async def ensure_graph_async(self, force_rebuild: bool = False) -> SemanticGraph:
        """
        Async variant of :meth:`ensure_graph`.
        """
        if self.graph is not None and not force_rebuild:
            return self.graph

        graph = None if force_rebuild else self.storage.load_graph()
        if graph is None:
            classes = await parse_java_project_async(self.project_path)
            graph = GraphBuilder(self.project_path).build(classes)
            self.storage.save_graph(graph)

        self.graph = graph
        return graph

    def rebuild_graph_static(
        self,
        file_overrides: dict[Path, str] | None = None,
        deleted_files: set[Path] | None = None,
    ) -> SemanticGraph:
        """
        Rebuild the graph with optional virtual file content.
        """
        classes = parse_java_project_static_with_overrides(
            self.project_path,
            file_overrides or {},
            deleted_files or set(),
        )
        return GraphBuilder(self.project_path).build(classes)

    def set_lsp_client(self, client: object | None) -> None:
        """
        Compatibility hook for ProjectSession; patch validation uses snapshots.
        """

    def plan(self, operation: Operation) -> PlanResult:
        """
        Validate a patch operation and compute affected files.
        """
        return run_async(self.plan_async(operation))

    async def plan_async(self, operation: Operation) -> PlanResult:
        """
        Async variant of :meth:`plan`.
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

        try:
            transaction = await self._build_patch_transaction_async(operation)
            patches = self._file_patches_from_virtual(transaction.patches)
            if not patches:
                raise EngineError(
                    ErrorType.VALIDATION_FAILED,
                    "Patch set produced no file changes",
                    target=operation.description,
                )
        except EngineError as exc:
            return PlanResult(
                operation=operation,
                affected_files=[],
                violations=[exc.to_dict()],
                is_valid=False,
            )

        return PlanResult(
            operation=operation,
            affected_files=sorted(
                (patch.destination or patch.path).relative_to(self.project_path).as_posix()
                for patch in patches
            ),
            violations=[],
            is_valid=True,
        )

    def apply(self, operation: Operation) -> ApplyResult:
        """
        Apply a patch operation through the VFS validation and commit pipeline.
        """
        return run_async(self.apply_async(operation))

    async def apply_async(self, operation: Operation) -> ApplyResult:
        """
        Async variant of :meth:`apply`.
        """
        graph = await self.ensure_graph_async()
        violations = self.validator.validate_pre(graph, operation)
        if violations:
            return ApplyResult(success=False, operation=operation, errors=violations)

        try:
            transaction = await self._build_patch_transaction_async(operation)
            patches = self._file_patches_from_virtual(transaction.patches)
            if not patches:
                raise EngineError(
                    ErrorType.VALIDATION_FAILED,
                    "Operation produced no file changes",
                    target=operation.description,
                )

            new_graph = self.rebuild_graph_static(
                transaction.overrides,
                transaction.deleted_files,
            )
            post_violations = self.validator.validate_post(new_graph, operation)
            if post_violations:
                return ApplyResult(success=False, operation=operation, errors=post_violations)

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
                target=operation.description,
            )
            return ApplyResult(success=False, operation=operation, errors=[error.to_dict()])

    def _build_patches(self, graph: SemanticGraph, operation: Operation) -> list[FilePatch]:
        """
        Compatibility wrapper for tests and programmatic callers.
        """
        return run_async(self._build_patches_async(graph, operation))

    async def _build_patches_async(
        self, graph: SemanticGraph, operation: Operation
    ) -> list[FilePatch]:
        """
        Build final file patches from a patch operation.
        """
        transaction = await self._build_patch_transaction_async(operation)
        return self._file_patches_from_virtual(transaction.patches)

    def _build_patch_operation_patches(self, operation: PatchOperation) -> list[FilePatch]:
        """
        Build file patches by applying a unified diff patch set in memory.
        """
        transaction = self._build_patch_transaction(operation)
        return self._file_patches_from_virtual(transaction.patches)

    def _build_patch_transaction(self, operation: PatchOperation) -> VirtualTransactionResult:
        """
        Build a virtual filesystem transaction from an ordered patch set.
        """
        return run_async(self._build_patch_transaction_async(operation))

    async def _build_patch_transaction_async(
        self, operation: PatchOperation
    ) -> VirtualTransactionResult:
        """
        Build and semantically validate a virtual filesystem transaction.
        """
        try:
            transaction = VirtualFileSystemTransaction(self.project_path)
            for patch_text in operation.patch_texts():
                for patch_file in parse_unified_patch(patch_text):
                    transaction.apply_patch_file(patch_file)
            result = transaction.result()
        except PatchParseError as exc:
            raise EngineError(
                ErrorType.VALIDATION_FAILED,
                str(exc),
                target=operation.description,
            ) from exc

        await self._validate_patch_snapshot_async(result, operation)
        return result

    def _file_patches_from_virtual(self, patches: list[VirtualFilePatch]) -> list[FilePatch]:
        """
        Convert VFS patches to execution engine patches.
        """
        return [
            FilePatch(
                path=patch.path,
                original=patch.original,
                modified=patch.modified,
                destination=patch.destination,
                exists=patch.exists,
                delete=patch.delete,
            )
            for patch in patches
        ]

    async def _validate_patch_snapshot_async(
        self,
        transaction: VirtualTransactionResult,
        operation: PatchOperation,
    ) -> None:
        """
        Validate the virtual transaction using a temporary snapshot under .voyager.
        """
        if get_language_config(Language.JAVA).find_server_command() is None:
            return

        snapshot_path: Path | None = None
        try:
            snapshot_path = materialize_snapshot(
                self.project_path,
                transaction,
                self.storage.get_vfs_snapshot_dir(),
            )
            classes = await parse_java_project_async(snapshot_path)
            GraphBuilder(snapshot_path).build(classes)
        except Exception as exc:
            raise EngineError(
                ErrorType.VALIDATION_FAILED,
                f"LSP snapshot validation failed: {exc}",
                target=operation.description,
                file_path=str(snapshot_path) if snapshot_path else None,
            ) from exc
        finally:
            if snapshot_path is not None:
                shutil.rmtree(snapshot_path, ignore_errors=True)

    def _assert_inside_project(self, path: Path) -> None:
        """
        Ensure a path is inside the project root.
        """
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
                modified.append(write_path.relative_to(self.project_path).as_posix())
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


def apply_lsp_edits(content: str, edits: list[LspTextEdit]) -> str:
    """
    Apply LSP edits to content.
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
    """
    Normalize mixed newlines introduced by LSP edits.
    """
    preferred = "\r\n" if "\r\n" in original else "\n"
    return modified.replace("\r\n", "\n").replace("\r", "\n").replace("\n", preferred)
