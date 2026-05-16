"""Deterministic context-pack construction for Alita runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from storage.manager import StorageManager


@dataclass(frozen=True)
class ContextPack:
    """
    A small, auditable context artifact for one Alita run.
    """

    task: str
    mode: str
    project_path: str
    anchors: list[dict[str, Any]] = field(default_factory=list)
    graph: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "mode": self.mode,
            "project_path": self.project_path,
            "anchors": self.anchors,
            "graph": self.graph,
            "constraints": self.constraints,
        }


def build_context_pack(
    project_path: Path,
    task: str,
    *,
    mode: str = "agent",
    active_file: Path | None = None,
) -> ContextPack:
    """
    Build the first deterministic context pack for an Alita run.

    This intentionally avoids model calls, embeddings, and broad file ingestion.
    V0 records the user task, active file hint, graph summary, and core Voyager
    constraints so a run is debuggable before the ADK runtime is introduced.
    """
    project_path = project_path.resolve()
    anchors: list[dict[str, Any]] = []
    if active_file is not None:
        anchors.append(_active_file_anchor(project_path, active_file))

    graph = StorageManager(project_path).load_graph()
    graph_summary = _graph_summary(graph)

    return ContextPack(
        task=task,
        mode=mode,
        project_path=str(project_path),
        anchors=anchors,
        graph=graph_summary,
        constraints=[
            "All source writes must go through Voyager patch plan/apply.",
            "Alita MVP stops at plan; apply is not performed by alita run yet.",
            "Patch input must be Git-style unified diff text.",
        ],
    )


def _active_file_anchor(project_path: Path, active_file: Path) -> dict[str, Any]:
    path = active_file.resolve()
    try:
        display_path = path.relative_to(project_path).as_posix()
    except ValueError:
        display_path = path.as_posix()
    return {
        "type": "active_file",
        "path": display_path,
        "reason": "User active file from IDE context.",
    }


def _graph_summary(graph: object | None) -> dict[str, Any]:
    if graph is None:
        return {"exists": False, "classes": 0, "fields": 0, "methods": 0, "references": 0}

    symbols = getattr(graph, "symbols", [])
    class_symbols = [symbol for symbol in symbols if symbol.type.value == "class"]
    field_symbols = [symbol for symbol in symbols if symbol.type.value == "field"]
    method_symbols = [symbol for symbol in symbols if symbol.type.value == "method"]
    return {
        "exists": True,
        "classes": len(class_symbols),
        "fields": len(field_symbols),
        "methods": len(method_symbols),
        "references": len(getattr(graph, "references", [])),
        "sample_classes": [symbol.id for symbol in class_symbols[:20]],
    }
