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


class StorageManager:
    """Manages persistent storage in the .voyager directory."""

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path
        self.voyager_dir = project_path / VOYAGER_DIR
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        """Ensure the .voyager directory exists."""
        self.voyager_dir.mkdir(parents=True, exist_ok=True)
        (self.voyager_dir / CACHE_DIR).mkdir(exist_ok=True)

    def load_graph(self) -> SemanticGraph | None:
        """Load the semantic graph from disk.

        Returns:
            SemanticGraph if it exists and is valid, None otherwise.
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
        """Save the semantic graph to disk."""
        graph_path = self.voyager_dir / GRAPH_FILE
        data = graph.model_dump(mode="json")
        graph_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Saved graph to %s (%d symbols)", graph_path, len(graph.symbols))

    def load_rules_path(self) -> Path:
        """Return the path to the rules file (may not exist)."""
        return self.voyager_dir / RULES_FILE

    def log_operation(self, operation, modified_files: list[str]) -> None:
        """Append an operation to the operations log."""
        log_path = self.voyager_dir / OPERATIONS_LOG
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "operation": operation.model_dump(mode="json") if hasattr(operation, 'model_dump') else str(operation),
            "modified_files": modified_files,
        }

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        logger.info("Logged operation to %s", log_path)

    def invalidate_graph(self) -> None:
        """Remove the cached graph, forcing a rebuild on next access."""
        graph_path = self.voyager_dir / GRAPH_FILE
        if graph_path.exists():
            graph_path.unlink()
            logger.info("Invalidated graph cache")

    def get_cache_dir(self) -> Path:
        """Return the cache directory path."""
        return self.voyager_dir / CACHE_DIR
