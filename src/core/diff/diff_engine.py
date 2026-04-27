"""Diff engine.

Generates structured diffs between original and modified file contents.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FileDiff:
    """Structured diff for a single file."""

    file_path: str
    unified_diff: str
    status: str = "pending"  # pending | applied | rolled_back
    additions: int = 0
    deletions: int = 0


class DiffEngine:
    """Generates diffs between original and modified file contents."""

    def __init__(self) -> None:
        pass

    def diff_file(self, file_path: Path, original: str, modified: str) -> FileDiff:
        """Generate a unified diff for a single file.

        Args:
            file_path: Path to the file.
            original: Original file content.
            modified: Modified file content.

        Returns:
            FileDiff with unified diff string.
        """
        original_lines = original.splitlines(keepends=True)
        modified_lines = modified.splitlines(keepends=True)

        diff = difflib.unified_diff(
            original_lines,
            modified_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            lineterm="",
        )

        diff_text = "".join(diff)
        additions = sum(
            1 for line in modified_lines if not line.startswith("-") and line.strip()
        )
        deletions = sum(
            1 for line in original_lines if not line.startswith("+") and line.strip()
        )

        return FileDiff(
            file_path=str(file_path),
            unified_diff=diff_text,
            additions=additions,
            deletions=deletions,
        )

    def diff_files(self, file_states: dict[Path, tuple[str, str]]) -> list[FileDiff]:
        """Generate diffs for multiple files.

        Args:
            file_states: Mapping of file path to (original_content, modified_content).

        Returns:
            List of FileDiff objects, one per modified file.
        """
        diffs = []
        for fp, (original, modified) in file_states.items():
            if original != modified:
                diffs.append(self.diff_file(fp, original, modified))
        return diffs

    def format_summary(self, diffs: list[FileDiff]) -> str:
        """Format a human-readable summary of all diffs."""
        if not diffs:
            return "No changes."

        lines = []
        total_add = sum(d.additions for d in diffs)
        total_del = sum(d.deletions for d in diffs)

        for d in diffs:
            lines.append(f"  {d.file_path}  (+{d.additions} -{d.deletions})")

        summary = "\n".join(lines)
        summary += f"\n\nTotal: {len(diffs)} file(s) changed, +{total_add} -{total_del}"
        return summary
