"""Small JSON-RPC/LSP client used by Voyager.

The client is intentionally thin.  It exposes LSP as a source of facts and
edits; transactionality and validation live in the execution engine.
"""

import asyncio
import json
import logging
import os
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse, ParseResult
from urllib.request import url2pathname

from core.lsp.config import Language, LanguageConfig, get_language_config

logger = logging.getLogger(__name__)

_JDTLS_STDERR_ENCODINGS = ("utf-8", "gbk", "mbcs", "cp1252")


@dataclass(frozen=True)
class LspPosition:
    """
    A zero-based LSP document position.

    LSP positions are 0-based (both line and character).  Python code that reads
    from source files typically uses 1-based line numbers, so convert when
    bridging between the two systems.

    Attributes:
        line: 0-based line index.
        character: 0-based column index, measured in UTF-16 code units per the
            LSP spec.  For ASCII characters this equals the byte/char offset.
    """

    line: int
    character: int

    def to_lsp(self) -> dict[str, int]:
        return {"line": self.line, "character": self.character}


@dataclass(frozen=True)
class LspRange:
    """
    A zero-based LSP document range.

    Represents a span of text in a document.  The range is half-open:
    the character at ``end`` is not included.

    Attributes:
        start: Start position (inclusive).
        end: End position (exclusive).
    """

    start: LspPosition
    end: LspPosition

    def to_lsp(self) -> dict[str, dict[str, int]]:
        return {"start": self.start.to_lsp(), "end": self.end.to_lsp()}


@dataclass(frozen=True)
class LspLocation:
    """
    A source location returned by an LSP server.

    Identifies a span of text in a specific file.

    Attributes:
        uri: File URI, e.g. ``file:///D:/project/src/OrderDTO.java``.
        range: The span within the file.
    """

    uri: str
    range: LspRange


@dataclass
class LspSymbolInfo:
    """
    DocumentSymbol information returned by an LSP server.

    Returned by ``textDocument/documentSymbol``.  This class mirrors the
    DocumentSymbol shape defined in the LSP spec, but stores both fields so
    callers can choose the appropriate granularity.

    Attributes:
        name: Symbol name, e.g. the class or method name.
        kind: LSP SymbolKind integer (Class=5, Method=6, Field=8, ...).
        detail: Additional detail, e.g. full method signature or field type.
        uri: File URI this symbol belongs to.
        range: Full extent of the symbol, including its body.  Used by LSP
            clients to determine whether a cursor position falls *inside* the
            symbol (for "reveal in sidebar" / navigation).  Not used by Voyager.
        selection_range: The span that should be selected when the user
            navigates to / reveals this symbol, typically just the name.
            Voyager uses this for source position (line / column) and as the
            cursor anchor when requesting LSP rename.
        children: Nested symbols, e.g. fields and methods inside a class.
    """

    name: str
    kind: int
    detail: str = ""
    uri: str = ""
    range: LspRange | None = None
    selection_range: LspRange | None = None
    children: list["LspSymbolInfo"] = field(default_factory=list)


@dataclass(frozen=True)
class LspTextEdit:
    """
    A single LSP text edit.

    Represents a replacement of ``range`` with ``new_text``.  The range is
    half-open (the character at ``range.end`` is not replaced).

    Attributes:
        range: The span to replace.  Measured in UTF-16 code units per the
            LSP spec; see :func:`_utf16_index_to_py_index` for conversion.
        new_text: The replacement text.
    """

    range: LspRange
    new_text: str


@dataclass
class LspWorkspaceEdit:
    """
    A parsed subset of :lsp:`WorkspaceEdit`.

    The core result of an LSP rename operation.  V1 supports the two common
    shapes emitted by language servers: ``changes`` (map of URI to edits) and
    ``documentChanges`` (list of text document edits with explicit URIs).

    Attributes:
        changes: Map of file URI to the list of edits that apply to that file.
    """

    changes: dict[str, list[LspTextEdit]] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not any(self.changes.values())


