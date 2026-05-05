"""Storage manager.

Manages the .voyager directory for persistent state:
- graph.json: semantic graph
- operations.log: operation history
- rules.yaml: project rules
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from core.graph.semantic_graph import SemanticGraph

logger = logging.getLogger(__name__)

VOYAGER_DIR = ".voyager"
GRAPH_FILE = "graph.json"
OPERATIONS_LOG = "operations.log"
RULES_FILE = "rules.yaml"
CACHE_DIR = "cache"
PENDING_PLAN_FILE = "pending_plan.json"


class StorageManager:
    """
    Manage persistent storage in the ``.voyager`` directory inside a project.

    The ``.voyager`` directory is the sole persistent state location.  All files are
    derived from source code and can be rebuilt from scratch (scan), so the directory
    can be safely deleted or regenerated.

    Responsibilities:
        - Save/load the semantic graph (``.voyager/graph.json``)
        - Persist pending operation plans (``.voyager/pending_plan.json``)
        - Append to the operation log (``.voyager/operations.log``)
        - Locate optional project rules (``.voyager/rules.yaml``)
    """

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path
        self.voyager_dir = project_path / VOYAGER_DIR
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        """
        Ensure the .voyager directory exists.
        """
        self.voyager_dir.mkdir(parents=True, exist_ok=True)
        (self.voyager_dir / CACHE_DIR).mkdir(exist_ok=True)

    def load_graph(self) -> SemanticGraph | None:
        """
        Load the semantic graph from disk.
        """
        graph_path = self.voyager_dir / GRAPH_FILE
        if not graph_path.exists():
            return None

        try:
            data = json.loads(graph_path.read_text(encoding="utf-8"))
            graph = SemanticGraph.model_validate(data)
            graph.build_index()
            logger.info("Loaded graph from %s (%d symbols)", graph_path, len(graph.symbols))
            return graph
        except Exception as e:
            logger.warning("Failed to load graph from %s: %s", graph_path, e)
            return None

    def save_graph(self, graph: SemanticGraph) -> None:
        """
        Save the semantic graph to disk.
        """
        graph_path = self.voyager_dir / GRAPH_FILE
        data = graph.model_dump(mode="json")
        graph_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Saved graph to %s (%d symbols)", graph_path, len(graph.symbols))

    def load_rules_path(self) -> Path:
        """
        Return the path to the rules file (may not exist).
        """
        return self.voyager_dir / RULES_FILE

    def load_pending_plan(self) -> dict | None:
        """
        Load the pending operation plan, if present.

        The plan is persisted to disk so that ``voyager plan`` and ``voyager apply``
        can be invoked as separate CLI commands (different process lifetimes).
        """
        plan_path = self.voyager_dir / PENDING_PLAN_FILE
        if not plan_path.exists():
            return None
        return json.loads(plan_path.read_text(encoding="utf-8"))

    def save_pending_plan(self, operation) -> Path:
        """
        Persist an operation plan for a later apply step.
        """
        plan_path = self.voyager_dir / PENDING_PLAN_FILE
        data = operation.model_dump(mode="json") if hasattr(operation, "model_dump") else operation
        plan_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return plan_path

    def clear_pending_plan(self) -> None:
        """
        Remove the pending plan file.
        """
        (self.voyager_dir / PENDING_PLAN_FILE).unlink(missing_ok=True)

    def log_operation(self, operation, modified_files: list[str]) -> None:
        """
        Append an operation to the operations log.
        """
        log_path = self.voyager_dir / OPERATIONS_LOG
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "operation": operation.model_dump(mode="json") if hasattr(operation, "model_dump") else str(operation),
            "modified_files": modified_files,
        }

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        logger.info("Logged operation to %s", log_path)

    def invalidate_graph(self) -> None:
        """
        Remove the cached graph, forcing a rebuild on next access.
        """
        graph_path = self.voyager_dir / GRAPH_FILE
        if graph_path.exists():
            graph_path.unlink()
            logger.info("Invalidated graph cache")

    def get_cache_dir(self) -> Path:
        """
        Return the cache directory path.
        """
        return self.voyager_dir / CACHE_DIR
