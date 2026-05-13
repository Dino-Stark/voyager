"""Patch-backed virtual filesystem transactions."""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from core.diff.patch_engine import ParsedPatchFile, PatchParseError, apply_parsed_patch


class FilePatchLike(Protocol):
    """
    Structural type for engine file patches produced from a virtual transaction.
    """

    path: Path
    original: str
    modified: str
    destination: Path | None
    exists: bool
    delete: bool


@dataclass
class VirtualFileState:
    """
    A per-file state inside a patch-set virtual filesystem.

    Attributes:
        path: Absolute project file path.
        original: File content before the transaction.
        content: Current virtual content.
        original_exists: Whether the file existed before the transaction.
        current_exists: Whether the file exists in the current virtual view.
        touched: Whether the transaction changed this file state.
    """

    path: Path
    original: str
    content: str
    original_exists: bool
    current_exists: bool
    touched: bool = False
    moved_from: Path | None = None


@dataclass(frozen=True)
class VirtualFilePatch:
    """
    Final file change computed from a virtual transaction.
    """

    path: Path
    original: str
    modified: str
    destination: Path | None = None
    exists: bool = True
    delete: bool = False


@dataclass(frozen=True)
class VirtualTransactionResult:
    """
    Materialized result of applying a patch set to a virtual filesystem.

    Attributes:
        patches: Final atomic file changes.
        overrides: In-memory content for files that exist after the transaction.
        deleted_files: Files that should be absent in the virtual final state.
    """

    patches: list[VirtualFilePatch]
    overrides: dict[Path, str]
    deleted_files: set[Path]


class VirtualFileSystemTransaction:
    """
    Apply ordered patch sections to a project-local virtual filesystem.
    """

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path.resolve()
        self._files: dict[Path, VirtualFileState] = {}
        self._moves: dict[Path, Path] = {}

    def apply_patch_file(self, patch_file: ParsedPatchFile) -> None:
        """
        Apply a parsed file patch to the virtual filesystem.
        """
        if patch_file.is_moved_file:
            self._apply_move_patch(patch_file)
            return

        target_path = self.resolve_path(patch_file.target_path)
        state = self._state_for(target_path)

        if patch_file.is_new_file:
            if state.current_exists:
                raise PatchParseError(f"Patch creates a file that already exists: {patch_file.target_path}")
            base_content = ""
        elif patch_file.is_deleted_file:
            if not state.current_exists:
                raise PatchParseError(f"Patch deletes a missing file: {patch_file.target_path}")
            base_content = state.content
        else:
            if not state.current_exists:
                raise PatchParseError(f"Patch targets a missing file: {patch_file.target_path}")
            base_content = state.content

        modified = apply_parsed_patch(base_content, patch_file)
        if patch_file.is_deleted_file and modified:
            raise PatchParseError(
                f"Deleted-file patch did not remove all file content: {patch_file.target_path}"
            )

        state.content = modified
        state.current_exists = not patch_file.is_deleted_file
        state.touched = True

    def result(self) -> VirtualTransactionResult:
        """
        Return the final virtual transaction state as atomic file patches.
        """
        patches: list[VirtualFilePatch] = []
        overrides: dict[Path, str] = {}
        deleted_files: set[Path] = set()

        moved_sources = set(self._moves)
        for state in self._files.values():
            if state.path in moved_sources:
                continue
            destination = self._moves.get(state.path)
            if not state.touched:
                continue
            if (
                state.original_exists == state.current_exists
                and state.original == state.content
                and destination is None
                and state.moved_from is None
            ):
                continue

            patch = VirtualFilePatch(
                path=state.moved_from or state.path,
                original=state.original,
                modified=state.content,
                destination=state.path if state.moved_from is not None else destination,
                exists=state.original_exists,
                delete=not state.current_exists,
            )
            patches.append(patch)

            if patch.delete:
                deleted_files.add(patch.path)
            elif patch.destination is not None:
                overrides[patch.destination] = patch.modified
                deleted_files.add(patch.path)
            else:
                overrides[patch.path] = patch.modified

        return VirtualTransactionResult(
            patches=patches,
            overrides=overrides,
            deleted_files=deleted_files,
        )

    def resolve_path(self, target_path: str) -> Path:
        """
        Resolve a project-relative path and reject path traversal.
        """
        path = (self.project_path / target_path).resolve()
        try:
            path.relative_to(self.project_path)
        except ValueError as exc:
            raise PatchParseError(f"Patch path escapes the project root: {target_path}") from exc
        return path

    def _apply_move_patch(self, patch_file: ParsedPatchFile) -> None:
        """
        Apply a file move, optionally with content modifications.
        """
        if patch_file.old_path is None or patch_file.new_path is None:
            raise PatchParseError("Move patch must include both old and new paths")

        old_path = self.resolve_path(patch_file.old_path)
        new_path = self.resolve_path(patch_file.new_path)
        old_state = self._state_for(old_path)
        new_state = self._state_for(new_path)

        if not old_state.current_exists:
            raise PatchParseError(f"Patch moves a missing file: {patch_file.old_path}")
        if new_state.current_exists:
            raise PatchParseError(f"Patch moves to an existing file: {patch_file.new_path}")

        moved_content = old_state.content
        if not patch_file.move_only:
            moved_content = apply_parsed_patch(moved_content, patch_file)

        old_state.current_exists = False
        old_state.touched = True
        new_state.content = moved_content
        new_state.current_exists = True
        new_state.touched = True
        new_state.original = old_state.original
        new_state.original_exists = old_state.original_exists
        new_state.moved_from = old_path
        self._moves[old_path] = new_path

    def _state_for(self, path: Path) -> VirtualFileState:
        """
        Load or return a virtual file state for an absolute project path.
        """
        if path in self._files:
            return self._files[path]

        if path.exists():
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                try:
                    display_path = path.relative_to(self.project_path).as_posix()
                except ValueError:
                    display_path = path.as_posix()
                raise PatchParseError(
                    f"Only UTF-8 text files can be patched: {display_path}"
                ) from exc
            state = VirtualFileState(
                path=path,
                original=content,
                content=content,
                original_exists=True,
                current_exists=True,
            )
        else:
            state = VirtualFileState(
                path=path,
                original="",
                content="",
                original_exists=False,
                current_exists=False,
            )
        self._files[path] = state
        return state


def materialize_snapshot(
    project_path: Path,
    transaction: VirtualTransactionResult,
    snapshot_root: Path,
) -> Path:
    """
    Copy the project into ``snapshot_root`` and apply a virtual transaction there.
    """
    project_path = project_path.resolve()
    snapshot_root.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_root / f"patch-{uuid.uuid4().hex}"
    ignore = shutil.ignore_patterns(".git", ".voyager")
    shutil.copytree(project_path, snapshot_path, ignore=ignore)

    for deleted in sorted(transaction.deleted_files, key=lambda item: len(item.parts), reverse=True):
        target = snapshot_path / deleted.relative_to(project_path)
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()

    for path, content in transaction.overrides.items():
        target = snapshot_path / path.relative_to(project_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    return snapshot_path