def path_to_uri(path: Path) -> str:
    """Convert a local path to a file URI."""

    return path.resolve().as_uri()


def uri_to_path(uri: str) -> Path:
    """Convert a file URI to a local path."""

    parsed: ParseResult = urlparse(uri)
    if parsed.scheme != "file":
        return Path(uri)
    if os.name == "nt":
        raw_path: str = unquote(parsed.path)
        if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
            raw_path = raw_path[1:]
        return Path(url2pathname(raw_path))
    return Path(unquote(parsed.path))


class LspClient:
    """
    Generic LSP client that speaks JSON-RPC over stdio.

    This is a thin client: it launches the language server process, sends requests
    and notifications, and parses responses.  Transactionality and validation live
    in the :class:`ExecutionEngine <core.engine.execution_engine.ExecutionEngine>`.

    **Lifecycle:** use as an async context manager (``async with``) so ``start()``
    is called on entry and ``shutdown()`` on exit.

    **Protocol:** messages are JSON-RPC 2.0 objects preceded by a ``Content-Length``
    header.  Each request gets a Future that is resolved when the matching response
    arrives in the background read loop.

    **File sync:** the client tracks open files and sends ``textDocument/didOpen``
    and ``textDocument/didChange`` notifications so the server stays in sync with
    in-memory content during rename operations.
    """

    def __init__(
        self,
        language: Language,
        project_path: Path,
        config: LanguageConfig | None = None,
        request_timeout: float = 30.0,
    ) -> None:
        self.language: Language = language
        self.project_path: Path = project_path.resolve()
        self.config = config or get_language_config(language)
        self.request_timeout = request_timeout

        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._initialized = False
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._open_files: dict[str, int] = {}
        self._diagnostics_cache: dict[str, list[dict[str, Any]]] = {}
        self._server_ready_event: asyncio.Event = asyncio.Event()

    async def __aenter__(self) -> "LspClient":
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.shutdown()

    async def start(self) -> None:
        """Start the language server and run the initialize handshake."""

        if self._initialized:
            return

        cmd = self.config.find_server_command()
        if cmd is None:
            raise RuntimeError(
                f"LSP server for '{self.language.value}' was not found. "
                f"Install it or put '{self.config.command[0]}' on PATH."
            )

        env = os.environ.copy()

        if self.language == Language.JAVA and "-data" not in cmd:
            workspace = self._jdtls_workspace_path()
            workspace.mkdir(parents=True, exist_ok=True)
            cmd = [*cmd, "-data", str(workspace)]

        logger.info("Starting LSP server: %s", " ".join(cmd))
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.project_path),
            env=env,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._read_stderr_loop())

        await self._send_request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": path_to_uri(self.project_path),
                "rootPath": str(self.project_path),
                "workspaceFolders": [
                    {"uri": path_to_uri(self.project_path), "name": self.project_path.name}
                ],
                "capabilities": {
                    "textDocument": {
                        "definition": {"dynamicRegistration": False},
                        "references": {"dynamicRegistration": False},
                        "implementation": {"dynamicRegistration": False},
                        "rename": {"prepareSupport": True},
                        "documentSymbol": {
                            "dynamicRegistration": False,
                            "hierarchicalDocumentSymbolSupport": True,
                        },
                        "synchronization": {
                            "didOpen": True,
                            "didChange": True,
                            "willSave": False,
                            "willSaveWaitUntil": False,
                            "didSave": False,
                        },
                    },
                    "workspace": {
                        "applyEdit": False,
                        "workspaceEdit": {
                            "documentChanges": True,
                            "resourceOperations": [],
                        },
                    },
                },
                "initializationOptions": self.config.initialization_options,
            },
        )
        await self._send_notification("initialized", {})
        # Explicitly disable diagnostics via workspace configuration; some
        # jdtls versions ignore the initialization option.
        await self._send_notification(
            "workspace/didChangeConfiguration",
            {"settings": {"java": {"diagnostics": {"enabled": False}}}},
        )
        # Wait for jdtls to signal it has finished internal setup (preference
        # manager, classpath indexing, etc.) before we send the first didOpen.
        try:
            await asyncio.wait_for(self._server_ready_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("LSP server did not report ready within 10s, proceeding anyway")
        self._initialized = True

    async def shutdown(self) -> None:
        """Gracefully shut down the language server."""

        process = self._process
        if process is None:
            return

        try:
            if self._initialized:
                await self._send_request("shutdown", None)
                await self._send_notification("exit", None)
                await asyncio.wait_for(process.wait(), timeout=5.0)
        except Exception as exc:
            logger.warning("LSP shutdown failed: %s", exc)
            if process.returncode is None:
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    pass
        finally:
            if self._reader_task is not None:
                self._reader_task.cancel()
                try:
                    await self._reader_task
                except asyncio.CancelledError:
                    pass
            if self._stderr_task is not None:
                self._stderr_task.cancel()
                try:
                    await self._stderr_task
                except asyncio.CancelledError:
                    pass
            for future in self._pending.values():
                if not future.done():
                    future.cancel()
            self._pending.clear()
            self._reader_task = None
            self._stderr_task = None
            self._process = None
            self._initialized = False
            self._server_ready_event.clear()

    def _jdtls_workspace_path(self) -> Path:
        """Return a JDT LS workspace outside the analyzed project tree."""

        digest = hashlib.sha1(str(self.project_path).encode("utf-8")).hexdigest()[:16]
        cache_root = Path(
            os.environ.get("LOCALAPPDATA")
            or os.environ.get("APPDATA")
            or os.environ.get("TEMP")
            or str(self.project_path.parent)
        )
        return cache_root / "Voyager" / "jdtls-workspaces" / digest

    async def get_symbols(self, file_path: Path) -> list[LspSymbolInfo]:
        """Return all document symbols for a file."""

        await self.open_file(file_path)
        uri = path_to_uri(file_path)
        result = await self._send_request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": uri}},
        )
        return self._parse_symbols(result or [], uri)

    async def get_references(
        self,
        file_path: Path,
        position: LspPosition,
        include_declaration: bool = True,
    ) -> list[LspLocation]:
        """Find references for the symbol at ``position``."""

        await self.open_file(file_path)
        result = await self._send_request(
            "textDocument/references",
            {
                "textDocument": {"uri": path_to_uri(file_path)},
                "position": position.to_lsp(),
                "context": {"includeDeclaration": include_declaration},
            },
        )
        return [self._parse_location(item) for item in result or []]

    async def find_definitions(
        self, file_path: Path, position: LspPosition
    ) -> list[LspLocation]:
        """Find definitions for the symbol at ``position``."""

        await self.open_file(file_path)
        result = await self._send_request(
            "textDocument/definition",
            {
                "textDocument": {"uri": path_to_uri(file_path)},
                "position": position.to_lsp(),
            },
        )
        items = result if isinstance(result, list) else [result] if result else []
        return [self._parse_location(item) for item in items]

    async def find_implementations(
        self, file_path: Path, position: LspPosition
    ) -> list[LspLocation]:
        """Find implementations for the symbol at ``position``."""

        await self.open_file(file_path)
        result = await self._send_request(
            "textDocument/implementation",
            {
                "textDocument": {"uri": path_to_uri(file_path)},
                "position": position.to_lsp(),
            },
        )
        items = result if isinstance(result, list) else [result] if result else []
        return [self._parse_location(item) for item in items]

    async def prepare_rename(self, file_path: Path, position: LspPosition) -> LspRange | None:
        """Ask the server whether a location can be renamed."""

        await self.open_file(file_path)
        result = await self._send_request(
            "textDocument/prepareRename",
            {
                "textDocument": {"uri": path_to_uri(file_path)},
                "position": position.to_lsp(),
            },
        )
        if not result:
            return None
        raw_range = result.get("range", result)
        return self._parse_range(raw_range)

    async def rename_symbol(
        self, file_path: Path, position: LspPosition, new_name: str
    ) -> LspWorkspaceEdit:
        """Use ``textDocument/rename`` to produce semantic edits."""

        await self.open_file(file_path)
        result = await self._send_request(
            "textDocument/rename",
            {
                "textDocument": {"uri": path_to_uri(file_path)},
                "position": position.to_lsp(),
                "newName": new_name,
            },
        )
        return self._parse_workspace_edit(result or {})

    async def get_diagnostics(self, file_path: Path) -> list[dict[str, Any]]:
        """Return the latest diagnostics published for a file."""

        await self.open_file(file_path)
        return self._diagnostics_cache.get(path_to_uri(file_path), [])

    async def open_file(self, file_path: Path) -> None:
        """Notify the language server that a file is open."""

        uri = path_to_uri(file_path)
        if uri in self._open_files:
            return
        text = file_path.read_text(encoding="utf-8")
        await self._send_notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": self.language.value,
                    "version": 1,
                    "text": text,
                }
            },
        )
        self._open_files[uri] = 1

    async def change_file(self, file_path: Path, text: str) -> None:
        """Update an already-open document in the server."""

        uri = path_to_uri(file_path)
        if uri not in self._open_files:
            await self.open_file(file_path)
        version = self._open_files.get(uri, 1) + 1
        self._open_files[uri] = version
        await self._send_notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": text}],
            },
        )

    async def _send_request(self, method: str, params: Any) -> Any:
        self._request_id += 1
        request_id = self._request_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = future
        await self._write_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        try:
            return await asyncio.wait_for(future, timeout=self.request_timeout)
        finally:
            self._pending.pop(request_id, None)

    async def _send_notification(self, method: str, params: Any) -> None:
        await self._write_message({"jsonrpc": "2.0", "method": method, "params": params})

    async def _write_message(self, message: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("LSP server is not running")
        body = json.dumps(message, separators=(",", ":")).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._process.stdin.write(header + body)
        await self._process.stdin.drain()

    async def _read_stderr_loop(self) -> None:
        """Drain stderr so the server never blocks on a full buffer."""

        if self._process is None or self._process.stderr is None:
            return
        while True:
            try:
                line = await self._process.stderr.readline()
                if not line:
                    return
                message = _decode_process_output(line).rstrip()
                if message:
                    logger.debug("LSP stderr: %s", _safe_log_text(message))
            except asyncio.CancelledError:
                raise
            except Exception:
                return

    async def _read_loop(self) -> None:
        while True:
            try:
                message = await self._read_message()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("LSP read loop stopped: %s", exc)
                self._fail_pending(exc)
                return

            if message is None:
                self._fail_pending(RuntimeError("LSP server closed stdout"))
                return

            response_id = message.get("id")
            if response_id is not None and response_id in self._pending:
                future = self._pending[response_id]
                if future.done():
                    continue
                if "error" in message:
                    future.set_exception(RuntimeError(f"LSP error: {message['error']}"))
                else:
                    future.set_result(message.get("result"))
                continue

            self._handle_notification(message)

    async def _read_message(self) -> dict[str, Any] | None:
        if self._process is None or self._process.stdout is None:
            raise RuntimeError("LSP server is not running")

        content_length: int | None = None
        while True:
            line = await self._process.stdout.readline()
            if not line:
                return None
            header = line.decode("ascii", errors="replace").strip()
            if not header:
                break
            name, _, value = header.partition(":")
            if name.lower() == "content-length":
                content_length = int(value.strip())

        if content_length is None:
            return None
        body = await self._process.stdout.readexactly(content_length)
        return json.loads(body.decode("utf-8"))

    def _fail_pending(self, exc: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)

    def _handle_notification(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params", {})
        if method == "textDocument/publishDiagnostics":
            self._diagnostics_cache[params.get("uri", "")] = params.get("diagnostics", [])
        elif method == "language/status":
            status_type = params.get("type", "")
            status_msg = params.get("message", "")
            logger.debug(
                "LSP status: %s - %s",
                _safe_log_text(str(status_type)),
                _safe_log_text(str(status_msg)),
            )
            if status_type in ("Started", "Ready") or "Ready" in status_msg:
                self._server_ready_event.set()
        elif method == "window/logMessage":
            msg = params.get("message", "")
            level = params.get("type", 3)
            # Downgrade known harmless jdtls internal errors/warnings so they
            # do not alarm users.  These are jdtls bugs, not Voyager bugs.
            if level == 1 and "NullPointerException" in msg and "Publish Diagnostics" in msg:
                logger.debug("Ignored known jdtls internal error: %s", _safe_log_text(msg))
                return
            if "Job found still running after platform shutdown" in msg and "PublishedGradleVersions" in msg:
                logger.debug("Ignored known jdtls internal warning: %s", _safe_log_text(msg))
                return
            log = {1: logger.error, 2: logger.warning, 3: logger.info}.get(
                level, logger.debug
            )
            log("LSP: %s", _safe_log_text(msg))
        elif method == "window/showMessage":
            logger.info("LSP message: %s", _safe_log_text(str(params.get("message", ""))))

    def _parse_workspace_edit(self, raw: dict[str, Any]) -> LspWorkspaceEdit:
        edit = LspWorkspaceEdit()

        for uri, text_edits in raw.get("changes", {}).items():
            edit.changes.setdefault(uri, []).extend(
                self._parse_text_edit(item) for item in text_edits
            )

        for change in raw.get("documentChanges", []) or []:
            if "textDocument" not in change:
                continue
            uri = change["textDocument"]["uri"]
            edit.changes.setdefault(uri, []).extend(
                self._parse_text_edit(item) for item in change.get("edits", [])
            )

        return edit

    def _parse_symbols(self, raw: list[dict[str, Any]], uri: str) -> list[LspSymbolInfo]:
        symbols: list[LspSymbolInfo] = []
        for item in raw:
            if "location" in item:
                location = item["location"]
                symbol_range = self._parse_range(location["range"])
                selection_range = symbol_range
                symbol_uri = location.get("uri", uri)
            else:
                symbol_range = self._parse_range(item["range"]) if "range" in item else None
                selection_range = (
                    self._parse_range(item["selectionRange"])
                    if "selectionRange" in item
                    else symbol_range
                )
                symbol_uri = uri

            symbols.append(
                LspSymbolInfo(
                    name=item.get("name", ""),
                    kind=item.get("kind", 0),
                    detail=item.get("detail", ""),
                    uri=symbol_uri,
                    range=symbol_range,
                    selection_range=selection_range,
                    children=self._parse_symbols(item.get("children", []), symbol_uri),
                )
            )
        return symbols

    def _parse_location(self, raw: dict[str, Any]) -> LspLocation:
        return LspLocation(uri=raw["uri"], range=self._parse_range(raw["range"]))

    def _parse_text_edit(self, raw: dict[str, Any]) -> LspTextEdit:
        return LspTextEdit(range=self._parse_range(raw["range"]), new_text=raw["newText"])

    def _parse_range(self, raw: dict[str, Any]) -> LspRange:
        return LspRange(
            start=LspPosition(**raw["start"]),
            end=LspPosition(**raw["end"]),
        )


def _decode_process_output(raw: bytes) -> str:
    for encoding in _JDTLS_STDERR_ENCODINGS:
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="backslashreplace")


def _safe_log_text(text: str) -> str:
    """
    Keep logs printable on legacy Windows consoles.

    Rich's legacy Windows renderer can crash when a log record contains U+FFFD
    and stdout is still using a non-UTF-8 code page.  Escaping non-ASCII in log
    records preserves the information without letting diagnostic noise abort a
    Voyager command.
    """
    return text.encode("ascii", errors="backslashreplace").decode("ascii")
