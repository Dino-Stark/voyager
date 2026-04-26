"""Generic LSP client that drives any JSON-RPC based Language Server.

This is the "super client" — it speaks LSP protocol regardless of which
language server is running on the backend (jdt.ls, pyright, clangd, etc.).

Key capabilities exposed as high-level "meta-instructions":
- get_impact_analysis(symbol): find all references to a symbol
- find_definitions(position): go to definition
- find_implementations(class): find interface/class implementations
- rename_symbol(symbol, new_name): perform a semantic rename
- get_diagnostics(file_path): validate code for errors
- get_symbols(file_path): list all symbols in a file
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.lsp.config import Language, LanguageConfig, get_language_config

logger = logging.getLogger(__name__)


@dataclass
class LspPosition:
    """A position in a text document (0-indexed)."""

    line: int
    character: int

    def to_lsp(self) -> dict:
        return {"line": self.line, "character": self.character}


@dataclass
class LspRange:
    """A range in a text document."""

    start: LspPosition
    end: LspPosition

    def to_lsp(self) -> dict:
        return {
            "start": self.start.to_lsp(),
            "end": self.end.to_lsp(),
        }


@dataclass
class LspLocation:
    """A location in a file."""

    uri: str
    range: LspRange


@dataclass
class LspSymbolInfo:
    """Symbol information extracted from LSP."""

    name: str
    kind: int  # LSP SymbolKind
    detail: str = ""
    uri: str = ""
    range: LspRange | None = None
    selection_range: LspRange | None = None
    children: list[LspSymbolInfo] = field(default_factory=list)


@dataclass
class LspTextEdit:
    """A text edit operation from LSP."""

    range: LspRange
    new_text: str


@dataclass
class LspWorkspaceEdit:
    """A workspace edit from LSP (rename results)."""

    changes: dict[str, list[LspTextEdit]] = field(default_factory=dict)


class LspClient:
    """Generic LSP client that communicates with any language server via stdio.

    Usage:
        client = LspClient(Language.JAVA, project_path=Path("/my/project"))
        await client.start()
        symbols = await client.get_symbols(file_path)
        refs = await client.get_references(file_path, position)
        await client.shutdown()
    """

    def __init__(
        self,
        language: Language,
        project_path: Path,
        config: LanguageConfig | None = None,
    ) -> None:
        self.language = language
        self.project_path = project_path
        self.config = config or get_language_config(language)

        self._process: subprocess.Popen | None = None
        self._request_id = 0
        self._initialized = False
        self._root_uri = self._path_to_uri(project_path)
        self._loop: asyncio.AbstractEventLoop | None = None

    def _path_to_uri(self, path: Path) -> str:
        """Convert a file path to a file URI."""
        abs_path = path.resolve()
        if os.name == "nt":
            return f"file:///{abs_path.as_posix().lstrip('/')}"
        return f"file://{abs_path.as_posix()}"

    def _file_to_uri(self, file_path: Path) -> str:
        """Convert a file path to a file URI."""
        return self._path_to_uri(file_path)

    async def start(self) -> None:
        """Start the language server process and perform LSP initialization."""
        cmd = self.config.find_server_command()
        if cmd is None:
            raise RuntimeError(
                f"LSP server for '{self.language.value}' not found. "
                f"Please install: {' '.join(self.config.command)}"
            )

        logger.info("Starting LSP server: %s", " ".join(cmd))

        env = os.environ.copy()
        # Set workspace for jdt.ls
        if self.language == Language.JAVA:
            workspace = self.project_path / ".voyager" / "cache" / "jdtls-workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            env["JDTLS_WORKSPACE"] = str(workspace)

        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(self.project_path),
        )

        # Perform LSP handshake
        await self._send_request("initialize", {
            "processId": os.getpid(),
            "rootUri": self._root_uri,
            "rootPath": str(self.project_path),
            "capabilities": {
                "textDocument": {
                    "definition": {"dynamicRegistration": False},
                    "references": {"dynamicRegistration": False},
                    "implementation": {"dynamicRegistration": False},
                    "rename": {"prepareSupport": True},
                    "documentSymbol": {"dynamicRegistration": False},
                    "hover": {"dynamicRegistration": False},
                    "semanticTokens": {"dynamicRegistration": False},
                },
                "workspace": {
                    "symbol": {"dynamicRegistration": False},
                },
            },
            "initializationOptions": self.config.initialization_options,
        })

        # Send initialized notification
        await self._send_notification("initialized", {})

        self._initialized = True
        logger.info("LSP server initialized for %s", self.language.value)

    async def shutdown(self) -> None:
        """Gracefully shut down the language server."""
        if not self._initialized or self._process is None:
            return

        try:
            await self._send_request("shutdown", None)
            await self._send_notification("exit", None)
            await asyncio.wait_for(self._process.wait(), timeout=5.0)
        except Exception as e:
            logger.warning("Error during LSP shutdown: %s", e)
            if self._process.returncode is None:
                self._process.kill()
        finally:
            self._initialized = False
            self._process = None

    # ── High-level "meta-instructions" ──────────────────────────────

    async def get_symbols(self, file_path: Path) -> list[LspSymbolInfo]:
        """List all symbols (classes, fields, methods) in a file.

        Wraps textDocument/documentSymbol.
        """
        uri = self._file_to_uri(file_path)
        result = await self._send_request("textDocument/documentSymbol", {
            "textDocument": {"uri": uri},
        })
        return self._parse_symbols(result, uri) if result else []

    async def get_references(
        self, file_path: Path, position: LspPosition
    ) -> list[LspLocation]:
        """Find all references to the symbol at the given position.

        Wraps textDocument/references. This is the core of impact analysis.
        """
        uri = self._file_to_uri(file_path)
        result = await self._send_request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": position.to_lsp(),
            "context": {"includeDeclaration": True},
        })
        return [
            LspLocation(
                uri=item["uri"],
                range=LspRange(
                    start=LspPosition(**item["range"]["start"]),
                    end=LspPosition(**item["range"]["end"]),
                ),
            )
            for item in (result or [])
        ]

    async def find_definitions(
        self, file_path: Path, position: LspPosition
    ) -> list[LspLocation]:
        """Go to definition for the symbol at position.

        Wraps textDocument/definition.
        """
        uri = self._file_to_uri(file_path)
        result = await self._send_request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": position.to_lsp(),
        })
        if not result:
            return []
        locations = result if isinstance(result, list) else [result]
        return [
            LspLocation(
                uri=loc["uri"],
                range=LspRange(
                    start=LspPosition(**loc["range"]["start"]),
                    end=LspPosition(**loc["range"]["end"]),
                ),
            )
            for loc in locations
        ]

    async def find_implementations(
        self, file_path: Path, position: LspPosition
    ) -> list[LspLocation]:
        """Find all implementations of the interface/class at position.

        Wraps textDocument/implementation.
        """
        uri = self._file_to_uri(file_path)
        result = await self._send_request("textDocument/implementation", {
            "textDocument": {"uri": uri},
            "position": position.to_lsp(),
        })
        if not result:
            return []
        locations = result if isinstance(result, list) else [result]
        return [
            LspLocation(
                uri=loc["uri"],
                range=LspRange(
                    start=LspPosition(**loc["range"]["start"]),
                    end=LspPosition(**loc["range"]["end"]),
                ),
            )
            for loc in locations
        ]

    async def rename_symbol(
        self, file_path: Path, position: LspPosition, new_name: str
    ) -> LspWorkspaceEdit:
        """Perform a semantic rename of the symbol at position.

        Wraps textDocument/rename. The server computes all edits needed.
        """
        uri = self._file_to_uri(file_path)
        result = await self._send_request("textDocument/rename", {
            "textDocument": {"uri": uri},
            "position": position.to_lsp(),
            "newName": new_name,
        })
        edit = LspWorkspaceEdit()
        if result and "changes" in result:
            for file_uri, text_edits in result["changes"].items():
                edit.changes[file_uri] = [
                    LspTextEdit(
                        range=LspRange(
                            start=LspPosition(**te["range"]["start"]),
                            end=LspPosition(**te["range"]["end"]),
                        ),
                        new_text=te["newText"],
                    )
                    for te in text_edits
                ]
        return edit

    async def prepare_rename(
        self, file_path: Path, position: LspPosition
    ) -> LspRange | None:
        """Prepare a rename to check if the position is renameable.

        Returns the range of the symbol, or None if rename is not possible.
        """
        uri = self._file_to_uri(file_path)
        result = await self._send_request("textDocument/prepareRename", {
            "textDocument": {"uri": uri},
            "position": position.to_lsp(),
        })
        if not result:
            return None
        return LspRange(
            start=LspPosition(**result["range"]["start"]),
            end=LspPosition(**result["range"]["end"]),
        )

    async def get_diagnostics(self, file_path: Path) -> list[dict]:
        """Get diagnostics (errors, warnings) for a file.

        Note: LSP servers push diagnostics automatically after didOpen/didChange.
        This method returns the cached diagnostics from the last server update.
        """
        # Diagnostics are push-based in LSP; this is a placeholder
        # that ensures the file is open so diagnostics are up-to-date.
        await self._ensure_file_open(file_path)
        # Diagnostics arrive asynchronously via notifications.
        # For synchronous use, callers should open files first and wait briefly.
        return self._diagnostics_cache.get(self._file_to_uri(file_path), [])

    async def hover(
        self, file_path: Path, position: LspPosition
    ) -> str | None:
        """Get hover information for the symbol at position."""
        uri = self._file_to_uri(file_path)
        result = await self._send_request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": position.to_lsp(),
        })
        if result and "contents" in result:
            contents = result["contents"]
            if isinstance(contents, dict):
                return contents.get("value", "")
            return str(contents)
        return None

    # ── Internal: file management ───────────────────────────────────

    async def open_file(self, file_path: Path) -> None:
        """Open a file in the language server (required before queries)."""
        content = file_path.read_text(encoding="utf-8")
        uri = self._file_to_uri(file_path)
        await self._send_notification("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": self.language.value,
                "version": 0,
                "text": content,
            }
        })
        self._open_files[uri] = content

    async def _ensure_file_open(self, file_path: Path) -> None:
        """Ensure a file is open in the language server."""
        uri = self._file_to_uri(file_path)
        if uri not in self._open_files:
            await self.open_file(file_path)

    # ── Internal: LSP protocol ──────────────────────────────────────

    async def _send_request(self, method: str, params: Any) -> Any:
        """Send a JSON-RPC request and wait for the response."""
        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        return await self._send_and_receive(request, expect_response_id=self._request_id)

    async def _send_notification(self, method: str, params: Any) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        await self._write_message(notification)

    async def _send_and_receive(
        self, request: dict, expect_response_id: int
    ) -> Any:
        """Write a request and wait for the matching response."""
        assert self._process and self._process.stdin and self._process.stdout

        # Start a reader task for the response
        response_future: asyncio.Future = asyncio.get_event_loop().create_future()

        async def _read_responses() -> None:
            while True:
                try:
                    msg = await self._read_message()
                    if msg is None:
                        break
                    if "id" in msg and msg.get("id") == expect_response_id:
                        if "error" in msg:
                            response_future.set_exception(
                                RuntimeError(
                                    f"LSP error: {msg['error']}"
                                )
                            )
                        else:
                            response_future.set_result(msg.get("result"))
                        break
                    else:
                        # Handle notifications (e.g., window/logMessage, textDocument/publishDiagnostics)
                        self._handle_notification(msg)
                except Exception as e:
                    if not response_future.done():
                        response_future.set_exception(e)
                    break

        reader_task = asyncio.create_task(_read_responses())
        await self._write_message(request)

        try:
            return await asyncio.wait_for(response_future, timeout=30.0)
        except asyncio.TimeoutError:
            reader_task.cancel()
            raise TimeoutError(
                f"LSP request '{request['method']}' timed out after 30s"
            )

    def _handle_notification(self, msg: dict) -> None:
        """Handle incoming LSP notifications."""
        method = msg.get("method", "")
        if method == "textDocument/publishDiagnostics":
            params = msg.get("params", {})
            uri = params.get("uri", "")
            self._diagnostics_cache[uri] = params.get("diagnostics", [])
        elif method == "window/logMessage":
            level = msg.get("params", {}).get("type", "info")
            text = msg.get("params", {}).get("message", "")
            log_fn = {
                1: logger.error,
                2: logger.warning,
                3: logger.info,
                4: logger.debug,
            }.get(level, logger.info)
            log_fn("LSP: %s", text)
        elif method == "window/showMessage":
            text = msg.get("params", {}).get("message", "")
            logger.info("LSP message: %s", text)

    async def _write_message(self, message: dict) -> None:
        """Write a JSON-RPC message to the LSP server's stdin."""
        assert self._process and self._process.stdin
        body = json.dumps(message).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._process.stdin.write(header + body)
        await self._process.stdin.drain()

    async def _read_message(self) -> dict | None:
        """Read a JSON-RPC message from the LSP server's stdout."""
        assert self._process and self._process.stdout

        # Read headers
        content_length = 0
        while True:
            line = await self._process.stdout.readline()
            if not line:
                return None
            line = line.decode("ascii").strip()
            if line.startswith("Content-Length:"):
                content_length = int(line.split(":", 1)[1].strip())
            elif line == "":
                break

        if content_length == 0:
            return None

        # Read body
        body = await self._process.stdout.readexactly(content_length)
        return json.loads(body.decode("utf-8"))

    def _parse_symbols(
        self, raw: list[dict], uri: str
    ) -> list[LspSymbolInfo]:
        """Recursively parse LSP DocumentSymbol response."""
        symbols: list[LspSymbolInfo] = []
        for item in raw:
            sym_range = LspRange(
                start=LspPosition(**item["range"]["start"]),
                end=LspPosition(**item["range"]["end"]),
            ) if "range" in item else None
            sel_range = LspRange(
                start=LspPosition(**item["selectionRange"]["start"]),
                end=LspPosition(**item["selectionRange"]["end"]),
            ) if "selectionRange" in item else None

            children = self._parse_symbols(item.get("children", []), uri)
            symbols.append(LspSymbolInfo(
                name=item.get("name", ""),
                kind=item.get("kind", 0),
                detail=item.get("detail", ""),
                uri=uri,
                range=sym_range,
                selection_range=sel_range,
                children=children,
            ))
        return symbols

    # ── State ───────────────────────────────────────────────────────

    _open_files: dict[str, str] = field(default_factory=dict)
    _diagnostics_cache: dict[str, list[dict]] = field(default_factory=dict)
