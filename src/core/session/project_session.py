"""Long-lived project session for semantic operations."""

from pathlib import Path

from core.engine.execution_engine import ExecutionEngine
from core.graph.builder import GraphBuilder
from core.graph.semantic_graph import SymbolType
from core.lsp.client import LspClient
from core.lsp.config import Language, get_language_config
from core.operation.models import ApplyResult, Operation, PlanResult
from core.parser.java_parser import parse_java_project_async
from storage.manager import StorageManager


class ProjectSession:
    """
    Keep project state and LSP process alive across multiple Voyager operations.

    The CLI can still run one command at a time, but the Voyager server owns
    this session so JDT LS is started once and reused for scan/plan/apply.
    """

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path.resolve()
        self.storage = StorageManager(self.project_path)
        self.engine = ExecutionEngine(self.project_path, self.storage)
        self._lsp_client: LspClient | None = None

    async def start(self) -> None:
        if self._lsp_client is not None:
            return
        if get_language_config(Language.JAVA).find_server_command() is None:
            return
        self._lsp_client = LspClient(Language.JAVA, self.project_path)
        await self._lsp_client.start()
        self.engine.set_lsp_client(self._lsp_client)

    async def close(self) -> None:
        client = self._lsp_client
        self._lsp_client = None
        self.engine.set_lsp_client(None)
        if client is not None:
            await client.shutdown()

    async def scan(self) -> dict:
        await self.start()
        classes = await parse_java_project_async(self.project_path, lsp_client=self._lsp_client)
        graph = GraphBuilder(self.project_path).build(classes)
        self.engine.graph = graph
        self.storage.save_graph(graph)

        return {
            "symbols_count": len(graph.symbols),
            "references_count": len(graph.references),
            "classes": [
                {
                    "name": symbol.name,
                    "fields": len(
                        [
                            item
                            for item in graph.symbols
                            if item.parent_id == symbol.id and item.type == SymbolType.FIELD
                        ]
                    ),
                    "methods": len(
                        [
                            item
                            for item in graph.symbols
                            if item.parent_id == symbol.id and item.type == SymbolType.METHOD
                        ]
                    ),
                    "references": len(
                        [ref for ref in graph.references if ref.to_symbol == symbol.id]
                    ),
                }
                for symbol in sorted(
                    graph.symbols_by_type(SymbolType.CLASS),
                    key=lambda item: item.name,
                )
            ],
        }

    async def plan(self, operation: Operation) -> PlanResult:
        await self.start()
        return await self.engine.plan_async(operation)

    async def apply(self, operation: Operation) -> ApplyResult:
        await self.start()
        return await self.engine.apply_async(operation)
