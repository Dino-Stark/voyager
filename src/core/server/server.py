"""Project-scoped Voyager server."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

from core.server.protocol import (
    DEFAULT_SERVER_HOST,
    VoyagerServerInfo,
    deserialize_operation,
)
from core.session.project_session import ProjectSession
from storage.manager import StorageManager

logger = logging.getLogger(__name__)


class VoyagerServer:
    """
    Async local server that owns the long-lived project session.

    A Voyager server is scoped to one project root. It keeps JDT LS warm via
    ``ProjectSession`` and exposes scan/plan/apply over a newline-delimited JSON
    request protocol. CLI commands are clients of this server, not the execution
    owner.
    """

    def __init__(
        self,
        project_path: Path,
        *,
        host: str = DEFAULT_SERVER_HOST,
        port: int = 0,
        token: str | None = None,
    ) -> None:
        self.project_path = project_path.resolve()
        self.host = host
        self.port = port
        self.storage = StorageManager(self.project_path)
        self.session = ProjectSession(self.project_path)
        self._server: asyncio.base_events.Server | None = None
        self._token = token or secrets.token_urlsafe(32)
        self._shutdown_event = asyncio.Event()
        self._request_lock = asyncio.Lock()

    async def run(self) -> None:
        """
        Start serving until a shutdown request arrives.
        """
        await self.session.start()
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        socket = self._server.sockets[0] if self._server.sockets else None
        bound_host, bound_port = socket.getsockname()[:2] if socket else (self.host, self.port)
        self.storage.save_server_info(
            VoyagerServerInfo(
                pid=os.getpid(),
                host=str(bound_host),
                port=int(bound_port),
                token=self._token,
                project_path=str(self.project_path),
            ).to_dict()
        )
        logger.info("Voyager server listening on %s:%s", bound_host, bound_port)
        try:
            async with self._server:
                await self._shutdown_event.wait()
        finally:
            await self.session.close()
            self.storage.clear_server_info()
            logger.info("Voyager server stopped")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        response: dict[str, Any]
        should_shutdown = False
        raw = b""
        try:
            raw = await reader.readline()
            if not raw:
                return
            request = json.loads(raw.decode("utf-8"))
            if request.get("token") != self._token:
                raise PermissionError("invalid server token")
            if self._requires_exclusive_session(request.get("method")):
                async with self._request_lock:
                    response, should_shutdown = await self._dispatch(request)
            else:
                response, should_shutdown = await self._dispatch(request)
        except Exception as exc:
            request_id = None
            try:
                request_id = json.loads(raw.decode("utf-8")).get("id") if raw else None
            except Exception:
                request_id = None
            response = {
                "id": request_id,
                "error": {
                    "message": str(exc),
                    "type": exc.__class__.__name__,
                },
            }

        writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()

        if should_shutdown:
            self._shutdown_event.set()

    async def _dispatch(self, request: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {}) or {}

        if method == "server/ping":
            return {
                "id": request_id,
                "result": {
                    "ok": True,
                    "project_path": str(self.project_path),
                    "pid": os.getpid(),
                },
            }, False

        if method == "server/status":
            return {
                "id": request_id,
                "result": {
                    "running": True,
                    "project_path": str(self.project_path),
                    "pid": os.getpid(),
                },
            }, False

        if method == "project/scan":
            result = await self.session.scan()
            return {"id": request_id, "result": result}, False

        if method == "operation/plan":
            operation = deserialize_operation(params["operation"])
            result = await self.session.plan(operation)
            return {"id": request_id, "result": result.model_dump(mode="json")}, False

        if method == "operation/apply":
            operation = deserialize_operation(params["operation"])
            result = await self.session.apply(operation)
            return {"id": request_id, "result": result.model_dump(mode="json")}, False

        if method == "server/shutdown":
            return {"id": request_id, "result": {"ok": True}}, True

        raise ValueError(f"Unknown Voyager server method: {method}")

    def _requires_exclusive_session(self, method: str | None) -> bool:
        """
        Return whether a method needs exclusive access to ProjectSession/JDT LS.
        """
        return method in {"project/scan", "operation/plan", "operation/apply", "server/shutdown"}


def run_server(project_path: Path, *, host: str = DEFAULT_SERVER_HOST, port: int = 0) -> None:
    """
    Blocking helper used by the server entrypoint.
    """
    _configure_logging(project_path)
    server = VoyagerServer(project_path, host=host, port=port)
    asyncio.run(server.run())


def _configure_logging(project_path: Path) -> None:
    log_path = StorageManager(project_path).get_server_log_path()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
        force=True,
    )
