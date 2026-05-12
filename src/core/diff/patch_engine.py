"""Unified diff parsing and application utilities."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath


_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


class PatchParseError(ValueError):
    """
    Raised when a unified diff cannot be parsed or applied safely.
    """


@dataclass(frozen=True)
class PatchLine:
    """
    A single line inside a unified diff hunk.
    """

    kind: str
    text: str


@dataclass(frozen=True)
class PatchHunk:
    """
    A parsed unified diff hunk with old/new source coordinates.
    """

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[PatchLine] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedPatchFile:
    """
    A parsed file section from a unified diff.
    """

    old_path: str | None
    new_path: str | None
    hunks: list[PatchHunk] = field(default_factory=list)
    move_only: bool = False

    @property
    def is_new_file(self) -> bool:
        return self.old_path is None and self.new_path is not None

    @property
    def is_deleted_file(self) -> bool:
        return self.new_path is None and self.old_path is not None

    @property
    def is_moved_file(self) -> bool:
        return (
            self.old_path is not None
            and self.new_path is not None
            and self.old_path != self.new_path
        )

    @property
    def target_path(self) -> str:
        path = self.new_path or self.old_path
        if path is None:
            raise PatchParseError("Patch file section has no target path")
        return path


def parse_unified_patch(patch_text: str) -> list[ParsedPatchFile]:
    """
    Parse a unified diff into per-file patch sections.
    """
    lines = patch_text.splitlines(keepends=True)
    files: list[ParsedPatchFile] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        if line.startswith("diff --git "):
            metadata_file, next_index = _parse_git_metadata_file(lines, index)
            if metadata_file is not None:
                files.append(metadata_file)
                index = next_index
                continue
            index += 1
            continue

        if not line.startswith("--- "):
            index += 1
            continue

        old_path = _parse_file_header(line, "--- ")
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise PatchParseError("Unified diff file header is missing +++ line")

        new_path = _parse_file_header(lines[index], "+++ ")
        index += 1

        hunks: list[PatchHunk] = []
        while index < len(lines):
            current = lines[index]
            if current.startswith("--- ") or current.startswith("diff --git "):
                break
            if not current.startswith("@@ "):
                index += 1
                continue

            hunk, index = _parse_hunk(lines, index)
            hunks.append(hunk)

        if not hunks:
            raise PatchParseError(f"Patch for '{new_path or old_path}' contains no hunks")
        files.append(ParsedPatchFile(old_path=old_path, new_path=new_path, hunks=hunks))

    if not files:
        raise PatchParseError("Patch contains no unified diff file sections")
    return files


def apply_parsed_patch(original: str, patch_file: ParsedPatchFile) -> str:
    """
    Apply one parsed file patch to original file content.
    """
    original_lines = original.splitlines(keepends=True)
    result_lines: list[str] = []
    original_index = 0

    for hunk in patch_file.hunks:
        expected_index = max(hunk.old_start - 1, 0)
        if expected_index < original_index:
            raise PatchParseError(f"Overlapping hunk in '{patch_file.target_path}'")
        if expected_index > len(original_lines):
            raise PatchParseError(f"Hunk starts beyond end of '{patch_file.target_path}'")

        result_lines.extend(original_lines[original_index:expected_index])
        original_index = expected_index

        for patch_line in hunk.lines:
            if patch_line.kind == " ":
                _assert_line_matches(original_lines, original_index, patch_line, patch_file)
                result_lines.append(original_lines[original_index])
                original_index += 1
            elif patch_line.kind == "-":
                _assert_line_matches(original_lines, original_index, patch_line, patch_file)
                original_index += 1
            elif patch_line.kind == "+":
                result_lines.append(patch_line.text)
            else:
                raise PatchParseError(f"Unsupported hunk line kind: {patch_line.kind!r}")

    result_lines.extend(original_lines[original_index:])
    return "".join(result_lines)


def normalize_patch_path(raw_path: str) -> str:
    """
    Normalize a patch header path to a project-relative POSIX path.
    """
    path = raw_path.strip()
    if path == "/dev/null":
        raise PatchParseError("/dev/null is not a file path")
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    path = path.replace("\\", "/")

    pure = PurePosixPath(path)
    if pure.is_absolute() or any(part == ".." for part in pure.parts):
        raise PatchParseError(f"Patch path escapes the project root: {raw_path}")
    if not path or path == ".":
        raise PatchParseError("Patch path is empty")
    return pure.as_posix()


def _parse_hunk(lines: list[str], start_index: int) -> tuple[PatchHunk, int]:
    header = lines[start_index]
    match = _HUNK_HEADER_RE.match(header)
    if match is None:
        raise PatchParseError(f"Invalid hunk header: {header.rstrip()}")

    hunk_lines: list[PatchLine] = []
    index = start_index + 1
    while index < len(lines):
        line = lines[index]
        if line.startswith("@@ ") or line.startswith("--- ") or line.startswith("diff --git "):
            break
        if line.startswith("\\"):
            if not hunk_lines:
                raise PatchParseError("No-newline marker has no preceding hunk line")
            previous = hunk_lines[-1]
            hunk_lines[-1] = PatchLine(previous.kind, previous.text.rstrip("\r\n"))
            index += 1
            continue
        if not line:
            raise PatchParseError("Unexpected empty patch parser line")

        kind = line[0]
        if kind not in {" ", "-", "+"}:
            raise PatchParseError(f"Invalid hunk line: {line.rstrip()}")
        hunk_lines.append(PatchLine(kind=kind, text=line[1:]))
        index += 1

    hunk = PatchHunk(
        old_start=int(match.group("old_start")),
        old_count=int(match.group("old_count") or "1"),
        new_start=int(match.group("new_start")),
        new_count=int(match.group("new_count") or "1"),
        lines=hunk_lines,
    )
    _validate_hunk_counts(hunk)
    return hunk, index


def _parse_git_metadata_file(
    lines: list[str], start_index: int
) -> tuple[ParsedPatchFile | None, int]:
    """
    Parse a ``diff --git`` metadata-only file move section when present.
    """
    index = start_index + 1
    section: list[str] = []
    while index < len(lines) and not lines[index].startswith("diff --git "):
        section.append(lines[index])
        if lines[index].startswith("--- "):
            return None, index
        index += 1

    old_path: str | None = None
    new_path: str | None = None
    for line in section:
        if line.startswith("rename from "):
            old_path = normalize_patch_path(line.removeprefix("rename from ").strip())
        elif line.startswith("rename to "):
            new_path = normalize_patch_path(line.removeprefix("rename to ").strip())

    if old_path is None or new_path is None:
        return None, index
    return ParsedPatchFile(old_path=old_path, new_path=new_path, move_only=True), index


def _parse_file_header(line: str, prefix: str) -> str | None:
    raw = line[len(prefix) :].strip()
    path = raw.split("\t", 1)[0].strip()
    if path == "/dev/null":
        return None
    return normalize_patch_path(path)


def _validate_hunk_counts(hunk: PatchHunk) -> None:
    old_count = sum(1 for line in hunk.lines if line.kind in {" ", "-"})
    new_count = sum(1 for line in hunk.lines if line.kind in {" ", "+"})
    if old_count != hunk.old_count:
        raise PatchParseError(
            f"Hunk old line count mismatch: expected {hunk.old_count}, got {old_count}"
        )
    if new_count != hunk.new_count:
        raise PatchParseError(
            f"Hunk new line count mismatch: expected {hunk.new_count}, got {new_count}"
        )


def _assert_line_matches(
    original_lines: list[str],
    original_index: int,
    patch_line: PatchLine,
    patch_file: ParsedPatchFile,
) -> None:
    if original_index >= len(original_lines):
        raise PatchParseError(f"Hunk context exceeds end of '{patch_file.target_path}'")
    actual = original_lines[original_index]
    if _normalize_line_for_compare(actual) != _normalize_line_for_compare(patch_line.text):
        raise PatchParseError(
            f"Hunk context did not match '{patch_file.target_path}' at line {original_index + 1}"
        )


def _normalize_line_for_compare(line: str) -> str:
    return line.replace("\r\n", "\n")
